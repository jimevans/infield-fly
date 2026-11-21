"""Database of episode metadata"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter

import requests

from infield_fly.concurrency import atomic_write

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30


class EpisodeDatabase:

    """The episode database of all known series"""

    def __init__(self, config):
        self.cache_directory = config.conversion.database_directory
        self.cache_file_name = config.conversion.database_cache_file
        self.known_series = {}
        self.metadata_provider = None
        self.tracked_series = []

    def to_json(self):
        """Serializes this episode database to a JSON format"""

        return {
            "series": self.known_series
        }

    def add_series(self, series):
        """Adds a series to this episode database"""

        self.known_series[series.series_id] = series

    def delete_series(self, series_id):
        """Removes a series to this episode database"""

        if series_id in self.known_series:
            del self.known_series[series_id]

    def get_series(self, series_id):
        """Gets a series from this episode database by its ID"""

        series_info = None
        if series_id in self.known_series:
            series_info = self.known_series[series_id]
        else:
            series_info = self.update_series(series_id)

        return series_info

    def get_tracked_series_by_keyword(self, keyword):
        """Gets a series from this episode database by a keyword"""

        for tracked_series in self.tracked_series:
            if (keyword in tracked_series.keywords
                    and tracked_series.series_id in self.known_series):
                return self.get_series(tracked_series.series_id)
        return None

    def get_all_tracked_series(self):
        """Gets all tracked series in this episode database"""

        return self.tracked_series

    def update_all_tracked_series(self, force_updates=False, is_unattended_mode=False):
        """Updates all tracked series in this episode database"""

        if is_unattended_mode:
            logger.info("Starting database update")
        start_time = perf_counter()

        for series in self.tracked_series:
            series_id = series.series_id
            series_description = series.description
            if series_id not in self.known_series:
                logger.info("Retrieving initial metadata for %s", series_description)
                self.update_series(series_id, is_unattended_mode)
            elif force_updates or self.known_series[series_id].is_ongoing:
                logger.info("Updating metadata for %s", series_description)
                self.update_series(series_id, is_unattended_mode)
            else:
                logger.info(
                    "Skipping update of %s; series status is '%s'.",
                    series_description,
                    self.known_series[series_id].status)

        end_time = perf_counter()
        if is_unattended_mode:
            logger.info("Database update completed in %s seconds", end_time - start_time)

    def update_series(self, series_id, is_unattended_mode=False):
        """Updates the metadata for a series in this episode database"""

        series_info = self.metadata_provider.get_series(series_id, is_unattended_mode)
        if series_info is None:
            logger.warning("Unable to retrieve metadata for series %s", series_id)
            return None

        self.add_series(series_info)
        return series_info

    def save_to_cache(self):
        """Writes this episode database to a cache file"""

        dbcache_file_path = self.cache_file_path
        with atomic_write(dbcache_file_path) as dbcache_file:
            json.dump(self, dbcache_file, indent=2, default=lambda x: x.to_json())

    def delete_cache(self):
        """Deletes the episode database cache file"""

        dbcache_file_path = self.cache_file_path
        if os.path.exists(dbcache_file_path):
            os.remove(dbcache_file_path)

    @property
    def cache_file_path(self):
        """Gets the path to the episode database cache file"""

        return os.path.join(self.cache_directory, self.cache_file_name)

    @classmethod
    def load_from_cache(cls, config):
        """Creates an episode database from a cache file"""

        episode_db = EpisodeDatabase(config)
        dbcache_file_path = episode_db.cache_file_path
        if os.path.exists(dbcache_file_path):
            with open(dbcache_file_path, encoding='utf-8') as dbcache_file:
                dbcache = json.load(dbcache_file)
                for series_id in dbcache["series"]:
                    series_object = dbcache["series"][series_id]
                    series_info = SeriesInfo.from_dictionary(series_object)
                    for episode_object in series_object["episodes"]:
                        episode_info = EpisodeInfo.from_dictionary(series_info.title,
                                                                   episode_object)
                        series_info.add_episode(episode_info)
                    episode_db.add_series(series_info)
        if config.metadata is not None:
            episode_db.metadata_provider = TVMetadataProvider(config.metadata)
            episode_db.tracked_series.extend(config.metadata.tracked_series)
        return episode_db


class SeriesInfo:

    """The metadata for a series"""

    def __init__(self, series_id, title, status, year):
        self.series_id = series_id
        self.title = title
        self.status = status
        self.year = year
        self.episodes = []

    def to_json(self):
        """Serializes this series metadata to a JSON format"""

        return {
            "id": self.series_id,
            "name": self.title,
            "status": self.status,
            "year": self.year,
            "episodes": self.episodes
        }

    @property
    def is_ongoing(self):
        """Gets a value indicating whether this series is currently airing new episodes"""

        return self.status.lower() == "continuing" or self.status.lower() == "upcoming"

    def add_episodes(self, episodes):
        """Adds metadata for multiple episodes to this series"""

        self.episodes.extend(episodes)

    def add_episode(self, episode):
        """Adds metadata for a single episode to this series"""

        self.episodes.append(episode)

    def get_episode(self, season_number, episode_number):
        """Gets metadata for an episode by its season number and episod number"""

        for episode in self.episodes:
            if episode.season_number == season_number and episode.episode_number == episode_number:
                return episode

        return None

    def get_episodes_by_airdate(self, start_date, end_date):
        """Gets metadata for all episodes aired between specified dates"""

        found_episodes = []
        for episode in self.episodes:
            if (episode.airdate is not None
                    and episode.airdate >= start_date and episode.airdate <= end_date):
                found_episodes.append(episode)

        return found_episodes

    @classmethod
    def from_dictionary(cls, series_dict):
        """Creates a SeriesInfo object from a dictionary, usually created from a JSON object."""

        series_id = series_dict["id"]
        series_title = series_dict["name"]
        # TheTVDB reports the status as an object with a name, or older caches as the name.
        series_status = series_dict["status"]
        if isinstance(series_status, dict):
            series_status = series_status.get("name", series_status)

        return cls(series_id, series_title, series_status, series_dict.get("year"))


class EpisodeInfo:

    """The metadata for an episode"""

    def __init__(self, episode_id, series_title, *, season_number=0, episode_number=0,
                 title="", airdate=None):
        self.episode_id = episode_id
        self.series_title = series_title
        self.season_number = season_number
        self.episode_number = episode_number
        self.title = title
        self.airdate = airdate

    def to_json(self):
        """Serializes this episode metadata to a JSON format"""

        return {
            "id": self.episode_id,
            "seasonNumber": self.season_number,
            "number": self.episode_number,
            "name": self.title,
            "aired": self.airdate.strftime("%Y-%m-%d") if self.airdate is not None else None
        }

    @classmethod
    def from_dictionary(cls, series_title, episode_dict):
        """Creates an EpisodeInfo object from a dictionary, usually created from a JSON object."""

        episode_id = episode_dict.get("id")
        episode_title = episode_dict["name"]
        season_number = episode_dict["seasonNumber"]
        episode_number = episode_dict["number"]
        aired = episode_dict.get("aired")
        airdate = datetime.strptime(aired, "%Y-%m-%d") if aired is not None else None

        return cls(episode_id,
                   series_title,
                   title=episode_title,
                   season_number=season_number,
                   episode_number=episode_number,
                   airdate=airdate)

    @property
    def plex_title(self):
        """Returns the title of the episode in a format compatible with Plex naming conventions"""

        return (f"{self.series_title} - "
                f"s{self.season_number:02d}e{self.episode_number:02d} - "
                f"{self.title}")


@dataclass
class SeriesSearchResult:

    """A series found by searching thetvdb.com"""

    series_id: int
    name: str
    year: str
    network: str
    status: str
    overview: str

    @classmethod
    def from_dictionary(cls, result_dict):
        """
        Creates a search result from a thetvdb.com search result, preferring its English
        name and overview
        """

        return cls(int(result_dict["tvdb_id"]),
                   (result_dict.get("translations") or {}).get("eng")
                   or result_dict.get("name") or "",
                   result_dict.get("year") or "",
                   result_dict.get("network") or "",
                   result_dict.get("status") or "",
                   (result_dict.get("overviews") or {}).get("eng")
                   or result_dict.get("overview") or "")


class TVMetadataProvider:

    """The provider to retrieve metadata from thetvdb.com"""

    def __init__(self, metadata_settings):
        self.api_key = metadata_settings.api_key
        self.pin = metadata_settings.pin
        self.base_url = "https://api4.thetvdb.com/v4/"
        self.token = None
        self.token_expiry = None

    def authenticate(self):
        """Authenticates the connection to use the thetvdb.com API"""

        logger.debug("Authorizing to TV metadata provider")
        authentication_payload = { "apikey": self.api_key, "pin": self.pin }
        response = requests.post(self.base_url + "login", json = authentication_payload,
                                 timeout = REQUEST_TIMEOUT_SECONDS)
        if not response.status_code == 200:
            logger.warning("Authorization failed")
            self.token = None
            self.token_expiry = None
            return
        authentication_response_json = response.json()
        logger.debug("Response from auth request: %s", authentication_response_json)
        self.token = authentication_response_json["data"]["token"]
        # Tokens are valid for one month; renew a little early to be safe.
        self.token_expiry = datetime.now() + timedelta(days=28)

    def get_data(self, url, params = None):
        """Gets data from a thetvdb.com end point"""

        if self.token is None or datetime.now() > self.token_expiry:
            self.authenticate()
            if self.token is None:
                return None

        headers = { "Authorization": "Bearer " + self.token }
        logger.debug("Making data request to %s", self.base_url + url)
        response = requests.get(self.base_url + url, headers = headers, params = params,
                                timeout = REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 401:
            # If receive Unauthorized response, authenticate and try again.
            self.authenticate()
            if self.token is None:
                return None
            headers = { "Authorization": "Bearer " + self.token }
            response = requests.get(self.base_url + url, headers = headers, params = params,
                                    timeout = REQUEST_TIMEOUT_SECONDS)

        if response.status_code != 200:
            logger.warning("Received error response (%s): %s",
                                response.status_code,
                                response.text)
            return None

        response_value = response.json()
        logger.debug("Response from data request: %s", response_value)
        return response_value

    def get_series(self, series_id, is_unattended_mode=False):
        """Retrieves metadata for a series and its episodes using the thetvdb.com API"""

        relative_url = f"series/{series_id}/episodes/default"
        page_index = 0
        params = { "page": page_index }
        result = self.get_data(relative_url, params)
        if result is None or result["data"] is None:
            return None

        data = result["data"]
        series_info = SeriesInfo.from_dictionary(data["series"])
        episodes = []
        while data["episodes"]:
            episodes.extend(data["episodes"])
            page_index += 1
            params["page"] = page_index
            result = self.get_data(relative_url, params)
            # Don't return a partial episode list if a page fails to load.
            if result is None or result["data"] is None:
                return None
            data = result["data"]
        episodes.sort(key=lambda x: (x["seasonNumber"], x["number"]))

        progress_bar = None
        if not is_unattended_mode and episodes:
            progress_bar = ProgressBar(len(episodes), length=80, prefix="Process episodes:")
        for episode_object in episodes:
            if not is_unattended_mode:
                progress_bar.increment()
            episode_info = EpisodeInfo.from_dictionary(series_info.title, episode_object)
            if episode_info.airdate is None and episode_info.season_number > 0:
                episode_info.airdate = self.get_episode_airdate(episode_info.episode_id)

            series_info.add_episode(episode_info)

        if not is_unattended_mode and progress_bar is not None:
            progress_bar.clear()

        return series_info

    def search_series(self, query):
        """
        Searches for series by name using the thetvdb.com API, returning the matching
        series, or None if the search failed
        """

        result = self.get_data("search", {"query": query, "type": "series"})
        if result is None or result.get("data") is None:
            return None
        return [SeriesSearchResult.from_dictionary(x) for x in result["data"]
                if str(x.get("tvdb_id", "")).isdigit()]

    def get_episode_airdate(self, episode_id):
        """Retrieves metadata for an episode and gets its air date using the thetvdb.com API"""

        relative_url = f"episodes/{episode_id}"
        result = self.get_data(relative_url)
        if result is None or result["data"] is None:
            return None

        data = result["data"]

        aired = data.get("aired")
        return datetime.strptime(aired, "%Y-%m-%d") if aired is not None else None


class ProgressBar:

    """Prints a progress bar in the console, with its formatting as attributes"""

    def __init__(self, total_value, *, increment_value=1, prefix="", suffix="", decimals=1,
                 length=100, fill="█", print_end="\r"):
        self.current_value = 0
        self.total_value = total_value
        self.increment_value = increment_value
        # Printed before and after the bar.
        self.prefix = prefix
        self.suffix = suffix
        # The number of decimal places in the percentage complete.
        self.decimals = decimals
        # The length of the whole line, and the character filling the completed part.
        self.length = length
        self.fill_character = fill
        # Printed at the end of each update of the line.
        self.print_end = print_end
        self._update_display()

    def increment(self):
        """Increments the progress bar and displays the result"""

        self.current_value += self.increment_value
        self._update_display()

    def clear(self):
        """Clears the progress bar"""

        clear = " " * self.length
        print(f"\r{clear}", end="\r")

    def _update_display(self):
        """Updates the display of the progress bar"""

        percent_value = 100 * (self.current_value / float(self.total_value))
        percent_format = f"{{0:>{4 + self.decimals}.{self.decimals}f}}"
        percent = percent_format.format(percent_value)
        bar_length = self.length - len(self.prefix) - len(percent) - len(self.suffix) - 6
        filled_length = int(bar_length * self.current_value // self.total_value)
        bar_text = self.fill_character * filled_length + '-' * (bar_length - filled_length)
        print(f"\r{self.prefix} |{bar_text}| {percent}% {self.suffix}", end=self.print_end)
