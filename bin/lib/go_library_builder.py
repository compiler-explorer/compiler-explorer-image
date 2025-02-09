from typing import Dict, Any, List
from lib.installation_context import InstallationContext
from lib.library_build_config import LibraryBuildConfig

class GoLibraryBuilder:
    def __init__(
        self,
        logger,
        language: str,
        libname: str,
        target_name: str,
        install_context: InstallationContext,
        buildconfig: LibraryBuildConfig,
    ):
        self.logger = logger

    def makebuildfor(
        self,
        compiler,
        options,
        exe,
        compiler_type,
        toolchain,
        buildos,
        buildtype,
        arch,
        stdver,
        stdlib,
        flagscombination,
        ld_path,
        source_staging,
    ):
        self.logger.error("No Implemented")
        return None
