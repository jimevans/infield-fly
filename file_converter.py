import os
import platform
import subprocess

from file_stream_info import FileStreamInfo

class Converter:

    def __init__(self, ffmpeg_location = None):
        super().__init__()
        self.ffmpeg_location = ffmpeg_location

    def _get_ffmpeg_tool_location(self, tool_name = "ffmpeg"):
        tool_location = tool_name
        if self.ffmpeg_location is not None:
            tool_location = os.path.join(self.ffmpeg_location, tool_name)
        if platform.system() == "Windows":
            tool_location += ".exe"
        return tool_location

    def convert_file(self, input_file, output_file, dry_run = False,
                     convert_video = False, convert_audio = True,
                     convert_subtitles = True):
        file_stream_info = FileStreamInfo.read_stream_info(
            input_file, self._get_ffmpeg_tool_location("ffprobe"))

        is_convert_subtitles = (convert_subtitles and 
                                file_stream_info.has_subtitle_stream)
        is_convert_audio = (convert_audio and 
                            file_stream_info.has_audio_stream and
                            file_stream_info.audio_stream_codec != "aac")

        ffmpeg_args = []
        ffmpeg_args.append(self._get_ffmpeg_tool_location("ffmpeg"))
        ffmpeg_args.append("-hide_banner")
        ffmpeg_args.append("-i")
        ffmpeg_args.append(input_file)
        ffmpeg_args.append("-map")
        ffmpeg_args.append("0:{}".format(file_stream_info.video_stream_index))
        ffmpeg_args.append("-map")
        ffmpeg_args.append("0:{}".format(file_stream_info.audio_stream_index))
        if is_convert_audio:
            ffmpeg_args.append("-map")
            ffmpeg_args.append("0:{}".format(file_stream_info.audio_stream_index))
        if is_convert_subtitles:
            ffmpeg_args.append("-map")
            ffmpeg_args.append("0:{}".format(file_stream_info.subtitle_stream_index))

        if convert_video:
            ffmpeg_args.append("-c:v")
            ffmpeg_args.append("libx264")
            ffmpeg_args.append("-vf")
            ffmpeg_args.append("scale=-1:1024")
            ffmpeg_args.append("-crf")
            ffmpeg_args.append("17")
            ffmpeg_args.append("-preset")
            ffmpeg_args.append("medium")
        else:
            ffmpeg_args.append("-c:v")
            ffmpeg_args.append("copy")

        if is_convert_subtitles:
            ffmpeg_args.append("-c:s")
            ffmpeg_args.append("mov_text")

        ffmpeg_args.append("-c:a:0")
        if not is_convert_audio:
            ffmpeg_args.append("copy")
        else:
            ffmpeg_args.append("aac")
            ffmpeg_args.append("-b:a:0")
            ffmpeg_args.append("160k")
            ffmpeg_args.append("-ac:a:0")
            ffmpeg_args.append("2")
            if file_stream_info.audio_stream_codec == "ac3" or file_stream_info.audio_stream_codec == "eac3":
                ffmpeg_args.append("-c:a:1")
                ffmpeg_args.append("copy")
            else:
                ffmpeg_args.append("-c:a:1")
                ffmpeg_args.append("ac3")
                ffmpeg_args.append("-b:a:1")
                ffmpeg_args.append("640k")
                ffmpeg_args.append("-ac:a:1")
                ffmpeg_args.append("{}".format(file_stream_info.audio_stream_channel_count))

        ffmpeg_args.append("-map_metadata")
        ffmpeg_args.append("-1")
        ffmpeg_args.append("-map_chapters")
        ffmpeg_args.append("0")
        ffmpeg_args.append("-metadata:s:a:0")
        ffmpeg_args.append("language=eng")
        if is_convert_audio:
            ffmpeg_args.append("-metadata:s:a:1")
            ffmpeg_args.append("language=eng")
        if is_convert_subtitles:
            ffmpeg_args.append("-metadata:s:s:0")
            ffmpeg_args.append("language=eng")

        ffmpeg_args.append("-disposition:a:0")
        ffmpeg_args.append("default")
        if is_convert_audio:
            ffmpeg_args.append("-disposition:a:1")
            ffmpeg_args.append("0")
        if is_convert_subtitles:
            ffmpeg_args.append("-disposition:s:0")
            ffmpeg_args.append("default")

        ffmpeg_args.append(output_file)
        if dry_run:
            print("Conversion arguments:")
            print(ffmpeg_args)
        else:
            p = subprocess.run(ffmpeg_args)

        if file_stream_info.has_forced_subtitle_stream:
            base_name, ext = os.path.splitext(output_file)
            forced_subs_file = "{}.eng.forced.srt".format(base_name)
            forced_subs_args = []
            forced_subs_args.append(self._get_ffmpeg_tool_location("ffmpeg"))
            forced_subs_args.append("-hide_banner")
            forced_subs_args.append("-i")
            forced_subs_args.append(input_file)
            forced_subs_args.append("-map")
            forced_subs_args.append("0:{}".format(file_stream_info.forced_subtitle_stream_index))
            forced_subs_args.append(forced_subs_file)
            if dry_run:
                print("Forced subtitle conversion arguments:")
                print(forced_subs_args)
            else:
                p = subprocess.run(forced_subs_args)

