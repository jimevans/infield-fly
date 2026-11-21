"""Module containing configuration information for use with Infield Fly"""

import logging
import os
from dataclasses import dataclass, field, fields
from typing import Dict, List, Optional

DEFAULT_CONFIG_DIRECTORY = os.path.join(os.path.expanduser("~"), ".config", "infield-fly")
CONFIG_FILE_ENVIRONMENT_VARIABLE = "INFIELD_FLY_CONFIG"


@dataclass(frozen=True)
class Configuration:

    """
    The settings read from the settings file, by section. A section missing from the file,
    and a setting missing from a section, have their defaults.
    """

    conversion: "ConversionSettings"
    metadata: "MetadataSettings"
    notification: "NotificationSettings"
    search: "SearchSettings"
    download: "DownloadSettings"

    @classmethod
    def from_dict(cls, infield_fly_directory=None, values=None):
        """
        Creates the configuration from the values of the settings file, in the directory
        against which default data directories are resolved
        """

        values = values or {}
        return cls(ConversionSettings.from_dict(infield_fly_directory, values.get("conversion")),
                   MetadataSettings.from_dict(values.get("metadata")),
                   NotificationSettings.from_dict(values.get("notification")),
                   SearchSettings.from_dict(values.get("search")),
                   DownloadSettings.from_dict(values.get("download")))


def read_section(cls, values, **defaults):
    """
    Creates the settings of a section, a frozen dataclass, from the section's values in the
    settings file. Each field takes the value under its name, or the key in its metadata,
    or if there is none, the default given here for it, or the field's own default. Fields
    whose metadata says they aren't read from the file take only defaults, and values whose
    keys aren't fields are ignored.
    """

    values = values or {}
    arguments = {}
    for setting in fields(cls):
        key = setting.metadata.get("key", setting.name)
        if setting.metadata.get("from_file", True) and key in values:
            arguments[setting.name] = values[key]
        elif setting.name in defaults:
            arguments[setting.name] = defaults[setting.name]
    return cls(**arguments)


# It has a field for each setting of the settings file's conversion section, and the
# settings file's directory, more than the project's limit for other classes.
@dataclass(frozen=True)
class ConversionSettings:  # pylint: disable=too-many-instance-attributes

    """The settings for converting files, and the locations of Infield Fly's own files"""

    # The directory containing the settings file, against which the default data
    # directories are resolved.
    infield_fly_directory: str = field(metadata={"from_file": False})
    # The directory into which converted files are written, and the directory to which
    # they are moved once converted.
    staging_directory: str
    final_directory: str
    # The directory and name of the episode database's cache file.
    database_directory: str
    database_cache_file: str
    log_directory: str
    job_directory: str
    # The directory to which NZB files found for jobs are written.
    nzb_directory: str
    # The directory containing ffmpeg and ffprobe, or None to find them on the PATH.
    ffmpeg_location: Optional[str] = None
    # The name of the level of messages logged, such as "info".
    log_level_name: str = field(default="info", metadata={"key": "log_level"})
    # Characters in episode titles replaced when naming converted files.
    substitutions: Optional[dict] = None

    @classmethod
    def from_dict(cls, infield_fly_directory=None, values=None):
        """
        Creates the conversion settings from their values in the settings file, with data
        directories defaulting to directories in the settings file's directory
        """

        directory = (infield_fly_directory if infield_fly_directory is not None
                     else DEFAULT_CONFIG_DIRECTORY)
        return read_section(
            cls, values, infield_fly_directory=directory,
            staging_directory=os.path.join(directory, "staging"),
            final_directory=os.path.join(directory, "completed"),
            database_directory=directory,
            database_cache_file=".dbcache",
            log_directory=os.path.join(directory, "logs"),
            job_directory=os.path.join(directory, ".jobs"),
            nzb_directory=os.path.join(directory, ".nzb"))

    @property
    def string_substitutions(self):
        """Gets the characters replaced in episode titles when naming converted files"""

        return self.substitutions or {}

    @property
    def log_level(self):
        """Gets the level of messages logged, as a logging level, such as logging.INFO"""

        return getattr(logging, self.log_level_name.upper(), logging.INFO)

    @property
    def log_file(self):
        """Gets the path of the log file written by unattended runs"""

        return os.path.join(self.log_directory, "infieldfly.log")

    @property
    def job_lock_file(self):
        """
        Gets the path of the lock file preventing more than one process from processing
        the jobs in the job directory at a time
        """

        return os.path.join(self.job_directory, ".lock")

    @property
    def database_lock_file(self):
        """
        Gets the path of the lock file preventing more than one process from updating the
        episode database at a time
        """

        return os.path.join(self.database_directory, f"{self.database_cache_file}.lock")

    @property
    def convert_lock_file(self):
        """
        Gets the path of the lock file preventing more than one staged conversion, such as
        one started from the web interface, from running at a time
        """

        return os.path.join(self.staging_directory, ".convert.lock")


@dataclass(frozen=True)
class NotificationSettings:

    """The settings for sending SMS notifications with Twilio"""

    sid: str = ""
    auth_token: str = ""
    sending_number: str = ""
    receiving_number: str = ""

    @classmethod
    def from_dict(cls, values=None):
        """Creates the notification settings from their values in the settings file"""

        return read_section(cls, values)


@dataclass(frozen=True)
class SearchSettings:

    """The settings for searching a Newznab indexer for downloads"""

    # The URL of the indexer's API end point.
    newznab_url: str = "https://api.nzbplanet.net/api"
    api_key: str = ""
    # The maximum number of search results to validate for a job on each run.
    max_candidates: int = 5

    @classmethod
    def from_dict(cls, values=None):
        """Creates the search settings from their values in the settings file"""

        return read_section(cls, values)

    @property
    def is_enabled(self):
        """Gets a value indicating whether searching for downloads is configured"""

        return self.api_key != ""


@dataclass(frozen=True)
class DownloadSettings:

    """The settings for downloading NZB files with SABnzbd, and torrents with Deluge"""

    sabnzbd_host: str = "localhost"
    sabnzbd_port: int = 8080
    sabnzbd_use_ssl: bool = False
    sabnzbd_api_key: str = ""
    # The SABnzbd category for downloads, or None for the default category.
    sabnzbd_category: Optional[str] = None
    # Whether to delete completed downloads, and remove them from the SABnzbd history, once
    # they have been converted.
    delete_completed_downloads: bool = False
    deluge_host: str = "localhost"
    deluge_port: Optional[int] = None
    deluge_user_name: Optional[str] = None
    deluge_password: Optional[str] = None

    @classmethod
    def from_dict(cls, values=None):
        """Creates the download settings from their values in the settings file"""

        return read_section(cls, values)


@dataclass(frozen=True)
class MetadataSettings:

    """The settings for retrieving episode metadata from TheTVDB, and the tracked series"""

    api_key: str = ""
    pin: str = ""
    user_name: str = ""
    user_key: str = ""
    legacy_api_key: str = ""
    # The tracked series with valid TheTVDB IDs, as TrackedSeries.
    tracked_series: tuple = field(default=(), metadata={"from_file": False})

    @classmethod
    def from_dict(cls, values=None):
        """Creates the metadata settings from their values in the settings file"""

        return read_section(cls, values, tracked_series=read_tracked_series(values or {}))


def read_tracked_series(values):
    """
    Reads the tracked series from the metadata section of the settings file, leaving out
    series without a valid TheTVDB ID
    """

    tracked_series = []
    for series_keyword, series_dict in values.get("tracked_series", {}).items():
        if "id" not in series_dict or series_dict["id"] <= 0:
            continue
        description = series_dict.get("description", "")
        keywords = []
        if description != "":
            keywords.append(series_keyword.lower())
        keywords.extend(x.lower() for x in series_dict.get("keywords", []))

        searches = []
        if series_dict.get("enable_search", False):
            primary_search_term = series_dict.get("primary_search_term", series_keyword)
            for search_config in series_dict.get("search_configs", [{}]):
                searches.append(SearchConfiguration(
                    [primary_search_term] + search_config.get("search_terms", []),
                    search_config.get("download_only", False),
                    list(search_config.get("release_groups", []))))
        tracked_series.append(TrackedSeries(series_dict["id"], description, series_keyword,
                                            keywords, searches))
    return tuple(tracked_series)


@dataclass
class SearchConfiguration:

    """
    Represents a search configuration. When release groups are listed, only search results
    from those groups are downloaded, preferring groups listed earlier.
    """
    search_terms: List[str]
    is_download_only: bool
    release_groups: List[str] = field(default_factory=list)


@dataclass
class EpisodeSearch:

    """
    Represents a search for downloads of an episode, made of queries to try in order until
    one finds a download, and the release groups, in order of preference, from which each
    query's results are accepted. Any group is accepted for a query with no groups.
    """

    queries: List[str]
    is_download_only: bool
    release_groups: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class TrackedSeries:

    """Represents a tracked series"""

    series_id: str
    description: str
    main_keyword: str
    keywords: List[str]
    stored_searches: List[SearchConfiguration]

    def get_episode_searches(self, season_number, episode_number):
        """
        Gets the searches for downloads of an episode. The search configurations whose
        downloads are converted are combined into one search, whose queries are tried in
        the order of the configurations, so only one download of the episode is converted.
        Each download-only configuration is a search of its own. Configurations producing
        the same query as an earlier one of the same kind are ignored.
        """

        episode = f"s{season_number:02d}e{episode_number:02d}"
        converted_search = EpisodeSearch([], False)
        searches = []
        for stored_search in self.stored_searches:
            query = " ".join(stored_search.search_terms + [episode])
            if not stored_search.is_download_only:
                search = converted_search
            else:
                search = next((x for x in searches if x.queries[0] == query), None)
                if search is None:
                    search = EpisodeSearch([], True)
                    searches.append(search)
            if query not in search.queries:
                search.queries.append(query)
                if stored_search.release_groups:
                    search.release_groups[query] = stored_search.release_groups[:]

        if converted_search.queries:
            searches.insert(0, converted_search)
        return searches
