"""Shared test setup for Infield Fly unit tests"""

import logging
from unittest import mock

import pytest

from infield_fly.configuration import Configuration
from infield_fly.database import EpisodeDatabase, SeriesInfo


@pytest.fixture
def make_config(tmp_path):
    """Returns a factory creating a Configuration rooted in a temporary directory"""

    def _make_config(conversion=None, metadata=None, notification=None):
        config_dict = {
            "conversion": {
                "database_directory": str(tmp_path),
                "job_directory": str(tmp_path / "jobs"),
                "staging_directory": str(tmp_path / "staging"),
                "final_directory": str(tmp_path / "final"),
                "log_directory": str(tmp_path / "logs"),
                **(conversion or {})
            },
            "metadata": metadata or {},
            "notification": notification or {}
        }
        return Configuration(str(tmp_path), config_dict)

    return _make_config


def make_episode_db(config, episodes):
    """
    Creates an episode database tracking the configured series, where series 1 is
    "The Show" with the specified episodes and other series cannot be retrieved
    """

    database = EpisodeDatabase(config)
    database.tracked_series.extend(config.metadata.tracked_series)
    database.metadata_provider = mock.Mock()
    database.metadata_provider.get_series.return_value = None
    series = SeriesInfo(1, "The Show", "Continuing", 2020)
    series.add_episodes(episodes)
    database.add_series(series)
    return database


@pytest.fixture(autouse=True)
def restore_root_logger():
    """Keeps tests that add handlers to the root logger from leaking into other tests"""

    logger = logging.getLogger()
    handlers = logger.handlers[:]
    level = logger.level
    yield
    for handler in logger.handlers[:]:
        if handler not in handlers:
            handler.close()
            logger.removeHandler(handler)
    logger.setLevel(level)
