"""Module containing the pages with which jobs are changed, removed, and added"""

import hashlib
import os
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlencode

from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse, RedirectResponse

from infield_fly import job_operations
from infield_fly.job_operations import JobOperationError, set_job_status
from infield_fly.job_status import JobStatus, parse_job_status
from infield_fly.jobs import JobQueue
from infield_fly.web.processing import get_run_status
from infield_fly.web.templating import add_flash, app_path, templates

# The statuses a job can be given, in the order of processing.
STATUSES = [x for x in JobStatus if x != JobStatus.UNKNOWN]
EPISODE_EXTENSIONS = (".mkv", ".mp4")


def get_job_version(job):
    """Gets the version identifying the contents of a job's file"""

    try:
        with open(job.file_path, "rb") as job_file:
            return hashlib.sha256(job_file.read()).hexdigest()
    except FileNotFoundError:
        return ""


@contextmanager
def changing_jobs(request, config):
    """
    Holds the job processing lock while jobs are changed, raising JobOperationError if a
    run is in progress, or a run started from the web interface is starting, which hasn't
    acquired the lock yet
    """

    if get_run_status(request.app, config)["state"] != "idle":
        raise JobOperationError("Job processing is running, so jobs can't be changed until "
                                "it finishes.")
    with job_operations.changing_jobs(config):
        yield


def find_browse_root(request, path):
    """
    Gets the browsable directory containing a path, after resolving symbolic links, or None
    if it is in none of them
    """

    real_path = os.path.realpath(path)
    for root in request.app.state.browse_roots:
        if os.path.commonpath([root, real_path]) == root:
            return root
    return None


def load_job(request):
    """Gets the configuration, the job queue, and the job named in the request's path"""

    config = request.app.state.load_config()
    job_queue = JobQueue(config)
    return config, job_queue, job_queue.get_job_by_id(request.path_params["job_id"])


def job_not_found(request):
    """Gets the response for a job that doesn't exist"""

    return PlainTextResponse(f"No existing job with ID '{request.path_params['job_id']}'", 404)


async def show_job(request):
    """Shows the details of a job, with the forms that change it"""

    config, _, job = load_job(request)
    if job is None:
        return job_not_found(request)
    download = request.query_params.get("download", "")
    return templates.TemplateResponse(request, "job.html", {
        "job": job,
        "version": get_job_version(job),
        "statuses": STATUSES,
        "selected_status": JobStatus.PENDING if download else job.status,
        "download": download,
        "is_busy": get_run_status(request.app, config)["state"] != "idle",
        "can_browse": bool(request.app.state.browse_roots),
    })


async def update_job(request):
    """Sets the status of a job, with a download for the adding or pending status"""

    config, job_queue, job = load_job(request)
    if job is None:
        return job_not_found(request)
    form_data = await request.form()
    job_page = app_path(request, "job", job_id=job.job_id)
    status = parse_job_status(form_data.get("status", ""))
    download = form_data.get("download", "").strip()
    try:
        if status is None:
            raise JobOperationError(f"Unknown status '{form_data.get('status', '')}'.")
        if form_data.get("version") != get_job_version(job):
            raise JobOperationError("The job was changed by something else after you opened "
                                    "the page, so it was not changed. Check it, and try "
                                    "again.")
        if (status == JobStatus.PENDING and download
                and find_browse_root(request, download) is None):
            raise JobOperationError("The download must be in one of the folders downloads "
                                    "can be chosen from.")
        with changing_jobs(request, config):
            # Setting an NZB file's download may fetch it from the indexer.
            warnings = await run_in_threadpool(set_job_status, job_queue, job, status,
                                               download)
    except JobOperationError as ex:
        add_flash(request, "warning", str(ex))
        return RedirectResponse(
            job_page + (f"?{urlencode({'download': download})}"
                        if status == JobStatus.PENDING and download else ""), 303)

    if not download:
        add_flash(request, "success", f"Set the status of the job to {status.value}.")
    elif status == JobStatus.PENDING:
        add_flash(request, "success",
                  f"Set the job to pending conversion of {os.path.normpath(download)}.")
    else:
        add_flash(request, "success", f"Set the download of the job to {download}.")
    for warning in warnings:
        add_flash(request, "warning", f"Warning: {warning}")
    return RedirectResponse(job_page, 303)


async def reset_job(request):
    """Clears the downloads rejected for a job's episode, so they are tried again"""

    config, job_queue, job = load_job(request)
    if job is None:
        return job_not_found(request)
    try:
        with changing_jobs(request, config):
            reset_jobs = job_queue.reset_rejections(job)
    except JobOperationError as ex:
        add_flash(request, "warning", str(ex))
    else:
        add_flash(request, "success", f"Cleared the rejected downloads of {len(reset_jobs)} "
                  f"job{'s' if len(reset_jobs) != 1 else ''} for this episode.")
    return RedirectResponse(app_path(request, "job", job_id=job.job_id), 303)


async def remove_job(request):
    """Removes a job, after asking for confirmation"""

    config, _, job = load_job(request)
    if job is None:
        return job_not_found(request)
    if request.method == "GET":
        return templates.TemplateResponse(request, "job_remove.html", {"job": job})

    try:
        with changing_jobs(request, config):
            job.delete()
    except JobOperationError as ex:
        add_flash(request, "warning", str(ex))
        return RedirectResponse(app_path(request, "job", job_id=job.job_id), 303)
    add_flash(request, "success", f"Removed the job for '{job.query}'.")
    return RedirectResponse(app_path(request, "jobs"), 303)


def get_series_choices(config):
    """
    Gets the tracked series to choose from for a job, as (main keyword, name) pairs, in
    alphabetical order of their names. A series is named by its description, followed by
    its main keyword when another series has the same description.
    """

    names = [(x.main_keyword, x.description or x.main_keyword)
             for x in config.metadata.tracked_series]
    counts = {}
    for _, name in names:
        counts[name.lower()] = counts.get(name.lower(), 0) + 1
    choices = [(keyword, f"{name} ({keyword})" if counts[name.lower()] > 1 else name)
               for keyword, name in names]
    return sorted(choices, key=lambda x: (x[1].lower(), x[0]))


async def add_job(request):
    """Adds a job searching for an episode of a tracked series"""

    config = request.app.state.load_config()
    series = get_series_choices(config)
    if request.method == "GET":
        return templates.TemplateResponse(request, "job_add.html", {
            "series": series, "keyword": "", "query": "", "errors": []})

    form_data = await request.form()
    keyword = form_data.get("keyword", "")
    query = form_data.get("query", "").strip()
    try:
        with changing_jobs(request, config):
            job = job_operations.add_job(JobQueue(config), config, keyword, query)
    except JobOperationError as ex:
        return templates.TemplateResponse(request, "job_add.html", {
            "series": series, "keyword": keyword, "query": query, "errors": [str(ex)]},
            status_code=400)
    add_flash(request, "success", f"Added a job searching for '{query}'.")
    return RedirectResponse(app_path(request, "job", job_id=job.job_id), 303)


def render_browser(request, path, *, browse_url, choose_url, **context):
    """
    Shows a browsable directory, or the list of browsable directories when path is None,
    with links to browse folders and choose files or folders made by the browse_url and
    choose_url functions of a path. The context names the page's back link, heading, and
    introduction.
    """

    roots = request.app.state.browse_roots
    if path is None:
        return templates.TemplateResponse(request, "browse.html", {
            **context, "path": None, "roots": roots, "entries": [], "parent": None,
            "browse_url": browse_url, "choose_url": choose_url})

    real_path = os.path.realpath(path)
    root = find_browse_root(request, real_path)
    if root is None or not os.path.isdir(real_path):
        return PlainTextResponse("That folder can't be browsed.", 404)
    return templates.TemplateResponse(request, "browse.html", {
        **context, "path": real_path, "roots": roots, "entries": list_directory(real_path),
        "parent": os.path.dirname(real_path) if real_path != root else None,
        "browse_url": browse_url, "choose_url": choose_url})


def with_query(base, **params):
    """Gets a path with query parameters, leaving out those that are None"""

    params = {name: value for name, value in params.items() if value is not None}
    return f"{base}?{urlencode(params)}" if params else base


async def browse_downloads(request):
    """Lists a browsable directory, from which a download is chosen for a job"""

    _, _, job = load_job(request)
    if job is None:
        return job_not_found(request)
    browse_path = app_path(request, "job_browse", job_id=job.job_id)
    job_path = app_path(request, "job", job_id=job.job_id)
    return render_browser(
        request, request.query_params.get("path"),
        browse_url=lambda path: with_query(browse_path, path=path),
        choose_url=lambda path: with_query(job_path, download=path),
        back_url=job_path, back_label="Job", heading="Choose the download",
        intro=(f"For {job.query}. Choose an episode file, or a folder to convert the "
               "largest episode file in it."))


def list_directory(path):
    """
    Lists the folders and files in a directory, folders first, leaving out hidden ones, and
    marking episode files
    """

    entries = []
    with os.scandir(path) as listing:
        for entry in listing:
            if entry.name.startswith("."):
                continue
            try:
                info = entry.stat()
            except OSError:
                continue
            is_directory = entry.is_dir()
            entries.append({
                "name": entry.name,
                "path": os.path.join(path, entry.name),
                "is_directory": is_directory,
                "is_episode": not is_directory and entry.name.lower().endswith(
                    EPISODE_EXTENSIONS),
                "size": None if is_directory else info.st_size,
                "modified": datetime.fromtimestamp(info.st_mtime),
            })
    return sorted(entries, key=lambda x: (not x["is_directory"], x["name"].lower()))
