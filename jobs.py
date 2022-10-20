"""Module containing job information for automated processing"""

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from enum import Enum
from time import perf_counter

from deluge_client import DelugeRPCClient

from conversion import Converter
from database import EpisodeDatabase
from search import TorrentDataProvider


class JobQueue:

    """Queue of jobs being processed"""

    def __init__(self, configuration):
        self.logger = logging.getLogger()
        self.config = configuration

    def get_job_by_id(self, job_id):
        """Gets a job by its ID, if it exists; otherwise, returns None"""

        jobs = self.load_jobs()
        for job in jobs:
            if job.job_id == job_id:
                return job

        return None

    def create_job(self, keyword, query, is_download_only=False):
        """Creates a new job using the specified keyword and query string"""

        job = Job(self.job_queue_file_path, {})
        job.keyword = keyword
        job.query = query
        job.is_download_only = is_download_only
        job.update_converted_file_name(self.config)
        job.save(self.logger)
        return job

    def get_jobs_by_status(self, status):
        """Gets all jubs with a specific status"""

        jobs = self.load_jobs()
        return [x for x in jobs if x.status == status]

    def perform_searches(self, airdate, is_unattended_mode=False):
        """Executes all pending search jobs, searching for available downloads"""

        completed_job_list = [x for x in self.get_jobs_by_status(JobStatus.COMPLETED)
                              if x.added != airdate.strftime("%Y-%m-%d")]
        for job in completed_job_list:
            job.delete()

        for job in self.get_jobs_by_status(JobStatus.WAITING):
            job.status = JobStatus.SEARCHING
            job.update_converted_file_name(self.config)
            job.save(self.logger)

        self.create_new_search_jobs(airdate)

        search_jobs_list = self.get_jobs_by_status(JobStatus.SEARCHING)
        if len(search_jobs_list) == 0:
            self.logger.info("No queries to search during job processing")
            return

        finder = TorrentDataProvider()
        if is_unattended_mode:
            self.logger.info("Starting search")
        start_time = perf_counter()
        for job in search_jobs_list:
            search_results = finder.search(
                job.query, retry_count=4, is_unattended_mode=is_unattended_mode)
            if len(search_results) == 0:
                self.logger.info("No search results found, setting job back to waiting.")
                job.status = JobStatus.WAITING
                job.save(self.logger)
            else:
                search_result_counter = 0
                for search_result in search_results:
                    if search_result_counter == 0:
                        added_job = job
                        self.logger.info(
                            ("Search result for query string '%s'. "
                            "Job ID: %s, Hash: %s, Title: '%s', Converted file: '%s'"),
                            added_job.query, added_job.job_id, search_result.hash,
                            search_result.title, added_job.converted_file_name)
                    else:
                        added_job = job.copy()
                        added_job.converted_file_name = (f"{added_job.converted_file_name}."
                                                         f"{search_result_counter}")
                        self.logger.warning(
                            ("Multiple search results for query string '%s' found: "
                            "Job ID: %s, Hash: %s, Title: '%s', Converted file: '%s'"),
                            added_job.query, added_job.job_id, search_result.hash,
                            search_result.title, added_job.converted_file_name)
                    added_job.status = JobStatus.ADDING
                    added_job.magnet_link = search_result.magnet_link
                    added_job.title = search_result.title
                    added_job.torrent_hash = search_result.hash
                    added_job.save(self.logger)
                    search_result_counter += 1
        end_time=perf_counter()
        if is_unattended_mode:
            self.logger.info("Search completed in %s seconds", end_time - start_time)

    def add_torrents(self):
        """Adds found torrents to the Deluse client"""

        job_list = self.get_jobs_by_status(JobStatus.ADDING)
        if len(job_list) == 0:
            self.logger.info("No search results to add during job processing")
            return

        self.logger.info("Adding downloads to Deluge instance on %s",
                         self.config.conversion.deluge_host)
        with DelugeRPCClient(self.config.conversion.deluge_host,
                             self.config.conversion.deluge_port,
                             self.config.conversion.deluge_user_name,
                             self.config.conversion.deluge_password) as client:
            for job in job_list:
                encoded_id, metadata = client.core.prefetch_magnet_metadata(job.magnet_link)
                if encoded_id is None:
                    self.logger.error("Magnet metadata prefetch failed, no ID returned")
                    return
                if metadata is None:
                    self.logger.error("Magnet metadata prefetch failed, no metadata returned")
                encoded_name = metadata.get("name".encode(), None)
                torrent_id = encoded_id.decode()
                client.core.add_torrent_magnet(job.magnet_link, {})
                torrent = client.core.get_torrent_status(
                    torrent_id, ["name", "download_location", "is_finished"])
                job.torrent_hash = torrent_id
                job.download_directory = torrent["download_location".encode()].decode()
                job.name = (encoded_name.decode()
                            if encoded_name is not None
                            else torrent["name".encode()].decode())
                job.status = JobStatus.DOWNLOADING
                job.save(self.logger)

    def query_torrents_status(self):
        """Updates downloaded torrents to the Deluse client"""

        job_list = self.get_jobs_by_status(JobStatus.DOWNLOADING)
        if len(job_list) == 0:
            self.logger.info("No downloading jobs to query for status during job processing")
            return

        self.logger.info("Marking completed downloads on Deluge instance at %s",
                         self.config.conversion.deluge_host)
        with DelugeRPCClient(self.config.conversion.deluge_host,
                             self.config.conversion.deluge_port,
                             self.config.conversion.deluge_user_name,
                             self.config.conversion.deluge_password) as client:
            for job in job_list:
                torrent = client.core.get_torrent_status(
                    job.torrent_hash, ["name", "download_location", "is_finished"])
                if torrent.get("is_finished".encode(), False):
                    job.name = torrent["name".encode()].decode()
                    job.status = JobStatus.PENDING
                    job.save(self.logger)

    def perform_conversions(self, is_unattended_mode=False):
        """Executes all pending conversion jobs, converting files to the proper format"""

        pending_job_list = self.get_jobs_by_status(JobStatus.PENDING)
        if len(pending_job_list) == 0:
            self.logger.info("No files to convert during job processing")
            return

        for job in pending_job_list:
            job.status = JobStatus.CONVERTING
            job.save(self.logger)
        for job in pending_job_list:
            if job.is_download_only:
                self.copy_downloaded_files(job, is_unattended_mode)
            else:
                self.convert_downloaded_files(job, is_unattended_mode)

    def create_new_search_jobs(self, airdate):
        """Creates new search jobs based on airdate"""

        episode_db = EpisodeDatabase.load_from_cache(self.config)
        for tracked_series in self.config.metadata.tracked_series:
            series = episode_db.get_series(tracked_series.series_id)
            series_episodes_since_last_search = series.get_episodes_by_airdate(
                airdate, airdate)
            for series_episode in series_episodes_since_last_search:
                for stored_search in tracked_series.stored_searches:
                    search_terms = stored_search.search_terms[:]
                    search_terms.append(
                        f"s{series_episode.season_number:02d}e{series_episode.episode_number:02d}")
                    search_string = " ".join(search_terms)
                    if not self.is_existing_job(tracked_series.main_keyword, search_string):
                        job = self.create_job(tracked_series.main_keyword, search_string)
                        job.status = JobStatus.SEARCHING
                        job.is_download_only = stored_search.is_download_only
                        if not stored_search.is_download_only:
                            job.converted_file_name = "".join(
                                self.config.conversion.string_substitutions.get(c, c)
                                for c in series_episode.plex_title).strip()
                        job.save(self.logger)

    def copy_downloaded_files(self, job, is_unattended_mode):
        """Copies downloaded job files to a destination directory"""

        src_dir = os.path.join(job.download_directory, job.name)
        dest_dir = os.path.join(self.config.conversion.final_directory, job.name)

        self.logger.info("Copying downloaded files from %s to %s", src_dir, dest_dir)
        start_time = perf_counter()
        shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
        end_time = perf_counter()
        if is_unattended_mode:
            self.logger.info("Copy completed in %s seconds", end_time - start_time)

        self.mark_job_complete(job)

    def convert_downloaded_files(self, job, is_unattended_mode):
        """Converts downloaded job files"""

        src_dir = os.path.join(job.download_directory, job.name)

        file_list = os.listdir(src_dir)
        file_list.sort()
        for input_file in file_list:
            match = re.match(
                r"(.*)s([0-9]+)e([0-9]+)(.*)(\.mkv|\.mp4)", input_file, re.IGNORECASE)
            if match is not None:
                src_file = os.path.join(src_dir, match.group(0))
                dest_file = os.path.join(self.config.conversion.staging_directory,
                                        f"{job.converted_file_name}.mp4")
                converter = Converter(src_file,
                                      dest_file,
                                      self.config.conversion.ffmpeg_location,
                                      is_unattended_mode)
                if is_unattended_mode:
                    self.logger.info("Starting conversion")
                start_time = perf_counter()
                converter.convert_file(
                    convert_video=False, convert_audio=True, convert_subtitles=True)
                end_time = perf_counter()
                if is_unattended_mode:
                    self.logger.info(
                        "Conversion completed in %s seconds", end_time - start_time)

                self.mark_job_complete(job)
                os.rename(dest_file,
                          os.path.join(self.config.conversion.final_directory,
                                       os.path.basename(dest_file)))

    def mark_job_complete(self, job):
        """Marks a job as completed"""

        job.status = JobStatus.COMPLETED
        job.save(self.logger)
        if datetime.now().strftime("%Y-%m-%d") != job.added:
            job.delete()

    def is_existing_job(self, keyword, search_string):
        """
        Gets a value indicating whether a job for a specified keyword and search string
        already exists
        """

        for job in self.load_jobs():
            if job.keyword == keyword and job.query == search_string:
                return True

        return False

    def load_jobs(self):
        """Loads all job files in the cache directory"""

        if not os.path.exists(self.job_queue_file_path):
            os.makedirs(self.job_queue_file_path)

        jobs = []
        for job_file in os.listdir(self.job_queue_file_path):
            jobs.append(Job.load(self.job_queue_file_path, job_file))

        return jobs

    @property
    def job_queue_file_path(self):
        """Gets the path to the job queue directory"""

        return self.config.conversion.job_directory


class JobStatus(Enum):
    """Provides the enumerated values for the status of a job"""

    WAITING = "waiting"
    SEARCHING = "searching"
    ADDING = "adding"
    DOWNLOADING = "downloading"
    PENDING = "pending"
    CONVERTING = "converting"
    COMPLETED = "completed"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value):
        """Returns the UNKNOWN value for values not matching valid values"""

        return cls(cls.UNKNOWN)


class Job:

    """Object representing a job to be processed"""

    def __init__(self, directory, job_dict):
        super().__init__()
        self.directory = directory
        self.dictionary = job_dict
        if "id" not in self.dictionary:
            self.dictionary["id"] = str(uuid.uuid1())
        if "status" not in self.dictionary:
            self.dictionary["status"] = JobStatus.WAITING
        else:
            self.dictionary["status"] = JobStatus(job_dict["status"])

        if "added" not in self.dictionary:
            self.dictionary["added"] =  datetime.now().strftime("%Y-%m-%d")
        if "download_only" not in self.dictionary:
            self.dictionary["download_only"] = False

    @property
    def file_path(self):
        """Gets the full path to this job file"""

        return os.path.join(self.directory, self.job_id)

    @property
    def job_id(self):
        """Gets the ID of this job"""

        return self.dictionary["id"]

    @property
    def keyword(self):
        """Gets or sets the keyword of the tracked series for this job"""

        return self.dictionary.get("keyword", None)

    @keyword.setter
    def keyword(self, value):
        self.dictionary["keyword"] = value

    @property
    def added(self):
        """Gets or sets the date on which this job was created"""

        return self.dictionary.get("added", None)

    @added.setter
    def added(self, value):
        self.dictionary["added"] = value

    @property
    def query(self):
        """Gets or sets the string used to search for downloads for this job"""

        return self.dictionary.get("query", None)

    @query.setter
    def query(self, value):
        self.dictionary["query"] = value

    @property
    def status(self):
        """Gets or sets the status for this job"""

        return self.dictionary["status"]

    @status.setter
    def status(self, value):
        self.dictionary["status"] = value

    @property
    def magnet_link(self):
        """Gets or sets the magnet link for this job"""

        return self.dictionary.get("magnet_link", None)

    @magnet_link.setter
    def magnet_link(self, value):
        self.dictionary["magnet_link"] = value

    @property
    def title(self):
        """Gets or sets the display title for this job"""

        return self.dictionary.get("title", None)

    @title.setter
    def title(self, value):
        self.dictionary["title"] = value

    @property
    def name(self):
        """Gets or sets the download name for this job"""

        return self.dictionary.get("name", None)

    @name.setter
    def name(self, value):
        self.dictionary["name"] = value

    @property
    def torrent_hash(self):
        """Gets the calculated SHA1 hash for the torrent in this job"""

        return self.dictionary.get("torrent_hash",  None)

    @torrent_hash.setter
    def torrent_hash(self, value):
        self.dictionary["torrent_hash"] = value

    @property
    def download_directory(self):
        """Gets the directory to which the torrent for this job is downloaded"""

        return self.dictionary.get("download_directory", None)

    @download_directory.setter
    def download_directory(self, value):
        self.dictionary["download_directory"] = value

    @property
    def converted_file_name(self):
        """Gets the file name of the download once converted"""

        return self.dictionary.get("converted_file_name", None)

    @converted_file_name.setter
    def converted_file_name(self, value):
        self.dictionary["converted_file_name"] = value

    @property
    def is_download_only(self):
        """
        Gets a value indicating whether this job only downloads the file as opposed to also
        converting it
        """

        return self.dictionary.get("download_only", False)

    @is_download_only.setter
    def is_download_only(self, value):
        self.dictionary["download_only"] = value

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
        """Reads a job file"""

        job = None
        job_file_path = os.path.join(directory, file_name)
        if os.path.exists(job_file_path):
            with open(job_file_path, encoding='utf-8') as job_file:
                job_queue_dictionary = json.load(job_file)
                job = Job(directory, job_queue_dictionary)

        return job

    def delete(self):
        """Deletes the file representing this job"""

        if os.path.exists(self.file_path):
            os.remove(self.file_path)

    def copy(self):
        """Creates a copy of this job with a new ID"""

        job_copy = Job(self.directory, {})
        for name in self.dictionary:
            if name != "id":
                job_copy.dictionary[name] = self.dictionary[name]

        return job_copy

    def save(self, logger):
        """Writes this job to a file"""

        if not os.path.exists(self.directory):
            os.makedirs(self.directory)

        if not os.path.isdir(self.directory):
            logger.warning(
                "Cannot save job; path '%s' exists, but is not a directory.", self.directory)

        with open(self.file_path, "w", encoding='utf-8') as job_file:
            json.dump(self.dictionary, job_file, indent=2, default=lambda x: x.value)

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
