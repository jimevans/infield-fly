"""Tests for the job processing page of the web interface"""

import asyncio
import json
import os
import sys
import time
from unittest import mock

import pytest
from starlette.websockets import WebSocketDisconnect
from web.conftest import ROOT, SAME_ORIGIN, make_settings

from infield_fly.concurrency import ProcessLock
from infield_fly.web import processing
from infield_fly.web.processing import LogFollower, get_run_status, start_run

WEBSOCKET_URL = f"wss://seed.example{ROOT}/processing/log"
ALL_PHASES = {name: "on" for name, _ in processing.PHASES}


class FakeProcess:

    def __init__(self, pid=12345, return_code=None):
        self.pid = pid
        self.return_code = return_code
        self.polled = 0

    def poll(self):
        self.polled += 1
        return self.return_code


def write_log(config, *lines, mode="a"):
    os.makedirs(config.conversion.log_directory, exist_ok=True)
    with open(config.conversion.log_file, mode, encoding="utf-8") as log_file:
        log_file.writelines(f"{x}\n" for x in lines)


@pytest.fixture
def fast(app):
    """Makes the log follower poll and send heartbeats quickly"""

    app.state.log_poll_seconds = 0.01
    with mock.patch.object(processing, "HEARTBEAT_SECONDS", 0.05):
        yield


class TestLogFollower:

    def test_missing_file(self, tmp_path):
        follower = LogFollower(str(tmp_path / "missing.log"))

        assert follower.start(10) == []
        assert follower.read_new() == []

    def test_start_returns_last_lines(self, tmp_path):
        path = tmp_path / "log"
        path.write_text("".join(f"line {x}\n" for x in range(5)), encoding="utf-8")

        assert LogFollower(str(path)).start(2) == ["line 3", "line 4"]

    def test_follows_complete_lines(self, tmp_path):
        path = tmp_path / "log"
        path.write_text("first\n", encoding="utf-8")
        follower = LogFollower(str(path))
        follower.start(10)

        with open(path, "a", encoding="utf-8") as log_file:
            log_file.write("second\nthi")
        assert follower.read_new() == ["second"]
        assert follower.read_new() == []
        with open(path, "a", encoding="utf-8") as log_file:
            log_file.write("rd\r\n")
        assert follower.read_new() == ["third"]

    def test_follows_rotated_file(self, tmp_path):
        path = tmp_path / "log"
        path.write_text("old\n", encoding="utf-8")
        follower = LogFollower(str(path))
        follower.start(10)

        os.rename(path, tmp_path / "log.1")
        path.write_text("new\n", encoding="utf-8")

        assert follower.read_new() == ["new"]

    def test_follows_truncated_file(self, tmp_path):
        path = tmp_path / "log"
        path.write_text("a long first line\n", encoding="utf-8")
        follower = LogFollower(str(path))
        follower.start(10)

        with open(path, "r+", encoding="utf-8") as log_file:
            log_file.truncate(0)
            log_file.write("short\n")

        assert follower.read_new() == ["short"]

    def test_invalid_text_is_replaced(self, tmp_path):
        path = tmp_path / "log"
        path.write_bytes(b"bad \xff byte\n")

        assert LogFollower(str(path)).start(10) == ["bad � byte"]


class TestRunStatus:

    def test_idle(self, app, config):
        assert get_run_status(app, config) == {"state": "idle"}

    def test_scheduled_run(self, app, config):
        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            status = get_run_status(app, config)

        assert status["state"] == "running"
        assert status["pid"] == os.getpid()
        assert status["from_web"] is False

    def test_run_started_here(self, app, config):
        app.state.started_runs = {"jobs": {"process": FakeProcess(os.getpid()),
                                           "pid": os.getpid(), "time": time.monotonic()}}

        assert get_run_status(app, config) == {"state": "starting"}
        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            assert get_run_status(app, config)["from_web"] is True

    def test_run_that_never_acquired_the_lock(self, app, config):
        app.state.started_runs = {"jobs": {
            "process": FakeProcess(), "pid": 12345,
            "time": time.monotonic() - processing.STARTING_SECONDS - 1}}

        assert get_run_status(app, config) == {"state": "idle"}

    def test_run_that_exited(self, app, config):
        app.state.started_runs = {"jobs": {"process": FakeProcess(return_code=0),
                                           "pid": 12345, "time": time.monotonic()}}

        assert get_run_status(app, config) == {"state": "idle"}


class TestStartRun:

    def test_starts_detached_run(self, app, config):
        previous = FakeProcess()
        app.state.started_runs = {"jobs": {"process": previous, "pid": 1, "time": 0}}

        with mock.patch("subprocess.Popen") as popen:
            start_run(app, config, "/settings.json", "jobs", ["jobs", "process", "--no-search"])

        args, options = popen.call_args
        assert args[0] == [sys.executable, "-m", "infield_fly", "-u", "-c", "/settings.json",
                           "jobs", "process", "--no-search"]
        assert options["start_new_session"] is True
        assert options["stdout"].name == processing.get_error_file(config)
        assert app.state.started_runs["jobs"]["process"] is popen.return_value
        assert previous.polled == 1

    def test_runs_of_each_kind_are_tracked_separately(self, app, config):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            start_run(app, config, "/settings.json", "database", ["database", "update"])

        assert popen.call_args[1]["stdout"].name == processing.get_error_file(config,
                                                                              "database")
        assert set(app.state.started_runs) == {"database"}
        assert get_run_status(app, config, "database") == {"state": "starting"}
        assert get_run_status(app, config) == {"state": "idle"}

    def test_real_run_completes(self, logged_in, app, config):
        response = logged_in.post("/processing", data={"query-downloads": "on"},
                                  headers=SAME_ORIGIN, follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/processing"
        process = app.state.started_runs["jobs"]["process"]
        assert process.wait(timeout=60) == 0
        assert get_run_status(app, config) == {"state": "idle"}
        # Libraries may print warnings, such as about the SSL library, but the run must not
        # crash.
        with open(processing.get_error_file(config), encoding="utf-8") as errors:
            assert "Traceback" not in errors.read()


def test_real_database_update_completes(logged_in, app, config, settings_path, tmp_path):
    # Without tracked series, the update finishes without contacting TheTVDB.
    settings_path.write_text(json.dumps(make_settings(tmp_path, {})), encoding="utf-8")

    logged_in.post("/processing", data={"kind": "database"}, headers=SAME_ORIGIN)

    assert app.state.started_runs["database"]["process"].wait(timeout=60) == 0
    assert os.path.exists(os.path.join(config.conversion.database_directory, ".dbcache"))
    assert get_run_status(app, config, "database") == {"state": "idle"}
    with open(config.conversion.log_file, encoding="utf-8") as log_file:
        assert "Starting database update" in log_file.read()


class TestProcessingPage:

    def test_idle(self, logged_in, config):
        write_log(config, "[2026-11-21 10:00:00] INFO - Converted something")

        text = logged_in.get("/processing").text

        assert "No job processing is in progress." in text
        assert "No database update is in progress." in text
        assert text.count("<fieldset>") == 2
        assert text.count('type="checkbox"') == len(processing.PHASES) + 1
        assert "[2026-11-21 10:00:00] INFO - Converted something" in text
        assert config.conversion.log_file in text
        assert f'data-socket="{ROOT}/processing/log"' in text
        assert 'data-busy-jobs="false" data-busy-database="false"' in text

    def test_running(self, logged_in, config):
        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            text = logged_in.get("/processing").text

        assert "Started by a scheduled run or the command line" in text
        assert text.count("<fieldset disabled>") == 1
        assert 'data-busy-jobs="true" data-busy-database="false"' in text

    def test_errors_of_last_run(self, logged_in, config):
        os.makedirs(config.conversion.log_directory)
        with open(processing.get_error_file(config), "w", encoding="utf-8") as errors:
            errors.write("Traceback: <boom>\n")

        text = logged_in.get("/processing").text

        assert "Errors from the last job processing run started here" in text
        assert "Traceback: &lt;boom&gt;" in text

    def test_empty_log(self, logged_in):
        assert "It has no entries yet." in logged_in.get("/processing").text

    def test_starts_run_with_chosen_phases(self, logged_in, app):
        with mock.patch.object(processing, "start_run") as start:
            logged_in.post("/processing", data={"convert": "on"}, headers=SAME_ORIGIN)

        assert start.call_args[0][2:] == (
            app.state.settings_file.path, "jobs",
            ["jobs", "process", "--no-search", "--no-search-downloads", "--no-add-downloads",
             "--no-query-downloads"])

    @pytest.mark.parametrize("data, arguments", [
        ({}, ["database", "update"]),
        ({"include_ended": "on"}, ["database", "update", "-f"]),
    ])
    def test_starts_database_update(self, logged_in, data, arguments):
        with mock.patch.object(processing, "start_run") as start:
            logged_in.post("/processing", data={"kind": "database", **data},
                           headers=SAME_ORIGIN)

        assert start.call_args[0][3:] == ("database", arguments)

    def test_database_update_while_processing_jobs(self, logged_in, config):
        with mock.patch.object(processing, "start_run") as start, \
                ProcessLock(config.conversion.job_lock_file, "jobs process"):
            logged_in.post("/processing", data={"kind": "database"}, headers=SAME_ORIGIN)

        assert start.call_args[0][3] == "database"

    def test_does_not_start_database_update_while_one_is_running(self, logged_in, config):
        with mock.patch.object(processing, "start_run") as start, \
                ProcessLock(config.conversion.database_lock_file, "database update"):
            text = logged_in.post("/processing", data={"kind": "database"},
                                  headers=SAME_ORIGIN).text

        start.assert_not_called()
        assert "A database update is already running" in text
        assert 'data-busy-jobs="false" data-busy-database="true"' in text

    def test_unknown_kind_is_job_processing(self, logged_in):
        with mock.patch.object(processing, "start_run") as start:
            logged_in.post("/processing", data={"kind": "bogus", "convert": "on"},
                           headers=SAME_ORIGIN)

        assert start.call_args[0][3] == "jobs"

    def test_errors_of_last_database_update(self, logged_in, config):
        os.makedirs(config.conversion.log_directory)
        with open(processing.get_error_file(config, "database"), "w",
                  encoding="utf-8") as errors:
            errors.write("Traceback\n")

        text = logged_in.get("/processing").text

        assert "Errors from the last database update started here" in text
        assert "Errors from the last job processing run" not in text

    def test_series_page_link(self, logged_in):
        assert f'href="{ROOT}/processing#database">Update database</a>' in (
            logged_in.get("/series").text)

    def test_no_phases_chosen(self, logged_in):
        with mock.patch.object(processing, "start_run") as start:
            text = logged_in.post("/processing", data={}, headers=SAME_ORIGIN).text

        start.assert_not_called()
        assert "Choose at least one phase to run." in text

    def test_does_not_start_while_running(self, logged_in, config):
        with mock.patch.object(processing, "start_run") as start, \
                ProcessLock(config.conversion.job_lock_file, "jobs process"):
            text = logged_in.post("/processing", data=ALL_PHASES, headers=SAME_ORIGIN).text

        start.assert_not_called()
        assert "Job processing is already running" in text

    def test_navigation_and_jobs_page_link(self, logged_in, config):
        assert f'<a href="{ROOT}/processing" aria-current="page">Processing</a>' in (
            logged_in.get("/processing").text)
        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            assert f'<a href="{ROOT}/processing">Follow it</a>' in logged_in.get("/jobs").text


class TestFollowLog:

    def test_sends_log_and_status(self, logged_in, config, fast):
        write_log(config, "earlier")

        with logged_in.websocket_connect(WEBSOCKET_URL, headers=SAME_ORIGIN) as websocket:
            assert websocket.receive_json() == {"type": "lines", "reset": True,
                                                "lines": ["earlier"]}
            assert websocket.receive_json() == {"type": "status", "jobs": {"state": "idle"},
                                                "database": {"state": "idle"}}

            write_log(config, "later")
            assert websocket.receive_json() == {"type": "lines", "reset": False,
                                                "lines": ["later"]}

            with ProcessLock(config.conversion.job_lock_file, "jobs process"):
                message = websocket.receive_json()
                while message["type"] == "heartbeat":
                    message = websocket.receive_json()
                assert message["type"] == "status"
                assert message["jobs"]["state"] == "running"
                assert message["database"] == {"state": "idle"}

    def test_sends_heartbeats(self, logged_in, fast):
        with logged_in.websocket_connect(WEBSOCKET_URL, headers=SAME_ORIGIN) as websocket:
            websocket.receive_json()
            websocket.receive_json()
            assert websocket.receive_json() == {"type": "heartbeat"}

    def test_requires_login(self, client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(WEBSOCKET_URL, headers=SAME_ORIGIN):
                pass


def test_follow_log_ends_when_connection_closes(app, config):
    """A closed connection is found by sending, as happens behind the real server"""

    class ClosedSocket:  # pylint: disable=too-few-public-methods
        def __init__(self):
            self.app = app
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, message):
            self.sent.append(message)
            raise WebSocketDisconnect(1006)

    websocket = ClosedSocket()

    asyncio.run(processing.follow_log(websocket))

    assert websocket.sent == [{"type": "lines", "reset": True, "lines": []}]
