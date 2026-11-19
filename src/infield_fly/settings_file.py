"""Module for changing the settings file, keeping its contents other than those changed"""

import hashlib
import json
import os
import re

from infield_fly.concurrency import atomic_write

# The version of a settings file that does not exist.
MISSING_VERSION = "missing"


class SettingsChangedError(Exception):

    """Raised when the settings file was changed after it was read for changing"""


class SettingsFile:

    """
    The settings file, read and written as its raw JSON contents rather than as a
    Configuration, which omits settings it does not recognize. Each read returns a version
    identifying the contents read, and a write is refused if the file changed since the
    version it was based on was read, so that changes made elsewhere, such as by hand, are
    never silently overwritten.
    """

    def __init__(self, path):
        self.path = path

    def read(self):
        """Reads the settings, returning them with the version of the file read"""

        contents = self._read_contents()
        if contents is None:
            return {}, MISSING_VERSION
        settings = json.loads(contents.decode("utf-8"))
        if not isinstance(settings, dict):
            raise ValueError(f"Settings file '{self.path}' does not contain a JSON object")
        return settings, get_version(contents)

    def write(self, settings, expected_version):
        """
        Writes the settings, if the file is still the version from which they were read,
        returning the version written. The indentation of the existing file is kept.
        """

        contents = self._read_contents()
        current_version = MISSING_VERSION if contents is None else get_version(contents)
        if current_version != expected_version:
            raise SettingsChangedError(
                f"Settings file '{self.path}' was changed after it was read")

        text = contents.decode("utf-8") if contents is not None else ""
        new_text = json.dumps(settings, indent=detect_indent(text), ensure_ascii=False) + "\n"
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with atomic_write(self.path) as settings_file:
            settings_file.write(new_text)
        return get_version(new_text.encode("utf-8"))

    def _read_contents(self):
        try:
            with open(self.path, "rb") as settings_file:
                return settings_file.read()
        except FileNotFoundError:
            return None


def get_version(contents):
    """Gets the version identifying the contents of a settings file"""

    return hashlib.sha256(contents).hexdigest()


def detect_indent(text):
    """Gets the indentation used by JSON text, defaulting to two spaces"""

    match = re.search(r"\n([ \t]+)\S", text)
    if match is None:
        return 2
    indent = match.group(1)
    return "\t" if indent.startswith("\t") else len(indent)
