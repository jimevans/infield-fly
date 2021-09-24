"""Module containing job information for automated processing"""

import json
import os
import uuid
from datetime import datetime
from config_settings import Configuration
from episode_database import EpisodeDatabase
from file_converter import Converter, FileMapper
from torrent_finder import TorrentDataProvider


class JobQueue:

    """Queue of jobs being processed"""

    def __init__(self):
        pass

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
                    job.save()
                elif job.status == "downloading":
                    job.name = torrent_name
                    job.status = "completed" if job.download_only else "pending"
                    job.save()
            elif job.status == "adding" and job.title == torrent_name:
                job.torrent_hash = torrent_hash
                job.download_directory = torrent_directory
                job.name = torrent_name
                job.status = "downloading"
                job.save()

    def create_job(self, keyword, query):
        """Creates a new job using the specified keyword and query string"""

        job = Job(self.cache_file_path, {})
        job.keyword = keyword
        job.query = query
        job.save()
        return job

    def perform_conversions(self):
        """Executes all pending conversion jobs, converting files to the proper format"""

        jobs = self.load_jobs()
        config = Configuration()
        staging_directory = (config.conversion.staging_directory
                             if config.conversion.staging_directory is not None
                             else "staging")
        final_directory = (config.conversion.final_directory
                           if config.conversion.final_directory is not None
                           else "completed")
        episode_db = EpisodeDatabase.load_from_cache(config.metadata)
        pending_job_list = [x for x in jobs if x.status == "pending"]
        for job in pending_job_list:
            job.status = "converting"
            job.save()
        for job in pending_job_list:
            if episode_db.get_tracked_series_by_keyword(job.keyword) is not None:
                mapper = FileMapper(episode_db)
                file_map = mapper.map_files(
                    os.path.join(job.download_directory, job.name) + os.sep,
                    staging_directory,
                    job.keyword)
                for src_file, dest_file in file_map:
                    converted_dest_file = "".join(
                        config.conversion.string_substitutions.get(c, c) for c in dest_file)
                    converter = Converter(
                        src_file, converted_dest_file, config.conversion.ffmpeg_location)
                    converter.convert_file(
                        convert_video=False, convert_audio=True, convert_subtitles=True)
                    job.status = "completed"
                    job.save()
                    if datetime.now().strftime("%Y-%m-%d") != job.added:
                        job.delete()
                    os.rename(converted_dest_file,
                              os.path.join(final_directory, os.path.basename(converted_dest_file)))

    def perform_searches(self, airdate):
        """Executes all pending search jobs, searching for available downloads"""

        completed_job_list = [x for x in self.load_jobs()
                              if x.status == "completed"
                              and x.added != airdate.strftime("%Y-%m-%d")]
        for job in completed_job_list:
            job.delete()

        for job in self.load_jobs():
            if job.status == "waiting":
                job.status = "searching"
                job.save()

        config = Configuration()
        self.create_new_search_jobs(config, airdate)

        finder = TorrentDataProvider()
        for job in self.load_jobs():
            if job.status == "searching":
                search_results = finder.search(job.query, retry_count=4)
                if len(search_results) == 0:
                    job.status = "waiting"
                    job.save()
                else:
                    for search_result in search_results:
                        job.status = "adding"
                        job.magnet_link = search_result.magnet_link
                        job.title = search_result.title
                        job.torrent_hash = search_result.hash
                        job.save()
                        job.write_magnet_file(config.conversion.staging_directory)

        magnet_directory = config.conversion.magnet_directory
        if magnet_directory is not None and os.path.isdir(magnet_directory):
            for existing_file in [x for x in os.listdir(magnet_directory)
                                  if x.endswith(".invalid")]:
                os.remove(os.path.join(magnet_directory, existing_file))

            for magnet_file_name in os.listdir(config.conversion.staging_directory):
                if magnet_file_name.endswith(".magnet"):
                    os.rename(os.path.join(config.conversion.staging_directory, magnet_file_name),
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
                            " ".join(stored_search),
                            "s{:02d}e{:02d}".format(
                                series_episode.season_number, series_episode.episode_number))
                    if not self.is_existing_job(tracked_series.main_keyword, search_string):
                        job = self.create_job(tracked_series.main_keyword, search_string)
                        job.status = "searching"
                        job.save()

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
        if "status" not in self.dictionary:
            self.dictionary["status"] = "waiting"
        if "id" not in self.dictionary:
            self.dictionary["id"] = str(uuid.uuid1())
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
    def download_only(self):
        """
        Gets a value indicating whether this job only downloads the file as opposed to also
        converting it
        """

        return self.dictionary.get("download_only", False)

    @download_only.setter
    def download_only(self, value):
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

    def write_magnet_file(self, destination_directory):
        """Writes a file containing the magnet link for this job"""

        if (self.magnet_link is None or self.title is None):
            print("Link or file name not set; cannot write magnet file.")

        if destination_directory is not None and os.path.isdir(destination_directory):
            magnet_file_path = os.path.join(destination_directory, self.title + ".magnet")
            print("Writing magnet link to {}".format(magnet_file_path))
            with open(magnet_file_path, "w") as magnet_file:
                magnet_file.write(self.magnet_link)
                magnet_file.flush()

    def save(self):
        """Writes this job to a file"""

        if not os.path.exists(self.directory):
            os.makedirs(self.directory)

        if not os.path.isdir(self.directory):
            print("Cannot save job; path '{}' exists, but is not a directory.".format(
                self.directory))

        with open(self.file_path, "w") as job_file:
            json.dump(self.dictionary, job_file, indent=2)
