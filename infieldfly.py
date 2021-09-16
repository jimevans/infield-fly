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
                    " ".join(stored_search),
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

def find_downloads(args, episode_db, metadata_settings):
    """Finds available downloads"""

    from_date = datetime.strptime(args.fromdate, "%Y-%m-%d")
    to_date = datetime.strptime(args.todate, "%Y-%m-%d")

    if args.update_metadata:
        episode_db.update_all_tracked_series()
        episode_db.save_to_cache()

    print("Searching for downloads between {} and {}".format(
          from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d")))
    searches_to_perform = get_search_strings(from_date, to_date, episode_db, metadata_settings)

    print("")
    if args.dry_run:
        print("Dry run requested. Not performing searches. Searches to perform:")
        for search in searches_to_perform:
            print(search)
    else:
        search_for_torrents(searches_to_perform, args.retry_count, args.directory)

def list_series(args, episode_db):
    """Lists the episodes of a series in the cached dataabase."""

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

def update_database(args, episode_db):
    """Updates the episode metadata in the databasae for tracked series"""

    episode_db.update_all_tracked_series(force_updates=args.force_updates)
    episode_db.save_to_cache()

def convert(args, episode_db, conversion_settings, notification_settings=None):
    """Converts a file using the soecified conversion arguments"""

    series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)
    if args.keyword is not None and series_metadata is None:
        print("Keyword '{}' was not found in the database".format(args.keyword))
    else:
        mapper = FileMapper(episode_db)
        file_map = mapper.map_files(args.source, args.destination, args.keyword)

        for src_file, dest_file in file_map:
            converted_dest_file = replace_strings(dest_file,
                                                  conversion_settings.string_substitutions)
            converter = Converter(src_file, converted_dest_file,
                                  conversion_settings.ffmpeg_location)
            converter.convert_file(convert_video=args.convert_video,
                                   convert_audio=args.convert_audio,
                                   convert_subtitles=args.convert_subtitles,
                                   dry_run=args.dry_run)

        if args.notify:
            if args.dry_run:
                print("Operation complete. Not sending notification on dry run.")
            else:
                if notification_settings is None:
                    print("No config notification settings in settings file. Not notifying.")
                elif (notification_settings.receiving_number is None
                    or notification_settings.receiving_number == ""):
                    print("No recipient number specified in notification settings in settings "
                        "file. Not notifying.")
                else:
                    notification_receiver = notification_settings.receiving_number
                    notifier = Notifier.create_default_notifier(notification_settings)
                    if notifier is not None:
                        notifier.notify(notification_receiver,
                                        "Conversion of {} complete.".format(args.source))

def parse_command_line_args():
    """Parses command line arguments"""

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True,
                                       help="Command to use")

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
                                notify=False)

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

    db_subparser = subparsers.add_parser("database")
    db_command_parsers = db_subparser.add_subparsers(dest="db_command", required=True,
                                                     help="Database subcommand")

    list_subparser = db_command_parsers.add_parser("list")
    list_subparser.add_argument("keyword",
                                help="Keyword to select the series to display from the episode " +
                                "database")

    update_subparser = db_command_parsers.add_parser("update")
    update_subparser.add_argument("-f", "--force-updates", action="store_true", default=False,
                                 help="Forces updates of metadata of all tracked series, " +
                                 "including ended ones")

    jobs_subparser = subparsers.add_parser("jobs")
    job_command_parsers = jobs_subparser.add_subparsers(dest="job_command", required=True,
                                                        help="Jobs subcommand")

    job_command_parsers.add_parser("list", help="List all jobs")

    job_add_parser = job_command_parsers.add_parser("add", help="Add a new job")
    job_add_parser.add_argument("keyword", help="Keyword for the job")
    job_add_parser.add_argument("search_term", help="Search term for the job")

    job_update_parser = job_command_parsers.add_parser("update", help="Update the status of a job")
    job_update_parser.add_argument("id", help="ID of the job to update")
    job_update_parser.add_argument("status", help="Status to which to update the job")

    job_remove_parser = job_command_parsers.add_parser("remove", help="Remove the specified job")
    job_remove_parser.add_argument("id", help="ID of the job to remove")

    job_command_parsers.add_parser("clear", help="Removes all jobs in the queue")

    job_command_parsers.add_parser("process", help="Process current queue")

    return parser.parse_args()

def main():
    """Main entry point"""

    args = parse_command_line_args()
    config = Configuration()
    episode_db = EpisodeDatabase.load_from_cache(config.metadata)
    job_queue = JobQueue()
    if args.command == "search":
        find_downloads(args, episode_db, config.metadata)
    elif args.command == "convert":
        convert(args, episode_db, config.conversion, notification_settings=config.notification)
    elif args.command == "database":
        if args.db_command == "list":
            list_series(args, episode_db)
        elif args.db_command == "update":
            update_database(args, episode_db)
    elif args.command == "jobs":
        if args.job_command == "list":
            jobs = job_queue.load_jobs()
            for job in jobs:
                print("{} {} {} '{}'".format(job.id, job.status, job.keyword, job.query))
        elif args.job_command == "clear":
            jobs = job_queue.load_jobs()
            for job in jobs:
                job.delete()
        elif args.job_command == "add":
            job = job_queue.create_job(args.keyword, args.search_term)
            job.save()
        elif args.job_command == "update":
            job = job_queue.get_job_by_id(args.id)
            job.status = args.status
            job.save()
        elif args.job_command == "remove":
            job = job_queue.get_job_by_id(args.id)
            job.delete()
        elif args.job_command == "process":
            airdate = datetime.now()
            job_queue.perform_searches(
                datetime(month=airdate.month, day=airdate.day, year=airdate.year))
            job_queue.perform_conversions()

if __name__ == "__main__":
    main()
