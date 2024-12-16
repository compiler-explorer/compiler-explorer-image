from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Dict, List, Mapping, Iterator, Iterable

from lib.cefs.config import CefsConfig

_LOGGER = logging.getLogger(__name__)
METADATA_FILENAME = "metadata.txt"


class BadCefsImage(RuntimeError):
    pass


class BadCefsLink(RuntimeError):
    pass


class CefsRootImage:
    """
    Holds information about a CEFS root image, which is a directory full of symlinks to other CEFS images, and some
    metadata. TODO naming
    """

    def __init__(self, *, config: CefsConfig, directory: Optional[Path] = None):
        self._config = config
        self._catalog: Dict[Path, Path] = {}
        self._metadata: List[str] = []

        if directory:
            self._recurse_find_paths(directory, directory)
            _LOGGER.info("CEFS image at %s has %d entries", directory, len(self.catalog))

    def _recurse_find_paths(self, path: Path, root_path: Path):
        for entry in path.iterdir():
            relative = entry.relative_to(root_path)
            if entry.is_symlink():
                link = entry.readlink()
                if not link.is_relative_to(self._config.mountpoint):
                    raise BadCefsImage(f"Found a symlink that wasn't a symlink to cefs: {entry} links to {link}")
                _LOGGER.debug("Found existing %s -> %s", relative, link)
                self._catalog[relative] = link
            elif entry.is_dir():
                self._recurse_find_paths(entry, root_path)
            elif entry.is_file():
                if relative == Path(METADATA_FILENAME):
                    self._metadata = entry.read_text(encoding="utf-8").splitlines(keepends=False)
                else:
                    raise BadCefsImage(f"Found an unexpected file: {entry}")
            else:
                raise BadCefsImage(f"Found an unexpected entry: {entry}")

    def add_metadata(self, metadata: str) -> None:
        # todo separate machine readable from human readable? add timestamp? json?
        self._metadata.append(metadata)

    def link_path(self, subdir: Path, cefs_link: Path):
        if not cefs_link.is_relative_to(self._config.mountpoint):
            raise BadCefsLink(f"Link is not relative to cefs: {cefs_link}")
        self._catalog[subdir] = cefs_link

    @property
    def catalog(self) -> Mapping[Path, Path]:
        return self._catalog

    @property
    def dependent_images(self) -> List[Path]:
        return sorted(
            set(
                (self._config.image_root / x.relative_to(self._config.mountpoint).parts[0]).with_suffix(".sqfs")
                for x in self._catalog.values()
            )
        )

    def remove(self, relative_path: Path) -> None:
        if relative_path not in self._catalog:
            # TODO one day consider splitting if this is a _subdir_ of an existing image...etc
            raise FileNotFoundError(f"{relative_path} is not in, or directly mapped as a subdir")
        self.add_metadata(f"Removing mapping from {relative_path} to {self._catalog[relative_path]}")
        del self._catalog[relative_path]

    @property
    def metadata(self) -> List[str]:
        return self._metadata

    def render_to(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / METADATA_FILENAME).write_text("\n".join(self._metadata) + "\n", encoding="utf-8")
        for entry, dest in self.catalog.items():
            source_dir = destination / entry
            source_dir.parent.mkdir(parents=True, exist_ok=True)
            source_dir.symlink_to(dest)

    def _items_for(self, directory: Path, source_root: Path, dest_root: Path) -> Iterator[str]:
        # TODO er, glob?
        for item in directory.iterdir():
            rel_path = dest_root / item.relative_to(source_root)
            stat = item.lstat()
            mode = f"0{oct(stat.st_mode & 0o7777)[2:]}"
            uid = stat.st_uid
            gid = stat.st_gid
            if item.is_symlink():
                yield f'slink "{rel_path}" {mode} {uid} {gid} {item.readlink()}'
            elif item.is_dir():
                yield f'dir "{rel_path}" {mode} {uid} {gid}'
                yield from self._items_for(directory=item, source_root=source_root, dest_root=dest_root)
            elif item.is_file():
                # TODO better hope there's no special characters as gensquashfs won't let the <location> have any...
                yield f'file "{rel_path}" {mode} {uid} {gid} {item}'
            else:
                raise RuntimeError(f"oh no {item}")  # TODO

    def consolidate(self) -> None:
        # TODO what if it's already one image? could still be consolidated e.g. if things were deleted in base image
        # this might compact
        # TODO keep some number of layers?
        # TODO put in squashfs build.py? or at least move functionality there
        with TemporaryDirectory(prefix="cefs-consolidate") as tmp_dir:
            tmp_path = Path(tmp_dir)
            tmp_sqfs = tmp_path / "temp.sqfs"
            tmp_packfile = tmp_path / "packfile"
            with tmp_packfile.open("w", encoding="utf-8") as tmp_packfile_file:
                for entry, dest in self.catalog.items():
                    _LOGGER.info("Finding things to consolidate: %s->%s", entry, dest)
                    dest = dest.resolve(strict=True)
                    for item in self._items_for(directory=dest, source_root=dest, dest_root=entry):
                        tmp_packfile_file.write(f"{item}\n")
            _LOGGER.info("Consolidating...")
            subprocess.check_call(
                [
                    "gensquashfs",
                    str(tmp_sqfs),
                    "--pack-file",
                    str(tmp_packfile),
                    "--compressor",
                    "zstd",
                    "-X",
                    "level=19",
                ]
            )
            # todo reconcile with other code elsehwere and proper naming. probably move things into self._config to
            # get named parts?
            sha, _filename = subprocess.check_output(["shasum", str(tmp_sqfs)]).decode("utf-8").split()
            # todo image_root/data/parts/sha/here.sqfs ?
            image = self._config.image_root / f"{sha}.sqfs"
            if not image.exists():
                _LOGGER.info("New squashfs image: %s", image)
                image.parent.mkdir(parents=True, exist_ok=True)
                tmp_sqfs.replace(image)
            else:
                _LOGGER.info("Existing: %s", image)
                tmp_sqfs.unlink()
        dest_image = self._config.mountpoint / sha
        self._catalog = {entry: dest_image / entry for entry, dest in self._catalog.items()}

    def import_existing(self, root_dir: Path, subdirs: Iterable[Path], replace: bool = False) -> None:
        with TemporaryDirectory(prefix="cefs-import", dir=self._config.image_root) as tmp_dir:
            tmp_path = Path(tmp_dir)
            tmp_sqfs = tmp_path / "temp.sqfs"
            tmp_packfile = tmp_path / "packfile"
            new_entries = []
            with tmp_packfile.open("w", encoding="utf-8") as tmp_packfile_file:
                for to_import in subdirs:
                    if not to_import.is_absolute():
                        to_import = root_dir / to_import
                        if not to_import.is_dir():
                            raise RuntimeError(f"{to_import} was not a directory when adding")
                    entry = to_import.relative_to(root_dir)
                    if entry in self._catalog:
                        if replace:
                            _LOGGER.info("Replacing existing %s", entry)
                        else:
                            _LOGGER.info("Skipping existing %s", entry)
                            continue
                    self.metadata.append(f"Importing {to_import} as {entry}")
                    new_entries.append(entry)
                    _LOGGER.info("Scanning for import: %s->%s", entry, to_import)
                    to_import = to_import.resolve(strict=True)
                    for item in self._items_for(directory=to_import, source_root=to_import, dest_root=entry):
                        tmp_packfile_file.write(f"{item}\n")
            _LOGGER.info("Importing...")
            subprocess.check_call(
                [
                    "gensquashfs",
                    str(tmp_sqfs),
                    "--pack-file",
                    str(tmp_packfile),
                    "--compressor",
                    "zstd",
                    "-X",
                    "level=19",
                ]
            )
            # todo de-duplicate with the other similar
            sha, _filename = subprocess.check_output(["shasum", str(tmp_sqfs)]).decode("utf-8").split()
            # todo namessss like image_root/data/sha/parts/here.sqfs
            image = self._config.image_root / f"{sha}.sqfs"
            if not image.exists():
                _LOGGER.info("New squashfs image: %s", image)
                image.parent.mkdir(parents=True, exist_ok=True)
                tmp_sqfs.replace(image)
            else:
                _LOGGER.info("Existing: %s", image)
                tmp_sqfs.unlink()
        dest_image = self._config.mountpoint / sha
        for new_entry in new_entries:
            self._catalog[new_entry] = dest_image / new_entry
