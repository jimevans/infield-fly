"""Module containing the pages with which tracked series are added, edited, and removed"""

from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse, RedirectResponse

from infield_fly.database import EpisodeDatabase, TVMetadataProvider
from infield_fly.settings_file import SettingsChangedError
from infield_fly.web.series_form import SeriesForm
from infield_fly.web.templating import add_flash, app_path, templates

CHANGED_MESSAGE = ("The settings file was changed by something else after you opened this "
                   "page, so nothing was saved. The page now shows the current settings; "
                   "make your change again.")


def get_tracked_series(settings):
    """Gets the tracked series in the raw settings, keyed by their main keywords"""

    tracked_series = (settings.get("metadata") or {}).get("tracked_series") or {}
    return {keyword: series if isinstance(series, dict) else {}
            for keyword, series in tracked_series.items()}


def set_tracked_series(settings, tracked_series):
    """Sets the tracked series in the raw settings"""

    if not isinstance(settings.get("metadata"), dict):
        settings["metadata"] = {}
    settings["metadata"]["tracked_series"] = tracked_series


def get_metadata_provider(request, config):
    """
    Gets the client for thetvdb.com, reusing it while the credentials in the settings are
    unchanged, so that it does not have to log in again for every search
    """

    credentials = (config.metadata.api_key, config.metadata.pin)
    cached = getattr(request.app.state, "metadata_provider", None)
    if cached is None or cached[0] != credentials:
        cached = (credentials, TVMetadataProvider(config.metadata))
        request.app.state.metadata_provider = cached
    return cached[1]


def fetch_episodes(config, provider, series_id):
    """
    Retrieves the episodes of a series from thetvdb.com into the episode database,
    returning the series, or None if it could not be retrieved
    """

    episode_db = EpisodeDatabase.load_from_cache(config)
    episode_db.metadata_provider = provider
    series = episode_db.update_series(series_id, is_unattended_mode=True)
    if series is not None:
        episode_db.save_to_cache()
    return series


async def add_series(request):
    """Searches thetvdb.com for a series to track"""

    config = request.app.state.load_config()
    query = request.query_params.get("q", "").strip()
    results = None
    error = None
    if query != "":
        if config.metadata.api_key == "":
            error = ("Searching TheTVDB needs its API key, set as api_key in the metadata "
                     "settings. You can still add a series by its TheTVDB ID below.")
        else:
            results = await run_in_threadpool(
                get_metadata_provider(request, config).search_series, query)
            if results is None:
                error = ("Searching TheTVDB failed. Try again, or add the series by its "
                         "TheTVDB ID below.")

    settings, _ = request.app.state.settings_file.read()
    tracked_ids = {series.get("id"): keyword
                   for keyword, series in get_tracked_series(settings).items()}
    return templates.TemplateResponse(request, "series_add.html", {
        "query": query, "results": results, "error": error,
        "tracked_ids": tracked_ids})


def render_form(request, form, original_keyword, version, errors=None, status_code=200):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Shows the series editor"""

    return templates.TemplateResponse(request, "series_edit.html", {
        "form": form, "original_keyword": original_keyword,
        "version": version, "errors": errors or [],
        "example_searches": form.get_example_searches(),
    }, status_code=status_code)


async def edit_series(request):
    """Edits a tracked series, or a new series to track, and saves it to the settings file"""

    settings_file = request.app.state.settings_file
    settings, version = settings_file.read()
    tracked_series = get_tracked_series(settings)
    if request.method == "GET":
        return show_editor(request, tracked_series, version)

    form_data = await request.form()
    form = SeriesForm.from_form_data(form_data)
    original_keyword = form_data.get("original_keyword") or None
    action = form_data.get("action", "save")
    if action != "save":
        form.apply_action(action)
        return render_form(request, form, original_keyword, form_data.get("version", ""))

    if (form_data.get("version") != version
            or (original_keyword is not None and original_keyword not in tracked_series)):
        return render_changed_form(request, form, original_keyword, tracked_series, version)

    errors = form.validate(tracked_series, original_keyword)
    if errors:
        return render_form(request, form, original_keyword, version, errors, 400)

    updated_series = {}
    for keyword, series in tracked_series.items():
        if keyword == original_keyword:
            updated_series[form.keyword] = form.to_settings(series)
        else:
            updated_series[keyword] = settings["metadata"]["tracked_series"][keyword]
    if original_keyword is None:
        updated_series[form.keyword] = form.to_settings()
    set_tracked_series(settings, updated_series)
    try:
        settings_file.write(settings, version)
    except SettingsChangedError:
        settings, version = settings_file.read()
        return render_changed_form(request, form, original_keyword,
                                   get_tracked_series(settings), version)

    original_id = (tracked_series[original_keyword].get("id")
                   if original_keyword is not None else None)
    await save_succeeded(request, form, original_keyword is None,
                         original_id != int(form.series_id))
    return RedirectResponse(app_path(request, "series"), 303)


def show_editor(request, tracked_series, version):
    """
    Shows the series editor for the tracked series named in the request, or for a new
    series with the TheTVDB ID and name in the request
    """

    keyword = request.query_params.get("keyword")
    if keyword is None:
        return render_form(request, SeriesForm.for_new_series(
            request.query_params.get("series_id", ""),
            request.query_params.get("name", "")), None, version)
    if keyword not in tracked_series:
        return PlainTextResponse(f"No tracked series with main keyword '{keyword}'", 404)
    return render_form(request, SeriesForm.from_settings(keyword, tracked_series[keyword]),
                       keyword, version)


def render_changed_form(request, form, original_keyword, tracked_series, version):
    """
    Shows the series editor after refusing to save it because the settings file changed,
    with the current settings of the series being edited, or the submitted settings of a
    new series
    """

    if original_keyword is not None:
        if original_keyword not in tracked_series:
            add_flash(request, "warning",
                      f"The series '{original_keyword}' was removed from the settings file "
                      "by something else, so your changes to it were not saved.")
            return RedirectResponse(app_path(request, "series"), 303)
        form = SeriesForm.from_settings(original_keyword, tracked_series[original_keyword])
    return render_form(request, form, original_keyword, version, [CHANGED_MESSAGE], 409)


async def save_succeeded(request, form, is_new, is_id_changed):
    """
    Reports a saved series, first retrieving the episodes of a series not yet tracked
    under its TheTVDB ID, so that it is complete without waiting for a database update
    """

    name = form.description
    if not is_id_changed:
        add_flash(request, "success", f"Saved {name}.")
        return

    action = "Started tracking" if is_new else "Saved"
    config = request.app.state.load_config()
    series = None
    if config.metadata.api_key != "":
        series = await run_in_threadpool(fetch_episodes, config,
                                         get_metadata_provider(request, config),
                                         int(form.series_id))
    if series is None:
        add_flash(request, "warning",
                  f"{action} {name}, but its episodes could not be retrieved from TheTVDB. "
                  "They will be retrieved by the next database update.")
    else:
        add_flash(request, "success",
                  f"{action} {name}, and retrieved its {len(series.episodes)} episodes.")


async def delete_series(request):
    """Stops tracking a series, after asking for confirmation"""

    settings_file = request.app.state.settings_file
    settings, version = settings_file.read()
    tracked_series = get_tracked_series(settings)
    if request.method == "GET":
        keyword = request.query_params.get("keyword", "")
        if keyword not in tracked_series:
            return PlainTextResponse(f"No tracked series with main keyword '{keyword}'", 404)
        return templates.TemplateResponse(request, "series_delete.html", {
            "keyword": keyword,
            "series": SeriesForm.from_settings(keyword, tracked_series[keyword]),
            "version": version})

    form_data = await request.form()
    keyword = form_data.get("keyword", "")
    if keyword not in tracked_series:
        add_flash(request, "warning", f"The series '{keyword}' is not tracked.")
        return RedirectResponse(app_path(request, "series"), 303)
    if form_data.get("version") != version:
        add_flash(request, "warning", "The settings file was changed by something else after "
                  "you opened the page, so the series was not removed. Check it, and remove "
                  "it again if you still want to.")
        return RedirectResponse(app_path(request, "series"), 303)

    name = tracked_series[keyword].get("description") or keyword
    remaining = dict(settings["metadata"]["tracked_series"])
    del remaining[keyword]
    set_tracked_series(settings, remaining)
    try:
        settings_file.write(settings, version)
    except SettingsChangedError:
        add_flash(request, "warning", "The settings file was changed by something else "
                  "while removing the series, so it was not removed.")
        return RedirectResponse(app_path(request, "series"), 303)

    add_flash(request, "success", f"Stopped tracking {name}. Its existing jobs and "
              "episodes in the episode database were kept.")
    return RedirectResponse(app_path(request, "series"), 303)
