"""Module containing job information for automated processing"""

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from time import perf_counter

from infield_fly.conversion import ConversionSettings, Converter
from infield_fly.database import EpisodeDatabase
from infield_fly.job_status import JobStatus
from infield_fly.torrent.downloader import TorrentDownloader
from infield_fly.usenet.downloader import NzbDownloader

# The download tracks, in the order in which they search for downloads for waiting jobs.
DOWNLOADER_CLASSES = (NzbDownloader, TorrentDownloader)


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

    def refresh_jobs(self, airdate):
        """
        Removes completed jobs added before the airdate, updates the converted file names of
        waiting jobs, and creates jobs for episodes of tracked series aired on the airdate
        """

        completed_job_list = [x for x in self.get_jobs_by_status(JobStatus.COMPLETED)
                              if x.added != airdate.strftime("%Y-%m-%d")]
        for job in completed_job_list:
            job.delete()

        for job in self.get_jobs_by_status(JobStatus.WAITING):
            job.update_converted_file_name(self.config)
            job.save(self.logger)

        self.create_new_search_jobs(airdate)

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
        self.share_rejections(all_jobs)
        job_list = [x for x in all_jobs
                    if x.status in (JobStatus.WAITING, JobStatus.SEARCHING)]
        if len(job_list) == 0:
            self.logger.info("No waiting jobs to search for during job processing")
            return

        for downloader in self.downloaders:
            downloader.search_downloads(
                [x for x in job_list if x.status in (JobStatus.WAITING, JobStatus.SEARCHING)])

    def share_rejections(self, job_list):
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
                    job.save(self.logger)

    def add_downloads(self):
        """Adds the found downloads of adding jobs to the download client of their tracks"""

        job_list = self.get_jobs_by_status(JobStatus.ADDING)
        for downloader, jobs in self.group_jobs_by_downloader(job_list):
            downloader.add_downloads(jobs)

    def query_downloads_status(self):
        """Marks downloading jobs whose downloads have finished in their tracks as pending"""

        job_list = self.get_jobs_by_status(JobStatus.DOWNLOADING)
        for downloader, jobs in self.group_jobs_by_downloader(job_list):
            downloader.query_downloads(jobs)

    def group_jobs_by_downloader(self, job_list):
        """
        Groups jobs by the download track handling them, returning a list of each track
        with its jobs, and logging jobs whose track is unknown
        """

        downloaders = self.downloaders
        for job in job_list:
            if job.downloader not in [x.name for x in downloaders]:
                self.logger.warning("Job %s has unknown downloader '%s'",
                                    job.job_id, job.downloader)
        return [(x, [job for job in job_list if job.downloader == x.name])
                for x in downloaders]

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
            try:
                if job.is_download_only:
                    self.copy_downloaded_files(job, is_unattended_mode)
                else:
                    self.convert_downloaded_files(job, is_unattended_mode)
            except Exception as ex:  # pylint: disable=broad-exception-caught
                # Return the job to pending so it is retried on the next run,
                # rather than leaving it stuck in the converting state.
                self.logger.error("Processing job %s failed: %s", job.job_id, ex)
                job.status = JobStatus.PENDING
                job.save(self.logger)

    def create_new_search_jobs(self, airdate):
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
                        job.save(self.logger)

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
        job.save(self.logger)
        return job

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

        src_path = os.path.join(job.download_directory, job.name)
        if os.path.isdir(src_path):
            src_dir = src_path
            file_list = os.listdir(src_path)
        else:
            src_dir = os.path.dirname(src_path)
            file_list = [os.path.basename(src_path)]

        episode_files = [
            x for x in file_list
            if re.match(r"(.*)s([0-9]+)e([0-9]+)(.*)(\.mkv|\.mp4)$", x, re.IGNORECASE)
            and os.path.isfile(os.path.join(src_dir, x))]
        if len(episode_files) == 0:
            self.logger.warning("No episode files found to convert in %s", src_path)
            job.status = JobStatus.PENDING
            job.save(self.logger)
            return

        # Downloads can include samples or other extras whose names also match the
        # episode file pattern, so convert only the largest matching file.
        src_file = max((os.path.join(src_dir, x) for x in episode_files), key=os.path.getsize)
        os.makedirs(self.config.conversion.staging_directory, exist_ok=True)
        os.makedirs(self.config.conversion.final_directory, exist_ok=True)
        dest_file = os.path.join(self.config.conversion.staging_directory,
                                 f"{job.converted_file_name}.mp4")
        converter = Converter(src_file,
                              dest_file,
                              self.config.conversion.ffmpeg_location,
                              is_unattended_mode)
        if is_unattended_mode:
            self.logger.info("Starting conversion")
        start_time = perf_counter()
        output_files = converter.convert_file(ConversionSettings(
            convert_video=False, convert_audio=True, convert_subtitles=True))
        end_time = perf_counter()
        if is_unattended_mode:
            self.logger.info(
                "Conversion completed in %s seconds", end_time - start_time)

        # Move before marking complete, so a failed move leaves the job retryable.
        # shutil.move handles staging and final directories on different volumes.
        for output_file in output_files:
            shutil.move(output_file,
                        os.path.join(self.config.conversion.final_directory,
                                     os.path.basename(output_file)))
        self.mark_job_complete(job)

    def mark_job_complete(self, job):
        """Marks a job as completed"""

        job.status = JobStatus.COMPLETED
        job.save(self.logger)
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
            jobs.append(Job.load(self.job_queue_file_path, job_file))

        return jobs

    @property
    def job_queue_file_path(self):
        """Gets the path to the job queue directory"""

        return self.config.conversion.job_directory


class Job:  # pylint: disable=too-many-public-methods,too-many-instance-attributes

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
    def fallback_queries(self):
        """
        Gets or sets the strings with which to search, in order, when searching with the
        query string of this job finds no download
        """

        return self.dictionary.get("fallback_queries", [])

    @fallback_queries.setter
    def fallback_queries(self, value):
        self.dictionary["fallback_queries"] = value

    @property
    def release_groups(self):
        """
        Gets or sets the release groups, in order of preference, from which results of each
        of this job's search strings are accepted, keyed by search string. Results from any
        group are accepted for a search string with no release groups.
        """

        return self.dictionary.get("release_groups", {})

    @release_groups.setter
    def release_groups(self, value):
        self.dictionary["release_groups"] = value

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

        match = re.search(r"s([0-9]+)e([0-9]+)", self.query or "", re.IGNORECASE)
        if match is None:
            return None
        return (self.keyword, f"s{int(match.group(1)):02d}e{int(match.group(2)):02d}")

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
    def downloader(self):
        """Gets or sets the download client, 'deluge' or 'sabnzbd', used for this job"""

        return self.dictionary.get("downloader", TorrentDownloader.name)

    @downloader.setter
    def downloader(self, value):
        self.dictionary["downloader"] = value

    @property
    def nzb_guid(self):
        """Gets or sets the indexer's ID for the NZB file found for this job"""

        return self.dictionary.get("nzb_guid", None)

    @nzb_guid.setter
    def nzb_guid(self, value):
        self.dictionary["nzb_guid"] = value

    @property
    def nzb_file(self):
        """Gets or sets the path to the NZB file waiting to be added for this job"""

        return self.dictionary.get("nzb_file", None)

    @nzb_file.setter
    def nzb_file(self, value):
        self.dictionary["nzb_file"] = value

    @property
    def nzo_id(self):
        """Gets or sets the SABnzbd ID of the download for this job"""

        return self.dictionary.get("nzo_id", None)

    @nzo_id.setter
    def nzo_id(self, value):
        self.dictionary["nzo_id"] = value

    @property
    def rejected(self):
        """Gets or sets the indexer IDs of NZB files rejected for this job"""

        return self.dictionary.get("rejected", [])

    @rejected.setter
    def rejected(self, value):
        self.dictionary["rejected"] = value

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
        """Reads a job file"""

        job = None
        job_file_path = os.path.join(directory, file_name)
        if os.path.exists(job_file_path):
            with open(job_file_path, encoding='utf-8') as job_file:
                job_queue_dictionary = json.load(job_file)
                job = Job(directory, job_queue_dictionary)

        return job

    def delete(self):
        """Deletes the file representing this job, and any NZB file waiting to be added"""

        if os.path.exists(self.file_path):
            os.remove(self.file_path)
        if self.nzb_file is not None and os.path.exists(self.nzb_file):
            os.remove(self.nzb_file)

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
            return

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
