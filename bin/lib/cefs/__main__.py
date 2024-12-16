from __future__ import annotations

import datetime
import getpass
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

import click
import humanfriendly

from lib.cefs.config import CefsConfig
from lib.cefs.root import CefsFsRoot
from lib.cefs.root_image import CefsRootImage
from lib.cefs.squash import SquashFsCreator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CliContext:
    config: CefsConfig


@click.group()
@click.option(
    "--cefs-mountpoint",
    default=Path("/cefs"),
    metavar="MOUNTPOINT",
    type=click.Path(file_okay=False, path_type=Path),
    help="Install or assume cefs is to use MOUNTPOINT",
    show_default=True,
)
@click.option(
    "--squash-image-root",
    default=Path("/opt/cefs-images"),
    metavar="IMAGE_DIR",
    type=click.Path(file_okay=False, path_type=Path),
    help="Store or look for squashfs images in IMAGE_DIR",
    show_default=True,
)
@click.option("--debug/--no-debug", help="Turn on debugging")
@click.option("--log-to-console", is_flag=True, help="Log output to console, even if logging to a file is requested")
@click.option("--log", metavar="LOGFILE", help="Log to LOGFILE", type=click.Path(dir_okay=False, writable=True))
@click.pass_context
def cli(
    ctx: click.Context,
    cefs_mountpoint: Path,
    squash_image_root: Path,
    debug: bool,
    log_to_console: bool,
    log: Optional[str],
):
    """Administrate the Compiler Explorer File System (cefs)."""
    formatter = logging.Formatter(fmt="%(asctime)s %(name)-15s %(levelname)-8s %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    if log:
        file_handler = logging.FileHandler(log)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    if not log or log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
    ctx.obj = CliContext(config=CefsConfig(mountpoint=cefs_mountpoint, image_root=squash_image_root))


@cli.command
@click.pass_obj
def install(context: CliContext):
    """Install the CEFS mountpoint and associated directories. Needs to be run as root."""
    config = context.config
    if os.geteuid() != 0:
        click.echo("Installing cefs needs root privileges.")
        sys.exit(1)
    if not Path("/etc/auto.master.d").is_dir():
        click.echo("Installing cefs needs autofs. Please install manually using e.g. `apt install autofs`")
        sys.exit(1)
    click.echo("Writing config files...")
    auto_cefs_config_file = Path("/etc/auto.cefs")
    auto_cefs_config_file.write_text(
        f"""#!/bin/bash
key="$1"
# Abuse the fact PWD is the parent dir to derive the full path to this subdirectory.
SUBDIR=${{PWD#{config.mountpoint}}}
if [[ ! "$SUBDIR" = "#{config.mountpoint}" ]]; then
   key=$SUBDIR/$key
fi
# Remove any trailing slash
key=${{key%/}}
# If it's a sqfs image, then mount it.
if [[ -f "{config.image_root}/$key.sqfs" ]]; then
    echo "-fstype=squashfs,loop,nosuid,nodev,ro :{config.image_root}/$key.sqfs"
fi
# Else if it's the name of a directory, make it appear as another autofs level.
if [[ -d "{config.image_root}/$key" ]]; then
    echo "-fstype=autofs :{auto_cefs_config_file}"
fi
""",
        encoding="utf-8",
    )
    auto_cefs_config_file.chmod(0o700)
    Path("/etc/auto.master.d/cefs.autofs").write_text(
        f"{config.mountpoint} {auto_cefs_config_file}  --negative-timeout 1", encoding="utf-8"
    )
    if not config.image_root.is_dir():
        click.echo(
            f"Creating {config.image_root} squash image root.\n"
            f"By default this will be writable BY ALL USERS, but is sticky like /tmp. This is to make it usable\n"
            f"by unprivileged users. You are responsible for making sure that's ok, and changing the directory\n"
            f"permissions as appropriate."
        )
        config.image_root.mkdir(parents=True)
        config.image_root.chmod(0o1777)
    click.echo("Restarting autofs")
    subprocess.check_call(["service", "autofs", "restart"])
    click.echo(f"CEFS installed with a root at {config.mountpoint} and squash image root at {config.image_root}.")
    click.echo(
        f"Note that the root {config.mountpoint} will appear empty, "
        f"but will automatically mount on demand when required."
    )


@cli.group
def image():
    """Create and manipulate raw squashfs images."""


@image.command(name="create")
@click.pass_obj
def create_root(context: CliContext):
    """Create an empty image."""
    created_path = _create_empty(context.config)
    click.echo(f"Fresh new cefs image created at {created_path}")


@image.command(name="import")
@click.argument("root", type=click.Path(file_okay=False, dir_okay=True, path_type=Path), required=True)
@click.pass_obj
def import_cmd(context: CliContext, root: Path):
    """Import an existing filesystem ROOT."""
    # DOESNT work on cefs images as they contain symlinks and...that would be bad for consolidation
    # todo check it isn't?
    # todo maybe support exclusions? `-ef` one line per file
    creator = SquashFsCreator(context.config)
    creator.import_existing_path(root)
    click.echo(f"Imported new cefs image created at {creator.cefs_path}")


@image.command
@click.argument("root", type=click.Path(exists=True, dir_okay=True, path_type=Path), required=True)
@click.pass_obj
def info(context: CliContext, root: Path):
    """Get information on the cefs root at ROOT."""
    cefs_root_image = CefsRootImage(config=context.config, directory=root)
    click.echo("Paths supported:")
    for path in cefs_root_image.catalog:
        click.echo(f"  {path}")
    click.echo("Images used:")
    for size, path in sorted(((path.stat().st_size, path) for path in cefs_root_image.dependent_images), reverse=True):
        click.echo(f"  {path} ({humanfriendly.format_size(size, binary=True)})")


def _create_empty(config: CefsConfig) -> Path:
    creator = SquashFsCreator(config)
    with creator.creation_path() as path:
        cefs_root_image = CefsRootImage(config=config)
        cefs_root_image.add_metadata(
            f"Initial empty image created at {datetime.datetime.utcnow()} by {getpass.getuser()}"
        )
        cefs_root_image.render_to(path)
    return creator.cefs_path


@cli.group
def fs():
    """Create and manipulate cefs filesystems."""


@fs.command(name="create")
@click.argument("root", type=click.Path(file_okay=False, dir_okay=False, writable=True, path_type=Path), required=True)
@click.pass_obj
def create_fs_root(context: CliContext, root: Path):
    """Create an empty cefs filesystem at ROOT."""
    empty_path = _create_empty(context.config)
    CefsFsRoot.create(base_image=empty_path, fs_root=root, config=context.config)


@fs.command
@click.argument("root", type=click.Path(file_okay=False, dir_okay=True, path_type=Path), required=True)
@click.pass_obj
def consolidate(context: CliContext, root: Path):
    """Consolidate a FS root into a single layer."""
    cefs_root = CefsFsRoot(fs_root=root, config=context.config)
    cefs_image = cefs_root.read_image()
    cefs_image.consolidate()
    _LOGGER.info("Building new root fs")
    root_creator = SquashFsCreator(config=context.config)
    with root_creator.creation_path() as tmp_path:
        cefs_image.render_to(tmp_path)
    # TODO if not changed by something else in the mean time...
    cefs_root.update(root_creator.cefs_path)


@fs.command(name="rm")
@click.argument("root", type=click.Path(file_okay=False, dir_okay=True, path_type=Path), required=True)
@click.argument("path", nargs=-1, type=click.Path(path_type=Path), required=True)
@click.pass_obj
def fs_remove(context: CliContext, root: Path, path: List[Path]):
    """Remove PATH entries from a filesystem at ROOT."""
    cefs_root = CefsFsRoot(fs_root=root, config=context.config)
    cefs_image = cefs_root.read_image()
    print(cefs_image.catalog)
    for pathlet in path:
        # TODO smart guess at "did you mean relative to the root of the fs if not absolute?
        if not pathlet.is_absolute():
            raise RuntimeError("yeah for now needs to be absolute")
        if not pathlet.is_relative_to(root):
            raise RuntimeError(f"{pathlet} not relative to {root}")
        relative = pathlet.relative_to(root)
        cefs_image.remove(relative)
    _LOGGER.info("Building new root fs")
    root_creator = SquashFsCreator(config=context.config)
    with root_creator.creation_path() as tmp_path:
        cefs_image.render_to(tmp_path)
    # TODO if not changed by something else in the mean time...
    cefs_root.update(root_creator.cefs_path)


@fs.command(name="import")
@click.option(
    "--relative-to",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Import relative to PATH",
)
@click.option("--replace/--no-replace", help="Replace (or not) anything in the path")
@click.argument("root", type=click.Path(file_okay=False, dir_okay=True, path_type=Path), required=True)
@click.argument("directory", nargs=-1, type=click.Path(file_okay=False, dir_okay=True, path_type=Path), required=True)
@click.pass_obj
def import_(context: CliContext, root: Path, relative_to: Path, directory: List[Path], replace: bool):
    """Import existing directories into a fs."""
    cefs_root = CefsFsRoot(fs_root=root, config=context.config)
    cefs_image = cefs_root.read_image()
    cefs_image.import_existing(relative_to, directory, replace=replace)
    _LOGGER.info("Building new root fs")
    root_creator = SquashFsCreator(config=context.config)
    with root_creator.creation_path() as tmp_path:
        cefs_image.render_to(tmp_path)
    # TODO if not changed by something else in the mean time...
    cefs_root.update(root_creator.cefs_path)


# TODO how to start the whole thing off? make an empty root and manually symlink it in position?
# TODO cases where we have a GIANT image that only exists because one dir is in it
#  ie repacking and refreshing old images
# TODO consolidation in general! aim is to have very few layers, after all.
# TODO delete old things
# TODO keep track of a symlink and its history /opt/compiler-explorer --> /opt/compiler-explorer.history?
# .... or ln -sf .prev to the previous state
# TODO better naming of dirs/manifest for each so we know who and why created each layer?
# TODO should we separate out root squashfs images from data?
# TODO detect cefs roots in `install` and redirect to buildroot?
# TODO delete/etc
# TODO handle old symlinks of trunk (and do trunk etc!)
# TODO should "cefs_mountpoint" be onfigurable.
# TODO should there be a magic file? should metadata.txt by .metadata.txt or indeed just that special file?
# TODO can add metadata directly with `--pseudofile` magic:
#  https://github.com/plougher/squashfs-tools/blob/a5df5dc42c564d10c1aebf2063fc30c26850ddc3/USAGE#L261
# TODO installation from ce_install of nightly things won't work as they try to remove things, and we need a solution
#  for that
# TODO installation of libraries
# TODO consolidation and other fs commands that take a while need to be defensive about changes while they're going.
# TODO /opt/cefs/images instead? then /opt/cefs/fs symlinks to all fs roots?
# TODO rust libraries don't have install_path
# TODO if we import (e.g.) `arm` wholesale, and then later add `arm/blah` as a sudirectory, need to "split" `arm` in the
#  symlinks into its components
# TODO  - can't move from one FS to another so should make sure temp is on dest drive or handle it.
# useful cmd? ce_install list gcc --json --installed-only \
#   | jq -r .install_path \
#   | xargs cefs fs import --relative-to /opt/compiler-explorer ~/ce


def main():
    cli(prog_name="cefs")  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter


if __name__ == "__main__":
    main()
