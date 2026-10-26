"""Tests for the usenet.downloader module"""

import os
from unittest import mock

import pytest

from conftest import load_job, save_job
from infield_fly.job_status import JobStatus
from infield_fly.usenet.downloader import NzbDownloader
from infield_fly.usenet.newznab import NzbResult
from infield_fly.usenet.sabnzbd import SabnzbdError

VALID_NZB = (b'<nzb><file subject=\'"Show.S01E02.mkv" yEnc (1/1)\'><segments>'
             b'<segment bytes="10" number="1">a@b</segment></segments></file></nzb>')
INVALID_NZB = b"<nzb/>"


@pytest.fixture
def config(make_config):
    return make_config(
        search={"api_key": "key", "max_candidates": 2},
        download={"sabnzbd_url": "http://sabnzbd:8080", "sabnzbd_api_key": "key"})


@pytest.fixture
def downloader(config):
    return NzbDownloader(config)


@pytest.fixture
def sabnzbd_client():
    with mock.patch("infield_fly.usenet.downloader.SabnzbdClient") as client_class:
        yield client_class.return_value


class TestSearchDownloads:

    @pytest.fixture
    def searcher(self):
        with mock.patch("infield_fly.usenet.downloader.NewznabSearcher") as searcher_class:
            yield searcher_class.return_value

    def test_disabled_without_api_key(self, make_config, searcher, caplog):
        caplog.set_level("INFO")
        config = make_config()
        job = save_job(config, JobStatus.WAITING, query="q")

        NzbDownloader(config).search_downloads([job])

        assert "No Newznab API key configured" in caplog.text
        searcher.search.assert_not_called()
        assert load_job(config, job.job_id).status == JobStatus.WAITING

    def test_sets_first_valid_result(self, downloader, config, searcher, caplog):
        job = save_job(config, JobStatus.SEARCHING, keyword="the show",
                       query="the show 1080p s01e02", rejected=["old"])
        searcher.search.return_value = [NzbResult("Bad", "bad", size=10),
                                        NzbResult("Good", "good", size=10),
                                        NzbResult("Unused", "unused")]
        searcher.fetch_nzb.side_effect = lambda result: {"bad": INVALID_NZB,
                                                         "good": VALID_NZB}[result.guid]

        downloader.search_downloads([job])

        searcher.search.assert_called_once_with("the show 1080p s01e02", ["old"], [])
        job = load_job(config, job.job_id)
        nzb_file = os.path.join(config.conversion.nzb_directory, f"{job.job_id}.nzb")
        assert (job.status, job.downloader, job.title, job.nzb_guid, job.nzb_file,
                job.rejected) == (JobStatus.ADDING, "sabnzbd", "Good", "good", nzb_file,
                                  ["old", "bad"])
        with open(nzb_file, "rb") as saved:
            assert saved.read() == VALID_NZB
        assert "Rejecting search result 'Bad'" in caplog.text
        assert "NZB file for 'Good': NZB file lists no par2 files" in caplog.text

    def test_no_valid_result_returns_job_to_waiting(self, downloader, config, searcher):
        job = save_job(config, JobStatus.WAITING, keyword="unknown", query="q s01e02")
        searcher.search.return_value = [NzbResult("Unavailable", "unavailable"),
                                        NzbResult("Bad", "bad"),
                                        NzbResult("Beyond limit", "beyond")]
        searcher.fetch_nzb.side_effect = [None, INVALID_NZB]

        downloader.search_downloads([job])

        searcher.search.assert_called_once_with("q s01e02", [], [])
        assert searcher.fetch_nzb.call_count == 2
        job = load_job(config, job.job_id)
        assert (job.status, job.rejected) == (JobStatus.WAITING, ["bad"])

    def test_tries_fallback_queries_in_order(self, downloader, config, searcher, caplog):
        caplog.set_level("INFO")
        job = save_job(config, JobStatus.WAITING, keyword="show", query="show 2160p s01e02",
                       fallback_queries=["show 1080p s01e02", "show 720p s01e02"],
                       release_groups={"show 1080p s01e02": ["flux", "ntb"]})
        results = {"show 2160p s01e02": [],
                   "show 1080p s01e02": [NzbResult("1080p", "hd")],
                   "show 720p s01e02": [NzbResult("720p", "sd")]}
        searcher.search.side_effect = lambda query, *_: results[query]
        searcher.fetch_nzb.return_value = VALID_NZB

        downloader.search_downloads([job])

        assert [(x[0][0], x[0][2]) for x in searcher.search.call_args_list] == [
            ("show 2160p s01e02", []), ("show 1080p s01e02", ["flux", "ntb"])]
        assert load_job(config, job.job_id).nzb_guid == "hd"
        assert "No search results found for 'show 2160p s01e02'" in caplog.text
        assert "Search result for query string 'show 1080p s01e02'" in caplog.text

    def test_candidate_limit_spans_fallback_queries(self, downloader, config, searcher):
        job = save_job(config, JobStatus.WAITING, keyword="show", query="show 2160p s01e02",
                       fallback_queries=["show 1080p s01e02", "show 720p s01e02"])
        results = {"show 2160p s01e02": [NzbResult("Bad", "bad")],
                   "show 1080p s01e02": [NzbResult("Unavailable", "unavailable"),
                                         NzbResult("Beyond limit", "beyond")],
                   "show 720p s01e02": [NzbResult("Never searched", "never")]}
        searcher.search.side_effect = lambda query, *_: results[query]
        searcher.fetch_nzb.side_effect = [INVALID_NZB, None]

        downloader.search_downloads([job])

        assert searcher.fetch_nzb.call_count == 2
        assert searcher.search.call_count == 2
        job = load_job(config, job.job_id)
        assert (job.status, job.rejected) == (JobStatus.WAITING, ["bad"])

    def test_rejection_is_shared_with_jobs_for_same_episode(self, downloader, config,
                                                             searcher):
        converted = save_job(config, JobStatus.WAITING, keyword="show",
                             query="show 1080p s01e02")
        download_only = save_job(config, JobStatus.WAITING, keyword="show",
                                 query="show s01e02", download_only=True)
        other_episode = save_job(config, JobStatus.WAITING, keyword="show",
                                 query="show s01e03")
        searcher.search.return_value = [NzbResult("Bad", "bad")]
        searcher.fetch_nzb.return_value = INVALID_NZB

        downloader.search_downloads([converted, download_only, other_episode])

        assert load_job(config, converted.job_id).rejected == ["bad"]
        assert load_job(config, download_only.job_id).rejected == ["bad"]
        assert load_job(config, other_episode.job_id).rejected == ["bad"]
        # The second job's search excludes the result rejected for the first.
        excluded = [x[0][1] for x in searcher.search.call_args_list]
        assert excluded == [[], ["bad"], []]

    def test_no_results_returns_job_to_waiting(self, downloader, config, searcher, caplog):
        caplog.set_level("INFO")
        job = save_job(config, JobStatus.WAITING, keyword="show", query="q")
        searcher.search.return_value = []

        downloader.search_downloads([job])

        assert load_job(config, job.job_id).status == JobStatus.WAITING
        assert "No search results found for 'q'" in caplog.text


class TestSetDownload:

    @pytest.fixture
    def nzb_file(self, tmp_path):
        path = tmp_path / "Show.S01E02.nzb"
        path.write_bytes(VALID_NZB)
        return path

    @pytest.mark.parametrize("location, expected", [
        ("https://indexer/x.nzb", True), ("http://indexer/x.nzb", True),
        ("magnet:?xt=urn:btih:ABCDEF", False), ("/no/such/file.nzb", False)])
    def test_recognizes_urls(self, downloader, location, expected):
        assert downloader.recognizes_download(location) == expected

    def test_recognizes_existing_file(self, downloader, nzb_file):
        assert downloader.recognizes_download(str(nzb_file))

    def test_sets_nzb_file(self, downloader, config, nzb_file):
        job = save_job(config, JobStatus.WAITING)

        warnings = downloader.set_download(job, str(nzb_file))

        assert warnings == ["NZB file lists no par2 files, so damage cannot be repaired"]
        job = load_job(config, job.job_id)
        assert (job.status, job.downloader, job.title, job.nzb_guid) == (
            JobStatus.ADDING, "sabnzbd", "Show.S01E02", None)
        with open(job.nzb_file, "rb") as saved:
            assert saved.read() == VALID_NZB

    def test_sets_nzb_url(self, downloader, config):
        job = save_job(config, JobStatus.WAITING)
        url = "https://indexer/getnzb/Show.S01E02.nzb?apikey=key"

        with mock.patch("infield_fly.usenet.downloader.NewznabSearcher") as searcher_class:
            searcher_class.return_value.fetch_nzb.return_value = VALID_NZB
            downloader.set_download(job, url)

        assert searcher_class.return_value.fetch_nzb.call_args[0][0].link == url
        job = load_job(config, job.job_id)
        assert (job.status, job.title) == (JobStatus.ADDING, "Show.S01E02")

    def test_unavailable_nzb_url(self, downloader, config):
        job = save_job(config, JobStatus.WAITING)

        with mock.patch("infield_fly.usenet.downloader.NewznabSearcher") as searcher_class, \
                pytest.raises(ValueError, match="Could not download NZB file"):
            searcher_class.return_value.fetch_nzb.return_value = None
            downloader.set_download(job, "https://indexer/x.nzb")

        assert load_job(config, job.job_id).status == JobStatus.WAITING

    def test_invalid_nzb_file(self, downloader, config, tmp_path):
        job = save_job(config, JobStatus.WAITING)
        nzb_file = tmp_path / "bad.nzb"
        nzb_file.write_bytes(INVALID_NZB)

        with pytest.raises(ValueError,
                           match="NZB file failed validation: NZB file lists no files"):
            downloader.set_download(job, str(nzb_file))

        assert load_job(config, job.job_id).status == JobStatus.WAITING


class TestAddDownloads:

    def test_no_jobs(self, downloader, sabnzbd_client, caplog):
        caplog.set_level("INFO")

        downloader.add_downloads([])

        assert "No NZB files to add" in caplog.text
        sabnzbd_client.add_nzb.assert_not_called()

    def test_adds_nzbs(self, downloader, config, tmp_path, sabnzbd_client):
        nzb_file = tmp_path / "job.nzb"
        nzb_file.write_bytes(VALID_NZB)
        job = save_job(config, JobStatus.ADDING, downloader="sabnzbd", title="Show.S01E02",
                       nzb_file=str(nzb_file))
        sabnzbd_client.add_nzb.return_value = "nzo1"

        downloader.add_downloads([job])

        sabnzbd_client.add_nzb.assert_called_once_with(str(nzb_file), "Show.S01E02")
        job = load_job(config, job.job_id)
        assert (job.status, job.nzo_id, job.name, job.nzb_file) == (
            JobStatus.DOWNLOADING, "nzo1", "Show.S01E02", None)
        assert not nzb_file.exists()

    def test_failed_add_is_retried(self, downloader, config, tmp_path, sabnzbd_client,
                                   caplog):
        nzb_file = tmp_path / "job.nzb"
        nzb_file.write_bytes(VALID_NZB)
        job = save_job(config, JobStatus.ADDING, downloader="sabnzbd", nzb_file=str(nzb_file))
        sabnzbd_client.add_nzb.side_effect = SabnzbdError("down")

        downloader.add_downloads([job])

        assert load_job(config, job.job_id).status == JobStatus.ADDING
        assert nzb_file.exists()
        assert f"Adding NZB file for job {job.job_id} failed: down" in caplog.text

    def test_missing_nzb_file_returns_job_to_waiting(self, downloader, config, tmp_path,
                                                     sabnzbd_client):
        job = save_job(config, JobStatus.ADDING, downloader="sabnzbd", nzb_guid="guid",
                       nzb_file=str(tmp_path / "missing.nzb"))

        downloader.add_downloads([job])

        sabnzbd_client.add_nzb.assert_not_called()
        job = load_job(config, job.job_id)
        assert (job.status, job.rejected) == (JobStatus.WAITING, ["guid"])


class TestQueryDownloads:

    @staticmethod
    def save_downloading_job(config, nzo_id, **values):
        return save_job(config, JobStatus.DOWNLOADING, downloader="sabnzbd", nzo_id=nzo_id,
                        title=nzo_id, **values)

    def test_no_jobs(self, downloader, sabnzbd_client, caplog):
        caplog.set_level("INFO")

        downloader.query_downloads([])

        assert "No downloading NZB files" in caplog.text
        sabnzbd_client.get_download_status.assert_not_called()

    def test_updates_jobs_by_status(self, downloader, config, sabnzbd_client, caplog):
        completed = self.save_downloading_job(config, "completed")
        failed = self.save_downloading_job(config, "failed", nzb_guid="guid", rejected=["old"])
        missing = self.save_downloading_job(config, "missing")
        running = self.save_downloading_job(config, "running")
        statuses = {"completed": ("Completed", "/dl/complete/Show.S01E02/", None),
                    "failed": ("Failed", "/dl/incomplete/Show", "Repair failed"),
                    "missing": (None, None, None),
                    "running": ("Downloading", None, None)}
        sabnzbd_client.get_download_status.side_effect = lambda nzo_id: statuses[nzo_id]

        downloader.query_downloads([completed, failed, missing, running])

        completed = load_job(config, completed.job_id)
        assert (completed.status, completed.name, completed.download_directory) == (
            JobStatus.PENDING, "Show.S01E02", "/dl/complete")
        failed = load_job(config, failed.job_id)
        assert (failed.status, failed.rejected, failed.nzb_guid, failed.nzo_id) == (
            JobStatus.WAITING, ["old", "guid"], None, None)
        sabnzbd_client.delete_history_item.assert_called_once_with("failed")
        assert "(Repair failed); searching again" in caplog.text
        assert load_job(config, missing.job_id).status == JobStatus.WAITING
        assert "(not found in SABnzbd)" in caplog.text
        assert load_job(config, running.job_id).status == JobStatus.DOWNLOADING

    def test_failed_query_leaves_job_downloading(self, downloader, config, sabnzbd_client,
                                                 caplog):
        job = self.save_downloading_job(config, "nzo1")
        sabnzbd_client.get_download_status.side_effect = SabnzbdError("down")

        downloader.query_downloads([job])

        assert load_job(config, job.job_id).status == JobStatus.DOWNLOADING
        assert f"Querying download for job {job.job_id} failed: down" in caplog.text
