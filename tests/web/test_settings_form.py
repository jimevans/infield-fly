"""Tests for the settings form of the web interface"""

import os

import pytest

from infield_fly.web.settings_form import FIELDS, SettingsForm, get_default

SETTINGS = {
    "conversion": {"final_directory": "/final", "substitutions": {":": " -", "?": ""},
                   "custom": "kept"},
    "metadata": {"api_key": "tvdb-key", "tracked_series": {"show": {"id": 1}}},
    "search": {"api_key": "search-key", "max_candidates": 3},
    "download": {"sabnzbd_port": 9090, "sabnzbd_use_ssl": False,
                 "delete_completed_downloads": True},
    "other": {"kept": True},
}


def form_data(**values):
    """Gets form data for the settings, with secrets left blank, and changes"""

    form = SettingsForm.from_settings(SETTINGS)
    data = {name: value for name, value in form.values.items() if value not in ("", False)}
    data = {name: ("on" if value is True else value) for name, value in data.items()}
    data["substitution_count"] = str(len(form.substitutions))
    for index, (character, replacement) in enumerate(form.substitutions):
        data[f"substitution-{index}-character"] = character
        data[f"substitution-{index}-replacement"] = replacement
    data.update(values)
    return {name: value for name, value in data.items() if value is not None}


class TestFromSettings:

    def test_values(self):
        form = SettingsForm.from_settings(SETTINGS)

        assert form.values["conversion-final_directory"] == "/final"
        assert form.values["search-max_candidates"] == "3"
        assert form.values["download-sabnzbd_port"] == "9090"
        assert form.values["download-delete_completed_downloads"] is True
        assert form.values["download-sabnzbd_use_ssl"] is False
        assert form.values["conversion-log_level"] == ""
        assert form.substitutions == [(":", " -"), ("?", "")]
        assert form.revealed == set()

    def test_secrets_are_hidden(self):
        form = SettingsForm.from_settings(SETTINGS)

        assert form.values["metadata-api_key"] == ""
        assert form.values["search-api_key"] == ""

    def test_empty_and_invalid_sections(self):
        form = SettingsForm.from_settings({"conversion": "not an object"})

        assert form.values["conversion-final_directory"] == ""
        assert form.substitutions == []


class TestFromFormData:

    def test_round_trip_keeps_settings(self):
        form = SettingsForm.from_form_data(form_data())

        assert form.apply_to(SETTINGS) == SETTINGS

    def test_values_are_stripped_except_replacements(self):
        form = SettingsForm.from_form_data(form_data(**{
            "conversion-final_directory": " /x ", "substitution-0-replacement": " - "}))

        assert form.values["conversion-final_directory"] == "/x"
        assert form.substitutions[0] == (":", " - ")

    @pytest.mark.parametrize("count", ["bogus", "-1"])
    def test_invalid_substitution_count(self, count):
        assert SettingsForm.from_form_data(form_data(substitution_count=count)).substitutions == []


class TestApplyTo:

    def test_changes_values_and_keeps_others(self):
        form = SettingsForm.from_form_data(form_data(**{
            "conversion-final_directory": "/new", "search-max_candidates": "7",
            "conversion-log_level": "debug", "download-sabnzbd_use_ssl": "on"}))

        settings = form.apply_to(SETTINGS)

        assert settings["conversion"]["final_directory"] == "/new"
        assert settings["conversion"]["log_level"] == "debug"
        assert settings["conversion"]["custom"] == "kept"
        assert settings["search"]["max_candidates"] == 7
        assert settings["download"]["sabnzbd_use_ssl"] is True
        assert settings["metadata"]["tracked_series"] == {"show": {"id": 1}}
        assert settings["other"] == {"kept": True}
        assert SETTINGS["conversion"]["final_directory"] == "/final"

    def test_empty_values_remove_settings(self):
        form = SettingsForm.from_form_data(form_data(**{
            "conversion-final_directory": "", "search-max_candidates": "",
            "download-delete_completed_downloads": None, "substitution_count": "0"}))

        settings = form.apply_to(SETTINGS)

        assert "final_directory" not in settings["conversion"]
        assert "substitutions" not in settings["conversion"]
        assert "max_candidates" not in settings["search"]
        assert settings["download"]["delete_completed_downloads"] is False

    def test_unset_false_flags_and_empty_sections_are_not_added(self):
        settings = SettingsForm.from_form_data({}).apply_to({"other": 1})

        assert settings == {"other": 1}

    def test_hidden_blank_secret_is_kept(self):
        settings = SettingsForm.from_form_data(form_data()).apply_to(SETTINGS)

        assert settings["search"]["api_key"] == "search-key"

    def test_entered_secret_replaces_saved_one(self):
        form = SettingsForm.from_form_data(form_data(**{"search-api_key": "new-key"}))

        assert form.apply_to(SETTINGS)["search"]["api_key"] == "new-key"

    def test_revealed_blank_secret_is_removed(self):
        form = SettingsForm.from_form_data(form_data(**{"search-api_key-revealed": "1"}))

        assert "api_key" not in form.apply_to(SETTINGS)["search"]

    def test_blank_substitution_rows_are_ignored(self):
        form = SettingsForm.from_form_data(form_data(**{"substitution_count": "3"}))

        assert form.validate() == []
        assert form.apply_to(SETTINGS)["conversion"]["substitutions"] == {":": " -", "?": ""}

    def test_configuration(self, tmp_path):
        form = SettingsForm.from_form_data(form_data(**{"search-max_candidates": "9"}))

        config = form.get_configuration(SETTINGS, str(tmp_path))

        assert config.search.max_candidates == 9
        assert config.search.api_key == "search-key"
        assert config.conversion.job_directory == os.path.join(str(tmp_path), ".jobs")


class TestActions:

    def test_add_and_remove_substitutions(self):
        form = SettingsForm.from_settings(SETTINGS)

        assert form.apply_action("add-substitution", SETTINGS)
        assert form.substitutions == [(":", " -"), ("?", ""), ("", "")]
        assert form.apply_action("remove-substitution-0", SETTINGS)
        assert form.substitutions == [("?", ""), ("", "")]

    def test_reveal_shows_saved_secret(self):
        form = SettingsForm.from_settings(SETTINGS)

        assert form.apply_action("reveal-search-api_key", SETTINGS)

        assert form.values["search-api_key"] == "search-key"
        assert form.revealed == {"search-api_key"}

    def test_reveal_keeps_entered_secret(self):
        form = SettingsForm.from_form_data(form_data(**{"search-api_key": "typed"}))

        form.apply_action("reveal-search-api_key", SETTINGS)

        assert form.values["search-api_key"] == "typed"

    def test_reveal_unset_secret(self):
        form = SettingsForm.from_settings(SETTINGS)

        form.apply_action("reveal-notification-auth_token", SETTINGS)

        assert form.values["notification-auth_token"] == ""
        assert form.revealed == {"notification-auth_token"}

    @pytest.mark.parametrize("action", [
        "remove-substitution-5", "reveal-search-newznab_url", "reveal-bogus", "bogus", None])
    def test_unknown_actions(self, action):
        form = SettingsForm.from_settings(SETTINGS)

        assert not form.apply_action(action, SETTINGS)

        assert form == SettingsForm.from_settings(SETTINGS)


class TestValidate:

    def test_valid(self):
        assert SettingsForm.from_form_data(form_data()).validate() == []

    @pytest.mark.parametrize("values, expected", [
        ({"search-max_candidates": "x"}, "Results to try per run must be a whole number."),
        ({"search-max_candidates": "0"}, "Results to try per run must be at least 1."),
        ({"download-sabnzbd_port": "70000"}, "Port must be at most 65535."),
        ({"conversion-final_directory": "relative"},
         "Final directory must be an absolute path, starting with /."),
        ({"conversion-log_level": "loud"},
         "Log level must be one of debug, info, warning, error, critical."),
        ({"search-newznab_url": "api.example.com"},
         "The Newznab API URL must start with http:// or https://."),
        ({"conversion-database_cache_file": "a/b"},
         "The episode database file name must be a file name, not a path."),
        ({"substitution-0-character": "ab"},
         "Each substitution must replace exactly one character."),
        ({"substitution-1-character": ":"}, "Each character can only have one substitution."),
    ])
    def test_invalid(self, values, expected):
        assert SettingsForm.from_form_data(form_data(**values)).validate() == [expected]


@pytest.mark.parametrize("key, expected", [
    ("final_directory", os.path.join("/settings", "completed")),
    ("log_level", "info"),
    ("newznab_url", "https://api.nzbplanet.net/api"),
    ("sabnzbd_port", 8080),
    ("sabnzbd_category", ""),
    ("deluge_port", ""),
])
def test_get_default(key, expected):
    setting_field = next(x for x in FIELDS if x.key == key)

    assert get_default(setting_field, "/settings") == expected
