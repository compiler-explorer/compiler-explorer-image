from __future__ import annotations

import logging
from pathlib import Path

from lib.cefs.config import CefsConfig
from lib.cefs.root_image import CefsRootImage

_LOGGER = logging.getLogger(__name__)


class BadCefsRoot(RuntimeError):
    pass


class CefsFsRoot:
    """
    Holds image about a cefs filesystem root: a symlink pointing at a CefsRootImage on a cefs mountpoint.
    """

    def __init__(self, *, fs_root: Path, config: CefsConfig):
        self._config = config
        self._original_root = fs_root
        self._root = fs_root
        if not self._root.is_symlink():
            raise BadCefsRoot(f"{self._root} is not a cefs filesystem root - it's not a symlink")
        root_link_to_update = self._root
        self._image_root = root_link_to_update.readlink()
        # Continue to follow along symlinks so the root can actually be a symlink to a symlink. We update the last
        # symlink found along the path (to allow for `/some/root-owned` dir to symlink elsewhere: we actually update
        # elsewhere.
        while self._image_root.is_symlink():
            _LOGGER.info("Following root symlink to %s...", self._image_root)
            self._root = self._image_root
            self._image_root = self._root.readlink()

        if not self._image_root.is_relative_to(self._config.mountpoint):
            raise BadCefsRoot(
                f"Destination {self._original_root} is not a CEFS root symlink "
                f"({self._image_root} not relative to {self._config.mountpoint})!"
            )

    @classmethod
    def create(cls, base_image: Path, fs_root: Path, config: CefsConfig) -> CefsFsRoot:
        if fs_root.exists():
            raise FileExistsError(f"{fs_root} already exists")
        if not base_image.is_dir():
            raise RuntimeError("Missing base image")
        # Construct just to ensure it's a valid image.
        CefsRootImage(config=config, directory=base_image)
        fs_root.parent.mkdir(parents=True, exist_ok=True)
        # TODO append a log?
        fs_root.symlink_to(base_image, target_is_directory=True)
        return cls(fs_root=fs_root, config=config)

    @property
    def fs_path(self) -> Path:
        return self._root

    @property
    def image_root(self) -> Path:
        return self._image_root

    def read_image(self) -> CefsRootImage:
        image = CefsRootImage(config=self._config, directory=self._image_root)
        image.add_metadata(f"Information read from root image {self._image_root}")
        return image

    def update(self, new_path: Path) -> None:
        # TODO append a log?
        self._root.unlink(missing_ok=True)
        self._root.symlink_to(new_path)
        _LOGGER.info("Updated %s to %s", self._root, new_path)
