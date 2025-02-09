import logging
import pytest
import requests
from lib.amazon_properties import get_properties_compilers_and_libraries, get_specific_library_version_details
from lib.library_platform import LibraryPlatform

logger = logging.getLogger(__name__)
logger.level = 9

# def test_should_contain_some_compilers_and_libraries():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('c++', logger)
#     assert len(_compilers) > 0
#     assert len(_libraries) > 0


def test_should_have_staticliblink():
    try:
        [_compilers, _libraries] = get_properties_compilers_and_libraries("c++", logger, LibraryPlatform.Linux)
        assert "googletest" in _libraries
        assert len(_libraries["googletest"]["versionprops"]["trunk"]["staticliblink"]) > 0
        assert _libraries["googletest"]["versionprops"]["trunk"]["staticliblink"][0] == "gtest"
        assert _libraries["googletest"]["versionprops"]["trunk"]["staticliblink"][1] == "gmock"
    except requests.exceptions.ConnectionError:
        pytest.skip("Connection error in test_should_have_staticliblink, which needs internet access")


def test_googletest_should_have_versions():
    try:
        [_compilers, _libraries] = get_properties_compilers_and_libraries("c++", logger, LibraryPlatform.Linux)
        assert "googletest" in _libraries
        assert len(_libraries["googletest"]["versionprops"]) > 0
        assert _libraries["googletest"]["versionprops"]["110"]["lookupversion"] == "release-1.10.0"
        assert _libraries["googletest"]["versionprops"]["110"]["version"] == "1.10.0"

        details = get_specific_library_version_details(_libraries, "googletest", "1.10.0")
        assert details != False

        details = get_specific_library_version_details(_libraries, "googletest", "release-1.10.0")
        assert details != False
    except requests.exceptions.ConnectionError:
        pytest.skip("Connection error in test_googletest_should_have_versions, which needs internet access")


# def test_should_not_contain_g412():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('c++', logger)
#     assert not 'g412' in _compilers

# def test_should_not_contain_msvc():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('c++', logger)
#     assert not 'cl19_2015_u3_64' in _compilers

# def test_should_contain_gcc101():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('c++', logger)
#     assert 'g101' in _compilers

# def test_should_contain_clang800():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('c++', logger)
#     assert 'clang800' in _compilers

# def test_should_contain_optionsforclang800():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('c++', logger)
#     assert '--gcc-toolchain=/opt/compiler-explorer/gcc-8.3.0' in _compilers['clang800']['options']

# def test_should_contain_mrustc():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('rust', logger, False)
#     assert 'mrustc-master' in _compilers

# def test_should_contain_objcgsnapshot():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('objc', logger, False)
#     assert 'objcgsnapshot' in _compilers
#     assert 'exe' in _compilers['objcgsnapshot']

# def test_should_contain_objcppgsnapshot():
#     [_compilers, _libraries] = get_properties_compilers_and_libraries('objc++', logger, False)
#     assert 'objcppgsnapshot' in _compilers
#     assert 'exe' in _compilers['objcppgsnapshot']
