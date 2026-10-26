"""Tests for the usenet.newznab module"""

from datetime import datetime, timezone
from unittest import mock
from xml.etree import ElementTree

import pytest
import requests

from infield_fly.configuration import SearchSettings
from infield_fly.usenet.newznab import NewznabSearcher, NzbResult

API_URL = "https://indexer/api"


def make_item(title, guid, pubdate="Mon, 06 Jan 2020 10:00:00 +0000", grabs=None,
              password=None, size=None):
    attributes = "".join(f'<newznab:attr name="{name}" value="{value}"/>'
                         for name, value in (("grabs", grabs), ("password", password),
                                             ("size", size))
                         if value is not None)
    return (f"<item><title>{title}</title>"
            f"<guid isPermaLink=\"true\">https://indexer/details/{guid}</guid>"
            f"<link>https://indexer/getnzb/{guid}.nzb</link>"
            f"<pubDate>{pubdate}</pubDate>"
            f"<enclosure url=\"https://indexer/getnzb/{guid}.nzb\" length=\"500\" "
            f"type=\"application/x-nzb\"/>{attributes}</item>")


def make_feed(*items):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">'
            f'<channel>{"".join(items)}</channel></rss>').encode()


def make_response(status_code=200, content=b""):
    response = mock.Mock(status_code=status_code, content=content)
    response.text = content.decode()
    return response


class TestNzbResult:

    @staticmethod
    def parse(item):
        return NzbResult.from_item(ElementTree.fromstring(make_feed(item)).find(".//item"))

    def test_from_item(self):
        result = self.parse(make_item("Show.S01E02.1080p", "abc", grabs=12, password=1,
                                      size=1234))

        assert result == NzbResult(
            "Show.S01E02.1080p", "abc", "https://indexer/getnzb/abc.nzb", 1234,
            datetime(2020, 1, 6, 10, tzinfo=timezone.utc), 12, True)

    def test_from_item_defaults(self):
        result = self.parse("<item><title>t</title><guid>https://indexer/details/xyz/</guid>"
                            "<pubDate>bogus</pubDate></item>")

        assert result == NzbResult("t", "xyz")

    def test_size_from_enclosure(self):
        assert self.parse(make_item("t", "abc")).size == 500

    @pytest.mark.parametrize("title, release_groups, expected", [
        ("Abbott.Elementary.S06E03.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX", ["flux", "ntb"], 0),
        ("Abbott.Elementary.S06E03.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb", ["flux", "ntb"], 1),
        ("Abbott Elementary S06E03 1080p AMZN WEB-DL NTb [FLUX repack]", ["ntb", "flux"], 0),
        ("Abbott.Elementary.S06E03.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUXX", ["flux"], None),
        ("Abbott.Elementary.S06E03.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP", ["flux"], None),
        ("Abbott.Elementary.S06E03.1080p.AMZN.WEB-DL-Two.Words", ["two words"], 0),
        ("Abbott.Elementary.S06E03.1080p.AMZN.WEB-DL-Two.Other.Words", ["two words"], None),
    ])
    def test_release_group_rank(self, title, release_groups, expected):
        assert NzbResult(title, "guid").get_release_group_rank(release_groups) == expected


class TestNewznabSearcher:

    @pytest.fixture
    def searcher(self):
        return NewznabSearcher(SearchSettings({"newznab_url": API_URL, "api_key": "key"}))

    @pytest.fixture
    def requests_get(self):
        with mock.patch("infield_fly.usenet.newznab.requests.get") as get:
            yield get

    def test_text_search_ranks_results_and_skips_excluded(self, searcher, requests_get):
        requests_get.return_value = make_response(content=make_feed(
            make_item("The.Show.S01E02.1080p.A", "older", "Sun, 05 Jan 2020 10:00:00 +0000"),
            make_item("The.Show.S01E02.1080p.B", "few", grabs=1),
            make_item("The.Show.S01E02.1080p.C", "many", grabs=5),
            make_item("The.Show.S01E02.1080p.D", "excluded", grabs=9),
            make_item("The.Show.S01E02.WEBDL", "unfiltered")))

        results = searcher.search("the show 1080p s01e02", ["excluded"])

        assert [x.guid for x in results] == ["many", "few", "unfiltered", "older"]
        requests_get.assert_called_once_with(API_URL, params={
            "apikey": "key", "extended": 1, "limit": 100, "t": "search",
            "q": "the show 1080p s01e02"}, timeout=30)

    def test_search_ranks_by_release_group_then_newest(self, searcher, requests_get):
        requests_get.return_value = make_response(content=make_feed(
            make_item("Show.S01E02.1080p-NTB", "ntb-newer", "Tue, 07 Jan 2020 10:00:00 +0000"),
            make_item("Show.S01E02.1080p-FLUX", "flux-older",
                      "Sun, 05 Jan 2020 10:00:00 +0000"),
            make_item("Show.S01E02.1080p-FLUX", "flux-newer"),
            make_item("Show.S01E02.1080p-FLUX", "flux-excluded"),
            make_item("Show.S01E02.1080p-OTHER", "unapproved",
                      "Wed, 08 Jan 2020 10:00:00 +0000")))

        results = searcher.search("show 1080p s01e02", ["flux-excluded"], ["flux", "ntb"])

        assert [x.guid for x in results] == ["flux-newer", "flux-older", "ntb-newer"]
        requests_get.assert_called_once()

    def test_search_logs_when_no_result_is_from_release_groups(self, searcher, requests_get,
                                                                caplog):
        caplog.set_level("INFO")
        requests_get.return_value = make_response(content=make_feed(
            make_item("Show.S01E02.1080p-OTHER", "unapproved")))

        assert searcher.search("show s01e02", release_groups=["flux", "ntb"]) == []
        assert "No search results for 'show s01e02' are from release groups flux, ntb" in (
            caplog.text)

    def test_search_without_results(self, searcher, requests_get):
        requests_get.return_value = make_response(content=make_feed())

        assert searcher.search("the show s01e02") == []

    @pytest.mark.parametrize("response, message", [
        (requests.ConnectionError("down"), "Search request failed: down"),
        (make_response(500, b"oops"), "Received error response to search (500): oops"),
        (make_response(content=b"<rss"), "Could not parse search response"),
        (make_response(content=b'<error code="100" description="Incorrect user credentials"/>'),
         "Search returned error 100: Incorrect user credentials"),
    ])
    def test_failed_search_returns_no_results(self, searcher, requests_get, caplog, response,
                                              message):
        requests_get.side_effect = [response]

        assert searcher.get_results({"t": "search", "q": "q"}) == []
        assert message in caplog.text

    def test_fetch_nzb_from_link(self, searcher, requests_get):
        requests_get.return_value = make_response(content=b"<nzb/>")

        assert searcher.fetch_nzb(NzbResult("t", "abc", "https://indexer/abc.nzb")) == b"<nzb/>"
        requests_get.assert_called_once_with("https://indexer/abc.nzb", params=None, timeout=30)

    def test_fetch_nzb_by_guid(self, searcher, requests_get):
        requests_get.return_value = make_response(content=b"<nzb/>")

        searcher.fetch_nzb(NzbResult("t", "abc"))

        requests_get.assert_called_once_with(
            API_URL, params={"t": "get", "id": "abc", "apikey": "key"}, timeout=30)

    @pytest.mark.parametrize("response", [
        requests.ConnectionError("down"), make_response(404, b"missing"), make_response()])
    def test_fetch_nzb_failure(self, searcher, requests_get, caplog, response):
        requests_get.side_effect = [response]

        assert searcher.fetch_nzb(NzbResult("Title", "abc")) is None
        assert "Downloading NZB for 'Title' failed" in caplog.text
