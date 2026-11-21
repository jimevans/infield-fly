"""Tests for the version of the infield_fly package"""

from importlib.metadata import PackageNotFoundError
from unittest import mock

import infield_fly


def test_installed_version():
    with mock.patch("infield_fly.version", return_value="1.2.3") as version:
        assert infield_fly.get_version() == "1.2.3"

    version.assert_called_once_with("infield-fly")


def test_version_when_not_installed():
    with mock.patch("infield_fly.version", side_effect=PackageNotFoundError):
        assert infield_fly.get_version() == "unknown"


def test_module_version_is_installed_version():
    assert infield_fly.__version__ == infield_fly.get_version()
