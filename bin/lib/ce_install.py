#!/usr/bin/env python3
# coding=utf-8
import json
import logging
import logging.config
import multiprocessing
from multiprocessing.pool import ThreadPool
import os
import signal
import sys
import traceback
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import List, Optional, Tuple, TextIO

import click
import yaml

from lib.amazon_properties import get_properties_compilers_and_libraries
from lib.library_platform import LibraryPlatform
from lib.config_safe_loader import ConfigSafeLoader
from lib.installable.installable import Installable
from lib.installation import installers_for
from lib.installation_context import InstallationContext
from lib.library_yaml import LibraryYaml

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CliContext:
    installation_context: InstallationContext
    enabled: List[str]
    filter_match_all: bool
    parallel: int

    def pool(self):  # no type hint as mypy freaks out, really a multiprocessing.Pool
        # https://stackoverflow.com/questions/11312525/catch-ctrlc-sigint-and-exit-multiprocesses-gracefully-in-python
        _LOGGER.info("Creating thread pool with %s workers", self.parallel)
        original_sigint_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        pool = ThreadPool(processes=self.parallel)
        signal.signal(signal.SIGINT, original_sigint_handler)
        return pool

    def get_installables(self, args_filter: List[str]) -> List[Installable]:
        installables = []
        for yaml_path in Path(self.installation_context.yaml_dir).glob("*.yaml"):
            with yaml_path.open(encoding="utf-8") as yaml_file:
                yaml_doc = yaml.load(yaml_file, Loader=ConfigSafeLoader)
            for installer in installers_for(self.installation_context, yaml_doc, self.enabled):
                installables.append(installer)
        Installable.resolve(installables)
        installables = sorted(
            filter(lambda installable: filter_aggregate(args_filter, installable, self.filter_match_all), installables),
            key=lambda x: x.sort_key,
        )
        return installables


def _context_match(context_query: str, installable: Installable) -> bool:
    context = context_query.split("/")
    root_only = context[0] == ""
    if root_only:
        context = context[1:]
        return installable.context[: len(context)] == context

    for sub in range(0, len(installable.context) - len(context) + 1):
        if installable.context[sub : sub + len(context)] == context:
            return True
    return False


def _target_match(target: str, installable: Installable) -> bool:
    return target == installable.target_name


def filter_match(filter_query: str, installable: Installable) -> bool:
    split = filter_query.split(" ", 1)
    if len(split) == 1:
        # We don't know if this is a target or context, so either work
        return _context_match(split[0], installable) or _target_match(split[0], installable)
    return _context_match(split[0], installable) and _target_match(split[1], installable)


def filter_aggregate(filters: list, installable: Installable, filter_match_all: bool = True) -> bool:
    # if there are no filters, accept it
    if not filters:
        return True

    # accept installable if it passes all filters (if filter_match_all is set) or any filters (otherwise)
    filter_generator = (filter_match(filt, installable) for filt in filters)
    return all(filter_generator) if filter_match_all else any(filter_generator)


def squash_mount_check(rootfolder: Path, subdir: str, context: CliContext) -> None:
    for filename in os.listdir(rootfolder / subdir):
        if filename.endswith(".img"):
            checkdir = Path("/opt/compiler-explorer/") / subdir / filename[:-4]
            if not checkdir.exists():
                _LOGGER.error("Missing mount point %s", checkdir)
        else:
            if subdir == "":
                squash_mount_check(rootfolder, filename, context)
            else:
                squash_mount_check(rootfolder, f"{subdir}/{filename}", context)


@click.group()
@click.option(
    "--dest",
    default=Path("/opt/compiler-explorer"),
    metavar="DEST",
    type=click.Path(file_okay=False, path_type=Path),
    help="Install with DEST as the installation root",
    show_default=True,
)
@click.option(
    "--staging-dir",
    default=Path("/opt/compiler-explorer/staging"),
    metavar="STAGEDIR",
    type=click.Path(file_okay=False, path_type=Path),
    help="Install to a unique subdirectory of STAGEDIR then rename in-place. Must be on the same drive as "
    "DEST for atomic rename/replace. Directory will be removed during install",
    show_default=True,
)
@click.option(
    "--check-user",
    default="",
    metavar="CHECKUSER",
    type=str,
    help="Executes --version checks under a different user",
)
@click.option("--debug/--no-debug", help="Turn on debugging")
@click.option("--dry-run/--for-real", help="Dry run only")
@click.option("--log-to-console", is_flag=True, help="Log output to console, even if logging to a file is requested")
@click.option("--log", metavar="LOGFILE", help="Log to LOGFILE", type=click.Path(dir_okay=False, writable=True))
@click.option(
    "--s3-bucket",
    default="compiler-explorer",
    metavar="BUCKET",
    help="Look for S3 resources in BUCKET",
    show_default=True,
)
@click.option(
    "--s3-dir",
    default="opt",
    metavar="DIR",
    help="Look for S3 resources in the bucket's subdirectory DIR",
    show_default=True,
)
@click.option("--enable", metavar="TYPE", multiple=True, help='Enable targets of type TYPE (e.g. "nightly")')
@click.option("--only-nightly", is_flag=True, help="Only install the nightly targets")
@click.option(
    "--cache",
    metavar="DIR",
    help="Cache requests at DIR",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
)
@click.option(
    "--yaml-dir",
    default=Path(__file__).resolve().parent.parent / "yaml",
    help="Look for installation yaml files in DIRs",
    metavar="DIR",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--resource-dir",
    default=Path(__file__).resolve().parent.parent / "resources",
    help="Look for installation yaml files in DIRs",
    metavar="DIR",
    show_default=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--allow-unsafe-ssl/-safe-ssl-only", help="Skip ssl certificate checks on https connections")
@click.option("--keep-staging", is_flag=True, help="Keep the unique staging directory")
@click.option(
    "--filter-match-all/--filter-match-any", help="Filter expressions must all match / any match", default=True
)
@click.option(
    "--parallel",
    type=int,
    default=min(8, multiprocessing.cpu_count()),
    help="Limit the number of concurrent processes to N",
    metavar="N",
    show_default=True,
)
@click.pass_context
def cli(
    ctx: click.Context,
    dest: Path,
    staging_dir: Path,
    debug: bool,
    log_to_console: bool,
    log: Optional[str],
    s3_bucket: str,
    s3_dir: str,
    dry_run: bool,
    enable: List[str],
    only_nightly: bool,
    cache: Optional[Path],
    yaml_dir: Path,
    allow_unsafe_ssl: bool,
    resource_dir: Path,
    keep_staging: bool,
    filter_match_all: bool,
    parallel: int,
    check_user: str,
):
    """Install binaries, libraries and compilers for Compiler Explorer."""
    formatter = logging.Formatter(fmt="%(asctime)s %(name)-15s %(levelname)-8s %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    if log:
        file_handler = logging.FileHandler(log)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    if not log or log_to_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    platform = LibraryPlatform.Linux
    if "windows" in enable:
        platform = LibraryPlatform.Windows

    context = InstallationContext(
        destination=dest,
        staging_root=staging_dir,
        s3_url=f"https://s3.amazonaws.com/{s3_bucket}/{s3_dir}",
        dry_run=dry_run,
        is_nightly_enabled="nightly" in enable,
        only_nightly=only_nightly,
        cache=cache,
        yaml_dir=yaml_dir,
        allow_unsafe_ssl=allow_unsafe_ssl,
        resource_dir=resource_dir,
        keep_staging=keep_staging,
        check_user=check_user,
        platform=platform,
    )
    ctx.obj = CliContext(
        installation_context=context,
        enabled=enable,
        filter_match_all=filter_match_all,
        parallel=parallel,
    )


@cli.command(name="list")
@click.pass_obj
@click.option("--json", "as_json", is_flag=True, help="Output in JSON format")
@click.option("--installed-only", is_flag=True, help="Only output installed targets")
@click.argument("filter_", metavar="FILTER", nargs=-1)
def list_cmd(context: CliContext, filter_: List[str], as_json: bool, installed_only: bool):
    """List installation targets matching FILTER."""
    for installable in context.get_installables(filter_):
        if installed_only and not installable.is_installed():
            continue
        print(installable.to_json() if as_json else installable.name)
        _LOGGER.debug(installable)


@cli.command()
@click.pass_obj
@click.argument("filter_", metavar="FILTER", nargs=-1)
def verify(context: CliContext, filter_: List[str]):
    """Verify the installations of targets matching FILTER."""
    num_ok = 0
    num_not_ok = 0
    for installable in context.get_installables(filter_):
        print(f"Checking {installable.name}")
        if not installable.is_installed():
            _LOGGER.info("%s is not installed", installable.name)
            num_not_ok += 1
        elif not installable.verify():
            _LOGGER.info("%s is not OK", installable.name)
            num_not_ok += 1
        else:
            num_ok += 1
    print(f"{num_ok} packages OK, {num_not_ok} not OK or not installed")
    if num_not_ok:
        sys.exit(1)


@cli.command()
@click.pass_obj
@click.argument("filter_", metavar="FILTER", nargs=-1)
def check_installed(context: CliContext, filter_: List[str]):
    """Check whether targets matching FILTER are installed."""
    for installable in context.get_installables(filter_):
        if installable.is_installed():
            print(f"{installable.name}: installed")
        else:
            print(f"{installable.name}: not installed")


@cli.command()
@click.pass_obj
@click.argument("filter_", metavar="FILTER", nargs=-1)
def check_should_install(context: CliContext, filter_: List[str]):
    """Check whether targets matching FILTER Should be installed."""
    for installable in context.get_installables(filter_):
        if installable.should_install():
            print(f"{installable.name}: yes")
        else:
            print(f"{installable.name}: no")


@cli.command()
def amazon_check():
    _LOGGER.debug("Starting Amazon Check")
    languages = ["c", "c++", "d", "cuda"]

    for language in languages:
        _LOGGER.info("Checking %s libraries", language)
        [_, libraries] = get_properties_compilers_and_libraries(language, _LOGGER, LibraryPlatform.Linux)

        for libraryid in libraries:
            _LOGGER.debug("Checking %s", libraryid)
            for version in libraries[libraryid]["versionprops"]:
                includepaths = libraries[libraryid]["versionprops"][version]["path"]
                for includepath in includepaths:
                    _LOGGER.debug("Checking for library %s %s: %s", libraryid, version, includepath)
                    if not os.path.exists(includepath):
                        _LOGGER.error("Path missing for library %s %s: %s", libraryid, version, includepath)
                    else:
                        _LOGGER.debug("Found path for library %s %s: %s", libraryid, version, includepath)

                libpaths = libraries[libraryid]["versionprops"][version]["libpath"]
                for libpath in libpaths:
                    _LOGGER.debug("Checking for library %s %s: %s", libraryid, version, libpath)
                    if not os.path.exists(libpath):
                        _LOGGER.error("Path missing for library %s %s: %s", libraryid, version, libpath)
                    else:
                        _LOGGER.debug("Found path for library %s %s: %s", libraryid, version, libpath)


def _to_squash(image_dir: Path, force: bool, installable: Installable) -> Optional[Tuple[Installable, Path]]:
    if not installable.is_installed():
        _LOGGER.warning("%s wasn't installed; skipping squash", installable.name)
        return None
    destination = image_dir / f"{installable.install_path}.img"
    if destination.exists() and not force:
        _LOGGER.info("Skipping %s as it already exists at %s", installable.name, destination)
        return None
    if installable.nightly_like:
        _LOGGER.info("Skipping %s as it looks like a nightly", installable.name)
        return None
    return installable, destination


@cli.command()
@click.pass_obj
@click.option("--force", is_flag=True, help="Force even if would otherwise skip")
@click.option(
    "--image-dir",
    default=Path("/opt/squash-images"),
    metavar="IMAGES",
    type=click.Path(file_okay=False, path_type=Path),
    help="Build images to IMAGES",
    show_default=True,
)
@click.argument("filter_", metavar="FILTER", nargs=-1)
def squash(context: CliContext, filter_: List[str], force: bool, image_dir: Path):
    """Create squashfs images for all targets matching FILTER."""

    with context.pool() as pool:
        should_install_func = partial(_to_squash, image_dir, force)
        to_do = filter(lambda x: x is not None, pool.map(should_install_func, context.get_installables(filter_)))

    for installable, destination in to_do:
        if context.installation_context.dry_run:
            _LOGGER.info("Would squash %s to %s", installable.name, destination)
        else:
            _LOGGER.info("Squashing %s to %s", installable.name, destination)
            installable.squash_to(destination)


@cli.command()
@click.pass_obj
@click.option(
    "--image-dir",
    default=Path("/opt/squash-images"),
    metavar="IMAGES",
    type=click.Path(file_okay=False, path_type=Path),
    help="Look for images in IMAGES",
    show_default=True,
)
@click.argument("filter_", metavar="FILTER", nargs=-1)
def squash_check(context: CliContext, filter_: List[str], image_dir: Path):
    """Check squash images matching FILTER."""
    if not image_dir.exists():
        _LOGGER.error("Missing squash directory %s", image_dir)
        exit(1)

    for installable in context.get_installables(filter_):
        destination = image_dir / f"{installable.install_path}.img"
        if installable.nightly_like:
            if destination.exists():
                _LOGGER.error("Found squash: %s for nightly", installable.name)
        elif not destination.exists():
            _LOGGER.error("Missing squash: %s (for %s)", installable.name, destination)

    squash_mount_check(image_dir, "", context)


def _should_install(force: bool, installable: Installable) -> Tuple[Installable, bool]:
    try:
        return installable, force or installable.should_install()
    except Exception as ex:
        raise RuntimeError(f"Unable to install {installable}") from ex


@cli.command()
@click.pass_obj
@click.option("--force", is_flag=True, help="Force even if would otherwise skip")
@click.argument("filter_", metavar="FILTER", nargs=-1)
def install(context: CliContext, filter_: List[str], force: bool):
    """Install targets matching FILTER."""
    num_installed = 0
    num_skipped = 0
    failed = []

    with context.pool() as pool:
        to_do = pool.map(partial(_should_install, force), context.get_installables(filter_))

    for installable, should_install in to_do:
        print(f"Installing {installable.name}")
        if should_install:
            try:
                installable.install()
                if context.installation_context.dry_run:
                    _LOGGER.info("Assuming %s installed OK (dry run)", installable.name)
                    num_installed += 1
                else:
                    if not installable.is_installed():
                        _LOGGER.error("%s installed OK, but doesn't appear as installed after", installable.name)
                        failed.append(installable.name)
                    else:
                        _LOGGER.info("%s installed OK", installable.name)
                        num_installed += 1
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.info("%s failed to install: %s\n%s", installable.name, e, traceback.format_exc(5))
                failed.append(installable.name)
        else:
            _LOGGER.info("%s is already installed, skipping", installable.name)
            num_skipped += 1
    print(
        f"{num_installed} packages installed "
        f'{"(apparently; this was a dry-run) " if context.installation_context.dry_run else ""}OK, '
        f"{num_skipped} skipped, and {len(failed)} failed installation"
    )
    if len(failed):
        print("Failed:")
        for f in sorted(failed):
            print(f"  {f}")
        sys.exit(1)


@cli.command()
@click.pass_obj
@click.option("--force", is_flag=True, help="Force even if would otherwise skip")
@click.option(
    "--buildfor",
    default="",
    metavar="BUILDFOR",
    help="Filter to only build for given compiler (should be a CE compiler identifier), "
    "leave empty to build for all",
)
@click.option("--popular-compilers-only", is_flag=True, help="Only build with popular (enough) compilers")
@click.option("--temp-install", is_flag=True, help="Temporary install target if it's not installed yet")
@click.argument("filter_", metavar="FILTER", nargs=-1)
def build(
    context: CliContext,
    filter_: List[str],
    force: bool,
    buildfor: str,
    popular_compilers_only: bool,
    temp_install: bool,
):
    """Build library targets matching FILTER."""
    num_installed = 0
    num_skipped = 0
    num_failed = 0

    platform = LibraryPlatform.Linux

    if "windows" in context.enabled:
        platform = LibraryPlatform.Windows

    for installable in context.get_installables(filter_):
        if buildfor:
            print(f"Building {installable.name} ({platform.value}) just for {buildfor}")
        else:
            print(f"Building {installable.name} ({platform.value}) for all")

        if force or installable.should_build():
            was_temp_installed = False

            if not installable.is_installed() and temp_install:
                _LOGGER.info("Temporarily installing %s", installable.name)
                installable.install()
                was_temp_installed = True

            if not installable.is_installed():
                _LOGGER.info("%s is not installed, unable to build", installable.name)
                num_skipped += 1
            else:
                try:
                    [num_installed, num_skipped, num_failed] = installable.build(
                        buildfor, popular_compilers_only, platform
                    )
                    if num_installed > 0:
                        _LOGGER.info("%s built OK", installable.name)
                    elif num_failed:
                        _LOGGER.info("%s failed to build", installable.name)
                    else:
                        _LOGGER.info("%s hit a BUG", installable.name)
                except RuntimeError as e:
                    if buildfor:
                        raise e
                    else:
                        _LOGGER.info("%s failed to build: %s", installable.name, e)
                        num_failed += 1

            if was_temp_installed:
                _LOGGER.info("Uninstalling temporary %s", installable.name)
                installable.uninstall()
        else:
            _LOGGER.info("%s is already built, skipping", installable.name)
            num_skipped += 1
    print(f"{num_installed} packages built OK, {num_skipped} skipped, and {num_failed} failed build")
    if num_failed:
        sys.exit(1)


@cli.command()
@click.pass_obj
def reformat(context: CliContext):
    """Reformat the YAML."""
    lib_yaml = LibraryYaml(context.installation_context.yaml_dir)
    lib_yaml.reformat()


@cli.command()
@click.pass_obj
def add_top_rust_crates(context: CliContext):
    """Add configuration for the top 100 rust crates."""
    libyaml = LibraryYaml(context.installation_context.yaml_dir)
    libyaml.add_top_rust_crates()
    libyaml.save()


@cli.command()
@click.pass_obj
def generate_rust_props(context: CliContext):
    """Generate Rust property files for crates."""
    propfile = Path(os.path.join(os.curdir, "props"))
    with propfile.open(mode="w", encoding="utf-8") as file:
        libyaml = LibraryYaml(context.installation_context.yaml_dir)
        props = libyaml.get_ce_properties_for_rust_libraries()
        file.write(props)


@cli.command()
@click.pass_obj
@click.argument("libid")
@click.argument("libversion")
def add_crate(context: CliContext, libid: str, libversion: str):
    """Add crate LIBID version LIBVERSION."""
    libyaml = LibraryYaml(context.installation_context.yaml_dir)
    libyaml.add_rust_crate(libid, libversion)
    libyaml.save()


@cli.command()
@click.argument("output", type=click.File("w", encoding="utf-8"), default="-")
@click.pass_obj
def config_dump(context: CliContext, output: TextIO):
    """Dumps all config, expanded."""
    for yaml_path in sorted(Path(context.installation_context.yaml_dir).glob("*.yaml")):
        with yaml_path.open(encoding="utf-8") as yaml_file:
            yaml_doc = yaml.load(yaml_file, Loader=ConfigSafeLoader)
        for installer in sorted(installers_for(context.installation_context, yaml_doc, True), key=str):
            # Read all public strings fields from installer
            as_dict = {
                "name": installer.name,
                "type": str(installer),
                "config": {
                    field: getattr(installer, field)
                    for field in dir(installer)
                    if not field.startswith("_") and isinstance(getattr(installer, field), str)
                },
            }
            output.write(json.dumps(as_dict) + "\n")


def main():
    cli(prog_name="ce_install")  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter


if __name__ == "__main__":
    main()
