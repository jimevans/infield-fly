"""Tests for the database module"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest

from infield_fly.configuration import MetadataSettings, TrackedSeries
from infield_fly.database import (REQUEST_TIMEOUT_SECONDS, EpisodeDatabase, EpisodeInfo,
                                  ProgressBar, SeriesInfo, TVMetadataProvider)


def make_response(status_code=200, json_value=None, text=""):
    """Creates a mock HTTP response"""

    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = json_value
    response.text = text
    return response


def episode_dict(episode_id, season, number, aired=None, name=None):
    """Creates a TheTVDB-style episode dictionary"""

    return {"id": episode_id, "seasonNumber": season, "number": number,
            "name": name or f"Episode {number}", "aired": aired}


def make_series(series_id=1, title="The Show", status="Continuing"):
    series = SeriesInfo(series_id, title, status, 2020)
    series.add_episode(EpisodeInfo(10, title, season_number=1, episode_number=1, title="Pilot",
                                   airdate=datetime(2020, 1, 1)))
    return series


@pytest.fixture
def tracked_config(make_config):
    return make_config(metadata={"api_key": "key", "pin": "pin", "tracked_series": {
        "show": {"id": 1, "description": "The Show"},
        "other": {"id": 2, "description": "Other Show"},
        "ended": {"id": 3, "description": "Ended Show"}
    }})


class TestEpisodeDatabase:

    def test_initial_state(self, make_config, tmp_path):
        episode_db = EpisodeDatabase(make_config())

        assert episode_db.cache_file_path == str(tmp_path / ".dbcache")
        assert episode_db.known_series == {}
        assert episode_db.to_json() == {"series": {}}

    def test_add_get_and_delete_series(self, make_config):
        episode_db = EpisodeDatabase(make_config())
        series = make_series()

        episode_db.add_series(series)
        assert episode_db.get_series(1) is series

        episode_db.delete_series(1)
        episode_db.delete_series(999)
        assert episode_db.known_series == {}

    def test_get_unknown_series_retrieves_metadata(self, make_config):
        episode_db = EpisodeDatabase(make_config())
        series = make_series(5)
        episode_db.metadata_provider = mock.Mock()
        episode_db.metadata_provider.get_series.return_value = series

        assert episode_db.get_series(5) is series
        assert episode_db.known_series[5] is series
        episode_db.metadata_provider.get_series.assert_called_once_with(5, False)

    def test_update_series_failure(self, make_config, caplog):
        episode_db = EpisodeDatabase(make_config())
        episode_db.metadata_provider = mock.Mock()
        episode_db.metadata_provider.get_series.return_value = None

        assert episode_db.update_series(5, True) is None
        assert episode_db.known_series == {}
        assert "Unable to retrieve metadata for series 5" in caplog.text

    def test_get_tracked_series_by_keyword(self, make_config):
        episode_db = EpisodeDatabase(make_config())
        series = make_series()
        episode_db.add_series(series)
        episode_db.tracked_series = [
            TrackedSeries(2, "Other", "other", ["other"], []),
            TrackedSeries(1, "The Show", "show", ["show", "alt"], []),
            TrackedSeries(3, "Not cached", "cached", ["cached"], [])
        ]

        assert episode_db.get_tracked_series_by_keyword("alt") is series
        assert episode_db.get_tracked_series_by_keyword("other") is None
        assert episode_db.get_tracked_series_by_keyword("cached") is None
        assert episode_db.get_tracked_series_by_keyword("missing") is None
        assert episode_db.get_all_tracked_series() is episode_db.tracked_series

    @pytest.mark.parametrize("force_updates, expected_updates", [
        (False, [1, 2]),
        (True, [1, 2, 3]),
    ])
    @pytest.mark.parametrize("is_unattended_mode", [False, True])
    def test_update_all_tracked_series(self, tracked_config, caplog, force_updates,
                                       expected_updates, is_unattended_mode):
        caplog.set_level("INFO")
        episode_db = EpisodeDatabase.load_from_cache(tracked_config)
        episode_db.add_series(make_series(2, "Other Show", "Upcoming"))
        episode_db.add_series(make_series(3, "Ended Show", "Ended"))

        with mock.patch.object(episode_db, "update_series") as update_series:
            episode_db.update_all_tracked_series(force_updates, is_unattended_mode)

        assert [x.args[0] for x in update_series.call_args_list] == expected_updates
        assert all(x.args[1] == is_unattended_mode for x in update_series.call_args_list)
        assert "Retrieving initial metadata for The Show" in caplog.text
        assert ("Skipping update of Ended Show" in caplog.text) != force_updates
        assert ("Database update completed" in caplog.text) == is_unattended_mode

    def test_save_and_load_cache_round_trip(self, tracked_config):
        episode_db = EpisodeDatabase(tracked_config)
        series = make_series()
        series.add_episode(EpisodeInfo(11, "The Show", season_number=1, episode_number=2,
                                       title="Unaired"))
        episode_db.add_series(series)

        episode_db.save_to_cache()
        with open(episode_db.cache_file_path, encoding="utf-8") as cache_file:
            assert json.load(cache_file) == {"series": {"1": {
                "id": 1, "name": "The Show", "status": "Continuing", "year": 2020,
                "episodes": [
                    {"id": 10, "seasonNumber": 1, "number": 1, "name": "Pilot",
                     "aired": "2020-01-01"},
                    {"id": 11, "seasonNumber": 1, "number": 2, "name": "Unaired",
                     "aired": None}
                ]}}}

        loaded = EpisodeDatabase.load_from_cache(tracked_config)
        loaded_series = loaded.known_series[1]
        assert loaded_series.title == "The Show"
        assert loaded_series.status == "Continuing"
        assert loaded_series.year == 2020
        assert [x.plex_title for x in loaded_series.episodes] == [
            "The Show - s01e01 - Pilot", "The Show - s01e02 - Unaired"]
        assert loaded_series.episodes[0].airdate == datetime(2020, 1, 1)
        assert loaded_series.episodes[1].airdate is None
        assert isinstance(loaded.metadata_provider, TVMetadataProvider)
        assert [x.series_id for x in loaded.tracked_series] == [1, 2, 3]

    def test_load_without_cache_or_metadata(self, tmp_path):
        config = SimpleNamespace(
            conversion=SimpleNamespace(database_directory=str(tmp_path),
                                       database_cache_file="missing"),
            metadata=None)

        episode_db = EpisodeDatabase.load_from_cache(config)

        assert episode_db.known_series == {}
        assert episode_db.metadata_provider is None
        assert episode_db.tracked_series == []

    def test_delete_cache(self, make_config):
        episode_db = EpisodeDatabase(make_config())
        episode_db.save_to_cache()

        episode_db.delete_cache()
        episode_db.delete_cache()

        assert not EpisodeDatabase.load_from_cache(make_config()).known_series


class TestSeriesInfo:

    @pytest.mark.parametrize("status, is_ongoing", [
        ("Continuing", True), ("UPCOMING", True), ("Ended", False)
    ])
    def test_is_ongoing(self, status, is_ongoing):
        assert SeriesInfo(1, "Show", status, None).is_ongoing == is_ongoing

    def test_add_and_get_episodes(self):
        series = SeriesInfo(1, "Show", "Ended", None)
        first = EpisodeInfo(1, "Show", season_number=1, episode_number=1)
        second = EpisodeInfo(2, "Show", season_number=1, episode_number=2)

        series.add_episodes([first, second])

        assert series.get_episode(1, 2) is second
        assert series.get_episode(2, 1) is None

    def test_get_episodes_by_airdate(self):
        series = SeriesInfo(1, "Show", "Ended", None)
        episodes = [EpisodeInfo(x, "Show", airdate=datetime(2020, 1, x)) for x in range(1, 6)]
        series.add_episodes(episodes)
        series.add_episode(EpisodeInfo(6, "Show"))

        found = series.get_episodes_by_airdate(datetime(2020, 1, 2), datetime(2020, 1, 4))

        assert found == episodes[1:4]

    def test_from_dictionary_with_status_object(self):
        series = SeriesInfo.from_dictionary(
            {"id": 1, "name": "Show", "status": {"name": "Ended"}, "year": "2001"})

        assert (series.series_id, series.title, series.status, series.year) == (
            1, "Show", "Ended", "2001")

    @pytest.mark.parametrize("extra", [{}, {"year": None}])
    def test_from_dictionary_with_status_string_and_no_year(self, extra):
        series = SeriesInfo.from_dictionary({"id": 1, "name": "Show", "status": "Ended", **extra})

        assert series.status == "Ended"
        assert series.year is None


class TestEpisodeInfo:

    def test_defaults(self):
        episode = EpisodeInfo(1, "Show")

        assert (episode.season_number, episode.episode_number, episode.title,
                episode.airdate) == (0, 0, "", None)

    def test_plex_title(self):
        episode = EpisodeInfo(1, "Show", season_number=2, episode_number=3, title="Name")

        assert episode.plex_title == "Show - s02e03 - Name"

    def test_from_dictionary(self):
        episode = EpisodeInfo.from_dictionary("Show", episode_dict(7, 1, 2, "2021-03-04"))

        assert episode.episode_id == 7
        assert episode.airdate == datetime(2021, 3, 4)
        assert episode.title == "Episode 2"

    def test_from_dictionary_without_id_or_airdate(self):
        episode = EpisodeInfo.from_dictionary(
            "Show", {"name": "Name", "seasonNumber": 1, "number": 2})

        assert episode.episode_id is None
        assert episode.airdate is None


@pytest.fixture
def provider():
    return TVMetadataProvider(MetadataSettings({"api_key": "key", "pin": "pin"}))


@pytest.fixture
def authenticated_provider(provider):
    provider.token = "tok"
    provider.token_expiry = datetime.now() + timedelta(days=1)
    return provider


class TestAuthenticate:

    def test_success(self, provider):
        with mock.patch("infield_fly.database.requests.post",
                        return_value=make_response(200, {"data": {"token": "tok"}})) as post:
            provider.authenticate()

        post.assert_called_once_with("https://api4.thetvdb.com/v4/login",
                                     json={"apikey": "key", "pin": "pin"},
                                     timeout=REQUEST_TIMEOUT_SECONDS)
        assert provider.token == "tok"
        assert provider.token_expiry > datetime.now() + timedelta(days=27)

    def test_failure_clears_token(self, authenticated_provider, caplog):
        with mock.patch("infield_fly.database.requests.post", return_value=make_response(401)):
            authenticated_provider.authenticate()

        assert authenticated_provider.token is None
        assert authenticated_provider.token_expiry is None
        assert "Authorization failed" in caplog.text


class TestGetData:

    def test_uses_existing_token(self, authenticated_provider):
        with mock.patch.object(authenticated_provider, "authenticate") as authenticate, \
                mock.patch("infield_fly.database.requests.get",
                           return_value=make_response(200, {"data": 1})) as get:
            result = authenticated_provider.get_data("series/1", {"page": 0})

        assert result == {"data": 1}
        authenticate.assert_not_called()
        get.assert_called_once_with("https://api4.thetvdb.com/v4/series/1",
                                    headers={"Authorization": "Bearer tok"},
                                    params={"page": 0}, timeout=REQUEST_TIMEOUT_SECONDS)

    @pytest.mark.parametrize("token, expiry", [
        (None, None),
        ("old", datetime.now() - timedelta(seconds=1)),
    ])
    def test_authenticates_when_token_missing_or_expired(self, provider, token, expiry):
        provider.token = token
        provider.token_expiry = expiry

        def authenticate():
            provider.token = "new"
            provider.token_expiry = datetime.now() + timedelta(days=1)

        with mock.patch.object(provider, "authenticate", side_effect=authenticate), \
                mock.patch("infield_fly.database.requests.get",
                           return_value=make_response(200, {})) as get:
            assert provider.get_data("x") == {}

        assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer new"}

    def test_returns_none_when_authentication_fails(self, provider):
        with mock.patch.object(provider, "authenticate"), \
                mock.patch("infield_fly.database.requests.get") as get:
            assert provider.get_data("x") is None

        get.assert_not_called()

    def test_reauthenticates_on_unauthorized(self, authenticated_provider):
        def authenticate():
            authenticated_provider.token = "new"

        responses = [make_response(401), make_response(200, {"data": 2})]
        with mock.patch.object(authenticated_provider, "authenticate",
                               side_effect=authenticate), \
                mock.patch("infield_fly.database.requests.get", side_effect=responses) as get:
            assert authenticated_provider.get_data("x") == {"data": 2}

        assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer new"}

    def test_returns_none_when_reauthentication_fails(self, authenticated_provider):
        def authenticate():
            authenticated_provider.token = None

        with mock.patch.object(authenticated_provider, "authenticate",
                               side_effect=authenticate), \
                mock.patch("infield_fly.database.requests.get",
                           return_value=make_response(401)) as get:
            assert authenticated_provider.get_data("x") is None

        get.assert_called_once()

    def test_returns_none_on_error_status(self, authenticated_provider, caplog):
        with mock.patch("infield_fly.database.requests.get",
                        return_value=make_response(500, text="Server error")):
            assert authenticated_provider.get_data("x") is None

        assert "Received error response (500): Server error" in caplog.text


SERIES_DATA = {"id": 1, "name": "The Show", "status": {"name": "Continuing"}, "year": "2020"}


class TestGetSeries:

    def test_paginates_and_sorts_episodes(self, provider, capsys):
        pages = [
            {"data": {"series": SERIES_DATA,
                      "episodes": [episode_dict(3, 2, 1, "2021-01-01"),
                                   episode_dict(1, 0, 1)]}},
            {"data": {"series": SERIES_DATA,
                      "episodes": [episode_dict(2, 1, 1)]}},
            {"data": {"series": SERIES_DATA, "episodes": []}},
        ]
        with mock.patch.object(provider, "get_data", side_effect=pages) as get_data, \
                mock.patch.object(provider, "get_episode_airdate",
                                  return_value=datetime(2020, 5, 5)) as get_airdate:
            series = provider.get_series(1)

        # The params dictionary is reused across requests, so every call records the last page.
        assert [x.args for x in get_data.call_args_list] == [
            ("series/1/episodes/default", {"page": 2})] * 3
        assert series.title == "The Show"
        assert series.status == "Continuing"
        assert [x.episode_id for x in series.episodes] == [1, 2, 3]
        # Only regular-season episodes without an air date are looked up.
        get_airdate.assert_called_once_with(2)
        assert series.episodes[1].airdate == datetime(2020, 5, 5)
        assert "Process episodes:" in capsys.readouterr().out

    def test_unattended_mode_does_not_show_progress(self, provider, capsys):
        pages = [{"data": {"series": SERIES_DATA,
                           "episodes": [episode_dict(1, 1, 1, "2020-01-01")]}},
                 {"data": {"series": SERIES_DATA, "episodes": []}}]
        with mock.patch.object(provider, "get_data", side_effect=pages):
            series = provider.get_series(1, is_unattended_mode=True)

        assert len(series.episodes) == 1
        assert capsys.readouterr().out == ""

    def test_series_without_episodes(self, provider, capsys):
        page = {"data": {"series": SERIES_DATA, "episodes": []}}
        with mock.patch.object(provider, "get_data", return_value=page):
            series = provider.get_series(1)

        assert series.episodes == []
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("response", [None, {"data": None}])
    def test_returns_none_on_first_page_failure(self, provider, response):
        with mock.patch.object(provider, "get_data", return_value=response):
            assert provider.get_series(1) is None

    @pytest.mark.parametrize("response", [None, {"data": None}])
    def test_returns_none_on_later_page_failure(self, provider, response):
        pages = [{"data": {"series": SERIES_DATA, "episodes": [episode_dict(1, 1, 1)]}}, response]
        with mock.patch.object(provider, "get_data", side_effect=pages):
            assert provider.get_series(1) is None


class TestGetEpisodeAirdate:

    def test_returns_airdate(self, provider):
        with mock.patch.object(provider, "get_data",
                               return_value={"data": {"aired": "2020-02-03"}}) as get_data:
            assert provider.get_episode_airdate(5) == datetime(2020, 2, 3)

        get_data.assert_called_once_with("episodes/5")

    @pytest.mark.parametrize("response", [
        None, {"data": None}, {"data": {}}, {"data": {"aired": None}}
    ])
    def test_returns_none_without_airdate(self, provider, response):
        with mock.patch.object(provider, "get_data", return_value=response):
            assert provider.get_episode_airdate(5) is None


class TestProgressBar:

    def test_defaults(self, capsys):
        progress_bar = ProgressBar(4)

        assert progress_bar.prefix == ""
        assert progress_bar.suffix == ""
        assert progress_bar.decimals == 1
        assert progress_bar.length == 100
        assert progress_bar.fill_character == "█"
        assert progress_bar.print_end == "\r"
        output = capsys.readouterr().out
        assert output.startswith("\r |---")
        assert output.endswith("|   0.0% \r")

    def test_increment_and_clear(self, capsys):
        progress_bar = ProgressBar(4, prefix="P", suffix="S", decimals=0, length=24, fill="#",
                                   print_end="\n", increment_value=2)

        progress_bar.increment()
        progress_bar.increment()
        progress_bar.clear()

        assert capsys.readouterr().out == (
            "\rP |------------|    0% S\n"
            "\rP |######------|   50% S\n"
            "\rP |############|  100% S\n"
            f"\r{' ' * 24}\r")
