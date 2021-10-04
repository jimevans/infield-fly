"""Module containing configuration information for use with Infield Fly"""

import json
import os
from dataclasses import dataclass
from typing import List

class Configuration:

    """Configuration object containing settings for use with Infield Fly"""

    def __init__(self, config_file=None):
        super().__init__()
        self.settings = {
            "notification": None,
            "metadata": None,
            "conversion": None
        }
        settings_file_path = (config_file
                              if config_file is not None
                              else os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                                "settings.json"))
        if os.path.exists(settings_file_path):
            with open(settings_file_path, encoding='utf-8') as settings_file:
                settings = json.load(settings_file)
                if "notification" in settings:
                    self.settings["notification"] = NotificationSettings(settings["notification"])

                if "metadata" in settings:
                    self.settings["metadata"] = MetadataSettings(settings["metadata"])

                if "conversion" in settings:
                    self.settings["conversion"] = ConversionSettings(settings["conversion"])

    @property
    def notification(self):
        """Gets the settings for sending notifications"""

        return self.settings["notification"]

    @property
    def metadata(self):
        """Gets the settings for retrieving TV episode metadata"""

        return self.settings["metadata"]

    @property
    def conversion(self):
        """Gets the settings for conversion"""

        return self.settings["conversion"]


class ConversionSettings:

    """Settings object containing settings for conversiion of files"""

    def __init__(self, raw_settings):
        super().__init__()
        self.settings = raw_settings if raw_settings is not None else {}

    @property
    def string_substitutions(self):
        """Gets the set of string substitution for file names during conversions"""

        return self.settings.get("substitutions", None)

    @property
    def ffmpeg_location(self):
        """Gets the location of ffmpeg tools"""

        return self.settings.get("ffmpeg_location", None)

    @property
    def staging_directory(self):
        """Gets the path of the directory into which converted files are to be written"""

        return self.settings.get("staging_directory",
                                 os.path.join(self.infield_fly_directory, "staging"))

    @property
    def final_directory(self):
        """Gets the path of the directory where final, converted files will be written"""

        return self.settings.get("final_directory",
                                 os.path.join(self.infield_fly_directory, "completed"))

    @property
    def database_directory(self):
        """Gets the path of the directory containing the episode database cache file"""

        return self.settings.get("database_directory", self.infield_fly_directory)

    @property
    def database_cache_file(self):
        """Gets the name of the episode database cache"""

        return self.settings.get("database_cache_file", ".dbcache")

    @property
    def deluge_host(self):
        """Gets the host name of the host running the Deluge BitTorrent client"""

        return self.settings.get("deluge_host", "localhost")

    @property
    def deluge_port(self):
        """Gets the port for the Deluge BitTorrent client"""

        return self.settings.get("deluge_port", None)

    @property
    def deluge_user_name(self):
        """Gets the user name for the Deluge BitTorrent client"""

        return self.settings.get("deluge_user_name", None)

    @property
    def deluge_password(self):
        """Gets the user name for the Deluge BitTorrent client"""

        return self.settings.get("deluge_password", None)

    @property
    def log_directory(self):
        """Gets the path of the directory to which log files are written"""

        return self.settings.get("log_directory",
                                 os.path.join(self.infield_fly_directory, "logs"))

    @property
    def job_directory(self):
        """Gets the path of the directory to which job files are written"""

        return self.settings.get("job_directory",
                                 os.path.join(self.infield_fly_directory, ".jobs"))

    @property
    def infield_fly_directory(self):
        """Gets the path of the tool installation directory"""

        return os.path.dirname(os.path.realpath(__file__))


class NotificationSettings:

    """Settings object containing settings for using SMS notification with Infield Fly"""

    def __init__(self, raw_settings=None):
        super().__init__()
        self.settings = raw_settings if raw_settings is not None else {}

    @property
    def sid(self):
        """Gets the security ID for sending notifications via Trillio"""

        return self.settings.get("sid", "")

    @property
    def auth_token(self):
        """Gets the authorization token for sending notifications via Trillio"""

        return self.settings.get("auth_token", "")

    @property
    def sending_number(self):
        """Gets the number from which to send notifications via Trillio"""

        return self.settings.get("sending_number", "")

    @property
    def receiving_number(self):
        """Gets the number to which to send notifications via Trillio"""

        return self.settings.get("receiving_number", "")


class MetadataSettings:

    """Settings object containing settings for retrieving episode meta with Infield Fly"""

    def __init__(self, raw_settings=None):
        super().__init__()
        self.settings = {}
        self.tracked_series = []
        if raw_settings is not None:
            self.settings["user_name"] = raw_settings.get("user_name", "")
            self.settings["user_key"] = raw_settings.get("user_key", "")
            self.settings["api_key"] = raw_settings.get("api_key", "")
            self.settings["legacy_api_key"] = raw_settings.get("legacy_api_key", "")
            self.settings["pin"] = raw_settings.get("pin", "")
            self._read_tracked_series(raw_settings)
        else:
            self.settings = {}

    @property
    def user_name(self):
        """Gets the user name to use for querying for TV episode metadata"""

        return self.settings.get("user_name", "")

    @property
    def user_key(self):
        """Gets the user key to use for querying for TV episode metadata"""

        return self.settings.get("user_key", "")

    @property
    def api_key(self):
        """Gets the API key to use for querying for TV episode metadata"""

        return self.settings.get("api_key", "")

    @property
    def legacy_api_key(self):
        """Gets the legacy API key to use for querying for TV episode metadata"""

        return self.settings.get("legacy_api_key", "")

    @property
    def pin(self):
        """Gets the PIN to use for querying for TV episode metadata"""

        return self.settings.get("pin", "")

    def _read_tracked_series(self, raw_settings):
        if "tracked_series" in raw_settings:
            for series_keyword in raw_settings["tracked_series"]:
                series_dict = raw_settings["tracked_series"][series_keyword]
                if "id" not in series_dict or series_dict["id"] <= 0:
                    continue
                series_id = series_dict["id"]
                description = series_dict.get("description", "")
                keywords = []
                if description != "":
                    keywords.append(series_keyword.lower())
                if "keywords" in series_dict:
                    for additional_keyword in series_dict["keywords"]:
                        keywords.append(additional_keyword.lower())

                searches = []
                enable_torrent_search = series_dict.get("enable_torrent_search", False)
                if enable_torrent_search:
                    primary_search_term = series_dict.get("primary_search_term", series_keyword)
                    if "search_configs" in series_dict:
                        for search_config in series_dict["search_configs"]:
                            search = SearchConfiguration(
                                [ primary_search_term ],
                                search_config.get("download_only", False)
                            )
                            search.search_terms.extend(search_config.get("search_terms", []))
                            searches.append(search)
                    else:
                        search = SearchConfiguration(
                            [ primary_search_term ],
                            False
                        )
                        searches.append(search)
                self.tracked_series.append(
                    TrackedSeries(series_id, description, series_keyword, keywords, searches))


@dataclass
class SearchConfiguration:

    """Represents a search configuration"""
    search_terms: List[str]
    is_download_only: bool


@dataclass
class TrackedSeries:

    """Represents a tracked series"""

    series_id: str
    description: str
    main_keyword: str
    keywords: List[str]
    stored_searches: List[SearchConfiguration]
