"""Tests for the jobs module"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from conftest import make_episode_db
from infield_fly.database import EpisodeInfo
from infield_fly.jobs import Job, JobQueue, JobStatus
from infield_fly.search import TorrentResult

TODAY = datetime.now().strftime("%Y-%m-%d")
PLEX_NAME = "The Show - s01e02 - Pilot - Part 2"


@pytest.fixture
def config(make_config):
    return make_config(
        conversion={"substitutions": {":": " -"}, "deluge_host": "deluge", "deluge_port": 1,
                    "deluge_user_name": "user", "deluge_password": "pass",
                    "ffmpeg_location": "/ffmpeg"},
        metadata={"tracked_series": {
            "show": {"id": 1, "description": "The Show", "enable_search": True,
                     "search_configs": [{"search_terms": ["1080p"]}, {"download_only": True}]},
            "missing": {"id": 2, "description": "Missing", "enable_search": True}
        }})


@pytest.fixture
def episode_db(config):
    """Patches the cached episode database with a known series and episode"""

    database = make_episode_db(config, [
        EpisodeInfo(12, "The Show", season_number=1, episode_number=2, title="Pilot: Part 2",
                    airdate=datetime(2020, 1, 2))])
    with mock.patch("infield_fly.jobs.EpisodeDatabase.load_from_cache", return_value=database):
        yield database


@pytest.fixture
def queue(config):
    return JobQueue(config)


def add_job(queue, status, **values):
    """Saves a job with the specified status and values to the queue directory"""

    job = Job(queue.job_queue_file_path, {"status": status.value, **values})
    job.save(logging.getLogger())
    return job


def job_statuses(queue):
    return sorted((x.query, x.status) for x in queue.load_jobs())


@pytest.fixture
def deluge_client():
    with mock.patch("infield_fly.jobs.DelugeRPCClient") as client_class:
        yield client_class.return_value.__enter__.return_value


class TestJobQueueBasics:

    def test_load_jobs_creates_directory(self, queue, config):
        assert queue.load_jobs() == []
        assert os.path.isdir(config.conversion.job_directory)

    def test_get_job_by_id(self, queue):
        job = add_job(queue, JobStatus.WAITING)

        assert queue.get_job_by_id(job.job_id).job_id == job.job_id
        assert queue.get_job_by_id("missing") is None

    def test_create_job(self, queue, episode_db):
        job = queue.create_job("show", "the show s01e02", True)

        loaded = queue.get_job_by_id(job.job_id)
        assert loaded.keyword == "show"
        assert loaded.query == "the show s01e02"
        assert loaded.is_download_only
        assert loaded.status == JobStatus.WAITING
        assert loaded.converted_file_name == PLEX_NAME

    def test_get_jobs_by_status(self, queue):
        add_job(queue, JobStatus.WAITING, query="a")
        add_job(queue, JobStatus.PENDING, query="b")

        assert [x.query for x in queue.get_jobs_by_status(JobStatus.PENDING)] == ["b"]

    def test_is_existing_job(self, queue):
        add_job(queue, JobStatus.WAITING, keyword="show", query="q")

        assert queue.is_existing_job("show", "q")
        assert not queue.is_existing_job("show", "other")
        assert not queue.is_existing_job("other", "q")

    def test_set_job_search_result(self, queue):
        job = add_job(queue, JobStatus.SEARCHING)

        queue.set_job_search_result(job, TorrentResult("Title", "magnet:?x", "HASH"))

        loaded = queue.get_job_by_id(job.job_id)
        assert loaded.status == JobStatus.ADDING
        assert (loaded.title, loaded.magnet_link, loaded.torrent_hash) == (
            "Title", "magnet:?x", "HASH")

    def test_set_job_download_status(self, queue):
        job = add_job(queue, JobStatus.ADDING)

        queue.set_job_download_status(job, JobStatus.DOWNLOADING, "name", "/downloads")
        queue.set_job_download_status(job, JobStatus.PENDING, "renamed")

        loaded = queue.get_job_by_id(job.job_id)
        assert (loaded.status, loaded.name, loaded.download_directory) == (
            JobStatus.PENDING, "renamed", "/downloads")

    def test_mark_job_complete_today_keeps_job(self, queue):
        job = add_job(queue, JobStatus.CONVERTING)

        queue.mark_job_complete(job)

        assert queue.get_job_by_id(job.job_id).status == JobStatus.COMPLETED

    def test_mark_job_complete_earlier_deletes_job(self, queue):
        job = add_job(queue, JobStatus.CONVERTING, added="2000-01-01")

        queue.mark_job_complete(job)

        assert queue.get_job_by_id(job.job_id) is None


class TestRefreshJobs:

    AIRDATE = datetime(2020, 1, 2)

    def test_creates_jobs_and_cleans_up_queue(self, queue, episode_db):
        old_completed = add_job(queue, JobStatus.COMPLETED, query="old", added="2020-01-01")
        add_job(queue, JobStatus.COMPLETED, query="current", added="2020-01-02")
        add_job(queue, JobStatus.WAITING, keyword="show", query="show s01e01")
        stale = add_job(queue, JobStatus.WAITING, keyword="show", query="show 720p s01e02",
                        converted_file_name="Old name")

        queue.refresh_jobs(self.AIRDATE)

        assert queue.get_job_by_id(old_completed.job_id) is None
        assert job_statuses(queue) == [
            ("current", JobStatus.COMPLETED),
            ("show 1080p s01e02", JobStatus.WAITING),
            ("show 720p s01e02", JobStatus.WAITING),
            ("show s01e01", JobStatus.WAITING),
            ("show s01e02", JobStatus.WAITING),
        ]
        jobs = {(x.query, x.is_download_only): x for x in queue.load_jobs()}
        assert jobs[("show 1080p s01e02", False)].converted_file_name == PLEX_NAME
        assert jobs[("show s01e02", True)].converted_file_name == PLEX_NAME
        assert queue.get_job_by_id(stale.job_id).converted_file_name == PLEX_NAME

    def test_does_not_duplicate_existing_jobs(self, queue, episode_db):
        queue.refresh_jobs(self.AIRDATE)
        queue.refresh_jobs(self.AIRDATE)

        assert len(queue.load_jobs()) == 2

    def test_no_episodes_on_airdate(self, queue, episode_db):
        queue.refresh_jobs(datetime(2021, 1, 1))

        assert queue.load_jobs() == []


class TestAddTorrents:

    def test_no_jobs(self, queue, deluge_client, caplog):
        caplog.set_level("INFO")

        queue.add_torrents()

        assert "No search results to add" in caplog.text
        deluge_client.core.prefetch_magnet_metadata.assert_not_called()

    def test_adds_torrents(self, queue, deluge_client):
        named = add_job(queue, JobStatus.ADDING, magnet_link="magnet:1")
        unnamed = add_job(queue, JobStatus.ADDING, magnet_link="magnet:2")
        metadata = {named.magnet_link: (b"id1", {b"name": b"Metadata name"}),
                    unnamed.magnet_link: (b"id2", {})}
        deluge_client.core.prefetch_magnet_metadata.side_effect = lambda link: metadata[link]
        deluge_client.core.get_torrent_status.return_value = {
            b"name": b"Torrent name", b"download_location": b"/downloads"}

        queue.add_torrents()

        named = queue.get_job_by_id(named.job_id)
        assert (named.status, named.torrent_hash, named.name, named.download_directory) == (
            JobStatus.DOWNLOADING, "id1", "Metadata name", "/downloads")
        assert queue.get_job_by_id(unnamed.job_id).name == "Torrent name"
        deluge_client.core.add_torrent_magnet.assert_any_call("magnet:1", {})
        deluge_client.core.get_torrent_status.assert_any_call(
            "id1", ["name", "download_location", "is_finished"])

    def test_skips_job_when_prefetch_returns_no_id(self, queue, deluge_client, caplog):
        failed = add_job(queue, JobStatus.ADDING, magnet_link="magnet:1")
        added = add_job(queue, JobStatus.ADDING, magnet_link="magnet:2")
        metadata = {failed.magnet_link: (None, None),
                    added.magnet_link: (b"id2", {b"name": b"Metadata name"})}
        deluge_client.core.prefetch_magnet_metadata.side_effect = lambda link: metadata[link]
        deluge_client.core.get_torrent_status.return_value = {
            b"name": b"Torrent name", b"download_location": b"/downloads"}

        queue.add_torrents()

        assert f"failed for job {failed.job_id}, no ID returned" in caplog.text
        deluge_client.core.add_torrent_magnet.assert_called_once_with("magnet:2", {})
        assert queue.get_job_by_id(failed.job_id).status == JobStatus.ADDING
        assert queue.get_job_by_id(added.job_id).status == JobStatus.DOWNLOADING

    def test_uses_torrent_name_when_prefetch_returns_no_metadata(self, queue, deluge_client,
                                                                 caplog):
        job = add_job(queue, JobStatus.ADDING, magnet_link="magnet:1")
        deluge_client.core.prefetch_magnet_metadata.return_value = (b"id1", None)
        deluge_client.core.get_torrent_status.return_value = {
            b"name": b"Torrent name", b"download_location": b"/downloads"}

        queue.add_torrents()

        job = queue.get_job_by_id(job.job_id)
        assert (job.status, job.torrent_hash, job.name) == (
            JobStatus.DOWNLOADING, "id1", "Torrent name")
        assert "returned no metadata" in caplog.text


class TestQueryTorrentsStatus:

    def test_no_jobs(self, queue, deluge_client, caplog):
        caplog.set_level("INFO")

        queue.query_torrents_status()

        assert "No downloading jobs" in caplog.text
        deluge_client.core.get_torrent_status.assert_not_called()

    def test_marks_finished_downloads_pending(self, queue, deluge_client):
        finished = add_job(queue, JobStatus.DOWNLOADING, torrent_hash="done",
                           download_directory="/downloads")
        unfinished = add_job(queue, JobStatus.DOWNLOADING, torrent_hash="busy")
        statuses = {"done": {b"name": b"Done name", b"is_finished": True},
                    "busy": {b"name": b"Busy name"}}
        deluge_client.core.get_torrent_status.side_effect = lambda torrent_hash, _: \
            statuses[torrent_hash]

        queue.query_torrents_status()

        finished = queue.get_job_by_id(finished.job_id)
        assert (finished.status, finished.name, finished.download_directory) == (
            JobStatus.PENDING, "Done name", "/downloads")
        assert queue.get_job_by_id(unfinished.job_id).status == JobStatus.DOWNLOADING


class TestPerformConversions:

    def test_no_jobs(self, queue, caplog):
        caplog.set_level("INFO")

        queue.perform_conversions()

        assert "No files to convert" in caplog.text

    def test_dispatches_by_job_type(self, queue):
        copy_job = add_job(queue, JobStatus.PENDING, download_only=True)
        convert_job = add_job(queue, JobStatus.PENDING)
        handled = []

        def record(job, is_unattended_mode):
            # Every job is marked converting before any is processed.
            assert all(x.status == JobStatus.CONVERTING for x in queue.load_jobs())
            handled.append((job.job_id, is_unattended_mode))

        with mock.patch.object(queue, "copy_downloaded_files", side_effect=record), \
                mock.patch.object(queue, "convert_downloaded_files", side_effect=record):
            queue.perform_conversions(True)

        assert sorted(handled) == sorted([(copy_job.job_id, True), (convert_job.job_id, True)])

    def test_failed_job_returns_to_pending(self, queue, caplog):
        job = add_job(queue, JobStatus.PENDING)

        with mock.patch.object(queue, "convert_downloaded_files",
                               side_effect=RuntimeError("ffmpeg failed")):
            queue.perform_conversions()

        assert queue.get_job_by_id(job.job_id).status == JobStatus.PENDING
        assert f"Processing job {job.job_id} failed: ffmpeg failed" in caplog.text


class TestCopyDownloadedFiles:

    @pytest.mark.parametrize("is_unattended_mode", [False, True])
    def test_copies_and_completes(self, queue, config, tmp_path, caplog, is_unattended_mode):
        caplog.set_level("INFO")
        download = tmp_path / "downloads" / "Show.S01"
        download.mkdir(parents=True)
        (download / "episode.mkv").write_text("data")
        job = add_job(queue, JobStatus.CONVERTING, name="Show.S01",
                      download_directory=str(tmp_path / "downloads"))

        queue.copy_downloaded_files(job, is_unattended_mode)

        copied = os.path.join(config.conversion.final_directory, "Show.S01", "episode.mkv")
        with open(copied, encoding="utf-8") as copied_file:
            assert copied_file.read() == "data"
        assert queue.get_job_by_id(job.job_id).status == JobStatus.COMPLETED
        assert ("Copy completed" in caplog.text) == is_unattended_mode


class TestConvertDownloadedFiles:

    @pytest.fixture
    def converter_class(self):
        converters = []

        def create_converter(_src_file, dest_file, *_):
            converter = mock.Mock()
            converter.convert_file.side_effect = lambda _: Path(dest_file).touch()
            converters.append(converter)
            return converter

        with mock.patch("infield_fly.jobs.Converter",
                        side_effect=create_converter) as converter_class:
            converter_class.created = converters
            yield converter_class

    def test_converts_episode_in_directory(self, queue, config, tmp_path, converter_class,
                                           caplog):
        caplog.set_level("INFO")
        download = tmp_path / "downloads" / "Show.S01E02"
        download.mkdir(parents=True)
        (download / "Show.S01E02.mkv").touch()
        (download / "sample.txt").touch()
        job = add_job(queue, JobStatus.CONVERTING, name="Show.S01E02",
                      download_directory=str(tmp_path / "downloads"),
                      converted_file_name=PLEX_NAME)

        queue.convert_downloaded_files(job, False)

        staged = os.path.join(config.conversion.staging_directory, f"{PLEX_NAME}.mp4")
        converter_class.assert_called_once_with(
            str(download / "Show.S01E02.mkv"), staged, "/ffmpeg", False)
        settings = converter_class.created[0].convert_file.call_args[0][0]
        assert (settings.convert_video, settings.convert_audio, settings.convert_subtitles) == (
            False, True, True)
        assert os.path.exists(os.path.join(config.conversion.final_directory,
                                           f"{PLEX_NAME}.mp4"))
        assert not os.path.exists(staged)
        assert queue.get_job_by_id(job.job_id).status == JobStatus.COMPLETED
        assert "Starting conversion" not in caplog.text

    def test_converts_single_file_unattended(self, queue, config, tmp_path, converter_class,
                                             caplog):
        caplog.set_level("INFO")
        (tmp_path / "Show.S01E02.mp4").touch()
        job = add_job(queue, JobStatus.CONVERTING, name="Show.S01E02.mp4",
                      download_directory=str(tmp_path), converted_file_name=PLEX_NAME)

        queue.convert_downloaded_files(job, True)

        assert converter_class.call_args[0][0] == str(tmp_path / "Show.S01E02.mp4")
        assert os.path.exists(os.path.join(config.conversion.final_directory,
                                           f"{PLEX_NAME}.mp4"))
        assert "Starting conversion" in caplog.text
        assert "Conversion completed" in caplog.text

    def test_converts_only_largest_episode_file(self, queue, config, tmp_path,
                                                converter_class):
        download = tmp_path / "Show.S01E02"
        download.mkdir()
        (download / "Show.S01E02.1080p.mkv").write_bytes(b"x" * 100)
        (download / "Show.S01E02.sample.mkv").write_bytes(b"x" * 10)
        (download / "Show.S01E02.1080p.mkv.part").write_bytes(b"x" * 1000)
        (download / "Show.S01E02.extras.mkv").mkdir()
        job = add_job(queue, JobStatus.CONVERTING, name="Show.S01E02",
                      download_directory=str(tmp_path), converted_file_name=PLEX_NAME)

        queue.convert_downloaded_files(job, False)

        converter_class.assert_called_once()
        assert converter_class.call_args[0][0] == str(download / "Show.S01E02.1080p.mkv")
        assert queue.get_job_by_id(job.job_id).status == JobStatus.COMPLETED

    def test_missing_single_file_returns_job_to_pending(self, queue, tmp_path,
                                                        converter_class):
        job = add_job(queue, JobStatus.CONVERTING, name="Show.S01E02.mkv",
                      download_directory=str(tmp_path))

        queue.convert_downloaded_files(job, False)

        converter_class.assert_not_called()
        assert queue.get_job_by_id(job.job_id).status == JobStatus.PENDING

    def test_no_episode_files_returns_job_to_pending(self, queue, tmp_path, converter_class,
                                                     caplog):
        download = tmp_path / "downloads" / "Show"
        download.mkdir(parents=True)
        (download / "readme.txt").touch()
        job = add_job(queue, JobStatus.CONVERTING, name="Show",
                      download_directory=str(tmp_path / "downloads"))

        queue.convert_downloaded_files(job, False)

        converter_class.assert_not_called()
        assert queue.get_job_by_id(job.job_id).status == JobStatus.PENDING
        assert "No episode files found" in caplog.text


class TestJobStatus:

    def test_known_value(self):
        assert JobStatus("pending") == JobStatus.PENDING

    def test_unknown_value(self):
        assert JobStatus("bogus") == JobStatus.UNKNOWN


class TestJob:

    def test_defaults(self, tmp_path):
        job = Job(str(tmp_path), {})

        assert job.job_id
        assert job.file_path == os.path.join(str(tmp_path), job.job_id)
        assert job.status == JobStatus.WAITING
        assert job.added == TODAY
        assert not job.is_download_only
        for name in ("keyword", "query", "magnet_link", "title", "name", "torrent_hash",
                     "download_directory", "converted_file_name"):
            assert getattr(job, name) is None

    def test_properties_round_trip(self, tmp_path):
        job = Job(str(tmp_path), {"id": "abc", "status": "pending", "added": "2020-01-01",
                                  "download_only": True})
        values = {"keyword": "k", "query": "q", "magnet_link": "m", "title": "t", "name": "n",
                  "torrent_hash": "h", "download_directory": "d", "converted_file_name": "c",
                  "added": "2021-01-01", "status": JobStatus.ADDING, "is_download_only": False}
        assert job.job_id == "abc"
        assert job.status == JobStatus.PENDING
        assert job.is_download_only

        for name, value in values.items():
            setattr(job, name, value)

        assert {name: getattr(job, name) for name in values} == values

    def test_save_load_and_delete(self, tmp_path):
        directory = str(tmp_path / "new")
        job = Job(directory, {"query": "q"})

        job.save(logging.getLogger())

        with open(job.file_path, encoding="utf-8") as job_file:
            assert json.load(job_file)["status"] == "waiting"
        loaded = Job.load(directory, job.job_id)
        assert loaded.dictionary == job.dictionary

        job.delete()
        job.delete()
        assert Job.load(directory, job.job_id) is None

    def test_save_to_non_directory_path_warns_without_writing(self, tmp_path, caplog):
        not_a_directory = tmp_path / "file"
        not_a_directory.touch()

        Job(str(not_a_directory), {}).save(logging.getLogger())

        assert "is not a directory" in caplog.text
        assert not_a_directory.read_text(encoding="utf-8") == ""

    def test_copy_has_new_id(self, tmp_path):
        job = Job(str(tmp_path), {"query": "q", "status": "pending"})

        job_copy = job.copy()

        assert job_copy.job_id != job.job_id
        assert job_copy.query == "q"
        assert job_copy.status == JobStatus.PENDING

    @pytest.mark.parametrize("status, values, expected", [
        ("waiting", {}, "Waiting to search for 'q'"),
        ("searching", {}, "Searching using search term 'q'"),
        ("adding", {}, "Adding download for 't'"),
        ("downloading", {}, "Downloading 'n'"),
        ("pending", {}, "Pending conversion of finished download 'n'"),
        ("converting", {}, "Converting 'n' to 'c'"),
        ("completed", {}, "Completed conversion to 'c'"),
        ("completed", {"download_only": True}, "Completed download of 'n'"),
        ("unknown", {}, "Unknown status - 'q'"),
    ])
    def test_status_description(self, tmp_path, status, values, expected):
        job = Job(str(tmp_path), {"status": status, "query": "q", "title": "t", "name": "n",
                                  "converted_file_name": "c", **values})

        assert job.status_description == expected


class TestUpdateConvertedFileName:

    @pytest.mark.parametrize("keyword, query", [
        ("show", "no episode marker"),
        ("unknown", "show s01e02"),
        ("show", "show s09e09"),
    ])
    def test_leaves_name_unchanged(self, config, episode_db, keyword, query):
        job = Job("unused", {"keyword": keyword, "query": query, "converted_file_name": "old"})

        job.update_converted_file_name(config)

        assert job.converted_file_name == "old"

    @pytest.mark.parametrize("current", [None, PLEX_NAME])
    def test_sets_name_from_episode(self, config, episode_db, current):
        job = Job("unused", {"keyword": "show", "query": "show S01E02",
                             "converted_file_name": current})

        job.update_converted_file_name(config)

        assert job.converted_file_name == PLEX_NAME
