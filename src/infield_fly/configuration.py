"""Module containing configuration information for use with Infield Fly"""

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List

DEFAULT_CONFIG_DIRECTORY = os.path.join(os.path.expanduser("~"), ".config", "infield-fly")
CONFIG_FILE_ENVIRONMENT_VARIABLE = "INFIELD_FLY_CONFIG"


class Configuration:

    """Configuration object containing settings for use with Infield Fly"""

    def __init__(self, infield_fly_directory=None, config_dict=None):
        super().__init__()

        self.settings = {}
        if config_dict is not None:
            self.settings["conversion"] = ConversionSettings(
                infield_fly_directory, config_dict.get("conversion", None))
            self.settings["metadata"] = MetadataSettings(config_dict.get("metadata", None))
            self.settings["notification"] = NotificationSettings(
                config_dict.get("notification", None))
            self.settings["search"] = SearchSettings(config_dict.get("search", None))
            self.settings["download"] = DownloadSettings(config_dict.get("download", None))
        else:
            self.settings["conversion"] = ConversionSettings(infield_fly_directory)
            self.settings["metadata"] = MetadataSettings()
            self.settings["notification"] = NotificationSettings()
            self.settings["search"] = SearchSettings()
            self.settings["download"] = DownloadSettings()

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

    @property
    def search(self):
        """Gets the settings for searching for downloads"""

        return self.settings["search"]

    @property
    def download(self):
        """Gets the settings for downloading NZB files and torrents"""

        return self.settings["download"]


class ConversionSettings:

    """Settings object containing settings for conversiion of files"""

    def __init__(self, infield_fly_dir=None, raw_settings=None):
        super().__init__()
        self.settings = raw_settings if raw_settings is not None else {}
        self.infield_fly_dir = (infield_fly_dir
                                if infield_fly_dir is not None
                                else DEFAULT_CONFIG_DIRECTORY)

    @property
    def string_substitutions(self):
        """Gets the set of string substitution for file names during conversions"""

        return self.settings.get("substitutions") or {}

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
    def log_directory(self):
        """Gets the path of the directory to which log files are written"""

        return self.settings.get("log_directory",
                                 os.path.join(self.infield_fly_directory, "logs"))

    @property
    def log_level(self):
        """Gets the log level for logging"""

        return getattr(logging, self.settings.get("log_level", "info").upper(), logging.INFO)

    @property
    def job_directory(self):
        """Gets the path of the directory to which job files are written"""

        return self.settings.get("job_directory",
                                 os.path.join(self.infield_fly_directory, ".jobs"))

    @property
    def nzb_directory(self):
        """Gets the path of the directory to which NZB files found for jobs are written"""

        return self.settings.get("nzb_directory",
                                 os.path.join(self.infield_fly_directory, ".nzb"))

    @property
    def infield_fly_directory(self):
        """
        Gets the path of the directory containing the settings file, against which
        default data directories are resolved
        """

        return self.infield_fly_dir


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


class SearchSettings:

    """Settings object containing settings for searching a Newznab indexer for downloads"""

    def __init__(self, raw_settings=None):
        super().__init__()
        self.settings = raw_settings if raw_settings is not None else {}

    @property
    def newznab_url(self):
        """Gets the URL of the Newznab API end point of the indexer"""

        return self.settings.get("newznab_url", "https://api.nzbplanet.net/api")

    @property
    def api_key(self):
        """Gets the API key for the Newznab indexer"""

        return self.settings.get("api_key", "")

    @property
    def max_candidates(self):
        """Gets the maximum number of search results to validate per job on each run"""

        return self.settings.get("max_candidates", 5)

    @property
    def is_enabled(self):
        """Gets a value indicating whether searching for downloads is configured"""

        return self.api_key != ""


class DownloadSettings:

    """
    Settings object containing settings for downloading NZB files with SABnzbd and
    torrents with Deluge
    """

    def __init__(self, raw_settings=None):
        super().__init__()
        self.settings = raw_settings if raw_settings is not None else {}

    @property
    def sabnzbd_host(self):
        """Gets the host name of the host running the SABnzbd instance"""

        return self.settings.get("sabnzbd_host", "localhost")

    @property
    def sabnzbd_port(self):
        """Gets the port for the SABnzbd instance"""

        return self.settings.get("sabnzbd_port", 8080)

    @property
    def sabnzbd_use_ssl(self):
        """Gets a value indicating whether to connect to the SABnzbd instance using SSL"""

        return self.settings.get("sabnzbd_use_ssl", False)

    @property
    def sabnzbd_api_key(self):
        """Gets the API key for the SABnzbd instance"""

        return self.settings.get("sabnzbd_api_key", "")

    @property
    def sabnzbd_category(self):
        """Gets the SABnzbd category for downloads, or None to use the default category"""

        return self.settings.get("sabnzbd_category", None)

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
        """Gets the password for the Deluge BitTorrent client"""

        return self.settings.get("deluge_password", None)

    @property
    def delete_completed_downloads(self):
        """
        Gets a value indicating whether to delete completed downloads, and remove them from
        the SABnzbd history, once they have been converted
        """

        return self.settings.get("delete_completed_downloads", False)


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
                enable_search = series_dict.get("enable_search", False)
                if enable_search:
                    primary_search_term = series_dict.get("primary_search_term", series_keyword)
                    if "search_configs" in series_dict:
                        for search_config in series_dict["search_configs"]:
                            search = SearchConfiguration(
                                [ primary_search_term ],
                                search_config.get("download_only", False)
                            )
                            search.search_terms.extend(search_config.get("search_terms", []))
                            search.release_groups.extend(
                                search_config.get("release_groups", []))
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
                if len(stored_search.release_groups) > 0:
                    search.release_groups[query] = stored_search.release_groups[:]

        if len(converted_search.queries) > 0:
            searches.insert(0, converted_search)
        return searches
