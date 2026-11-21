"""Tests for the usenet.downloader module"""

import os
from unittest import mock

import pytest

from conftest import load_job, save_job
from infield_fly.job_status import JobStatus
from infield_fly.usenet.downloader import NzbDownloader
from infield_fly.usenet.newznab import NzbResult
from infield_fly.usenet.sabnzbd import DownloadStatus, SabnzbdError

VALID_NZB = (b'<nzb><file subject=\'"Show.S01E02.mkv" yEnc (1/1)\'><segments>'
             b'<segment bytes="10" number="1">a@b</segment></segments></file></nzb>')
INVALID_NZB = b"<nzb/>"


@pytest.fixture
def config(make_config):
    return make_config(
        search={"api_key": "key", "max_candidates": 2},
        download={"sabnzbd_host": "sabnzbd", "sabnzbd_port": 8080, "sabnzbd_api_key": "key"})


@pytest.fixture
def downloader(config):
    return NzbDownloader(config)


@pytest.fixture
def sabnzbd_client():
    with mock.patch("infield_fly.usenet.downloader.SabnzbdClient") as client_class:
        client = client_class.return_value
        client.get_download_status.return_value = DownloadStatus(None)
        client.get_complete_directory.return_value = None
        yield client


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
        # The searcher logs why it returned no results, so the downloader logs nothing more.
        assert caplog.messages == []


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
        statuses = {"completed": DownloadStatus("Completed", "/dl/complete/Show.S01E02/"),
                    "failed": DownloadStatus("Failed", "/dl/incomplete/Show", "Repair failed"),
                    "missing": DownloadStatus(None),
                    "running": DownloadStatus("Downloading")}
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


class TestRecoverFinishedDownload:

    TITLE = "Show.S01E02.1080p-GROUP"

    @pytest.fixture
    def complete(self, tmp_path, sabnzbd_client):
        """SABnzbd's completed directory, which no longer has the download in its history"""

        complete = tmp_path / "complete"
        complete.mkdir()
        sabnzbd_client.get_complete_directory.return_value = str(complete)
        return complete

    @staticmethod
    def make_download(parent, name, episode_file="show.s01e02.mkv"):
        folder = parent / name
        folder.mkdir(parents=True)
        if episode_file:
            (folder / episode_file).write_bytes(b"episode")
        return folder

    def save_job(self, config):
        return save_job(config, JobStatus.DOWNLOADING, downloader="sabnzbd", nzo_id="nzo1",
                        title=self.TITLE, query="show 1080p s01e02", nzb_guid="guid")

    def test_finished_download_is_converted(self, downloader, config, complete, caplog):
        folder = self.make_download(complete, self.TITLE)
        job = self.save_job(config)

        downloader.query_downloads([job])

        job = load_job(config, job.job_id)
        assert (job.status, job.name, job.download_directory, job.nzo_id, job.rejected) == (
            JobStatus.PENDING, self.TITLE, str(complete), None, [])
        assert f"is no longer in SABnzbd, but was found in {folder}" in caplog.text

    def test_download_in_category_folder(self, downloader, config, complete):
        self.make_download(complete / "tv", self.TITLE)
        job = self.save_job(config)

        downloader.query_downloads([job])

        assert load_job(config, job.job_id).download_directory == str(complete / "tv")

    def test_most_recent_copy_is_used(self, downloader, config, complete):
        older = self.make_download(complete, self.TITLE)
        self.make_download(complete, f"{self.TITLE}.1")
        os.utime(older, (0, 0))

        job = self.save_job(config)
        downloader.query_downloads([job])

        assert load_job(config, job.job_id).name == f"{self.TITLE}.1"

    @pytest.mark.parametrize("name, episode_file", [
        ("Other.Download", "show.s01e02.mkv"),
        (f"{TITLE}.extra", "show.s01e02.mkv"),
        (TITLE, "show.s01e03.mkv"),
        (TITLE, None),
    ])
    def test_unsuitable_folders_are_not_used(self, downloader, config, complete, caplog,
                                             name, episode_file):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.make_download(complete, name, episode_file)
        job = self.save_job(config)

        downloader.query_downloads([job])

        job = load_job(config, job.job_id)
        assert (job.status, job.rejected) == (JobStatus.WAITING, ["guid"])
        assert "(not found in SABnzbd); searching again" in caplog.text

    def test_job_without_episode_number_accepts_any_episode(self, downloader, config,
                                                             complete):
        self.make_download(complete, self.TITLE, "show.special.s00e01.mkv")
        job = save_job(config, JobStatus.DOWNLOADING, downloader="sabnzbd", nzo_id="nzo1",
                       title=self.TITLE, query="show special")

        downloader.query_downloads([job])

        assert load_job(config, job.job_id).status == JobStatus.PENDING

    @pytest.mark.parametrize("complete_directory", [None, "/missing/complete"])
    def test_no_completed_directory(self, downloader, config, sabnzbd_client,
                                    complete_directory):
        sabnzbd_client.get_complete_directory.return_value = complete_directory
        job = self.save_job(config)

        downloader.query_downloads([job])

        assert load_job(config, job.job_id).status == JobStatus.WAITING

    def test_failed_lookup(self, downloader, config, sabnzbd_client, caplog):
        sabnzbd_client.get_complete_directory.side_effect = SabnzbdError("down")
        job = self.save_job(config)

        downloader.query_downloads([job])

        assert load_job(config, job.job_id).status == JobStatus.WAITING
        assert f"Could not look for the download of job {job.job_id}: down" in caplog.text


class TestRemoveDownload:

    @pytest.fixture
    def delete_config(self, make_config):
        return make_config(download={"sabnzbd_api_key": "key",
                                     "delete_completed_downloads": True})

    @staticmethod
    def save_converted_job(config, download_directory, name, nzo_id="nzo1"):
        return save_job(config, JobStatus.COMPLETED, downloader="sabnzbd", nzo_id=nzo_id,
                        name=name, download_directory=str(download_directory))

    @pytest.fixture
    def completed(self, tmp_path, sabnzbd_client):
        """
        SABnzbd's completed directory, with a download whose episode file was given to its
        job by hand, a leftover file and sample beside it, and another download
        """

        complete = tmp_path / "complete"
        folder = complete / "Show.S01E02.1080p-GROUP"
        (folder / "Sample").mkdir(parents=True)
        (folder / "show.s01e02.mkv").touch()
        (folder / "show.s01e02.nfo").touch()
        (folder / "Sample" / "sample.mkv").touch()
        (complete / "Other.Download").mkdir()
        sabnzbd_client.get_download_status.return_value = DownloadStatus("Completed",
                                                                         str(folder))
        sabnzbd_client.get_complete_directory.return_value = str(complete)
        return complete, folder

    def test_deletes_folder_of_file_given_by_hand(self, delete_config, sabnzbd_client,
                                                  completed, caplog):
        caplog.set_level("INFO")
        complete, folder = completed
        job = self.save_converted_job(delete_config, folder, "show.s01e02.mkv")

        NzbDownloader(delete_config).remove_download(job)

        assert not folder.exists()
        assert sorted(os.listdir(complete)) == ["Other.Download"]
        sabnzbd_client.get_download_status.assert_called_once_with("nzo1")
        sabnzbd_client.delete_history_item.assert_called_once_with("nzo1")
        assert f"Deleting completed download {os.path.realpath(folder)}" in caplog.text

    @pytest.mark.parametrize("folder_suffix", ["", ".1"])
    def test_deletes_folder_of_file_recorded_for_job(self, delete_config, sabnzbd_client,
                                                     completed, folder_suffix):
        complete, folder = completed
        if folder_suffix:
            folder = folder.rename(complete / f"{folder.name}{folder_suffix}")
        sabnzbd_client.get_download_status.return_value = DownloadStatus(
            "Completed", str(folder / "show.s01e02.mkv"), None, "Show.S01E02.1080p-GROUP")
        job = self.save_converted_job(delete_config, folder, "show.s01e02.mkv")

        NzbDownloader(delete_config).remove_download(job)

        assert not folder.exists()
        assert sorted(os.listdir(complete)) == ["Other.Download"]

    def test_job_title_names_folder_without_history_name(self, delete_config,
                                                          sabnzbd_client, completed):
        _, folder = completed
        sabnzbd_client.get_download_status.return_value = DownloadStatus(
            "Completed", str(folder / "show.s01e02.mkv"))
        job = save_job(delete_config, JobStatus.COMPLETED, downloader="sabnzbd",
                       nzo_id="nzo1", name="show.s01e02.mkv", download_directory=str(folder),
                       title="Show.S01E02.1080p-GROUP")

        NzbDownloader(delete_config).remove_download(job)

        assert not folder.exists()

    def test_keeps_shared_folder_of_file(self, delete_config, sabnzbd_client, completed):
        complete, _ = completed
        category = complete / "tv"
        category.mkdir()
        (category / "show.s01e02.mkv").touch()
        (category / "another.download.mkv").touch()
        sabnzbd_client.get_download_status.return_value = DownloadStatus(
            "Completed", str(category / "show.s01e02.mkv"), None, "Show.S01E02.1080p-GROUP")
        job = self.save_converted_job(delete_config, category, "show.s01e02.mkv")

        NzbDownloader(delete_config).remove_download(job)

        assert sorted(os.listdir(category)) == ["another.download.mkv"]

    def test_deletes_folder_recorded_for_job(self, delete_config, sabnzbd_client, completed):
        complete, folder = completed
        job = self.save_converted_job(delete_config, complete, folder.name)

        NzbDownloader(delete_config).remove_download(job)

        assert not folder.exists()
        assert complete.exists()

    @pytest.mark.parametrize("storage, complete_directory", [
        (None, "complete"),
        ("Show.S01E02.1080p-GROUP", None),
        # A download saved directly in the completed directory.
        ("", "complete"),
        # A folder outside the completed directory.
        ("Show.S01E02.1080p-GROUP", "elsewhere"),
        # A file rather than a folder.
        ("Show.S01E02.1080p-GROUP/show.s01e02.mkv", "complete"),
    ])
    def test_deletes_only_file_when_folder_not_confirmed(
            self, delete_config, sabnzbd_client, completed, tmp_path, storage,
            complete_directory):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        complete, folder = completed
        (tmp_path / "elsewhere").mkdir()
        sabnzbd_client.get_download_status.return_value = DownloadStatus(
            "Completed", None if storage is None else str(complete / storage))
        sabnzbd_client.get_complete_directory.return_value = (
            None if complete_directory is None else str(tmp_path / complete_directory))
        job = self.save_converted_job(delete_config, folder, "show.s01e02.mkv")

        NzbDownloader(delete_config).remove_download(job)

        assert not (folder / "show.s01e02.mkv").exists()
        assert (folder / "show.s01e02.nfo").exists()
        sabnzbd_client.delete_history_item.assert_called_once_with("nzo1")

    def test_keeps_folder_not_containing_download(self, delete_config, sabnzbd_client,
                                                  completed, tmp_path):
        _, folder = completed
        (tmp_path / "torrents").mkdir()
        (tmp_path / "torrents" / "show.s01e02.mkv").touch()
        job = self.save_converted_job(delete_config, tmp_path / "torrents", "show.s01e02.mkv")

        NzbDownloader(delete_config).remove_download(job)

        assert not (tmp_path / "torrents" / "show.s01e02.mkv").exists()
        assert folder.exists()

    def test_failed_folder_lookup_deletes_only_file(self, delete_config, sabnzbd_client,
                                                    completed, caplog):
        _, folder = completed
        sabnzbd_client.get_complete_directory.side_effect = SabnzbdError("down")
        job = self.save_converted_job(delete_config, folder, "show.s01e02.mkv")

        NzbDownloader(delete_config).remove_download(job)

        assert not (folder / "show.s01e02.mkv").exists()
        assert folder.exists()
        assert f"Could not find the folder of the download for job {job.job_id}: down" in (
            caplog.text)
        sabnzbd_client.delete_history_item.assert_called_once_with("nzo1")

    def test_without_nzo_id_does_not_ask_sabnzbd(self, delete_config, sabnzbd_client,
                                                 completed):
        _, folder = completed
        job = self.save_converted_job(delete_config, folder, "show.s01e02.mkv", nzo_id=None)

        NzbDownloader(delete_config).remove_download(job)

        assert folder.exists()
        sabnzbd_client.get_download_status.assert_not_called()

    def test_disabled_by_default(self, downloader, config, sabnzbd_client, tmp_path):
        (tmp_path / "Show.S01E02").mkdir()
        job = self.save_converted_job(config, tmp_path, "Show.S01E02")

        downloader.remove_download(job)

        assert (tmp_path / "Show.S01E02").exists()
        sabnzbd_client.delete_history_item.assert_not_called()

    def test_deletes_directory_and_history_item(self, delete_config, sabnzbd_client,
                                                tmp_path, caplog):
        caplog.set_level("INFO")
        download = tmp_path / "Show.S01E02"
        download.mkdir()
        (download / "Show.S01E02.mkv").touch()
        job = self.save_converted_job(delete_config, tmp_path, "Show.S01E02")

        NzbDownloader(delete_config).remove_download(job)

        assert not download.exists()
        sabnzbd_client.delete_history_item.assert_called_once_with("nzo1")
        assert f"Deleting completed download {download}" in caplog.text

    def test_deletes_single_file(self, delete_config, sabnzbd_client, tmp_path):
        (tmp_path / "Show.S01E02.mkv").touch()
        job = self.save_converted_job(delete_config, tmp_path, "Show.S01E02.mkv")

        NzbDownloader(delete_config).remove_download(job)

        assert not (tmp_path / "Show.S01E02.mkv").exists()
        sabnzbd_client.delete_history_item.assert_called_once_with("nzo1")

    def test_missing_download_still_removes_history_item(self, delete_config, sabnzbd_client,
                                                         tmp_path):
        job = self.save_converted_job(delete_config, tmp_path, "Already.Deleted")

        NzbDownloader(delete_config).remove_download(job)

        sabnzbd_client.delete_history_item.assert_called_once_with("nzo1")

    def test_without_nzo_id_deletes_only_files(self, delete_config, sabnzbd_client, tmp_path):
        (tmp_path / "Show.S01E02").mkdir()
        job = self.save_converted_job(delete_config, tmp_path, "Show.S01E02", nzo_id=None)

        NzbDownloader(delete_config).remove_download(job)

        assert not (tmp_path / "Show.S01E02").exists()
        sabnzbd_client.delete_history_item.assert_not_called()

    def test_failed_file_deletion_keeps_history_item(self, delete_config, sabnzbd_client,
                                                     tmp_path, caplog):
        (tmp_path / "Show.S01E02").mkdir()
        job = self.save_converted_job(delete_config, tmp_path, "Show.S01E02")

        with mock.patch("infield_fly.usenet.downloader.shutil.rmtree",
                        side_effect=OSError("busy")):
            NzbDownloader(delete_config).remove_download(job)

        sabnzbd_client.delete_history_item.assert_not_called()
        assert f"Deleting download for job {job.job_id} failed: busy" in caplog.text

    def test_failed_history_deletion_is_logged(self, delete_config, sabnzbd_client, tmp_path,
                                               caplog):
        (tmp_path / "Show.S01E02").mkdir()
        job = self.save_converted_job(delete_config, tmp_path, "Show.S01E02")
        sabnzbd_client.delete_history_item.side_effect = SabnzbdError("down")

        NzbDownloader(delete_config).remove_download(job)

        assert not (tmp_path / "Show.S01E02").exists()
        assert (f"Removing download for job {job.job_id} from SABnzbd history failed: down"
                in caplog.text)
