"""Module containing job information for automated processing"""

import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import MISSING, dataclass, field, fields, replace
from datetime import datetime
from time import perf_counter
from typing import Optional

from infield_fly.concurrency import atomic_write
from infield_fly.conversion import ConversionSettings, Converter
from infield_fly.database import EpisodeDatabase
from infield_fly.episodes import find_episode_file, get_episode_number
from infield_fly.job_status import JobStatus
from infield_fly.torrent.downloader import TorrentDownloader
from infield_fly.usenet.downloader import NzbDownloader

logger = logging.getLogger(__name__)

# The download tracks, in the order in which they search for downloads for waiting jobs.
DOWNLOADER_CLASSES = (NzbDownloader, TorrentDownloader)


class JobQueue:

    """Queue of jobs being processed"""

    def __init__(self, configuration):
        self.config = configuration

    def get_job_by_id(self, job_id):
        """Gets a job by its ID, if it exists; otherwise, returns None"""

        return next((job for job in self.load_jobs() if job.job_id == job_id), None)

    def create_job(self, keyword, query, is_download_only=False):
        """Creates a new job using the specified keyword and query string"""

        job = Job(self.job_queue_file_path)
        job.keyword = keyword
        job.query = query
        job.is_download_only = is_download_only
        job.update_converted_file_name(self.config)
        job.save()
        return job

    def _get_jobs_by_status(self, status):
        """Gets all jubs with a specific status"""

        jobs = self.load_jobs()
        return [x for x in jobs if x.status == status]

    def refresh_jobs(self, airdate):
        """
        Removes completed jobs added before the airdate, updates the converted file names of
        waiting jobs, and creates jobs for episodes of tracked series aired on the airdate
        """

        completed_job_list = [x for x in self._get_jobs_by_status(JobStatus.COMPLETED)
                              if x.added != airdate.strftime("%Y-%m-%d")]
        for job in completed_job_list:
            job.delete()

        for job in self._get_jobs_by_status(JobStatus.WAITING):
            job.update_converted_file_name(self.config)
            job.save()

        self._create_new_search_jobs(airdate)

    @property
    def downloaders(self):
        """Gets the download tracks with which to search for and download files for jobs"""

        return [x(self.config) for x in DOWNLOADER_CLASSES]

    def get_downloader(self, location):
        """
        Gets the download track recognizing a download specified by the user, or None if
        no track recognizes it
        """

        for downloader in self.downloaders:
            if downloader.recognizes_download(location):
                return downloader
        return None

    def search_downloads(self):
        """Searches for downloads for waiting jobs with each download track in turn"""

        all_jobs = self.load_jobs()
        self._share_rejections(all_jobs)
        job_list = [x for x in all_jobs
                    if x.status in (JobStatus.WAITING, JobStatus.SEARCHING)]
        if not job_list:
            logger.info("No waiting jobs to search for during job processing")
            return

        for downloader in self.downloaders:
            downloader.search_downloads(
                [x for x in job_list if x.status in (JobStatus.WAITING, JobStatus.SEARCHING)])

    def _share_rejections(self, job_list):
        """
        Shares the downloads rejected for each job with the other jobs for the same episode,
        so that a download found to be incomplete or unavailable is not tried again for the
        episode
        """

        episodes = {}
        for job in job_list:
            if job.episode_key is not None:
                episodes.setdefault(job.episode_key, []).append(job)

        for episode_jobs in episodes.values():
            rejected = []
            for job in episode_jobs:
                rejected.extend(x for x in job.rejected if x not in rejected)
            for job in episode_jobs:
                if job.rejected != rejected:
                    job.rejected = rejected[:]
                    job.save()

    def reset_rejections(self, job):
        """
        Clears the downloads rejected for a job, and for the other jobs for the same episode,
        so that they are tried again by the next search. Returns the jobs that were reset.
        """

        reset_jobs = [x for x in self.load_jobs()
                      if x.job_id == job.job_id
                      or (job.episode_key is not None and x.episode_key == job.episode_key)]
        for reset_job in reset_jobs:
            if reset_job.rejected:
                reset_job.rejected = []
                reset_job.save()
        return reset_jobs

    def add_downloads(self):
        """Adds the found downloads of adding jobs to the download client of their tracks"""

        job_list = self._get_jobs_by_status(JobStatus.ADDING)
        for downloader, jobs in self._group_jobs_by_downloader(job_list):
            downloader.add_downloads(jobs)

    def query_downloads_status(self):
        """Marks downloading jobs whose downloads have finished in their tracks as pending"""

        job_list = self._get_jobs_by_status(JobStatus.DOWNLOADING)
        for downloader, jobs in self._group_jobs_by_downloader(job_list):
            downloader.query_downloads(jobs)

    def _group_jobs_by_downloader(self, job_list):
        """
        Groups jobs by the download track handling them, returning a list of each track
        with its jobs, and logging jobs whose track is unknown
        """

        downloaders = self.downloaders
        for job in job_list:
            if job.downloader not in [x.name for x in downloaders]:
                logger.warning("Job %s has unknown downloader '%s'",
                                    job.job_id, job.downloader)
        return [(x, [job for job in job_list if job.downloader == x.name])
                for x in downloaders]

    def perform_conversions(self, is_unattended_mode=False):
        """Executes all pending conversion jobs, converting files to the proper format"""

        pending_job_list = self._get_jobs_by_status(JobStatus.PENDING)
        if not pending_job_list:
            logger.info("No files to convert during job processing")
            return

        for job in pending_job_list:
            job.status = JobStatus.CONVERTING
            job.save()
        for job in pending_job_list:
            try:
                if job.is_download_only:
                    self._copy_downloaded_files(job, is_unattended_mode)
                else:
                    self._convert_downloaded_files(job, is_unattended_mode)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                # Return the job to pending so it is retried on the next run,
                # rather than leaving it stuck in the converting state.
                logger.error("Processing job %s failed: %s", job.job_id, ex)
                job.status = JobStatus.PENDING
                job.save()

    def _create_new_search_jobs(self, airdate):
        """Creates new search jobs based on airdate"""

        episode_db = EpisodeDatabase.load_from_cache(self.config)
        for tracked_series in self.config.metadata.tracked_series:
            series = episode_db.get_series(tracked_series.series_id)
            if series is None:
                continue
            series_episodes_since_last_search = series.get_episodes_by_airdate(
                airdate, airdate)
            for series_episode in series_episodes_since_last_search:
                for search in tracked_series.get_episode_searches(
                        series_episode.season_number, series_episode.episode_number):
                    job = self.create_search_job(tracked_series.main_keyword, search)
                    if job is not None and not search.is_download_only:
                        job.converted_file_name = "".join(
                            self.config.conversion.string_substitutions.get(c, c)
                            for c in series_episode.plex_title).strip()
                        job.save()

    def create_search_job(self, keyword, search):
        """
        Creates a job for a search for downloads of an episode, returning the job, or None
        if a job for the search already exists
        """

        if self.is_existing_job(keyword, search.queries[0], search.is_download_only):
            return None
        job = self.create_job(keyword, search.queries[0], search.is_download_only)
        job.fallback_queries = search.queries[1:]
        job.release_groups = dict(search.release_groups)
        job.save()
        return job

    def _copy_downloaded_files(self, job, is_unattended_mode):
        """Copies downloaded job files to a destination directory"""

        src_dir = os.path.join(job.download_directory, job.name)
        dest_dir = os.path.join(self.config.conversion.final_directory, job.name)

        logger.info("Copying downloaded files from %s to %s", src_dir, dest_dir)
        start_time = perf_counter()
        shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
        end_time = perf_counter()
        if is_unattended_mode:
            logger.info("Copy completed in %s seconds", end_time - start_time)

        self._mark_job_complete(job)

    def _convert_downloaded_files(self, job, is_unattended_mode):
        """Converts downloaded job files"""

        src_path = os.path.join(job.download_directory, job.name)
        src_file = find_episode_file(src_path)
        if src_file is None:
            logger.warning("No episode files found to convert in %s", src_path)
            job.status = JobStatus.PENDING
            job.save()
            return

        os.makedirs(self.config.conversion.staging_directory, exist_ok=True)
        os.makedirs(self.config.conversion.final_directory, exist_ok=True)
        dest_file = os.path.join(self.config.conversion.staging_directory,
                                 f"{job.converted_file_name}.mp4")
        converter = Converter(src_file,
                              dest_file,
                              self.config.conversion.ffmpeg_location,
                              is_unattended_mode)
        if is_unattended_mode:
            logger.info("Starting conversion")
        start_time = perf_counter()
        output_files = converter.convert_file(ConversionSettings(
            convert_video=False, convert_audio=True, convert_subtitles=True))
        end_time = perf_counter()
        if is_unattended_mode:
            logger.info(
                "Conversion completed in %s seconds", end_time - start_time)

        # Move before marking complete, so a failed move leaves the job retryable.
        # shutil.move handles staging and final directories on different volumes.
        for output_file in output_files:
            shutil.move(output_file,
                        os.path.join(self.config.conversion.final_directory,
                                     os.path.basename(output_file)))
        self._mark_job_complete(job)
        for downloader in self.downloaders:
            if downloader.name == job.downloader:
                downloader.remove_download(job)

    def _mark_job_complete(self, job):
        """Marks a job as completed"""

        job.status = JobStatus.COMPLETED
        job.save()
        if datetime.now().strftime("%Y-%m-%d") != job.added:
            job.delete()

    def is_existing_job(self, keyword, search_string, is_download_only=False):
        """
        Gets a value indicating whether a job for a specified keyword and search string,
        either converting its download or only downloading it, already exists
        """

        for job in self.load_jobs():
            if (job.keyword == keyword and job.query == search_string
                    and job.is_download_only == is_download_only):
                return True

        return False

    def load_jobs(self):
        """Loads all job files in the cache directory"""

        if not os.path.exists(self.job_queue_file_path):
            os.makedirs(self.job_queue_file_path)

        jobs = []
        for job_file in os.listdir(self.job_queue_file_path):
            # Job files are named by job ID. Files starting with a dot, such as the lock
            # file and job files being written, are not jobs.
            if job_file.startswith("."):
                continue
            job = Job.load(self.job_queue_file_path, job_file)
            if job is not None:
                jobs.append(job)

        return jobs

    @property
    def job_queue_file_path(self):
        """Gets the path to the job queue directory"""

        return self.config.conversion.job_directory


def new_job_id():
    """Gets a new, unique job ID"""

    return str(uuid.uuid1())


def today():
    """Gets today's date, as YYYY-MM-DD"""

    return datetime.now().strftime("%Y-%m-%d")


# A dataclass with eq=False, since a job is identified by its file, rather than its values:
# two jobs are equal only if they are the same object, as for other objects. It has a field
# for each value of a job file, more than the project's limit for other classes.
@dataclass(eq=False)
class Job:  # pylint: disable=too-many-instance-attributes

    """
    A job to be processed. Its fields are saved in the job's file, under their names, or the
    keys in their metadata. Keys in the file that this code doesn't know are written back as
    they were read, as is the order of the keys, and fields that were never set aren't
    written, except those always written for a new job.
    """

    # The directory of the job's file, which isn't saved in it.
    directory: str = field(metadata={"saved": False})
    # The ID of the job, which names its file.
    job_id: str = field(default_factory=new_job_id, metadata={"key": "id", "always": True})
    status: JobStatus = field(default=JobStatus.WAITING, metadata={"always": True})
    # The date on which the job was created, as YYYY-MM-DD.
    added: str = field(default_factory=today, metadata={"always": True})
    # Whether the job only downloads its file, as opposed to also converting it.
    is_download_only: bool = field(default=False,
                                   metadata={"key": "download_only", "always": True})
    # The main keyword of the job's tracked series.
    keyword: Optional[str] = None
    # The string with which to search for downloads.
    query: Optional[str] = None
    # The file name of the download once converted, without its extension.
    converted_file_name: Optional[str] = None
    # The strings with which to search, in order, when searching with the query finds no
    # download.
    fallback_queries: list = field(default_factory=list)
    # The release groups, in order of preference, from which results of each search string
    # are accepted, keyed by search string; results from any group are accepted for a search
    # string with none.
    release_groups: dict = field(default_factory=dict)
    # The display title of the job's download, and its name and directory once downloaded.
    title: Optional[str] = None
    name: Optional[str] = None
    download_directory: Optional[str] = None
    # The download client used for the job: "deluge" or "sabnzbd".
    downloader: str = TorrentDownloader.name
    magnet_link: Optional[str] = None
    # The calculated SHA1 hash of the job's torrent.
    torrent_hash: Optional[str] = None
    # The indexer's ID for the NZB file found for the job, the path of the NZB file waiting to
    # be added, and the SABnzbd ID of its download.
    nzb_guid: Optional[str] = None
    nzb_file: Optional[str] = None
    nzo_id: Optional[str] = None
    # The indexer IDs of NZB files rejected for the job.
    rejected: list = field(default_factory=list)
    # The values read from the job's file, kept so that keys this code doesn't know, and the
    # order of the keys, are written back.
    file_values: dict = field(default_factory=dict, repr=False, metadata={"saved": False})

    @classmethod
    def from_dict(cls, directory, values):
        """Creates a job from the values of its file"""

        job = cls(directory, file_values=dict(values))
        for job_field in saved_fields():
            key = job_field.metadata.get("key", job_field.name)
            if key in values:
                setattr(job, job_field.name, values[key])
        job.status = JobStatus(job.status)
        return job

    def to_dict(self):
        """Gets the values to save in the job's file"""

        values = dict(self.file_values)
        for job_field in saved_fields():
            key = job_field.metadata.get("key", job_field.name)
            value = getattr(self, job_field.name)
            if key in values or job_field.metadata.get("always") or value != get_default(
                    job_field):
                values[key] = value.value if isinstance(value, JobStatus) else value
        return values

    @property
    def file_path(self):
        """Gets the full path to this job file"""

        return os.path.join(self.directory, self.job_id)

    @property
    def search_queries(self):
        """Gets the strings with which to search for downloads for this job, in order"""

        return [self.query] + self.fallback_queries

    @property
    def episode_key(self):
        """
        Gets the series keyword and episode number, such as ('show', 's01e02'), identifying
        the episode this job downloads, or None if the query string has no episode number
        """

        episode_number = get_episode_number(self.query)
        if episode_number is None:
            return None
        return (self.keyword, episode_number)

    def set_download_status(self, status, name, download_directory=None):
        """Sets the status, download name, and optionally the download directory of this job"""

        if download_directory is not None:
            self.download_directory = download_directory
        self.name = name
        self.status = status

    @property
    def status_description(self):
        """Gets a textual status description of this job"""

        description_string = f"Unknown status - '{self.query}'"
        if self.status == JobStatus.WAITING:
            description_string = f"Waiting to search for '{self.query}'"

        if self.status == JobStatus.SEARCHING:
            description_string = f"Searching using search term '{self.query}'"

        if self.status == JobStatus.ADDING:
            description_string = f"Adding download for '{self.title}'"

        if self.status == JobStatus.DOWNLOADING:
            description_string = f"Downloading '{self.name}'"

        if self.status == JobStatus.PENDING:
            description_string = f"Pending conversion of finished download '{self.name}'"

        if self.status == JobStatus.CONVERTING:
            description_string = f"Converting '{self.name}' to '{self.converted_file_name}'"

        if self.status == JobStatus.COMPLETED:
            description_string = (f"Completed download of '{self.name}'"
                                  if self.is_download_only
                                  else f"Completed conversion to '{self.converted_file_name}'")

        return description_string

    @classmethod
    def load(cls, directory, file_name):
        """
        Reads a job file, returning None if it does not exist (for example, because another
        process deleted it), or cannot be read
        """

        job_file_path = os.path.join(directory, file_name)
        try:
            with open(job_file_path, encoding='utf-8') as job_file:
                job_queue_dictionary = json.load(job_file)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as ex:
            logger.warning("Skipping unreadable job file %s: %s", job_file_path, ex)
            return None

        return Job.from_dict(directory, job_queue_dictionary)

    def delete(self):
        """Deletes the file representing this job, and any NZB file waiting to be added"""

        if os.path.exists(self.file_path):
            os.remove(self.file_path)
        if self.nzb_file is not None and os.path.exists(self.nzb_file):
            os.remove(self.nzb_file)

    def copy(self):
        """Creates a copy of this job with a new ID"""

        return replace(self, job_id=new_job_id(), file_values=dict(self.file_values))

    def save(self):
        """Writes this job to a file"""

        if not os.path.exists(self.directory):
            os.makedirs(self.directory)

        if not os.path.isdir(self.directory):
            logger.warning(
                "Cannot save job; path '%s' exists, but is not a directory.", self.directory)
            return

        with atomic_write(self.file_path) as job_file:
            json.dump(self.to_dict(), job_file, indent=2)

    def update_converted_file_name(self, config):
        """Updates converted file name with latest name from cached episode database"""

        match = re.match(r"(.*)s([0-9]+)e([0-9]+)(.*)", self.query, re.IGNORECASE)
        if match is not None:
            episode_db = EpisodeDatabase.load_from_cache(config)
            series = episode_db.get_tracked_series_by_keyword(self.keyword)
            if series is not None:
                episode = series.get_episode(int(match.group(2)), int(match.group(3)))
                if episode is not None:
                    candidate_converted_file_name = "".join(
                        config.conversion.string_substitutions.get(c, c)
                        for c in episode.plex_title).strip()
                    if self.converted_file_name != candidate_converted_file_name:
                        self.converted_file_name = candidate_converted_file_name


def saved_fields():
    """Gets the fields of a job that are saved in its file"""

    return [x for x in fields(Job) if x.metadata.get("saved", True)]


def get_default(job_field):
    """Gets the value a field of a job has when it isn't set"""

    return job_field.default_factory() if job_field.default is MISSING else job_field.default
