"""Tests for the web interface authentication module"""

import os
import stat
import sys
from unittest import mock

import pytest

from infield_fly.web.auth import Authenticator, load_or_create_secret_key


class FakeClock:

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def authenticator(clock):
    authenticator = Authenticator("me", "infield-fly", max_client_failures=3,
                                  max_total_failures=5, failure_window_seconds=60, clock=clock)
    with mock.patch.object(authenticator, "check_password",
                           side_effect=lambda user, password: password == "secret"):
        yield authenticator


class TestAuthenticator:

    def test_correct_user_and_password(self, authenticator):
        assert authenticator.authenticate("me", "secret", "1.2.3.4")

        authenticator.check_password.assert_called_once_with("me", "secret")

    def test_wrong_password_is_logged(self, authenticator, caplog):
        assert not authenticator.authenticate("me", "wrong", "1.2.3.4")

        assert "Failed login for user 'me' from 1.2.3.4" in caplog.text

    def test_user_name_is_logged_escaped(self, authenticator, caplog):
        authenticator.authenticate("x\nFailed login for user 'y' from 9.9.9.9", "wrong",
                                   "1.2.3.4")

        assert caplog.messages == [
            "Failed login for user \"x\\nFailed login for user 'y' from 9.9.9.9\" from 1.2.3.4"]

    def test_other_user_is_refused_even_with_correct_password(self, authenticator, caplog):
        assert not authenticator.authenticate("root", "secret", "1.2.3.4")

        # The password is checked for the allowed user only, never for the user named.
        authenticator.check_password.assert_called_once_with("me", "secret")
        assert "Failed login for user 'root' from 1.2.3.4" in caplog.text

    def test_client_is_limited_after_repeated_failures(self, authenticator, caplog):
        for _ in range(3):
            authenticator.authenticate("me", "wrong", "1.2.3.4")
        authenticator.check_password.reset_mock()

        assert authenticator.is_limited("1.2.3.4")
        assert not authenticator.authenticate("me", "secret", "1.2.3.4")
        authenticator.check_password.assert_not_called()
        assert "(too many failed logins)" in caplog.text
        assert not authenticator.is_limited("5.6.7.8")
        assert authenticator.authenticate("me", "secret", "5.6.7.8")

    def test_all_clients_are_limited_after_many_failures(self, authenticator):
        for client in range(5):
            authenticator.authenticate("me", "wrong", f"10.0.0.{client}")

        assert authenticator.is_limited("5.6.7.8")
        assert not authenticator.authenticate("me", "secret", "5.6.7.8")

    def test_failures_expire(self, authenticator, clock):
        for _ in range(3):
            authenticator.authenticate("me", "wrong", "1.2.3.4")
        clock.now += 61

        assert not authenticator.is_limited("1.2.3.4")
        assert authenticator.authenticate("me", "secret", "1.2.3.4")

    def test_successful_login_does_not_count_as_failure(self, authenticator):
        for _ in range(5):
            authenticator.authenticate("me", "secret", "1.2.3.4")

        assert not authenticator.is_limited("1.2.3.4")


class TestCheckPassword:

    def test_checks_password_with_pam_service(self):
        pam_module = mock.Mock()
        pam_module.pam.return_value.authenticate.return_value = True

        with mock.patch.dict(sys.modules, {"pam": pam_module}):
            assert Authenticator("me", "custom").check_password("me", "secret")

        pam_module.pam.return_value.authenticate.assert_called_once_with(
            "me", "secret", service="custom")


class TestSecretKey:

    def test_creates_key_readable_only_by_owner(self, tmp_path):
        path = tmp_path / "config" / ".web_secret"

        key = load_or_create_secret_key(str(path))

        assert len(key) == 64
        assert path.read_text(encoding="utf-8") == key
        if sys.platform != "win32":
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_reuses_existing_key(self, tmp_path):
        path = str(tmp_path / ".web_secret")

        assert load_or_create_secret_key(path) == load_or_create_secret_key(path)

    def test_replaces_empty_key(self, tmp_path):
        path = tmp_path / ".web_secret"
        path.write_text("\n", encoding="utf-8")

        assert len(load_or_create_secret_key(str(path))) == 64
