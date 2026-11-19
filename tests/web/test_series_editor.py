"""Tests for the tracked series editor of the web interface"""

import json
from unittest import mock

import pytest
from web.conftest import ROOT, SAME_ORIGIN, make_settings

from infield_fly.database import EpisodeInfo, SeriesInfo, SeriesSearchResult
from infield_fly.settings_file import SettingsChangedError, SettingsFile

SERIES_PAGE = f"{ROOT}/series"


def write_settings(settings_path, settings):
    settings_path.write_text(json.dumps(settings, indent=4), encoding="utf-8")


def read_settings(settings_path):
    return json.loads(settings_path.read_text(encoding="utf-8"))


def tracked_series(settings_path):
    return read_settings(settings_path)["metadata"]["tracked_series"]


def current_version(settings_path):
    return SettingsFile(str(settings_path)).read()[1]


@pytest.fixture
def with_api_key(settings_path, tmp_path):
    settings = make_settings(tmp_path)
    settings["metadata"]["api_key"] = "key"
    write_settings(settings_path, settings)


@pytest.fixture
def get_series():
    with mock.patch("infield_fly.database.TVMetadataProvider.get_series") as get_series:
        series = SeriesInfo(99, "New Show", "Continuing", 2024)
        series.add_episodes([EpisodeInfo(1, "New Show", season_number=1, episode_number=1),
                             EpisodeInfo(2, "New Show", season_number=1, episode_number=2)])
        get_series.return_value = series
        yield get_series


def editor_data(settings_path, original_keyword="show", **values):
    """Gets the data submitted by the series editor for the series "show", with changes"""

    data = {"version": current_version(settings_path), "original_keyword": original_keyword,
            "keyword": "show", "series_id": "1", "description": "The Show",
            "enable_search": "on", "config_count": "2",
            "config-0-search_terms": "1080p", "config-1-download_only": "on",
            "action": "save"}
    data.update(values)
    return {name: value for name, value in data.items() if value is not None}


def save(client, data):
    return client.post("/series/edit", data=data, headers=SAME_ORIGIN, follow_redirects=False)


class TestAddSeries:

    def test_search_page(self, logged_in):
        text = logged_in.get("/series/add").text

        assert "Search TheTVDB" in text
        assert "Add by TheTVDB ID instead" in text

    def test_search_requires_api_key(self, logged_in):
        text = logged_in.get("/series/add", params={"q": "show"}).text

        assert "Searching TheTVDB needs its API key" in text

    def test_search_results(self, logged_in, with_api_key):
        results = [SeriesSearchResult(1, "The Show", "2020", "Net", "Continuing", "About"),
                   SeriesSearchResult(99, "New & Show", "2024", "", "Upcoming", "")]
        with mock.patch("infield_fly.database.TVMetadataProvider.search_series",
                        return_value=results) as search_series:
            text = logged_in.get("/series/add", params={"q": " show "}).text

        search_series.assert_called_once_with("show")
        assert "Already tracked as “show”" in text
        assert "Net · Continuing · TheTVDB ID 1" in text
        assert "New &amp; Show" in text
        assert f'href="{ROOT}/series/edit?series_id=99&amp;name=New+%26+Show"' in text

    def test_no_results(self, logged_in, with_api_key):
        with mock.patch("infield_fly.database.TVMetadataProvider.search_series",
                        return_value=[]):
            text = logged_in.get("/series/add", params={"q": "nothing"}).text

        assert "No series found for “nothing”." in text

    def test_failed_search(self, logged_in, with_api_key):
        with mock.patch("infield_fly.database.TVMetadataProvider.search_series",
                        return_value=None):
            text = logged_in.get("/series/add", params={"q": "show"}).text

        assert "Searching TheTVDB failed." in text

    def test_client_is_reused_while_credentials_unchanged(self, logged_in, with_api_key,
                                                          settings_path, tmp_path):
        with mock.patch("infield_fly.web.series_editor.TVMetadataProvider") as provider_class:
            provider_class.return_value.search_series.return_value = []
            logged_in.get("/series/add", params={"q": "a"})
            logged_in.get("/series/add", params={"q": "b"})
            assert provider_class.call_count == 1

            settings = make_settings(tmp_path)
            settings["metadata"]["api_key"] = "new key"
            write_settings(settings_path, settings)
            logged_in.get("/series/add", params={"q": "c"})
            assert provider_class.call_count == 2


class TestEditForm:

    def test_existing_series(self, logged_in):
        text = logged_in.get("/series/edit", params={"keyword": "show"}).text

        assert "Edit The Show" in text
        assert 'name="config-0-search_terms" value="1080p"' in text
        assert 'name="config-1-download_only" checked' in text
        assert "<code>show 1080p s01e01</code>" in text
        assert "Stop tracking…" in text

    def test_unknown_series(self, logged_in):
        response = logged_in.get("/series/edit", params={"keyword": "nope"})

        assert response.status_code == 404

    def test_new_series(self, logged_in):
        text = logged_in.get("/series/edit",
                             params={"series_id": "99", "name": "New Show (2024)"}).text

        assert "Track New Show (2024)" in text
        assert 'name="keyword" value="new show"' in text
        assert 'name="series_id" value="99"' in text
        assert 'name="config_count" value="1"' in text
        assert "Start tracking" in text

    def test_first_submit_button_saves(self, logged_in):
        text = logged_in.get("/series/edit", params={"keyword": "show"}).text

        first_button = text[text.index("<button", text.index('class="editor"')):]
        assert first_button.startswith('<button type="submit" name="action" value="save"')


class TestSaveSeries:

    def test_saves_changes_keeping_other_settings(self, logged_in, settings_path, tmp_path,
                                                  get_series):
        settings = make_settings(tmp_path)
        settings["metadata"]["tracked_series"]["show"]["custom"] = "kept"
        settings["other"] = {"kept": True}
        write_settings(settings_path, settings)

        response = save(logged_in, editor_data(
            settings_path, description="The Show Changed", keywords="a, b",
            **{"config-0-release_groups": "flux ntb"}))

        assert response.headers["location"] == SERIES_PAGE
        saved = read_settings(settings_path)
        assert saved["other"] == {"kept": True}
        assert saved["metadata"]["tracked_series"]["show"] == {
            "id": 1, "description": "The Show Changed", "enable_search": True,
            "search_configs": [{"search_terms": ["1080p"], "release_groups": ["flux", "ntb"]},
                               {"download_only": True}],
            "custom": "kept", "keywords": ["a", "b"]}
        assert saved["metadata"]["tracked_series"]["missing"] == (
            settings["metadata"]["tracked_series"]["missing"])
        assert settings_path.read_text(encoding="utf-8").startswith('{\n    "conversion"')
        get_series.assert_not_called()

        text = logged_in.get("/series").text
        assert "Saved The Show Changed." in text
        assert "Saved The Show Changed." not in logged_in.get("/series").text

    def test_rename_keeps_position(self, logged_in, settings_path):
        save(logged_in, editor_data(settings_path, keyword="renamed"))

        assert list(tracked_series(settings_path)) == ["renamed", "missing"]

    def test_buttons_change_form_without_saving(self, logged_in, settings_path):
        before = settings_path.read_text(encoding="utf-8")

        response = save(logged_in, editor_data(settings_path, action="add-config"))

        assert response.status_code == 200
        assert 'name="config_count" value="3"' in response.text
        assert settings_path.read_text(encoding="utf-8") == before

    def test_invalid_form_is_not_saved(self, logged_in, settings_path):
        before = settings_path.read_text(encoding="utf-8")

        response = save(logged_in, editor_data(settings_path, keyword="missing"))

        assert response.status_code == 400
        assert "Another series already has the main keyword" in response.text
        assert settings_path.read_text(encoding="utf-8") == before

    def test_changed_settings_file_is_not_overwritten(self, logged_in, settings_path,
                                                      tmp_path):
        data = editor_data(settings_path, description="Mine")
        settings = make_settings(tmp_path)
        settings["metadata"]["tracked_series"]["show"]["description"] = "Theirs"
        write_settings(settings_path, settings)

        response = save(logged_in, data)

        assert response.status_code == 409
        assert "was changed by something else" in response.text
        assert 'name="description" value="Theirs"' in response.text
        assert f'name="version" value="{current_version(settings_path)}"' in response.text
        assert tracked_series(settings_path)["show"]["description"] == "Theirs"

    def test_changed_settings_file_keeps_new_series(self, logged_in, settings_path):
        data = editor_data(settings_path, original_keyword="", keyword="new show",
                           series_id="99", description="New Show")
        settings_path.write_text(settings_path.read_text(encoding="utf-8") + " ",
                                 encoding="utf-8")

        response = save(logged_in, data)

        assert response.status_code == 409
        assert 'name="keyword" value="new show"' in response.text
        assert f'name="version" value="{current_version(settings_path)}"' in response.text
        assert "new show" not in tracked_series(settings_path)

    def test_settings_file_changed_while_saving(self, logged_in, settings_path):
        data = editor_data(settings_path, description="Mine")

        with mock.patch("infield_fly.settings_file.SettingsFile.write",
                        side_effect=SettingsChangedError):
            response = save(logged_in, data)

        assert response.status_code == 409
        assert "was changed by something else" in response.text

    def test_series_removed_by_something_else(self, logged_in, settings_path, tmp_path):
        data = editor_data(settings_path)
        write_settings(settings_path, make_settings(tmp_path, {}))

        response = save(logged_in, data)

        assert response.headers["location"] == SERIES_PAGE
        assert "was removed from the settings file by something else" in (
            logged_in.get("/series").text)

    def test_new_series_retrieves_episodes(self, logged_in, settings_path, config,
                                           with_api_key, get_series):
        response = save(logged_in, editor_data(
            settings_path, original_keyword="", keyword="new show", series_id="99",
            description="New Show", config_count="1", **{"config-1-download_only": None}))

        assert response.headers["location"] == SERIES_PAGE
        assert list(tracked_series(settings_path)) == ["show", "missing", "new show"]
        assert tracked_series(settings_path)["new show"] == {
            "id": 99, "description": "New Show", "enable_search": True,
            "search_configs": [{"search_terms": ["1080p"]}]}
        get_series.assert_called_once_with(99, True)
        with open(config.conversion.database_directory + "/.dbcache", encoding="utf-8") as cache:
            assert "99" in json.load(cache)["series"]
        text = logged_in.get("/series").text
        assert "Started tracking New Show, and retrieved its 2 episodes." in text
        assert "<dd>2</dd>" in text

    def test_new_series_episodes_not_retrieved(self, logged_in, settings_path, with_api_key,
                                               get_series):
        get_series.return_value = None

        save(logged_in, editor_data(settings_path, original_keyword="", keyword="new show",
                                    series_id="99", description="New Show"))

        assert "Started tracking New Show, but its episodes could not be retrieved" in (
            logged_in.get("/series").text)

    def test_new_series_without_api_key(self, logged_in, settings_path, get_series):
        save(logged_in, editor_data(settings_path, original_keyword="", keyword="new show",
                                    series_id="99", description="New Show"))

        get_series.assert_not_called()
        assert "new show" in tracked_series(settings_path)
        assert "could not be retrieved" in logged_in.get("/series").text

    def test_changed_id_retrieves_episodes(self, logged_in, settings_path, with_api_key,
                                           get_series):
        save(logged_in, editor_data(settings_path, series_id="99"))

        get_series.assert_called_once_with(99, True)
        assert "Saved The Show, and retrieved its 2 episodes." in (
            logged_in.get("/series").text)

    def test_new_series_in_settings_without_metadata(self, logged_in, settings_path):
        write_settings(settings_path, {"other": 1})

        save(logged_in, editor_data(settings_path, original_keyword=""))

        assert read_settings(settings_path)["metadata"]["tracked_series"]["show"]["id"] == 1


class TestIgnoredSeries:

    def test_series_without_valid_id_can_be_fixed(self, logged_in, settings_path, tmp_path):
        write_settings(settings_path, make_settings(tmp_path, {
            "show": {"id": 1, "description": "The Show"},
            "broken": {"description": "Broken"},
            "garbage": "not an object"}))

        text = logged_in.get("/series").text

        assert "are ignored, because they don't have a valid TheTVDB ID" in text
        assert f'href="{ROOT}/series/edit?keyword=broken"' in text
        assert "garbage" in text
        response = logged_in.get("/series/edit", params={"keyword": "broken"})
        assert 'name="series_id" value=""' in response.text

        save(logged_in, {"version": current_version(settings_path),
                         "original_keyword": "garbage", "keyword": "garbage",
                         "series_id": "5", "description": "Garbage", "action": "save"})
        assert tracked_series(settings_path)["garbage"] == {
            "id": 5, "description": "Garbage", "enable_search": False}


class TestDeleteSeries:

    def test_confirmation(self, logged_in):
        text = logged_in.get("/series/delete", params={"keyword": "show"}).text

        assert "Stop tracking The Show?" in text
        assert "existing jobs, and its episodes in the episode database, are kept" in text

    def test_unknown_series(self, logged_in):
        assert logged_in.get("/series/delete", params={"keyword": "x"}).status_code == 404

    def test_deletes_series(self, logged_in, settings_path):
        response = logged_in.post("/series/delete", headers=SAME_ORIGIN,
                                  follow_redirects=False,
                                  data={"keyword": "show",
                                        "version": current_version(settings_path)})

        assert response.headers["location"] == SERIES_PAGE
        assert list(tracked_series(settings_path)) == ["missing"]
        assert "Stopped tracking The Show." in logged_in.get("/series").text

    def test_changed_settings_file_is_not_changed(self, logged_in, settings_path):
        version = current_version(settings_path)
        settings_path.write_text(settings_path.read_text(encoding="utf-8") + " ",
                                 encoding="utf-8")

        logged_in.post("/series/delete", headers=SAME_ORIGIN,
                       data={"keyword": "show", "version": version})

        assert "show" in tracked_series(settings_path)

    def test_changed_while_deleting(self, logged_in, settings_path):
        with mock.patch("infield_fly.settings_file.SettingsFile.write",
                        side_effect=SettingsChangedError):
            text = logged_in.post("/series/delete", headers=SAME_ORIGIN,
                                  data={"keyword": "show",
                                        "version": current_version(settings_path)}).text

        assert "was changed by something else while removing the series" in text

    def test_already_removed(self, logged_in, settings_path):
        text = logged_in.post("/series/delete", headers=SAME_ORIGIN,
                              data={"keyword": "gone",
                                    "version": current_version(settings_path)}).text

        assert "The series &#39;gone&#39; is not tracked." in text
