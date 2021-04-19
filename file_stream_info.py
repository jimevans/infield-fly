import json
import subprocess


class FileStreamInfo:

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

        p = subprocess.run(ffprobe_args,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        json_output = p.stdout
        return json.loads(json_output)


    @classmethod
    def read_stream_info(cls, input_file, ffprobe_location = "ffprobe"):
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
                if stream.is_default or (stream.language == "eng" and audio_stream_index == FileStreamInfo.UNDEFINED_CHANNEL_COUNT):
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
        return self.video_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX

    
    @property
    def has_audio_stream(self):
        return self.audio_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX


    @property
    def has_subtitle_stream(self):
        return self.subtitle_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX


    @property
    def has_forced_subtitle_stream(self):
        return self.forced_subtitle_stream_index != FileStreamInfo.UNDEFINED_STREAM_INDEX


    def show(self):
        print("Stream Info:")
        print("video stream: index={}, codec={}".format(self.video_stream_index, self.video_stream_codec))
        print("audio stream: index={}, codec={}, channels={}".format(self.audio_stream_index, self.audio_stream_codec, self.audio_stream_channel_count))
        print("subtitle stream: index={}, codec={}".format(self.subtitle_stream_index, self.subtitle_stream_codec))
        print("forced subtitle stream: index={}, codec={}".format(self.forced_subtitle_stream_index, self.forced_subtitle_stream_codec))


    class StreamInfo:

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
            return self.codec_type == "video"

        
        @property
        def is_audio(self):
            return self.codec_type == "audio"

        
        @property
        def is_subtitle(self):
            return self.codec_type == "subtitle"
