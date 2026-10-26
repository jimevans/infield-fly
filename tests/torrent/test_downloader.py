"""Tests for the torrent.downloader module"""

from unittest import mock

import pytest

from conftest import load_job, save_job
from infield_fly.job_status import JobStatus
from infield_fly.torrent.downloader import TorrentDownloader

MAGNET = "magnet:?xt=urn:btih:ABCDEF&dn=Show.S01E02"


@pytest.fixture
def config(make_config):
    return make_config(conversion={"deluge_host": "deluge", "deluge_port": 1,
                                   "deluge_user_name": "user", "deluge_password": "pass"})


@pytest.fixture
def downloader(config):
    return TorrentDownloader(config)


@pytest.fixture
def deluge_client():
    with mock.patch("infield_fly.torrent.downloader.DelugeRPCClient") as client_class:
        yield client_class.return_value.__enter__.return_value


class TestSetDownload:

    @pytest.mark.parametrize("location, expected", [
        (MAGNET, True), ("/downloads/Show.nzb", False), ("https://indexer/x.nzb", False)])
    def test_recognizes_magnet_links(self, downloader, location, expected):
        assert downloader.recognizes_download(location) == expected

    def test_sets_magnet_link(self, downloader, config):
        job = save_job(config, JobStatus.WAITING)

        assert downloader.set_download(job, MAGNET) == []

        job = load_job(config, job.job_id)
        assert (job.status, job.downloader, job.magnet_link, job.title, job.torrent_hash) == (
            JobStatus.ADDING, "deluge", MAGNET, "Show.S01E02", "ABCDEF")


class TestAddDownloads:

    def test_no_jobs(self, downloader, deluge_client, caplog):
        caplog.set_level("INFO")

        downloader.add_downloads([])

        assert "No torrents to add" in caplog.text
        deluge_client.core.prefetch_magnet_metadata.assert_not_called()

    def test_adds_torrents(self, downloader, config, deluge_client):
        named = save_job(config, JobStatus.ADDING, magnet_link="magnet:1")
        unnamed = save_job(config, JobStatus.ADDING, magnet_link="magnet:2")
        metadata = {named.magnet_link: (b"id1", {b"name": b"Metadata name"}),
                    unnamed.magnet_link: (b"id2", {})}
        deluge_client.core.prefetch_magnet_metadata.side_effect = lambda link: metadata[link]
        deluge_client.core.get_torrent_status.return_value = {
            b"name": b"Torrent name", b"download_location": b"/downloads"}

        downloader.add_downloads([named, unnamed])

        named = load_job(config, named.job_id)
        assert (named.status, named.torrent_hash, named.name, named.download_directory) == (
            JobStatus.DOWNLOADING, "id1", "Metadata name", "/downloads")
        assert load_job(config, unnamed.job_id).name == "Torrent name"
        deluge_client.core.add_torrent_magnet.assert_any_call("magnet:1", {})
        deluge_client.core.get_torrent_status.assert_any_call(
            "id1", ["name", "download_location", "is_finished"])

    def test_skips_job_when_prefetch_returns_no_id(self, downloader, config, deluge_client,
                                                   caplog):
        failed = save_job(config, JobStatus.ADDING, magnet_link="magnet:1")
        added = save_job(config, JobStatus.ADDING, magnet_link="magnet:2")
        metadata = {failed.magnet_link: (None, None),
                    added.magnet_link: (b"id2", {b"name": b"Metadata name"})}
        deluge_client.core.prefetch_magnet_metadata.side_effect = lambda link: metadata[link]
        deluge_client.core.get_torrent_status.return_value = {
            b"name": b"Torrent name", b"download_location": b"/downloads"}

        downloader.add_downloads([failed, added])

        assert f"failed for job {failed.job_id}, no ID returned" in caplog.text
        deluge_client.core.add_torrent_magnet.assert_called_once_with("magnet:2", {})
        assert load_job(config, failed.job_id).status == JobStatus.ADDING
        assert load_job(config, added.job_id).status == JobStatus.DOWNLOADING

    def test_uses_torrent_name_when_prefetch_returns_no_metadata(self, downloader, config,
                                                                 deluge_client, caplog):
        job = save_job(config, JobStatus.ADDING, magnet_link="magnet:1")
        deluge_client.core.prefetch_magnet_metadata.return_value = (b"id1", None)
        deluge_client.core.get_torrent_status.return_value = {
            b"name": b"Torrent name", b"download_location": b"/downloads"}

        downloader.add_downloads([job])

        job = load_job(config, job.job_id)
        assert (job.status, job.torrent_hash, job.name) == (
            JobStatus.DOWNLOADING, "id1", "Torrent name")
        assert "returned no metadata" in caplog.text


class TestQueryDownloads:

    def test_no_jobs(self, downloader, deluge_client, caplog):
        caplog.set_level("INFO")

        downloader.query_downloads([])

        assert "No downloading torrents" in caplog.text
        deluge_client.core.get_torrent_status.assert_not_called()

    def test_marks_finished_downloads_pending(self, downloader, config, deluge_client):
        finished = save_job(config, JobStatus.DOWNLOADING, torrent_hash="done",
                            download_directory="/downloads")
        unfinished = save_job(config, JobStatus.DOWNLOADING, torrent_hash="busy")
        statuses = {"done": {b"name": b"Done name", b"is_finished": True},
                    "busy": {b"name": b"Busy name"}}
        deluge_client.core.get_torrent_status.side_effect = lambda torrent_hash, _: \
            statuses[torrent_hash]

        downloader.query_downloads([finished, unfinished])

        finished = load_job(config, finished.job_id)
        assert (finished.status, finished.name, finished.download_directory) == (
            JobStatus.PENDING, "Done name", "/downloads")
        assert load_job(config, unfinished.job_id).status == JobStatus.DOWNLOADING

    def test_connects_with_configured_settings(self, downloader):
        with mock.patch("infield_fly.torrent.downloader.DelugeRPCClient") as client_class:
            downloader.connect()

        client_class.assert_called_once_with("deluge", 1, "user", "pass")
