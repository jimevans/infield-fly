"""Main module for converting files"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from configuration import Configuration
from conversion import Converter, FileMapper
from database import EpisodeDatabase
from jobs import JobQueue, JobStatus
from notification import Notifier
from search import TorrentDataProvider


def get_search_strings(from_date, to_date, episode_db, metadata_settings):
    """Gets the set of search strings for all tracked series during the specified date range"""

    found_episodes = []
    searches_to_perform = []
    for tracked_series in metadata_settings.tracked_series:
        series = episode_db.get_series(tracked_series.series_id)
        series_episodes = series.get_episodes_by_airdate(from_date, to_date)
        for series_episode in series_episodes:
            found_episodes.append(series_episode)
            for stored_search in tracked_series.stored_searches:
                search_terms = stored_search.search_terms[:]
                search_terms.append((
                    f"s{series_episode.season_number:02d}e{series_episode.episode_number:02d}"))
                searches_to_perform.append({
                    "keyword": tracked_series.main_keyword,
                    "query": " ".join(search_terms),
                    "download_only": stored_search.is_download_only
                })

    print("Episodes found:")
    for episode in found_episodes:
        print(F"{episode.plex_title} (airdate {episode.airdate:%Y-%m-%d})")

    return searches_to_perform

def search_for_torrents(searches_to_perform, search_retry_count, output_directory):
    """Searches for torrents using the specified search strings"""

    finder = TorrentDataProvider()
    print("Performing searches")
    for search in searches_to_perform:
        search_results = finder.search(search["query"], retry_count=search_retry_count)
        if len(search_results) == 0:
            print("No results found after retries")
        for search_result in search_results:
            if output_directory is not None and os.path.isdir(output_directory):
                magnet_file_path = os.path.join(output_directory,
                                                search_result.title + ".magnet")
                print(f"Writing magnet link to {magnet_file_path}")
                with open(magnet_file_path, "w", encoding='utf-8') as magnet_file:
                    magnet_file.write(search_result.magnet_link)
                    magnet_file.flush()
            else:
                print(f"Torrent title: {search_result.title}")
                print(f"Magnet link: {search_result.magnet_link}")

def find_downloads(args, config):
    """Finds available downloads"""

    episode_db = EpisodeDatabase.load_from_cache(config)
    from_date = datetime.strptime(args.fromdate, "%Y-%m-%d")
    to_date = datetime.strptime(args.todate, "%Y-%m-%d")

    if args.update_metadata:
        episode_db.update_all_tracked_series()
        episode_db.save_to_cache()

    print(f"Searching for downloads between {from_date:%Y-%m-%d} and {to_date:%Y-%m-%d}")
    searches_to_perform = get_search_strings(from_date, to_date, episode_db, config.metadata)

    print("")
    if args.dry_run:
        print("Dry run requested. Not performing searches. Searches to perform:")
        for search in searches_to_perform:
            print(search["query"])
    else:
        if args.create_jobs:
            job_queue = JobQueue(config)
            for search in searches_to_perform:
                job_queue.create_job(search["keyword"], search["query"], search["download_only"])
        else:
            search_for_torrents(searches_to_perform, args.retry_count, args.directory)

def list_series(args, config):
    """Lists the episodes of a series in the cached dataabase."""

    episode_db = EpisodeDatabase.load_from_cache(config)
    series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)
    if series_metadata is None:
        print(f"Keyword '{args.keyword}' was not found in the database")
    else:
        for episode in series_metadata.episodes:
            airdate = ("not aired"
                       if episode.airdate is None
                       else episode.airdate.strftime("%Y-%m-%d"))
            print((f"s{episode.season_number:02d}e{episode.episode_number:02d} "
                   f"(aired: {airdate}) - {episode.title}"))

def update_database(args, config):
    """Updates the episode metadata in the databasae for tracked series"""

    episode_db = EpisodeDatabase.load_from_cache(config)
    episode_db.update_all_tracked_series(force_updates=args.force_updates,
                                         is_unattended_mode=args.unattended)
    episode_db.save_to_cache()

def convert(args, config):
    """Converts a file using the soecified conversion arguments"""

    episode_db = EpisodeDatabase.load_from_cache(config)
    series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)
    if args.keyword is not None and series_metadata is None:
        print(f"Keyword '{args.keyword}' was not found in the database")
    else:
        mapper = FileMapper(episode_db,
                            file_name_subtitutions=config.conversion.string_substitutions)
        file_map = mapper.map_files(args.source, args.destination, args.keyword)

        for src_file, dest_file in file_map:
            converter = Converter(src_file, dest_file, config.conversion.ffmpeg_location)
            converter.convert_file(convert_video=args.convert_video,
                                   convert_audio=args.convert_audio,
                                   convert_subtitles=args.convert_subtitles,
                                   dry_run=args.dry_run)

        if args.notify:
            if args.dry_run:
                print("Operation complete. Not sending notification on dry run.")
            else:
                if config.notification is None:
                    print("No config notification settings in settings file. Not notifying.")
                elif (config.notification.receiving_number is None
                    or config.notification.receiving_number == ""):
                    print("No recipient number specified in notification settings in settings "
                        "file. Not notifying.")
                else:
                    notifier = Notifier.create_default_notifier(config)
                    if notifier is not None:
                        notifier.notify(config.notification.receiving_number,
                                        f"Conversion of {args.source} complete.")

def show_job(args, config):
    """Deletes a new queued job"""

    job_queue = JobQueue(config)
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print(f"No existing job with ID '{args.id}'")
    else:
        print(f"ID: {job.job_id}")
        print(f"Status: {job.status.value}")
        print(f"Date added: {job.added}")
        print(f"Series keyword: {job.keyword}")
        print(f"Search string: {job.query}")
        if job.magnet_link is not None:
            print(f"Torrent title: {job.title}")
            print(f"Magnet link: {job.magnet_link}")
        if job.torrent_hash is not None:
            print(f"Torrent hash: {job.torrent_hash}")
        if job.download_directory is not None:
            print(f"Torrent directory: {os.path.join(job.download_directory, job.name)}")
        if job.is_download_only is not None:
            print(f"Is download-only job: {job.is_download_only}")
        if job.converted_file_name is not None:
            print(f"File name of converted file: {job.converted_file_name}.mp4")

def create_job(args, config):
    """Creates a new queued job"""

    job_queue = JobQueue(config)
    job = job_queue.create_job(args.keyword, args.search_term)
    job.save(logging.getLogger())

def delete_job(args, config):
    """Deletes a new queued job"""

    job_queue = JobQueue(config)
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print(f"No existing job with ID '{args.id}'")
    else:
        job.delete()

def update_job(args, config):
    """Updates a new queued job"""

    job_queue = JobQueue(config)
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print(f"No existing job with ID '{args.id}'")
    else:
        if args.status not in set(item.value for item in JobStatus):
            print(f"Unknown status value '{args.status}'")
        else:
            if args.status.lower() == "adding" and args.magnet_url is not None:
                torrent_provider = TorrentDataProvider()
                search_result = torrent_provider.create_torrent_result(args.magnet_url)
                job_queue.set_job_search_result(job, search_result)
            else:
                job.status = JobStatus(args.status)
                job.save(logging.getLogger())

def list_jobs(args, config):
    """Lists all jobs in the job queue"""

    status = args.status
    job_queue = JobQueue(config)
    jobs = job_queue.load_jobs()
    for job in jobs:
        if status is None or status == job.status:
            print(f"{job.job_id} {job.status_description}")

def clear_jobs(args, config):
    """Clears the job queue"""

    status = args.status
    job_queue = JobQueue(config)
    jobs = job_queue.load_jobs()
    for job in jobs:
        if status is None or status == job.status:
            job.delete()

def process_jobs(args, config):
    """Executes current jobs in the job queue"""

    job_queue = JobQueue(config)
    if not args.skip_search:
        airdate = datetime.now()
        job_queue.perform_searches(
            datetime(month=airdate.month, day=airdate.day, year=airdate.year), args.unattended)

    if not args.skip_add_downloads:
        job_queue.add_torrents()

    if not args.skip_query_downloads:
        job_queue.query_torrents_status()

    if not args.skip_convert:
        job_queue.perform_conversions(args.unattended)

def add_convert_subparser(subparsers):
    """Adds the argument subparser for the 'convert' command"""

    convert_subparser = subparsers.add_parser("convert")
    convert_subparser.add_argument("source", help="Source directory")
    convert_subparser.add_argument("destination", help="Destination directory")

    convert_subparser.add_argument("-k", "--keyword",
                                   help="Keyword for series to map episode names")

    video_parser = convert_subparser.add_mutually_exclusive_group(required=False)
    video_parser.add_argument("--convert-video", dest="convert_video", action="store_true")
    video_parser.add_argument("--no-convert-video", dest="convert_video", action="store_false")

    audio_parser = convert_subparser.add_mutually_exclusive_group(required=False)
    audio_parser.add_argument("--convert-audio", dest="convert_audio", action = "store_true")
    audio_parser.add_argument("--no-convert-audio", dest="convert_audio", action="store_false")

    subtitle_parser = convert_subparser.add_mutually_exclusive_group(required=False)
    subtitle_parser.add_argument("--convert-subtitles", dest="convert_subtitles",
                                 action="store_true")
    subtitle_parser.add_argument("--no-convert-subtitles", dest="convert_subtitles",
                                 action="store_false")

    convert_subparser.add_argument("-x", "--dry-run", action="store_true",
                                   help="Perform a dry run, printing data, but do not convert")
    convert_subparser.add_argument("-n", "--notify", action="store_true",
                                   help="Notify via SMS when job is complete")

    convert_subparser.set_defaults(convert_video=False,
                                   convert_audio=True,
                                   convert_subtitles=True,
                                   dry_run=False,
                                   notify=False,
                                   func=convert)

def add_search_subparser(subparsers):
    """Adds the argument subparser for the 'search' command"""

    search_subparser = subparsers.add_parser("search")
    search_subparser.add_argument("fromdate", nargs="?",
                                 default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                                 help="Start of date range within which to search for episodes " +
                                 "(defaults to previous day)")
    search_subparser.add_argument("todate", nargs="?", default=datetime.now().strftime("%Y-%m-%d"),
                                  help="End of date range within which to search for episodes " +
                                  "(defaults to current day)")

    search_subparser.add_argument("-u", "--update-metadata", dest="update_metadata",
                                  action="store_true", help="Update metadata cache from online " +
                                  "sources")

    search_subparser.add_argument("-r", "--retry-count", type=int, default=4,
                                  help="Number of times to retry to find torrents")
    search_subparser.add_argument("-d", "--directory",
                                  help="Directory to which to write magnet links to files")
    search_subparser.add_argument("-x", "--dry-run", action="store_true",
                                  help="Perform a dry run, printing data, but do not convert")
    search_subparser.add_argument("-c", "--create-jobs", dest="create_jobs", action="store_true",
                                  help="Create jobs for searches in the job queue")
    search_subparser.set_defaults(func=find_downloads)

def add_database_subparser(subparsers):
    """Adds the argument subparser for the 'database' command"""

    db_subparser = subparsers.add_parser("database")
    db_command_parsers = db_subparser.add_subparsers(dest="db_command", required=True,
                                                     help="Database subcommand")

    list_subparser = db_command_parsers.add_parser("list")
    list_subparser.add_argument("keyword",
                                help="Keyword to select the series to display from the episode " +
                                "database")
    list_subparser.set_defaults(func=list_series)

    update_subparser = db_command_parsers.add_parser("update")
    update_subparser.add_argument("-f", "--force-updates", action="store_true", default=False,
                                 help="Forces updates of metadata of all tracked series, " +
                                 "including ended ones")
    update_subparser.set_defaults(func=update_database)

def add_jobs_subparser(subparsers):
    """Adds the argument subparser for the 'jobs' command"""

    jobs_subparser = subparsers.add_parser("jobs")
    job_command_parsers = jobs_subparser.add_subparsers(dest="job_command", required=True,
                                                        help="Jobs subcommand")

    job_add_parser = job_command_parsers.add_parser("add", help="Add a new job")
    job_add_parser.add_argument("keyword", help="Keyword for the job")
    job_add_parser.add_argument("search_term", help="Search term for the job")
    job_add_parser.set_defaults(func=create_job)

    job_show_parser = job_command_parsers.add_parser("show", help="Show details of a job")
    job_show_parser.add_argument("id", help="ID of the job to show")
    job_show_parser.set_defaults(func=show_job)

    job_update_parser = job_command_parsers.add_parser("update", help="Update the status of a job")
    job_update_parser.add_argument("id", help="ID of the job to update")
    job_update_parser.add_argument("status", help="Status to which to update the job")
    job_update_parser.add_argument("magnet_url", nargs="?", default=None,
                                   help="Magnet URL with which to update the job")
    job_update_parser.set_defaults(func=update_job)

    job_remove_parser = job_command_parsers.add_parser("remove", help="Remove the specified job")
    job_remove_parser.add_argument("id", help="ID of the job to remove")
    job_remove_parser.set_defaults(func=delete_job)

    list_jobs_parser = job_command_parsers.add_parser("list", help="List jobs")
    list_jobs_parser.add_argument("status", nargs="?", default=None,
                                  help="Filter job list to status")
    list_jobs_parser.set_defaults(func=list_jobs)

    clear_jobs_parser = job_command_parsers.add_parser("clear", help="Remove jobs")
    clear_jobs_parser.add_argument("status", nargs="?", default=None,
                                  help="Remove only jobs with status")
    clear_jobs_parser.set_defaults(func=clear_jobs)

    process_jobs_parser = job_command_parsers.add_parser("process", help="Process current queue")
    search_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    search_parser.add_argument(
        "--skip-search", dest="skip_search", action = "store_true",
        help="Skip the search phase of job processing")
    search_parser.add_argument(
        "--no-skip-search", dest="skip_search", action="store_false",
        help="Perform the search phase of job processing")
    add_downloads_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    add_downloads_parser.add_argument(
        "--skip-add-downloads", dest="skip_add_downloads", action = "store_true",
        help="Skip the add downloads phase of job processing")
    add_downloads_parser.add_argument(
        "--no-skip-add-downloads", dest="skip_add_downloads", action="store_false",
        help="Perform the add downloads phase of job processing")
    query_downloads_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    query_downloads_parser.add_argument(
        "--skip-query-downloads", dest="skip_query_downloads", action = "store_true",
        help="Skip the query downloads status phase of job processing")
    query_downloads_parser.add_argument(
        "--no-skip-query-downloads", dest="skip_query_downloads", action="store_false",
        help="Perform the query downloads status phase of job processing")
    convert_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    convert_parser.add_argument("--skip-convert", dest="skip_convert", action = "store_true",
                                help="Skip the convert phase of job processing")
    convert_parser.add_argument("--no-skip-convert", dest="skip_convert", action="store_false",
                                help="Perform the convert phase of job processing")
    process_jobs_parser.set_defaults(skip_search=False, skip_convert=False, func=process_jobs)

def setup_logging(config, is_unattended):
    """Sets up logging for the Infield Fly library"""

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    if is_unattended:
        if not os.path.isdir(config.conversion.log_directory):
            os.makedirs(config.conversion.log_directory)
        handler = RotatingFileHandler(
            os.path.join(config.conversion.log_directory, "infieldfly.log"),
            backupCount=9,
            maxBytes=1048576)
        handler.setLevel(config.conversion.log_level)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(config.conversion.log_level)
        handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

def load_config(config_file):
    """Loads configuration from the specified file"""

    settings_dict = None
    infield_fly_directory = os.path.dirname(os.path.realpath(__file__))

    # If unspecified, default to reading config from settings.json in the
    # same directory as this file.
    config_file_path = (config_file
                        if config_file is not None
                        else os.path.join(infield_fly_directory, "settings.json"))

    if os.path.exists(config_file_path):
        with open(config_file_path, encoding='utf-8') as settings_file:
            settings_dict = json.load(settings_file)

    return Configuration(infield_fly_directory, settings_dict)

def main():
    """Main entry point"""

    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--unattended", action="store_true", default=False,
                        help="Run Infield Fly in unattended mode.")
    parser.add_argument("-c", "--config", nargs="?", default=None,
                        help="Path to the JSON file containing configuration values " +
                        "(defaults to settings.json in the same directory as infieldfly.py)")
    subparsers = parser.add_subparsers(dest="command", required=True,
                                       help="Command to use")
    add_convert_subparser(subparsers)
    add_search_subparser(subparsers)
    add_database_subparser(subparsers)
    add_jobs_subparser(subparsers)
    args = parser.parse_args()
    config = load_config(args.config)
    setup_logging(config, args.unattended)
    args.func(args, config)

if __name__ == "__main__":
    main()
