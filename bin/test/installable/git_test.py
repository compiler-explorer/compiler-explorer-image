import subprocess
from pathlib import Path
from unittest import mock

import pytest
import logging

from lib.installable.git import GitHubInstallable
from lib.installation_context import InstallationContext
from lib.staging import StagingDir
import os


@pytest.fixture(autouse=True, scope="session")
def _ensure_no_git_set():
    # This env var is set during git hooks and interferes with the git stuff we do below.
    if "GIT_INDEX_FILE" in os.environ:
        del os.environ["GIT_INDEX_FILE"]


@pytest.fixture(autouse=True, scope="session")
def _ensure_no_global_config():
    os.environ["GIT_CONFIG_GLOBAL"] = ""
    os.environ["GIT_CONFIG_SYSTEM"] = ""


@pytest.fixture(name="fake_context")
def fake_context_fixture():
    ctx = mock.Mock(spec=InstallationContext)
    ctx.only_nightly = False
    ctx.only_native_aarch64 = False
    return ctx


@pytest.fixture(name="fake_context_nightly")
def fake_context_nightly_fixture():
    ctx = mock.Mock(spec=InstallationContext)
    ctx.only_nightly = True
    ctx.only_native_aarch64 = False
    return ctx


@pytest.fixture(name="staging_path")
def staging_path_fixture(tmp_path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    return staging


@pytest.fixture(name="fake_remote_repo")
def fake_remote_repo_fixture(tmp_path) -> str:
    repo = tmp_path / "some-remote-repo"
    repo.mkdir()
    subprocess.check_call(["git", "init"], cwd=repo)
    subprocess.check_call(["git", "config", "--local", "user.email", "nobody@nowhere.not.real"], cwd=repo)
    subprocess.check_call(["git", "config", "--local", "user.name", "Not a Real Person"], cwd=repo)
    (repo / "some_file.txt").touch()
    subprocess.check_call(["git", "add", "some_file.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-minitial"], cwd=repo)
    return str(repo)


@pytest.fixture(name="staging_dir")
def staging_dir_fixture(staging_path) -> StagingDir:
    staging = mock.Mock(spec_set=StagingDir)
    staging.path = staging_path
    return staging


def test_git_install_from_scratch(fake_context, staging_dir, tmp_path, fake_remote_repo):
    fake_context.prior_installation = tmp_path / "nonexistent"
    ghi = GitHubInstallable(
        fake_context,
        dict(
            context=["outer", "inner"],
            name="fake",
            domainrepo="",
            repo=fake_remote_repo,
            check_file="fake-none",
            method="nightlyclone",
        ),
    )
    dest = ghi.clone(staging_dir, remote_url=fake_remote_repo, branch=None)
    assert dest.is_relative_to(staging_dir.path)
    assert (dest / "some_file.txt").exists()


def _make_ro(path: Path) -> None:
    for item in path.rglob("*"):
        item.chmod(item.stat().st_mode & 0o555)


@pytest.fixture(name="previously_installed_ghi")
def previously_installed_ghi_ficture(fake_context, tmp_path, fake_remote_repo, staging_dir):
    prior_root = tmp_path / "prior_version"
    fake_context.prior_installation = fake_context.destination = prior_root
    ghi = GitHubInstallable(
        fake_context,
        dict(
            context=["outer", "inner"],
            name="fake",
            domainrepo="",
            repo=fake_remote_repo,
            check_file="fake-none",
            method="nightlyclone",
        ),
    )
    # Fake out an installation at prior_version
    original = ghi.clone(staging_dir, remote_url=fake_remote_repo, branch=None)
    prior_version = prior_root / "libs" / "inner" / "fake"
    prior_version.parent.mkdir(parents=True)
    original.replace(prior_version)
    # Make the prior version read only
    _make_ro(prior_version)
    return ghi


def test_git_install_from_prior_version(previously_installed_ghi, caplog, fake_remote_repo, staging_dir):
    # Update the remote repo
    (Path(fake_remote_repo) / "some_new_file.txt").touch()
    subprocess.check_call(["git", "add", "some_new_file.txt"], cwd=fake_remote_repo)
    subprocess.check_call(["git", "commit", "-mupdated"], cwd=fake_remote_repo)

    # check we updated
    with caplog.at_level(logging.DEBUG):
        new_dest = previously_installed_ghi.clone(staging_dir, remote_url=fake_remote_repo, branch=None)
    assert new_dest.is_relative_to(staging_dir.path)
    assert (new_dest / "some_file.txt").exists()
    assert (new_dest / "some_new_file.txt").exists()

    # Naff way of ensuring we did actually bootstrap
    assert "Bootstrapping from existing branch at" in caplog.text


def test_should_install_when_not_present(fake_context, tmp_path, fake_remote_repo):
    fake_context.prior_installation = fake_context.destination = tmp_path / "nonexistent"
    ghi = GitHubInstallable(
        fake_context,
        dict(context=["outer", "inner"], name="fake", domainrepo="", repo=fake_remote_repo, check_file="fake-none"),
    )
    assert ghi.should_install()


def test_should_not_install_when_not_nightly(fake_context_nightly, tmp_path, fake_remote_repo):
    fake_context_nightly.prior_installation = fake_context_nightly.destination = tmp_path / "nonexistent"
    ghi = GitHubInstallable(
        fake_context_nightly,
        dict(context=["outer", "inner"], name="fake", domainrepo="", repo=fake_remote_repo, check_file="fake-none"),
    )
    assert not ghi.should_install()


def test_should_install_when_nightly(fake_context_nightly, tmp_path, fake_remote_repo):
    fake_context_nightly.prior_installation = fake_context_nightly.destination = tmp_path / "nonexistent"
    ghi = GitHubInstallable(
        fake_context_nightly,
        dict(
            context=["outer", "inner"],
            name="fake",
            domainrepo="",
            method="nightlyclone",
            repo=fake_remote_repo,
            check_file="fake-none",
        ),
    )
    assert ghi.should_install()


def test_should_not_install_when_present_and_up_to_date(previously_installed_ghi):
    assert not previously_installed_ghi.should_install()


def test_should_install_when_present_but_remote_has_updated(previously_installed_ghi, fake_remote_repo):
    # Update the remote repo
    (Path(fake_remote_repo) / "some_new_file.txt").touch()
    subprocess.check_call(["git", "add", "some_new_file.txt"], cwd=fake_remote_repo)
    subprocess.check_call(["git", "commit", "-mupdated"], cwd=fake_remote_repo)
    assert previously_installed_ghi.should_install()
