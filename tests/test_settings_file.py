"""Tests for the settings_file module"""

import json

import pytest

from infield_fly.settings_file import (MISSING_VERSION, SettingsChangedError, SettingsFile,
                                       detect_indent)


class TestSettingsFile:

    def test_read_missing_file(self, tmp_path):
        assert SettingsFile(str(tmp_path / "settings.json")).read() == ({}, MISSING_VERSION)

    def test_write_keeps_unknown_settings_order_and_indentation(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text('{\n    "zeta": {"custom": 1},\n    "alpha": "é"\n}\n', encoding="utf-8")
        settings_file = SettingsFile(str(path))
        settings, version = settings_file.read()

        settings["alpha"] = "ü"
        new_version = settings_file.write(settings, version)

        assert path.read_text(encoding="utf-8") == (
            '{\n    "zeta": {\n        "custom": 1\n    },\n    "alpha": "ü"\n}\n')
        assert settings_file.read() == ({"zeta": {"custom": 1}, "alpha": "ü"}, new_version)
        assert new_version != version

    def test_write_refuses_if_file_changed(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        settings_file = SettingsFile(str(path))
        _, version = settings_file.read()
        path.write_text('{"changed": true}', encoding="utf-8")

        with pytest.raises(SettingsChangedError):
            settings_file.write({"mine": True}, version)

        assert json.loads(path.read_text(encoding="utf-8")) == {"changed": True}

    def test_write_refuses_if_file_created(self, tmp_path):
        path = tmp_path / "settings.json"
        settings_file = SettingsFile(str(path))
        path.write_text("{}", encoding="utf-8")

        with pytest.raises(SettingsChangedError):
            settings_file.write({}, MISSING_VERSION)

    def test_write_creates_missing_file_and_directory(self, tmp_path):
        path = tmp_path / "config" / "settings.json"
        settings_file = SettingsFile(str(path))

        settings_file.write({"a": [1]}, MISSING_VERSION)

        assert path.read_text(encoding="utf-8") == '{\n  "a": [\n    1\n  ]\n}\n'

    def test_read_refuses_non_object(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("[]", encoding="utf-8")

        with pytest.raises(ValueError):
            SettingsFile(str(path)).read()


@pytest.mark.parametrize("text, expected", [
    ("", 2),
    ("{}", 2),
    ('{\n  "a": 1\n}', 2),
    ('{\n    "a": 1\n}', 4),
    ('{\n\t"a": 1\n}', "\t"),
])
def test_detect_indent(text, expected):
    assert detect_indent(text) == expected
