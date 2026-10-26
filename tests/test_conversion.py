"""Tests for the conversion module"""

import json
import os
from types import SimpleNamespace
from unittest import mock

import pytest

from infield_fly.conversion import ConversionSettings, Converter, FileMapper, FileStreamInfo
from infield_fly.database import EpisodeInfo, SeriesInfo


def stream(index, codec_type, codec, language=None, default=False, forced=False, **extra):
    """Creates ffprobe-style metadata for a single stream"""

    metadata = {"index": index, "codec_type": codec_type, "codec_name": codec,
                "disposition": {"default": int(default), "forced": int(forced)}, **extra}
    if language is not None:
        metadata["tags"] = {"language": language}
    return metadata


def stream_info(*streams):
    return [FileStreamInfo.StreamInfo(x) for x in streams]


VIDEO = stream(0, "video", "h264", height=1080, width=1920)
AUDIO = stream(1, "audio", "aac", "eng", default=True, channels=2)
SUBTITLE = stream(2, "subtitle", "subrip", "eng")
FORCED_SUBTITLE = stream(3, "subtitle", "subrip", "eng", forced=True)


@pytest.fixture
def mock_run():
    with mock.patch("infield_fly.conversion.subprocess.run") as run:
        yield run


def make_converter(*streams, ffmpeg_location=None, is_unattended_mode=False):
    """Creates a converter for a file with the specified streams, without running ffprobe"""

    with mock.patch.object(FileStreamInfo, "_probe_file", return_value={"streams": streams}):
        return Converter("in.mkv", "out.mp4", ffmpeg_location, is_unattended_mode)


class TestConverterToolLocation:

    def test_tool_name_on_path(self):
        with mock.patch("infield_fly.conversion.platform.system", return_value="Darwin"):
            converter = make_converter()
            assert converter._get_ffmpeg_tool_location() == "ffmpeg"

    def test_tool_in_ffmpeg_location(self):
        with mock.patch("infield_fly.conversion.platform.system", return_value="Linux"):
            converter = make_converter(ffmpeg_location="/opt/ffmpeg")
            assert converter._get_ffmpeg_tool_location("ffprobe") == os.path.join(
                "/opt/ffmpeg", "ffprobe")

    def test_windows_adds_exe_extension(self):
        with mock.patch("infield_fly.conversion.platform.system", return_value="Windows"):
            converter = make_converter()
            assert converter._get_ffmpeg_tool_location("ffprobe") == "ffprobe.exe"

    def test_constructor_probes_with_ffprobe(self):
        with mock.patch("infield_fly.conversion.platform.system", return_value="Linux"), \
                mock.patch.object(FileStreamInfo, "_probe_file",
                                  return_value={"streams": []}) as probe:
            Converter("in.mkv", "out.mp4", "/opt/ffmpeg")

        probe.assert_called_once_with("in.mkv", os.path.join("/opt/ffmpeg", "ffprobe"))


class TestConvertFile:

    def test_default_settings(self, mock_run):
        converter = make_converter(VIDEO, AUDIO, SUBTITLE)

        converter.convert_file()

        args = mock_run.call_args[0][0]
        assert args[:9] == ["ffmpeg", "-hide_banner", "-i", "in.mkv", "-map_metadata", "-1",
                            "-map_chapters", "0", "-map"]
        assert args[-1] == "out.mp4"
        assert "-loglevel" not in args
        assert ["-c:v", "copy"] == args[args.index("-c:v"):args.index("-c:v") + 2]
        assert "-c:a:1" in args
        assert "-c:s" in args
        mock_run.assert_called_once_with(args, check=True)

    def test_unattended_mode_reduces_output(self, mock_run):
        converter = make_converter(VIDEO, is_unattended_mode=True)

        converter.convert_file()

        args = mock_run.call_args[0][0]
        assert args[2:5] == ["-loglevel", "warning", "-nostats"]

    def test_settings_disable_audio_and_subtitle_conversion(self, mock_run):
        converter = make_converter(VIDEO, AUDIO, SUBTITLE)

        converter.convert_file(ConversionSettings(convert_audio=False, convert_subtitles=False))

        args = mock_run.call_args[0][0]
        assert "-c:a:1" not in args
        assert "-c:s" not in args

    def test_passes_video_settings(self, mock_run):
        converter = make_converter(VIDEO)

        converter.convert_file(ConversionSettings(convert_video=True, aspect_ratio=2.4, crf=20))

        args = mock_run.call_args[0][0]
        assert "libx264" in args
        assert args[args.index("-crf") + 1] == "20"
        assert "crop=" in args[args.index("-vf") + 1]

    def test_dry_run_does_not_execute(self, mock_run, caplog):
        caplog.set_level("INFO")
        converter = make_converter(VIDEO, AUDIO, SUBTITLE, FORCED_SUBTITLE)

        converter.convert_file(dry_run=True)

        mock_run.assert_not_called()
        assert "Conversion arguments" in caplog.text
        assert "Forced subtitle conversion arguments" in caplog.text

    def test_converts_forced_subtitles(self, mock_run):
        converter = make_converter(VIDEO, AUDIO, SUBTITLE, FORCED_SUBTITLE)

        converter.convert_file()

        assert mock_run.call_count == 2
        mock_run.assert_called_with(
            ["ffmpeg", "-hide_banner", "-i", "in.mkv", "-map", "0:3", "out.eng.forced.srt"],
            check=True)

    def test_skips_forced_subtitles_when_not_converting_subtitles(self, mock_run):
        converter = make_converter(VIDEO, AUDIO, SUBTITLE, FORCED_SUBTITLE)

        converter.convert_file(ConversionSettings(convert_subtitles=False))

        mock_run.assert_called_once()

    def test_skips_forced_subtitles_without_subtitle_stream(self, mock_run):
        converter = make_converter(VIDEO, AUDIO, FORCED_SUBTITLE)

        converter.convert_file()

        mock_run.assert_called_once()


class TestConvertForcedSubtitles:

    def test_does_nothing_without_forced_stream(self, mock_run):
        converter = make_converter(VIDEO, SUBTITLE)

        converter.convert_forced_subtitles()

        mock_run.assert_not_called()


class TestVideoConversionArgs:

    def test_no_video_stream(self):
        assert make_converter(AUDIO).get_video_conversion_args(True) == []

    def test_copy(self):
        args = make_converter(VIDEO).get_video_conversion_args(False)

        assert args == ["-map", "0:0", "-c:v", "copy"]

    def test_copy_hevc_adds_tag(self):
        hevc = stream(0, "video", "hevc", height=2160)

        args = make_converter(hevc).get_video_conversion_args(False)

        assert args == ["-map", "0:0", "-c:v", "copy", "-tag:v", "hvc1"]

    def test_convert_without_filters(self):
        args = make_converter(VIDEO).get_video_conversion_args(True)

        assert args == ["-map", "0:0", "-c:v", "libx264", "-crf", "17", "-preset", "medium"]

    def test_convert_scales_down_above_1080p(self):
        uhd = stream(0, "video", "h264", height=2160)

        args = make_converter(uhd).get_video_conversion_args(True, crf=18.5)

        assert args == ["-map", "0:0", "-c:v", "libx264", "-vf", "scale=-2:1080",
                        "-crf", "18.5", "-preset", "medium"]

    def test_convert_with_aspect_ratio_and_scaling(self):
        uhd = stream(0, "video", "hevc", height=2160)

        args = make_converter(uhd).get_video_conversion_args(True, aspect_ratio=2.4)

        filters = args[args.index("-vf") + 1]
        assert filters == (
            "scale=-2:1080,"
            "crop=w='if(gt(iw/ih,2.4+0.01),trunc(ih*2.4/2)*2,iw)'"
            ":h='if(lt(iw/ih,2.4-0.01),trunc(iw/2.4/2)*2,ih)'")
        assert args[-2:] == ["-tag:v", "hvc1"]


class TestAudioConversionArgs:

    def test_no_audio_stream(self):
        assert make_converter(VIDEO).get_audio_conversion_args(True) == []

    def test_copy_without_conversion(self):
        args = make_converter(AUDIO).get_audio_conversion_args(False)

        assert args == ["-map", "0:1", "-metadata:s:a:0", "language=eng",
                        "-disposition:a:0", "default", "-c:a:0", "copy"]

    def test_missing_language_assumed_english(self):
        audio = stream(1, "audio", "aac", channels=2)

        args = make_converter(audio).get_audio_conversion_args(False)

        assert "language=eng" in args

    def test_stereo_aac_copied_and_ac3_created(self):
        args = make_converter(AUDIO).get_audio_conversion_args(True)

        assert args == ["-map", "0:1", "-metadata:s:a:0", "language=eng",
                        "-disposition:a:0", "default", "-c:a:0", "copy",
                        "-map", "0:1", "-metadata:s:a:1", "language=eng",
                        "-disposition:a:1", "0", "-c:a:1", "ac3",
                        "-b:a:1", "640k", "-ac:a:1", "2"]

    @pytest.mark.parametrize("codec", ["ac3", "eac3"])
    def test_surround_ac3_converted_to_aac_and_copied(self, codec):
        audio = stream(1, "audio", codec, "fre", channels=6)

        args = make_converter(audio).get_audio_conversion_args(True)

        assert args == ["-map", "0:1", "-metadata:s:a:0", "language=fre",
                        "-disposition:a:0", "default", "-c:a:0", "aac",
                        "-b:a:0", "160k", "-ac:a:0", "2",
                        "-map", "0:1", "-metadata:s:a:1", "language=fre",
                        "-disposition:a:1", "0", "-c:a:1", "copy"]

    def test_surround_aac_converted(self):
        audio = stream(1, "audio", "aac", "eng", channels=8)

        args = make_converter(audio).get_audio_conversion_args(True)

        assert args[args.index("-c:a:0") + 1] == "aac"
        assert args[args.index("-ac:a:0") + 1] == "2"
        assert args[args.index("-ac:a:1") + 1] == "6"

    def test_mono_audio_channel_counts(self):
        audio = stream(1, "audio", "dts", "eng", channels=1)

        args = make_converter(audio).get_audio_conversion_args(True)

        assert args[args.index("-ac:a:0") + 1] == "1"
        assert args[args.index("-ac:a:1") + 1] == "1"


class TestSubtitleConversionArgs:

    def test_not_converting(self):
        assert make_converter(SUBTITLE).get_subtitle_conversion_args(False) == []

    def test_converts_to_mov_text(self):
        args = make_converter(SUBTITLE).get_subtitle_conversion_args(True)

        assert args == ["-map", "0:2", "-metadata:s:s:0", "language=eng",
                        "-disposition:s:0", "default", "-c:s", "mov_text"]

    def test_copies_mov_text(self):
        subtitle = stream(2, "subtitle", "mov_text", "eng")

        args = make_converter(subtitle).get_subtitle_conversion_args(True)

        assert args[-2:] == ["-c:s", "copy"]


class TestFileStreamInfo:

    def test_probe_file_runs_ffprobe(self, mock_run):
        mock_run.return_value.stdout = json.dumps({"streams": []}).encode()

        result = FileStreamInfo._probe_file("in.mkv", "/bin/ffprobe")

        assert result == {"streams": []}
        mock_run.assert_called_once_with(
            ["/bin/ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
             "-show_streams", "in.mkv"],
            check=True, stdout=mock.ANY, stderr=mock.ANY)

    def test_read_stream_info_selects_streams(self):
        streams = [
            VIDEO,
            stream(1, "video", "mjpeg"),
            stream(2, "audio", "aac", "eng", channels=2),
            stream(3, "subtitle", "hdmv_pgs_subtitle", "eng"),
            stream(4, "subtitle", "subrip", "fre"),
            stream(5, "subtitle", "ass", ""),
            stream(6, "subtitle", "subrip", "eng"),
            stream(7, "subtitle", "mov_text", "eng", forced=True),
            stream(8, "subtitle", "subrip", "eng", forced=True),
        ]
        with mock.patch.object(FileStreamInfo, "_probe_file",
                               return_value={"streams": streams}) as probe:
            info = FileStreamInfo.read_stream_info("in.mkv")

        probe.assert_called_once_with("in.mkv", "ffprobe")
        assert info.video_stream.index == 0
        assert info.audio_stream.index == 2
        assert info.subtitle_stream.index == 5
        assert info.forced_subtitle_stream.index == 7

    def test_read_stream_info_with_no_streams(self):
        with mock.patch.object(FileStreamInfo, "_probe_file", return_value={"streams": []}):
            info = FileStreamInfo.read_stream_info("in.mkv")

        assert not info.has_video_stream
        assert not info.has_audio_stream
        assert not info.has_subtitle_stream
        assert not info.has_forced_subtitle_stream

    @pytest.mark.parametrize("streams, expected_index", [
        # Default English stream wins over earlier English stream.
        ([stream(0, "audio", "aac", "eng"), stream(1, "audio", "aac", "eng", default=True)], 1),
        # Untagged stream counts as English.
        ([stream(0, "audio", "aac", "fre", default=True), stream(1, "audio", "aac")], 1),
        # First English stream when none is default.
        ([stream(0, "audio", "aac", "fre"), stream(1, "audio", "aac", "eng"),
          stream(2, "audio", "aac", "eng")], 1),
        # Default stream when there are no English streams.
        ([stream(0, "audio", "aac", "fre"), stream(1, "audio", "aac", "ger", default=True)], 1),
        # First stream otherwise.
        ([stream(0, "audio", "aac", "fre"), stream(1, "audio", "aac", "ger")], 0),
    ])
    def test_select_audio_stream(self, streams, expected_index):
        selected = FileStreamInfo._select_audio_stream(stream_info(*streams))

        assert selected.index == expected_index

    def test_select_audio_stream_with_no_streams(self):
        assert FileStreamInfo._select_audio_stream([]) is None


class TestStreamInfo:

    def test_properties(self):
        info = FileStreamInfo.StreamInfo(
            stream(4, "video", "h264", "eng", default=True, forced=True, height=720, width=1280))

        assert info.index == 4
        assert info.codec == "h264"
        assert info.codec_type == "video"
        assert info.is_default
        assert info.is_forced
        assert info.height == 720
        assert info.width == 1280
        assert info.channel_count == -1
        assert info.language == "eng"
        assert info.is_video
        assert not info.is_audio
        assert not info.is_subtitle

    def test_missing_optional_metadata(self):
        info = FileStreamInfo.StreamInfo({"index": 0, "codec_type": "audio", "codec_name": "aac"})

        assert not info.is_default
        assert not info.is_forced
        assert info.height == -1
        assert info.width == -1
        assert info.language == ""
        assert info.is_audio

    def test_disposition_without_flags(self):
        info = FileStreamInfo.StreamInfo(
            {"codec_type": "subtitle", "disposition": {}, "tags": {"title": "x"}})

        assert not info.is_default
        assert not info.is_forced
        assert info.language == ""
        assert info.is_subtitle


class FakeEpisodeDatabase:

    """Minimal stand-in for EpisodeDatabase used by FileMapper"""

    def __init__(self):
        self.series = SeriesInfo(1, "The Show", "Continuing", 2020)
        self.series.add_episode(EpisodeInfo(10, "The Show", season_number=1, episode_number=2,
                                            title="Pilot: Part 2"))
        self.tracked = [SimpleNamespace(keywords=["other"]),
                        SimpleNamespace(keywords=["the show", "show"])]

    def get_all_tracked_series(self):
        return self.tracked

    def get_tracked_series_by_keyword(self, keyword):
        return self.series if keyword in ("the show", "show") else None


class TestFileMapper:

    @pytest.fixture
    def mapper(self):
        return FileMapper(FakeEpisodeDatabase(), file_name_substitutions={":": " -"})

    def test_default_substitutions(self):
        assert FileMapper(FakeEpisodeDatabase()).file_name_substitutions == {}

    def test_find_keyword_match(self, mapper):
        assert mapper.find_keyword_match("The.SHOW.") == "show"
        assert mapper.find_keyword_match("unrelated") is None

    def test_get_mapped_file_name(self, mapper):
        assert (mapper.get_mapped_file_name("The.Show.S01E02.1080p.mkv")
                == "The Show - s01e02 - Pilot - Part 2.mp4")

    def test_get_mapped_file_name_with_keyword(self, mapper):
        assert (mapper.get_mapped_file_name("x.s01e02.avi", "the show")
                == "The Show - s01e02 - Pilot - Part 2.mp4")

    def test_get_mapped_file_name_no_regex_match(self, mapper):
        assert mapper.get_mapped_file_name("The.Show.S01E02.txt") is None

    def test_get_mapped_file_name_untracked_series(self, mapper, caplog):
        assert mapper.get_mapped_file_name("Unknown.S01E02.mkv") is None
        assert "No tracked series found" in caplog.text

    def test_get_mapped_file_name_unknown_episode(self, mapper):
        assert mapper.get_mapped_file_name("The.Show.S09E09.mkv") is None

    def test_map_directory_to_directory(self, mapper, tmp_path):
        src = tmp_path / "src"
        dest = tmp_path / "dest"
        src.mkdir()
        dest.mkdir()
        for name in ("The.Show.S01E02.mkv", "notes.txt", "The.Show.S09E09.mkv"):
            (src / name).touch()

        assert mapper.map_files(str(src), str(dest)) == [
            (str(src / "The.Show.S01E02.mkv"), str(dest / "The Show - s01e02 - Pilot - Part 2.mp4"))
        ]

    def test_map_directory_to_nonexistent_path_uses_parent(self, mapper, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "The.Show.S01E02.mkv").touch()

        file_map = mapper.map_files(str(src), str(tmp_path / "missing"))

        assert file_map[0][1] == str(tmp_path / "The Show - s01e02 - Pilot - Part 2.mp4")

    def test_map_file_to_directory(self, mapper, tmp_path):
        assert mapper.map_files("/media/input.mkv", str(tmp_path)) == [
            ("/media/input.mkv", str(tmp_path / "input.mp4"))
        ]

    def test_map_file_to_file(self, mapper, tmp_path):
        dest = str(tmp_path / "output.mp4")

        assert mapper.map_files("/media/input.mkv", dest) == [("/media/input.mkv", dest)]
