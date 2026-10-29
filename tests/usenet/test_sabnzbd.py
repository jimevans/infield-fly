"""Tests for the usenet.sabnzbd module"""

from unittest import mock

import pytest
import requests

from infield_fly.configuration import DownloadSettings
from infield_fly.usenet.sabnzbd import SabnzbdClient, SabnzbdError

API_URL = "http://sabnzbd:8080/api"


def make_response(json_value=None, status_code=200, text=""):
    response = mock.Mock(status_code=status_code, text=text)
    if isinstance(json_value, Exception):
        response.json.side_effect = json_value
    else:
        response.json.return_value = json_value
    return response


@pytest.fixture
def client():
    return SabnzbdClient(DownloadSettings({"sabnzbd_host": "sabnzbd",
                                           "sabnzbd_port": 8080,
                                           "sabnzbd_api_key": "key",
                                           "sabnzbd_category": "tv"}))


@pytest.fixture
def requests_get():
    with mock.patch("infield_fly.usenet.sabnzbd.requests.get") as get:
        yield get


class TestAddNzb:

    @pytest.fixture
    def nzb_file(self, tmp_path):
        path = tmp_path / "job.nzb"
        path.write_bytes(b"<nzb/>")
        return str(path)

    def test_adds_file(self, client, nzb_file):
        with mock.patch("infield_fly.usenet.sabnzbd.requests.post",
                        return_value=make_response({"status": True, "nzo_ids": ["nzo1"]})
                        ) as post:
            assert client.add_nzb(nzb_file, "Show.S01E02") == "nzo1"

        assert post.call_args[0] == (API_URL,)
        assert post.call_args[1]["params"] == {
            "apikey": "key", "output": "json", "mode": "addfile", "nzbname": "Show.S01E02",
            "cat": "tv"}
        assert post.call_args[1]["files"]["name"][0] == "job.nzb"

    def test_omits_category_when_not_configured(self, nzb_file):
        client = SabnzbdClient(DownloadSettings({"sabnzbd_api_key": "key"}))
        with mock.patch("infield_fly.usenet.sabnzbd.requests.post",
                        return_value=make_response({"status": True, "nzo_ids": ["nzo1"]})
                        ) as post:
            client.add_nzb(nzb_file, "name")

        assert post.call_args[0] == ("http://localhost:8080/api",)
        assert "cat" not in post.call_args[1]["params"]

    @pytest.mark.parametrize("response", [{"status": False}, {"status": True, "nzo_ids": []}])
    def test_not_added(self, client, nzb_file, response):
        with mock.patch("infield_fly.usenet.sabnzbd.requests.post",
                        return_value=make_response(response)), \
                pytest.raises(SabnzbdError, match="NZB file was not added"):
            client.add_nzb(nzb_file, "name")


class TestGetDownloadStatus:

    def test_finished_download_from_history(self, client, requests_get):
        requests_get.return_value = make_response({"history": {"slots": [
            {"nzo_id": "other", "status": "Completed"},
            {"nzo_id": "nzo1", "status": "Failed", "storage": "/dl/Show",
             "fail_message": "Repair failed"}]}})

        assert client.get_download_status("nzo1") == ("Failed", "/dl/Show", "Repair failed")
        requests_get.assert_called_once_with(API_URL, params={
            "apikey": "key", "output": "json", "mode": "history", "nzo_ids": "nzo1"},
            timeout=30)

    def test_queued_download(self, client, requests_get):
        requests_get.side_effect = [
            make_response({"history": {"slots": []}}),
            make_response({"queue": {"slots": [{"nzo_id": "other", "status": "Queued"},
                                               {"nzo_id": "nzo1", "status": "Downloading"}]}})]

        assert client.get_download_status("nzo1") == ("Downloading", None, None)
        assert requests_get.call_args[1]["params"]["mode"] == "queue"

    def test_unknown_download(self, client, requests_get):
        requests_get.return_value = make_response({})

        assert client.get_download_status("nzo1") == (None, None, None)


class TestCallApi:

    def test_delete_history_item(self, client, requests_get):
        requests_get.return_value = make_response({"status": True})

        client.delete_history_item("nzo1")

        assert requests_get.call_args[1]["params"] == {
            "apikey": "key", "output": "json", "mode": "history", "name": "delete",
            "value": "nzo1", "del_files": 1}

    def test_uses_https_when_ssl_configured(self, requests_get):
        client = SabnzbdClient(DownloadSettings({"sabnzbd_host": "sabnzbd", "sabnzbd_port": 9090,
                                                 "sabnzbd_use_ssl": True}))
        requests_get.return_value = make_response({"status": True})

        client.delete_history_item("nzo1")

        assert requests_get.call_args[0] == ("https://sabnzbd:9090/api",)

    @pytest.mark.parametrize("response, message", [
        (requests.ConnectionError("down"), "Request to SABnzbd failed: down"),
        (make_response(status_code=403, text="denied"),
         r"Received error response from SABnzbd \(403\): denied"),
        (make_response(ValueError("bad"), text="html"), "Could not parse SABnzbd response: html"),
        (make_response({"status": False, "error": "API Key Incorrect"}),
         "SABnzbd returned error: API Key Incorrect"),
    ])
    def test_errors(self, client, requests_get, response, message):
        requests_get.side_effect = [response]

        with pytest.raises(SabnzbdError, match=message):
            client.call_api({"mode": "queue"})
