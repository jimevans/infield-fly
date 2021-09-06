"""Module containing configuration information for use with Infield Fly"""

import json
import os
from dataclasses import dataclass
from typing import List

class Configuration:

    """Configuration object containing settings for use with Infield Fly"""

    def __init__(self):
        super().__init__()
        self.settings = {}
        self.settings["notification"] = None
        self.settings["metadata"] = None
        settings_file_path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                          "settings.json")
        if os.path.exists(settings_file_path):
            with open(settings_file_path) as settings_file:
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
        self.settings = raw_settings

    @property
    def string_substitutions(self):
        """Gets the set of string substitution for file names during conversions"""

        return self._get_setting("substitutions")

    @property
    def ffmpeg_location(self):
        """Gets the location of ffmpeg tools"""

        return self._get_setting("ffmpeg_location")

    def _get_setting(self, setting_name):
        return (self.settings[setting_name]
               if self.settings is not None and setting_name in self.settings
               else None)


class NotificationSettings:

    """Settings object containing settings for using SMS notification with Infield Fly"""

    def __init__(self, raw_settings=None):
        super().__init__()
        self.settings = raw_settings

    @property
    def sid(self):
        """Gets the security ID for sending notifications via Trillio"""

        return self._get_setting("sid")

    @property
    def auth_token(self):
        """Gets the authorization token for sending notifications via Trillio"""

        return self._get_setting("auth_token")

    @property
    def sending_number(self):
        """Gets the number from which to send notifications via Trillio"""

        return self._get_setting("sending_number")

    @property
    def receiving_number(self):
        """Gets the number to which to send notifications via Trillio"""

        return self._get_setting("receiving_number")

    def _get_setting(self, setting_name):
        return (self.settings[setting_name]
               if self.settings is not None and setting_name in self.settings
               else "")


class MetadataSettings:

    """Settings object containing settings for retrieving episode meta with Infield Fly"""

    def __init__(self, raw_settings=None):
        super().__init__()
        self.settings = {}
        self.tracked_series = []
        if raw_settings is not None:
            self.settings["user_name"] = (raw_settings["user_name"]
                                         if "user_name" in raw_settings
                                         else "")
            self.settings["user_key"] = (raw_settings["user_key"]
                                         if "user_key" in raw_settings
                                         else "")
            self.settings["api_key"] = (raw_settings["api_key"]
                                       if "api_key" in raw_settings
                                       else "")
            self.settings["legacy_api_key"] = (raw_settings["legacy_api_key"]
                                              if "legacy_api_key" in raw_settings
                                              else "")
            self.settings["pin"] = (raw_settings["pin"]
                                   if "pin" in raw_settings
                                   else "")
            self._read_tracked_series(raw_settings)

    @property
    def user_name(self):
        """Gets the user name to use for querying for TV episode metadata"""

        return self._get_setting("user_name")

    @property
    def user_key(self):
        """Gets the user key to use for querying for TV episode metadata"""

        return self._get_setting("user_key")

    @property
    def api_key(self):
        """Gets the API key to use for querying for TV episode metadata"""

        return self._get_setting("api_key")

    @property
    def legacy_api_key(self):
        """Gets the legacy API key to use for querying for TV episode metadata"""

        return self._get_setting("legacy_api_key")

    @property
    def pin(self):
        """Gets the PIN to use for querying for TV episode metadata"""

        return self._get_setting("pin")

    def _get_setting(self, setting_name):
        return (self.settings[setting_name]
               if self.settings is not None and setting_name in self.settings
               else "")

    def _read_tracked_series(self, raw_settings):
        if "tracked_series" in raw_settings:
            for series_keyword in raw_settings["tracked_series"]:
                series_dict = raw_settings["tracked_series"][series_keyword]
                if "id" not in series_dict or series_dict["id"] <= 0:
                    continue
                series_id = series_dict["id"]
                description = series_dict["description"] if "description" in series_dict else ""
                keywords = []
                if description != "":
                    keywords.append(series_keyword.lower())
                if "keywords" in series_dict:
                    for additional_keyword in series_dict["keywords"]:
                        keywords.append(additional_keyword.lower())

                searches = []
                enable_torrent_search = (series_dict["enable_torrent_search"]
                                        if "enable_torrent_search" in series_dict
                                        else False)
                if enable_torrent_search:
                    primary_search_term = (series_dict["primary_search_term"]
                                          if "primary_search_term" in series_dict
                                          else series_keyword)
                    if "additional_search_term_sets" in series_dict:
                        for search_term_set in series_dict["additional_search_term_sets"]:
                            search = [ primary_search_term ]
                            search.extend(search_term_set)
                            searches.append(search)
                    else:
                        searches.append([primary_search_term])
                self.tracked_series.append(
                    TrackedSeries(series_id, description, series_keyword, keywords, searches))


@dataclass
class TrackedSeries:

    """Represents a tracked series"""

    series_id: str
    description: str
    main_keyword: str
    keywords: List[str]
    stored_searches: List[str]
