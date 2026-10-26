"""Tests for the notification module"""

from unittest import mock

import pytest

from infield_fly.configuration import Configuration
from infield_fly.notification import Notifier

VALID_SETTINGS = {"sid": "sid", "auth_token": "token", "sending_number": "+15550001111"}


def config_with(**overrides):
    """Creates a configuration with valid notification settings, overridden as specified"""

    return Configuration("/install", {"notification": {**VALID_SETTINGS, **overrides}})


class TestCreateDefaultNotifier:

    def test_creates_notifier_with_valid_settings(self):
        notifier = Notifier.create_default_notifier(config_with())

        assert notifier.account_sid == "sid"
        assert notifier.auth_token == "token"
        assert notifier.sending_number == "+15550001111"

    @pytest.mark.parametrize("setting, message", [
        ("sid", "account SID"),
        ("auth_token", "auth token"),
        ("sending_number", "sending number"),
    ])
    @pytest.mark.parametrize("value", [None, ""])
    def test_missing_setting_returns_none(self, caplog, setting, message, value):
        notifier = Notifier.create_default_notifier(config_with(**{setting: value}))

        assert notifier is None
        assert message in caplog.text


class TestNotify:

    def test_sends_message_and_returns_sid(self):
        notifier = Notifier("sid", "token", "+15550001111")
        with mock.patch("infield_fly.notification.Client") as client_class:
            client_class.return_value.messages.create.return_value.sid = "SM123"

            result = notifier.notify("+15552223333", "Hello")

        assert result == "SM123"
        client_class.assert_called_once_with("sid", "token")
        client_class.return_value.messages.create.assert_called_once_with(
            body="Hello", from_="+15550001111", to="+15552223333")
