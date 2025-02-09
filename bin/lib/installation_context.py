from __future__ import annotations

import contextlib
import glob
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Iterator, Dict, IO, Sequence, Union, Collection, List

import requests
import requests.adapters
import yaml
import requests_cache

from lib.library_platform import LibraryPlatform
from lib.config_safe_loader import ConfigSafeLoader
from lib.staging import StagingDir

_LOGGER = logging.getLogger(__name__)
PathOrString = Union[Path, str]


def is_windows():
    return os.name == "nt"


class FetchFailure(RuntimeError):
    pass


class InstallationContext:
    def __init__(
        self,
        destination: Path,
        staging_root: Path,
        s3_url: str,
        dry_run: bool,
        is_nightly_enabled: bool,
        only_nightly: bool,
        cache: Optional[Path],
        yaml_dir: Path,
        allow_unsafe_ssl: bool,
        resource_dir: Path,
        keep_staging: bool,
        check_user: str,
        platform: LibraryPlatform,
    ):
        self._destination = destination
        self._prior_installation = self.destination
        self._staging_root = staging_root
        self._keep_staging = keep_staging
        self.s3_url = s3_url
        self.dry_run = dry_run
        self.is_nightly_enabled = is_nightly_enabled
        self.only_nightly = only_nightly
        self.platform = platform
        retry_strategy = requests.adapters.Retry(
            total=10,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )
        self.allow_unsafe_ssl = allow_unsafe_ssl
        adapter = requests.adapters.HTTPAdapter(max_retries=retry_strategy)
        if cache:
            _LOGGER.info("Using cache %s", cache)
            self.fetcher = requests_cache.CachedSession(cache)
        else:
            _LOGGER.info("Making uncached requests")
            self.fetcher = requests.Session()
        self.fetcher.mount("https://", adapter)
        self.fetcher.mount("http://", adapter)
        self.yaml_dir = yaml_dir
        self.resource_dir = resource_dir
        self.run_checks_as_user = check_user

    @property
    def destination(self) -> Path:
        return self._destination

    @property
    def prior_installation(self) -> Path:
        return self._prior_installation

    @contextlib.contextmanager
    def new_staging_dir(self) -> Iterator[StagingDir]:
        staging_dir = StagingDir(self._staging_root / str(uuid.uuid4()), self._keep_staging)
        try:
            yield staging_dir
        finally:
            if not self._keep_staging:
                if staging_dir.path.is_dir() and not is_windows():
                    subprocess.check_call(["chmod", "-R", "u+w", staging_dir.path])
                shutil.rmtree(staging_dir.path, ignore_errors=True)

    def fetch_rest_query(self, url: str) -> Dict:
        _LOGGER.debug("Fetching %s", url)
        return yaml.load(self.fetcher.get(url).text, Loader=ConfigSafeLoader)

    def fetch_to(self, url: str, fd: IO[bytes], agent: str = "") -> None:
        _LOGGER.debug("Fetching %s", url)

        if agent:
            headers = {"User-Agent": agent}

            if self.allow_unsafe_ssl:
                request = self.fetcher.get(url, stream=True, verify=False, allow_redirects=True, headers=headers)
            else:
                request = self.fetcher.get(url, stream=True, allow_redirects=True, headers=headers)
        else:
            if self.allow_unsafe_ssl:
                request = self.fetcher.get(url, stream=True, verify=False, allow_redirects=True)
            else:
                request = self.fetcher.get(url, stream=True, allow_redirects=True)

        if not request.ok:
            _LOGGER.error("Failed to fetch %s: %s", url, request)
            raise FetchFailure(f"Fetch failure for {url}: {request}")
        fetched = 0
        length = int(request.headers.get("content-length", 0))
        _LOGGER.info("Fetching %s (%d bytes)", url, length)
        report_every_secs = 5
        report_time = time.time() + report_every_secs
        for chunk in request.iter_content(chunk_size=4 * 1024 * 1024):
            fd.write(chunk)
            fetched += len(chunk)
            now = time.time()
            if now >= report_time:
                if length != 0:
                    _LOGGER.info("%.1f%% of %s...", 100.0 * fetched / length, url)
                report_time = now + report_every_secs
        _LOGGER.info("100%% of %s", url)
        fd.flush()

    def fetch_url_and_pipe_to(
        self, staging: StagingDir, url: str, command: Sequence[str], subdir: Union[Path, str] = ".", agent: str = ""
    ) -> None:
        untar_dir = staging.path / subdir
        untar_dir.mkdir(parents=True, exist_ok=True)

        if is_windows() and command[0] == "7z":
            temp_file_path = ""

            # download the file first
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file_path = temp_file.name
                self.fetch_to(url, temp_file, agent)

            # create a powershell script to extract the file
            with tempfile.NamedTemporaryFile(suffix=".ps1", delete=False) as script_file:
                # first to extract the tar file
                script_file.write(
                    f'{" ".join(command)} -o"{os.path.dirname(temp_file_path)}" {temp_file_path}\n'.encode("utf-8")
                )
                # the tar file was automatically suffixed with ~ by 7z, extract that tar to the untar_dir
                script_file.write(f'7z x -ttar -o"{untar_dir}" {temp_file_path}~\n'.encode("utf-8"))

            subprocess.check_call(["pwsh", script_file.name], cwd=str(untar_dir))

            os.remove(temp_file_path + "~")
            os.remove(temp_file_path)
            os.remove(script_file.name)
        else:
            # We stream to a temporary file first before then piping this to the command
            # as sometimes the command can take so long the URL endpoint closes the door on us
            with tempfile.TemporaryFile() as fd:
                self.fetch_to(url, fd, agent)
                fd.seek(0)
                _LOGGER.info("Piping to %s", shlex.join(command))
                subprocess.check_call(command, stdin=fd, cwd=str(untar_dir))

    def stage_command(self, staging: StagingDir, command: Sequence[str], cwd: Optional[Path] = None) -> None:
        _LOGGER.info("Staging with %s", shlex.join(command))
        env = os.environ.copy()
        env["CE_STAGING_DIR"] = str(staging.path)
        subprocess.check_call(command, cwd=str(cwd or staging.path), env=env)

    def fetch_s3_and_pipe_to(self, staging: StagingDir, s3: str, command: Sequence[str]) -> None:
        return self.fetch_url_and_pipe_to(staging, f"{self.s3_url}/{s3}", command)

    def stage_subdir(self, staging: StagingDir, subdir: str) -> None:
        (staging.path / subdir).mkdir(parents=True, exist_ok=True)

    def make_subdir(self, subdir: str) -> None:
        (self.destination / subdir).mkdir(parents=True, exist_ok=True)

    def get_current_link_target(self, dest: str) -> Path:
        full_dest = self.destination / dest
        if full_dest.is_symlink():
            return full_dest.readlink()
        else:
            return full_dest

    def set_link(self, source: Path, dest: str) -> None:
        if self.dry_run:
            _LOGGER.info("Would symlink %s to %s", source, dest)
            return

        full_source = self.destination / source
        if not full_source.exists():
            raise RuntimeError(f"During symlinking, {full_source} was not present")
        full_dest = self.destination / dest
        _LOGGER.debug("Checking whether Symlink %s exists", full_dest)
        if full_dest.is_symlink():
            _LOGGER.debug("Symlink does exist, unlinking...")
            full_dest.unlink()
            _LOGGER.debug("Symlink should be unlinked")
        relative_source = full_source.relative_to(Path(os.path.commonpath([full_source, full_dest])))
        _LOGGER.info("Symlinking %s to %s", relative_source, full_dest)
        full_dest.symlink_to(relative_source)

    def glob(self, pattern: str) -> Collection[str]:
        return [os.path.relpath(x, str(self.destination)) for x in glob.glob(str(self.destination / pattern))]

    def remove_dir(self, directory: Union[str, Path]) -> None:
        if self.dry_run:
            _LOGGER.info("Would remove directory %s but in dry-run mode", directory)
        else:
            shutil.rmtree(str(self.destination / directory), ignore_errors=True)
            _LOGGER.info("Removing %s", directory)

    def check_link(self, source: str, link: str) -> bool:
        _LOGGER.debug("check link %s", link)
        try:
            link_dest = (self.destination / link).resolve(strict=True)
            full_source = (self.destination / source).resolve(strict=True)
            _LOGGER.debug("resolving link returned %s, comparing to %s", link_dest, full_source)
            return full_source == link_dest
        except FileNotFoundError:
            _LOGGER.debug("File not found for %s", link)
            return False

    def move_from_staging(
        self,
        staging: StagingDir,
        source: PathOrString,
        dest: Optional[PathOrString] = None,
        do_staging_move=lambda source, dest: source.replace(dest),
    ) -> None:
        dest = dest or source
        existing_dir_rename = staging.path.with_suffix(".orig")
        source = staging.path / source
        dest = self.destination / dest
        if self.dry_run:
            _LOGGER.info("Would install %s to %s but in dry-run mode", source, dest)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        _LOGGER.info("Moving from staging (%s) to final destination (%s)", source, dest)
        if not source.is_dir():
            staging_contents = subprocess.check_output(["ls", "-l", str(staging.path)]).decode("utf-8")
            _LOGGER.info("Directory listing of staging:\n%s", staging_contents)
            raise RuntimeError(f"Missing source '{source}'")
        # Some tar'd up GCCs are actually marked read-only...
        if not is_windows():
            subprocess.check_call(["chmod", "-R", "u+w", source])
        state = ""
        if dest.is_dir():
            if list(dest.iterdir()):
                _LOGGER.info(
                    "Destination %s exists, temporarily moving out of the way (to %s)", dest, existing_dir_rename
                )
                dest.replace(existing_dir_rename)
                state = "old_renamed"
            else:
                _LOGGER.info("Destination %s exists but is empty; deleting it", dest)
                shutil.rmtree(dest)
        try:
            do_staging_move(source, dest)
            if state == "old_renamed":
                state = "old_needs_remove"
        finally:
            if state == "old_needs_remove":
                _LOGGER.info("Removing temporarily moved %s", existing_dir_rename)
                shutil.rmtree(existing_dir_rename, ignore_errors=True)
            elif state == "old_renamed":
                _LOGGER.warning("Moving old destination back")
                existing_dir_rename.replace(dest)

    def compare_against_staging(self, staging: StagingDir, source_str: str, dest_str: Optional[str] = None) -> bool:
        dest_str = dest_str or source_str
        source = staging.path / source_str
        dest = self.destination / dest_str
        _LOGGER.info("Comparing %s vs %s...", source, dest)
        result = subprocess.call(["diff", "-r", source, dest])
        if result == 0:
            _LOGGER.info("Contents match")
        else:
            _LOGGER.warning("Contents differ")
        return result == 0

    def check_output(self, args: List[str], env: Optional[dict] = None, stderr_on_stdout=False) -> str:
        args = args[:]
        args[0] = str(self.destination / args[0])
        _LOGGER.debug("Executing %s in %s", args, self.destination)
        if not is_windows():
            output = subprocess.run(
                args,
                cwd=str(self.destination),
                env=env,
                stdin=subprocess.DEVNULL,
                stderr=subprocess.STDOUT if stderr_on_stdout else None,
                stdout=subprocess.PIPE,
                check=True,
            )
        else:
            output = subprocess.run(
                args,
                cwd=str(self.destination),
                stdin=subprocess.DEVNULL,
                stderr=subprocess.STDOUT if stderr_on_stdout else None,
                stdout=subprocess.PIPE,
                check=True,
            )

        fulloutput = ""
        if output.stdout != None:
            fulloutput += output.stdout.decode("utf-8")
        if output.stderr != None:
            fulloutput += output.stderr.decode("utf-8")

        return fulloutput

    def check_call(self, args: List[str], env: Optional[dict] = None) -> None:
        args = args[:]
        args[0] = str(self.destination / args[0])
        _LOGGER.debug("Executing %s in %s", args, self.destination)
        subprocess.check_call(args, cwd=str(self.destination), env=env, stdin=subprocess.DEVNULL)

    def strip_exes(self, staging: StagingDir, paths: Union[bool, List[str]]) -> None:
        if isinstance(paths, bool):
            if not paths:
                return
            paths = ["."]
        to_strip = []
        for path_part in paths:
            path = staging.path / path_part
            _LOGGER.debug("Looking for executables to strip in %s", path)
            if not path.is_dir():
                raise RuntimeError(f"While looking for files to strip, {path} was not a directory")
            for dirpath, _, filenames in os.walk(str(path)):
                for filename in filenames:
                    full_path = os.path.join(dirpath, filename)
                    if os.access(full_path, os.X_OK):
                        to_strip.append(full_path)

        # Deliberately ignore errors
        subprocess.call(["strip"] + to_strip)

    def run_script(self, staging: StagingDir, from_path: Union[str, Path], lines: List[str]) -> None:
        from_path = Path(from_path)
        if len(lines) > 0:
            _LOGGER.info("Running script")
            if self.platform == LibraryPlatform.Linux:
                script_file = from_path / "ce_script.sh"
                with script_file.open("w", encoding="utf-8") as f:
                    f.write("#!/bin/bash\n\nset -euo pipefail\n\n")
                    for line in lines:
                        f.write(f"{line}\n")

                script_file.chmod(0o755)
                self.stage_command(staging, [str(script_file)], cwd=from_path)
                if not self.dry_run:
                    script_file.unlink()
            elif self.platform == LibraryPlatform.Windows:
                script_file = from_path / "ce_script.ps1"
                with script_file.open("w", encoding="utf-8") as f:
                    for line in lines:
                        f.write(f"{line}\n")
                self.stage_command(staging, ["pwsh", str(script_file)], cwd=from_path)
                if not self.dry_run:
                    script_file.unlink()

    def is_elf(self, maybe_elf_file: Path):
        return b"ELF" in subprocess.check_output(["file", maybe_elf_file])
