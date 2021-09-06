"""Main module for converting files"""

import argparse
from config_settings import Configuration
from episode_database import EpisodeDatabase
from file_converter import Converter, FileMapper
from notifier import Notifier

def replace_strings(input, substitutions):
    output = input
    if substitutions is not None:
        for replacement in substitutions:
            output = output.replace(replacement, substitutions[replacement])
    return output

parser = argparse.ArgumentParser()
parser.add_argument("source", help="Source directory")
parser.add_argument("destination", help="Destination directory")

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
if args.keyword is not None and series_metadata is None:
    print("Keyword '{}' was not found in the database".format(args.keyword))
else:
    mapper = FileMapper(episode_db)
    file_map = mapper.map_files(args.source, args.destination, args.keyword)

    if args.dry_run:
        for src_file, dest_file in file_map:
            converted_dest_file = replace_strings(dest_file, config.conversion.string_substitutions)
            print("Convert {} -> {}".format(src_file, converted_dest_file))

    for src_file, dest_file in file_map:
        converted_dest_file = replace_strings(dest_file, config.conversion.string_substitutions)
        converter = Converter(src_file, converted_dest_file, config.conversion.ffmpeg_location)
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
