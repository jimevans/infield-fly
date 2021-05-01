"""Module for converting files into correct format"""

import json
import os
import platform
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
        is_convert_audio = (convert_audio and
                            self.file_stream_info.has_audio_stream and
                            self.file_stream_info.audio_stream_codec != "aac")

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
                self.file_stream_info.forced_subtitle_stream_index))
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
        ffmpeg_args.append("0:{}".format(self.file_stream_info.video_stream_index))
        ffmpeg_args.append("-c:v")
        if is_convert_video:
            ffmpeg_args.append("libx264")
            ffmpeg_args.append("-vf")
            ffmpeg_args.append("scale=-1:1024")
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
        ffmpeg_args.append("0:{}".format(self.file_stream_info.audio_stream_index))
        ffmpeg_args.append("-metadata:s:a:0")
        ffmpeg_args.append("language=eng")
        ffmpeg_args.append("-disposition:a:0")
        ffmpeg_args.append("default")
        ffmpeg_args.append("-c:a:0")
        if is_convert_audio:
            ffmpeg_args.append("aac")
            ffmpeg_args.append("-b:a:0")
            ffmpeg_args.append("160k")
            ffmpeg_args.append("-ac:a:0")
            ffmpeg_args.append("2")
            ffmpeg_args.append("-map")
            ffmpeg_args.append("0:{}".format(self.file_stream_info.audio_stream_index))
            ffmpeg_args.append("-metadata:s:a:1")
            ffmpeg_args.append("language=eng")
            ffmpeg_args.append("-disposition:a:1")
            ffmpeg_args.append("0")
            ffmpeg_args.append("-c:a:1")
            if (self.file_stream_info.audio_stream_codec == "ac3"
                    or self.file_stream_info.audio_stream_codec == "eac3"):
                ffmpeg_args.append("copy")
            else:
                ffmpeg_args.append("ac3")
                ffmpeg_args.append("-b:a:1")
                ffmpeg_args.append("640k")
                ffmpeg_args.append("-ac:a:1")
                ffmpeg_args.append("{}".format(self.file_stream_info.audio_stream_channel_count))
        else:
            ffmpeg_args.append("copy")

        return ffmpeg_args

    def get_subtitle_conversion_args(self, is_convert_subtitles):
        """Gets ffmpeg command line arguments for subtitle streams in the file"""

        ffmpeg_args = []
        if is_convert_subtitles:
            ffmpeg_args.append("-map")
            ffmpeg_args.append("0:{}".format(self.file_stream_info.subtitle_stream_index))
            ffmpeg_args.append("-c:s")
            ffmpeg_args.append("mov_text")
            ffmpeg_args.append("-metadata:s:s:0")
            ffmpeg_args.append("language=eng")
            ffmpeg_args.append("-disposition:s:0")
            ffmpeg_args.append("default")

        return ffmpeg_args

class FileStreamInfo:

    """Represents the file stream information of a media file"""

    UNDEFINED_STREAM_INDEX = -1
    UNDEFINED_STREAM_CODEC = ""
    UNDEFINED_CHANNEL_COUNT = -1

    def __init__(self,
                 video_stream_index = UNDEFINED_STREAM_INDEX,
                 video_stream_codec = UNDEFINED_STREAM_CODEC,
                 audio_stream_index = UNDEFINED_STREAM_INDEX,
                 audio_stream_codec = UNDEFINED_STREAM_CODEC,
                 audio_stream_channel_count = UNDEFINED_CHANNEL_COUNT,
                 subtitle_stream_index = UNDEFINED_STREAM_INDEX,
                 subtitle_stream_codec = UNDEFINED_STREAM_CODEC,
                 forced_subtitle_stream_index = UNDEFINED_STREAM_INDEX,
                 forced_subtitle_stream_codec = UNDEFINED_STREAM_CODEC):
        super().__init__()
        self.video_stream_index = video_stream_index
        self.video_stream_codec = video_stream_codec
        self.audio_stream_index = audio_stream_index
        self.audio_stream_codec = audio_stream_codec
        self.audio_stream_channel_count = audio_stream_channel_count
        self.subtitle_stream_index = subtitle_stream_index
        self.subtitle_stream_codec = subtitle_stream_codec
        self.forced_subtitle_stream_index = forced_subtitle_stream_index
        self.forced_subtitle_stream_codec = forced_subtitle_stream_codec

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

        metadata = FileStreamInfo._probe_file(input_file, ffprobe_location)
        video_stream_index = FileStreamInfo.UNDEFINED_STREAM_INDEX
        video_stream_codec = FileStreamInfo.UNDEFINED_STREAM_CODEC
        audio_stream_index = FileStreamInfo.UNDEFINED_STREAM_INDEX
        audio_stream_codec = FileStreamInfo.UNDEFINED_STREAM_CODEC
        audio_stream_channel_count = FileStreamInfo.UNDEFINED_CHANNEL_COUNT
        subtitle_stream_index = FileStreamInfo.UNDEFINED_STREAM_INDEX
        subtitle_stream_codec = FileStreamInfo.UNDEFINED_STREAM_CODEC
        forced_subtitle_stream_index = FileStreamInfo.UNDEFINED_STREAM_INDEX
        forced_subtitle_stream_codec = FileStreamInfo.UNDEFINED_STREAM_CODEC
        for stream_metadata in metadata["streams"]:
            stream = FileStreamInfo.StreamInfo(stream_metadata)
            if stream.is_video and video_stream_index == FileStreamInfo.UNDEFINED_STREAM_INDEX:
                video_stream_index = stream.index
                video_stream_codec = stream.codec_name

            if stream.is_audio:
                if (stream.is_default
                        or (stream.language == "eng"
                            and audio_stream_index == FileStreamInfo.UNDEFINED_CHANNEL_COUNT)):
                    audio_stream_index = stream.index
                    audio_stream_codec = stream.codec_name
                    audio_stream_channel_count = stream.channel_count

            if stream.is_subtitle and stream.codec_name == "subrip" and stream.language == "eng":
                if stream.is_forced:
                    if forced_subtitle_stream_index == FileStreamInfo.UNDEFINED_STREAM_INDEX:
                        forced_subtitle_stream_index = stream.index
                        forced_subtitle_stream_codec = stream.codec_name
                elif subtitle_stream_index == FileStreamInfo.UNDEFINED_STREAM_INDEX:
                    subtitle_stream_index = stream.index
                    subtitle_stream_codec = stream.codec_name

        return cls(video_stream_index = video_stream_index,
                   video_stream_codec = video_stream_codec,
                   audio_stream_index = audio_stream_index,
                   audio_stream_codec = audio_stream_codec,
                   audio_stream_channel_count = audio_stream_channel_count,
                   subtitle_stream_index = subtitle_stream_index,
                   subtitle_stream_codec = subtitle_stream_codec,
                   forced_subtitle_stream_index = forced_subtitle_stream_index,
                   forced_subtitle_stream_codec = forced_subtitle_stream_codec)

    @property
    def has_video_stream(self):
        """Gets a value indicating whether the file has a video stream"""

        return self.video_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX

    @property
    def has_audio_stream(self):
        """Gets a value indicating whether the file has an audio stream"""

        return self.audio_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX

    @property
    def has_subtitle_stream(self):
        """Gets a value indicating whether the file has a subtitle stream"""

        return self.subtitle_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX

    @property
    def has_forced_subtitle_stream(self):
        """Gets a value indicating whether the file has a forced subtitle stream"""

        return self.forced_subtitle_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX

    def show(self):
        """Displays the file stream information"""

        print("Stream Info:")
        print("video stream: index={}, codec={}".format(
            self.video_stream_index, self.video_stream_codec))
        print("audio stream: index={}, codec={}, channels={}".format(
            self.audio_stream_index, self.audio_stream_codec, self.audio_stream_channel_count))
        print("subtitle stream: index={}, codec={}".format(
            self.subtitle_stream_index, self.subtitle_stream_codec))
        print("forced subtitle stream: index={}, codec={}".format(
            self.forced_subtitle_stream_index, self.forced_subtitle_stream_codec))


    class StreamInfo:

        """Gets information about an individual stream within a file"""

        def __init__(self, stream_metadata):
            super().__init__()
            self.index = stream_metadata["index"]
            self.codec_type = stream_metadata["codec_type"]
            self.codec_name = stream_metadata["codec_name"]

            self.is_forced = False
            self.is_default = False
            if "disposition" in stream_metadata:
                disposition = stream_metadata["disposition"]
                if "default" in disposition and disposition["default"] == 1:
                    self.is_default = True
                if "forced" in disposition and disposition["forced"] == 1:
                    self.is_forced = True

            self.channel_count = FileStreamInfo.UNDEFINED_CHANNEL_COUNT
            if "channels" in stream_metadata:
                self.channel_count = stream_metadata["channels"]

            self.language = ""
            if "tags" in stream_metadata and "language" in stream_metadata["tags"]:
                self.language = stream_metadata["tags"]["language"]

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
