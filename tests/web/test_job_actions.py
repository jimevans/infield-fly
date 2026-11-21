"""Tests for the pages of the web interface that change, remove, and add jobs"""

import json
import logging
import os
import time
from unittest import mock
from urllib.parse import quote

import pytest
from web.conftest import ROOT, SAME_ORIGIN, make_settings

from infield_fly.concurrency import ProcessLock, read_lock_holder
from infield_fly.jobs import Job, JobQueue, JobStatus
from infield_fly.web import job_actions
from infield_fly.web.templating import format_size

VALID_NZB = (b'<nzb><file subject=\'"Show.S01E02.mkv" yEnc (1/1)\'><segments>'
             b'<segment bytes="10" number="1">a@b</segment></segments></file></nzb>')


@pytest.fixture
def downloads(app, tmp_path):
    """A folder from which downloads can be chosen, with a download in it"""

    root = tmp_path / "downloads"
    (root / "Show.S01E02").mkdir(parents=True)
    (root / "Show.S01E02" / "show.s01e02.mkv").write_bytes(b"x" * 2048)
    app.state.browse_roots = [os.path.realpath(root)]
    return root


def add_job(config, status=JobStatus.WAITING, **values):
    job = Job(config.conversion.job_directory,
              {"status": status.value, "keyword": "show", "query": "show s01e02", **values})
    job.save(logging.getLogger())
    return job


def reload(config, job):
    return JobQueue(config).get_job_by_id(job.job_id)


def update(client, job, status, download="", version=None):
    return client.post(f"/jobs/{job.job_id}/update", headers=SAME_ORIGIN,
                       follow_redirects=False,
                       data={"status": status, "download": download,
                             "version": version or job_actions.get_job_version(job)})


def flashes(client, job):
    return client.get(f"/jobs/{job.job_id}").text


class TestJobPage:

    def test_shows_change_forms(self, logged_in, config, downloads):
        job = add_job(config, rejected=["guid-1"])

        text = logged_in.get(f"/jobs/{job.job_id}").text

        assert '<option value="waiting" selected>waiting</option>' in text
        assert "unknown" not in text.split('id="status"')[1].split("</select>")[0]
        assert f'name="version" value="{job_actions.get_job_version(job)}"' in text
        assert f'href="{ROOT}/jobs/{job.job_id}/browse">choose one</a>' in text
        assert 'class="secondary">Reset rejected downloads' in text
        assert f'href="{ROOT}/jobs/{job.job_id}/remove">Remove job…</a>' in text

    def test_chosen_download_selects_pending(self, logged_in, config):
        job = add_job(config)

        text = logged_in.get(f"/jobs/{job.job_id}", params={"download": "/d/x.mkv"}).text

        assert '<option value="pending" selected>pending</option>' in text
        assert 'name="download" value="/d/x.mkv"' in text

    def test_without_browse_roots_or_rejections(self, logged_in, config):
        job = add_job(config)

        text = logged_in.get(f"/jobs/{job.job_id}").text

        assert "choose one" not in text
        assert 'class="secondary" disabled>Reset rejected downloads' in text

    def test_disabled_while_processing(self, logged_in, config):
        job = add_job(config)

        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            text = logged_in.get(f"/jobs/{job.job_id}").text

        assert "jobs can't be changed until it finishes" in text
        assert "<fieldset disabled>" in text

    @pytest.mark.parametrize("path", ["", "/browse", "/remove"])
    def test_missing_job(self, logged_in, path):
        assert logged_in.get(f"/jobs/missing{path}").status_code == 404

    @pytest.mark.parametrize("path", ["/update", "/reset", "/remove"])
    def test_missing_job_changes(self, logged_in, path):
        response = logged_in.post(f"/jobs/missing{path}", headers=SAME_ORIGIN)

        assert response.status_code == 404


class TestUpdateJob:

    def test_sets_status(self, logged_in, config):
        job = add_job(config, status=JobStatus.CONVERTING, name="Show.S01E02",
                      download_directory="/d")

        response = update(logged_in, job, "pending")

        assert response.headers["location"] == f"{ROOT}/jobs/{job.job_id}"
        updated = reload(config, job)
        assert updated.status == JobStatus.PENDING
        assert (updated.name, updated.download_directory) == ("Show.S01E02", "/d")
        assert "Set the status of the job to pending." in flashes(logged_in, job)

    def test_pending_with_chosen_file(self, logged_in, config, downloads):
        job = add_job(config)
        path = os.path.join(os.path.realpath(downloads), "Show.S01E02", "show.s01e02.mkv")

        update(logged_in, job, "pending", path)

        updated = reload(config, job)
        assert updated.status == JobStatus.PENDING
        assert updated.name == "show.s01e02.mkv"
        assert updated.download_directory == os.path.dirname(path)
        assert f"Set the job to pending conversion of {path}." in flashes(logged_in, job)

    def test_pending_with_chosen_folder(self, logged_in, config, downloads):
        job = add_job(config)

        update(logged_in, job, "pending", str(downloads / "Show.S01E02") + "/")

        updated = reload(config, job)
        assert updated.name == "Show.S01E02"
        assert updated.download_directory == str(downloads)

    @pytest.mark.parametrize("make_path, message", [
        (lambda root: "/etc/passwd", "must be in one of the folders"),
        (lambda root: str(root / "missing.mkv"), "does not exist"),
        (lambda root: str(root / "escape" / "file"), "must be in one of the folders"),
    ])
    def test_pending_download_refused(self, logged_in, config, downloads, tmp_path,
                                      make_path, message):
        (tmp_path / "outside").mkdir()
        (tmp_path / "outside" / "file").touch()
        (downloads / "escape").symlink_to(tmp_path / "outside")
        job = add_job(config)
        path = make_path(downloads)

        response = update(logged_in, job, "pending", path)

        assert response.headers["location"] == (
            f"{ROOT}/jobs/{job.job_id}?download={quote(path, safe='')}")
        assert reload(config, job).status == JobStatus.WAITING
        assert message in flashes(logged_in, job)

    def test_pending_download_without_browse_roots(self, logged_in, config):
        job = add_job(config)

        update(logged_in, job, "pending", "/anything")

        assert reload(config, job).status == JobStatus.WAITING

    def test_adding_with_magnet_link(self, logged_in, config):
        job = add_job(config)

        update(logged_in, job, "adding", "magnet:?xt=urn:btih:ABCDEF&dn=Show.S01E02")

        updated = reload(config, job)
        assert updated.status == JobStatus.ADDING
        assert updated.magnet_link == "magnet:?xt=urn:btih:ABCDEF&dn=Show.S01E02"

    def test_adding_with_nzb_file(self, logged_in, config, tmp_path):
        nzb = tmp_path / "Show.S01E02.nzb"
        nzb.write_bytes(VALID_NZB)
        job = add_job(config)

        update(logged_in, job, "adding", str(nzb))

        updated = reload(config, job)
        assert updated.status == JobStatus.ADDING
        assert updated.downloader == "sabnzbd"
        assert f"Set the download of the job to {nzb}." in flashes(logged_in, job)

    def test_adding_with_warnings(self, logged_in, config):
        job = add_job(config)

        with mock.patch("infield_fly.torrent.downloader.TorrentDownloader.set_download",
                        return_value=["looks odd"]):
            update(logged_in, job, "adding", "magnet:?xt=urn:btih:ABCDEF")

        assert "Warning: looks odd" in flashes(logged_in, job)

    def test_adding_with_invalid_nzb_file(self, logged_in, config, tmp_path):
        nzb = tmp_path / "bad.nzb"
        nzb.write_bytes(b"<nzb></nzb>")
        job = add_job(config)

        update(logged_in, job, "adding", str(nzb))

        assert reload(config, job).status == JobStatus.WAITING
        assert "NZB file failed validation" in flashes(logged_in, job)

    def test_adding_with_unrecognized_download(self, logged_in, config):
        job = add_job(config)

        update(logged_in, job, "adding", "not a download")

        assert reload(config, job).status == JobStatus.WAITING
        assert "give a magnet link, or the path or URL of an NZB file" in (
            flashes(logged_in, job))

    def test_unknown_status(self, logged_in, config):
        job = add_job(config)

        update(logged_in, job, "bogus")

        assert reload(config, job).status == JobStatus.WAITING
        assert "Unknown status &#39;bogus&#39;." in flashes(logged_in, job)

    def test_job_changed_since_page_opened(self, logged_in, config):
        job = add_job(config)
        version = job_actions.get_job_version(job)
        job.query = "changed s01e02"
        job.save(logging.getLogger())

        update(logged_in, job, "completed", version=version)

        assert reload(config, job).status == JobStatus.WAITING
        assert "The job was changed by something else" in flashes(logged_in, job)

    def test_refused_while_processing(self, logged_in, config):
        job = add_job(config)

        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            update(logged_in, job, "completed")

        assert reload(config, job).status == JobStatus.WAITING
        assert "Job processing is running" in flashes(logged_in, job)

    def test_refused_while_run_started_here_is_starting(self, logged_in, app, config):
        job = add_job(config)
        process = mock.Mock(pid=1)
        process.poll.return_value = None
        app.state.started_runs = {"jobs": {"process": process, "pid": 1,
                                           "time": time.monotonic()}}

        update(logged_in, job, "completed")

        assert reload(config, job).status == JobStatus.WAITING

    def test_refused_if_run_starts_first(self, logged_in, config):
        job = add_job(config)

        with mock.patch.object(job_actions, "get_run_status", return_value={"state": "idle"}), \
                ProcessLock(config.conversion.job_lock_file, "jobs process"):
            update(logged_in, job, "completed")

        assert reload(config, job).status == JobStatus.WAITING

    def test_holds_lock_while_changing(self, logged_in, config):
        job = add_job(config)
        holders = []

        async def record_holder(*_):
            holders.append(read_lock_holder(config.conversion.job_lock_file))
            return []

        with mock.patch.object(job_actions, "apply_status", side_effect=record_holder):
            update(logged_in, job, "completed")

        assert holders[0]["description"] == "web interface job change"
        assert read_lock_holder(config.conversion.job_lock_file) is None


class TestResetAndRemove:

    def test_reset_clears_rejections_for_episode(self, logged_in, config):
        job = add_job(config, rejected=["guid-1"])
        other = add_job(config, query="show 720p s01e02", rejected=["guid-1", "guid-2"])

        response = logged_in.post(f"/jobs/{job.job_id}/reset", headers=SAME_ORIGIN,
                                  follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/jobs/{job.job_id}"
        assert reload(config, job).rejected == []
        assert reload(config, other).rejected == []
        assert "Cleared the rejected downloads of 2 jobs for this episode." in (
            flashes(logged_in, job))

    def test_reset_of_one_job(self, logged_in, config):
        job = add_job(config, query="no episode number", rejected=["guid-1"])

        text = logged_in.post(f"/jobs/{job.job_id}/reset", headers=SAME_ORIGIN).text

        assert "Cleared the rejected downloads of 1 job for this episode." in text

    def test_reset_refused_while_processing(self, logged_in, config):
        job = add_job(config, rejected=["guid-1"])

        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            logged_in.post(f"/jobs/{job.job_id}/reset", headers=SAME_ORIGIN)

        assert reload(config, job).rejected == ["guid-1"]

    def test_remove_confirmation(self, logged_in, config):
        job = add_job(config)

        text = logged_in.get(f"/jobs/{job.job_id}/remove").text

        assert "Remove this job?" in text
        assert "Waiting to search for &#39;show s01e02&#39;" in text

    def test_remove(self, logged_in, config):
        job = add_job(config)

        response = logged_in.post(f"/jobs/{job.job_id}/remove", headers=SAME_ORIGIN,
                                  follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/jobs"
        assert reload(config, job) is None
        assert "Removed the job for &#39;show s01e02&#39;." in logged_in.get("/jobs").text

    def test_remove_refused_while_processing(self, logged_in, config):
        job = add_job(config)

        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            response = logged_in.post(f"/jobs/{job.job_id}/remove", headers=SAME_ORIGIN,
                                      follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/jobs/{job.job_id}"
        assert reload(config, job) is not None


class TestAddJob:

    def test_form_lists_series_by_name(self, logged_in, settings_path, tmp_path):
        settings_path.write_text(json.dumps(make_settings(tmp_path, {
            "show": {"id": 1, "description": "The Show"},
            "another": {"id": 2, "description": "another Show"},
            "zebra": {"id": 3, "description": "A Zebra"},
        })), encoding="utf-8")

        text = logged_in.get("/jobs/add").text

        assert (text.index('<option value="zebra">A Zebra</option>')
                < text.index('<option value="another">another Show</option>')
                < text.index('<option value="show">The Show</option>'))
        assert f'href="{ROOT}/jobs/add">Add job</a>' in logged_in.get("/jobs").text

    def test_series_with_same_name_show_keywords(self, logged_in, settings_path, tmp_path):
        settings_path.write_text(json.dumps(make_settings(tmp_path, {
            "show": {"id": 1, "description": "The Show"},
            "show us": {"id": 2, "description": "the show"},
        })), encoding="utf-8")

        text = logged_in.get("/jobs/add").text

        assert '<option value="show">The Show (show)</option>' in text
        assert '<option value="show us">the show (show us)</option>' in text

    def test_chosen_series_stays_selected(self, logged_in):
        text = logged_in.post("/jobs/add", headers=SAME_ORIGIN,
                              data={"keyword": "missing", "query": " "}).text

        assert '<option value="missing" selected>Missing</option>' in text

    def test_adds_job(self, logged_in, config):
        response = logged_in.post("/jobs/add", headers=SAME_ORIGIN, follow_redirects=False,
                                  data={"keyword": "show", "query": " show 1080p s01e05 "})

        jobs = JobQueue(config).load_jobs()
        assert [(x.keyword, x.query, x.status) for x in jobs] == [
            ("show", "show 1080p s01e05", JobStatus.WAITING)]
        assert response.headers["location"] == f"{ROOT}/jobs/{jobs[0].job_id}"
        assert "Added a job searching for &#39;show 1080p s01e05&#39;." in (
            flashes(logged_in, jobs[0]))

    @pytest.mark.parametrize("data, message", [
        ({"keyword": "nope", "query": "q"}, "Choose a tracked series."),
        ({"keyword": "show", "query": " "}, "Enter the search string."),
        ({"keyword": "show", "query": "show s01e02"},
         "A job for that series and search string already exists."),
    ])
    def test_invalid(self, logged_in, config, data, message):
        add_job(config)

        response = logged_in.post("/jobs/add", headers=SAME_ORIGIN, data=data)

        assert response.status_code == 400
        assert message in response.text
        assert len(JobQueue(config).load_jobs()) == 1

    def test_refused_while_processing(self, logged_in, config):
        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            response = logged_in.post("/jobs/add", headers=SAME_ORIGIN,
                                      data={"keyword": "show", "query": "show s01e09"})

        assert response.status_code == 400
        assert "Job processing is running" in response.text
        assert JobQueue(config).load_jobs() == []

    def test_no_tracked_series(self, logged_in, settings_path):
        settings_path.write_text("{}", encoding="utf-8")

        assert "Jobs are for tracked series, and there are none." in (
            logged_in.get("/jobs/add").text)


class TestBrowse:

    def test_lists_browse_roots(self, logged_in, config, downloads):
        job = add_job(config)

        text = logged_in.get(f"/jobs/{job.job_id}/browse").text

        root = os.path.realpath(downloads)
        assert f"?path={quote(root, safe='')}" in text

    def test_no_browse_roots(self, logged_in, config):
        job = add_job(config)

        assert "Set BROWSE_ROOTS in deploy/deploy.env" in (
            logged_in.get(f"/jobs/{job.job_id}/browse").text)

    def test_lists_folder(self, logged_in, config, downloads):
        (downloads / "a-file.txt").write_text("x", encoding="utf-8")
        (downloads / ".hidden").touch()
        job = add_job(config)
        root = os.path.realpath(downloads)

        text = logged_in.get(f"/jobs/{job.job_id}/browse", params={"path": root}).text

        assert text.index("📁 Show.S01E02") < text.index("a-file.txt")
        assert ".hidden" not in text
        assert "1 bytes" in text
        assert "↑ All folders" in text
        assert f"?download={quote(root, safe='')}\">Choose this folder" in text

    def test_lists_subfolder_with_episode(self, logged_in, config, downloads):
        job = add_job(config)
        folder = os.path.join(os.path.realpath(downloads), "Show.S01E02")

        text = logged_in.get(f"/jobs/{job.job_id}/browse", params={"path": folder}).text

        assert '<li class="episode">' in text
        assert "2.0 KB" in text
        assert f"?path={quote(os.path.realpath(downloads), safe='')}\">↑ Up" in text
        file_path = quote(os.path.join(folder, "show.s01e02.mkv"), safe="")
        assert f'href="{ROOT}/jobs/{job.job_id}?download={file_path}"' in text

    def test_empty_folder(self, logged_in, config, downloads):
        (downloads / "empty").mkdir()
        job = add_job(config)

        text = logged_in.get(f"/jobs/{job.job_id}/browse",
                             params={"path": str(downloads / "empty")}).text

        assert "This folder is empty." in text

    @pytest.mark.parametrize("make_path", [
        lambda root, tmp_path: "/etc",
        lambda root, tmp_path: str(root / ".."),
        lambda root, tmp_path: str(root / "escape"),
        lambda root, tmp_path: str(root / "Show.S01E02" / "show.s01e02.mkv"),
    ])
    def test_refuses_other_folders(self, logged_in, config, downloads, tmp_path, make_path):
        (tmp_path / "outside").mkdir()
        (downloads / "escape").symlink_to(tmp_path / "outside")
        job = add_job(config)

        response = logged_in.get(f"/jobs/{job.job_id}/browse",
                                 params={"path": make_path(downloads, tmp_path)})

        assert response.status_code == 404

    def test_unreadable_entries_are_skipped(self, logged_in, config, downloads):
        (downloads / "broken").symlink_to(downloads / "missing")
        job = add_job(config)

        text = logged_in.get(f"/jobs/{job.job_id}/browse",
                             params={"path": str(downloads)}).text

        assert "broken" not in text


@pytest.mark.parametrize("size, expected", [
    (0, "0 bytes"), (1023, "1023 bytes"), (1536, "1.5 KB"), (5 * 1024 ** 2, "5.0 MB"),
    (3 * 1024 ** 3, "3.0 GB"), (2048 * 1024 ** 3, "2048.0 GB"),
])
def test_format_size(size, expected):
    assert format_size(size) == expected


def test_version_of_deleted_job(config):
    job = add_job(config)
    job.delete()

    assert job_actions.get_job_version(job) == ""
