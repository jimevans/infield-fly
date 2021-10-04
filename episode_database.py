"""Database of episode metadata"""

import json
import logging
import os
import urllib
from datetime import datetime
from time import perf_counter
import requests

class EpisodeDatabase:

    """The episode database of all known series"""

    def __init__(self, config):
        super().__init__()
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

        logger = logging.getLogger()
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
        self.add_series(series_info)
        return series_info

    def save_to_cache(self):
        """Writes this episode database to a cache file"""

        dbcache_file_path = self.cache_file_path
        with open(dbcache_file_path, "w", encoding='utf-8') as dbcache_file:
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
        super().__init__()
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
        series_status = (series_dict["status"]["name"]
                         if "name" in series_dict["status"]
                         else series_dict["status"])
        series_year = (series_dict["year"]
                       if "year" in series_dict and series_dict["year"] is not None
                       else None)

        return cls(series_id, series_title, series_status, series_year)


class EpisodeInfo:

    """The metadata for an episode"""

    def __init__(self, episode_id, series_title, **kwargs):
        super().__init__()
        self.episode_id = episode_id
        self.series_title = series_title
        self.season_number = kwargs["season_number"] if "season_number" in kwargs else 0
        self.episode_number = kwargs["episode_number"] if "episode_number" in kwargs else 0
        self.title = kwargs["title"] if "title" in kwargs else ""
        self.airdate = kwargs["airdate"] if "airdate" in kwargs else None

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

        episode_id = episode_dict["id"] if "id" in episode_dict else None
        episode_title = episode_dict["name"]
        season_number = episode_dict["seasonNumber"]
        episode_number = episode_dict["number"]
        airdate = (datetime.strptime(episode_dict["aired"], "%Y-%m-%d")
                  if "aired" in episode_dict and episode_dict["aired"] is not None
                  else None)

        return cls(episode_id,
                   series_title,
                   title=episode_title,
                   season_number=season_number,
                   episode_number=episode_number,
                   airdate=airdate)

    @property
    def plex_title(self):
        """Returns the title of the episode in a format compatible with Plex naming conventions"""

        return "{} - s{:02d}e{:02d} - {}".format(self.series_title, self.season_number,
                                                 self.episode_number, self.title)


class TVMetadataProvider:

    """The provider to retrieve metadata from thetvdb.com"""

    def __init__(self, metadata_settings):
        super().__init__()
        self.api_key = metadata_settings.api_key
        self.pin = metadata_settings.pin
        self.base_url = "https://api4.thetvdb.com/v4/"
        self.token = None
        self.token_expiry = None
        self.logger = logging.getLogger()

    def authenticate(self):
        """Authenticates the connection to use the thetvdb.com API"""

        self.logger.debug("Authorizing to TV metadata provider")
        authentication_payload = { "apikey": self.api_key, "pin": self.pin }
        response = requests.post(self.base_url + "login", json = authentication_payload)
        if not response.status_code == 200:
            self.logger.warning("Authorization failed")
            return
        authentication_response_json = response.json()
        self.token = authentication_response_json["data"]["token"]
        self.token_expiry = datetime.now()

    def get_data(self, url, params = None):
        """Gets data from a thetvdb.com end point"""

        if self.token is None or datetime.now() > self.token_expiry:
            self.authenticate()

        headers = { "Authorization": "Bearer " + self.token }
        response = requests.get(self.base_url + url, headers = headers, params = params)
        if response.status_code == 401:
            # If receive Unauthorized response, authenticate and try again.
            self.authenticate()
            headers = { "Authorization": "Bearer " + self.token }
            response = requests.get(self.base_url + url, headers = headers, params = params)

        response_value = response.json()
        if response.status_code != 200:
            self.logger.warning("Received error response (%s): %s",
                                response.status_code,
                                response_value["message"])
            return None

        return response.json()

    def search_for_series(self, search_term, status=None):
        """Searches for a series using the thetvdb.com API"""

        series = []
        params = { "q": search_term, "type": "series" }
        relative_url = "search?{}".format(urllib.parse.urlencode(params))
        result = self.get_data(relative_url)
        search_object = result["data"]
        if search_object is not None:
            for search_result in search_object:
                if ("primary_language" in search_result
                        and search_result["primary_language"] == "eng"):
                    if status is None or search_result["status"].lower() == status.lower():
                        series_info = {}
                        series_info["id"] = search_result["tvdb_id"]
                        series_info["title"] = search_result["name"]
                        series_info["status"] = search_result["status"]["name"]
                        if "year" in search_result:
                            series_info["year"] = search_result["year"]
                        series.append(series_info)
        return series

    def get_series(self, series_id, is_unattended_mode=False):
        """Retrieves metadata for a series and its episodes using the thetvdb.com API"""

        relative_url = "series/{}/episodes/default".format(series_id)
        page_index = 0
        params = { "page": page_index }
        result = self.get_data(relative_url, params)
        data = result["data"]
        if data is None:
            return None

        series_info = SeriesInfo.from_dictionary(data["series"])
        episodes = []
        while len(data["episodes"]) > 0:
            episodes.extend(data["episodes"])
            page_index += 1
            params["page"] = page_index
            result = self.get_data(relative_url, params)
            data = result["data"]
        episodes.sort(key=lambda x: (x["seasonNumber"], x["number"]))

        if not is_unattended_mode:
            progress_bar = ProgressBar(len(episodes), length=80, prefix="Process episodes:")
        for episode_object in episodes:
            if not is_unattended_mode:
                progress_bar.increment()
            episode_info = EpisodeInfo.from_dictionary(series_info.title, episode_object)
            if episode_info.airdate is None and episode_info.season_number > 0:
                episode_info.airdate = self.get_episode_airdate(episode_info.episode_id)

            series_info.add_episode(episode_info)

        if not is_unattended_mode:
            progress_bar.clear()

        return series_info

    def get_series_extended(self, series_id):
        """
        Retrieves metadata for a series and its episodes using the thetvdb.com API, but this
        method uses a far more chatty protocol than get_series(), which should be preferred
        """

        relative_url = "series/{}/extended".format(series_id)
        page_index = 0
        params = { "page": page_index }
        result = self.get_data(relative_url, params)
        data = result["data"]
        if data is None:
            return None

        series_info = SeriesInfo.from_dictionary(data)
        episodes = self._get_episodes_extended(data)

        progress_bar = ProgressBar(len(episodes), prefix="Get episodes:")
        for episode in episodes:
            episode_info = EpisodeInfo.from_dictionary(series_info.title, episode)
            progress_bar.increment()
            if episode_info.airdate is None and episode_info.season_number > 0:
                episode_info.airdate = self.get_episode_airdate(episode_info.episode_id)

            series_info.add_episode(episode_info)
        progress_bar.clear()

        return series_info

    def get_episode_airdate(self, episode_id):
        """Retrieves metadata for an episode and gets its air date using the thetvdb.com API"""

        relative_url = "episodes/{}".format(episode_id)
        result = self.get_data(relative_url)
        data = result["data"]
        if data is None:
            return None

        airdate = (datetime.strptime(data["aired"], "%Y-%m-%d")
                  if "aired" in data and data["aired"] is not None
                  else None)
        return airdate

    def _get_episodes_extended(self, data):
        season_ids = []
        seasons = []
        for raw_season in data["seasons"]:
            if raw_season["type"]["id"] == 1 and raw_season["id"] not in season_ids:
                season_ids.append(raw_season["id"])
                seasons.append(raw_season)

        progress_bar = ProgressBar(len(seasons), prefix="Get seasons:")
        episode_ids = []
        episodes = []
        for season in seasons:
            season_url = "seasons/{}/extended".format(season["id"])
            progress_bar.increment()
            season_result = self.get_data(season_url)
            season_data = season_result["data"]
            for episode_object in season_data["episodes"]:
                if episode_object["id"] not in episode_ids:
                    episode_ids.append(episode_object["id"])
                    episodes.append(episode_object)
        progress_bar.clear()

        episodes.sort(key=lambda x: (x["seasonNumber"], x["number"]))
        return episodes


class ProgressBar:

    """Prints a progress bar in the console"""

    def __init__(self, total_value, **kwargs):
        self.current_value = 0
        self.total_value = total_value
        self.format_settings = {}
        self.increment_value = kwargs["increment_value"] if "increment_value" in kwargs else 1
        self.format_settings["prefix"] = kwargs["prefix"] if "prefix" in kwargs else ""
        self.format_settings["suffix"] = kwargs["suffix"] if "suffix" in kwargs else ""
        self.format_settings["decimals"] = kwargs["decimals"] if "decimals" in kwargs else 1
        self.format_settings["length"] = kwargs["length"] if "length" in kwargs else 100
        self.format_settings["fill_character"] = kwargs["fill"] if "fill" in kwargs else "█"
        self.format_settings["print_end"] = kwargs["print_end"] if "print_end" in kwargs else "\r"
        self._update_display()

    @property
    def prefix(self):
        """Gets the prefix printed at the beginning the progress bar"""

        return self.format_settings["prefix"]

    @property
    def suffix(self):
        """Gets the suffix printed at the end of the progress bar"""

        return self.format_settings["suffix"]

    @property
    def decimals(self):
        """
        Gets the number of decimal points to use in printing the percentage complete
        in the progress bar
        """

        return self.format_settings["decimals"]

    @property
    def length(self):
        """Gets the total line length of the progress bar"""

        return self.format_settings["length"]

    @property
    def fill_character(self):
        """Gets the character to use to fill the progress of the progress bar"""

        return self.format_settings["fill_character"]

    @property
    def print_end(self):
        """Gets the character to be used to print at the end of a progress bar line update"""

        return self.format_settings["print_end"]

    def increment(self):
        """Increments the progress bar and displays the result"""

        self.current_value += self.increment_value
        self._update_display()

    def clear(self):
        """Clears the progress bar"""

        clear = " " * self.length
        print("\r{}".format(clear), end="\r")

    def _update_display(self):
        """Updates the display of the progress bar"""

        percent_value = 100 * (self.current_value / float(self.total_value))
        percent_format = "{{0:>{}.{}f}}".format(4 + self.decimals, self.decimals)
        percent = percent_format.format(percent_value)
        bar_length = self.length - len(self.prefix) - len(percent) - len(self.suffix) - 6
        filled_length = int(bar_length * self.current_value // self.total_value)
        bar_text = self.fill_character * filled_length + '-' * (bar_length - filled_length)
        print("\r{} |{}| {}% {}".format(self.prefix, bar_text, percent, self.suffix),
              end=self.print_end)
