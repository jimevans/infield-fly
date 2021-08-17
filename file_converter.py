"""Module for converting files into correct format"""

import json
import os
import platform
import re
import subprocess


class Converter:

    """Converts files into the correct format"""

    def __init__(self, input_file, output_file, ffmpeg_location=None):
        super().__init__()
        self.ffmpeg_location = ffmpeg_location
        self.input_file = input_file
        self.output_file = output_file
        self.file_stream_info = FileStreamInfo.read_stream_info(
            input_file, self._get_ffmpeg_tool_location("ffprobe"))

    def _get_ffmpeg_tool_location(self, tool_name="ffmpeg"):
        tool_location = tool_name
        if self.ffmpeg_location is not None:
            tool_location = os.path.join(self.ffmpeg_location, tool_name)
        if platform.system() == "Windows":
            tool_location += ".exe"
        return tool_location

    def convert_file(self, dry_run=False, convert_video=False, convert_audio=True,
                     convert_subtitles=True):
        """Converts a single file"""

        is_convert_subtitles = convert_subtitles and self.file_stream_info.has_subtitle_stream
        is_convert_audio = (convert_audio
                           and self.file_stream_info.has_audio_stream)

        ffmpeg_args = []
        ffmpeg_args.append(self._get_ffmpeg_tool_location("ffmpeg"))
        ffmpeg_args.append("-hide_banner")
        ffmpeg_args.append("-i")
        ffmpeg_args.append(self.input_file)
        ffmpeg_args.append("-map_metadata")
        ffmpeg_args.append("-1")
        ffmpeg_args.append("-map_chapters")
        ffmpeg_args.append("0")
        ffmpeg_args.extend(self.get_video_conversion_args(convert_video))
        ffmpeg_args.extend(self.get_audio_conversion_args(is_convert_audio))
        ffmpeg_args.extend(self.get_subtitle_conversion_args(is_convert_subtitles))

        ffmpeg_args.append(self.output_file)
        if dry_run:
            print("Conversion arguments:")
            print(ffmpeg_args)
        else:
            subprocess.run(ffmpeg_args, check=True)

        self.convert_forced_subtitles(dry_run)

    def convert_forced_subtitles(self, dry_run=False):
        """Converts forced subtitle track, if any"""

        if self.file_stream_info.has_forced_subtitle_stream:
            base_name, _ = os.path.splitext(self.output_file)
            forced_subs_file = "{}.eng.forced.srt".format(base_name)
            forced_subs_args = []
            forced_subs_args.append(self._get_ffmpeg_tool_location("ffmpeg"))
            forced_subs_args.append("-hide_banner")
            forced_subs_args.append("-i")
            forced_subs_args.append(self.input_file)
            forced_subs_args.append("-map")
            forced_subs_args.append("0:{}".format(
                self.file_stream_info.forced_subtitle_stream.index))
            forced_subs_args.append(forced_subs_file)
            if dry_run:
                print("Forced subtitle conversion arguments:")
                print(forced_subs_args)
            else:
                subprocess.run(forced_subs_args, check=True)

    def get_video_conversion_args(self, is_convert_video):
        """Gets ffmpeg command line arguments for video streams in the file"""

        ffmpeg_args = []
        ffmpeg_args.append("-map")
        ffmpeg_args.append("0:{}".format(self.file_stream_info.video_stream.index))
        ffmpeg_args.append("-c:v")
        if is_convert_video:
            ffmpeg_args.append("libx264")
            ffmpeg_args.append("-vf")
            ffmpeg_args.append("scale=-1:1080")
            ffmpeg_args.append("-crf")
            ffmpeg_args.append("17")
            ffmpeg_args.append("-preset")
            ffmpeg_args.append("medium")
        else:
            ffmpeg_args.append("copy")

        return ffmpeg_args

    def get_audio_conversion_args(self, is_convert_audio):
        """Gets ffmpeg command line arguments for audio streams in the file"""

        ffmpeg_args = []
        ffmpeg_args.append("-map")
        ffmpeg_args.append("0:{}".format(self.file_stream_info.audio_stream.index))
        ffmpeg_args.append("-metadata:s:a:0")
        ffmpeg_args.append("language=eng")
        ffmpeg_args.append("-disposition:a:0")
        ffmpeg_args.append("default")
        ffmpeg_args.append("-c:a:0")
        if is_convert_audio:
            if (self.file_stream_info.audio_stream.codec == "aac"
                    and self.file_stream_info.audio_stream.channel_count <= 2):
                ffmpeg_args.append("copy")
            else:
                ffmpeg_args.append("aac")
                ffmpeg_args.append("-b:a:0")
                ffmpeg_args.append("160k")
                ffmpeg_args.append("-ac:a:0")
                ffmpeg_args.append("{}".format(self.file_stream_info.audio_stream.channel_count
                                            if self.file_stream_info.audio_stream.channel_count < 2
                                            else 2))
            ffmpeg_args.append("-map")
            ffmpeg_args.append("0:{}".format(self.file_stream_info.audio_stream.index))
            ffmpeg_args.append("-metadata:s:a:1")
            ffmpeg_args.append("language=eng")
            ffmpeg_args.append("-disposition:a:1")
            ffmpeg_args.append("0")
            ffmpeg_args.append("-c:a:1")
            if (self.file_stream_info.audio_stream.codec == "ac3"
                    or self.file_stream_info.audio_stream.codec == "eac3"):
                ffmpeg_args.append("copy")
            else:
                ffmpeg_args.append("ac3")
                ffmpeg_args.append("-b:a:1")
                ffmpeg_args.append("640k")
                ffmpeg_args.append("-ac:a:1")
                ffmpeg_args.append("{}".format(
                    self.file_stream_info.audio_stream.channel_count
                    if self.file_stream_info.audio_stream.channel_count < 7
                    else 6))
        else:
            ffmpeg_args.append("copy")

        return ffmpeg_args

    def get_subtitle_conversion_args(self, is_convert_subtitles):
        """Gets ffmpeg command line arguments for subtitle streams in the file"""

        ffmpeg_args = []
        if is_convert_subtitles:
            ffmpeg_args.append("-map")
            ffmpeg_args.append("0:{}".format(self.file_stream_info.subtitle_stream.index))
            ffmpeg_args.append("-metadata:s:s:0")
            ffmpeg_args.append("language=eng")
            ffmpeg_args.append("-disposition:s:0")
            ffmpeg_args.append("default")
            ffmpeg_args.append("-c:s")
            if self.file_stream_info.subtitle_stream.codec == "mov_text":
                ffmpeg_args.append("copy")
            else:
                ffmpeg_args.append("mov_text")

        return ffmpeg_args


class FileStreamInfo:

    """Represents the file stream information of a media file"""

    def __init__(self, streams):
        super().__init__()
        self.video_stream = streams["video"]
        self.audio_stream = streams["audio"]
        self.subtitle_stream = streams["subtitle"]
        self.forced_subtitle_stream = streams["forced_subtitle"]

    @staticmethod
    def _probe_file(input_file, ffprobe_location):
        ffprobe_args = []
        ffprobe_args.append(ffprobe_location)
        ffprobe_args.append("-v")
        ffprobe_args.append("quiet")
        ffprobe_args.append("-print_format")
        ffprobe_args.append("json")
        ffprobe_args.append("-show_format")
        ffprobe_args.append("-show_streams")

        ffprobe_args.append(input_file)

        process_result = subprocess.run(ffprobe_args,
                                        check=True,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        json_output = process_result.stdout
        return json.loads(json_output)

    @classmethod
    def read_stream_info(cls, input_file, ffprobe_location = "ffprobe"):
        """Reads the stream information from a file and creates a FileStreamInfo object"""

        streams = {
            "video": None,
            "audio": None,
            "subtitle": None,
            "forced_subtitle": None
        }
        metadata = FileStreamInfo._probe_file(input_file, ffprobe_location)
        for stream_metadata in metadata["streams"]:
            stream = FileStreamInfo.StreamInfo(stream_metadata)
            if stream.is_video and streams["video"] is None:
                streams["video"] = stream

            if stream.is_audio:
                if (stream.is_default or (stream.language == "eng" and streams["audio"] is None)):
                    streams["audio"] = stream

            if (stream.is_subtitle
                    and (stream.codec == "subrip" or stream.codec == "mov_text")
                    and stream.language == "eng"):
                if stream.is_forced:
                    if streams["forced_subtitle"] is None:
                        streams["forced_subtitle"] = stream
                elif streams["subtitle"] is None:
                    streams["subtitle"] = stream

        return cls(streams)

    @property
    def has_video_stream(self):
        """Gets a value indicating whether the file has a video stream"""

        return self.video_stream is not None

    @property
    def has_audio_stream(self):
        """Gets a value indicating whether the file has an audio stream"""

        return self.audio_stream is not None

    @property
    def has_subtitle_stream(self):
        """Gets a value indicating whether the file has a subtitle stream"""

        return self.subtitle_stream is not None

    @property
    def has_forced_subtitle_stream(self):
        """Gets a value indicating whether the file has a forced subtitle stream"""

        return self.forced_subtitle_stream is not None

    def show(self):
        """Displays the file stream information"""

        print("Stream Info:")
        print("video stream: index={}, codec={}".format(
            self.video_stream.index, self.video_stream.codec))
        print("audio stream: index={}, codec={}, channels={}".format(
            self.audio_stream.index, self.audio_stream.codec, self.audio_stream.channel_count))
        print("subtitle stream: index={}, codec={}".format(
            self.subtitle_stream.index, self.subtitle_stream.codec))
        print("forced subtitle stream: index={}, codec={}".format(
            self.forced_subtitle_stream.index, self.forced_subtitle_stream.codec))


    class StreamInfo:

        """Gets information about an individual stream within a file"""

        def __init__(self, stream_metadata):
            super().__init__()
            self.index = stream_metadata["index"]
            self.codec = stream_metadata["codec_name"]
            self.codec_type = stream_metadata["codec_type"]

            self.is_default = ("disposition" in stream_metadata
                              and "default" in stream_metadata["disposition"]
                              and stream_metadata["disposition"]["default"] == 1)

            self.is_forced = ("disposition" in stream_metadata
                             and "forced" in stream_metadata["disposition"]
                             and stream_metadata["disposition"]["forced"] == 1)

            self.channel_count = (stream_metadata["channels"]
                                 if "channels" in stream_metadata
                                 else -1)

            self.language = (stream_metadata["tags"]["language"]
                            if "tags" in stream_metadata and "language" in stream_metadata["tags"]
                            else "")

        @property
        def is_video(self):
            """Gets a value indicating whether this stream is a video stream"""

            return self.codec_type == "video"

        @property
        def is_audio(self):
            """Gets a value indicating whether this stream is an audio stream"""

            return self.codec_type == "audio"

        @property
        def is_subtitle(self):
            """Gets a value indicating whether this stream is a subtitle stream"""

            return self.codec_type == "subtitle"


class FileMapper:

    """Maps file names to a Plex-friendly format"""

    def __init__(self,
                 episode_db,
                 file_name_match_regex=r"(.*)s([0-9]+)e([0-9]+)(.*)(\.mkv|\.mp4)"):
        super().__init__()
        self.file_name_match_regex = file_name_match_regex
        self.episode_db = episode_db

    def find_keyword_match(self, partial_file_name):
        """Attempts to find a keyword match based on the partial file name"""

        for tracked_series in self.episode_db.get_all_tracked_series():
            for keyword in tracked_series.keywords:
                if keyword.lower() in partial_file_name.lower():
                    return keyword

        return None

    def map_files(self, source, destination, keyword=None):
        """Maps a file given a source and destination, handling individual files and directories"""

        file_map = []
        if os.path.isdir(source):
            src_dir = os.path.dirname(source)
            dest_dir = destination
            if not os.path.isdir(destination):
                dest_dir = os.path.dirname(destination)
            file_list = os.listdir(src_dir)
            file_list.sort()
            for input_file in file_list:
                match = re.match(self.file_name_match_regex, input_file, re.IGNORECASE)
                if match is not None:
                    if keyword is None:
                        keyword = self.find_keyword_match(match.group(1))
                    series_metadata = self.episode_db.get_tracked_series_by_keyword(keyword)
                    episode_metadata = series_metadata.get_episode(
                        int(match.group(2)), int(match.group(3)))
                    if episode_metadata is not None:
                        converted_file_name = "{}.mp4".format(episode_metadata.plex_title)
                        file_map.append((os.path.join(src_dir, input_file),
                                         os.path.join(dest_dir, converted_file_name)))
        else:
            if os.path.isdir(destination):
                source_file_base, _ = os.path.splitext(os.path.basename(source))
                file_map.append((source, os.path.join(destination, source_file_base + ".mp4")))
            else:
                file_map.append((source, destination))

        return file_map
