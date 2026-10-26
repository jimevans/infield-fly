"""Tests for the configuration module"""

import logging
import os

from infield_fly.configuration import (Configuration, ConversionSettings, MetadataSettings,
                           NotificationSettings, SearchConfiguration, TrackedSeries)


class TestConfiguration:

    def test_defaults_without_config_dict(self):
        config = Configuration("/install")

        assert isinstance(config.conversion, ConversionSettings)
        assert isinstance(config.metadata, MetadataSettings)
        assert isinstance(config.notification, NotificationSettings)
        assert config.conversion.infield_fly_directory == "/install"
        assert config.metadata.tracked_series == []
        assert config.notification.sid == ""

    def test_with_config_dict(self):
        config = Configuration("/install", {
            "conversion": {"ffmpeg_location": "/ffmpeg"},
            "metadata": {"api_key": "key"},
            "notification": {"sid": "abc"}
        })

        assert config.conversion.ffmpeg_location == "/ffmpeg"
        assert config.metadata.api_key == "key"
        assert config.notification.sid == "abc"

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
