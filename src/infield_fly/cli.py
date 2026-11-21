# PYTHON_ARGCOMPLETE_OK
"""Main module for converting files"""

# Shell completion runs this module on every press of the Tab key, so modules
# with expensive dependencies are imported in the commands that use them.
  # pylint: disable=import-outside-toplevel

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import argcomplete

from infield_fly import __version__
from infield_fly.configuration import (CONFIG_FILE_ENVIRONMENT_VARIABLE,
                                       DEFAULT_CONFIG_DIRECTORY, Configuration)
from infield_fly.job_status import JobStatus, parse_job_status

logger = logging.getLogger(__name__)

# Logged when the episode database isn't updated, since another update is in progress.
DATABASE_UPDATE_SKIPPED = "Not updating the episode database; an update by %s is still in progress"


def get_search_strings(from_date, to_date, episode_db, metadata_settings):
    """Gets the set of search strings for all tracked series during the specified date range"""

    found_episodes = []
    searches_to_perform = []
    for tracked_series in metadata_settings.tracked_series:
        series = episode_db.get_series(tracked_series.series_id)
        if series is None:
            continue
        series_episodes = series.get_episodes_by_airdate(from_date, to_date)
        for series_episode in series_episodes:
            found_episodes.append(series_episode)
            for search in tracked_series.get_episode_searches(
                    series_episode.season_number, series_episode.episode_number):
                searches_to_perform.append(
                    {"keyword": tracked_series.main_keyword, "search": search})

    print("Episodes found:")
    for episode in found_episodes:
        print(f"{episode.plex_title} (airdate {episode.airdate:%Y-%m-%d})")

    return searches_to_perform

def find_downloads(args, config):
    """Finds episodes aired in a date range, and prints or creates jobs for their search strings"""

    from infield_fly.database import EpisodeDatabase

    episode_db = EpisodeDatabase.load_from_cache(config)
    from_date = datetime.strptime(args.fromdate, "%Y-%m-%d")
    to_date = datetime.strptime(args.todate, "%Y-%m-%d")

    if args.update_metadata:
        def update():
            episode_db.update_all_tracked_series()
            episode_db.save_to_cache()

        run_holding_lock(config.conversion.database_lock_file, "search --update-metadata",
                         DATABASE_UPDATE_SKIPPED, update)

    print(f"Finding episodes aired between {from_date:%Y-%m-%d} and {to_date:%Y-%m-%d}")
    searches_to_perform = get_search_strings(from_date, to_date, episode_db, config.metadata)

    print("")
    if args.create_jobs and not args.dry_run:
        create_search_jobs(config, searches_to_perform)
    else:
        if args.create_jobs:
            print("Dry run requested. Not creating jobs.")
        print("Search strings:")
        for search in searches_to_perform:
            for index, query in enumerate(search["search"].queries):
                print(("  then: " if index > 0 else "") + query
                      + format_release_groups(search["search"].release_groups.get(query))
                      + (" (download only)" if index == 0 and search["search"].is_download_only
                         else ""))

def create_search_jobs(config, searches):
    """Creates a job for each search, unless job processing is running"""

    from infield_fly.job_operations import JobOperationError, changing_jobs
    from infield_fly.jobs import JobQueue

    job_queue = JobQueue(config)
    try:
        with changing_jobs(config):
            for search in searches:
                job_queue.create_search_job(search["keyword"], search["search"])
    except JobOperationError as ex:
        print(ex)

def format_release_groups(release_groups):
    """Formats the release groups accepted for a search string for display"""

    return f" [release groups: {', '.join(release_groups)}]" if release_groups else ""

def list_series(args, config):
    """Lists the episodes of a series in the cached dataabase."""

    from infield_fly.database import EpisodeDatabase

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
    """
    Updates the episode metadata in the database for tracked series, unless another process
    is already updating it
    """

    from infield_fly.database import EpisodeDatabase

    def update():
        # Loading the database once the lock is held starts from the latest update.
        episode_db = EpisodeDatabase.load_from_cache(config)
        episode_db.update_all_tracked_series(force_updates=args.force_updates,
                                             is_unattended_mode=args.unattended)
        episode_db.save_to_cache()

    run_holding_lock(config.conversion.database_lock_file, "database update",
                     DATABASE_UPDATE_SKIPPED, update)

def run_holding_lock(lock_file, description, skipped_message, work):
    """
    Runs work, a function, holding a process lock, so that another process wanting the lock
    can't run at the same time. If another process holds the lock, the work is skipped, and
    skipped_message is logged, with %s replaced by a description of the lock's holder.
    """

    from contextlib import ExitStack

    from infield_fly.concurrency import LockHeldError, ProcessLock, describe_lock_holder

    with ExitStack() as stack:
        # Only acquiring the lock is caught, not errors raised by the work.
        try:
            stack.enter_context(ProcessLock(lock_file, description))
        except LockHeldError as ex:
            logger.warning(skipped_message, describe_lock_holder(ex.holder))
            return
        work()

def convert(args, config):
    """Converts a file using the specified conversion arguments"""

    from infield_fly import conversion
    from infield_fly.database import EpisodeDatabase

    episode_db = EpisodeDatabase.load_from_cache(config)
    series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)
    if args.keyword is not None and series_metadata is None:
        print(f"Keyword '{args.keyword}' was not found in the database")
        return

    errors = conversion.validate_video_options(args.convert_video, args.aspect_ratio, args.crf)
    if errors:
        print(errors[0])
        return

    mapper = conversion.FileMapper(
        episode_db, file_name_substitutions=config.conversion.string_substitutions)
    file_map = mapper.map_files(args.source, args.destination, args.keyword)

    settings = conversion.ConversionSettings(
        convert_video=args.convert_video,
        convert_audio=args.convert_audio,
        convert_subtitles=args.convert_subtitles,
        aspect_ratio=args.aspect_ratio,
        crf=(args.crf if args.crf is not None else conversion.ConversionSettings.crf))

    if not file_map:
        logger.warning("No files to convert in %s", args.source)
        return

    if args.stage and not args.dry_run:
        if not convert_staged(args, config, file_map, settings):
            return
    else:
        for src_file, dest_file in file_map:
            converter = conversion.Converter(src_file, dest_file,
                                             config.conversion.ffmpeg_location, args.unattended)
            converter.convert_file(settings, dry_run=args.dry_run, overwrite=args.overwrite)

    if args.notify:
        notify_conversion_complete(args, config)

def notify_conversion_complete(args, config):
    """Sends an SMS notification that a conversion is complete, if notifications are set up"""

    from infield_fly import notification

    if args.dry_run:
        print("Operation complete. Not sending notification on dry run.")
    elif config.notification is None:
        print("No config notification settings in settings file. Not notifying.")
    elif (config.notification.receiving_number is None
          or config.notification.receiving_number == ""):
        print("No recipient number specified in notification settings in settings "
              "file. Not notifying.")
    else:
        notifier = notification.Notifier.create_default_notifier(config)
        if notifier is not None:
            notifier.notify(config.notification.receiving_number,
                            f"Conversion of {args.source} complete.")

def convert_staged(args, config, file_map, settings):
    """
    Converts files in the staging directory, moving them to their destinations once
    converted, returning whether the conversion succeeded
    """

    import subprocess

    from infield_fly.concurrency import LockHeldError, describe_lock_holder
    from infield_fly.staged_conversion import ConversionCancelled
    from infield_fly.staged_conversion import convert_staged as convert_in_staging

    try:
        convert_in_staging(config, file_map, settings, overwrite=args.overwrite,
                           progress_file=args.progress_file,
                           is_unattended_mode=args.unattended)
    except LockHeldError as ex:
        logger.warning("Not converting; a conversion by %s is still in progress",
                       describe_lock_holder(ex.holder))
    except FileExistsError as ex:
        logger.error("Not converting: %s; use --overwrite to replace them", ex)
    except ConversionCancelled:
        logger.warning("Conversion of %s cancelled", args.source)
    except subprocess.CalledProcessError as ex:
        logger.error("Conversion of %s failed: %s", args.source, ex)
    else:
        logger.info("Conversion of %s complete", args.source)
        return True
    return False

def show_job(args, config):
    """Deletes a new queued job"""

    from infield_fly.jobs import JobQueue

    job_queue = JobQueue(config)
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print(f"No existing job with ID '{args.id}'")
    else:
        print(f"ID: {job.job_id}")
        print(f"Status: {job.status.value}")
        print(f"Date added: {job.added}")
        print(f"Series keyword: {job.keyword}")
        print(f"Search string: {job.query}"
              f"{format_release_groups(job.release_groups.get(job.query))}")
        for fallback_query in job.fallback_queries:
            print(f"Fallback search string: {fallback_query}"
                  f"{format_release_groups(job.release_groups.get(fallback_query))}")
        if job.magnet_link is not None:
            print(f"Torrent title: {job.title}")
            print(f"Magnet link: {job.magnet_link}")
        if job.torrent_hash is not None:
            print(f"Torrent hash: {job.torrent_hash}")
        print_nzb_details(job)
        if job.download_directory is not None:
            print(f"Torrent directory: {os.path.join(job.download_directory, job.name)}")
        if job.is_download_only is not None:
            print(f"Is download-only job: {job.is_download_only}")
        if job.converted_file_name is not None:
            print(f"File name of converted file: {job.converted_file_name}.mp4")

def print_nzb_details(job):
    """Prints the details of the NZB file found for a job, and of those rejected for it"""

    if job.nzb_guid is not None or job.nzo_id is not None or job.nzb_file is not None:
        print(f"NZB title: {job.title}")
    if job.nzb_guid is not None:
        print(f"NZB indexer ID: {job.nzb_guid}")
    if job.nzb_file is not None:
        print(f"NZB file: {job.nzb_file}")
    if job.nzo_id is not None:
        print(f"SABnzbd ID: {job.nzo_id}")
    if job.rejected:
        print(f"Rejected NZB indexer IDs: {', '.join(job.rejected)}")

def create_job(args, config):
    """Creates a new queued job"""

    from infield_fly.job_operations import JobOperationError, add_job, changing_jobs
    from infield_fly.jobs import JobQueue

    try:
        with changing_jobs(config):
            job = add_job(JobQueue(config), config, args.keyword, args.search_term)
    except JobOperationError as ex:
        print(ex)
    else:
        print(f"Added job {job.job_id}")

def delete_job(args, config):
    """Deletes a queued job"""

    from infield_fly.job_operations import JobOperationError, changing_jobs
    from infield_fly.jobs import JobQueue

    job = JobQueue(config).get_job_by_id(args.id)
    if job is None:
        print(f"No existing job with ID '{args.id}'")
        return
    try:
        with changing_jobs(config):
            job.delete()
    except JobOperationError as ex:
        print(ex)

def update_job(args, config):
    """Sets the status of a queued job, with a download for the adding or pending status"""

    from infield_fly.job_operations import JobOperationError, changing_jobs, set_job_status
    from infield_fly.jobs import JobQueue

    job_queue = JobQueue(config)
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print(f"No existing job with ID '{args.id}'")
        return
    status = parse_job_status(args.status)
    if status is None:
        print(f"Unknown status value '{args.status}'")
        return
    try:
        with changing_jobs(config):
            warnings = set_job_status(job_queue, job, status, args.update_data)
    except JobOperationError as ex:
        print(ex)
        return
    for warning in warnings:
        print(f"Warning: {warning}")

def reset_job(args, config):
    """Clears the downloads rejected for a job, and for the other jobs for the same episode"""

    from infield_fly.job_operations import JobOperationError, changing_jobs
    from infield_fly.jobs import JobQueue

    job_queue = JobQueue(config)
    job = job_queue.get_job_by_id(args.id)
    if job is None:
        print(f"No existing job with ID '{args.id}'")
        return
    try:
        with changing_jobs(config):
            reset_jobs = job_queue.reset_rejections(job)
    except JobOperationError as ex:
        print(ex)
        return
    for reset in reset_jobs:
        print(f"Cleared rejected downloads for job {reset.job_id}")

def list_jobs(args, config):
    """Lists all jobs in the job queue"""

    from infield_fly.jobs import JobQueue

    status = None
    if args.status is not None:
        status = parse_job_status(args.status)
        if status is None:
            print(f"Unknown status value '{args.status}'")
            return

    job_queue = JobQueue(config)
    jobs = job_queue.load_jobs()
    for job in jobs:
        if status is None or status == job.status:
            print(f"{job.job_id} {job.status_description}")

def clear_jobs(args, config):
    """Removes all jobs, or those with a status"""

    from infield_fly.job_operations import JobOperationError, changing_jobs
    from infield_fly.jobs import JobQueue

    status = None
    if args.status is not None:
        status = parse_job_status(args.status)
        if status is None:
            print(f"Unknown status value '{args.status}'")
            return

    try:
        with changing_jobs(config):
            for job in JobQueue(config).load_jobs():
                if status is None or status == job.status:
                    job.delete()
    except JobOperationError as ex:
        print(ex)

def process_jobs(args, config):
    """
    Executes current jobs in the job queue, unless another process, such as a scheduled run,
    is already processing them
    """

    run_holding_lock(config.conversion.job_lock_file, "jobs process",
                     "Not processing jobs; job processing by %s is still in progress",
                     lambda: run_processing_phases(args, config))

def run_processing_phases(args, config):
    """Runs the job processing phases selected by the command line arguments"""

    from infield_fly.jobs import JobQueue

    job_queue = JobQueue(config)
    if args.search:
        airdate = datetime.now()
        job_queue.refresh_jobs(datetime(month=airdate.month, day=airdate.day, year=airdate.year))

    if args.search_downloads:
        job_queue.search_downloads()

    if args.add_downloads:
        job_queue.add_downloads()

    if args.query_downloads:
        job_queue.query_downloads_status()

    if args.convert:
        job_queue.perform_conversions(args.unattended)

def run_web(args, config):
    """Runs the web interface until interrupted"""

    import getpass

    try:
        import uvicorn

        from infield_fly.settings_file import SettingsFile
        from infield_fly.web.app import create_app
        from infield_fly.web.auth import Authenticator, load_or_create_secret_key
    except ImportError as ex:
        print(f"The web interface requires the '{ex.name}' package. Install the web "
              "interface dependencies with: pip install -e \".[web]\"")
        return

    app = create_app(
        lambda: load_config(args.config),
        Authenticator(args.allowed_user or getpass.getuser(), args.pam_service),
        load_or_create_secret_key(
            os.path.join(config.conversion.infield_fly_directory, ".web_secret")),
        https_only=not args.allow_http,
        root_path=args.root_path,
        settings_file=SettingsFile(resolve_config_path(args.config)[0]),
        browse_roots=args.browse_roots)
    # Proxy headers are trusted only from the local reverse proxy, so that the client
    # address used to limit failed logins cannot be forged by other clients.
    uvicorn.run(app, host=args.host, port=args.port, root_path=args.root_path,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", log_config=None)

def complete_job_status(**_):
    """Completes a job status argument with the valid job status values"""

    return [item.value for item in JobStatus if item != JobStatus.UNKNOWN]

def complete_job_id(parsed_args, **_):
    """Completes a job ID argument with the IDs of the jobs in the job queue"""

    # Job files are named by job ID, so listing the job directory avoids
    # importing and loading the job queue.
    try:
        config = load_config(parsed_args.config)
        return sorted(x for x in os.listdir(config.conversion.job_directory)
                      if not x.startswith("."))
    except (OSError, ValueError):
        return []

def complete_series_keyword(parsed_args, **_):
    """Completes a series keyword argument with the keywords of the tracked series"""

    try:
        config = load_config(parsed_args.config)
    except (OSError, ValueError):
        return []
    return sorted(set(keyword
                      for tracked_series in config.metadata.tracked_series
                      for keyword in tracked_series.keywords))

def add_convert_subparser(subparsers):
    """Adds the argument subparser for the 'convert' command"""

    convert_subparser = subparsers.add_parser("convert")
    convert_subparser.add_argument("source", help="Source directory")
    convert_subparser.add_argument("destination", help="Destination directory")

    convert_subparser.add_argument("-k", "--keyword",
                                   help="Keyword for series to map episode names"
                                   ).completer = complete_series_keyword

    video_parser = convert_subparser.add_mutually_exclusive_group(required=False)
    video_parser.add_argument("--convert-video", dest="convert_video", action="store_true")
    video_parser.add_argument("--no-convert-video", dest="convert_video", action="store_false")
    convert_subparser.add_argument("-a", "--aspect-ratio", dest="aspect_ratio", type=float,
                                   help="Aspect ratio (e.g., 2.40) to which to crop the center " +
                                   "of the video stream; requires --convert-video")
    convert_subparser.add_argument("--crf", type=float,
                                   help="Constant rate factor (0-51) for video conversion " +
                                   "(defaults to 17); requires --convert-video")

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
    convert_subparser.add_argument("--stage", action="store_true",
                                   help="Convert in the staging directory, moving converted " +
                                   "files to the destination when done; only one staged " +
                                   "conversion runs at a time")
    convert_subparser.add_argument("--overwrite", action="store_true",
                                   help="Replace existing converted files")
    convert_subparser.add_argument("--progress-file", metavar="PATH",
                                   help="With --stage, write the conversion's progress to " +
                                   "this file")

    convert_subparser.set_defaults(convert_video=False,
                                   aspect_ratio=None,
                                   crf=None,
                                   convert_audio=True,
                                   convert_subtitles=True,
                                   dry_run=False,
                                   notify=False,
                                   stage=False,
                                   overwrite=False,
                                   progress_file=None,
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

    search_subparser.add_argument("-x", "--dry-run", action="store_true",
                                  help="Print the search strings, but do not create jobs")
    search_subparser.add_argument("-c", "--create-jobs", dest="create_jobs", action="store_true",
                                  help="Create jobs for the search strings in the job queue")
    search_subparser.set_defaults(func=find_downloads)

def add_database_subparser(subparsers):
    """Adds the argument subparser for the 'database' command"""

    db_subparser = subparsers.add_parser("database")
    db_command_parsers = db_subparser.add_subparsers(dest="db_command", required=True,
                                                     help="Database subcommand")

    list_subparser = db_command_parsers.add_parser("list")
    list_subparser.add_argument("keyword",
                                help="Keyword to select the series to display from the episode " +
                                "database").completer = complete_series_keyword
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
    job_add_parser.add_argument(
        "keyword", help="Keyword for the job").completer = complete_series_keyword
    job_add_parser.add_argument("search_term", help="Search term for the job")
    job_add_parser.set_defaults(func=create_job)

    job_show_parser = job_command_parsers.add_parser("show", help="Show details of a job")
    job_show_parser.add_argument(
        "id", help="ID of the job to show").completer = complete_job_id
    job_show_parser.set_defaults(func=show_job)

    job_update_parser = job_command_parsers.add_parser("update", help="Update the status of a job")
    job_update_parser.add_argument(
        "id", help="ID of the job to update").completer = complete_job_id
    job_update_parser.add_argument(
        "status", help="Status to which to update the job").completer = complete_job_status
    job_update_parser.add_argument("update_data", nargs="?", default=None,
                                   help="Additional data with which to update the job")
    job_update_parser.set_defaults(func=update_job)

    job_reset_parser = job_command_parsers.add_parser(
        "reset", help="Clear the downloads rejected for a job and others for its episode")
    job_reset_parser.add_argument(
        "id", help="ID of the job to reset").completer = complete_job_id
    job_reset_parser.set_defaults(func=reset_job)

    job_remove_parser = job_command_parsers.add_parser("remove", help="Remove the specified job")
    job_remove_parser.add_argument(
        "id", help="ID of the job to remove").completer = complete_job_id
    job_remove_parser.set_defaults(func=delete_job)

    list_jobs_parser = job_command_parsers.add_parser("list", help="List jobs")
    list_jobs_parser.add_argument("status", nargs="?", default=None,
                                  help="Filter job list to status"
                                  ).completer = complete_job_status
    list_jobs_parser.set_defaults(func=list_jobs)

    clear_jobs_parser = job_command_parsers.add_parser("clear", help="Remove jobs")
    clear_jobs_parser.add_argument("status", nargs="?", default=None,
                                  help="Remove only jobs with status"
                                  ).completer = complete_job_status
    clear_jobs_parser.set_defaults(func=clear_jobs)

    add_process_jobs_subparser(job_command_parsers)

def add_process_jobs_subparser(job_command_parsers):
    """Adds the argument subparser for the 'jobs process' command"""

    process_jobs_parser = job_command_parsers.add_parser("process", help="Process current queue")
    search_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    search_parser.add_argument(
        "--search", dest="search", action = "store_true",
        help="Create jobs for episodes of tracked series airing today")
    search_parser.add_argument(
        "--no-search", dest="search", action="store_false",
        help="Skip creating jobs for episodes airing today")
    search_downloads_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    search_downloads_parser.add_argument(
        "--search-downloads", dest="search_downloads", action="store_true",
        help="Search the Newznab indexer for NZB files for waiting jobs")
    search_downloads_parser.add_argument(
        "--no-search-downloads", dest="search_downloads", action="store_false",
        help="Skip searching for NZB files for waiting jobs")
    add_downloads_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    add_downloads_parser.add_argument(
        "--add-downloads", dest="add_downloads", action = "store_true",
        help="Perform the add downloads phase of job processing")
    add_downloads_parser.add_argument(
        "--no-add-downloads", dest="add_downloads", action="store_false",
        help="Skip the add downloads phase of job processing")
    query_downloads_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    query_downloads_parser.add_argument(
        "--query-downloads", dest="query_downloads", action = "store_true",
        help="Perform the query downloads status phase of job processing")
    query_downloads_parser.add_argument(
        "--no-query-downloads", dest="query_downloads", action="store_false",
        help="Skip the query downloads status phase of job processing")
    convert_parser = process_jobs_parser.add_mutually_exclusive_group(required=False)
    convert_parser.add_argument("--convert", dest="convert", action = "store_true",
                                help="Perform the convert phase of job processing")
    convert_parser.add_argument("--no-convert", dest="convert", action="store_false",
                                help="Skip the convert phase of job processing")
    process_jobs_parser.set_defaults(
        search=True, search_downloads=True, add_downloads=True, query_downloads=True,
        convert=True, func=process_jobs)

def add_web_subparser(subparsers):
    """Adds the argument subparser for the 'web' command"""

    web_subparser = subparsers.add_parser("web", help="Run the web interface")
    web_subparser.add_argument("--host", default="127.0.0.1",
                               help="Address on which to listen (defaults to 127.0.0.1)")
    web_subparser.add_argument("--port", type=int, default=8642,
                               help="Port on which to listen (defaults to 8642)")
    web_subparser.add_argument("--root-path", default="",
                               help="URL path under which a reverse proxy serves the web " +
                               "interface, such as /infield-fly")
    web_subparser.add_argument("--allowed-user", default=None,
                               help="User allowed to log in (defaults to the user running " +
                               "the web interface)")
    web_subparser.add_argument("--pam-service", default="infield-fly",
                               help="PAM service with which to check passwords (defaults " +
                               "to infield-fly)")
    web_subparser.add_argument("--browse-root", dest="browse_roots", action="append",
                               default=[], metavar="PATH",
                               help="Directory whose downloads can be chosen for jobs; " +
                               "can be given more than once")
    web_subparser.add_argument("--allow-http", action="store_true",
                               help="Send the session cookie over plain HTTP; use only for " +
                               "local testing")
    web_subparser.set_defaults(func=run_web)

def setup_logging(config, is_unattended):
    """Sets up logging for the Infield Fly library"""

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    if is_unattended:
        if not os.path.isdir(config.conversion.log_directory):
            os.makedirs(config.conversion.log_directory)
        handler = RotatingFileHandler(
            config.conversion.log_file,
            backupCount=9,
            maxBytes=1048576)
        handler.setLevel(config.conversion.log_level)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(config.conversion.log_level)
        handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)

def resolve_config_path(config_file):
    """
    Gets the absolute path of the settings file, and whether it was specified explicitly
    rather than being the default
    """

    # If unspecified, read the config file named by the environment variable,
    # falling back to settings.json in the user's config directory.
    explicit_config_file = config_file or os.environ.get(CONFIG_FILE_ENVIRONMENT_VARIABLE)
    config_file_path = (explicit_config_file
                        or os.path.join(DEFAULT_CONFIG_DIRECTORY, "settings.json"))
    return os.path.abspath(os.path.expanduser(config_file_path)), bool(explicit_config_file)

def load_config(config_file):
    """Loads configuration from the specified file"""

    settings_dict = None

    # Default data directories are resolved relative to the config file's directory.
    config_file_path, explicit_config_file = resolve_config_path(config_file)
    infield_fly_directory = os.path.dirname(config_file_path)

    if os.path.exists(config_file_path):
        with open(config_file_path, encoding='utf-8') as settings_file:
            settings_dict = json.load(settings_file)
    elif explicit_config_file:
        # A missing default settings file means default settings, but a missing
        # explicitly specified one is almost certainly a mistake.
        raise FileNotFoundError(f"Settings file '{config_file_path}' does not exist")

    return Configuration.from_dict(infield_fly_directory, settings_dict)

def main():
    """Main entry point"""

    parser = argparse.ArgumentParser(prog="infield-fly")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-u", "--unattended", action="store_true", default=False,
                        help="Run Infield Fly in unattended mode.")
    parser.add_argument("-c", "--config", nargs="?", default=None,
                        help="Path to the JSON file containing configuration values " +
                        f"(defaults to ${CONFIG_FILE_ENVIRONMENT_VARIABLE}, then " +
                        "~/.config/infield-fly/settings.json); default data directories " +
                        "are created alongside it")
    subparsers = parser.add_subparsers(dest="command", required=True,
                                       help="Command to use")
    add_convert_subparser(subparsers)
    add_search_subparser(subparsers)
    add_database_subparser(subparsers)
    add_jobs_subparser(subparsers)
    add_web_subparser(subparsers)
    argcomplete.autocomplete(parser)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except FileNotFoundError as ex:
        parser.error(str(ex))
    setup_logging(config, args.unattended)
    args.func(args, config)
