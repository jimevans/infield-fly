"""Shared setup for tests of the web interface"""

import copy
import json

import pytest
from starlette.testclient import TestClient

from conftest import TRACKED_SERIES, working_directories
from infield_fly.cli import load_config
from infield_fly.settings_file import SettingsFile
from infield_fly.web.app import create_app

ROOT = "/infield-fly"
SAME_ORIGIN = {"Origin": "https://seed.example"}


class FakeAuthenticator:

    def __init__(self):
        self.limited = False
        self.calls = []

    def authenticate(self, user_name, password, client):
        self.calls.append((user_name, password, client))
        return user_name == "me" and password == "secret"

    def is_limited(self, _):
        return self.limited


def make_settings(tmp_path, tracked_series=None, **sections):
    """
    Gets settings with working directories in a temporary directory, copied so that tests
    can change them
    """

    return copy.deepcopy({
        "conversion": working_directories(tmp_path),
        "metadata": {"tracked_series": TRACKED_SERIES if tracked_series is None
                     else tracked_series},
        **sections,
    })


@pytest.fixture
def settings_path(tmp_path):
    """The path of the settings file, which tests can replace before the app reads it"""

    path = tmp_path / "settings.json"
    path.write_text(json.dumps(make_settings(tmp_path), indent=4), encoding="utf-8")
    return path


@pytest.fixture
def config(settings_path):
    return load_config(str(settings_path))


@pytest.fixture
def authenticator():
    return FakeAuthenticator()


@pytest.fixture
def app(settings_path, authenticator):
    return create_app(lambda: load_config(str(settings_path)), authenticator, "test-secret",
                      settings_file=SettingsFile(str(settings_path)), root_path=ROOT)


@pytest.fixture
def client(app):
    # As behind the reverse proxy, where uvicorn adds the root path back to the path of
    # each request, requests are made to paths under the root path.
    with TestClient(app, base_url=f"https://seed.example{ROOT}",
                    root_path=ROOT) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client):
    response = client.post("/login", data={"user_name": "me", "password": "secret"},
                           headers=SAME_ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    return client
