import contextlib
import csv
import glob
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from enum import Enum, unique
from pathlib import Path
import time
from typing import Dict, Any, List, Optional, Generator, TextIO
from urllib3.exceptions import ProtocolError

import requests

from lib.amazon import get_ssm_param
from lib.amazon_properties import get_specific_library_version_details, get_properties_compilers_and_libraries
from lib.binary_info import BinaryInfo
from lib.library_build_config import LibraryBuildConfig
from lib.staging import StagingDir

_TIMEOUT = 600
compiler_popularity_treshhold = 1000
popular_compilers: Dict[str, Any] = defaultdict(lambda: [])

build_supported_os = ["Linux"]
build_supported_buildtype = ["Debug"]
build_supported_arch = ["x86_64", "x86"]
build_supported_stdver = [""]
build_supported_stdlib = ["", "libc++"]
build_supported_flags = [""]
build_supported_flagscollection = [[""]]

disable_clang_libcpp = [
    "clang30",
    "clang31",
    "clang32",
    "clang33",
    "clang341",
    "clang350",
    "clang351",
    "clang352",
    "clang37x",
    "clang36x",
    "clang371",
    "clang380",
    "clang381",
    "clang390",
    "clang391",
    "clang400",
    "clang401",
]
disable_clang_32bit = disable_clang_libcpp.copy()
disable_clang_libcpp += ["clang_lifetime"]
disable_compiler_ids = ["avrg454"]

_propsandlibs: Dict[str, Any] = defaultdict(lambda: [])
_supports_x86: Dict[str, Any] = defaultdict(lambda: [])

GITCOMMITHASH_RE = re.compile(r"^(\w*)\s.*")
CONANINFOHASH_RE = re.compile(r"\s+ID:\s(\w*)")


def _quote(string: str) -> str:
    return f'"{string}"'


@unique
class BuildStatus(Enum):
    Ok = 0
    Failed = 1
    Skipped = 2
    TimedOut = 3


build_timeout = 600

conanserver_url = "https://conan.compiler-explorer.com"


@contextlib.contextmanager
def open_script(script: Path) -> Generator[TextIO, None, None]:
    with script.open("w", encoding="utf-8") as f:
        yield f
    script.chmod(0o755)


class LibraryBuilder:
    def __init__(
        self,
        logger,
        language: str,
        libname: str,
        target_name: str,
        sourcefolder: str,
        install_context,
        buildconfig: LibraryBuildConfig,
        popular_compilers_only: bool,
    ):
        self.logger = logger
        self.language = language
        self.libname = libname
        self.buildconfig = buildconfig
        self.install_context = install_context
        self.sourcefolder = sourcefolder
        self.target_name = target_name
        self.forcebuild = False
        self.current_buildparameters_obj: Dict[str, Any] = defaultdict(lambda: [])
        self.current_buildparameters: List[str] = []
        self.needs_uploading = 0
        self.libid = self.libname  # TODO: CE libid might be different from yaml libname
        self.conanserverproxy_token = None
        self.current_commit_hash = ""

        if self.language in _propsandlibs:
            [self.compilerprops, self.libraryprops] = _propsandlibs[self.language]
        else:
            [self.compilerprops, self.libraryprops] = get_properties_compilers_and_libraries(self.language, self.logger)
            _propsandlibs[self.language] = [self.compilerprops, self.libraryprops]

        self.check_compiler_popularity = popular_compilers_only

        self.completeBuildConfig()

    def completeBuildConfig(self):
        if "description" in self.libraryprops[self.libid]:
            self.buildconfig.description = self.libraryprops[self.libid]["description"]
        if "name" in self.libraryprops[self.libid]:
            self.buildconfig.description = self.libraryprops[self.libid]["name"]
        if "url" in self.libraryprops[self.libid]:
            self.buildconfig.url = self.libraryprops[self.libid]["url"]

        if "staticliblink" in self.libraryprops[self.libid]:
            self.buildconfig.staticliblink = list(
                set(self.buildconfig.staticliblink + self.libraryprops[self.libid]["staticliblink"])
            )

        if "liblink" in self.libraryprops[self.libid]:
            self.buildconfig.sharedliblink = list(
                set(self.buildconfig.sharedliblink + self.libraryprops[self.libid]["liblink"])
            )

        specificVersionDetails = get_specific_library_version_details(self.libraryprops, self.libid, self.target_name)
        if specificVersionDetails:
            if "staticliblink" in specificVersionDetails:
                self.buildconfig.staticliblink = list(
                    set(self.buildconfig.staticliblink + specificVersionDetails["staticliblink"])
                )

            if "liblink" in specificVersionDetails:
                self.buildconfig.sharedliblink = list(
                    set(self.buildconfig.sharedliblink + specificVersionDetails["liblink"])
                )
        else:
            self.logger.debug("No specific library version information found")

        if self.buildconfig.lib_type == "static":
            if self.buildconfig.staticliblink == []:
                self.buildconfig.staticliblink = [f"{self.libname}"]
        elif self.buildconfig.lib_type == "shared":
            if self.buildconfig.sharedliblink == []:
                self.buildconfig.sharedliblink = [f"{self.libname}"]
        elif self.buildconfig.lib_type == "cshared":
            if self.buildconfig.sharedliblink == []:
                self.buildconfig.sharedliblink = [f"{self.libname}"]

        alternatelibs = []
        for lib in self.buildconfig.staticliblink:
            if lib.endswith("d") and lib[:-1] not in self.buildconfig.staticliblink:
                alternatelibs += [lib[:-1]]
            else:
                if f"{lib}d" not in self.buildconfig.staticliblink:
                    alternatelibs += [f"{lib}d"]

        self.buildconfig.staticliblink += alternatelibs

    def getToolchainPathFromOptions(self, options):
        match = re.search(r"--gcc-toolchain=(\S*)", options)
        if match:
            return match[1]
        else:
            match = re.search(r"--gxx-name=(\S*)", options)
            if match:
                return os.path.realpath(os.path.join(os.path.dirname(match[1]), ".."))
        return False

    def getStdVerFromOptions(self, options):
        match = re.search(r"-std=(\S*)", options)
        if match:
            return match[1]
        return False

    def getStdLibFromOptions(self, options):
        match = re.search(r"-stdlib=(\S*)", options)
        if match:
            return match[1]
        return False

    def getTargetFromOptions(self, options):
        match = re.search(r"-target (\S*)", options)
        if match:
            return match[1]
        return False

    def does_compiler_support(self, exe, compilerType, arch, options, ldPath):
        fixedTarget = self.getTargetFromOptions(options)
        if fixedTarget:
            return fixedTarget == arch

        fullenv = os.environ
        fullenv["LD_LIBRARY_PATH"] = ldPath

        if compilerType == "":
            if "icpx" in exe:
                return arch == "x86" or arch == "x86_64"
            elif "icc" in exe:
                output = subprocess.check_output([exe, "--help"], env=fullenv).decode("utf-8", "ignore")
                if arch == "x86":
                    arch = "-m32"
                elif arch == "x86_64":
                    arch = "-m64"
            else:
                if "zapcc" in exe:
                    return arch == "x86" or arch == "x86_64"
                else:
                    try:
                        output = subprocess.check_output([exe, "--target-help"], env=fullenv).decode("utf-8", "ignore")
                    except subprocess.CalledProcessError as e:
                        output = e.output.decode("utf-8", "ignore")
        elif compilerType == "clang":
            folder = os.path.dirname(exe)
            llcexe = os.path.join(folder, "llc")
            if os.path.exists(llcexe):
                try:
                    output = subprocess.check_output([llcexe, "--version"], env=fullenv).decode("utf-8", "ignore")
                except subprocess.CalledProcessError as e:
                    output = e.output.decode("utf-8", "ignore")
            else:
                output = ""
        else:
            output = ""

        if arch in output:
            self.logger.debug(f"Compiler {exe} supports {arch}")
            return True
        else:
            self.logger.debug(f"Compiler {exe} does not support {arch}")
            return False

    def does_compiler_support_x86(self, exe, compilerType, options, ldPath):
        cachekey = f"{exe}|{options}"
        if cachekey not in _supports_x86:
            _supports_x86[cachekey] = self.does_compiler_support(exe, compilerType, "x86", options, ldPath)
        return _supports_x86[cachekey]

    def replace_optional_arg(self, arg, name, value):
        optional = "%" + name + "?%"
        if optional in arg:
            if value != "":
                return arg.replace(optional, value)
            else:
                return ""
        else:
            return arg.replace("%" + name + "%", value)

    def expand_make_arg(self, arg, compilerTypeOrGcc, buildtype, arch, stdver, stdlib):
        expanded = arg

        expanded = self.replace_optional_arg(expanded, "compilerTypeOrGcc", compilerTypeOrGcc)
        expanded = self.replace_optional_arg(expanded, "buildtype", buildtype)
        expanded = self.replace_optional_arg(expanded, "arch", arch)
        expanded = self.replace_optional_arg(expanded, "stdver", stdver)
        expanded = self.replace_optional_arg(expanded, "stdlib", stdlib)

        intelarch = ""
        if arch == "x86":
            intelarch = "ia32"
        elif arch == "x86_64":
            intelarch = "intel64"

        expanded = self.replace_optional_arg(expanded, "intelarch", intelarch)

        return expanded

    def resil_post(self, url, json_data, headers=None):
        request = None
        retries = 3
        last_error = ""
        while retries > 0:
            try:
                if headers != None:
                    request = requests.post(url, data=json_data, headers=headers, timeout=_TIMEOUT)
                else:
                    request = requests.post(
                        url, data=json_data, headers={"Content-Type": "application/json"}, timeout=_TIMEOUT
                    )

                retries = 0
            except ProtocolError as e:
                last_error = e
                retries = retries - 1
                time.sleep(1)

        if request == None:
            request = {"ok": False, "text": last_error}

        return request

    def resil_get(self, url: str, stream: bool, timeout: int, headers=None) -> Optional[requests.Response]:
        request: Optional[requests.Response] = None
        retries = 3
        while retries > 0:
            try:
                if headers != None:
                    request = requests.get(url, stream=stream, headers=headers, timeout=timeout)
                else:
                    request = requests.get(
                        url, stream=stream, headers={"Content-Type": "application/json"}, timeout=timeout
                    )

                retries = 0
            except ProtocolError:
                retries = retries - 1
                time.sleep(1)

        return request

    def writebuildscript(
        self,
        buildfolder,
        installfolder,
        sourcefolder,
        compiler,
        compileroptions,
        compilerexe,
        compilerType,
        toolchain,
        buildos,
        buildtype,
        arch,
        stdver,
        stdlib,
        flagscombination,
        ldPath,
    ):
        with open_script(Path(buildfolder) / "cebuild.sh") as f:
            f.write("#!/bin/sh\n\n")
            compilerexecc = compilerexe[:-2]
            if compilerexe.endswith("clang++"):
                compilerexecc = f"{compilerexecc}"
            elif compilerexe.endswith("g++"):
                compilerexecc = f"{compilerexecc}cc"
            elif compilerType == "edg":
                compilerexecc = compilerexe

            f.write(f"export CC={compilerexecc}\n")
            f.write(f"export CXX={compilerexe}\n")

            libparampaths = []
            archflag = ""
            if arch == "" or arch == "x86_64":
                # note: native arch for the compiler, so most of the time 64, but not always
                if os.path.exists(f"{toolchain}/lib64"):
                    libparampaths.append(f"{toolchain}/lib64")
                    libparampaths.append(f"{toolchain}/lib")
                else:
                    libparampaths.append(f"{toolchain}/lib")
            elif arch == "x86":
                libparampaths.append(f"{toolchain}/lib")
                if os.path.exists(f"{toolchain}/lib32"):
                    libparampaths.append(f"{toolchain}/lib32")

                if compilerType == "clang":
                    archflag = "-m32"
                elif compilerType == "":
                    archflag = "-march=i386 -m32"

            rpathflags = ""
            ldflags = ""
            if compilerType != "edg":
                for path in libparampaths:
                    rpathflags += f"-Wl,-rpath={path} "

            for path in libparampaths:
                ldflags += f"-L{path} "

            ldlibpathsstr = ldPath.replace("${exePath}", os.path.dirname(compilerexe)).replace("|", ":")

            f.write(f'export LD_LIBRARY_PATH="{ldlibpathsstr}"\n')
            f.write(f'export LDFLAGS="{ldflags} {rpathflags}"\n')
            f.write('export NUMCPUS="$(nproc)"\n')

            stdverflag = ""
            if stdver != "":
                stdverflag = f"-std={stdver}"

            stdlibflag = ""
            if stdlib != "" and compilerType == "clang":
                libcxx = stdlib
                stdlibflag = f"-stdlib={stdlib}"
                if stdlibflag in compileroptions:
                    stdlibflag = ""
            else:
                libcxx = "libstdc++"

            extraflags = " ".join(x for x in flagscombination)

            if compilerType == "":
                compilerTypeOrGcc = "gcc"
            else:
                compilerTypeOrGcc = compilerType

            cxx_flags = f"{compileroptions} {archflag} {stdverflag} {stdlibflag} {rpathflags} {extraflags}"

            expanded_configure_flags = [
                self.expand_make_arg(arg, compilerTypeOrGcc, buildtype, arch, stdver, stdlib)
                for arg in self.buildconfig.configure_flags
            ]
            configure_flags = " ".join(expanded_configure_flags)

            make_utility = self.buildconfig.make_utility

            if self.buildconfig.build_type == "cmake":
                expanded_cmake_args = [
                    self.expand_make_arg(arg, compilerTypeOrGcc, buildtype, arch, stdver, stdlib)
                    for arg in self.buildconfig.extra_cmake_arg
                ]
                extracmakeargs = " ".join(expanded_cmake_args)
                if compilerTypeOrGcc == "clang" and "--gcc-toolchain=" not in compileroptions:
                    toolchainparam = ""
                else:
                    toolchainparam = f'"-DCMAKE_CXX_COMPILER_EXTERNAL_TOOLCHAIN={toolchain}"'

                generator = ""
                if make_utility == "ninja":
                    generator = "-GNinja"

                cmakeline = f'cmake --install-prefix "{installfolder}" {generator} -DCMAKE_BUILD_TYPE={buildtype} {toolchainparam} "-DCMAKE_CXX_FLAGS_DEBUG={cxx_flags}" {extracmakeargs} {sourcefolder} > cecmakelog.txt 2>&1\n'
                self.logger.debug(cmakeline)
                f.write(cmakeline)

                for line in self.buildconfig.prebuild_script:
                    f.write(f"{line}\n")

                extramakeargs = " ".join(
                    ["-j$NUMCPUS"]
                    + [
                        self.expand_make_arg(arg, compilerTypeOrGcc, buildtype, arch, stdver, stdlib)
                        for arg in self.buildconfig.extra_make_arg
                    ]
                )

                if len(self.buildconfig.make_targets) != 0:
                    if len(self.buildconfig.make_targets) == 1 and self.buildconfig.make_targets[0] == "all":
                        f.write(f"cmake --build . {extramakeargs} > cemakelog_.txt 2>&1\n")
                    else:
                        for lognum, target in enumerate(self.buildconfig.make_targets):
                            f.write(
                                f"cmake --build . {extramakeargs} --target={target} > cemakelog_{lognum}.txt 2>&1\n"
                            )
                else:
                    lognum = 0
                    for lib in itertools.chain(self.buildconfig.staticliblink, self.buildconfig.sharedliblink):
                        f.write(f"cmake --build . {extramakeargs} --target={lib} > cemakelog_{lognum}.txt 2>&1\n")
                        lognum += 1

                    if len(self.buildconfig.staticliblink) != 0:
                        f.write("libsfound=$(find . -iname 'lib*.a')\n")
                    elif len(self.buildconfig.sharedliblink) != 0:
                        f.write("libsfound=$(find . -iname 'lib*.so*')\n")

                    f.write('if [ "$libsfound" = "" ]; then\n')
                    f.write(f"  cmake --build . {extramakeargs} > cemakelog_{lognum}.txt 2>&1\n")
                    f.write("fi\n")

                if self.buildconfig.package_install:
                    f.write("cmake --install . > ceinstall_0.txt 2>&1\n")
            else:
                if os.path.exists(os.path.join(sourcefolder, "Makefile")):
                    f.write("make clean\n")
                f.write("rm -f *.so*\n")
                f.write("rm -f *.a\n")
                f.write(f'export CXXFLAGS="{cxx_flags}"\n')
                if self.buildconfig.build_type == "make":
                    configurepath = os.path.join(sourcefolder, "configure")
                    if os.path.exists(configurepath):
                        f.write(f"./configure {configure_flags} > ceconfiglog.txt 2>&1\n")

                for line in self.buildconfig.prebuild_script:
                    f.write(f"{line}\n")

                extramakeargs = " ".join(
                    ["-j$NUMCPUS"]
                    + [
                        self.expand_make_arg(arg, compilerTypeOrGcc, buildtype, arch, stdver, stdlib)
                        for arg in self.buildconfig.extra_make_arg
                    ]
                )

                if len(self.buildconfig.make_targets) != 0:
                    for lognum, target in enumerate(self.buildconfig.make_targets):
                        f.write(f"{make_utility} {extramakeargs} {target} > cemakelog_{lognum}.txt 2>&1\n")
                else:
                    lognum = 0
                    for lib in itertools.chain(self.buildconfig.staticliblink, self.buildconfig.sharedliblink):
                        f.write(f"{make_utility} {extramakeargs} {lib} > cemakelog_{lognum}.txt 2>&1\n")
                        lognum += 1

                    if len(self.buildconfig.staticliblink) != 0:
                        f.write("libsfound=$(find . -iname 'lib*.a')\n")
                    elif len(self.buildconfig.sharedliblink) != 0:
                        f.write("libsfound=$(find . -iname 'lib*.so*')\n")

                    f.write('if [ "$libsfound" = "" ]; then\n')
                    f.write(f"  {make_utility} {extramakeargs} all > cemakelog_{lognum}.txt 2>&1\n")
                    f.write("fi\n")

            if not self.buildconfig.package_install:
                for lib in self.buildconfig.staticliblink:
                    f.write(f"find . -iname 'lib{lib}*.a' -type f -exec mv {{}} . \\;\n")

                for lib in self.buildconfig.sharedliblink:
                    f.write(f"find . -iname 'lib{lib}*.so*' -type f,l -exec mv {{}} . \\;\n")

            for line in self.buildconfig.postbuild_script:
                f.write(f"{line}\n")

        if self.buildconfig.lib_type == "cshared":
            self.setCurrentConanBuildParameters(
                buildos, buildtype, "cshared", "cshared", libcxx, arch, stdver, extraflags
            )
        else:
            self.setCurrentConanBuildParameters(
                buildos, buildtype, compilerTypeOrGcc, compiler, libcxx, arch, stdver, extraflags
            )

    def setCurrentConanBuildParameters(
        self, buildos, buildtype, compilerTypeOrGcc, compiler, libcxx, arch, stdver, extraflags
    ):
        self.current_buildparameters_obj["os"] = buildos
        self.current_buildparameters_obj["buildtype"] = buildtype
        self.current_buildparameters_obj["compiler"] = compilerTypeOrGcc
        self.current_buildparameters_obj["compiler_version"] = compiler
        self.current_buildparameters_obj["libcxx"] = libcxx
        self.current_buildparameters_obj["arch"] = arch
        self.current_buildparameters_obj["stdver"] = stdver
        self.current_buildparameters_obj["flagcollection"] = extraflags
        self.current_buildparameters_obj["library"] = self.libid
        self.current_buildparameters_obj["library_version"] = self.target_name

        self.current_buildparameters = [
            "-s",
            f"os={buildos}",
            "-s",
            f"build_type={buildtype}",
            "-s",
            f"compiler={compilerTypeOrGcc}",
            "-s",
            f"compiler.version={compiler}",
            "-s",
            f"compiler.libcxx={libcxx}",
            "-s",
            f"arch={arch}",
            "-s",
            f"stdver={stdver}",
            "-s",
            f"flagcollection={extraflags}",
        ]

    def writeconanscript(self, buildfolder):
        conanparamsstr = " ".join(self.current_buildparameters)
        with open_script(Path(buildfolder) / "conanexport.sh") as f:
            f.write("#!/bin/sh\n\n")
            f.write(f"conan export-pkg . {self.libname}/{self.target_name} -f {conanparamsstr}\n")

    def write_conan_file_to(self, f: TextIO) -> None:
        libsum = ",".join(
            f'"{lib}"' for lib in itertools.chain(self.buildconfig.staticliblink, self.buildconfig.sharedliblink)
        )

        f.write("from conans import ConanFile, tools\n")
        f.write(f"class {self.libname}Conan(ConanFile):\n")
        f.write(f'    name = "{self.libname}"\n')
        f.write(f'    version = "{self.target_name}"\n')
        f.write('    settings = "os", "compiler", "build_type", "arch", "stdver", "flagcollection"\n')
        f.write(f'    description = "{self.buildconfig.description}"\n')
        f.write(f'    url = "{self.buildconfig.url}"\n')
        f.write('    license = "None"\n')
        f.write('    author = "None"\n')
        f.write("    topics = None\n")
        f.write("    def package(self):\n")

        if self.buildconfig.package_install:
            f.write('        self.copy("*", src="../install", dst=".", keep_path=True)\n')
        else:
            for copy_line in self.buildconfig.copy_files:
                f.write(f"        {copy_line}\n")

            for lib in self.buildconfig.staticliblink:
                f.write(f'        self.copy("lib{lib}*.a", dst="lib", keep_path=False)\n')

            for lib in self.buildconfig.sharedliblink:
                f.write(f'        self.copy("lib{lib}*.so*", dst="lib", keep_path=False)\n')

        f.write("    def package_info(self):\n")
        f.write(f"        self.cpp_info.libs = [{libsum}]\n")

    def writeconanfile(self, buildfolder):
        with (Path(buildfolder) / "conanfile.py").open(mode="w", encoding="utf-8") as f:
            self.write_conan_file_to(f)

    def countValidLibraryBinaries(self, buildfolder, arch, stdlib):
        filesfound = 0

        if self.buildconfig.lib_type == "cshared":
            for lib in self.buildconfig.sharedliblink:
                filepath = os.path.join(buildfolder, f"lib{lib}.so")
                bininfo = BinaryInfo(self.logger, buildfolder, filepath)
                if "libstdc++.so" not in bininfo.ldd_details and "libc++.so" not in bininfo.ldd_details:
                    if arch == "":
                        filesfound += 1
                    elif arch == "x86" and "ELF32" in bininfo.readelf_header_details:
                        filesfound += 1
                    elif arch == "x86_64" and "ELF64" in bininfo.readelf_header_details:
                        filesfound += 1
            return filesfound

        for lib in self.buildconfig.staticliblink:
            filepath = os.path.join(buildfolder, f"lib{lib}.a")
            if os.path.exists(filepath):
                bininfo = BinaryInfo(self.logger, buildfolder, filepath)
                cxxinfo = bininfo.cxx_info_from_binary()
                if (stdlib == "") or (stdlib == "libc++" and not cxxinfo["has_maybecxx11abi"]):
                    if arch == "":
                        filesfound += 1
                    if arch == "x86" and "ELF32" in bininfo.readelf_header_details:
                        filesfound += 1
                    elif arch == "x86_64" and "ELF64" in bininfo.readelf_header_details:
                        filesfound += 1
            else:
                self.logger.debug(f"lib{lib}.a not found")

        for lib in self.buildconfig.sharedliblink:
            filepath = os.path.join(buildfolder, f"lib{lib}.so")
            bininfo = BinaryInfo(self.logger, buildfolder, filepath)
            if (stdlib == "" and "libstdc++.so" in bininfo.ldd_details) or (
                stdlib != "" and f"{stdlib}.so" in bininfo.ldd_details
            ):
                if arch == "":
                    filesfound += 1
                elif arch == "x86" and "ELF32" in bininfo.readelf_header_details:
                    filesfound += 1
                elif arch == "x86_64" and "ELF64" in bininfo.readelf_header_details:
                    filesfound += 1

        return filesfound

    def executeconanscript(self, buildfolder):
        if subprocess.call(["./conanexport.sh"], cwd=buildfolder) == 0:
            self.logger.info("Export succesful")
            return BuildStatus.Ok
        else:
            return BuildStatus.Failed

    def executebuildscript(self, buildfolder):
        try:
            if subprocess.call(["./cebuild.sh"], cwd=buildfolder, timeout=build_timeout) == 0:
                self.logger.info(f"Build succeeded in {buildfolder}")
                return BuildStatus.Ok
            else:
                return BuildStatus.Failed
        except subprocess.TimeoutExpired:
            self.logger.info(f"Build timed out and was killed ({buildfolder})")
            return BuildStatus.TimedOut

    def makebuildhash(self, compiler, options, toolchain, buildos, buildtype, arch, stdver, stdlib, flagscombination):
        hasher = hashlib.sha256()
        flagsstr = "|".join(x for x in flagscombination)
        hasher.update(
            bytes(
                f"{compiler},{options},{toolchain},{buildos},{buildtype},{arch},{stdver},{stdlib},{flagsstr}", "utf-8"
            )
        )

        self.logger.info(
            f"Building {self.libname} {self.target_name} for [{compiler},{options},{toolchain},{buildos},{buildtype},{arch},{stdver},{stdlib},{flagsstr}]"
        )

        return compiler + "_" + hasher.hexdigest()

    def get_conan_hash(self, buildfolder: str) -> Optional[str]:
        if not self.install_context.dry_run:
            self.logger.debug(["conan", "info", "."] + self.current_buildparameters)
            conaninfo = subprocess.check_output(
                ["conan", "info", "-r", "ceserver", "."] + self.current_buildparameters, cwd=buildfolder
            ).decode("utf-8", "ignore")
            self.logger.debug(conaninfo)
            match = CONANINFOHASH_RE.search(conaninfo, re.MULTILINE)
            if match:
                return match[1]
        return None

    def conanproxy_login(self):
        url = f"{conanserver_url}/login"

        login_body = defaultdict(lambda: [])
        login_body["password"] = get_ssm_param("/compiler-explorer/conanpwd")

        request = self.resil_post(url, json_data=json.dumps(login_body))
        if not request.ok:
            self.logger.info(request.text)
            raise RuntimeError(f"Post failure for {url}: {request}")
        else:
            response = json.loads(request.content)
            self.conanserverproxy_token = response["token"]

    def save_build_logging(self, builtok, buildfolder, extralogtext):
        if builtok == BuildStatus.Failed:
            url = f"{conanserver_url}/buildfailed"
        elif builtok == BuildStatus.Ok:
            url = f"{conanserver_url}/buildsuccess"
        elif builtok == BuildStatus.TimedOut:
            url = f"{conanserver_url}/buildfailed"
        else:
            return

        loggingfiles = []
        loggingfiles += glob.glob(buildfolder + "/cecmake*.txt")
        loggingfiles += glob.glob(buildfolder + "/ceconfiglog.txt")
        loggingfiles += glob.glob(buildfolder + "/cemake*.txt")
        loggingfiles += glob.glob(buildfolder + "/ceinstall*.txt")

        logging_data = ""
        for logfile in loggingfiles:
            logging_data += Path(logfile).read_text(encoding="utf-8")

        if builtok == BuildStatus.TimedOut:
            logging_data = logging_data + "\n\n" + "BUILD TIMED OUT!!"

        buildparameters_copy = self.current_buildparameters_obj.copy()
        buildparameters_copy["logging"] = logging_data + "\n\n" + extralogtext
        buildparameters_copy["commithash"] = self.get_commit_hash()

        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.conanserverproxy_token}

        return self.resil_post(url, json_data=json.dumps(buildparameters_copy), headers=headers)

    def get_build_annotations(self, buildfolder):
        conanhash = self.get_conan_hash(buildfolder)
        if conanhash is None:
            return defaultdict(lambda: [])

        url = f"{conanserver_url}/annotations/{self.libname}/{self.target_name}/{conanhash}"
        with tempfile.TemporaryFile() as fd:
            request = self.resil_get(url, stream=True, timeout=_TIMEOUT)
            if not request or not request.ok:
                raise RuntimeError(f"Fetch failure for {url}: {request}")
            for chunk in request.iter_content(chunk_size=4 * 1024 * 1024):
                fd.write(chunk)
            fd.flush()
            fd.seek(0)
            buffer = fd.read()
            return json.loads(buffer)

    def get_commit_hash(self) -> str:
        if self.current_commit_hash:
            return self.current_commit_hash

        if os.path.exists(f"{self.sourcefolder}/.git"):
            lastcommitinfo = subprocess.check_output(
                ["git", "-C", self.sourcefolder, "log", "-1", "--oneline", "--no-color"]
            ).decode("utf-8", "ignore")
            self.logger.debug(f"last git commit: {lastcommitinfo}")
            match = GITCOMMITHASH_RE.match(lastcommitinfo)
            if match:
                self.current_commit_hash = match[1]
            else:
                self.current_commit_hash = self.target_name
                return self.current_commit_hash
        else:
            self.current_commit_hash = self.target_name

        return self.current_commit_hash

    def has_failed_before(self):
        url = f"{conanserver_url}/whathasfailedbefore"
        request = self.resil_post(url, json_data=json.dumps(self.current_buildparameters_obj))
        if not request.ok:
            raise RuntimeError(f"Post failure for {url}: {request}")
        else:
            response = json.loads(request.content)
            current_commit = self.get_commit_hash()
            if response["commithash"] == current_commit:
                return response["response"]
            else:
                return False

    def is_already_uploaded(self, buildfolder):
        annotations = self.get_build_annotations(buildfolder)

        if "commithash" in annotations:
            commithash = self.get_commit_hash()

            return commithash == annotations["commithash"]
        else:
            return False

    def set_as_uploaded(self, buildfolder):
        conanhash = self.get_conan_hash(buildfolder)
        if conanhash is None:
            raise RuntimeError(f"Error determining conan hash in {buildfolder}")

        self.logger.info(f"commithash: {conanhash}")

        annotations = self.get_build_annotations(buildfolder)
        if "commithash" not in annotations:
            self.upload_builds()
        annotations["commithash"] = self.get_commit_hash()

        for lib in itertools.chain(self.buildconfig.staticliblink, self.buildconfig.sharedliblink):
            # TODO - this is the same as the original code but I wonder if this needs to be *.so for shared?
            if os.path.exists(os.path.join(buildfolder, f"lib{lib}.a")):
                bininfo = BinaryInfo(self.logger, buildfolder, os.path.join(buildfolder, f"lib{lib}.a"))
                libinfo = bininfo.cxx_info_from_binary()
                archinfo = bininfo.arch_info_from_binary()
                annotations["cxx11"] = libinfo["has_maybecxx11abi"]
                annotations["machine"] = archinfo["elf_machine"]
                annotations["osabi"] = archinfo["elf_osabi"]

        self.logger.info(annotations)

        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + self.conanserverproxy_token}

        url = f"{conanserver_url}/annotations/{self.libname}/{self.target_name}/{conanhash}"
        request = self.resil_post(url, json_data=json.dumps(annotations), headers=headers)
        if not request.ok:
            raise RuntimeError(f"Post failure for {url}: {request}")

    def makebuildfor(
        self,
        compiler,
        options,
        exe,
        compiler_type,
        toolchain,
        buildos,
        buildtype,
        arch,
        stdver,
        stdlib,
        flagscombination,
        ld_path,
        staging: StagingDir,
    ):
        combined_hash = self.makebuildhash(
            compiler, options, toolchain, buildos, buildtype, arch, stdver, stdlib, flagscombination
        )

        build_folder = os.path.join(staging.path, combined_hash)
        if os.path.exists(build_folder):
            shutil.rmtree(build_folder, ignore_errors=True)
        os.makedirs(build_folder, exist_ok=True)
        requires_tree_copy = self.buildconfig.build_type != "cmake"

        self.logger.debug(f"Buildfolder: {build_folder}")

        install_folder = os.path.join(staging.path, "install")
        self.logger.debug(f"Installfolder: {install_folder}")

        self.writebuildscript(
            build_folder,
            install_folder,
            self.sourcefolder,
            compiler,
            options,
            exe,
            compiler_type,
            toolchain,
            buildos,
            buildtype,
            arch,
            stdver,
            stdlib,
            flagscombination,
            ld_path,
        )
        self.writeconanfile(build_folder)
        extralogtext = ""

        if not self.forcebuild and self.has_failed_before():
            self.logger.info("Build has failed before, not re-attempting")
            return BuildStatus.Skipped

        if self.is_already_uploaded(build_folder):
            self.logger.info("Build already uploaded")
            if not self.forcebuild:
                return BuildStatus.Skipped

        if requires_tree_copy:
            shutil.copytree(self.sourcefolder, build_folder, dirs_exist_ok=True)

        if not self.install_context.dry_run and not self.conanserverproxy_token:
            self.conanproxy_login()

        build_status = self.executebuildscript(build_folder)
        if build_status == BuildStatus.Ok:
            if self.buildconfig.package_install:
                filesfound = self.countValidLibraryBinaries(Path(install_folder) / "lib", arch, stdlib)
            else:
                filesfound = self.countValidLibraryBinaries(build_folder, arch, stdlib)

            if filesfound != 0:
                self.writeconanscript(build_folder)
                if not self.install_context.dry_run:
                    build_status = self.executeconanscript(build_folder)
                    if build_status == BuildStatus.Ok:
                        self.needs_uploading += 1
                        self.set_as_uploaded(build_folder)
            else:
                extralogtext = "No binaries found to export"
                self.logger.info("No binaries found to export")
                build_status = BuildStatus.Failed

        if not self.install_context.dry_run:
            self.save_build_logging(build_status, build_folder, extralogtext)

        if build_status == BuildStatus.Ok:
            if self.buildconfig.build_type == "cmake":
                self.build_cleanup(build_folder)
            elif self.buildconfig.build_type == "make":
                subprocess.call(["make", "clean"], cwd=build_folder)

        return build_status

    def build_cleanup(self, buildfolder):
        if self.install_context.dry_run:
            self.logger.info(f"Would remove directory {buildfolder} but in dry-run mode")
        else:
            shutil.rmtree(buildfolder, ignore_errors=True)
            self.logger.info(f"Removing {buildfolder}")

    def upload_builds(self):
        if self.needs_uploading > 0:
            if not self.install_context.dry_run:
                self.logger.info("Uploading cached builds")
                subprocess.check_call(
                    ["conan", "upload", f"{self.libname}/{self.target_name}", "--all", "-r=ceserver", "-c"]
                )
                self.logger.debug("Clearing cache to speed up next upload")
                subprocess.check_call(["conan", "remove", "-f", f"{self.libname}/{self.target_name}"])
            self.needs_uploading = 0

    def get_compiler_type(self, compiler):
        compilerType = ""
        if "compilerType" in self.compilerprops[compiler]:
            compilerType = self.compilerprops[compiler]["compilerType"]
        else:
            raise RuntimeError(f"Something is wrong with {compiler}")

        if self.compilerprops[compiler]["compilerType"] == "clang-intel":
            # hack for icpx so we don't get duplicate builds
            compilerType = "gcc"

        return compilerType

    def download_compiler_usage_csv(self):
        url = "https://compiler-explorer.s3.amazonaws.com/public/compiler_usage.csv"
        with tempfile.TemporaryFile() as fd:
            request = self.resil_get(url, stream=True, timeout=_TIMEOUT)
            if not request or not request.ok:
                raise RuntimeError(f"Fetch failure for {url}: {request}")
            for chunk in request.iter_content(chunk_size=4 * 1024 * 1024):
                fd.write(chunk)
            fd.flush()
            fd.seek(0)

            reader = csv.DictReader(line.decode("utf-8") for line in fd.readlines())
            for row in reader:
                popular_compilers[row["compiler"]] = int(row["times_used"])

    def is_popular_enough(self, compiler):
        if len(popular_compilers) == 0:
            self.logger.debug("downloading compiler popularity csv")
            self.download_compiler_usage_csv()

        if not compiler in popular_compilers:
            return False

        if popular_compilers[compiler] < compiler_popularity_treshhold:
            return False

        return True

    def should_build_with_compiler(self, compiler, checkcompiler, buildfor):
        if checkcompiler != "" and compiler != checkcompiler:
            return False

        if compiler in self.buildconfig.skip_compilers:
            return False

        compilerType = self.get_compiler_type(compiler)

        exe = self.compilerprops[compiler]["exe"]

        if buildfor == "allclang" and compilerType != "clang":
            return False
        elif buildfor == "allicc" and "/icc" not in exe:
            return False
        elif buildfor == "allgcc" and compilerType != "":
            return False

        if self.check_compiler_popularity:
            if not self.is_popular_enough(compiler):
                self.logger.info(f"compiler {compiler} is not popular enough")
                return False

        return True

    def makebuild(self, buildfor):
        builds_failed = 0
        builds_succeeded = 0
        builds_skipped = 0
        checkcompiler = ""

        if buildfor != "":
            self.forcebuild = True

        if self.buildconfig.lib_type == "cshared":
            checkcompiler = self.buildconfig.use_compiler
            if checkcompiler not in self.compilerprops:
                self.logger.error(
                    f"Unknown compiler {checkcompiler} to build cshared lib {self.buildconfig.sharedliblink}"
                )
        elif buildfor == "nonx86":
            self.forcebuild = True
            checkcompiler = ""
        elif buildfor == "allclang" or buildfor == "allicc" or buildfor == "allgcc" or buildfor == "forceall":
            self.forcebuild = True
            checkcompiler = ""
        elif buildfor != "":
            checkcompiler = buildfor
            if checkcompiler not in self.compilerprops:
                self.logger.error(f"Unknown compiler {checkcompiler}")

        for compiler in self.compilerprops:
            if compiler in disable_compiler_ids:
                self.logger.debug(f"Skipping {compiler}")
                continue

            if not self.should_build_with_compiler(compiler, checkcompiler, buildfor):
                self.logger.debug(f"Skipping {compiler}")
                continue

            compilerType = self.get_compiler_type(compiler)

            exe = self.compilerprops[compiler]["exe"]

            options = self.compilerprops[compiler]["options"]

            toolchain = self.getToolchainPathFromOptions(options)
            fixedStdver = self.getStdVerFromOptions(options)
            fixedStdlib = self.getStdLibFromOptions(options)

            if not toolchain:
                toolchain = os.path.realpath(os.path.join(os.path.dirname(exe), ".."))

            if (
                self.buildconfig.build_fixed_stdlib != ""
                and fixedStdlib
                and self.buildconfig.build_fixed_stdlib != fixedStdlib
            ):
                continue

            stdlibs = [""]
            if self.buildconfig.lib_type != "cshared":
                if compiler in disable_clang_libcpp:
                    stdlibs = [""]
                elif fixedStdlib:
                    self.logger.debug(f"Fixed stdlib {fixedStdlib}")
                    stdlibs = [fixedStdlib]
                else:
                    if self.buildconfig.build_fixed_stdlib != "":
                        if self.buildconfig.build_fixed_stdlib != "libstdc++":
                            stdlibs = [self.buildconfig.build_fixed_stdlib]
                    else:
                        if compilerType == "":
                            self.logger.debug("Gcc-like compiler")
                        elif compilerType == "clang":
                            self.logger.debug("Clang-like compiler")
                            stdlibs = build_supported_stdlib
                        else:
                            self.logger.debug("Some other compiler")

            archs = build_supported_arch

            if compiler in disable_clang_32bit:
                archs = ["x86_64"]
            else:
                if self.buildconfig.build_fixed_arch != "":
                    if not self.does_compiler_support(
                        exe,
                        compilerType,
                        self.buildconfig.build_fixed_arch,
                        self.compilerprops[compiler]["options"],
                        self.compilerprops[compiler]["ldPath"],
                    ):
                        self.logger.debug(
                            f"Compiler {compiler} does not support fixed arch {self.buildconfig.build_fixed_arch}"
                        )
                        continue
                    else:
                        archs = [self.buildconfig.build_fixed_arch]

                if not self.does_compiler_support_x86(
                    exe, compilerType, self.compilerprops[compiler]["options"], self.compilerprops[compiler]["ldPath"]
                ):
                    archs = [""]

            if buildfor == "nonx86" and archs[0] != "":
                continue

            stdvers = build_supported_stdver
            if fixedStdver:
                stdvers = [fixedStdver]

            for args in itertools.product(
                build_supported_os, build_supported_buildtype, archs, stdvers, stdlibs, build_supported_flagscollection
            ):
                with self.install_context.new_staging_dir() as staging:
                    buildstatus = self.makebuildfor(
                        compiler,
                        options,
                        exe,
                        compilerType,
                        toolchain,
                        *args,
                        self.compilerprops[compiler]["ldPath"],
                        staging,
                    )
                    if buildstatus == BuildStatus.Ok:
                        builds_succeeded = builds_succeeded + 1
                    elif buildstatus == BuildStatus.Skipped:
                        builds_skipped = builds_skipped + 1
                    else:
                        builds_failed = builds_failed + 1

            if builds_succeeded > 0:
                self.upload_builds()

        return [builds_succeeded, builds_skipped, builds_failed]
