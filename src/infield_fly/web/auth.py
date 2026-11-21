"""Module containing authentication of web interface users against the host's accounts"""

import logging
import os
import secrets
import time
from collections import deque

logger = logging.getLogger(__name__)

# Logged on each failed login, and matched by the fail2ban filter in the deploy directory.
# The user name is logged as a quoted, escaped string, so that a user name containing a
# line break cannot forge a log line blaming another address.
FAILED_LOGIN_MESSAGE = "Failed login for user %r from %s"
LIMITED_LOGIN_MESSAGE = FAILED_LOGIN_MESSAGE + " (too many failed logins)"


class Authenticator:

    """
    Authenticates the user of the web interface with the user name and password of an
    account on the host, using PAM. Only the allowed user may log in, which by default is
    the account running the web interface. That is the only account whose password PAM
    can check without root privileges, so the web interface never needs them.

    Failed logins are limited per client and across all clients. Once a limit is
    reached, further logins are refused without checking the password until enough
    failures have aged out of the window.
    """

    def __init__(self, allowed_user, pam_service="infield-fly", max_client_failures=5,
                 max_total_failures=20, failure_window_seconds=900, clock=time.monotonic):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.allowed_user = allowed_user
        self.pam_service = pam_service
        self.max_client_failures = max_client_failures
        self.max_total_failures = max_total_failures
        self.failure_window_seconds = failure_window_seconds
        self.clock = clock
        self.failures = deque()

    def is_limited(self, client):
        """Gets a value indicating whether logins from a client are currently refused"""

        self._expire_failures()
        client_failures = sum(1 for _, failed_client in self.failures if failed_client == client)
        return (client_failures >= self.max_client_failures
                or len(self.failures) >= self.max_total_failures)

    def authenticate(self, user_name, password, client):
        """
        Authenticates a user name and password from a client, returning True if the user is
        the allowed user and the password is correct. This blocks while PAM checks the
        password, which takes seconds when it is wrong, so call it off the event loop.
        """

        if self.is_limited(client):
            logger.warning(LIMITED_LOGIN_MESSAGE, user_name, client)
            return False

        # Compare in constant time, and check the password even for the wrong user, so
        # that the response does not reveal whether the user name was right.
        is_allowed_user = secrets.compare_digest(user_name.encode("utf-8"),
                                                 self.allowed_user.encode("utf-8"))
        is_password_correct = self.check_password(self.allowed_user, password)
        if is_allowed_user and is_password_correct:
            return True

        self.failures.append((self.clock(), client))
        logger.warning(FAILED_LOGIN_MESSAGE, user_name, client)
        return False

    def check_password(self, user_name, password):
        """Checks the password of a user with PAM"""

        import pam  # pylint: disable=import-outside-toplevel

        return pam.pam().authenticate(user_name, password, service=self.pam_service)

    def _expire_failures(self):
        oldest_allowed = self.clock() - self.failure_window_seconds
        while self.failures and self.failures[0][0] < oldest_allowed:
            self.failures.popleft()


def load_or_create_secret_key(path):
    """
    Gets the key with which session cookies are signed from a file readable only by its
    owner, creating the file with a new random key if it does not exist, so that sessions
    survive restarts of the web interface
    """

    try:
        with open(path, encoding="utf-8") as key_file:
            key = key_file.read().strip()
        if key != "":
            return key
    except FileNotFoundError:
        pass

    key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(file_descriptor, "w", encoding="utf-8") as key_file:
        key_file.write(key)
    return key
