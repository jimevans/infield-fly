import argparse
import json
import os

from file_converter import Converter
from file_mapper import FileMapper
from notifier import Notifier

parser = argparse.ArgumentParser()
parser.add_argument("source", help = "Source directory")
parser.add_argument("destination", help = "Destination directory")

parser.add_argument("-f", "--ffmpeg", help = "Location of ffmpeg")
parser.add_argument("-d", "--data-file", 
                    help = "Location of episode name data file")

video_parser = parser.add_mutually_exclusive_group(required = False)
video_parser.add_argument("--convert-video", dest = "convert_video",
                          action = "store_true")
video_parser.add_argument("--no-convert-video", dest = "convert_video",
                          action = "store_false")

audio_parser = parser.add_mutually_exclusive_group(required = False)
audio_parser.add_argument("--convert-audio", dest = "convert_audio",
                          action = "store_true")
audio_parser.add_argument("--no-convert-audio", dest = "convert_audio",
                          action = "store_false")

subtitle_parser = parser.add_mutually_exclusive_group(required = False)
subtitle_parser.add_argument("--convert-subtitles", dest = "convert_subtitles",
                             action = "store_true")
subtitle_parser.add_argument("--no-convert-subtitles",
                             dest = "convert_subtitles",
                             action = "store_false")

parser.add_argument("-x", "--dry-run", action = "store_true",
                    help = "Perform a dry run, printing data, but do not convert")
parser.add_argument("-n", "--notify", action = "store_true",
                    help = "Notify via SMS when job is complete")

parser.set_defaults(convert_video = False,
                    convert_audio = True,
                    convert_subtitles = True,
                    dry_run = False,
                    notify = False)
args = parser.parse_args()

settings_file_path = os.path.join(os.path.realpath(__file__), "settings.json")
if os.path.exists(settings_file_path):
    settings_file = open(settings_file_path)
    settings = json.load(settings_file)
    for name in settings:
        os.environ[name] = settings[name]
    settings_file.close()

mapper = FileMapper(args.data_file)
file_map = mapper.map_files(args.source, args.destination)

if args.dry_run:
    for src_file, dest_file in file_map:
        print("Convert {} -> {}".format(src_file, dest_file))

converter = Converter(args.ffmpeg)
for src_file, dest_file in file_map:
    converter.convert_file(src_file, dest_file,
                           convert_video = args.convert_video,
                           convert_audio = args.convert_audio,
                           convert_subtitles = args.convert_subtitles,
                           dry_run = args.dry_run)

if args.notify:
    if args.dry_run:
        print("Operation complete. Not sending notification on dry run.")
    else:
        if "TWILIO_SMS_RECIPIENT" not in os.environ:
            print("TWILIO_SMS_RECIPIENT environment variable not set. Not notirying.")
        else:
            notification_receiver = os.environ["TWILIO_SMS_RECIPIENT"]
            notifier = Notifier.create_default_notifier()
            notifier.notify(notification_receiver, "Conversion of {} complete.".format(args.source))