from datetime import datetime
import json
import os
import fasteners
import uuid
from config_settings import Configuration
from episode_database import EpisodeDatabase
from file_converter import Converter, FileMapper
from torrent_finder import TorrentDataProvider

class JobQueue:

    def __init__(self):
        super().__init__()
        self.lock = fasteners.InterProcessLock(
            os.path.join(os.path.dirname(self.cache_file_path), ".queuelock"))
        self.jobs = []

    def add_job(self, job):
        self.jobs.append(job)

    def remove_job(self, job):
        self.jobs.remove(job)

    def get_job_by_id(self, id):
        for job in self.jobs:
            if job.id == id:
                return job

        return None

    def get_jobs(self):
        return self.jobs

    def update_download_job(self, torrent_hash, torrent_name, torrent_directory):
        with self.lock:
            for job in self.jobs:
                if job.status == "adding" and job.title == torrent_name:
                    job.torrent_hash = torrent_hash
                    job.download_directory = torrent_directory
                    job.name = torrent_name
                    job.status = "downloading"
                elif job.status == "downloading" and job.torrent_hash == torrent_hash:
                    job.name = torrent_name
                    job.status = "converting"

            self.save_to_cache()

    def perform_conversions(self):
        config = Configuration()
        staging_directory = (config.conversion.staging_directory
                             if config.conversion.staging_directory is not None
                             else "staging")
        final_directory = (config.conversion.final_directory
                           if config.conversion.final_directory is not None
                           else "completed")
        episode_db = EpisodeDatabase.load_from_cache(config.metadata)
        with self.lock:
            converting_job_list = [x for x in self.jobs if x.status == "converting"]
            for job in converting_job_list:
                if episode_db.get_tracked_series_by_keyword(job.keyword) is not None:
                    mapper = FileMapper(episode_db)
                    file_map = mapper.map_files(
                        os.path.join(job.download_directory, job.name) + os.sep,
                        staging_directory,
                        job.keyword)
                    for src_file, dest_file in file_map:
                        converted_dest_file = self._replace_strings(
                            dest_file, 
                            config.conversion.string_substitutions)
                        converter = Converter(src_file, converted_dest_file, config.conversion.ffmpeg_location)
                        converter.convert_file(
                            convert_video=False, convert_audio=True, convert_subtitles=True)
                        job.status = "completed"
                        if datetime.now().strftime("%Y-%m-%d") != job.added:
                            self.remove_job(job)
                        os.rename(converted_dest_file, os.path.join(final_directory, os.path.basename(converted_dest_file)))

            self.save_to_cache()

    def perform_searches(self, airdate):
        completed_job_list = [x for x in self.jobs if x.status == "completed" and x.added != airdate.strftime("%Y-%m-%d")]
        for job in completed_job_list:
            self.remove_job(job)

        for job in self.jobs:
            if job.status == "waiting":
                job.status = "searching"

        config = Configuration()
        found_episodes = []
        episode_db = EpisodeDatabase.load_from_cache(config.metadata)
        with self.lock:
            for tracked_series in config.metadata.tracked_series:
                series = episode_db.get_series(tracked_series.series_id)
                series_episodes_since_last_search = series.get_episodes_by_airdate(
                    airdate, airdate)
                for series_episode in series_episodes_since_last_search:
                    found_episodes.append(series_episode)
                    for stored_search in tracked_series.stored_searches:
                        search_string = "{} {}".format(
                                " ".join(stored_search),
                                "s{:02d}e{:02d}".format(
                                    series_episode.season_number, series_episode.episode_number))
                        if not self.is_existing_job(tracked_series.main_keyword, search_string):
                            job_to_add = Job({})
                            job_to_add.keyword = tracked_series.main_keyword
                            job_to_add.query = search_string
                            job_to_add.added = datetime.now().strftime("%Y-%m-%d")
                            job_to_add.status = "searching"
                            self.add_job(job_to_add)

            staging_directory = config.conversion.staging_directory
            finder = TorrentDataProvider()
            for job in self.jobs:
                if job.status == "searching":
                    search_results = finder.search(job.query, retry_count=4)
                    if len(search_results) == 0:
                        job.status = "waiting"
                    else:
                        for search_result in search_results:
                            job.status = "adding"
                            job.magnet_link = search_result.magnet_link
                            job.title = search_result.title
                            if staging_directory is not None and os.path.isdir(staging_directory):
                                magnet_file_path = os.path.join(staging_directory, search_result.title + ".magnet")
                                print("Writing magnet link to {}".format(magnet_file_path))
                                with open(magnet_file_path, "w") as magnet_file:
                                    magnet_file.write(search_result.magnet_link)
                                    magnet_file.flush()

            self.save_to_cache()

        magnet_directory = config.conversion.magnet_directory
        if magnet_directory is not None and os.path.isdir(magnet_directory):
            for magnet_file_name in os.listdir(staging_directory):
                if magnet_file_name.endswith(".magnet"):
                    os.rename(os.path.join(staging_directory, magnet_file_name),
                              os.path.join(magnet_directory, magnet_file_name))

    def save_to_cache(self):
        """Writes this job queue to a cache file"""

        job_queue_file_path = self.cache_file_path
        with open(job_queue_file_path, "w") as job_queue_file:
            json.dump(self, job_queue_file, indent=2, default=lambda x: x.to_json())

    def is_existing_job(self, keyword, search_string):
        for job in self.jobs:
            print("existing job: keyword={}, search={}".format(job.keyword, job.query))
            if job.keyword == keyword and job.query == search_string:
                return True

        return False

    def to_json(self):
        """Serializes this job queue to a JSON format"""

        return {
            "jobs": self.jobs
        }

    def _replace_strings(self, input, substitutions):
        output = input
        if substitutions is not None:
            for replacement in substitutions:
                output = output.replace(replacement, substitutions[replacement])
        return output

    @property
    def cache_file_path(self):
        """Gets the path to the job queue file"""

        return os.path.join(os.path.dirname(os.path.realpath(__file__)), ".jobqueue")

    @classmethod
    def load_from_cache(cls):
        """Creates a job queue from a cache file"""

        job_queue = JobQueue()
        job_queue_file_path = job_queue.cache_file_path
        if os.path.exists(job_queue_file_path):
            with open(job_queue_file_path) as job_queue_file:
                job_queue_dictionary = json.load(job_queue_file)
                for job_object in job_queue_dictionary["jobs"]:
                    job_queue.add_job(Job(job_object))

        return job_queue

class Job:

    def __init__(self, job_dict):
        super().__init__()
        self.dictionary = job_dict
        if "status" not in self.dictionary:
            self.dictionary["status"] = "waiting"
        if "id" not in self.dictionary:
            self.dictionary["id"] = str(uuid.uuid1())

    @property
    def id(self):
        return self.dictionary["id"]

    @property
    def keyword(self):
        return self.dictionary["keyword"] if "keyword" in self.dictionary else None

    @keyword.setter
    def keyword(self, value):
        self.dictionary["keyword"] = value

    @property
    def added(self):
        return self.dictionary["added"] if "added" in self.dictionary else None

    @added.setter
    def added(self, value):
        self.dictionary["added"] = value

    @property
    def query(self):
        return self.dictionary["query"] if "query" in self.dictionary else None

    @query.setter
    def query(self, value):
        self.dictionary["query"] = value

    @property
    def status(self):
        return self.dictionary["status"]

    @status.setter
    def status(self, value):
        self.dictionary["status"] = value

    @property
    def magnet_link(self):
        return self.dictionary["magnet_link"] if "magnet_link" in self.dictionary else None

    @magnet_link.setter
    def magnet_link(self, value):
        self.dictionary["magnet_link"] = value

    @property
    def title(self):
        return self.dictionary["title"] if "title" in self.dictionary else None

    @title.setter
    def title(self, value):
        self.dictionary["title"] = value

    @property
    def name(self):
        return self.dictionary["name"] if "name" in self.dictionary else None

    @name.setter
    def name(self, value):
        self.dictionary["name"] = value

    @property
    def torrent_hash(self):
        return self.dictionary["torrent_hash"] if "torrent_hash" in self.dictionary else None

    @torrent_hash.setter
    def torrent_hash(self, value):
        self.dictionary["torrent_hash"] = value

    @property
    def download_directory(self):
        return self.dictionary["download_directory"] if "download_directory" in self.dictionary else None

    @download_directory.setter
    def download_directory(self, value):
        self.dictionary["download_directory"] = value

    def to_json(self):
        return self.dictionary
