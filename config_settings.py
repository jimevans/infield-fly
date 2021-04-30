"""Module containing configuration information for use with Infield Fly"""

import json
import os

class Configuration:

    """Configuration object containing settings for use with Infield Fly"""

    def __init__(self):
        super().__init__()
        self.notification = None
        self.metadata = None
        settings_file_path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                          "settings.json")
        if os.path.exists(settings_file_path):
            with open(settings_file_path) as settings_file:
                settings = json.load(settings_file)
                if "notification" in settings:
                    self.notification = NotificationSettings(settings["notification"])

                if "metadata" in settings:
                    self.metadata = MetadataSettings(settings["metadata"])


class NotificationSettings:

    """Settings object containing settings for using SMS notification with Infield Fly"""

    def __init__(self, raw_settings = None):
        super().__init__()
        self.sid = ""
        self.auth_token = ""
        self.sending_number = ""
        self.receiving_number = ""
        if raw_settings is not None:
            if "sid" in raw_settings:
                self.sid = raw_settings["sid"]
            if "auth_token" in raw_settings:
                self.auth_token = raw_settings["auth_token"]
            if "sending_number" in raw_settings:
                self.sending_number = raw_settings["sending_number"]
            if "receiving_number" in raw_settings:
                self.receiving_number = raw_settings["receiving_number"]


class MetadataSettings:

    """Settings object containing settings for retrieving episode meta with Infield Fly"""

    def __init__(self, raw_settings = None):
        super().__init__()
        self.user_name = ""
        self.user_key = ""
        self.api_key = ""
        self.legacy_api_key = ""
        self.pin = ""
        self.tracked_series = []
        if raw_settings is not None:
            if "user_name" in raw_settings:
                self.user_name = raw_settings["user_name"]
            if "user_key" in raw_settings:
                self.user_key = raw_settings["user_key"]
            if "api_key" in raw_settings:
                self.api_key = raw_settings["api_key"]
            if "legacy_api_key" in raw_settings:
                self.legacy_api_key = raw_settings["legacy_api_key"]
            if "pin" in raw_settings:
                self.pin = raw_settings["pin"]
            if "tracked_series" in raw_settings:
                for series_keyword in raw_settings["tracked_series"]:
                    tracked_series = TrackedSeries.from_dictionary(
                        series_keyword, raw_settings["tracked_series"][series_keyword])
                    if tracked_series is not None:
                        self.tracked_series.append(tracked_series)


class TrackedSeries:

    """Represents a tracked series"""

    def __init__(self, series_id, description, keywords):
        super().__init__()
        self.series_id = series_id
        self.description = description
        self.keywords = keywords

    @classmethod
    def from_dictionary(cls, keyword, series_dict):
        """Creates a TrackeSeries object from a dictionary, usually created from a JSON object."""

        if "id" not in series_dict or series_dict["id"] <= 0:
            return None
        series_id = series_dict["id"]
        description = series_dict["description"] if "description" in series_dict else ""
        keywords = []
        if description != "":
            keywords.append(keyword.lower())
        if "keywords" in series_dict:
            for additional_keyword in series_dict["keywords"]:
                keywords.append(additional_keyword.lower())

        return TrackedSeries(series_id, description, keywords)
