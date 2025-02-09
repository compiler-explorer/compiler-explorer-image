import os
from pathlib import Path
from typing import Dict, Any

from lib.installable.installable import Installable
from lib.installation_context import InstallationContext
from lib.staging import StagingDir

import logging

_LOGGER = logging.getLogger(__name__)

class GoInstallable(Installable):
    def __init__(self, install_context: InstallationContext, config: Dict[str, Any]):
        super().__init__(install_context, config)
        self.install_always = True

        version = self.config_get("name")
        library = self.config_get("context")[-1]

        self.install_path = "libs/golibs"
        self.repo_url = self.config_get("url")
        self.base_package = f"{library}@{version}"

    def do_go_install(self, staging: StagingDir, component: str, install_to: Path) -> None:
        os.environ['GOPATH'] = install_to.as_posix()
        self.install_context.stage_command(
            staging, ["go", "mod", "download", f"{self.repo_url}/{component}"],
        )

    def stage(self, staging: StagingDir) -> None:
        base_path = staging.path / "libs/golibs"
        self.do_go_install(staging, self.base_package, base_path)

    def should_install(self) -> bool:
        return super().should_install()

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
        return f"GoInstallable({self.name}, {self.install_path})"
