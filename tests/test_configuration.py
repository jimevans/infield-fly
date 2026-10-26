"""Tests for the configuration module"""

import logging
import os

import pytest

from infield_fly.configuration import (Configuration, ConversionSettings, DownloadSettings,
                                       EpisodeSearch, MetadataSettings, NotificationSettings,
                                       SearchConfiguration, SearchSettings, TrackedSeries)


class TestConfiguration:

    def test_defaults_without_config_dict(self):
        config = Configuration("/install")

        assert isinstance(config.conversion, ConversionSettings)
        assert isinstance(config.metadata, MetadataSettings)
        assert isinstance(config.notification, NotificationSettings)
        assert isinstance(config.search, SearchSettings)
        assert isinstance(config.download, DownloadSettings)
        assert config.conversion.infield_fly_directory == "/install"
        assert config.metadata.tracked_series == []
        assert config.notification.sid == ""

    def test_with_config_dict(self):
        config = Configuration("/install", {
            "conversion": {"ffmpeg_location": "/ffmpeg"},
            "metadata": {"api_key": "key"},
            "notification": {"sid": "abc"},
            "search": {"api_key": "search key"},
            "download": {"sabnzbd_api_key": "download key"}
        })

        assert config.conversion.ffmpeg_location == "/ffmpeg"
        assert config.metadata.api_key == "key"
        assert config.notification.sid == "abc"
        assert config.search.api_key == "search key"
        assert config.download.sabnzbd_api_key == "download key"

    def test_with_empty_config_dict(self):
        config = Configuration("/install", {})

        assert config.conversion.settings == {}
        assert config.metadata.settings == {}
        assert config.notification.settings == {}


class TestConversionSettings:

    def test_defaults(self):
        settings = ConversionSettings("/install")

        assert settings.string_substitutions == {}
        assert settings.ffmpeg_location is None
        assert settings.staging_directory == os.path.join("/install", "staging")
        assert settings.final_directory == os.path.join("/install", "completed")
        assert settings.database_directory == "/install"
        assert settings.database_cache_file == ".dbcache"
        assert settings.deluge_host == "localhost"
        assert settings.deluge_port is None
        assert settings.deluge_user_name is None
        assert settings.deluge_password is None
        assert settings.log_directory == os.path.join("/install", "logs")
        assert settings.log_level == logging.INFO
        assert settings.job_directory == os.path.join("/install", ".jobs")
        assert settings.nzb_directory == os.path.join("/install", ".nzb")

    def test_default_directory_is_user_config_directory(self):
        settings = ConversionSettings()

        assert settings.infield_fly_directory == os.path.join(
            os.path.expanduser("~"), ".config", "infield-fly")

    def test_explicit_values(self):
        settings = ConversionSettings("/install", {
            "substitutions": {":": "-"},
            "ffmpeg_location": "/ffmpeg",
            "staging_directory": "/staging",
            "final_directory": "/final",
            "database_directory": "/db",
            "database_cache_file": "cache.json",
            "deluge_host": "deluge.local",
            "deluge_port": 58846,
            "deluge_user_name": "user",
            "deluge_password": "pass",
            "log_directory": "/logs",
            "log_level": "debug",
            "job_directory": "/jobs"
        })

        assert settings.string_substitutions == {":": "-"}
        assert settings.ffmpeg_location == "/ffmpeg"
        assert settings.staging_directory == "/staging"
        assert settings.final_directory == "/final"
        assert settings.database_directory == "/db"
        assert settings.database_cache_file == "cache.json"
        assert settings.deluge_host == "deluge.local"
        assert settings.deluge_port == 58846
        assert settings.deluge_user_name == "user"
        assert settings.deluge_password == "pass"
        assert settings.log_directory == "/logs"
        assert settings.log_level == logging.DEBUG
        assert settings.job_directory == "/jobs"

    def test_null_substitutions_returns_empty_dict(self):
        settings = ConversionSettings("/install", {"substitutions": None})

        assert settings.string_substitutions == {}

    def test_invalid_log_level_falls_back_to_info(self):
        settings = ConversionSettings("/install", {"log_level": "not-a-level"})

        assert settings.log_level == logging.INFO


class TestNotificationSettings:

    def test_defaults(self):
        settings = NotificationSettings()

        assert settings.sid == ""
        assert settings.auth_token == ""
        assert settings.sending_number == ""
        assert settings.receiving_number == ""

    def test_explicit_values(self):
        settings = NotificationSettings({
            "sid": "sid",
            "auth_token": "token",
            "sending_number": "+15550001111",
            "receiving_number": "+15552223333"
        })

        assert settings.sid == "sid"
        assert settings.auth_token == "token"
        assert settings.sending_number == "+15550001111"
        assert settings.receiving_number == "+15552223333"


class TestMetadataSettings:

    def test_defaults(self):
        settings = MetadataSettings()

        assert settings.user_name == ""
        assert settings.user_key == ""
        assert settings.api_key == ""
        assert settings.legacy_api_key == ""
        assert settings.pin == ""
        assert settings.tracked_series == []

    def test_explicit_values(self):
        settings = MetadataSettings({
            "user_name": "user",
            "user_key": "ukey",
            "api_key": "akey",
            "legacy_api_key": "lkey",
            "pin": "1234"
        })

        assert settings.user_name == "user"
        assert settings.user_key == "ukey"
        assert settings.api_key == "akey"
        assert settings.legacy_api_key == "lkey"
        assert settings.pin == "1234"
        assert settings.tracked_series == []

    def test_tracked_series_without_valid_id_is_skipped(self):
        settings = MetadataSettings({"tracked_series": {
            "missing": {"description": "No ID"},
            "zero": {"id": 0, "description": "Zero ID"},
            "negative": {"id": -1, "description": "Negative ID"}
        }})

        assert settings.tracked_series == []

    def test_tracked_series_with_description_and_keywords(self):
        settings = MetadataSettings({"tracked_series": {
            "Show": {"id": 100, "description": "The Show", "keywords": ["Alt", "OTHER"]}
        }})

        assert settings.tracked_series == [
            TrackedSeries(100, "The Show", "Show", ["show", "alt", "other"], [])
        ]

    def test_tracked_series_without_description_omits_main_keyword(self):
        settings = MetadataSettings({"tracked_series": {
            "Show": {"id": 100, "keywords": ["alt"]}
        }})

        assert settings.tracked_series[0].description == ""
        assert settings.tracked_series[0].keywords == ["alt"]

    def test_search_disabled_creates_no_searches(self):
        settings = MetadataSettings({"tracked_series": {
            "show": {"id": 1, "description": "Show", "enable_search": False,
                     "search_configs": [{"search_terms": ["1080p"]}]}
        }})

        assert settings.tracked_series[0].stored_searches == []

    def test_search_enabled_without_configs_uses_series_keyword(self):
        settings = MetadataSettings({"tracked_series": {
            "show": {"id": 1, "description": "Show", "enable_search": True}
        }})

        assert settings.tracked_series[0].stored_searches == [
            SearchConfiguration(["show"], False)
        ]

    def test_search_enabled_with_configs(self):
        settings = MetadataSettings({"tracked_series": {
            "show": {
                "id": 1,
                "description": "Show",
                "enable_search": True,
                "primary_search_term": "the show",
                "search_configs": [
                    {"search_terms": ["1080p", "web"]},
                    {"download_only": True},
                ]
            }
        }})

        assert settings.tracked_series[0].stored_searches == [
            SearchConfiguration(["the show", "1080p", "web"], False),
            SearchConfiguration(["the show"], True)
        ]


class TestSearchSettings:

    def test_defaults(self):
        settings = SearchSettings()

        assert settings.newznab_url == "https://api.nzbplanet.net/api"
        assert settings.api_key == ""
        assert settings.max_candidates == 5
        assert not settings.is_enabled

    def test_explicit_values(self):
        settings = SearchSettings({"newznab_url": "https://indexer/api", "api_key": "key",
                                   "max_candidates": 2})

        assert (settings.newznab_url, settings.api_key, settings.max_candidates) == (
            "https://indexer/api", "key", 2)
        assert settings.is_enabled


class TestDownloadSettings:

    def test_defaults(self):
        settings = DownloadSettings()

        assert settings.sabnzbd_url == "http://localhost:8080"
        assert settings.sabnzbd_api_key == ""
        assert settings.sabnzbd_category is None

    def test_explicit_values(self):
        settings = DownloadSettings({"sabnzbd_url": "http://sab:9090", "sabnzbd_api_key": "key",
                                     "sabnzbd_category": "tv"})

        assert (settings.sabnzbd_url, settings.sabnzbd_api_key, settings.sabnzbd_category) == (
            "http://sab:9090", "key", "tv")


class TestGetEpisodeSearches:

    @staticmethod
    def searches(*search_configs):
        tracked_series = TrackedSeries(1, "The Show", "show", ["show"], [
            SearchConfiguration(["show", *config[0]], config[1], *config[2:])
            for config in search_configs])
        return tracked_series.get_episode_searches(1, 2)

    def test_single_search(self):
        assert self.searches((["1080p"], False)) == [
            EpisodeSearch(["show 1080p s01e02"], False)]

    def test_converted_searches_are_fallbacks_in_order(self):
        assert self.searches((["2160p"], False), (["4k"], True), (["1080p"], False)) == [
            EpisodeSearch(["show 2160p s01e02", "show 1080p s01e02"], False),
            EpisodeSearch(["show 4k s01e02"], True)]

    def test_download_only_searches_are_separate(self):
        assert self.searches(([], True), (["2160p"], True)) == [
            EpisodeSearch(["show s01e02"], True), EpisodeSearch(["show 2160p s01e02"], True)]

    @pytest.mark.parametrize("download_only", [False, True])
    def test_ignores_duplicate_queries_of_same_kind(self, download_only):
        assert self.searches((["1080p"], download_only), (["1080p"], download_only)) == [
            EpisodeSearch(["show 1080p s01e02"], download_only)]

    def test_same_query_converted_and_download_only(self):
        assert self.searches((["1080p"], False), (["1080p"], True)) == [
            EpisodeSearch(["show 1080p s01e02"], False),
            EpisodeSearch(["show 1080p s01e02"], True)]

    def test_no_searches(self):
        assert self.searches() == []

    def test_release_groups_are_kept_for_each_query(self):
        assert self.searches((["2160p"], False, ["flux", "ntb"]), (["1080p"], False),
                             (["4k"], True, ["grp"])) == [
            EpisodeSearch(["show 2160p s01e02", "show 1080p s01e02"], False,
                          {"show 2160p s01e02": ["flux", "ntb"]}),
            EpisodeSearch(["show 4k s01e02"], True, {"show 4k s01e02": ["grp"]})]

    @pytest.mark.parametrize("download_only", [False, True])
    def test_duplicate_query_keeps_first_release_groups(self, download_only):
        assert self.searches((["1080p"], download_only, ["flux"]),
                             (["1080p"], download_only, ["ntb"])) == [
            EpisodeSearch(["show 1080p s01e02"], download_only,
                          {"show 1080p s01e02": ["flux"]})]

    def test_reads_release_groups_from_settings(self):
        settings = MetadataSettings({"tracked_series": {"show": {
            "id": 1, "description": "The Show", "enable_search": True,
            "search_configs": [{"search_terms": ["1080p"], "release_groups": ["flux", "ntb"]},
                               {"search_terms": ["720p"]}]}}})

        assert [x.release_groups for x in settings.tracked_series[0].stored_searches] == [
            ["flux", "ntb"], []]
