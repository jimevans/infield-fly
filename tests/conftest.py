"""Shared test setup for Infield Fly unit tests"""

import logging
from unittest import mock

import pytest

from infield_fly.configuration import Configuration
from infield_fly.database import EpisodeDatabase, SeriesInfo
from infield_fly.jobs import Job


# Tracked series settings where series 1 is "The Show", searched for in 1080p and as a
# download-only job, and series 2 cannot be retrieved from the episode database.
TRACKED_SERIES = {
    "show": {"id": 1, "description": "The Show", "enable_search": True,
             "search_configs": [{"search_terms": ["1080p"]}, {"download_only": True}]},
    "missing": {"id": 2, "description": "Missing", "enable_search": True}
}


@pytest.fixture
def make_config(tmp_path):
    """Returns a factory creating a Configuration rooted in a temporary directory"""

    def _make_config(conversion=None, metadata=None, notification=None, search=None,
                     download=None):
        config_dict = {
            "conversion": {
                "database_directory": str(tmp_path),
                "job_directory": str(tmp_path / "jobs"),
                "staging_directory": str(tmp_path / "staging"),
                "final_directory": str(tmp_path / "final"),
                "log_directory": str(tmp_path / "logs"),
                "nzb_directory": str(tmp_path / "nzb"),
                **(conversion or {})
            },
            "metadata": metadata or {},
            "notification": notification or {},
            "search": search or {},
            "download": download or {}
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


def save_job(config, status, **values):
    """Saves a job with the specified status and values to the configured job directory"""

    job = Job(config.conversion.job_directory, {"status": status.value, **values})
    job.save(logging.getLogger())
    return job


def load_job(config, job_id):
    """Loads a job from the configured job directory"""

    return Job.load(config.conversion.job_directory, job_id)


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
