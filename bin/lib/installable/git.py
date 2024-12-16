from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Union, Optional

from lib.installable.installable import Installable
from lib.staging import StagingDir

_CLONE_METHODS = {"clone_branch", "nightlyclone", "nightlybranch"}
_ARCHIVE_METHOD = "archive"
_VALID_METHODS = _CLONE_METHODS | {_ARCHIVE_METHOD}
_LOGGER = logging.getLogger(__name__)


def _git_raw(logger: logging.Logger, cwd: Path, *git_args: Union[str, Path]) -> str:
    full_args = ["git"] + [str(x) for x in git_args]
    _LOGGER.debug("(in %s): %s", cwd, shlex.join(full_args))
    result = subprocess.check_output(full_args, cwd=cwd).decode("utf-8").strip()
    if result:
        logger.debug(" -> %s", result)
    return result


def _remote_default_branch_for(logger: logging.Logger, git_repo: Path) -> str:
    match_re = re.compile(r"^ref:\s+refs/heads/(\S+)\s+HEAD$")
    for line in _git_raw(logger, git_repo, "ls-remote", "--symref", "origin", "HEAD").splitlines(keepends=False):
        if match := match_re.match(line):
            return match.group(1)
    raise RuntimeError(f"Unable to detect remote default branch for {git_repo}")


def _remote_get_current_hash(logger: logging.Logger, git_repo: Path, branch: str) -> str:
    match_re = re.compile(r"^([a-f0-9]+)\s+(.*)$")
    for line in _git_raw(logger, git_repo, "ls-remote", "origin", branch).splitlines(keepends=False):
        if (match := match_re.match(line)) is not None and match.group(2) == f"refs/heads/{branch}":
            return match.group(1)
    raise RuntimeError(f"Unable to get remote hash for {git_repo}:{branch}")


def _git_current_hash(logger: logging.Logger, git_dir: Path) -> str:
    # These two are for debug output:
    _git_raw(logger, git_dir, "log", "--oneline", "-n5")  # done for debug only
    return _git_raw(logger, git_dir, "rev-parse", "HEAD").strip()


class GitHubInstallable(Installable):
    def __init__(self, install_context, config):
        super().__init__(install_context, config)
        last_context = self.context[-1]
        self.repo = self.config_get("repo")
        self.domainurl = self.config_get("domainurl", "https://github.com")
        self.method = self.config_get("method", "archive")
        if self.method not in _VALID_METHODS:
            raise RuntimeError(f"Not a valid method: {self.method}")
        self.decompress_flag = self.config_get("decompress_flag", "z")
        self.strip = False
        self.subdir = os.path.join("libs", self.config_get("subdir", last_context))
        self.target_prefix = self.config_get("target_prefix", "")
        self.branch_name = self.target_prefix + self.target_name
        self.install_path = self.config_get("path_name", os.path.join(self.subdir, self.branch_name))
        self.recursive = self.config_get("recursive", True)

        splitrepo = self.repo.split("/")
        self.reponame = splitrepo[1]
        default_untar_dir = f"{self.reponame}-{self.target_name}"
        self.untar_dir = self.config_get("untar_dir", default_untar_dir)

        if not self.check_file:
            if self.build_config.build_type == "cmake":
                self.check_file = "CMakeLists.txt"
            elif self.build_config.build_type == "make":
                self.check_file = "Makefile"
            elif self.build_config.build_type == "cake":
                self.check_file = "config.cake"
            elif self.build_config.build_type == "cargo":
                self.check_file = None
            else:
                raise RuntimeError(f"Requires check_file ({last_context})")

        self.install_always = self.install_always or self.nightly_like

    def _update_args(self):
        if self.recursive:
            return ["--recursive"]
        return []

    def _git(self, staging: StagingDir, *git_args: Union[str, Path]) -> str:
        return _git_raw(self._logger, staging.path, *git_args)

    def clone(self, staging: StagingDir, remote_url: str, branch: Optional[str]) -> Path:
        self._logger.info("Cloning %s, branch: %s", remote_url, branch or "(default)")
        prior_installation = self.install_context.prior_installation / self.install_path
        dest = staging.path / self.install_path

        # We assume the prior may be read only. If it exists we use it as a quick starting point only.
        if prior_installation.exists():
            self._logger.info(
                "Bootstrapping from existing branch at %s", _git_current_hash(self._logger, prior_installation)
            )
            self._git(staging, "clone", "-n", "-q", prior_installation, dest)
        else:
            self._git(staging, "clone", "-n", "-q", remote_url, dest)

        def _git(*git_args: Union[str, Path]) -> str:
            return self._git(staging, "-C", dest, *git_args)

        # Ensure we're not configured to do any detached background work which will get upset when we start moving
        # things around. See https://github.com/compiler-explorer/compiler-explorer/issues/4813
        _git("config", "gc.autodetach", "false")

        # Ensure we are pulling from the correct URL, and fetch latest.
        _git("remote", "set-url", "origin", remote_url)
        _git("fetch", "-q")

        # Borrowed from github actions; this is how it "cleans" an existing directory prior to checkout.
        if _git("branch", "--show-current"):
            # detach if not detached
            _git("checkout", "--detach")

        # List and remove all local branches
        for existing_branch in [
            existing_branch.removeprefix("refs/heads/").removeprefix("refs/remotes/")
            for existing_branch in _git("rev-parse", "--symbolic-full-name", "--branches").splitlines(keepends=False)
            if existing_branch
        ]:
            _git("branch", "--delete", "--force", existing_branch)

        _git("clean", "-ffdx")
        _git("reset", "--hard", "HEAD")

        to_checkout = branch or self._find_remote_branch(dest)
        if _git("branch", "--list", f"origin/{to_checkout}").strip():
            to_checkout = f"origin/{to_checkout}"
        _git("checkout", to_checkout)

        _git("submodule", "sync")
        _git("submodule", "update", "--init", *self._update_args())
        self._logger.info("Now at %s", _git_current_hash(self._logger, dest))
        return dest

    def _find_remote_branch(self, git_repo: Path) -> str:
        branch = _remote_default_branch_for(self._logger, git_repo)
        self._logger.info("Detected remote default branch as '%s'", branch)
        return branch

    def uses_explicit_branch(self) -> bool:
        return self.method == "clone_branch" or self.method == "nightlybranch"

    def should_install(self) -> bool:
        if not super().should_install():
            return False
        if self.method in _CLONE_METHODS:
            prior_installation = self.install_context.prior_installation / self.install_path
            if prior_installation.exists():
                branch = (
                    self.branch_name if self.uses_explicit_branch() else self._find_remote_branch(prior_installation)
                )
                remote_hash = _remote_get_current_hash(self._logger, prior_installation, branch)
                local_hash = _git_current_hash(self._logger, prior_installation)
                needs_install = remote_hash != local_hash
                self._logger.info(
                    "remote hash: %s, current hash %s, %s",
                    remote_hash,
                    local_hash,
                    "needs installation" if needs_install else "installation is up to date",
                )
                return needs_install
        return True

    def get_archive_url(self):
        return f"{self.domainurl}/{self.repo}/archive/{self.target_prefix}{self.target_name}.tar.gz"

    def get_archive_pipecommand(self):
        return ["tar", f"{self.decompress_flag}xf", "-"]

    @property
    def nightly_like(self) -> bool:
        return self.method == "nightlyclone" or self.method == "nightlybranch"

    def stage(self, staging: StagingDir):
        if self.method == _ARCHIVE_METHOD:
            self.install_context.fetch_url_and_pipe_to(staging, self.get_archive_url(), self.get_archive_pipecommand())
            staged_dest = staging.path / self.untar_dir
        elif self.method in _CLONE_METHODS:
            staged_dest = self.clone(
                staging,
                remote_url=f"{self.domainurl}/{self.repo}.git",
                branch=self.branch_name if self.uses_explicit_branch() else None,
            )
        else:
            raise RuntimeError(f"Unknown Github method {self.method}")

        if self.strip:
            self.install_context.strip_exes(staging, self.strip)

        self.install_context.run_script(staging, staged_dest, self.after_stage_script)

    def verify(self):
        if not super().verify():
            return False
        with self.install_context.new_staging_dir() as staging:
            self.stage(staging)
            return self.install_context.compare_against_staging(staging, self.untar_dir, self.install_path)

    def install(self) -> None:
        super().install()
        with self.install_context.new_staging_dir() as staging:
            self.stage(staging)
            if self.subdir:
                self.install_context.make_subdir(self.subdir)
            self.install_context.move_from_staging(
                staging, self.untar_dir if self.method == "archive" else self.install_path, self.install_path
            )

    def __repr__(self) -> str:
        return f"GitHubInstallable({self.name}, {self.install_path})"


class GitLabInstallable(GitHubInstallable):
    def __init__(self, install_context, config):
        super().__init__(install_context, config)
        self.domainurl = self.config_get("domainurl", "https://gitlab.com")

    def get_archive_url(self):
        return f"{self.domainurl}/{self.repo}/-/archive/{self.target_name}/{self.reponame}-{self.target_name}.tar.gz"

    def __repr__(self) -> str:
        return f"GitLabInstallable({self.name}, {self.install_path})"


class BitbucketInstallable(GitHubInstallable):
    def __init__(self, install_context, config):
        super().__init__(install_context, config)
        self.domainurl = self.config_get("domainurl", "https://bitbucket.org")

    def get_archive_url(self):
        return f"{self.domainurl}/{self.repo}/downloads/{self.reponame}-{self.target_name}.tar.gz"

    def __repr__(self) -> str:
        return f"BitbucketInstallable({self.name}, {self.install_path})"
