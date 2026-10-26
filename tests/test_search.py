"""Tests for the search module"""

from datetime import datetime, timedelta
from unittest import mock

import pytest
import requests

from infield_fly.search import REQUEST_TIMEOUT_SECONDS, TorrentDataProvider, TorrentResult

MAGNET = "magnet:?xt=urn:btih:ABCDEF&dn=Show.S01E01.1080p"


def make_response(status_code=200, json_value=None):
    """Creates a mock HTTP response"""

    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = json_value
    return response


@pytest.fixture
def provider():
    return TorrentDataProvider()


@pytest.fixture
def mock_get():
    with mock.patch("infield_fly.search.requests.get") as get:
        yield get


@pytest.fixture
def mock_sleep():
    with mock.patch("infield_fly.search.sleep") as sleep:
        yield sleep


class TestUserAgent:

    def test_contains_platform_and_python_version(self, provider):
        with mock.patch("infield_fly.search.platform.uname", return_value=("Darwin", "host")), \
                mock.patch("infield_fly.search.platform.python_version", return_value="3.9.6"):
            assert provider.user_agent == "infield-fly/1.0 (Darwin; host) python 3.9.6"


class TestGetToken:

    def test_stores_token_on_success(self, provider):
        with mock.patch.object(provider, "get_data", return_value={"token": "tok"}) as get_data:
            provider.get_token()

        get_data.assert_called_once_with({"get_token": "get_token", "app_id": "infield-fly"})
        assert provider.token == "tok"
        assert provider.token_expiration > datetime.now()

    @pytest.mark.parametrize("response", [None, {"error": "bad"}])
    def test_leaves_token_unset_on_failure(self, provider, caplog, response):
        with mock.patch.object(provider, "get_data", return_value=response):
            provider.get_token()

        assert provider.token is None
        assert "Error retrieving token" in caplog.text


class TestGetData:

    def test_returns_json_on_success(self, provider, mock_get, mock_sleep):
        mock_get.return_value = make_response(200, {"value": 1})

        result = provider.get_data({"a": "b"})

        assert result == {"value": 1}
        assert provider.last_request is not None
        mock_sleep.assert_not_called()
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"a": "b"}
        assert kwargs["timeout"] == REQUEST_TIMEOUT_SECONDS
        assert kwargs["headers"]["Accept"] == "application/json"

    def test_throttles_recent_requests(self, provider, mock_get, mock_sleep):
        mock_get.return_value = make_response(200, {})
        provider.last_request = datetime.now()

        provider.get_data(throttle_delay_in_seconds=60.0)

        mock_sleep.assert_called_once()
        assert 0 < mock_sleep.call_args[0][0] <= 60.0

    def test_does_not_throttle_old_requests(self, provider, mock_get, mock_sleep):
        mock_get.return_value = make_response(200, {})
        provider.last_request = datetime.now() - timedelta(minutes=1)

        provider.get_data()

        mock_sleep.assert_not_called()

    def test_request_exception_returns_error(self, provider, mock_get):
        mock_get.side_effect = requests.ConnectionError("boom")

        result = provider.get_data()

        assert result == {"error": "Unknown error: boom"}
        assert provider.last_request is None

    def test_cloudflare_response_returns_error(self, provider, mock_get):
        mock_get.return_value = make_response(520)

        assert provider.get_data() == {"error": "Cloudflare error"}

    def test_unexpected_status_returns_none(self, provider, mock_get):
        mock_get.return_value = make_response(500)

        assert provider.get_data() is None


class TestSearch:

    def test_gets_token_when_missing(self, provider):
        def set_token():
            provider.token = "tok"

        with mock.patch.object(provider, "get_token", side_effect=set_token) as get_token, \
                mock.patch.object(provider, "get_data", return_value={"torrent_results": []}) \
                as get_data:
            assert provider.search("show") == []

        get_token.assert_called_once()
        assert get_data.call_args[0][0]["token"] == "tok"
        assert get_data.call_args[0][0]["search_string"] == "show"

    def test_skips_token_when_present(self, provider):
        provider.token = "tok"
        with mock.patch.object(provider, "get_token") as get_token, \
                mock.patch.object(provider, "get_data", return_value={"torrent_results": []}):
            provider.search("show")

        get_token.assert_not_called()

    def test_parses_results(self, provider):
        provider.token = "tok"
        response = {"torrent_results": [
            {"download": MAGNET, "title": "With TVDB", "episode_info": {"tvdb": "123"}},
            {"download": MAGNET, "title": "Episode info without TVDB", "episode_info": {}},
            {"download": MAGNET, "title": "No episode info"}
        ]}
        with mock.patch.object(provider, "get_data", return_value=response):
            results = provider.search("show")

        assert results == [
            TorrentResult("With TVDB", MAGNET, "ABCDEF", 123),
            TorrentResult("Episode info without TVDB", MAGNET, "ABCDEF", None),
            TorrentResult("No episode info", MAGNET, "ABCDEF", None)
        ]

    @pytest.mark.parametrize("is_unattended_mode", [False, True])
    def test_retries_on_error_then_succeeds(self, provider, mock_sleep, caplog,
                                            is_unattended_mode):
        caplog.set_level("INFO")
        provider.token = "tok"
        responses = [{"error": "busy"}, {"torrent_results": []}]
        with mock.patch.object(provider, "get_data", side_effect=responses) as get_data:
            results = provider.search("show", is_unattended_mode=is_unattended_mode)

        assert results == []
        assert get_data.call_count == 2
        assert get_data.call_args.kwargs == {"throttle_delay_in_seconds": 5.0}
        mock_sleep.assert_called_once_with(3)
        assert ("waiting and trying again" in caplog.text) != is_unattended_mode

    def test_returns_empty_after_exhausting_retries(self, provider, mock_sleep):
        provider.token = "tok"
        with mock.patch.object(provider, "get_data", return_value={"error": "busy"}) as get_data:
            results = provider.search("show", retry_count=2)

        assert results == []
        assert get_data.call_count == 3
        assert mock_sleep.call_count == 2

    def test_returns_empty_on_none_response(self, provider, mock_sleep):
        provider.token = "tok"
        with mock.patch.object(provider, "get_data", return_value=None) as get_data:
            assert provider.search("show") == []

        get_data.assert_called_once()
        mock_sleep.assert_not_called()


class TestCreateTorrentResult:

    def test_uses_explicit_title_and_tvdb_id(self, provider):
        result = provider.create_torrent_result(MAGNET, "Title", 5)

        assert result == TorrentResult("Title", MAGNET, "ABCDEF", 5)

    def test_uses_display_name_when_no_title(self, provider):
        result = provider.create_torrent_result(MAGNET)

        assert result.title == "Show.S01E01.1080p"

    def test_empty_title_when_no_title_or_display_name(self, provider):
        result = provider.create_torrent_result("magnet:?xt=urn:btih:ABCDEF")

        assert result.title == ""
        assert result.hash == "ABCDEF"

    def test_no_hash_without_btih_urn(self, provider):
        result = provider.create_torrent_result("magnet:?xt=urn:sha1:XYZ&dn=name")

        assert result.hash is None

    def test_no_hash_without_exact_topic(self, provider):
        result = provider.create_torrent_result("magnet:?dn=name")

        assert result.hash is None
        assert result.title == "name"

    def test_empty_magnet_with_title(self, provider):
        result = provider.create_torrent_result("", "Title")

        assert result == TorrentResult("Title", "", None, None)

    @pytest.mark.xfail(raises=TypeError, strict=True,
                       reason="looks up the display name in the parsed query, which is None")
    def test_empty_magnet_without_title(self, provider):
        result = provider.create_torrent_result("")

        assert result.title == ""
