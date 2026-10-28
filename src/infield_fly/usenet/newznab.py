"""Module for searching a Newznab indexer for NZB files for jobs"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests

REQUEST_TIMEOUT_SECONDS = 30
NEWZNAB_NAMESPACE = "http://www.newznab.com/DTD/2010/feeds/attributes/"


@dataclass
class NzbResult:

    """Describes an NZB found for a job on a Newznab indexer"""

    title: str
    guid: str
    link: str = None
    size: int = 0
    pubdate: datetime = None
    grabs: int = 0
    is_passworded: bool = False

    @classmethod
    def from_item(cls, item):
        """Creates an NZB result from an item element of a Newznab search response"""

        attributes = {x.get("name"): x.get("value")
                      for x in item.findall(f"{{{NEWZNAB_NAMESPACE}}}attr")}
        guid = attributes.get("guid") or item.findtext("guid", "").rstrip("/").split("/")[-1]
        enclosure = item.find("enclosure")
        size = attributes.get("size") or (enclosure.get("length") if enclosure is not None
                                          else None)
        pubdate_text = item.findtext("pubDate")
        try:
            pubdate = parsedate_to_datetime(pubdate_text) if pubdate_text else None
        except (TypeError, ValueError):
            pubdate = None

        return cls(title=item.findtext("title", ""),
                   guid=guid,
                   link=item.findtext("link"),
                   size=_to_int(size),
                   pubdate=pubdate,
                   grabs=_to_int(attributes.get("grabs")),
                   is_passworded=attributes.get("password", "0") != "0")

    def get_release_group_rank(self, release_groups):
        """
        Gets the position in a list of release groups of the first group whose name appears
        as one or more whole words in the title of this result, ignoring case and
        punctuation, or None if no group in the list appears in the title
        """

        title_words = _words(self.title)
        for rank, release_group in enumerate(release_groups):
            group_words = _words(release_group)
            if any(title_words[index:index + len(group_words)] == group_words
                   for index in range(len(title_words) - len(group_words) + 1)):
                return rank
        return None

    @property
    def rank_key(self):
        """Gets the key by which to rank results, with newer and more grabbed results higher"""

        return (self.pubdate.timestamp() if self.pubdate is not None else 0, self.grabs)


class NewznabSearcher:

    """Searches a Newznab indexer, such as NZBPlanet, for NZB files"""

    def __init__(self, search_settings):
        super().__init__()
        self.settings = search_settings
        self.logger = logging.getLogger()

    def search(self, query, excluded_guids=(), release_groups=()):
        """
        Searches the indexer's text search for a query, returning the results that are not
        excluded, ranked best first. When release groups are listed, only results from those
        groups are returned, ranked by the order of their group in the list, then newest
        first. The query must be specific enough that the indexer returns only posts of the
        episode being searched for. When no results are returned, the reason is logged.
        """

        results = self.get_results({"t": "search", "q": query})
        if results is None:
            return []
        if len(results) == 0:
            self.logger.info("No search results found for '%s'", query)
            return []

        results.sort(key=lambda x: x.rank_key, reverse=True)
        if len(release_groups) > 0:
            ranked_results = [(x.get_release_group_rank(release_groups), x) for x in results]
            # The sort is stable, so results from the same group stay newest first.
            results = [x for _, x in sorted([(rank, x) for rank, x in ranked_results
                                             if rank is not None], key=lambda x: x[0])]
            if len(results) == 0:
                self.logger.info("No search results for '%s' are from release groups %s",
                                 query, ", ".join(release_groups))
                return []

        candidates = [x for x in results if x.guid not in excluded_guids]
        if len(candidates) == 0:
            self.logger.info("All %d search results for '%s' were rejected previously",
                             len(results), query)
        return candidates

    def get_results(self, params):
        """Gets the results of a Newznab API search, or None if the search failed"""

        request_params = {"apikey": self.settings.api_key, "extended": 1, "limit": 100,
                          **params}
        self.logger.debug("Searching %s with %s", self.settings.newznab_url, params)
        try:
            response = requests.get(self.settings.newznab_url, params=request_params,
                                    timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as ex:
            self.logger.warning("Search request failed: %s", ex)
            return None

        if response.status_code != 200:
            self.logger.warning("Received error response to search (%s): %s",
                                response.status_code, response.text)
            return None

        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as ex:
            self.logger.warning("Could not parse search response: %s", ex)
            return None

        if root.tag == "error":
            self.logger.warning("Search returned error %s: %s",
                                root.get("code"), root.get("description"))
            return None

        return [NzbResult.from_item(x) for x in root.iter("item")]

    def fetch_nzb(self, result):
        """Downloads the NZB file for a search result, returning its contents or None"""

        if result.link:
            url, params = result.link, None
        else:
            url, params = self.settings.newznab_url, {
                "t": "get", "id": result.guid, "apikey": self.settings.api_key}
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as ex:
            self.logger.warning("Downloading NZB for '%s' failed: %s", result.title, ex)
            return None

        if response.status_code != 200 or len(response.content) == 0:
            self.logger.warning("Downloading NZB for '%s' failed (%s)",
                                result.title, response.status_code)
            return None

        return response.content


def _words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
