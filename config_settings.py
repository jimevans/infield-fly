import json
import os

class Configuration:

    def __init__(self):
        super().__init__()
        self.notification = None
        self.metadata = None
        settings_file_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "settings.json")
        if os.path.exists(settings_file_path):
            settings_file = open(settings_file_path)
            settings = json.load(settings_file)
            if "notification" in settings:
                self.notification = NotificationSettings(settings["notification"])

            if "metadata" in settings:
                self.metadata = MetadataSettings(settings["metadata"])

            settings_file.close()


class NotificationSettings:

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
                for series in raw_settings["tracked_series"]:
                    if "id" in series and series["id"] > 0:
                        self.tracked_series.append(series["id"])
