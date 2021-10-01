"""Module for retrieving torrent file data"""

import logging
import platform
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep
from urllib.parse import urlparse, parse_qs
import requests


class TorrentDataProvider:

    """Provider to retrieve torrent data from rarbg.to"""

    def __init__(self):
        super().__init__()
        self.base_url = "https://torrentapi.org/pubapi_v2.php"
        self.token = None
        self.token_expiration = None
        self.last_request = None
        self.logger = logging.getLogger()

    @property
    def user_agent(self):
        """Gets the user agent string to be used with the torrent API"""

        return "{}/{} ({}) python {}".format(
            "infield-fly",
            "1.0",
            "; ".join(platform.uname()),
            platform.python_version())

    def get_token(self):
        """Gets the API token for use with the torrent API"""

        params = { "get_token": "get_token", "app_id": "infield-fly" }
        token_response = self.get_data(params)
        self.logger.info("Geting API token")
        if "error" in token_response:
            self.logger.warning("Error retrieving token")
        else:
            self.token = token_response["token"]
            self.token_expiration = datetime.now() + timedelta(minutes=10)
            self.logger.info("Token retrieved: %s", self.token)

    def get_data(self, params=None, throttle_delay_in_seconds=2.0):
        """Gets data from the torrent API, waiting between calls if necessary"""

        if self.last_request is not None:
            seconds_since_last_request = (datetime.now() - self.last_request).total_seconds()
            if seconds_since_last_request < throttle_delay_in_seconds:
                sleep(throttle_delay_in_seconds - seconds_since_last_request)

        headers = { "User-Agent": self.user_agent, "Accept": "application/json" }
        response = requests.get(self.base_url, params = params, headers = headers)
        self.last_request = datetime.now()

        if response.status_code != 200:
            return None

        return response.json()

    def search(self, search_string, retry_count=4, is_unattended_mode=False):
        """
        Searches for a string using the torrent API, retrying up to the specified
        number of times
        """

        if self.token is None:
            self.get_token()

        params = {
            "mode": "search",
            "search_string": search_string,
            "token": self.token,
            "format": "json_extended",
            "app_id": "infield-fly"
        }

        self.logger.info("Searching for '%s' (retry up to %s times)", search_string, retry_count)
        search_response = self.get_data(params)
        while "error" in search_response and retry_count > 0:
            if not is_unattended_mode:
                self.logger.info("No results received; waiting and trying again...")
            sleep(3)
            retry_count -= 1
            search_response = self.get_data(params, throttle_delay_in_seconds=5.0)

        if "error" in search_response:
            return []

        torrent_results = []
        for torrent_dict in search_response["torrent_results"]:
            tvdb_id = (int(torrent_dict["episode_info"]["tvdb"])
                       if "episode_info" in torrent_dict and "tvdb" in torrent_dict["episode_info"]
                       else None)

            magnet_link_query = (parse_qs(urlparse(torrent_dict["download"]).query)
                                 if "download" in torrent_dict
                                 else None)
            urn_list = ([x for x in magnet_link_query["xt"] if x.startswith("urn:btih:")]
                        if magnet_link_query is not None and "xt" in magnet_link_query
                        else [])
            torrent_hash = urn_list[0][9:] if len(urn_list) > 0 else None

            torrent_results.append(TorrentResult(
                torrent_dict["title"], torrent_dict["download"], torrent_hash, tvdb_id))

        return torrent_results


@dataclass
class TorrentResult:

    """Describes a torrent search result"""

    title: str
    magnet_link: str
    hash: str = None
    tvdb_id: int = None
