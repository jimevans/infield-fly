"""Main module for converting files"""

import argparse
import os
from datetime import datetime, timedelta
from config_settings import Configuration
from episode_database import EpisodeDatabase
from file_converter import Converter, FileMapper
from job_queue import JobQueue
from notifier import Notifier
from torrent_finder import TorrentDataProvider

def replace_strings(input_value, substitutions):
    """Replaces substring values according to the passed in list of substitutions"""

    output_value = input_value
    if substitutions is not None:
        for replacement in substitutions:
            output_value = output_value.replace(replacement, substitutions[replacement])
    return output_value

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
                searches_to_perform.append("{} {}".format(
                    " ".join(stored_search.search_terms),
                    "s{:02d}e{:02d}".format(
                        series_episode.season_number, series_episode.episode_number)
                ))

    print("Episodes found:")
    for episode in found_episodes:
        print("{} (airdate {:%Y-%m-%d})".format(episode.plex_title, episode.airdate))

    return searches_to_perform

def search_for_torrents(searches_to_perform, search_retry_count, output_directory):
    """Searches for torrents using the specified search strings"""

    finder = TorrentDataProvider()
    print("Performing searches")
    for search in searches_to_perform:
        search_results = finder.search(search, retry_count=search_retry_count)
        if len(search_results) == 0:
            print("No results found after retries")
        for search_result in search_results:
            if output_directory is not None and os.path.isdir(output_directory):
                magnet_file_path = os.path.join(output_directory,
                                                search_result.title + ".magnet")
                print("Writing magnet link to {}".format(magnet_file_path))
                with open(magnet_file_path, "w") as magnet_file:
                    magnet_file.write(search_result.magnet_link)
                    magnet_file.flush()
            else:
                print("Torrent title: {}".format(search_result.title))
                print("Magnet link: {}".format(search_result.magnet_link))

def find_downloads(args):
    """Finds available downloads"""

    config = Configuration()
    episode_db = EpisodeDatabase.load_from_cache(config.metadata)
    from_date = datetime.strptime(args.fromdate, "%Y-%m-%d")
    to_date = datetime.strptime(args.todate, "%Y-%m-%d")

    if args.update_metadata:
        episode_db.update_all_tracked_series()
        episode_db.save_to_cache()

    print("Searching for downloads between {} and {}".format(
          from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")))
    searches_to_perform = get_search_strings(from_date, to_date, episode_db, config.metadata)

    print("")
    if args.dry_run:
        print("Dry run requested. Not performing searches. Searches to perform:")
        for search in searches_to_perform:
            print(search)
    else:
        search_for_torrents(searches_to_perform, args.retry_count, args.directory)

def list_series(args):
    """Lists the episodes of a series in the cached dataabase."""

    config = Configuration()
    episode_db = EpisodeDatabase.load_from_cache(config.metadata)
    series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)
    if series_metadata is None:
        print("Keyword '{}' was not found in the database".format(args.keyword))
    else:
        for episode in series_metadata.episodes:
            airdate = ("not aired"
                       if episode.airdate is None
                       else episode.airdate.strftime("%Y-%m-%d"))
            print("s{:02d}e{:02d} (aired: {}) - {}".format(
                episode.season_number, episode.episode_number, airdate, episode.title))

def update_database(args):
    """Updates the episode metadata in the databasae for tracked series"""

    config = Configuration()
    episode_db = EpisodeDatabase.load_from_cache(config.metadata)
    episode_db.update_all_tracked_series(force_updates=args.force_updates)
    episode_db.save_to_cache()

def convert(args):
    """Converts a file using the soecified conversion arguments"""

    config = Configuration()
    episode_db = EpisodeDatabase.load_from_cache(config.metadata)
    series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)
    if args.keyword is not None and series_metadata is None:
        print("Keyword '{}' was not found in the database".format(args.keyword))
    else:
        mapper = FileMapper(episode_db)
        file_map = mapper.map_files(args.source, args.destination, args.keyword)

        for src_file, dest_file in file_map:
            converted_dest_file = replace_strings(dest_file,
                                                  config.conversion.string_substitutions)
            converted_dest_file = "".join(
                config.conversion.string_substitutions.get(c, c) for c in dest_file)
            converter = Converter(src_file, converted_dest_file,
                                  config.conversion.ffmpeg_location)
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
                    notifier = Notifier.create_default_notifier(config.notification)
                    if notifier is not None:
                        notifier.notify(config.notification.receiving_number,
                                        "Conversion of {} complete.".format(args.source))

def show_job(args):
    """Deletes a new queued job"""

    job_queue = JobQueue()
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print("No existing job with ID '{}'".format(args.id))
    else:
        print("ID: {}".format(job.job_id))
        print("Status: {}".format(job.status))
        print("Date added: {}".format(job.added))
        print("Series keyword: {}".format(job.keyword))
        print("Search string: {}".format(job.query))
        if job.magnet_link is not None:
            print("Torrent title: {}".format(job.title))
            print("Magnet link: {}".format(job.magnet_link))
        if job.torrent_hash is not None:
            print("Torrent hash: {}".format(job.torrent_hash))
        if job.download_directory is not None:
            print("Torrent directory: {}".format(os.path.join(job.download_directory, job.name)))
        if job.is_download_only is not None:
            print("Is download-only job: {}".format(job.is_download_only))

def create_job(args):
    """Creates a new queued job"""

    job_queue = JobQueue()
    job = job_queue.create_job(args.keyword, args.search_term)
    job.save()

def delete_job(args):
    """Deletes a new queued job"""

    job_queue = JobQueue()
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print("No existing job with ID '{}'".format(args.id))
    else:
        job.delete()

def update_job(args):
    """Updates a new queued job"""

    job_queue = JobQueue()
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print("No existing job with ID '{}'".format(args.id))
    else:
        job.status = args.status
        job.save()

def list_jobs(args):
    """Lists all jobs in the job queue"""

    status = args.status
    job_queue = JobQueue()
    jobs = job_queue.load_jobs()
    for job in jobs:
        if status is None or status == job.status:
            print("{} {} {} '{}'".format(job.job_id, job.status, job.keyword, job.query))

def clear_jobs(args):
    """Clears the job queue"""

    status = args.status
    job_queue = JobQueue()
    jobs = job_queue.load_jobs()
    for job in jobs:
        if status is None or status == job.status:
            job.delete()

def process_jobs(args):
    """Executes current jobs in the job queue"""

    job_queue = JobQueue()
    if not args.skip_search:
        airdate = datetime.now()
        job_queue.perform_searches(datetime(month=airdate.month,
                                            day=airdate.day,
                                            year=airdate.year))
    if not args.skip_convert:
        job_queue.perform_conversions()

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
    search_parser.add_argument("--skip-search", dest="skip_search", action = "store_true",
                               help="Skip the search phase of job processing")
    search_parser.add_argument("--no-skip-search", dest="skip_search", action="store_false",
                               help="Perform the search phase of job processing")
    convert_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    convert_parser.add_argument("--skip-convert", dest="skip_convert", action = "store_true",
                                help="Skip the convert phase of job processing")
    convert_parser.add_argument("--no-skip-convert", dest="skip_convert", action="store_false",
                                help="Perform the convert phase of job processing")
    process_jobs_parser.set_defaults(skip_search=False, skip_convert=False, func=process_jobs)

def main():
    """Main entry point"""

    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--unattended", action="store_true", default=False,
                        help="Run Infield Fly in unattended mode.")
    subparsers = parser.add_subparsers(dest="command", required=True,
                                       help="Command to use")
    add_convert_subparser(subparsers)
    add_search_subparser(subparsers)
    add_database_subparser(subparsers)
    add_jobs_subparser(subparsers)
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
