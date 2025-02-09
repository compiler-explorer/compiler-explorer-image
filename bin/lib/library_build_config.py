from typing import Optional, Dict, Any
from lib.installation_context import is_windows

valid_lib_types = ["static", "shared", "cshared"]


class LibraryBuildConfig:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.build_type = self.config_get("build_type", "")
        self.build_fixed_arch = self.config_get("build_fixed_arch", "")
        self.build_fixed_stdlib = self.config_get("build_fixed_stdlib", "")
        self.lib_type = self.config_get("lib_type", "static")
        if not self.lib_type in valid_lib_types:
            raise RuntimeError(f"{self.lib_type} not a valid lib_type")
        self.staticliblink = self.config_get("staticliblink", [])
        self.sharedliblink = self.config_get("sharedliblink", [])
        self.url = "None"
        self.description = ""
        self.configure_flags = self.config_get("configure_flags", [])
        self.prebuild_script = self.config_get("prebuild_script", [])
        if is_windows():
            self.prebuild_script = self.config_get("prebuild_script_pwsh", self.prebuild_script)
        self.postbuild_script = self.config_get("postbuild_script", [])
        if is_windows():
            self.postbuild_script = self.config_get("postbuild_script_pwsh", self.postbuild_script)
        self.extra_cmake_arg = self.config_get("extra_cmake_arg", [])
        self.extra_make_arg = self.config_get("extra_make_arg", [])
        self.make_targets = self.config_get("make_targets", [])
        self.make_utility = self.config_get("make_utility", "make")
        self.skip_compilers = self.config_get("skip_compilers", [])
        self.copy_files = self.config_get("copy_files", [])
        self.package_install = self.config_get("package_install", False)
        self.use_compiler = self.config_get("use_compiler", "")
        if self.lib_type == "cshared" and self.use_compiler == "":
            raise RuntimeError(
                "When lib_type is cshared, it is required to supply a (cross)compiler with property use_compiler"
            )

        if self.build_type == "cargo":
            self.domainurl = self.config_get("domainurl", "https://github.com")
            self.repo = self.config_get("repo", "")

    def config_get(self, config_key: str, default: Optional[Any] = None) -> Any:
        if config_key not in self.config and default is None:
            raise RuntimeError(f"Missing required key '{config_key}'")
        return self.config.get(config_key, default)
