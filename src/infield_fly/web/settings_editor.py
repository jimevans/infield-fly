"""Module containing the page with which the global settings are edited"""

import os

from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from infield_fly.settings_file import SettingsChangedError
from infield_fly.web.connection_checks import CHECKS
from infield_fly.web.settings_form import (FIELDS, GROUPS, WORKING_DIRECTORIES, SettingsForm,
                                          get_default)
from infield_fly.web.templating import add_flash, app_path, templates

CHANGED_MESSAGE = ("The settings file was changed by something else after you opened this "
                   "page, so nothing was saved. The page now shows the current settings; "
                   "make your changes again.")


def render_settings(request, form, version, **context):
    """Shows the settings page"""

    settings_file = request.app.state.settings_file
    settings, _ = settings_file.read()
    settings_directory = os.path.dirname(settings_file.path)
    status_code = context.pop("status_code", 200)
    return templates.TemplateResponse(request, "settings.html", {
        "groups": GROUPS,
        "form": form,
        "version": version,
        "errors": context.get("errors", []),
        "check_results": context.get("check_results", {}),
        "defaults": {x.name: get_default(x, settings_directory) for x in FIELDS},
        "saved_secrets": {x.name for x in FIELDS if x.kind == "secret"
                          and (settings.get(x.section) or {}).get(x.key)},
        "settings_path": settings_file.path,
    }, status_code=status_code)


async def edit_settings(request):
    """Edits the global settings, and saves them to the settings file"""

    settings_file = request.app.state.settings_file
    settings, version = settings_file.read()
    if request.method == "GET":
        return render_settings(request, SettingsForm.from_settings(settings), version)

    form_data = await request.form()
    form = SettingsForm.from_form_data(form_data)
    posted_version = form_data.get("version", "")
    action = form_data.get("action", "save")
    if action.startswith("check-") and action[len("check-"):] in CHECKS:
        return await check_connection(request, form, settings, posted_version,
                                      action[len("check-"):])
    if action != "save":
        form.apply_action(action, settings)
        return render_settings(request, form, posted_version)
    return save_settings(request, form, settings, version, posted_version)


def save_settings(request, form, settings, version, posted_version):
    """
    Saves the settings in the form, unless they are invalid, or the settings file was
    changed since the version on which the form is based
    """

    settings_file = request.app.state.settings_file
    if posted_version != version:
        return render_settings(request, SettingsForm.from_settings(settings), version,
                               errors=[CHANGED_MESSAGE], status_code=409)
    errors = form.validate()
    if errors:
        return render_settings(request, form, version, errors=errors, status_code=400)

    updated = form.apply_to(settings)
    try:
        settings_file.write(updated, version)
    except SettingsChangedError:
        settings, version = settings_file.read()
        return render_settings(request, SettingsForm.from_settings(settings), version,
                               errors=[CHANGED_MESSAGE], status_code=409)

    add_flash(request, "success", "Saved the settings.")
    for warning in get_save_warnings(settings, updated):
        add_flash(request, "warning", warning)
    return RedirectResponse(app_path(request, "settings"), 303)


async def check_connection(request, form, settings, version, check_name):
    """Checks a connection using the values in the form, without saving them"""

    errors = form.validate()
    if errors:
        return render_settings(request, form, version, errors=errors, status_code=400)
    config = form.get_configuration(settings,
                                    os.path.dirname(request.app.state.settings_file.path))
    result = await run_in_threadpool(CHECKS[check_name], config)
    return render_settings(request, form, version, check_results={check_name: result})


def get_save_warnings(old_settings, new_settings):
    """Gets warnings about saved settings that may not do what was intended"""

    warnings = []
    old_conversion = old_settings.get("conversion") or {}
    new_conversion = new_settings.get("conversion") or {}
    moved = [x.label.lower() for x in WORKING_DIRECTORIES.fields
             if old_conversion.get(x.key) != new_conversion.get(x.key)]
    if moved:
        warnings.append(f"The {', '.join(moved)} changed. Files already in the old locations "
                        "were not moved.")

    ffmpeg_location = new_conversion.get("ffmpeg_location")
    if ffmpeg_location and not os.path.isfile(os.path.join(ffmpeg_location, "ffmpeg")):
        warnings.append(f"There is no ffmpeg in {ffmpeg_location}, so conversions will fail "
                        "until it is installed there.")
    return warnings
