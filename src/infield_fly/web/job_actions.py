"""Module containing the pages with which jobs are changed, removed, and added"""

import hashlib
import os
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlencode

from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse, RedirectResponse

from infield_fly.concurrency import LockHeldError, ProcessLock
from infield_fly.job_status import JobStatus
from infield_fly.jobs import JobQueue
from infield_fly.web.processing import get_run_status
from infield_fly.web.templating import add_flash, app_path, templates

# The statuses a job can be given, in the order of processing.
STATUSES = [x for x in JobStatus if x != JobStatus.UNKNOWN]
EPISODE_EXTENSIONS = (".mkv", ".mp4")


class JobChangeRefused(Exception):

    """Raised when a job cannot be changed, with the reason to show the user"""


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
    Holds the job processing lock while jobs are changed, so that a processing run, which
    keeps its own copies of jobs, can't overwrite the change, raising JobChangeRefused if a
    run is in progress or starting
    """

    if get_run_status(request.app, config)["state"] != "idle":
        raise JobChangeRefused("Job processing is running, so jobs can't be changed until it "
                               "finishes.")
    try:
        with ProcessLock(config.conversion.job_lock_file, "web interface job change"):
            yield
    except LockHeldError as ex:
        raise JobChangeRefused("Job processing is running, so jobs can't be changed until it "
                               "finishes.") from ex


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
    status = JobStatus(form_data.get("status", ""))
    download = form_data.get("download", "").strip()
    try:
        if status == JobStatus.UNKNOWN:
            raise JobChangeRefused(f"Unknown status '{form_data.get('status', '')}'.")
        if form_data.get("version") != get_job_version(job):
            raise JobChangeRefused("The job was changed by something else after you opened "
                                   "the page, so it was not changed. Check it, and try again.")
        with changing_jobs(request, config):
            messages = await apply_status(request, job_queue, job, status, download)
    except JobChangeRefused as ex:
        add_flash(request, "warning", str(ex))
        return RedirectResponse(
            job_page + (f"?{urlencode({'download': download})}"
                        if status == JobStatus.PENDING and download else ""), 303)

    for kind, message in messages:
        add_flash(request, kind, message)
    return RedirectResponse(job_page, 303)


async def apply_status(request, job_queue, job, status, download):
    """
    Sets the status of a job, and the download given for the adding or pending status,
    returning the messages to show, or raising JobChangeRefused if the download can't be used
    """

    if status == JobStatus.ADDING and download:
        downloader = job_queue.get_downloader(download)
        if downloader is None:
            raise JobChangeRefused("For adding, give a magnet link, or the path or URL of an "
                                   "NZB file.")
        try:
            warnings = await run_in_threadpool(downloader.set_download, job, download)
        except ValueError as ex:
            raise JobChangeRefused(str(ex)) from ex
        return ([("success", f"Set the download of the job to {download}.")]
                + [("warning", f"Warning: {x}") for x in warnings])

    if status == JobStatus.PENDING and download:
        if find_browse_root(request, download) is None:
            raise JobChangeRefused("The download must be in one of the folders downloads can "
                                   "be chosen from.")
        if not os.path.exists(download):
            raise JobChangeRefused(f"'{download}' does not exist.")
        download = os.path.normpath(download)
        job.set_download_status(JobStatus.PENDING, os.path.basename(download),
                                os.path.dirname(download))
        job.save(job_queue.logger)
        return [("success", f"Set the job to pending conversion of {download}.")]

    job.status = status
    job.save(job_queue.logger)
    return [("success", f"Set the status of the job to {status.value}.")]


async def reset_job(request):
    """Clears the downloads rejected for a job's episode, so they are tried again"""

    config, job_queue, job = load_job(request)
    if job is None:
        return job_not_found(request)
    try:
        with changing_jobs(request, config):
            reset_jobs = job_queue.reset_rejections(job)
    except JobChangeRefused as ex:
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
    except JobChangeRefused as ex:
        add_flash(request, "warning", str(ex))
        return RedirectResponse(app_path(request, "job", job_id=job.job_id), 303)
    add_flash(request, "success", f"Removed the job for '{job.query}'.")
    return RedirectResponse(app_path(request, "jobs"), 303)


async def add_job(request):
    """Adds a job searching for an episode of a tracked series"""

    config = request.app.state.load_config()
    keywords = [x.main_keyword for x in config.metadata.tracked_series]
    if request.method == "GET":
        return templates.TemplateResponse(request, "job_add.html", {
            "keywords": keywords, "keyword": "", "query": "", "errors": []})

    form_data = await request.form()
    keyword = form_data.get("keyword", "")
    query = form_data.get("query", "").strip()
    job_queue = JobQueue(config)
    errors = []
    if keyword not in keywords:
        errors.append("Choose a tracked series.")
    if query == "":
        errors.append("Enter the search string.")
    elif job_queue.is_existing_job(keyword, query):
        errors.append("A job for that series and search string already exists.")
    if not errors:
        try:
            with changing_jobs(request, config):
                job = job_queue.create_job(keyword, query)
        except JobChangeRefused as ex:
            errors.append(str(ex))
        else:
            add_flash(request, "success", f"Added a job searching for '{query}'.")
            return RedirectResponse(app_path(request, "job", job_id=job.job_id), 303)

    return templates.TemplateResponse(request, "job_add.html", {
        "keywords": keywords, "keyword": keyword, "query": query, "errors": errors},
        status_code=400)


async def browse_downloads(request):
    """Lists a browsable directory, from which a download is chosen for a job"""

    _, _, job = load_job(request)
    if job is None:
        return job_not_found(request)
    roots = request.app.state.browse_roots
    path = request.query_params.get("path")
    if path is None:
        return templates.TemplateResponse(request, "job_browse.html", {
            "job": job, "path": None, "roots": roots, "entries": [], "parent": None})

    real_path = os.path.realpath(path)
    root = find_browse_root(request, real_path)
    if root is None or not os.path.isdir(real_path):
        return PlainTextResponse("That folder can't be browsed.", 404)
    return templates.TemplateResponse(request, "job_browse.html", {
        "job": job, "path": real_path, "roots": roots,
        "entries": list_directory(real_path),
        "parent": os.path.dirname(real_path) if real_path != root else None,
    })


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
