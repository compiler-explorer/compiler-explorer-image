from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
from functools import partial
from pathlib import Path
from typing import Optional, Callable, Dict, List, Any, Union
from lib.nightly_versions import NightlyVersions

from lib.installation_context import InstallationContext
from lib.library_build_config import LibraryBuildConfig
from lib.library_builder import LibraryBuilder
from lib.rust_library_builder import RustLibraryBuilder
from lib.fortran_library_builder import FortranLibraryBuilder
from lib.go_library_builder import GoLibraryBuilder
from lib.staging import StagingDir

_LOGGER = logging.getLogger(__name__)
_DEP_RE = re.compile("%DEP([0-9]+)%")

running_on_admin_node = socket.gethostname() == "admin-node"

nightlies: NightlyVersions = NightlyVersions(_LOGGER)

SimpleJsonType = (int, float, str, bool)


class Installable:
    _check_link: Optional[Callable[[], bool]]
    check_env: Dict
    check_file: str
    check_call: List[str]

    def __init__(self, install_context: InstallationContext, config: Dict[str, Any]):
        self.install_context = install_context
        self.config = config
        self.target_name = str(self.config.get("name", "(unnamed)"))
        self.context = self.config_get("context", [])
        self.name = f'{"/".join(self.context)} {self.target_name}'
        self.is_library = False
        self.language = False
        if len(self.context) > 0:
            self.is_library = self.context[0] == "libraries"
        if len(self.context) > 1:
            self.language = self.context[1]
        self.depends_by_name = self.config.get("depends", [])
        self.depends: List[Installable] = []
        self.install_always = self.config.get("install_always", False)
        self._check_link = None
        self.build_config = LibraryBuildConfig(config)
        self.check_env = {}
        self.check_file = self.config_get("check_file", "")
        self.check_call = []
        check_exe = self.config_get("check_exe", "")
        if check_exe:
            self.check_call = command_config(check_exe)
        self.check_stderr_on_stdout = self.config.get("check_stderr_on_stdout", False)
        self.install_path = ""
        self.after_stage_script = self.config_get("after_stage_script", [])
        self._logger = logging.getLogger(self.name)
        self.install_path_symlink = self.config_get("symlink", False)

    def to_json_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if isinstance(value, SimpleJsonType)}

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict())

    def _resolve(self, all_installables: Dict[str, Installable]):
        try:
            self.depends = [all_installables[dep] for dep in self.depends_by_name]
        except KeyError as ke:
            self._logger.error("Unable to find dependency %s in %s", ke, all_installables)
            raise

        def dep_n(match):
            return str(self.install_context.destination / self.depends[int(match.group(1))].install_path)

        def resolve_deps(s: str) -> str:
            return _DEP_RE.sub(dep_n, s)

        self.check_env = dict(
            [x.replace("%PATH%", self.install_path).split("=", 1) for x in self.config_get("check_env", [])]
        )
        self.check_env = {key: resolve_deps(value) for key, value in self.check_env.items()}

        self.after_stage_script = [resolve_deps(line) for line in self.after_stage_script]
        self.check_file = resolve_deps(self.check_file)
        if self.check_file:
            self.check_file = os.path.join(self.install_path, self.check_file)

        self.check_call = [resolve_deps(arg) for arg in self.check_call]
        if self.check_call:
            self.check_call[0] = os.path.join(self.install_path, self.check_call[0])

        if self.install_path_symlink:
            self._check_link = partial(self.install_context.check_link, self.install_path, self.install_path_symlink)

        self.resolve_dependencies(resolve_deps)

    def resolve_dependencies(self, resolver: Callable[[str], str]) -> None:
        pass

    @staticmethod
    def resolve(installables: list[Installable]) -> None:
        installables_by_name = {installable.name: installable for installable in installables}
        for installable in installables:
            installable._resolve(installables_by_name)  # pylint: disable=protected-access

    def find_dependee(self, name: str) -> Installable:
        for i in self.depends:
            if i.name == name:
                return i
        raise RuntimeError(f"Missing dependee {name} - did you forget to add it as a dependency?")

    def verify(self) -> bool:
        return True

    def should_install(self) -> bool:
        if self.install_context.only_nightly and not self.nightly_like:
            return False

        return self.install_always or not self.is_installed()

    def should_build(self):
        return (
            self.is_library
            and self.build_config.build_type != "manual"
            and self.build_config.build_type != "none"
            and self.build_config.build_type != ""
        )

    def install(self) -> None:
        self._logger.debug("Ensuring dependees are installed")
        for dependee in self.depends:
            if not dependee.is_installed():
                self._logger.info("Installing required dependee %s", dependee)
                dependee.install()
        self._logger.debug("Dependees installed")

    def save_version(self, exe: str, res_call: str):
        if not self.nightly_like:
            return

        if not running_on_admin_node:
            self._logger.warning("Not running on admin node - not saving compiler version info to AWS")
            return

        # exe is something like "gcc-trunk-20231008/bin/g++" here
        #  but we need the actual symlinked path ("/opt/compiler-explorer/gcc-snapshot/bin/g++")

        # in case of 'hook', exe is "hook/hook-0.1.0-20240213/bin/hook"

        # note: NightlyInstallable also has "path_name_symlink", so this function is overridden there

        relative_exe = "/".join(exe.split("/")[1:])
        if self.install_path_symlink:
            fullpath = self.install_context.destination / self.install_path_symlink / relative_exe
        else:
            fullpath = self.install_context.destination / exe

        # just iterate until we found the right path, we know it's there (otherwise save_version wouldn't be called)
        while not fullpath.exists():
            relative_exe = "/".join(relative_exe.split("/")[1:])
            if self.install_path_symlink:
                fullpath = self.install_context.destination / self.install_path_symlink / relative_exe
            else:
                fullpath = self.install_context.destination / exe

        stat = fullpath.stat()
        modified = stat.st_mtime

        nightlies.update_version(fullpath.as_posix(), str(modified), res_call.split("\n", 1)[0], res_call)

    def check_output_under_different_user(self):
        if self.install_context.run_checks_as_user:
            envvars = []
            for key, value in self.check_env.items():
                envvars += [key + "=" + value]
            call = ["/usr/bin/sudo", "-u", self.install_context.run_checks_as_user] + envvars + self.check_call
            res_call = self.install_context.check_output(
                call, env=self.check_env, stderr_on_stdout=self.check_stderr_on_stdout
            )
        else:
            res_call = self.install_context.check_output(
                self.check_call, env=self.check_env, stderr_on_stdout=self.check_stderr_on_stdout
            )
        return res_call

    def is_installed(self) -> bool:
        if not self.check_file and not self.check_call:
            return True

        if self._check_link and not self._check_link():
            self._logger.debug("Check link returned false")
            return False

        if self.check_file:
            res = (self.install_context.destination / self.check_file).is_file()
            self._logger.debug(
                'Check file for "%s" returned %s', self.install_context.destination / self.check_file, res
            )
            return res

        try:
            res_call = self.check_output_under_different_user()

            self.save_version(self.check_call[0], res_call)

            self._logger.debug("Check call returned %s", res_call)
            return True
        except FileNotFoundError:
            self._logger.debug("File not found for %s", self.check_call)
            return False
        except PermissionError:
            self._logger.debug("Permissions error %s", self.check_call)
            return False
        except subprocess.CalledProcessError as cpe:
            self._logger.debug("Got an error for %s: %s", self.check_call, cpe)
            return False

    def config_get(self, config_key: str, default: Optional[Any] = None) -> Any:
        if config_key not in self.config and default is None:
            raise RuntimeError(f"Missing required key '{config_key}' in {self.name}")
        return self.config.get(config_key, default)

    def __repr__(self) -> str:
        return f"Installable({self.name})"

    @property
    def sort_key(self):
        return self.context, [
            (int(num) if num else 0, non) for num, non in re.findall(r"([0-9]+)|([^0-9]+)", self.target_name)
        ]

    @property
    def nightly_like(self) -> bool:
        return self.install_always or self.target_name in ["nightly", "trunk", "master", "main"]

    def build(self, buildfor, popular_compilers_only):
        if not self.is_library:
            raise RuntimeError("Nothing to build")

        if self.build_config.build_type == "":
            raise RuntimeError("No build_type")

        if self.build_config.build_type in ["cmake", "make"]:
            sourcefolder = os.path.join(self.install_context.destination, self.install_path)
            builder = LibraryBuilder(
                _LOGGER,
                self.language,
                self.context[-1],
                self.target_name,
                sourcefolder,
                self.install_context,
                self.build_config,
                popular_compilers_only,
            )
            if self.build_config.build_type == "cmake":
                return builder.makebuild(buildfor)
            elif self.build_config.build_type == "make":
                return builder.makebuild(buildfor)
        elif self.build_config.build_type == "fpm":
            sourcefolder = os.path.join(self.install_context.destination, self.install_path)
            builder = FortranLibraryBuilder(
                _LOGGER,
                self.language,
                self.context[-1],
                self.target_name,
                sourcefolder,
                self.install_context,
                self.build_config,
                popular_compilers_only,
            )
            return builder.makebuild(buildfor)
        elif self.build_config.build_type == "cargo":
            builder = RustLibraryBuilder(
                _LOGGER, self.language, self.context[-1], self.target_name, self.install_context, self.build_config
            )
            return builder.makebuild(buildfor)
        elif self.build_config.build_type == "golib":
            builder = GoLibraryBuilder(
                _LOGGER, self.language, self.context[-1], self.target_name, self.install_context, self.build_config
            )
            return builder.makebuild(buildfor)
        else:
            raise RuntimeError("Unsupported build_type")

    def squash_to(self, destination_image: Path):
        destination_image.parent.mkdir(parents=True, exist_ok=True)
        source_folder = self.install_context.destination / self.install_path
        temp_image = destination_image.with_suffix(".tmp")
        temp_image.unlink(missing_ok=True)
        self._logger.info("Squashing %s...", source_folder)
        self.install_context.check_call(
            [
                "/usr/bin/mksquashfs",
                str(source_folder),
                str(temp_image),
                "-all-root",
                "-progress",
                "-comp",
                "zstd",
                "-Xcompression-level",
                "19",
            ]
        )
        temp_image.replace(destination_image)


class SingleFileInstallable(Installable):
    def __init__(self, install_context: InstallationContext, config: Dict[str, Any]):
        super().__init__(install_context, config)
        self.install_path = self.config_get("dir")
        self.url = self.config_get("url")
        self.filename = self.config_get("filename")

    def stage(self, staging: StagingDir) -> None:
        out_path = staging.path / self.install_path
        out_path.mkdir()
        out_file_path = out_path / self.filename
        with out_file_path.open("wb") as f:
            self.install_context.fetch_to(self.url, f)
        out_file_path.chmod(0o755)

    def verify(self) -> bool:
        if not super().verify():
            return False
        with self.install_context.new_staging_dir() as staging:
            self.stage(staging)
            return self.install_context.compare_against_staging(staging, self.install_path)

    def install(self) -> None:
        super().install()
        with self.install_context.new_staging_dir() as staging:
            self.stage(staging)
            self.install_context.move_from_staging(staging, self.install_path)

    def __repr__(self) -> str:
        return f"SingleFileInstallable({self.name}, {self.install_path})"


def command_config(config: Union[List[str], str]) -> List[str]:
    if isinstance(config, str):
        return config.split(" ")
    return config
