from __future__ import annotations

from collections import ChainMap
from datetime import datetime

from lib.config_expand import is_value_type, expand_target
from lib.installable.archives import (
    S3TarballInstallable,
    NightlyInstallable,
    TarballInstallable,
    NightlyTarballInstallable,
    ZipArchiveInstallable,
    RestQueryTarballInstallable,
    NonFreeS3TarballInstallable,
)
from lib.installable.edg import EdgCompilerInstallable
from lib.installable.git import GitHubInstallable, GitLabInstallable, BitbucketInstallable
from lib.installable.installable import SingleFileInstallable
from lib.installable.python import PipInstallable
from lib.installable.rust import RustInstallable, CratesIOInstallable
from lib.installable.script import ScriptInstallable
from lib.installable.solidity import SolidityInstallable
from lib.installable.go import GoInstallable

def targets_from(node, enabled, base_config=None):
    if base_config is None:
        base_config = {}
    return _targets_from(node, enabled, [], "", base_config)


def _check_if(enabled, node) -> bool:
    if "if" not in node or enabled is True:
        return True
    if isinstance(node["if"], list):
        condition = set(node["if"])
    else:
        condition = {node["if"]}
    return set(enabled).intersection(condition) == condition


def _targets_from(node, enabled, context, name, base_config):
    if not node:
        return

    if isinstance(node, list):
        for child in node:
            for target in _targets_from(child, enabled, context, name, base_config):
                yield target
        return

    if not isinstance(node, dict):
        return

    if not _check_if(enabled, node):
        return

    context = context[:]
    if name:
        context.append(name)
    base_config = dict(base_config)
    for key, value in node.items():
        if key != "targets" and is_value_type(value):
            base_config[key] = value

    for child_name, child in node.items():
        for target in _targets_from(child, enabled, context, child_name, base_config):
            yield target

    if "targets" in node:
        base_config["context"] = context
        for target in node["targets"]:
            if isinstance(target, float):
                raise RuntimeError(f"Target {target} was parsed as a float. Enclose in quotes")
            if isinstance(target, str):
                target = {"name": target, "underscore_name": target.replace(".", "_")}
            elif not _check_if(enabled, target):
                continue
            yield expand_target(ChainMap(target, base_config), context)


_INSTALLER_TYPES = {
    "tarballs": TarballInstallable,
    "restQueryTarballs": RestQueryTarballInstallable,
    "s3tarballs": S3TarballInstallable,
    "nightlytarballs": NightlyTarballInstallable,
    "nightly": NightlyInstallable,
    "script": ScriptInstallable,
    "solidity": SolidityInstallable,
    "singleFile": SingleFileInstallable,
    "github": GitHubInstallable,
    "gitlab": GitLabInstallable,
    "bitbucket": BitbucketInstallable,
    "rust": RustInstallable,
    "pip": PipInstallable,
    "ziparchive": ZipArchiveInstallable,
    "cratesio": CratesIOInstallable,
    "non-free-s3tarballs": NonFreeS3TarballInstallable,
    "edg": EdgCompilerInstallable,
    "golib": GoInstallable,
}


def installers_for(install_context, nodes, enabled):
    for target in targets_from(
        nodes,
        enabled,
        dict(
            destination=install_context.destination,
            yaml_dir=install_context.yaml_dir,
            resource_dir=install_context.resource_dir,
            now=datetime.now(),
        ),
    ):
        assert "type" in target
        target_type = target["type"]
        if target_type not in _INSTALLER_TYPES:
            raise RuntimeError(f"Unknown installer type {target_type}")
        installer_type = _INSTALLER_TYPES[target_type]
        yield installer_type(install_context, target)
