"""Tests for the web interface application"""

import json
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from unittest import mock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import make_episode_db
from web.conftest import ROOT, SAME_ORIGIN, make_settings
from infield_fly.cli import load_config
from infield_fly.concurrency import ProcessLock
from infield_fly.database import EpisodeInfo
from infield_fly.jobs import Job, JobStatus
from infield_fly.settings_file import SettingsFile
from infield_fly.web.app import create_app

# The test client connects WebSockets to an absolute URL, ignoring its base URL.
WEBSOCKET_URL = f"wss://seed.example{ROOT}/ws"
TODAY = datetime.combine(datetime.now().date(), datetime.min.time())


def add_job(config, status, **values):
    job = Job.from_dict(config.conversion.job_directory, {"status": status.value, **values})
    job.save()
    return job


class TestAccessControl:

    @pytest.mark.parametrize("path", ["/", "/jobs", "/jobs/abc", "/series"])
    def test_pages_require_login(self, client, path):
        response = client.get(path, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == f"{ROOT}/login?next={ROOT}{path}"

    def test_static_files_and_login_page_are_public(self, client):
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/login").status_code == 200

    def test_version_in_footer(self, client):
        with mock.patch.dict("infield_fly.web.templating.templates.env.globals",
                             {"version": "1.2.3"}):
            text = client.get("/login").text

        assert '<footer class="site-footer">Infield Fly 1.2.3</footer>' in text

    def test_security_headers(self, client):
        response = client.get("/login")

        assert response.headers["content-security-policy"].startswith("default-src 'self'")
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize("headers", [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/page"},
    ])
    def test_changes_from_other_sites_are_refused(self, logged_in, headers):
        response = logged_in.post("/logout", headers=headers, follow_redirects=False)

        assert response.status_code == 403
        assert logged_in.get("/jobs", follow_redirects=False).status_code == 200

    def test_referer_from_this_site_is_accepted(self, logged_in):
        response = logged_in.post("/logout", headers={"Referer": "https://seed.example/x"},
                                  follow_redirects=False)

        assert response.status_code == 303

    def test_websocket_requires_login(self, app, client):
        async def echo(websocket):
            await websocket.accept()
            await websocket.send_text("connected")
            await websocket.close()

        app.router.add_websocket_route("/ws", echo)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(WEBSOCKET_URL, headers=SAME_ORIGIN):
                pass

        client.post("/login", data={"user_name": "me", "password": "secret"},
                    headers=SAME_ORIGIN)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(WEBSOCKET_URL,
                                          headers={"Origin": "https://evil.example"}):
                pass
        with client.websocket_connect(WEBSOCKET_URL, headers=SAME_ORIGIN) as websocket:
            assert websocket.receive_text() == "connected"


class TestLogin:

    def test_login_sets_restricted_session_cookie(self, client, authenticator):
        response = client.post(f"/login?next={ROOT}/series",
                               data={"user_name": "me", "password": "secret"},
                               headers=SAME_ORIGIN, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == f"{ROOT}/series"
        assert authenticator.calls == [("me", "secret", "testclient")]
        cookie = SimpleCookie(response.headers["set-cookie"])["infield_fly_session"]
        assert cookie["path"] == ROOT
        assert cookie["secure"] and cookie["httponly"]
        assert cookie["samesite"].lower() == "lax"

    def test_wrong_password(self, client):
        response = client.post("/login", data={"user_name": "me", "password": "wrong"},
                               headers=SAME_ORIGIN)

        assert response.status_code == 401
        assert "Incorrect user name or password." in response.text
        assert 'value="me"' in response.text
        assert client.get("/jobs", follow_redirects=False).status_code == 303

    def test_too_many_failures(self, client, authenticator):
        authenticator.limited = True

        response = client.post("/login", data={"user_name": "me", "password": "wrong"},
                               headers=SAME_ORIGIN)

        assert response.status_code == 429
        assert "Too many failed logins" in response.text

    @pytest.mark.parametrize("target", [
        "https://evil.example/", "//evil.example/", f"{ROOT}\\..\\evil", "/other-app",
        f"{ROOT}/login", f"{ROOT}/logout", ""])
    def test_redirect_after_login_stays_in_application(self, client, target):
        response = client.post("/login", params={"next": target},
                               data={"user_name": "me", "password": "secret"},
                               headers=SAME_ORIGIN, follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/jobs"

    def test_login_page_when_logged_in_redirects(self, logged_in):
        response = logged_in.get("/login", follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/jobs"

    def test_logout(self, logged_in):
        response = logged_in.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/login"
        assert logged_in.get("/jobs", follow_redirects=False).status_code == 303


class TestJobs:

    def test_home_shows_jobs(self, logged_in):
        response = logged_in.get("/", follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/jobs"

    def test_lists_jobs_in_processing_order(self, logged_in, config):
        add_job(config, JobStatus.COMPLETED, query="done s01e01", converted_file_name="Done")
        add_job(config, JobStatus.WAITING, query="show s01e02", keyword="show")
        add_job(config, JobStatus.DOWNLOADING, query="show s01e03", name="Show.S01E03")

        response = logged_in.get("/jobs")

        text = response.text
        assert (text.index("Waiting to search for &#39;show s01e02&#39;")
                < text.index("Downloading &#39;Show.S01E03&#39;")
                < text.index("Completed conversion to &#39;Done&#39;"))
        assert "All <span class=\"count\">3</span>" in text
        assert "Waiting <span class=\"count\">1</span>" in text
        assert "Pending <span" not in text

    def test_filters_by_status(self, logged_in, config):
        add_job(config, JobStatus.WAITING, query="show s01e02")
        add_job(config, JobStatus.PENDING, query="show s01e03", name="Show.S01E03")

        text = logged_in.get("/jobs", params={"status": "Pending"}).text

        assert "Show.S01E03" in text
        assert "show s01e02" not in text

    def test_unknown_status_filter(self, logged_in):
        response = logged_in.get("/jobs", params={"status": "bogus"})

        assert response.status_code == 400

    def test_no_jobs(self, logged_in):
        assert "No pending jobs." in logged_in.get("/jobs?status=pending").text

    def test_job_values_are_escaped(self, logged_in, config):
        add_job(config, JobStatus.WAITING, query="<script>alert(1)</script>")

        text = logged_in.get("/jobs").text

        assert "<script>alert" not in text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text

    def test_shows_job_processing_in_progress(self, logged_in, config):
        assert "Job processing is running" not in logged_in.get("/jobs").text

        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            text = logged_in.get("/jobs").text

        assert "Job processing is running (jobs process, process" in text

    def test_shows_job_details(self, logged_in, config):
        job = add_job(config, JobStatus.DOWNLOADING, keyword="show", query="show s01e02",
                      fallback_queries=["show 720p s01e02"],
                      release_groups={"show s01e02": ["flux", "ntb"]},
                      title="Show.S01E02.1080p", nzb_guid="guid-1", nzo_id="SABnzbd_1",
                      download_directory="/downloads", name="Show.S01E02",
                      converted_file_name="The Show - s01e02 - Pilot", rejected=["guid-0"])

        text = logged_in.get(f"/jobs/{job.job_id}").text

        for expected in [job.job_id, "show s01e02", "[release groups: flux, ntb]",
                         "show 720p s01e02", "Show.S01E02.1080p", "guid-1", "SABnzbd_1",
                         "/downloads/Show.S01E02", "The Show - s01e02 - Pilot.mp4", "guid-0"]:
            assert expected in text

    def test_missing_job(self, logged_in):
        response = logged_in.get("/jobs/missing")

        assert response.status_code == 404
        assert "No existing job with ID 'missing'" in response.text


class TestSeries:

    def test_shows_status_from_episode_database(self, logged_in, config):
        database = make_episode_db(config, [
            EpisodeInfo(11, "The Show", season_number=1, episode_number=1, title="Pilot",
                        airdate=TODAY - timedelta(days=7)),
            EpisodeInfo(12, "The Show", season_number=1, episode_number=2, title="Second",
                        airdate=TODAY),
            EpisodeInfo(13, "The Show", season_number=1, episode_number=3, title="Third",
                        airdate=TODAY + timedelta(days=1)),
            EpisodeInfo(14, "The Show", season_number=1, episode_number=4, title="Fourth",
                        airdate=TODAY + timedelta(days=8)),
            EpisodeInfo(15, "The Show", season_number=1, episode_number=5, title="Unaired"),
        ])
        database.save_to_cache()

        with mock.patch("infield_fly.database.TVMetadataProvider.get_series") as get_series:
            text = logged_in.get("/series").text

        get_series.assert_not_called()
        assert "Episode database last updated never." not in text
        # Series are listed by name, so "Missing" comes before "The Show".
        missing = text[text.index("Missing"):text.index("The Show")]
        the_show = text[text.index("The Show"):]
        assert "Continuing" in the_show
        assert "(2020)" in the_show
        assert "<dd>5</dd>" in the_show
        assert "s01e02 · Second\n<span class=\"muted\">(today)</span>" in the_show
        assert "s01e03 · Third\n<span class=\"muted\">(tomorrow)</span>" in the_show
        assert "2 search configs" in the_show
        assert "not in database" in missing
        # Searching enabled without search configs searches with the main keyword alone.
        assert "1 search config</dd>" in missing

    def test_no_episode_database(self, logged_in):
        text = logged_in.get("/series").text

        assert "Episode database last updated never." in text
        assert text.count("not in database") == 2

    def test_no_tracked_series(self, tmp_path, authenticator):
        settings_path = tmp_path / "empty.json"
        settings_path.write_text(json.dumps(make_settings(tmp_path, {})), encoding="utf-8")
        app = create_app(lambda: load_config(str(settings_path)), authenticator,
                         "test-secret", settings_file=SettingsFile(str(settings_path)))
        with TestClient(app, base_url="https://seed.example") as client:
            client.post("/login", data={"user_name": "me", "password": "secret"},
                        headers=SAME_ORIGIN)
            text = client.get("/series").text

        assert "No tracked series in the settings file." in text
