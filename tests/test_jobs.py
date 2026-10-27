"""Tests for the jobs module"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from conftest import TRACKED_SERIES, make_episode_db
from infield_fly.configuration import EpisodeSearch
from infield_fly.database import EpisodeInfo
from infield_fly.jobs import Job, JobQueue, JobStatus
from infield_fly.torrent.downloader import TorrentDownloader
from infield_fly.usenet.downloader import NzbDownloader

TODAY = datetime.now().strftime("%Y-%m-%d")
PLEX_NAME = "The Show - s01e02 - Pilot - Part 2"


@pytest.fixture
def config(make_config):
    return make_config(
        conversion={"substitutions": {":": " -"}, "ffmpeg_location": "/ffmpeg"},
        metadata={"tracked_series": TRACKED_SERIES})


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
        assert not queue.is_existing_job("show", "q", True)

    def test_create_search_job(self, queue):
        search = EpisodeSearch(["show 2160p s01e02", "show s01e02"], False,
                               {"show 2160p s01e02": ["flux", "ntb"]})

        job = queue.create_search_job("show", search)

        job = queue.get_job_by_id(job.job_id)
        assert (job.search_queries, job.release_groups, job.is_download_only) == (
            ["show 2160p s01e02", "show s01e02"], {"show 2160p s01e02": ["flux", "ntb"]},
            False)
        assert queue.create_search_job("show", search) is None

    def test_share_rejections(self, queue):
        first = add_job(queue, JobStatus.WAITING, keyword="show", query="show s01e02",
                        rejected=["a", "b"])
        second = add_job(queue, JobStatus.DOWNLOADING, keyword="show",
                         query="show 2160p S1E2", rejected=["c", "a"])
        other_episode = add_job(queue, JobStatus.WAITING, keyword="show",
                                query="show s01e03", rejected=["d"])
        other_series = add_job(queue, JobStatus.WAITING, keyword="other",
                               query="other s01e02")
        no_episode = add_job(queue, JobStatus.WAITING, keyword="show", query="show special",
                             rejected=["e"])

        queue.share_rejections(queue.load_jobs())

        rejected = {x.job_id: x.rejected for x in queue.load_jobs()}
        assert sorted(rejected[first.job_id]) == ["a", "b", "c"]
        assert sorted(rejected[second.job_id]) == ["a", "b", "c"]
        assert rejected[other_episode.job_id] == ["d"]
        assert rejected[other_series.job_id] == []
        assert rejected[no_episode.job_id] == ["e"]

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

    def test_creates_one_converted_job_with_fallbacks(self, make_config):
        config = make_config(
            conversion={"substitutions": {":": " -"}},
            metadata={"tracked_series": {"show": {
                "id": 1, "description": "The Show", "enable_search": True,
                "search_configs": [{"search_terms": ["2160p"]}, {"search_terms": ["1080p"]},
                                   {"search_terms": ["1080p"], "download_only": True}]}}})
        queue = JobQueue(config)
        database = make_episode_db(config, [
            EpisodeInfo(12, "The Show", season_number=1, episode_number=2,
                        title="Pilot: Part 2", airdate=self.AIRDATE)])

        with mock.patch("infield_fly.jobs.EpisodeDatabase.load_from_cache",
                        return_value=database):
            queue.refresh_jobs(self.AIRDATE)

        jobs = sorted(((x.search_queries, x.is_download_only, x.converted_file_name)
                       for x in queue.load_jobs()), key=lambda x: x[1])
        assert jobs == [
            (["show 2160p s01e02", "show 1080p s01e02"], False, PLEX_NAME),
            (["show 1080p s01e02"], True, PLEX_NAME)]

    def test_no_episodes_on_airdate(self, queue, episode_db):
        queue.refresh_jobs(datetime(2021, 1, 1))

        assert queue.load_jobs() == []


class TestSearchDownloadsSharesRejections:

    def test_shares_rejections_before_searching(self, queue):
        waiting = add_job(queue, JobStatus.WAITING, keyword="show", query="show s01e02")
        add_job(queue, JobStatus.DOWNLOADING, keyword="show", query="show 1080p s01e02",
                rejected=["bad"])

        with mock.patch("infield_fly.jobs.DOWNLOADER_CLASSES", ()):
            queue.search_downloads()

        assert queue.get_job_by_id(waiting.job_id).rejected == ["bad"]


class TestDownloadTracks:

    @pytest.fixture
    def tracks(self):
        """Replaces the download tracks with two mock tracks, named 'first' and 'second'"""

        first, second = mock.Mock(), mock.Mock()
        first.name, second.name = "first", "second"
        with mock.patch("infield_fly.jobs.DOWNLOADER_CLASSES",
                        (mock.Mock(return_value=first), mock.Mock(return_value=second))):
            yield first, second

    def test_default_tracks(self, queue):
        assert [type(x) for x in queue.downloaders] == [NzbDownloader, TorrentDownloader]

    def test_get_downloader(self, queue, tracks):
        first, second = tracks
        first.recognizes_download.side_effect = lambda location: location == "a"
        second.recognizes_download.side_effect = lambda location: location == "b"

        assert queue.get_downloader("a") is first
        assert queue.get_downloader("b") is second
        assert queue.get_downloader("c") is None

    def test_get_downloader_for_real_tracks(self, queue):
        assert isinstance(queue.get_downloader("magnet:?xt=x"), TorrentDownloader)
        assert isinstance(queue.get_downloader("https://indexer/x.nzb"), NzbDownloader)
        assert queue.get_downloader("nonsense") is None

    def test_search_with_no_jobs(self, queue, tracks, caplog):
        caplog.set_level("INFO")
        add_job(queue, JobStatus.ADDING)

        queue.search_downloads()

        assert "No waiting jobs to search for" in caplog.text
        tracks[0].search_downloads.assert_not_called()

    def test_search_offers_remaining_jobs_to_each_track_in_turn(self, queue, tracks):
        first, second = tracks
        found = add_job(queue, JobStatus.WAITING, query="found")
        add_job(queue, JobStatus.SEARCHING, query="interrupted")
        add_job(queue, JobStatus.ADDING, query="adding")

        def find(jobs):
            for job in jobs:
                if job.job_id == found.job_id:
                    job.status = JobStatus.ADDING

        first.search_downloads.side_effect = find

        queue.search_downloads()

        assert sorted(x.query for x in first.search_downloads.call_args[0][0]) == [
            "found", "interrupted"]
        assert [x.query for x in second.search_downloads.call_args[0][0]] == ["interrupted"]

    @pytest.mark.parametrize("phase, status, track_method", [
        ("add_downloads", JobStatus.ADDING, "add_downloads"),
        ("query_downloads_status", JobStatus.DOWNLOADING, "query_downloads"),
    ])
    def test_phases_give_each_track_its_jobs(self, queue, tracks, caplog, phase, status,
                                            track_method):
        first, second = tracks
        add_job(queue, status, query="first job", downloader="first")
        add_job(queue, status, query="unknown job", downloader="unknown")
        add_job(queue, JobStatus.WAITING, query="other status", downloader="first")

        getattr(queue, phase)()

        assert [x.query for x in getattr(first, track_method).call_args[0][0]] == [
            "first job"]
        getattr(second, track_method).assert_called_once_with([])
        assert "has unknown downloader 'unknown'" in caplog.text


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
            def convert_file(_):
                base_name, _ext = os.path.splitext(dest_file)
                output_files = [dest_file] + [f"{base_name}{x}" for x in subtitle_files]
                for output_file in output_files:
                    Path(output_file).touch()
                return output_files

            converter = mock.Mock()
            converter.convert_file.side_effect = convert_file
            converters.append(converter)
            return converter

        subtitle_files = []
        with mock.patch("infield_fly.jobs.Converter",
                        side_effect=create_converter) as converter_class:
            converter_class.created = converters
            converter_class.subtitle_files = subtitle_files
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

    def test_moves_subtitle_files_to_final_directory(self, queue, config, tmp_path,
                                                      converter_class):
        converter_class.subtitle_files.extend([".eng.forced.srt", ".eng.srt"])
        (tmp_path / "Show.S01E02.mkv").touch()
        job = add_job(queue, JobStatus.CONVERTING, name="Show.S01E02.mkv",
                      download_directory=str(tmp_path), converted_file_name=PLEX_NAME)

        queue.convert_downloaded_files(job, False)

        for extension in (".mp4", ".eng.forced.srt", ".eng.srt"):
            assert os.path.exists(os.path.join(config.conversion.final_directory,
                                               f"{PLEX_NAME}{extension}"))
            assert not os.path.exists(os.path.join(config.conversion.staging_directory,
                                                   f"{PLEX_NAME}{extension}"))

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
                     "download_directory", "converted_file_name", "nzb_guid", "nzb_file",
                     "nzo_id", "episode_key"):
            assert getattr(job, name) is None
        assert job.downloader == "deluge"
        assert job.rejected == []
        assert job.fallback_queries == []
        assert job.search_queries == [None]

    def test_properties_round_trip(self, tmp_path):
        job = Job(str(tmp_path), {"id": "abc", "status": "pending", "added": "2020-01-01",
                                  "download_only": True})
        values = {"keyword": "k", "query": "q", "magnet_link": "m", "title": "t", "name": "n",
                  "torrent_hash": "h", "download_directory": "d", "converted_file_name": "c",
                  "downloader": "sabnzbd", "nzb_guid": "g", "nzb_file": "f", "nzo_id": "z",
                  "rejected": ["r"], "fallback_queries": ["f"],
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

    @pytest.mark.parametrize("query, expected", [
        ("show 1080p s01e02", ("show", "s01e02")),
        ("show S1E2", ("show", "s01e02")),
        ("show special", None),
    ])
    def test_episode_key(self, tmp_path, query, expected):
        assert Job(str(tmp_path), {"keyword": "show", "query": query}).episode_key == expected

    def test_set_download_status(self, tmp_path):
        job = Job(str(tmp_path), {})

        job.set_download_status(JobStatus.DOWNLOADING, "name", "/downloads")
        job.set_download_status(JobStatus.PENDING, "renamed")

        assert (job.status, job.name, job.download_directory) == (
            JobStatus.PENDING, "renamed", "/downloads")

    def test_delete_removes_nzb_file(self, tmp_path):
        nzb_file = tmp_path / "job.nzb"
        nzb_file.write_bytes(b"<nzb/>")
        job = Job(str(tmp_path / "jobs"), {"nzb_file": str(nzb_file)})
        job.save(logging.getLogger())

        job.delete()

        assert not nzb_file.exists()
        assert not os.path.exists(job.file_path)

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
