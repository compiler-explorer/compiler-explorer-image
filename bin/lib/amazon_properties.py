import os
import re
import urllib.parse
from collections import defaultdict
from typing import Dict, Any
from lib.library_platform import LibraryPlatform

import requests


def get_specific_library_version_details(libraries, libid, library_version):
    if "versionprops" in libraries[libid]:
        for verid in libraries[libid]["versionprops"]:
            version_props = libraries[libid]["versionprops"][verid]
            if version_props["version"] == library_version or version_props["lookupversion"] == library_version:
                return version_props

    return False


COMPILEROPT_RE = re.compile(r"(\w*)\.(.*)\.(\w*)")


def get_properties_compilers_and_libraries(
    language, logger, platform: LibraryPlatform, filter_binary_support: bool = True
):
    _compilers: Dict[str, Dict[str, Any]] = defaultdict(lambda: {})
    _libraries: Dict[str, Dict[str, Any]] = defaultdict(lambda: {})

    encoded_language = urllib.parse.quote(language)

    if platform == LibraryPlatform.Linux:
        url = f"https://raw.githubusercontent.com/compiler-explorer/compiler-explorer/main/etc/config/{encoded_language}.amazon.properties"
    elif platform == LibraryPlatform.Windows:
        url = f"https://raw.githubusercontent.com/compiler-explorer/compiler-explorer/main/etc/config/{encoded_language}.amazonwin.properties"
    else:
        raise RuntimeError("Unsupported platform")

    request = requests.get(url, timeout=30)
    if not request.ok:
        raise RuntimeError(f"Fetch failure for {url}: {request}")
    lines = request.text.splitlines(keepends=False)

    if platform == LibraryPlatform.Windows and "libs=\n" in request.text:
        # Windows properties file is missing the libs section, so we need to fetch the Linux one to kickstart Windows libraries
        #  but technically it's better to always supply enough information in the yaml file, so this is a workaround
        request = requests.get(
            f"https://raw.githubusercontent.com/compiler-explorer/compiler-explorer/main/etc/config/{encoded_language}.amazon.properties",
            timeout=30,
        )
        if not request.ok:
            raise RuntimeError(f"Fetch failure for {url}: {request}")
        lines += request.text[request.text.index("libs=") :].splitlines(keepends=False)

    logger.debug("Reading properties for groups")
    groups: Dict[str, Dict[str, Any]] = defaultdict(lambda: {})
    for line in lines:
        if line.startswith("group."):
            keyval = line.split("=", 1)
            key = keyval[0].split(".")
            val = keyval[1]
            group = key[1]

            if key[2] == "compilers":
                groups[group]["compilers"] = val.split(":")
            elif key[2] == "options":
                groups[group]["options"] = val
            elif key[2] == "compilerType":
                groups[group]["compilerType"] = val
            elif key[2] == "supportsBinary":
                groups[group]["supportsBinary"] = val == "true"
            elif key[2] == "ldPath":
                groups[group]["ldPath"] = val
            elif key[2] == "libPath":
                groups[group]["libPath"] = val
            elif key[2] == "includePath":
                groups[group]["includePath"] = val
        elif line.startswith("libs."):
            keyval = line.split("=", 1)
            key = keyval[0].split(".")
            val = keyval[1]
            libid = key[1]

            if key[2] == "description":
                _libraries[libid]["description"] = val
            elif key[2] == "name":
                _libraries[libid]["name"] = val
            elif key[2] == "url":
                _libraries[libid]["url"] = val
            elif key[2] == "liblink":
                _libraries[libid]["liblink"] = val.split(":")
            elif key[2] == "staticliblink":
                _libraries[libid]["staticliblink"] = val.split(":")
            elif key[2] == "versions":
                if len(key) > 3:
                    versionid = key[3]
                    if "versionprops" not in _libraries[libid]:
                        _libraries[libid]["versionprops"] = {}
                    if versionid not in _libraries[libid]["versionprops"]:
                        _libraries[libid]["versionprops"][versionid] = defaultdict(lambda: [])
                    if len(key) > 4:
                        if key[4] == "version":
                            _libraries[libid]["versionprops"][versionid][key[4]] = val
                        if key[4] == "lookupversion":
                            _libraries[libid]["versionprops"][versionid][key[4]] = val
                        if key[4] == "path":
                            _libraries[libid]["versionprops"][versionid][key[4]] = val.split(":")
                        if key[4] == "libpath":
                            _libraries[libid]["versionprops"][versionid][key[4]] = val.split(":")
                        if key[4] == "staticliblink":
                            _libraries[libid]["versionprops"][versionid][key[4]] = val.split(":")
                        if key[4] == "liblink":
                            _libraries[libid]["versionprops"][versionid][key[4]] = val.split(":")
                else:
                    _libraries[libid]["versions"] = val

    logger.debug("Setting default values for compilers")
    for group in groups:
        for compiler in groups[group]["compilers"]:
            if "&" in compiler:
                subgroupname = compiler[1:]
                if "options" not in groups[subgroupname] and "options" in groups[group]:
                    groups[subgroupname]["options"] = groups[group]["options"]
                if "compilerType" not in groups[subgroupname] and "compilerType" in groups[group]:
                    groups[subgroupname]["compilerType"] = groups[group]["compilerType"]
                if "supportsBinary" not in groups[subgroupname] and "supportsBinary" in groups[group]:
                    groups[subgroupname]["supportsBinary"] = groups[group]["supportsBinary"]
                if "ldPath" not in groups[subgroupname] and "ldPath" in groups[group]:
                    groups[subgroupname]["ldPath"] = groups[group]["ldPath"]
                if "libPath" not in groups[subgroupname] and "libPath" in groups[group]:
                    groups[subgroupname]["libPath"] = groups[group]["libPath"]
                if "includePath" not in groups[subgroupname] and "includePath" in groups[group]:
                    groups[subgroupname]["includePath"] = groups[group]["includePath"]
            else:
                _compilers[compiler]["options"] = groups[group].get("options", "")
                _compilers[compiler]["compilerType"] = groups[group].get("compilerType", "")
                _compilers[compiler]["supportsBinary"] = groups[group].get("supportsBinary", True)
                _compilers[compiler]["ldPath"] = groups[group].get("ldPath", "")
                _compilers[compiler]["libPath"] = groups[group].get("libPath", "")
                _compilers[compiler]["includePath"] = groups[group].get("includePath", "")
                _compilers[compiler]["group"] = group

    logger.debug("Reading properties for compilers")
    for line in lines:
        if line.startswith("compiler."):
            compilerkey, compilervalue = line.split("=", 1)
            matches = COMPILEROPT_RE.match(compilerkey)
            if not matches:
                raise RuntimeError(f"Not a valid compiler? {compilerkey}={compilervalue}")
            compiler = matches[2]
            compilerid = matches[3]

            if key == "supportsBinary":
                _compilers[compiler][compilerid] = compilervalue == "true"
            else:
                _compilers[compiler][compilerid] = compilervalue

    if filter_binary_support:
        logger.debug("Removing compilers that are not available or do not support binaries")
        keys_to_remove = set()
        for compiler in _compilers:
            if "supportsBinary" in _compilers[compiler] and not _compilers[compiler]["supportsBinary"]:
                logger.debug("%s does not supportsBinary", compiler)
                keys_to_remove.add(compiler)
            elif "compilerType" in _compilers[compiler] and _compilers[compiler]["compilerType"] == "wine-vc":
                keys_to_remove.add(compiler)
                logger.debug("%s is wine", compiler)
            elif "exe" in _compilers[compiler]:
                exe = _compilers[compiler]["exe"]
                if not os.path.exists(exe):
                    keys_to_remove.add(compiler)
                    logger.debug("%s does not exist (looked for %s)", compiler, exe)
            else:
                keys_to_remove.add(compiler)
                logger.debug("%s didn't have the required config keys", compiler)

        for compiler in keys_to_remove:
            logger.debug("removing %s", compiler)
            del _compilers[compiler]

    return [_compilers, _libraries]
