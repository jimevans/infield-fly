"""Main module for converting files"""

import argparse
from config_settings import Configuration
from episode_database import EpisodeDatabase
from file_converter import Converter
from file_mapper import FileMapper
from notifier import Notifier

parser = argparse.ArgumentParser()
parser.add_argument("source", help="Source directory")
parser.add_argument("destination", help="Destination directory")

parser.add_argument("-f", "--ffmpeg", help="Location of ffmpeg")
parser.add_argument("-k", "--keyword", help="Keyword for series to map episode names")

video_parser = parser.add_mutually_exclusive_group(required=False)
video_parser.add_argument("--convert-video", dest="convert_video", action="store_true")
video_parser.add_argument("--no-convert-video", dest="convert_video", action="store_false")

audio_parser = parser.add_mutually_exclusive_group(required=False)
audio_parser.add_argument("--convert-audio", dest="convert_audio", action = "store_true")
audio_parser.add_argument("--no-convert-audio", dest="convert_audio", action="store_false")

subtitle_parser = parser.add_mutually_exclusive_group(required=False)
subtitle_parser.add_argument("--convert-subtitles", dest="convert_subtitles", action="store_true")
subtitle_parser.add_argument("--no-convert-subtitles", dest="convert_subtitles",
                             action="store_false")

parser.add_argument("-x", "--dry-run", action="store_true",
                    help="Perform a dry run, printing data, but do not convert")
parser.add_argument("-n", "--notify", action="store_true",
                    help="Notify via SMS when job is complete")

parser.set_defaults(convert_video=False,
                    convert_audio=True,
                    convert_subtitles=True,
                    dry_run=False,
                    notify=False)
args = parser.parse_args()

config = Configuration()
episode_db = EpisodeDatabase.load_from_cache(config.metadata)
series_metadata = episode_db.get_tracked_series_by_keyword(args.keyword)

mapper = FileMapper(series_metadata)
file_map = mapper.map_files(args.source, args.destination)

if args.dry_run:
    for src_file, dest_file in file_map:
        print("Convert {} -> {}".format(src_file, dest_file))

for src_file, dest_file in file_map:
    converter = Converter(src_file, dest_file, args.ffmpeg)
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
            print("No recipient number specified in notification settings in settings file. "
                  "Not notifying.")
        else:
            notification_receiver = config.notification.receiving_number
            notifier = Notifier.create_default_notifier(config.notification)
            if notifier is not None:
                notifier.notify(notification_receiver,
                                "Conversion of {} complete.".format(args.source))
