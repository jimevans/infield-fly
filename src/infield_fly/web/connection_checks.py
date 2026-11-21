"""
Module containing checks of the connections to the services Infield Fly uses, made with
settings that may not be saved yet
"""

import logging
import threading
from contextlib import contextmanager

import requests

from infield_fly.database import TVMetadataProvider
from infield_fly.usenet.newznab import NewznabSearcher
from infield_fly.usenet.sabnzbd import SabnzbdClient, SabnzbdError


@contextmanager
def captured_warnings():
    """
    Collects the warnings logged by the current thread while the block runs, so that the
    reason for a failure that is only logged can be shown
    """

    messages = []
    thread = threading.get_ident()

    class WarningCollector(logging.Handler):

        """Collects the messages of warnings logged by the thread"""

        def emit(self, record):
            if record.thread == thread and record.levelno >= logging.WARNING:
                messages.append(record.getMessage())

    collector = WarningCollector()
    logging.getLogger().addHandler(collector)
    try:
        yield messages
    finally:
        logging.getLogger().removeHandler(collector)


def check_sabnzbd(config):
    """
    Checks the connection to SABnzbd, and that it has the configured category, returning
    whether the check passed and a message describing the result
    """

    client = SabnzbdClient(config.download)
    try:
        queue = client.call_api({"mode": "queue", "limit": 0})
        categories = client.call_api({"mode": "get_cats"}).get("categories", [])
    except SabnzbdError as ex:
        return False, str(ex)

    version = (queue.get("queue") or {}).get("version")
    message = f"Connected to SABnzbd{' ' + version if version else ''}."
    category = config.download.sabnzbd_category
    if category is not None and category not in categories:
        return False, (f"{message} But it has no category '{category}'; its categories are "
                       f"{', '.join(categories) or 'none'}.")
    return True, message


def check_newznab(config):
    """
    Checks searching the Newznab indexer with a small search, returning whether the check
    passed and a message describing the result
    """

    if not config.search.is_enabled:
        return False, "Enter the indexer's API key first."
    with captured_warnings() as warnings:
        results = NewznabSearcher(config.search).get_results(
            {"t": "search", "q": "test", "limit": 1})
    if results is None:
        return False, "Searching failed. " + " ".join(warnings)
    return True, "The indexer answered a test search."


def check_tvdb(config):
    """Checks logging in to TheTVDB, returning whether it passed and a message"""

    if config.metadata.api_key == "":
        return False, "Enter the TheTVDB API key first."
    provider = TVMetadataProvider(config.metadata)
    try:
        provider.authenticate()
    except requests.RequestException as ex:
        return False, f"Could not reach TheTVDB: {ex}"
    if provider.token is None:
        return False, "TheTVDB refused the API key and PIN."
    return True, "Logged in to TheTVDB."


CHECKS = {"sabnzbd": check_sabnzbd, "newznab": check_newznab, "tvdb": check_tvdb}
