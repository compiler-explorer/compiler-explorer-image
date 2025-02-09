import datetime
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from typing import Optional, Dict, Sequence

import click
import requests

from lib.amazon import (
    download_release_file,
    download_release_fileobj,
    find_latest_release,
    find_release,
    get_all_releases,
    get_key_counterpart,
    log_new_build,
    set_current_key,
    get_ssm_param,
    get_all_current,
    get_releases,
    remove_release,
    get_current_key,
    list_all_build_logs,
    list_period_build_logs,
    put_bouncelock_file,
    delete_bouncelock_file,
    has_bouncelock_file,
    set_current_notify,
)
from lib.cdn import DeploymentJob
from lib.ce_utils import describe_current_release, are_you_sure, display_releases, confirm_branch, confirm_action
from lib.cli import cli
from lib.cli.runner import runner_discoveryexists
from lib.env import Config, Environment
from lib.releases import Version, Release, VersionSource


@cli.group()
def builds():
    """Build manipulation commands."""


@builds.command(name="current")
@click.pass_obj
def builds_current(cfg: Config):
    """Print the current release."""
    print(describe_current_release(cfg))


def old_deploy_staticfiles(branch, versionfile):
    print("Deploying static files")
    downloadfile = versionfile
    filename = "deploy.tar.xz"
    remotefile = branch + "/" + downloadfile
    download_release_file(remotefile[1:], filename)
    os.mkdir("deploy")
    subprocess.call(["tar", "-C", "deploy", "-Jxf", filename])
    os.remove(filename)
    subprocess.call(["aws", "s3", "sync", "deploy/out/dist/dist", "s3://compiler-explorer/dist/cdn"])
    subprocess.call(["rm", "-Rf", "deploy"])


def deploy_staticfiles_windows(release) -> bool:
    print("Deploying static files to cdn (Windows)")
    cc = f"public, max-age={int(datetime.timedelta(days=365).total_seconds())}"

    with tempfile.NamedTemporaryFile(suffix=os.path.basename(release.static_key)) as f:
        download_release_fileobj(release.static_key, f)
        f.flush()
        with DeploymentJob(
            f.name, "ce-cdn.net", version=release.version, cache_control=cc, bucket_path="windows"
        ) as job:
            return job.run()


def deploy_staticfiles(release) -> bool:
    print("Deploying static files to cdn")
    cc = f"public, max-age={int(datetime.timedelta(days=365).total_seconds())}"

    with tempfile.NamedTemporaryFile(suffix=os.path.basename(release.static_key)) as f:
        download_release_fileobj(release.static_key, f)
        f.flush()
        with DeploymentJob(f.name, "ce-cdn.net", version=release.version, cache_control=cc) as job:
            return job.run()


def check_staticfiles_for_deployment(release) -> bool:
    print("Checking static files for cdn deployment")
    cc = f"public, max-age={int(datetime.timedelta(days=365).total_seconds())}"

    with tempfile.NamedTemporaryFile(suffix=os.path.basename(release.static_key)) as f:
        download_release_fileobj(release.static_key, f)
        f.flush()
        with DeploymentJob(f.name, "ce-cdn.net", version=release.version, cache_control=cc) as job:
            if job.check_hashes():
                print("No problems found")
                return True
            else:
                print("New webpackJsHack version number required to deploy static files to cdn")
                return False


@builds.command(name="check_hashes")
@click.pass_obj
@click.option("--branch", help="if version == latest, branch to get latest version from")
@click.option("--raw/--no-raw", help="Set a raw path for a version")
@click.argument("version")
def check_hashes(cfg: Config, branch: Optional[str], version: str, raw: bool):
    """Checks the static files for this version."""
    to_set: Optional[str] = None
    release: Optional[Release] = None
    if raw:
        to_set = version
    else:
        setting_latest = version == "latest"
        release = (
            find_latest_release(cfg, branch or "")
            if setting_latest
            else find_release(cfg, Version.from_string(version))
        )
        if not release:
            print("Unable to find version " + version)
            if setting_latest and branch != "":
                print("Branch {} has no available versions (Bad branch/No image yet built)".format(branch))
            sys.exit(1)
        else:
            to_set = release.key

    if to_set is not None and release is not None:
        if release and release.static_key:
            if not check_staticfiles_for_deployment(release):
                sys.exit(1)


@builds.command(name="set_current")
@click.pass_obj
@click.option("--branch", help="if version == latest, branch to get latest version from")
@click.option("--raw/--no-raw", help="Set a raw path for a version")
@click.option("--confirm", help="Skip confirmation questions", is_flag=True)
@click.argument("version")
def builds_set_current(cfg: Config, branch: Optional[str], version: str, raw: bool, confirm: bool):
    """Set the current version to VERSION for this environment.

    If VERSION is "latest" then the latest version (optionally filtered by --branch), is set.
    """
    if has_bouncelock_file(cfg):
        print(f"{cfg.env.value} is currently bounce locked. New versions can't be set until the lock is lifted")
        sys.exit(1)
    to_set: Optional[str] = None
    release: Optional[Release] = None
    if raw:
        to_set = version
    else:
        setting_latest = version == "latest"
        release = (
            find_latest_release(cfg, branch or "")
            if setting_latest
            else find_release(cfg, Version.from_string(version))
        )
        if not release:
            print("Unable to find version " + version)
            if setting_latest and branch != "":
                print("Branch {} has no available versions (Bad branch/No image yet built)".format(branch))
            sys.exit(1)
        elif confirm:
            print(f"Found release {release}")
            to_set = release.key
        elif are_you_sure("change current version to {}".format(release.key), cfg) and confirm_branch(release.branch):
            print(f"Found release {release}")
            to_set = release.key
    if to_set is not None and release is not None:
        if (
            (cfg.env.value != "runner")
            and not cfg.env.is_windows
            and not runner_discoveryexists(cfg.env.value, str(release.version))
        ):
            if not confirm_action(
                f"Compiler discovery has not run for {cfg.env.value}/{release.version}, are you sure you want to continue?"
            ):
                sys.exit(1)

        log_new_build(cfg, to_set)
        if release and release.static_key:
            if cfg.env.is_windows:
                if not deploy_staticfiles_windows(release):
                    print("...aborted due to deployment failure!")
                    sys.exit(1)
            else:
                if not deploy_staticfiles(release):
                    print("...aborted due to deployment failure!")
                    sys.exit(1)
        else:
            old_deploy_staticfiles(branch, to_set)
        set_current_key(cfg, to_set)
        if release:
            if cfg.env.value == cfg.env.PROD:
                print("Logging for notifications")
                set_current_notify(release.hash.hash)
            print("Marking as a release in sentry...")
            token = get_ssm_param("/compiler-explorer/sentryAuthToken")
            result = requests.post(
                f"https://sentry.io/api/0/organizations/compiler-explorer/releases/{release.version}/deploys/",
                data=dict(environment=cfg.env.value),
                headers=dict(Authorization=f"Bearer {token}"),
                timeout=30,
            )
            if not result.ok:
                raise RuntimeError(f"Failed to send to sentry: {result} {result.content.decode('utf-8')}")
            print("...done", json.loads(result.content.decode()))


@builds.command(name="rm_old")
@click.option("--dry-run/--no-dry-run", help="dry run only")
@click.argument("max_age", type=int)
def builds_rm_old(dry_run: bool, max_age: int):
    """Remove all but the last MAX_AGE builds."""
    current = get_all_current()
    max_builds: Dict[VersionSource, int] = defaultdict(int)
    for release in get_all_releases():
        max_builds[release.version.source] = max(release.version.number, max_builds[release.version.source])
    for release in get_all_releases():
        counterpart = get_key_counterpart(release.key)
        if release.key in current or counterpart in current:
            print(f"Skipping {release} as it is a current version")
            if dry_run:
                if counterpart in current:
                    print(f"Skipping because of counterpart {counterpart}")
        else:
            age = max_builds[release.version.source] - release.version.number
            if age > max_age:
                if dry_run:
                    print(f"Would remove build {release} (age {age})")
                else:
                    print(f"Removing build {release} (age {age})")
                    remove_release(release)
            else:
                print(f"Keeping build {release} (age {age})")


@builds.command(name="list")
@click.pass_obj
@click.option(
    "-b",
    "--branch",
    type=str,
    help="show only BRANCH (may be specified more than once)",
    metavar="BRANCH",
    multiple=True,
)
def builds_list(cfg: Config, branch: Sequence[str]):
    """List available builds.

    The --> indicates the build currently deployed in this environment."""
    current = get_current_key(cfg) or ""
    releases = get_releases(cfg)
    display_releases(current, set(branch), releases)


@builds.command(name="history")
@click.option("--from", "from_time")
@click.option("--until", "until_time")
@click.pass_obj
def builds_history(cfg: Config, from_time: Optional[str], until_time: Optional[str]):
    """Show the history of current versions for this environment."""
    if from_time is None and until_time is None:
        if confirm_action(
            "Do you want list all builds for {}? It might be an expensive operation:".format(cfg.env.value)
        ):
            list_all_build_logs(cfg)
    else:
        list_period_build_logs(cfg, from_time, until_time)


@builds.command(name="is_locked")
@click.pass_obj
def builds_is_locked(cfg: Config):
    """Check whether the current env is version bounce locked."""
    if has_bouncelock_file(cfg):
        print(f"Env {cfg.env.value} is currently locked from version bounce")
    else:
        print(f"Env {cfg.env.value} is NOT locked from version bounce")


@builds.command(name="lock")
@click.pass_obj
def builds_lock(cfg: Config):
    """Lock version bounce for the specified env."""
    put_bouncelock_file(cfg)


@builds.command(name="unlock")
@click.pass_obj
def builds_unlock(cfg: Config):
    """Unlock version bounce for the specified env."""
    delete_bouncelock_file(cfg)


@builds.command(name="diff")
@click.option("--dest-env", help="env to compare with", default=Environment.PROD.value)
@click.pass_obj
def builds_diff(cfg: Config, dest_env: str):
    """Opens a URL that diffs changes from this environment and another."""
    releases = get_releases(cfg)
    (current,) = [x for x in releases if x.key == get_current_key(cfg)]
    (dest,) = [x for x in releases if x.key == get_current_key(Config(env=Environment(dest_env)))]
    url = f"https://github.com/compiler-explorer/compiler-explorer/compare/{dest.version}...{current.version}"
    print(f"Opening {url}")
    os.system(f"open {url}")
