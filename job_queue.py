"""Module containing job information for automated processing"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from time import perf_counter
from config_settings import Configuration
from episode_database import EpisodeDatabase
from file_converter import Converter
from torrent_finder import TorrentDataProvider


class JobQueue:

    """Queue of jobs being processed"""

    def __init__(self):
        self.logger = logging.getLogger()

    def get_job_by_id(self, job_id):
        """Gets a job by its ID, if it exists; otherwise, returns None"""

        jobs = self.load_jobs()
        for job in jobs:
            if job.job_id == job_id:
                return job

        return None

    def update_download_job(self, torrent_hash, torrent_name, torrent_directory):
        """Updates the status of a currently downloading job"""

        jobs = self.load_jobs()
        for job in jobs:
            if job.torrent_hash == torrent_hash:
                if job.status == "adding":
                    job.download_directory = torrent_directory
                    job.name = torrent_name
                    job.status = "downloading"
                    job.save(self.logger)
                elif job.status == "downloading":
                    job.name = torrent_name
                    job.status = "completed" if job.is_download_only else "pending"
                    job.save(self.logger)
            elif job.status == "adding" and job.title == torrent_name:
                job.torrent_hash = torrent_hash
                job.download_directory = torrent_directory
                job.name = torrent_name
                job.status = "downloading"
                job.save(self.logger)

    def create_job(self, keyword, query):
        """Creates a new job using the specified keyword and query string"""

        job = Job(self.cache_file_path, {})
        job.keyword = keyword
        job.query = query
        job.save(self.logger)
        return job

    def get_jobs_by_status(self, status):
        """Gets all jubs with a specific status"""

        jobs = self.load_jobs()
        return [x for x in jobs if x.status == status]

    def perform_conversions(self, is_unattended_mode=False):
        """Executes all pending conversion jobs, converting files to the proper format"""

        config = Configuration()
        final_directory = config.conversion.final_directory
        pending_job_list = self.get_jobs_by_status("pending")
        for job in pending_job_list:
            job.status = "converting"
            job.save(self.logger)
        for job in pending_job_list:
            src_dir = os.path.join(job.download_directory, job.name)
            file_list = os.listdir(src_dir)
            file_list.sort()
            for input_file in file_list:
                match = re.match(
                    r"(.*)s([0-9]+)e([0-9]+)(.*)(\.mkv|\.mp4)", input_file, re.IGNORECASE)
                if match is not None:
                    src_file = os.path.join(src_dir, match.group(0))
                    dest_file = os.path.join(config.conversion.staging_directory,
                                             "{}.mp4".format(job.converted_file_name))
                    converter = Converter(
                        src_file, dest_file, config.conversion.ffmpeg_location, is_unattended_mode)
                    if is_unattended_mode:
                        self.logger.info("Starting conversion")
                    start_time = perf_counter()
                    converter.convert_file(
                        convert_video=False, convert_audio=True, convert_subtitles=True)
                    end_time = perf_counter()
                    if is_unattended_mode:
                        self.logger.info(
                            "Conversion completed in %s seconds", end_time - start_time)

                    job.status = "completed"
                    job.save(self.logger)
                    if datetime.now().strftime("%Y-%m-%d") != job.added:
                        job.delete()
                    os.rename(
                        dest_file, os.path.join(final_directory, os.path.basename(dest_file)))

    def perform_searches(self, airdate, is_unattended_mode=False):
        """Executes all pending search jobs, searching for available downloads"""

        completed_job_list = [x for x in self.get_jobs_by_status("completed")
                              if x.added != airdate.strftime("%Y-%m-%d")]
        for job in completed_job_list:
            job.delete()

        for job in self.get_jobs_by_status("waiting"):
            job.status = "searching"
            job.save(self.logger)

        config = Configuration()
        self.create_new_search_jobs(config, airdate)

        finder = TorrentDataProvider()
        if is_unattended_mode:
            self.logger.info("Starting search")
        start_time = perf_counter()
        for job in self.get_jobs_by_status("searching"):
            search_results = finder.search(
                job.query, retry_count=4, is_unattended_mode=is_unattended_mode)
            if len(search_results) == 0:
                job.status = "waiting"
                job.save(self.logger)
            else:
                search_result_counter = 0
                for search_result in search_results:
                    if search_result_counter == 0:
                        added_job = job
                    else:
                        added_job = job.copy()
                        added_job.converted_file_name = "{}.{}".format(
                            added_job.converted_file_name, search_result_counter)
                    added_job.status = "adding"
                    added_job.magnet_link = search_result.magnet_link
                    added_job.title = search_result.title
                    added_job.torrent_hash = search_result.hash
                    added_job.save(self.logger)
                    added_job.write_magnet_file(config.conversion.staging_directory, self.logger)
                    search_result_counter += 1
        end_time=perf_counter()
        if is_unattended_mode:
            self.logger.info("Search completed in %s seconds", end_time - start_time)

        self._write_magnet_files(
            config.conversion.magnet_directory, config.conversion.staging_directory)

    def _write_magnet_files(self, magnet_directory, staging_directory):
        self.logger.info("Adding files to download")
        if os.path.isdir(magnet_directory):
            for existing_file in [x for x in os.listdir(magnet_directory)
                                  if x.endswith(".invalid")]:
                os.remove(os.path.join(magnet_directory, existing_file))

            for magnet_file_name in os.listdir(staging_directory):
                if magnet_file_name.endswith(".magnet"):
                    os.rename(os.path.join(staging_directory, magnet_file_name),
                              os.path.join(magnet_directory, magnet_file_name))

    def create_new_search_jobs(self, config, airdate):
        """Creates new search jobs based on airdate"""

        episode_db = EpisodeDatabase.load_from_cache(config.metadata)
        for tracked_series in config.metadata.tracked_series:
            series = episode_db.get_series(tracked_series.series_id)
            series_episodes_since_last_search = series.get_episodes_by_airdate(
                airdate, airdate)
            for series_episode in series_episodes_since_last_search:
                for stored_search in tracked_series.stored_searches:
                    search_string = "{} {}".format(
                            " ".join(stored_search.search_terms),
                            "s{:02d}e{:02d}".format(
                                series_episode.season_number, series_episode.episode_number))
                    if not self.is_existing_job(tracked_series.main_keyword, search_string):
                        job = self.create_job(tracked_series.main_keyword, search_string)
                        job.status = "searching"
                        job.is_download_only = stored_search.is_download_only
                        if not stored_search.is_download_only:
                            job.converted_file_name = "".join(
                                config.conversion.string_substitutions.get(c, c)
                                for c in series_episode.plex_title).strip()
                        job.save(self.logger)

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

        if not os.path.exists(self.cache_file_path):
            os.makedirs(self.cache_file_path)

        jobs = []
        for job_file in os.listdir(self.cache_file_path):
            jobs.append(Job.load(self.cache_file_path, job_file))

        return jobs

    @property
    def cache_file_path(self):
        """Gets the path to the job queue directory"""

        return os.path.join(os.path.dirname(os.path.realpath(__file__)), ".jobs")


class Job:

    """Object representing a job to be processed"""

    def __init__(self, directory, job_dict):
        super().__init__()
        self.directory = directory
        self.dictionary = job_dict
        if "id" not in self.dictionary:
            self.dictionary["id"] = str(uuid.uuid1())
        if "status" not in self.dictionary:
            self.dictionary["status"] = "waiting"
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

    @classmethod
    def load(cls, directory, file_name):
        """Reads a job file"""

        job = None
        job_file_path = os.path.join(directory, file_name)
        if os.path.exists(job_file_path):
            with open(job_file_path) as job_file:
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

    def write_magnet_file(self, destination_directory, logger):
        """Writes a file containing the magnet link for this job"""

        if (self.magnet_link is None or self.title is None):
            logger.warning("Link or file name not set; cannot write magnet file.")

        if destination_directory is not None and os.path.isdir(destination_directory):
            magnet_file_path = os.path.join(destination_directory, self.title + ".magnet")
            logger.info("Writing magnet link to %s", magnet_file_path)
            with open(magnet_file_path, "w") as magnet_file:
                magnet_file.write(self.magnet_link)
                magnet_file.flush()

    def save(self, logger):
        """Writes this job to a file"""

        if not os.path.exists(self.directory):
            os.makedirs(self.directory)

        if not os.path.isdir(self.directory):
            logger.warning(
                "Cannot save job; path '%s' exists, but is not a directory.", self.directory)

        with open(self.file_path, "w") as job_file:
            json.dump(self.dictionary, job_file, indent=2)
