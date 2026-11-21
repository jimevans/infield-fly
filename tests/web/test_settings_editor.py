"""Tests for the settings page of the web interface"""

import json
from unittest import mock

import pytest
from web.conftest import ROOT, SAME_ORIGIN, make_settings

from infield_fly.settings_file import SettingsChangedError, SettingsFile

SETTINGS_PAGE = f"{ROOT}/settings"


@pytest.fixture
def settings(settings_path, tmp_path):
    """Writes settings with secrets and other values, returning them"""

    values = make_settings(tmp_path, search={"api_key": "SEARCH-SECRET", "max_candidates": 3},
                           download={"sabnzbd_api_key": "SAB-SECRET"}, other={"kept": True})
    values["conversion"]["substitutions"] = {":": " -"}
    settings_path.write_text(json.dumps(values, indent=4), encoding="utf-8")
    return values


def read_settings(settings_path):
    return json.loads(settings_path.read_text(encoding="utf-8"))


def page_data(settings_path, **values):
    """Gets the data submitted by the settings page for the saved settings, with changes"""

    saved = read_settings(settings_path)
    conversion = saved["conversion"]
    data = {"version": SettingsFile(str(settings_path)).read()[1], "action": "save",
            "search-max_candidates": "3",
            "substitution_count": str(len(conversion.get("substitutions", {})))}
    for key in ("staging_directory", "job_directory", "database_directory", "log_directory",
                "nzb_directory"):
        data[f"conversion-{key}"] = conversion[key]
    for index, (character, replacement) in enumerate(conversion.get("substitutions",
                                                                    {}).items()):
        data[f"substitution-{index}-character"] = character
        data[f"substitution-{index}-replacement"] = replacement
    data.update(values)
    return {name: value for name, value in data.items() if value is not None}


def post(client, data):
    return client.post("/settings", data=data, headers=SAME_ORIGIN, follow_redirects=False)


class TestSettingsPage:

    def test_secrets_are_not_shown(self, logged_in, settings, settings_path):
        text = logged_in.get("/settings").text

        assert "SEARCH-SECRET" not in text
        assert "SAB-SECRET" not in text
        assert 'API key\n  <span class="muted">· set</span>' in text
        assert 'Auth token\n  <span class="muted">· not set</span>' in text
        assert str(settings_path) in text

    def test_values_and_defaults(self, logged_in, settings):
        text = logged_in.get("/settings").text

        assert 'name="search-max_candidates" value="3"' in text
        assert "Default: https://api.nzbplanet.net/api" in text
        assert 'name="substitution-0-replacement" value=" -"' in text
        assert '<option value="" selected>Default (info)</option>' in text

    def test_navigation_link(self, logged_in, settings):
        assert f'<a href="{SETTINGS_PAGE}" aria-current="page">Settings</a>' in (
            logged_in.get("/settings").text)


class TestSaveSettings:

    def test_saves_and_keeps_other_settings(self, logged_in, settings, settings_path):
        response = post(logged_in, page_data(settings_path, **{
            "conversion-log_level": "debug", "download-sabnzbd_port": "9090",
            "search-api_key": "NEW-SECRET"}))

        assert response.headers["location"] == SETTINGS_PAGE
        saved = read_settings(settings_path)
        assert saved["conversion"]["log_level"] == "debug"
        assert saved["download"] == {"sabnzbd_api_key": "SAB-SECRET", "sabnzbd_port": 9090}
        assert saved["search"] == {"api_key": "NEW-SECRET", "max_candidates": 3}
        assert saved["metadata"] == settings["metadata"]
        assert saved["other"] == {"kept": True}
        assert settings_path.read_text(encoding="utf-8").startswith('{\n    "conversion"')
        text = logged_in.get("/settings").text
        assert "Saved the settings." in text
        assert "Saved the settings." not in logged_in.get("/settings").text

    def test_invalid_settings_are_not_saved(self, logged_in, settings, settings_path):
        before = settings_path.read_text(encoding="utf-8")

        response = post(logged_in, page_data(settings_path,
                                             **{"download-sabnzbd_port": "port"}))

        assert response.status_code == 400
        assert "Port must be a whole number." in response.text
        assert 'name="download-sabnzbd_port" value="port"' in response.text
        assert settings_path.read_text(encoding="utf-8") == before

    def test_changed_settings_file_is_not_overwritten(self, logged_in, settings,
                                                      settings_path):
        data = page_data(settings_path, **{"conversion-log_level": "debug"})
        changed = read_settings(settings_path)
        changed["search"]["max_candidates"] = 8
        settings_path.write_text(json.dumps(changed), encoding="utf-8")

        response = post(logged_in, data)

        assert response.status_code == 409
        assert "was changed by something else" in response.text
        assert 'name="search-max_candidates" value="8"' in response.text
        assert "log_level" not in read_settings(settings_path)["conversion"]

    def test_settings_file_changed_while_saving(self, logged_in, settings, settings_path):
        with mock.patch("infield_fly.settings_file.SettingsFile.write",
                        side_effect=SettingsChangedError):
            response = post(logged_in, page_data(settings_path))

        assert response.status_code == 409

    def test_moved_directories_and_missing_ffmpeg_are_warned(self, logged_in, settings,
                                                             settings_path, tmp_path):
        post(logged_in, page_data(settings_path, **{
            "conversion-job_directory": str(tmp_path / "new-jobs"),
            "conversion-ffmpeg_location": str(tmp_path)}))

        text = logged_in.get("/settings").text
        assert ("The job directory changed. Files already in the old locations were not "
                "moved.") in text
        assert f"There is no ffmpeg in {tmp_path}" in text

    def test_ffmpeg_found(self, logged_in, settings, settings_path, tmp_path):
        (tmp_path / "ffmpeg").touch()

        post(logged_in, page_data(settings_path,
                                  **{"conversion-ffmpeg_location": str(tmp_path)}))

        text = logged_in.get("/settings").text
        assert "Saved the settings." in text
        assert "There is no ffmpeg" not in text


class TestActions:

    def test_reveal_shows_secret_without_saving(self, logged_in, settings, settings_path):
        before = settings_path.read_text(encoding="utf-8")

        response = post(logged_in, page_data(settings_path, **{
            "action": "reveal-search-api_key", "search-max_candidates": "4"}))

        assert 'name="search-api_key" value="SEARCH-SECRET"' in response.text
        assert 'name="search-api_key-revealed" value="1"' in response.text
        assert "SAB-SECRET" not in response.text
        assert 'name="search-max_candidates" value="4"' in response.text
        assert settings_path.read_text(encoding="utf-8") == before

    def test_revealed_secret_in_collapsed_group_opens_it(self, logged_in, settings,
                                                         settings_path):
        text = post(logged_in, page_data(settings_path,
                                         action="reveal-download-deluge_password")).text

        assert '<details class="group" open>\n    <summary>Deluge (torrents)' in text

    def test_add_substitution(self, logged_in, settings, settings_path):
        text = post(logged_in, page_data(settings_path, action="add-substitution")).text

        assert 'name="substitution_count" value="2"' in text


class TestChecks:

    def test_check_uses_unsaved_values_and_saved_secrets(self, logged_in, settings,
                                                         settings_path):
        before = settings_path.read_text(encoding="utf-8")
        check = mock.Mock(return_value=(True, "Connected to SABnzbd 4.3.2."))

        with mock.patch.dict("infield_fly.web.connection_checks.CHECKS", {"sabnzbd": check}):
            response = post(logged_in, page_data(settings_path, **{
                "action": "check-sabnzbd", "download-sabnzbd_host": "sab.local"}))

        config = check.call_args[0][0]
        assert config.download.sabnzbd_host == "sab.local"
        assert config.download.sabnzbd_api_key == "SAB-SECRET"
        assert config.conversion.job_directory == settings["conversion"]["job_directory"]
        assert ('<p class="flash flash-success" role="status">Connected to SABnzbd 4.3.2.</p>'
                in response.text)
        assert 'name="download-sabnzbd_host" value="sab.local"' in response.text
        assert settings_path.read_text(encoding="utf-8") == before

    def test_failed_check(self, logged_in, settings, settings_path):
        check = mock.Mock(return_value=(False, "API Key Incorrect"))

        with mock.patch.dict("infield_fly.web.connection_checks.CHECKS", {"tvdb": check}):
            text = post(logged_in, page_data(settings_path, action="check-tvdb")).text

        assert '<p class="flash flash-warning" role="status">API Key Incorrect</p>' in text

    def test_invalid_values_are_not_checked(self, logged_in, settings, settings_path):
        check = mock.Mock()

        with mock.patch.dict("infield_fly.web.connection_checks.CHECKS", {"sabnzbd": check}):
            response = post(logged_in, page_data(settings_path, **{
                "action": "check-sabnzbd", "download-sabnzbd_port": "x"}))

        check.assert_not_called()
        assert response.status_code == 400

    def test_unknown_check_is_ignored(self, logged_in, settings, settings_path):
        before = settings_path.read_text(encoding="utf-8")

        response = post(logged_in, page_data(settings_path, action="check-bogus"))

        assert response.status_code == 200
        assert settings_path.read_text(encoding="utf-8") == before
