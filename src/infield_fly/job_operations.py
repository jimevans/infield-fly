"""
Module containing the operations that change jobs, shared by the command line and the web
interface, so that both change jobs by the same rules
"""

import logging
import os
from contextlib import ExitStack, contextmanager

from infield_fly.concurrency import LockHeldError, ProcessLock
from infield_fly.job_status import JobStatus
from infield_fly.episodes import find_episode_file, get_episode_number


class JobOperationError(Exception):

    """Raised when a job can't be changed as asked, with the reason to show the user"""


@contextmanager
def changing_jobs(config):
    """
    Holds the job processing lock while jobs are changed, so that a processing run, which
    keeps its own copies of jobs, can't overwrite the change, raising JobOperationError if
    a run is in progress
    """

    with ExitStack() as stack:
        # Only acquiring the lock is caught, not errors raised while changing jobs.
        try:
            stack.enter_context(ProcessLock(config.conversion.job_lock_file, "job change"))
        except LockHeldError as ex:
            raise JobOperationError("Job processing is running, so jobs can't be changed "
                                    "until it finishes.") from ex
        yield


def set_job_status(job_queue, job, status, download=None):
    """
    Sets the status of a job, returning a list of warnings about the download given. A
    download can be given for the adding status: a magnet link, or the path or URL of an
    NZB file; or for the pending status: a finished download, either an episode file or a
    folder containing one, which must be the job's episode. JobOperationError is raised if
    the status or download can't be used.
    """

    if status is None or status == JobStatus.UNKNOWN:
        raise JobOperationError("Unknown job status.")
    if not download:
        job.status = status
        job.save(logging.getLogger())
        return []
    if status == JobStatus.ADDING:
        return set_found_download(job_queue, job, download)
    if status == JobStatus.PENDING:
        set_finished_download(job, download)
        return []
    raise JobOperationError("A download can only be given for the adding or pending status.")


def set_found_download(job_queue, job, download):
    """Sets a magnet link or NZB file as the download for a job, which will be added next"""

    downloader = job_queue.get_downloader(download)
    if downloader is None:
        raise JobOperationError("For adding, give a magnet link, or the path or URL of an "
                                "NZB file.")
    try:
        return downloader.set_download(job, download)
    except ValueError as ex:
        raise JobOperationError(str(ex)) from ex


def set_finished_download(job, download):
    """
    Sets a finished download as the download of a job, which will be converted next, after
    checking that the episode file that will be converted is the job's episode
    """

    if not os.path.exists(download):
        raise JobOperationError(f"'{download}' does not exist.")
    episode_file = find_episode_file(download)
    if episode_file is None:
        raise JobOperationError(
            f"'{download}' has no episode file to convert: an .mkv or .mp4 file named with "
            "its episode number, such as s01e02.")
    file_episode = get_episode_number(os.path.basename(episode_file))
    job_episode = get_episode_number(job.query)
    if job_episode is not None and file_episode != job_episode:
        raise JobOperationError(
            f"'{os.path.basename(episode_file)}' is episode {file_episode}, but this job is "
            f"for {job_episode}.")

    download = os.path.normpath(download)
    job.set_download_status(JobStatus.PENDING, os.path.basename(download),
                            os.path.dirname(download))
    job.save(logging.getLogger())


def add_job(job_queue, config, keyword, query):
    """
    Adds a job searching for an episode of a tracked series, returning it, or raising
    JobOperationError if the series isn't tracked, or the job already exists
    """

    query = query.strip()
    if keyword not in [x.main_keyword for x in config.metadata.tracked_series]:
        raise JobOperationError(f"'{keyword}' is not the main keyword of a tracked series.")
    if query == "":
        raise JobOperationError("Enter the search string.")
    if job_queue.is_existing_job(keyword, query):
        raise JobOperationError("A job for that series and search string already exists.")
    return job_queue.create_job(keyword, query)
