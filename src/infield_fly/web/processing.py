"""
Module containing the page from which job processing and episode database updates are
started and followed as they run, whether started from the web interface or elsewhere
"""

import asyncio
import os
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from starlette.responses import RedirectResponse
from starlette.websockets import WebSocketDisconnect

from infield_fly.concurrency import read_lock_holder
from infield_fly.web.templating import add_flash, app_path, templates

# The processing phases, by the name of their form field, which is also their option of
# the jobs process command, as --no-search-downloads for "search-downloads".
PHASES = (
    ("search", "Create jobs for episodes airing today"),
    ("search-downloads", "Search the indexer for waiting jobs"),
    ("add-downloads", "Add found downloads to SABnzbd"),
    ("query-downloads", "Check on downloads in progress"),
    ("convert", "Convert finished downloads"),
)
TAIL_LINES = 200
# How long a run started here is shown as starting before it acquires the lock.
STARTING_SECONDS = 30
HEARTBEAT_SECONDS = 10


class LogFollower:

    """
    Follows a log file as it is written, like tail -f, starting again from the beginning
    of the file when it is rotated or truncated. Only complete lines are returned.
    """

    def __init__(self, path):
        self.path = path
        self.identity = None
        self.position = 0
        self.partial = b""

    def start(self, max_lines):
        """Gets the last lines of the file, and starts following it from its end"""

        self.identity, self.position, self.partial = None, 0, b""
        return list(deque(self.read_new(), maxlen=max_lines))

    def read_new(self):
        """Gets the lines written since the last read"""

        try:
            status = os.stat(self.path)
        except FileNotFoundError:
            return []
        identity = (status.st_dev, status.st_ino)
        if identity != self.identity or status.st_size < self.position:
            self.identity, self.position, self.partial = identity, 0, b""
        if status.st_size == self.position:
            return []

        with open(self.path, "rb") as log_file:
            log_file.seek(self.position)
            data = self.partial + log_file.read()
            self.position = log_file.tell()
        *lines, self.partial = data.split(b"\n")
        return [x.decode("utf-8", errors="replace").rstrip("\r") for x in lines]


@dataclass(frozen=True)
class RunKind:

    """A kind of run that can be started from the processing page"""

    name: str
    get_lock_file: Callable
    error_file_name: str


RUN_KINDS = {
    "jobs": RunKind("jobs", lambda config: config.conversion.job_lock_file, "process-now.err"),
    "database": RunKind("database", lambda config: config.conversion.database_lock_file,
                        "database-update.err"),
}


def get_started_run(app, kind):
    """Gets the run of a kind last started here, or None"""

    return getattr(app.state, "started_runs", {}).get(kind)


def get_run_status(app, config, kind="jobs"):
    """
    Gets the status of a kind of run, "jobs" or "database": "running" with the holder of its
    lock while a run holds it, "starting" for a run started here that hasn't acquired it
    yet, or "idle"
    """

    holder = read_lock_holder(RUN_KINDS[kind].get_lock_file(config))
    started = get_started_run(app, kind)
    is_started_here_running = started is not None and started["process"].poll() is None
    if holder is not None:
        return {"state": "running", "pid": holder.get("pid"),
                "started": holder.get("started"),
                "from_web": is_started_here_running and holder.get("pid") == started["pid"]}
    if is_started_here_running and time.monotonic() - started["time"] < STARTING_SECONDS:
        return {"state": "starting"}
    return {"state": "idle"}


def get_statuses(app, config):
    """Gets the status of each kind of run, by kind"""

    return {kind: get_run_status(app, config, kind) for kind in RUN_KINDS}


def get_error_file(config, kind="jobs"):
    """Gets the path of the file receiving the error output of runs of a kind started here"""

    return os.path.join(config.conversion.log_directory, RUN_KINDS[kind].error_file_name)


def start_run(app, config, settings_path, kind, arguments):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    """
    Starts a run of a kind, running the infield-fly command with arguments, in a process of
    its own, in a new session, so that it keeps running if the web interface is stopped. It
    runs unattended, logging to the same log file as scheduled runs.
    """

    previous = get_started_run(app, kind)
    if previous is not None:
        previous["process"].poll()

    os.makedirs(config.conversion.log_directory, exist_ok=True)
    command = [sys.executable, "-m", "infield_fly", "-u", "-c", settings_path, *arguments]
    with open(get_error_file(config, kind), "wb") as error_file:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            command, stdin=subprocess.DEVNULL, stdout=error_file, stderr=error_file,
            start_new_session=True, close_fds=True)
    if not hasattr(app.state, "started_runs"):
        app.state.started_runs = {}
    app.state.started_runs[kind] = {"process": process, "pid": process.pid,
                                    "time": time.monotonic()}


def get_run_arguments(kind, form_data):
    """
    Gets the arguments of the infield-fly command for a run of a kind requested with the
    form data, or None if the form requests nothing to run
    """

    if kind == "database":
        return ["database", "update"] + (["-f"] if form_data.get("include_ended") else [])
    skipped = [name for name, _ in PHASES if form_data.get(name) is None]
    if len(skipped) == len(PHASES):
        return None
    return ["jobs", "process", *[f"--no-{x}" for x in skipped]]


async def show_processing(request):
    """
    Shows the status of job processing and database updates, with the end of the log, and
    starts runs of them
    """

    config = request.app.state.load_config()
    if request.method == "POST":
        form_data = await request.form()
        kind = form_data.get("kind", "jobs")
        if kind not in RUN_KINDS:
            kind = "jobs"
        arguments = get_run_arguments(kind, form_data)
        if get_run_status(request.app, config, kind)["state"] != "idle":
            add_flash(request, "warning", ("Job processing" if kind == "jobs" else
                                           "A database update")
                      + " is already running, so another was not started.")
        elif arguments is None:
            add_flash(request, "warning", "Choose at least one phase to run.")
        else:
            start_run(request.app, config, request.app.state.settings_file.path, kind,
                      arguments)
        return RedirectResponse(app_path(request, "processing"), 303)

    statuses = get_statuses(request.app, config)
    errors = {}
    for kind in RUN_KINDS:
        error_file = get_error_file(config, kind)
        if statuses[kind]["state"] == "idle" and os.path.exists(error_file):
            with open(error_file, encoding="utf-8", errors="replace") as error_output:
                errors[kind] = error_output.read().strip()
    return templates.TemplateResponse(request, "processing.html", {
        "statuses": statuses,
        "phases": PHASES,
        "lines": LogFollower(config.conversion.log_file).start(TAIL_LINES),
        "log_file": config.conversion.log_file,
        "errors": {kind: text for kind, text in errors.items() if text},
    })


async def follow_log(websocket):
    """
    Sends the end of the log, then lines as they are written, and the status of job
    processing whenever it changes, as JSON messages, until the connection closes
    """

    await websocket.accept()
    app = websocket.app
    config = app.state.load_config()
    follower = LogFollower(config.conversion.log_file)
    poll_seconds = getattr(app.state, "log_poll_seconds", 1)
    last_status = None
    last_sent = time.monotonic()
    try:
        await websocket.send_json({"type": "lines", "reset": True,
                                   "lines": follower.start(TAIL_LINES)})
        while True:
            lines = follower.read_new()
            if lines:
                await websocket.send_json({"type": "lines", "reset": False, "lines": lines})
                last_sent = time.monotonic()
            status = get_statuses(app, config)
            if status != last_status:
                await websocket.send_json({"type": "status", **status})
                last_status = status
                last_sent = time.monotonic()
            if time.monotonic() - last_sent >= HEARTBEAT_SECONDS:
                # Sending regularly finds connections that have closed, and keeps proxies
                # from closing idle ones.
                await websocket.send_json({"type": "heartbeat"})
                last_sent = time.monotonic()
            await asyncio.sleep(poll_seconds)
    except (WebSocketDisconnect, RuntimeError):
        pass
