"""Tests for the job_operations module"""

import os
from unittest import mock

import pytest

from conftest import TRACKED_SERIES
from infield_fly.concurrency import ProcessLock, read_lock_holder
from infield_fly.job_operations import (JobOperationError, add_job, changing_jobs,
                                        set_job_status)
from infield_fly.jobs import Job, JobQueue, JobStatus

VALID_NZB = (b'<nzb><file subject=\'"Show.S01E02.mkv" yEnc (1/1)\'><segments>'
             b'<segment bytes="10" number="1">a@b</segment></segments></file></nzb>')


@pytest.fixture
def config(make_config):
    return make_config(metadata={"tracked_series": TRACKED_SERIES})


@pytest.fixture
def queue(config):
    return JobQueue(config)


@pytest.fixture
def job(config):
    job = Job.from_dict(config.conversion.job_directory,
                        {"status": "waiting", "keyword": "show", "query": "show 1080p s01e02"})
    job.save()
    return job


def reload(queue, job):
    return queue.get_job_by_id(job.job_id)


class TestChangingJobs:

    def test_holds_job_processing_lock(self, config):
        with changing_jobs(config):
            assert read_lock_holder(config.conversion.job_lock_file)["description"] == (
                "job change")

        assert read_lock_holder(config.conversion.job_lock_file) is None

    def test_refused_while_processing(self, config):
        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            with pytest.raises(JobOperationError) as error:
                with changing_jobs(config):
                    pytest.fail("Changed jobs while processing")

        assert "Job processing is running" in str(error.value)

    def test_errors_while_changing_are_not_reported_as_processing(self, config):
        with pytest.raises(KeyError):
            with changing_jobs(config):
                raise KeyError("x")

        assert read_lock_holder(config.conversion.job_lock_file) is None


class TestSetJobStatus:

    def test_status_only(self, queue, job):
        assert set_job_status(queue, job, JobStatus.COMPLETED) == []

        assert reload(queue, job).status == JobStatus.COMPLETED

    @pytest.mark.parametrize("status", [None, JobStatus.UNKNOWN])
    def test_unknown_status(self, queue, job, status):
        with pytest.raises(JobOperationError, match="Unknown job status."):
            set_job_status(queue, job, status)

    def test_adding_with_magnet_link(self, queue, job):
        set_job_status(queue, job, JobStatus.ADDING, "magnet:?xt=urn:btih:ABC&dn=Show")

        assert reload(queue, job).magnet_link == "magnet:?xt=urn:btih:ABC&dn=Show"

    def test_adding_with_nzb_file(self, queue, job, tmp_path):
        nzb = tmp_path / "Show.S01E02.nzb"
        nzb.write_bytes(VALID_NZB)

        set_job_status(queue, job, JobStatus.ADDING, str(nzb))

        assert reload(queue, job).status == JobStatus.ADDING

    def test_adding_returns_warnings(self, queue, job):
        with mock.patch("infield_fly.torrent.downloader.TorrentDownloader.set_download",
                        return_value=["odd"]):
            assert set_job_status(queue, job, JobStatus.ADDING, "magnet:?x") == ["odd"]

    def test_adding_with_unrecognized_download(self, queue, job):
        with pytest.raises(JobOperationError, match="give a magnet link"):
            set_job_status(queue, job, JobStatus.ADDING, "nonsense")

    def test_adding_with_invalid_nzb_file(self, queue, job, tmp_path):
        nzb = tmp_path / "bad.nzb"
        nzb.write_bytes(b"<nzb></nzb>")

        with pytest.raises(JobOperationError, match="NZB file failed validation"):
            set_job_status(queue, job, JobStatus.ADDING, str(nzb))

    def test_pending_with_episode_file(self, queue, job, tmp_path):
        episode = tmp_path / "Show.S01E02.mkv"
        episode.touch()

        set_job_status(queue, job, JobStatus.PENDING, str(episode))

        updated = reload(queue, job)
        assert (updated.status, updated.name, updated.download_directory) == (
            JobStatus.PENDING, "Show.S01E02.mkv", str(tmp_path))

    def test_pending_with_folder(self, queue, job, tmp_path):
        folder = tmp_path / "Show.S01E02.1080p"
        folder.mkdir()
        (folder / "show.s01e02.mkv").write_bytes(b"x" * 100)
        (folder / "sample-show.s01e03.mkv").write_bytes(b"x")

        set_job_status(queue, job, JobStatus.PENDING, f"{folder}/")

        updated = reload(queue, job)
        assert (updated.name, updated.download_directory) == (folder.name, str(tmp_path))

    def test_pending_with_largest_file_of_another_episode(self, queue, job, tmp_path):
        folder = tmp_path / "download"
        folder.mkdir()
        (folder / "show.s01e03.mkv").write_bytes(b"x" * 100)
        (folder / "show.s01e02.mkv").write_bytes(b"x")

        with pytest.raises(JobOperationError) as error:
            set_job_status(queue, job, JobStatus.PENDING, str(folder))

        assert str(error.value) == (
            "'show.s01e03.mkv' is episode s01e03, but this job is for s01e02.")
        assert reload(queue, job).status == JobStatus.WAITING

    def test_pending_for_job_without_episode_number(self, queue, config, tmp_path):
        special = Job.from_dict(config.conversion.job_directory,
                                {"status": "waiting", "keyword": "show", "query": "show special"})
        special.save()
        (tmp_path / "show.s00e01.mkv").touch()

        set_job_status(queue, special, JobStatus.PENDING, str(tmp_path / "show.s00e01.mkv"))

        assert reload(queue, special).status == JobStatus.PENDING

    @pytest.mark.parametrize("name, message", [
        ("missing.mkv", "does not exist."),
        ("notes.txt", "has no episode file to convert"),
    ])
    def test_pending_with_unusable_download(self, queue, job, tmp_path, name, message):
        (tmp_path / "notes.txt").touch()

        with pytest.raises(JobOperationError, match=message):
            set_job_status(queue, job, JobStatus.PENDING, str(tmp_path / name))

    def test_download_with_another_status(self, queue, job):
        with pytest.raises(JobOperationError, match="only be given for the adding or pending"):
            set_job_status(queue, job, JobStatus.WAITING, "magnet:?x")

        assert reload(queue, job).magnet_link is None


class TestAddJob:

    def test_adds_job(self, queue, config):
        job = add_job(queue, config, "show", " show 720p s01e05 ")

        assert [(x.keyword, x.query) for x in queue.load_jobs()] == [
            ("show", "show 720p s01e05")]
        assert job.job_id == queue.load_jobs()[0].job_id

    @pytest.mark.parametrize("keyword, query, message", [
        ("nope", "q", "'nope' is not the main keyword of a tracked series."),
        ("show", "  ", "Enter the search string."),
        ("show", "show 1080p s01e02", "A job for that series and search string already "
                                      "exists."),
    ])
    def test_refused(self, queue, config, job, keyword, query, message):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        with pytest.raises(JobOperationError) as error:
            add_job(queue, config, keyword, query)

        assert str(error.value) == message
        assert len(queue.load_jobs()) == 1
        assert os.path.exists(job.file_path)
