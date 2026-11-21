"""Module containing the form with which the global settings are edited in the web interface"""

import copy
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from infield_fly.configuration import Configuration

LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


@dataclass(frozen=True)
class SettingField:

    """
    A setting edited with the settings form. The kind of a setting is "text", "path" (an
    absolute path), "int", "bool", "secret" (a text value hidden unless revealed), or
    "choice".
    """

    section: str
    key: str
    label: str
    kind: str = "text"
    hint: str = ""
    choices: Tuple[str, ...] = ()
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    @property
    def name(self):
        """Gets the name of the form field for this setting"""

        return f"{self.section}-{self.key}"


@dataclass(frozen=True)
class SettingGroup:

    """A group of settings shown together, with an optional connection check"""

    title: str
    fields: Tuple[SettingField, ...]
    check: Optional[str] = None
    is_collapsed: bool = False
    warning: str = ""


WORKING_DIRECTORIES = SettingGroup("Working directories", (
    SettingField("conversion", "staging_directory", "Staging directory", "path"),
    SettingField("conversion", "job_directory", "Job directory", "path"),
    SettingField("conversion", "database_directory", "Episode database directory", "path"),
    SettingField("conversion", "database_cache_file", "Episode database file name"),
    SettingField("conversion", "log_directory", "Log directory", "path"),
    SettingField("conversion", "nzb_directory", "NZB file directory", "path"),
), is_collapsed=True, warning=(
    "Changing these doesn't move the files already in them. Jobs in the old job directory "
    "are no longer processed, and the episode database is empty until it is updated, "
    "unless you move the files yourself."))

GROUPS = (
    SettingGroup("Conversion", (
        SettingField("conversion", "final_directory", "Final directory", "path",
                     "Where converted files are moved when conversion finishes."),
        SettingField("conversion", "ffmpeg_location", "ffmpeg location", "path",
                     "Directory containing ffmpeg and ffprobe. Leave empty to find them on "
                     "the PATH."),
        SettingField("conversion", "log_level", "Log level", "choice", choices=LOG_LEVELS),
    )),
    SettingGroup("TheTVDB", (
        SettingField("metadata", "api_key", "API key", "secret"),
        SettingField("metadata", "pin", "Subscriber PIN", "secret"),
    ), check="tvdb"),
    SettingGroup("Newznab indexer", (
        SettingField("search", "newznab_url", "API URL",
                     hint="The indexer's API end point."),
        SettingField("search", "api_key", "API key", "secret",
                     "Searching for downloads is turned off while this is not set."),
        SettingField("search", "max_candidates", "Results to try per run", "int",
                     "How many search results are downloaded and checked for a job on "
                     "each run.", minimum=1),
    ), check="newznab"),
    SettingGroup("SABnzbd", (
        SettingField("download", "sabnzbd_host", "Host"),
        SettingField("download", "sabnzbd_port", "Port", "int", minimum=1, maximum=65535),
        SettingField("download", "sabnzbd_use_ssl", "Connect using HTTPS", "bool"),
        SettingField("download", "sabnzbd_api_key", "API key", "secret"),
        SettingField("download", "sabnzbd_category", "Category",
                     hint="Leave empty to use SABnzbd's default category."),
        SettingField("download", "delete_completed_downloads",
                     "Delete downloads once converted", "bool"),
    ), check="sabnzbd"),
    SettingGroup("Notifications (Twilio)", (
        SettingField("notification", "sid", "Account SID"),
        SettingField("notification", "auth_token", "Auth token", "secret"),
        SettingField("notification", "sending_number", "Sending number",
                     hint="In international format, such as +15555550100."),
        SettingField("notification", "receiving_number", "Receiving number"),
    )),
    SettingGroup("Deluge (torrents)", (
        SettingField("download", "deluge_host", "Host"),
        SettingField("download", "deluge_port", "Port", "int", minimum=1, maximum=65535),
        SettingField("download", "deluge_user_name", "User name"),
        SettingField("download", "deluge_password", "Password", "secret"),
    ), is_collapsed=True),
    WORKING_DIRECTORIES,
)

FIELDS = tuple(x for group in GROUPS for x in group.fields)


def get_default(setting_field, settings_directory):
    """Gets the value a setting has when it is not set, for display"""

    if setting_field.key == "log_level":
        return "info"
    section = Configuration(settings_directory, {}).settings[setting_field.section]
    default = getattr(section, setting_field.key)
    return "" if default is None else default


@dataclass
class SettingsForm:

    """
    The values of the settings form, as entered. Values are text, except for those of
    "bool" settings. Secrets are blank unless revealed or newly entered, and a blank secret
    that isn't revealed keeps its saved value. Character substitutions are kept as a list
    of (character, replacement) pairs, in order.
    """

    values: dict
    revealed: set = field(default_factory=set)
    substitutions: List[Tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings):
        """Creates a form from the raw settings, with secrets hidden"""

        values = {}
        for setting_field in FIELDS:
            value = _get_section(settings, setting_field.section).get(setting_field.key)
            if setting_field.kind == "bool":
                values[setting_field.name] = bool(value)
            elif setting_field.kind == "secret" or value is None:
                values[setting_field.name] = ""
            else:
                values[setting_field.name] = str(value)
        substitutions = _get_section(settings, "conversion").get("substitutions") or {}
        return cls(values, set(), list(substitutions.items()))

    @classmethod
    def from_form_data(cls, form_data):
        """Creates a form from the data submitted from the settings page"""

        values = {}
        revealed = set()
        for setting_field in FIELDS:
            if setting_field.kind == "bool":
                values[setting_field.name] = form_data.get(setting_field.name) is not None
            else:
                values[setting_field.name] = form_data.get(setting_field.name, "").strip()
            if (setting_field.kind == "secret"
                    and form_data.get(f"{setting_field.name}-revealed") is not None):
                revealed.add(setting_field.name)

        try:
            count = max(0, int(form_data.get("substitution_count", "0")))
        except ValueError:
            count = 0
        # Replacements are not stripped, because their spaces are often meaningful.
        substitutions = [(form_data.get(f"substitution-{index}-character", ""),
                          form_data.get(f"substitution-{index}-replacement", ""))
                         for index in range(count)]
        return cls(values, revealed, substitutions)

    @property
    def entered_substitutions(self):
        """Gets the substitutions entered, leaving out rows left empty"""

        return [x for x in self.substitutions if x != ("", "")]

    def apply_action(self, action, settings):
        """
        Applies a change to the form requested by a button on the settings page:
        "add-substitution", "remove-substitution-N", or "reveal-NAME" to show a secret,
        returning False for any other action
        """

        if action == "add-substitution":
            self.substitutions.append(("", ""))
            return True

        match = re.fullmatch(r"remove-substitution-([0-9]+)", action or "")
        if match is not None and int(match.group(1)) < len(self.substitutions):
            del self.substitutions[int(match.group(1))]
            return True

        secret = next((x for x in FIELDS if x.kind == "secret"
                       and action == f"reveal-{x.name}"), None)
        if secret is None:
            return False
        if secret.name not in self.revealed and self.values[secret.name] == "":
            # Show the saved value, unless a new one was already entered.
            self.values[secret.name] = str(
                _get_section(settings, secret.section).get(secret.key) or "")
        self.revealed.add(secret.name)
        return True

    def validate(self):
        """Validates the form, returning a list of errors, which is empty if it is valid"""

        errors = []
        for setting_field in FIELDS:
            value = self.values[setting_field.name]
            if setting_field.kind == "bool" or value == "":
                continue
            if setting_field.kind == "int":
                errors.extend(_validate_int(setting_field, value))
            elif setting_field.kind == "path" and not os.path.isabs(value):
                errors.append(f"{setting_field.label} must be an absolute path, starting "
                              "with /.")
            elif setting_field.kind == "choice" and value not in setting_field.choices:
                errors.append(f"{setting_field.label} must be one of "
                              f"{', '.join(setting_field.choices)}.")

        url = self.values["search-newznab_url"]
        if url != "" and re.match(r"https?://[^/\s]+", url) is None:
            errors.append("The Newznab API URL must start with http:// or https://.")
        if "/" in self.values["conversion-database_cache_file"]:
            errors.append("The episode database file name must be a file name, not a path.")

        characters = [character for character, _ in self.entered_substitutions]
        if any(len(x) != 1 for x in characters):
            errors.append("Each substitution must replace exactly one character.")
        elif len(set(characters)) != len(characters):
            errors.append("Each character can only have one substitution.")
        return errors

    def apply_to(self, settings):
        """
        Gets a copy of the raw settings with the values of the form applied. An empty value
        removes the setting, so that its default applies, and settings not edited with the
        form are kept.
        """

        updated = copy.deepcopy(settings)
        for setting_field in FIELDS:
            value = self.values[setting_field.name]
            is_secret_kept = (setting_field.kind == "secret" and value == ""
                              and setting_field.name not in self.revealed)
            if is_secret_kept:
                continue
            section = _get_section(updated, setting_field.section)
            if setting_field.kind == "bool":
                # A default that isn't set stays unset, rather than being written out.
                if value or setting_field.key in section:
                    section[setting_field.key] = value
            elif value == "":
                section.pop(setting_field.key, None)
            else:
                section[setting_field.key] = (int(value) if setting_field.kind == "int"
                                              else value)
            _set_section(updated, setting_field.section, section)

        conversion = _get_section(updated, "conversion")
        if self.entered_substitutions:
            conversion["substitutions"] = dict(self.entered_substitutions)
        else:
            conversion.pop("substitutions", None)
        _set_section(updated, "conversion", conversion)
        return updated

    def get_configuration(self, settings, settings_directory):
        """
        Gets the configuration with the values of the form applied to the raw settings,
        without saving them, so that connections can be checked before saving
        """

        return Configuration(settings_directory, self.apply_to(settings))


def _validate_int(setting_field, value):
    if not re.fullmatch(r"[0-9]+", value):
        return [f"{setting_field.label} must be a whole number."]
    if setting_field.minimum is not None and int(value) < setting_field.minimum:
        return [f"{setting_field.label} must be at least {setting_field.minimum}."]
    if setting_field.maximum is not None and int(value) > setting_field.maximum:
        return [f"{setting_field.label} must be at most {setting_field.maximum}."]
    return []


def _get_section(settings, section):
    value = settings.get(section)
    return dict(value) if isinstance(value, dict) else {}


def _set_section(settings, section, values):
    """Sets a section of the raw settings, leaving out a section that is new and empty"""

    if values or section in settings:
        settings[section] = values
