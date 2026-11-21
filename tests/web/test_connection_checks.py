"""Tests for the connection checks of the web interface"""

import logging
import threading
from unittest import mock

import pytest
import requests

from infield_fly.configuration import Configuration
from infield_fly.usenet.sabnzbd import SabnzbdError
from infield_fly.web.connection_checks import (captured_warnings, check_newznab,
                                               check_sabnzbd, check_tvdb)


def make_config(**sections):
    return Configuration("/settings", sections)


class TestCapturedWarnings:

    def test_collects_warnings_of_this_thread_only(self):
        def warn_in_other_thread():
            logging.getLogger().warning("other thread")

        with captured_warnings() as warnings:
            logging.getLogger().info("not a warning")
            logging.getLogger().warning("first %s", "warning")
            other = threading.Thread(target=warn_in_other_thread)
            other.start()
            other.join()
            logging.getLogger().error("an error")
        logging.getLogger().warning("after")

        assert warnings == ["first warning", "an error"]


class TestCheckSabnzbd:

    @pytest.fixture
    def call_api(self):
        with mock.patch("infield_fly.usenet.sabnzbd.SabnzbdClient.call_api") as call_api:
            yield call_api

    @staticmethod
    def respond(call_api, version="4.3.2", categories=("*", "tv")):
        call_api.side_effect = lambda params: (
            {"queue": {"version": version}} if params["mode"] == "queue"
            else {"categories": list(categories)})

    def test_connected(self, call_api):
        self.respond(call_api)

        assert check_sabnzbd(make_config(download={"sabnzbd_category": "tv"})) == (
            True, "Connected to SABnzbd 4.3.2.")
        assert [x.args[0]["mode"] for x in call_api.call_args_list] == ["queue", "get_cats"]

    def test_connected_without_version(self, call_api):
        self.respond(call_api, version=None)

        assert check_sabnzbd(make_config()) == (True, "Connected to SABnzbd.")

    def test_missing_category(self, call_api):
        self.respond(call_api, categories=("*", "movies"))

        assert check_sabnzbd(make_config(download={"sabnzbd_category": "tv"})) == (
            False, "Connected to SABnzbd 4.3.2. But it has no category 'tv'; its categories "
            "are *, movies.")

    def test_no_categories(self, call_api):
        self.respond(call_api, categories=())

        assert check_sabnzbd(make_config(download={"sabnzbd_category": "tv"}))[1].endswith(
            "its categories are none.")

    def test_failure(self, call_api):
        call_api.side_effect = SabnzbdError("SABnzbd returned error: API Key Incorrect")

        assert check_sabnzbd(make_config()) == (
            False, "SABnzbd returned error: API Key Incorrect")


class TestCheckNewznab:

    def test_requires_api_key(self):
        assert check_newznab(make_config()) == (False, "Enter the indexer's API key first.")

    def test_answered(self):
        with mock.patch("infield_fly.usenet.newznab.NewznabSearcher.get_results",
                        return_value=[]) as get_results:
            assert check_newznab(make_config(search={"api_key": "key"})) == (
                True, "The indexer answered a test search.")

        get_results.assert_called_once_with({"t": "search", "q": "test", "limit": 1})

    def test_failure_reports_logged_reason(self):
        response = mock.Mock(
            status_code=200,
            content=b'<error code="100" description="Incorrect user credentials"/>')
        with mock.patch("requests.get", return_value=response):
            ok, message = check_newznab(make_config(search={"api_key": "key"}))

        assert not ok
        assert message == "Searching failed. Search returned error 100: Incorrect user credentials"


class TestCheckTvdb:

    def test_requires_api_key(self):
        assert check_tvdb(make_config()) == (False, "Enter the TheTVDB API key first.")

    @pytest.mark.parametrize("token, expected", [
        ("token", (True, "Logged in to TheTVDB.")),
        (None, (False, "TheTVDB refused the API key and PIN.")),
    ])
    def test_login(self, token, expected):
        def authenticate(provider):
            provider.token = token

        with mock.patch("infield_fly.database.TVMetadataProvider.authenticate",
                        autospec=True, side_effect=authenticate):
            assert check_tvdb(make_config(metadata={"api_key": "key"})) == expected

    def test_unreachable(self):
        with mock.patch("infield_fly.database.TVMetadataProvider.authenticate",
                        side_effect=requests.ConnectionError("no route")):
            assert check_tvdb(make_config(metadata={"api_key": "key"})) == (
                False, "Could not reach TheTVDB: no route")
