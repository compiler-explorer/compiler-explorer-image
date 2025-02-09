from logging import Logger

from lib.installation_context import InstallationContext
from lib.library_build_config import LibraryBuildConfig
from lib.library_builder import LibraryBuilder
from lib.library_platform import LibraryPlatform

from unittest import mock
import io
import ast

BASE = "https://raw.githubusercontent.com/compiler-explorer/compiler-explorer/main/etc/config/"


def assert_valid_python(python_source: str) -> None:
    try:
        ast.parse(python_source)
    except Exception as e:
        raise AssertionError("Not valid python") from e


def test_can_write_conan_file(requests_mock):
    requests_mock.get(f"{BASE}lang.amazon.properties", text="")
    logger = mock.Mock(spec_set=Logger)
    install_context = mock.Mock(spec_set=InstallationContext)
    build_config = mock.Mock(spec=LibraryBuildConfig)
    build_config.lib_type = "static"
    build_config.staticliblink = ["static1", "static2"]
    build_config.sharedliblink = ["shared1", "shared2"]
    build_config.copy_files = [
        'self.copy("*", src="include", dst="include", keep_path=True)',
        'self.copy("*.png", src="resources", dst="resources", keep_path=True)',
    ]
    build_config.description = "description"
    build_config.url = "https://some.url"
    build_config.package_install = False
    lb = LibraryBuilder(
        logger, "lang", "somelib", "target", "src-folder", install_context, build_config, False, LibraryPlatform.Linux
    )
    tio = io.StringIO()
    lb.write_conan_file_to(tio)
    conan_file = tio.getvalue()
    assert_valid_python(conan_file)
    lines = [line.strip() for line in conan_file.split("\n")]
    assert 'name = "somelib"' in lines
    assert 'version = "target"' in lines
    assert 'description = "description"' in lines
    assert 'url = "https://some.url"' in lines
    assert 'self.copy("*", src="include", dst="include", keep_path=True)' in lines
    assert 'self.copy("*.png", src="resources", dst="resources", keep_path=True)' in lines
    assert 'self.copy("libstatic1*.a", dst="lib", keep_path=False)' in lines
    assert 'self.copy("libstatic2*.a", dst="lib", keep_path=False)' in lines
    assert 'self.copy("libshared1*.so*", dst="lib", keep_path=False)' in lines
    assert 'self.copy("libshared2*.so*", dst="lib", keep_path=False)' in lines
    assert 'self.cpp_info.libs = ["static1","static2","static1d","static2d","shared1","shared2"]' in lines
