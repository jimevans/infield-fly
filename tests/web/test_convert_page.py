"""Tests for the convert page of the web interface"""

import html
import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
from web.conftest import ROOT, SAME_ORIGIN

from conftest import make_episode_db
from infield_fly.concurrency import ProcessLock
from infield_fly.database import EpisodeInfo
from infield_fly.web import convert_page, processing


def stream(index, codec_type, codec, language=None, forced=False, title=None, **extra):
    tags = {}
    if language is not None:
        tags["language"] = language
    if title is not None:
        tags["title"] = title
    return {"index": index, "codec_type": codec_type, "codec_name": codec,
            "disposition": {"default": int(index == 1), "forced": int(forced)},
            "tags": tags, **extra}


MOVIE_STREAMS = [
    stream(0, "video", "h264"),
    stream(1, "audio", "dts", "eng", channels=6),
    stream(2, "audio", "ac3", "eng", title="Commentary", channels=2),
    stream(3, "subtitle", "subrip", "eng"),
    stream(4, "subtitle", "subrip", "eng", forced=True),
]


@pytest.fixture
def downloads(app, tmp_path):
    """A download folder with a movie, and a folder of episodes, to convert"""

    root = tmp_path / "downloads"
    (root / "Show.S01").mkdir(parents=True)
    (root / "Movie.mkv").touch()
    (root / "Show.S01" / "the.show.s01e01.mkv").touch()
    (root / "Show.S01" / "notes.txt").touch()
    app.state.browse_roots = [os.path.realpath(root)]
    return root


@pytest.fixture
def movie(downloads):
    return os.path.realpath(downloads / "Movie.mkv")


@pytest.fixture
def probe():
    with mock.patch("infield_fly.conversion.FileStreamInfo._probe_file",
                    return_value={"streams": MOVIE_STREAMS,
                                  "format": {"duration": "7200"}}) as probe_file:
        yield probe_file


@pytest.fixture
def start_run():
    with mock.patch.object(processing, "start_run") as start:
        yield start


def form(source, **values):
    return {"source": str(source), "name": "Movie (1999)", "keyword": "",
            "convert_audio": "on", "convert_subtitles": "on", "action": "review", **values}


def post(client, data, **options):
    return client.post("/convert", data={name: value for name, value in data.items()
                                         if value is not None},
                       headers=SAME_ORIGIN, **options)


def final_path(config, name):
    return os.path.join(config.conversion.final_directory, name)


class TestConvertPage:

    def test_form(self, logged_in, config):
        text = logged_in.get("/convert").text

        assert f'<a href="{ROOT}/convert" aria-current="page">Convert</a>' in text
        assert config.conversion.final_directory in text
        assert '<option value="" selected>Match series by file name</option>' in text
        assert '<option value="show">The Show</option>' in text
        assert 'name="convert_audio" checked' in text
        assert 'name="convert_video">' in text
        assert 'data-busy-convert="false"' in text
        assert "<fieldset>" in text

    def test_chosen_source(self, logged_in, movie):
        text = logged_in.get("/convert", params={"source": movie}).text

        assert f'name="source" value="{movie}"' in text
        assert 'name="name" value="Movie"' in text

    def test_conversion_in_progress(self, logged_in, config):
        os.makedirs(config.conversion.log_directory)
        progress = processing.get_conversion_progress_file(config)
        with open(progress, "w", encoding="utf-8") as progress_file:
            json.dump({"file": 1, "files": 1, "source": "/d/Movie.mkv", "duration": 100},
                      progress_file)

        with ProcessLock(config.conversion.convert_lock_file, "convert"):
            text = logged_in.get("/convert").text

        assert '<span id="convert-progress">Movie.mkv: starting</span>' in text
        assert 'name="action" value="cancel"' in text
        assert text.count("<fieldset disabled>") == 2
        assert 'data-busy-convert="true"' in text

    def test_errors_of_last_conversion(self, logged_in, config):
        os.makedirs(config.conversion.log_directory)
        with open(processing.get_error_file(config, "convert"), "w",
                  encoding="utf-8") as errors:
            errors.write("Traceback\n")

        assert "Errors from the last conversion started here" in (
            logged_in.get("/convert").text)


class TestReviewFile:

    def test_track_mapping(self, logged_in, config, movie, probe):
        response = post(logged_in, form(movie))

        text = html.unescape(response.text)
        assert response.status_code == 200
        for source, destination in [
                ("Source track 0: video (h264)", "Destination track 0 (copy)"),
                ("Source track 1: audio (dts, 5.1, eng)",
                 "Destination track 1 (dts -> aac, stereo)"),
                ("Source track 1: audio (dts, 5.1, eng)",
                 "Destination track 2 (dts -> ac3, 5.1)"),
                ("Source track 3: subtitle (srt, eng)",
                 "Destination track 3 (srt -> mov_text)"),
                ("Source track 4: subtitle (srt, eng, forced)",
                 "Movie (1999).eng.forced.srt"),
                ("Source track 3: subtitle (srt, eng)", "Movie (1999).eng.srt")]:
            assert (f"<li><span>{source}</span> <span aria-hidden=\"true\">→</span> "
                    f"<span>{destination}</span></li>") in text
        assert "<li>Source track 2: audio (ac3, stereo, eng, “Commentary”)</li>" in text
        assert final_path(config, "Movie (1999).mp4") in text
        probe.assert_called_once_with(movie, "ffprobe")

    def test_ffmpeg_commands(self, logged_in, config, movie, probe):
        text = html.unescape(post(logged_in, form(movie)).text)

        commands = text.split('<pre class="log command">')[1:]
        assert len(commands) == 3
        assert commands[0].startswith(f"ffmpeg -hide_banner -i {movie} ")
        assert f"'{final_path(config, 'Movie (1999).mp4')}'</pre>" in commands[0]
        assert "-y" not in commands[0].split()
        assert f"'{final_path(config, 'Movie (1999).eng.forced.srt')}'" in commands[1]

    def test_re_encoded_video(self, logged_in, movie, probe):
        text = html.unescape(post(logged_in, form(
            movie, convert_video="on", crf="20", aspect_ratio="2.40")).text)

        assert "Destination track 0 (h264 -> h264, re-encoded)" in text
        assert "-crf 20" in text
        assert "crop=" in text

    def test_choices_are_kept(self, logged_in, movie, probe):
        text = post(logged_in, form(movie, convert_video="on", crf="20",
                                    convert_subtitles=None)).text

        for field in ['name="source" value="{movie}"', 'name="name" value="Movie (1999)"',
                      'name="crf" value="20"', 'name="convert_video" value="on"',
                      'name="convert_audio" value="on"']:
            assert field.format(movie=movie) in text
        assert 'name="convert_subtitles"' not in text
        assert "eng.srt" not in text

    def test_existing_output(self, logged_in, config, movie, probe):
        os.makedirs(config.conversion.final_directory)
        Path(final_path(config, "Movie (1999).mp4")).touch()

        text = post(logged_in, form(movie)).text

        assert "These converted files already exist:" in text
        assert 'name="overwrite">' in text

    def test_unreadable_source(self, logged_in, movie):
        with mock.patch("infield_fly.conversion.FileStreamInfo._probe_file",
                        side_effect=subprocess.CalledProcessError(1, ["ffprobe"])):
            response = post(logged_in, form(movie))

        assert response.status_code == 400
        assert f"Couldn&#39;t read &#39;{movie}&#39;" in response.text

    def test_change_returns_to_form(self, logged_in, movie):
        text = post(logged_in, form(movie, action="change", convert_video="on",
                                    crf="20")).text

        assert 'name="crf" value="20"' in text
        assert 'name="convert_video" checked' in text


class TestReviewFolder:

    @pytest.fixture
    def episodes(self, config):
        database = make_episode_db(config, [
            EpisodeInfo(11, "The Show", season_number=1, episode_number=1, title="Pilot",
                        airdate=datetime(2020, 1, 1))])
        database.save_to_cache()

    def test_episode_files(self, logged_in, config, downloads, episodes):
        folder = os.path.realpath(downloads / "Show.S01")

        text = html.unescape(post(logged_in, form(folder, keyword="show")).text)

        assert ("<li><span>the.show.s01e01.mkv</span> <span aria-hidden=\"true\">→</span> "
                "<span>The Show - s01e01 - Pilot.mp4</span></li>") in text
        assert "<li>notes.txt</li>" in text
        assert f"<dd>{config.conversion.final_directory}</dd>" in text
        assert "ffmpeg commands" not in text

    def test_no_episode_files(self, logged_in, downloads):
        folder = os.path.realpath(downloads / "Show.S01")

        text = post(logged_in, form(folder, keyword="show")).text

        assert "No files in the folder can be named from a tracked series." in text


class TestValidation:

    @pytest.mark.parametrize("values, message", [
        ({"source": ""}, "Choose the file or folder to convert."),
        ({"source": "/etc/hosts"}, "The source must be in one of the folders"),
        ({"source": "MISSING"}, "does not exist."),
        ({"name": ""}, "Enter a file name for the converted file, without folders."),
        ({"name": ".mp4"}, "Enter a file name"),
        ({"name": "a/b"}, "Enter a file name"),
        ({"name": ".hidden"}, "Enter a file name"),
        ({"convert_video": "on", "crf": "x"}, "The constant rate factor (CRF) must be a number."),
        ({"convert_video": "on", "crf": "60"},
         "The constant rate factor (CRF) must be between 0 and 51."),
        ({"convert_video": "on", "aspect_ratio": "wide"}, "The aspect ratio must be a number"),
        ({"convert_video": "on", "aspect_ratio": "-1"}, "must be a positive number"),
    ])
    def test_invalid(self, logged_in, downloads, movie, values, message):
        if values.get("source") == "MISSING":
            values["source"] = os.path.join(os.path.realpath(downloads), "missing.mkv")

        response = post(logged_in, {**form(movie), **values})

        assert response.status_code == 400
        assert html.escape(message, quote=True).replace("&#x27;", "&#39;") in response.text

    def test_unknown_series_for_folder(self, logged_in, downloads):
        response = post(logged_in, form(os.path.realpath(downloads / "Show.S01"),
                                        keyword="nope"))

        assert response.status_code == 400
        assert "Choose a tracked series, or to match series by file name." in response.text

    def test_video_options_ignored_without_re_encoding(self, logged_in, movie, probe):
        response = post(logged_in, form(movie, crf="x", aspect_ratio="wide"))

        assert response.status_code == 200


class TestStart:

    def test_starts_staged_conversion(self, logged_in, app, config, movie, probe, start_run):
        os.makedirs(config.conversion.log_directory)
        progress = processing.get_conversion_progress_file(config)
        for path in (progress, f"{progress}.ffmpeg"):
            Path(path).touch()

        response = post(logged_in, form(movie, action="start"), follow_redirects=False)

        assert response.headers["location"] == f"{ROOT}/convert"
        assert start_run.call_args[0][1].conversion.final_directory == (
            config.conversion.final_directory)
        assert start_run.call_args[0][2:] == (
            app.state.settings_file.path, "convert",
            ["convert", "--stage", "--progress-file", progress, movie,
             final_path(config, "Movie (1999).mp4")])
        assert not os.path.exists(progress)
        assert not os.path.exists(f"{progress}.ffmpeg")
        assert "Started converting Movie.mkv." in logged_in.get("/convert").text

    def test_options(self, logged_in, config, movie, probe, start_run):
        post(logged_in, form(movie, action="start", name="Movie.MP4", convert_video="on",
                             crf="20", aspect_ratio="2.4", convert_audio=None,
                             convert_subtitles=None, overwrite="on"))

        arguments = start_run.call_args[0][4]
        assert arguments[4:] == ["--overwrite", "--convert-video", "--crf", "20", "-a", "2.4",
                                 "--no-convert-audio", "--no-convert-subtitles", movie,
                                 final_path(config, "Movie.MP4")]

    def test_re_encoding_with_default_quality(self, logged_in, movie, probe, start_run):
        post(logged_in, form(movie, action="start", convert_video="on"))

        arguments = start_run.call_args[0][4]
        assert "--convert-video" in arguments
        assert "--crf" not in arguments
        assert "-a" not in arguments

    def test_folder(self, logged_in, config, downloads, start_run):
        database = make_episode_db(config, [
            EpisodeInfo(11, "The Show", season_number=1, episode_number=1, title="Pilot",
                        airdate=datetime(2020, 1, 1))])
        database.save_to_cache()
        folder = os.path.realpath(downloads / "Show.S01")

        post(logged_in, form(folder, action="start", keyword="show"))

        assert start_run.call_args[0][4][4:] == ["-k", "show", folder,
                                                 config.conversion.final_directory]

    def test_folder_without_episode_files(self, logged_in, downloads, start_run):
        response = post(logged_in, form(os.path.realpath(downloads / "Show.S01"),
                                        action="start"))

        assert response.status_code == 400
        start_run.assert_not_called()

    def test_refuses_to_replace_existing_file(self, logged_in, config, movie, probe,
                                              start_run):
        os.makedirs(config.conversion.final_directory)
        Path(final_path(config, "Movie (1999).mp4")).touch()

        response = post(logged_in, form(movie, action="start"))

        assert response.status_code == 409
        assert "Converted files already exist." in response.text
        start_run.assert_not_called()

        post(logged_in, form(movie, action="start", overwrite="on"))
        assert "--overwrite" in start_run.call_args[0][4]

    def test_not_while_converting(self, logged_in, config, movie, start_run):
        with ProcessLock(config.conversion.convert_lock_file, "convert"):
            text = post(logged_in, form(movie, action="start")).text

        start_run.assert_not_called()
        assert "A conversion is already running" in text

    def test_invalid_form_is_not_started(self, logged_in, movie, start_run):
        response = post(logged_in, form(movie, action="start", name=""))

        assert response.status_code == 400
        start_run.assert_not_called()


class TestCancel:

    @pytest.fixture
    def signals(self):
        with mock.patch("os.getpgid") as getpgid, mock.patch("os.killpg") as killpg, \
                mock.patch("os.kill") as kill:
            yield getpgid, killpg, kill

    def cancel(self, client):
        return client.post("/convert", data={"action": "cancel"}, headers=SAME_ORIGIN).text

    def test_stops_process_group_of_conversion_started_here(self, logged_in, config,
                                                            signals):
        getpgid, killpg, kill = signals
        getpgid.side_effect = lambda pid: pid

        with ProcessLock(config.conversion.convert_lock_file, "convert"):
            text = self.cancel(logged_in)

        killpg.assert_called_once_with(os.getpid(), signal.SIGTERM)
        # Signal 0 only checks that the lock holder is running.
        assert all(x[0][1] == 0 for x in kill.call_args_list)
        assert "Cancelling the conversion." in text

    def test_stops_conversion_started_elsewhere(self, logged_in, config, signals):
        getpgid, killpg, kill = signals
        getpgid.return_value = 1

        with ProcessLock(config.conversion.convert_lock_file, "convert"):
            self.cancel(logged_in)

        assert mock.call(os.getpid(), signal.SIGTERM) in kill.call_args_list
        killpg.assert_not_called()

    def test_stops_starting_conversion(self, logged_in, app, signals):
        getpgid, killpg, _ = signals
        getpgid.side_effect = lambda pid: pid
        process = mock.Mock(pid=4321)
        process.poll.return_value = None
        app.state.started_runs = {"convert": {"process": process, "pid": 4321,
                                              "time": time.monotonic()}}

        self.cancel(logged_in)

        killpg.assert_called_once_with(4321, signal.SIGTERM)

    def test_conversion_already_finished(self, logged_in, config, signals):
        getpgid, killpg, _ = signals
        getpgid.side_effect = ProcessLookupError

        with ProcessLock(config.conversion.convert_lock_file, "convert"):
            text = self.cancel(logged_in)

        killpg.assert_not_called()
        assert "Cancelling the conversion." in text

    def test_nothing_to_cancel(self, logged_in, signals):
        assert "No conversion is running." in self.cancel(logged_in)


class TestBrowse:

    def test_chooses_source_for_convert_page(self, logged_in, downloads):
        root = os.path.realpath(downloads)

        text = logged_in.get("/convert/browse", params={"path": root}).text

        assert "Choose what to convert" in text
        assert f'href="{ROOT}/convert">← Convert</a>' in text
        assert f"{ROOT}/convert?source=" in text
        assert f"{ROOT}/convert/browse?path=" in text

    def test_lists_browse_roots(self, logged_in, downloads):
        assert f"{ROOT}/convert/browse?path=" in logged_in.get("/convert/browse").text


class TestProgress:

    @pytest.fixture
    def progress(self, config):
        os.makedirs(config.conversion.log_directory)
        path = processing.get_conversion_progress_file(config)

        def write(progress=None, ffmpeg=None):
            if progress is not None:
                with open(path, "w", encoding="utf-8") as progress_file:
                    progress_file.write(progress if isinstance(progress, str)
                                        else json.dumps(progress))
            if ffmpeg is not None:
                with open(f"{path}.ffmpeg", "w", encoding="utf-8") as ffmpeg_file:
                    ffmpeg_file.write(ffmpeg)
        return write

    MOVIE = {"file": 1, "files": 1, "source": "/d/Movie.mkv", "duration": 7200}

    def test_no_progress_yet(self, config):
        assert processing.describe_conversion_progress(config) is None

    def test_invalid_progress(self, config, progress):
        progress("not json")

        assert processing.describe_conversion_progress(config) is None

    def test_starting(self, config, progress):
        progress(self.MOVIE)

        assert processing.describe_conversion_progress(config) == "Movie.mkv: starting"

    def test_percent_and_time_left(self, config, progress):
        progress(self.MOVIE, "out_time_us=1000000\nspeed=1x\nprogress=continue\n"
                             "out_time_us=3024000000\nspeed=1.0x\nprogress=continue\n")

        assert processing.describe_conversion_progress(config) == (
            "Movie.mkv: 42% · about 1 h 9 min left")

    def test_several_files_without_speed(self, config, progress):
        progress({**self.MOVIE, "file": 2, "files": 3},
                 "out_time_us=720000000\nspeed=N/A\n")

        assert processing.describe_conversion_progress(config) == (
            "Movie.mkv: file 2 of 3 · 10%")

    def test_unknown_duration(self, config, progress):
        progress({**self.MOVIE, "duration": None}, "out_time_us=720000000\nspeed=2x\n")

        assert processing.describe_conversion_progress(config) == "Movie.mkv: starting"

    def test_reads_only_end_of_long_progress_file(self, config, progress):
        progress(self.MOVIE, "out_time_us=1\n" * 2000 + "out_time_us=7200000000\nspeed=4x\n")

        assert processing.describe_conversion_progress(config) == (
            "Movie.mkv: 100% · less than a minute left")

    @pytest.mark.parametrize("text, expected", [
        ("", (None, None)),
        ("out_time_us=N/A\nspeed=1.5x\n", (None, 1.5)),
        ("out_time_us=1500000\n", (1.5, None)),
    ])
    def test_read_ffmpeg_progress(self, tmp_path, text, expected):
        path = tmp_path / "progress"
        path.write_text(text, encoding="utf-8")

        assert processing.read_ffmpeg_progress(str(path)) == expected

    def test_read_missing_ffmpeg_progress(self, tmp_path):
        assert processing.read_ffmpeg_progress(str(tmp_path / "missing")) == (None, None)

    @pytest.mark.parametrize("seconds, expected", [
        (30, "less than a minute left"), (720, "about 12 min left"),
        (4200, "about 1 h 10 min left"),
    ])
    def test_format_time_left(self, seconds, expected):
        assert processing.format_time_left(seconds) == expected

    def test_status_of_running_conversion(self, app, config, progress):
        progress(self.MOVIE)

        with ProcessLock(config.conversion.convert_lock_file, "convert"):
            status = processing.get_run_status(app, config, "convert")

        assert status["progress"] == "Movie.mkv: starting"


@pytest.mark.parametrize("mapping, expected", [
    (mock.Mock(source=mock.Mock(index=1, codec="truehd", codec_type="audio", channel_count=8,
                                language="", title="", is_audio=True, is_forced=False),
               output_file=None, codec="aac", channels=2, output_track=1),
     ("Source track 1: audio (truehd, 7.1)", "Destination track 1 (truehd -> aac, stereo)")),
    (mock.Mock(source=mock.Mock(index=1, codec="pcm", codec_type="audio", channel_count=3,
                                language="fre", title="", is_audio=True, is_forced=False),
               output_file=None, codec="ac3", channels=3, output_track=2),
     ("Source track 1: audio (pcm, 3 channels, fre)",
      "Destination track 2 (pcm -> ac3, 3)")),
    (mock.Mock(source=mock.Mock(index=0, codec="h264", codec_type="video", channel_count=-1,
                                language="", title="", is_audio=False, is_forced=False),
               output_file=None, codec="libx264", channels=None, output_track=0),
     ("Source track 0: video (h264)", "Destination track 0 (h264 -> h264, re-encoded)")),
])
def test_describe_mapping(mapping, expected):
    assert convert_page.describe_mapping(mapping) == expected
