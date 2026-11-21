"""Module containing the web interface to Infield Fly"""

import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from infield_fly.concurrency import read_lock_holder
from infield_fly.database import EpisodeDatabase
from infield_fly.job_status import JobStatus
from infield_fly.jobs import JobQueue
from infield_fly.web import (convert_page, job_actions, processing, series_editor,
                             settings_editor)
from infield_fly.web.templating import WEB_DIRECTORY, app_path, templates

SESSION_COOKIE = "infield_fly_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
PUBLIC_PATHS = ("/login", "/static/")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# The order in which job statuses are listed, following a job through processing.
STATUS_ORDER = [x for x in JobStatus if x != JobStatus.UNKNOWN] + [JobStatus.UNKNOWN]

def get_route_path(scope):
    """Gets the path of a request relative to the root path of the application"""

    path = scope["path"]
    root_path = scope.get("root_path", "")
    if root_path and path.startswith(root_path + "/"):
        return path[len(root_path):]
    return path if path != root_path else "/"


def is_same_origin(connection):
    """
    Gets a value indicating whether a request was sent by a page of this site, from its
    Origin header, or its Referer header when there is no Origin header
    """

    source = connection.headers.get("origin") or connection.headers.get("referer")
    if source is None:
        return False
    return urlsplit(source).netloc == connection.headers.get("host")


class AccessControlMiddleware:

    """
    Requires a logged in session for every request other than for the login page and
    static files, refuses requests that change state unless sent by a page of this site,
    and adds security headers to responses. Checking every request here, rather than in
    each endpoint, keeps endpoints added later from being exposed by mistake.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        is_logged_in = connection.session.get("user") is not None
        is_unsafe = scope["type"] == "websocket" or scope["method"] not in SAFE_METHODS
        route_path = get_route_path(scope)
        is_public = route_path == PUBLIC_PATHS[0] or route_path.startswith(PUBLIC_PATHS[1])

        if scope["type"] == "websocket":
            if not is_logged_in or not is_same_origin(connection):
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return

        if is_unsafe and not is_same_origin(connection):
            response = PlainTextResponse("Request refused: not sent from this site", 403)
        elif not is_public and not is_logged_in:
            login_path = connection.url_for("login").path
            response = RedirectResponse(
                f"{login_path}?next={quote(scope.get('root_path', '') + route_path)}", 303)
        else:
            response = None

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        if response is not None:
            await response(scope, receive, send_with_headers)
        else:
            await self.app(scope, receive, send_with_headers)


def get_redirect_target(request, target):
    """
    Gets the path to which to send the user after logging in, accepting only paths within
    this application, so the login page cannot be used to redirect to another site
    """

    root_path = request.scope.get("root_path", "")
    if (target and target.startswith(root_path + "/") and not target.startswith("//")
            and "\\" not in target and get_route_path(
                {"path": target, "root_path": root_path}) not in ("/login", "/logout")):
        return target
    return app_path(request, "jobs")


async def home(request):
    """Shows the job list"""

    return RedirectResponse(app_path(request, "jobs"), 303)


async def login(request):
    """Shows the login page, and logs the user in with their user name and password"""

    authenticator = request.app.state.authenticator
    target = get_redirect_target(request, request.query_params.get("next"))
    if request.method == "GET":
        if request.session.get("user") is not None:
            return RedirectResponse(target, 303)
        return templates.TemplateResponse(request, "login.html", {"next": target})

    form = await request.form()
    user_name = str(form.get("user_name", ""))
    client = request.client.host if request.client is not None else "unknown"
    if await run_in_threadpool(authenticator.authenticate, user_name,
                               str(form.get("password", "")), client):
        request.session.clear()
        request.session["user"] = user_name
        return RedirectResponse(target, 303)

    if authenticator.is_limited(client):
        error, status_code = "Too many failed logins. Try again later.", 429
    else:
        error, status_code = "Incorrect user name or password.", 401
    return templates.TemplateResponse(
        request, "login.html",
        {"next": target, "error": error, "user_name": user_name}, status_code=status_code)


async def logout(request):
    """Logs the user out"""

    request.session.clear()
    return RedirectResponse(app_path(request, "login"), 303)


async def list_jobs(request):
    """Shows the jobs in the job queue, optionally only those with a status"""

    config = request.app.state.load_config()
    status_value = request.query_params.get("status")
    status = None
    if status_value:
        status = JobStatus(status_value.lower())
        if status == JobStatus.UNKNOWN and status_value.lower() != JobStatus.UNKNOWN.value:
            return PlainTextResponse(f"Unknown status value '{status_value}'", 400)

    jobs = JobQueue(config).load_jobs()
    counts = {x: sum(1 for job in jobs if job.status == x) for x in STATUS_ORDER}
    jobs = sorted((x for x in jobs if status is None or x.status == status),
                  key=lambda x: (STATUS_ORDER.index(x.status), x.added or "", x.query or ""))
    return templates.TemplateResponse(request, "jobs.html", {
        "jobs": jobs,
        "status": status,
        "counts": {x: count for x, count in counts.items() if count > 0},
        "total": sum(counts.values()),
        "processing": read_lock_holder(config.conversion.job_lock_file),
    })


def describe_tracked_series(config, today):
    """
    Gets the status of each tracked series from the cached episode database, without
    retrieving series missing from the cache
    """

    episode_db = EpisodeDatabase.load_from_cache(config)
    descriptions = []
    for tracked_series in config.metadata.tracked_series:
        series = episode_db.known_series.get(tracked_series.series_id)
        episodes = series.episodes if series is not None else []
        aired = [x for x in episodes if x.airdate is not None and x.airdate <= today]
        upcoming = [x for x in episodes if x.airdate is not None and x.airdate > today]
        descriptions.append(SimpleNamespace(
            tracked=tracked_series,
            series=series,
            episode_count=len(episodes),
            last_aired=max(aired, default=None, key=episode_sort_key),
            next_airing=min(upcoming, default=None, key=episode_sort_key),
        ))

    database_updated = None
    if os.path.exists(episode_db.cache_file_path):
        database_updated = datetime.fromtimestamp(os.path.getmtime(episode_db.cache_file_path))
    return sorted(descriptions, key=lambda x: x.tracked.description.lower()), database_updated


def episode_sort_key(episode):
    """Gets the key by which to order episodes by airdate"""

    return (episode.airdate, episode.season_number, episode.episode_number)


async def list_series(request):
    """Shows the status of each tracked series"""

    config = request.app.state.load_config()
    now = datetime.now()
    series, database_updated = describe_tracked_series(
        config, datetime(now.year, now.month, now.day))
    # Entries the configuration ignores, such as those without a valid TheTVDB ID, are
    # listed so that they can be fixed.
    settings, _ = request.app.state.settings_file.read()
    ignored = [x for x in series_editor.get_tracked_series(settings)
               if x not in {y.main_keyword for y in config.metadata.tracked_series}]
    return templates.TemplateResponse(request, "series.html", {
        "series": series,
        "ignored": ignored,
        "database_updated": database_updated,
        "today": datetime(now.year, now.month, now.day),
        "tomorrow": datetime(now.year, now.month, now.day) + timedelta(days=1),
    })


def create_app(load_config, authenticator, secret_key, *, settings_file, https_only=True,
               root_path="", browse_roots=()):
    """
    Creates the web interface application. The configuration is loaded with load_config
    on each request, so changes to the settings file apply without restarting the
    application, and the settings file is changed with settings_file. When https_only is
    set, the session cookie is only sent over HTTPS. Downloads for jobs can be chosen from
    the directories in browse_roots.
    """

    app = Starlette(
        routes=[
            Route("/", home, name="home"),
            Route("/login", login, methods=["GET", "POST"], name="login"),
            Route("/logout", logout, methods=["POST"], name="logout"),
            Route("/jobs", list_jobs, name="jobs"),
            Route("/jobs/add", job_actions.add_job, methods=["GET", "POST"], name="job_add"),
            Route("/jobs/{job_id}", job_actions.show_job, name="job"),
            Route("/jobs/{job_id}/update", job_actions.update_job, methods=["POST"],
                  name="job_update"),
            Route("/jobs/{job_id}/reset", job_actions.reset_job, methods=["POST"],
                  name="job_reset"),
            Route("/jobs/{job_id}/remove", job_actions.remove_job, methods=["GET", "POST"],
                  name="job_remove"),
            Route("/jobs/{job_id}/browse", job_actions.browse_downloads, name="job_browse"),
            Route("/processing", processing.show_processing, methods=["GET", "POST"],
                  name="processing"),
            WebSocketRoute("/processing/log", processing.follow_log, name="processing_log"),
            Route("/convert", convert_page.show_convert, name="convert"),
            Route("/convert", convert_page.convert, methods=["POST"], name="convert_post"),
            Route("/convert/browse", convert_page.browse_sources, name="convert_browse"),
            Route("/series", list_series, name="series"),
            Route("/series/add", series_editor.add_series, name="series_add"),
            Route("/series/edit", series_editor.edit_series, methods=["GET", "POST"],
                  name="series_edit"),
            Route("/series/delete", series_editor.delete_series, methods=["GET", "POST"],
                  name="series_delete"),
            Route("/settings", settings_editor.edit_settings, methods=["GET", "POST"],
                  name="settings"),
            Mount("/static", StaticFiles(directory=os.path.join(WEB_DIRECTORY, "static")),
                  name="static"),
        ],
        middleware=[
            Middleware(SessionMiddleware, secret_key=secret_key, session_cookie=SESSION_COOKIE,
                       max_age=SESSION_MAX_AGE_SECONDS, path=root_path or "/",
                       same_site="lax", https_only=https_only),
            Middleware(AccessControlMiddleware),
        ])
    app.state.load_config = load_config
    app.state.authenticator = authenticator
    app.state.settings_file = settings_file
    app.state.browse_roots = [os.path.realpath(x) for x in browse_roots]
    return app
