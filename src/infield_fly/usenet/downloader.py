"""Module for finding NZB files for jobs on a Newznab indexer and downloading them using SABnzbd"""

import logging
import os
import re
import shutil

from infield_fly.concurrency import atomic_write
from infield_fly.downloader import Downloader
from infield_fly.episodes import find_episode_file, get_episode_number
from infield_fly.job_status import JobStatus
from infield_fly.usenet.newznab import NewznabSearcher, NzbResult
from infield_fly.usenet.sabnzbd import SabnzbdClient, SabnzbdError
from infield_fly.usenet.validation import validate_nzb

logger = logging.getLogger(__name__)


class NzbDownloader(Downloader):

    """
    Download track searching a Newznab indexer for NZB files, validating them, and adding
    them to, and querying them from, a SABnzbd instance
    """

    name = "sabnzbd"

    def search_downloads(self, jobs):
        """Searches the Newznab indexer for NZB files for waiting jobs"""

        if not self.config.search.is_enabled:
            logger.info("No Newznab API key configured; not searching for downloads")
            return

        searcher = NewznabSearcher(self.config.search)
        for job in jobs:
            job.status = JobStatus.SEARCHING
            job.save()
            if not self.find_nzb(job, searcher, jobs):
                job.status = JobStatus.WAITING
                job.save()

    def find_nzb(self, job, searcher, jobs):
        """
        Finds the best search result for a job whose NZB file passes validation, and sets
        it as the job's download. The job's search strings are tried in order, and results
        that fail validation are rejected for the job and for the other jobs being searched
        for the same episode. Returns a value indicating whether a download was found.
        """

        remaining_candidates = self.config.search.max_candidates
        for query in job.search_queries:
            results = searcher.search(query, job.rejected, job.release_groups.get(query, []))
            for result in results:
                if remaining_candidates == 0:
                    return False
                remaining_candidates -= 1
                content = searcher.fetch_nzb(result)
                if content is None:
                    # Download failures may be temporary, so the result is not rejected.
                    continue
                validation = validate_nzb(content, result.size)
                for warning in validation.warnings:
                    logger.warning("NZB file for '%s': %s", result.title, warning)
                if not validation.is_valid:
                    logger.warning("Rejecting search result '%s' for job %s: %s",
                                        result.title, job.job_id,
                                        "; ".join(validation.problems))
                    self.reject_result(job, result.guid, jobs)
                    continue

                logger.info(
                    "Search result for query string '%s'. Job ID: %s, Title: '%s'",
                    query, job.job_id, result.title)
                self.set_nzb_result(job, result, self.save_nzb(job, content))
                return True

        return False

    def reject_result(self, job, guid, jobs):
        """Rejects a search result for a job, and for the other jobs for the same episode"""

        for episode_job in [job] + [x for x in jobs
                                    if x is not job and job.episode_key is not None
                                    and x.episode_key == job.episode_key]:
            if guid not in episode_job.rejected:
                episode_job.rejected = episode_job.rejected + [guid]
                if episode_job is not job:
                    episode_job.save()

    def recognizes_download(self, location):
        """Gets a value indicating whether a download is the path or URL of an NZB file"""

        return os.path.isfile(location) or location.startswith(("http://", "https://"))

    def set_download(self, job, location):
        """Validates an NZB file at a path or URL, and sets it as the download for a job"""

        title = os.path.splitext(os.path.basename(location.split("?")[0]))[0]
        if os.path.isfile(location):
            with open(location, "rb") as nzb_file:
                content = nzb_file.read()
        else:
            content = NewznabSearcher(self.config.search).fetch_nzb(
                NzbResult(title, None, link=location))
            if content is None:
                raise ValueError(f"Could not download NZB file from '{location}'")

        validation = validate_nzb(content)
        if not validation.is_valid:
            raise ValueError(f"NZB file failed validation: {'; '.join(validation.problems)}")

        self.set_nzb_result(job, NzbResult(title, None), self.save_nzb(job, content))
        return validation.warnings

    def set_nzb_result(self, job, search_result, nzb_file_path):
        """Sets the NZB search result, and the path of its saved NZB file, for a job"""

        job.status = JobStatus.ADDING
        job.downloader = self.name
        job.title = search_result.title
        job.nzb_guid = search_result.guid
        job.nzb_file = nzb_file_path
        job.save()

    def save_nzb(self, job, content):
        """Saves the contents of the NZB file for a job, returning the path to the file"""

        os.makedirs(self.config.conversion.nzb_directory, exist_ok=True)
        nzb_file_path = os.path.join(self.config.conversion.nzb_directory, f"{job.job_id}.nzb")
        with atomic_write(nzb_file_path, binary=True) as nzb_file:
            nzb_file.write(content)
        return nzb_file_path

    def add_downloads(self, jobs):
        """Adds the NZB files of jobs to the SABnzbd client"""

        if len(jobs) == 0:
            logger.info("No NZB files to add during job processing")
            return

        logger.info("Adding downloads to SABnzbd instance on %s",
                         self.config.download.sabnzbd_host)
        client = SabnzbdClient(self.config.download)
        for job in jobs:
            if job.nzb_file is None or not os.path.isfile(job.nzb_file):
                logger.warning("NZB file for job %s is missing; searching again",
                                    job.job_id)
                self.reject_nzb(job)
                continue
            try:
                job.nzo_id = client.add_nzb(job.nzb_file, job.title or job.job_id)
            except (SabnzbdError, OSError) as ex:
                # Leave the job in the adding state so it is retried on the next run.
                logger.error("Adding NZB file for job %s failed: %s", job.job_id, ex)
                continue
            os.remove(job.nzb_file)
            job.nzb_file = None
            job.set_download_status(JobStatus.DOWNLOADING, job.title)
            job.save()

    def query_downloads(self, jobs):
        """
        Marks jobs whose NZB files have finished downloading in the SABnzbd client as
        pending, and rejects those whose downloads failed so they are searched for again
        """

        if len(jobs) == 0:
            logger.info("No downloading NZB files to query for status during job "
                             "processing")
            return

        logger.info("Marking completed downloads on SABnzbd instance at %s",
                         self.config.download.sabnzbd_host)
        client = SabnzbdClient(self.config.download)
        for job in jobs:
            try:
                download = client.get_download_status(job.nzo_id)
                if download.status == "Failed":
                    client.delete_history_item(job.nzo_id)
            except SabnzbdError as ex:
                logger.error("Querying download for job %s failed: %s", job.job_id, ex)
                continue

            if download.status == "Completed":
                storage = os.path.normpath(download.storage)
                job.set_download_status(JobStatus.PENDING,
                                        os.path.basename(storage),
                                        os.path.dirname(storage))
                job.save()
            elif download.status is None and self.recover_finished_download(client, job):
                continue
            elif download.status in ("Failed", None):
                logger.warning("Download of '%s' for job %s failed (%s); searching again",
                                    job.title, job.job_id,
                                    download.fail_message or "not found in SABnzbd")
                self.reject_nzb(job)

    def recover_finished_download(self, client, job):
        """
        Looks for the folder of a job's download in SABnzbd's completed directory, when
        SABnzbd no longer knows the download, as when it is removed from SABnzbd's history
        between checks. If a folder named after the download's job holds the job's episode,
        it is used as the job's download, rather than downloading the episode again, and
        True is returned.
        """

        try:
            complete_directory = client.get_complete_directory()
        except SabnzbdError as ex:
            logger.warning("Could not look for the download of job %s: %s",
                                job.job_id, ex)
            return False
        folder = find_job_folder(complete_directory, job.title, get_episode_number(job.query))
        if folder is None:
            return False

        logger.warning("Download of '%s' for job %s is no longer in SABnzbd, but was "
                            "found in %s; converting it", job.title, job.job_id, folder)
        # SABnzbd no longer has the download, so there is no history item to remove later.
        job.nzo_id = None
        job.set_download_status(JobStatus.PENDING, os.path.basename(folder),
                                os.path.dirname(folder))
        job.save()
        return True

    def remove_download(self, job):
        """
        Deletes the completed download of a converted job, and removes it from the SABnzbd
        history, when configured to do so. When the download is in a folder of its own in
        SABnzbd's completed directory, as SABnzbd's history confirms, the whole folder is
        deleted, with any other files left in it. Failures are logged, leaving the download
        in place.
        """

        if not self.config.download.delete_completed_downloads:
            return

        client = SabnzbdClient(self.config.download)
        # SABnzbd only deletes the files of failed jobs from its history, so the files of
        # the completed download are deleted directly.
        download_path = os.path.join(job.download_directory, job.name)
        folder = self.find_download_folder(client, job, download_path)
        delete_path = folder if folder is not None else download_path
        logger.info("Deleting completed download %s", delete_path)
        try:
            if os.path.isdir(delete_path):
                shutil.rmtree(delete_path)
            elif os.path.isfile(delete_path):
                os.remove(delete_path)
        except OSError as ex:
            logger.warning("Deleting download for job %s failed: %s", job.job_id, ex)
            return

        if job.nzo_id is None:
            return
        try:
            client.delete_history_item(job.nzo_id)
        except SabnzbdError as ex:
            logger.warning("Removing download for job %s from SABnzbd history failed: %s",
                                job.job_id, ex)

    def find_download_folder(self, client, job, download_path):
        """
        Gets the folder of a job's download, as SABnzbd's history records its location, so
        that the whole folder can be deleted, or None if SABnzbd doesn't confirm such a
        folder. SABnzbd records the folder itself, or for some downloads, the file in it, in
        which case the file's folder is used only if it is named after the download's job,
        so that a folder shared by other downloads, such as a category's folder, is never
        mistaken for it. The folder must be inside SABnzbd's completed directory, but not the
        directory itself, and contain the job's download path, which is the file in the
        folder when the job was given its download by hand.
        """

        if job.nzo_id is None:
            return None
        try:
            download = client.get_download_status(job.nzo_id)
            complete_directory = client.get_complete_directory()
        except SabnzbdError as ex:
            logger.warning("Could not find the folder of the download for job %s: %s",
                                job.job_id, ex)
            return None
        if not download.storage or not complete_directory:
            return None

        folder = os.path.realpath(download.storage)
        if os.path.isfile(folder) and is_job_folder(os.path.dirname(folder),
                                                    download.name or job.title):
            folder = os.path.dirname(folder)
        complete_directory = os.path.realpath(complete_directory)
        if (not os.path.isdir(folder) or folder == complete_directory
                or os.path.commonpath([complete_directory, folder]) != complete_directory
                or os.path.commonpath([folder, os.path.realpath(download_path)]) != folder):
            return None
        return folder

    def reject_nzb(self, job):
        """Rejects the NZB file found for a job, and returns the job to waiting for a search"""

        if job.nzb_guid is not None:
            job.rejected = job.rejected + [job.nzb_guid]
        job.nzb_guid = None
        job.nzo_id = None
        job.status = JobStatus.WAITING
        job.save()


def is_job_folder(folder, job_name):
    """
    Gets a value indicating whether a folder is named after a SABnzbd job, as SABnzbd names
    the folder of a job's download: the job's name, followed by a number, as ".1", when a
    folder of that name already existed
    """

    name = os.path.basename(os.path.normpath(folder))
    return bool(job_name) and (name == job_name
                               or re.fullmatch(re.escape(job_name) + r"\.[0-9]+", name)
                               is not None)


def find_job_folder(complete_directory, job_name, episode_number):
    """
    Finds the folder of a download named after a SABnzbd job in SABnzbd's completed
    directory, or a folder in it, such as a category's folder, whose episode file is the
    episode, returning the most recently changed such folder, or None if there is none. An
    episode number of None accepts any episode file.
    """

    if not complete_directory or not os.path.isdir(complete_directory):
        return None
    parents = [complete_directory] + [
        os.path.join(complete_directory, x) for x in os.listdir(complete_directory)
        if os.path.isdir(os.path.join(complete_directory, x))]
    candidates = []
    for parent in parents:
        for name in os.listdir(parent):
            folder = os.path.join(parent, name)
            if not os.path.isdir(folder) or not is_job_folder(folder, job_name):
                continue
            episode_file = find_episode_file(folder)
            if episode_file is not None and episode_number in (
                    None, get_episode_number(os.path.basename(episode_file))):
                candidates.append(folder)
    return max(candidates, key=os.path.getmtime, default=None)
