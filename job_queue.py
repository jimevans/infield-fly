"""Module containing job information for automated processing"""

import json
import logging
import os
import re
import uuid
from datetime import datetime
from time import perf_counter
from deluge_client import DelugeRPCClient
from episode_database import EpisodeDatabase
from file_converter import Converter
from torrent_finder import TorrentDataProvider


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
        match = re.match(r"(.*)s([0-9]+)e([0-9]+)(.*)", query, re.IGNORECASE)
        if match is not None:
            episode_db = EpisodeDatabase.load_from_cache(self.config)
            series = episode_db.get_tracked_series_by_keyword(keyword)
            if series is not None:
                episode = series.get_episode(int(match.group(2)), int(match.group(3)))
                if episode is not None:
                    job.converted_file_name = "".join(
                        self.config.conversion.string_substitutions.get(c, c)
                        for c in episode.plex_title).strip()
        job.save(self.logger)
        return job

    def get_jobs_by_status(self, status):
        """Gets all jubs with a specific status"""

        jobs = self.load_jobs()
        return [x for x in jobs if x.status == status]

    def perform_conversions(self, is_unattended_mode=False):
        """Executes all pending conversion jobs, converting files to the proper format"""

        self.query_torrents_status()
        final_directory = self.config.conversion.final_directory
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

        self.create_new_search_jobs(airdate)

        finder = TorrentDataProvider()
        if is_unattended_mode:
            self.logger.info("Starting search")
        start_time = perf_counter()
        for job in self.get_jobs_by_status("searching"):
            search_results = finder.search(
                job.query, retry_count=4, is_unattended_mode=is_unattended_mode)
            if len(search_results) == 0:
                self.logger.info("No search results found, setting job back to waiting.")
                job.status = "waiting"
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
                    added_job.status = "adding"
                    added_job.magnet_link = search_result.magnet_link
                    added_job.title = search_result.title
                    added_job.torrent_hash = search_result.hash
                    added_job.save(self.logger)
                    search_result_counter += 1
        end_time=perf_counter()
        if is_unattended_mode:
            self.logger.info("Search completed in %s seconds", end_time - start_time)

        self.add_torrents()

    def add_torrents(self):
        """Adds found torrents to the Deluse client"""

        job_list = self.get_jobs_by_status("adding")
        if len(job_list) > 0:
            self.logger.info("Adding downloads to Deluge instance on %s",
                             self.config.conversion.deluge_host)
            with DelugeRPCClient(self.config.conversion.deluge_host,
                                 self.config.conversion.deluge_port,
                                 self.config.conversion.deluge_user_name,
                                 self.config.conversion.deluge_password) as client:
                for job in job_list:
                    encoded_id, metadata = client.core.prefetch_magnet_metadata(job.magnet_link)
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
                    job.status = "downloading"
                    job.save(self.logger)
        else:
            self.logger.info("No search results to add during job processing")

    def query_torrents_status(self):
        """Updates downloaded torrents to the Deluse client"""

        job_list = self.get_jobs_by_status("downloading")
        if len(job_list) > 0:
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
                        job.status = "pending"
                        job.save(self.logger)
        else:
            self.logger.info("No files to convert during job processing")

    def create_new_search_jobs(self, airdate):
        """Creates new search jobs based on airdate"""

        episode_db = EpisodeDatabase.load_from_cache(self.config)
        for tracked_series in self.config.metadata.tracked_series:
            series = episode_db.get_series(tracked_series.series_id)
            series_episodes_since_last_search = series.get_episodes_by_airdate(
                airdate, airdate)
            for series_episode in series_episodes_since_last_search:
                for stored_search in tracked_series.stored_searches:
                    search_terms = stored_search[:]
                    search_terms.append(
                        f"s{series_episode.season_number:02d}e{series_episode.episode_number:02d}")
                    search_string = " ".join(search_terms)
                    if not self.is_existing_job(tracked_series.main_keyword, search_string):
                        job = self.create_job(tracked_series.main_keyword, search_string)
                        job.status = "searching"
                        job.is_download_only = stored_search.is_download_only
                        if not stored_search.is_download_only:
                            job.converted_file_name = "".join(
                                self.config.conversion.string_substitutions.get(c, c)
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

        return self.config.conversion.job_directory


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
            json.dump(self.dictionary, job_file, indent=2)
