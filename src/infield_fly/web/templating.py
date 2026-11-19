"""Module containing the page templates of the web interface, and helpers used by them"""

import os

from starlette.templating import Jinja2Templates

WEB_DIRECTORY = os.path.dirname(os.path.abspath(__file__))

templates = Jinja2Templates(directory=os.path.join(WEB_DIRECTORY, "templates"))


def app_path(request, name, **path_params):
    """Gets the path, including the root path of the application, of a named route"""

    return request.url_for(name, **path_params).path


def add_flash(request, kind, message):
    """
    Adds a message to show on the next page shown to the user, such as the page to which
    they are redirected after saving; kind is "success" or "warning"
    """

    request.session["flash"] = request.session.get("flash", []) + [[kind, message]]


def pop_flashes(request):
    """Gets the messages to show on this page, removing them so they are shown only once"""

    return request.session.pop("flash", [])


templates.env.globals["app_path"] = app_path
templates.env.globals["pop_flashes"] = pop_flashes
