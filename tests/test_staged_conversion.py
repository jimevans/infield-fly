"""Tests for the staged_conversion module"""

import json
import os
import signal
import subprocess
from unittest import mock

import pytest

from infield_fly.concurrency import LockHeldError, ProcessLock
from infield_fly.conversion import ConversionSettings, StreamChoice, TrackMapping
from infield_fly.staged_conversion import (ConversionCancelled, convert_staged,
                                           get_staging_directory, write_progress)


class FakeConverter:

    """Stands in for a converter, writing its output files as ffmpeg would"""

    instances = []
    failure = None
    writes_subtitles = False

    def __init__(self, input_file, output_file, ffmpeg_location, is_unattended_mode, *,
                 stream_choice):
        self.input_file = input_file
        self.output_file = output_file
        self.options = (ffmpeg_location, is_unattended_mode, stream_choice)
        self.file_stream_info = mock.Mock(duration=60.0)
        self.convert_options = None
        FakeConverter.instances.append(self)

    @property
    def subtitle_file(self):
        return os.path.splitext(self.output_file)[0] + ".eng.srt"

    def get_track_mappings(self, _settings):
        return ([TrackMapping(None, "srt", output_file=self.subtitle_file)]
                if self.writes_subtitles else [])

    def convert_file(self, settings, overwrite=False, progress_file=None):
        self.convert_options = (settings, overwrite, progress_file)
        with open(self.output_file, "w", encoding="utf-8") as output:
            output.write(f"converted {self.input_file}")
        outputs = [self.output_file]
        if self.writes_subtitles:
            with open(self.subtitle_file, "w", encoding="utf-8") as output:
                output.write("subtitles")
            outputs.append(self.subtitle_file)
        if FakeConverter.failure is not None:
            FakeConverter.failure()
        return outputs


@pytest.fixture(autouse=True)
def fake_converter():
    FakeConverter.instances = []
    FakeConverter.failure = None
    FakeConverter.writes_subtitles = False
    with mock.patch("infield_fly.staged_conversion.Converter", FakeConverter):
        yield FakeConverter


@pytest.fixture
def config(make_config):
    return make_config(conversion={"ffmpeg_location": "/ffmpeg"})


@pytest.fixture
def final(tmp_path):
    return tmp_path / "final"


def test_converts_in_staging_and_moves_to_destination(config, final, tmp_path):
    FakeConverter.writes_subtitles = True
    settings = ConversionSettings(convert_video=True)

    convert_staged(config, [("/in/Movie.mkv", str(final / "Movie.mp4"))], settings,
                   is_unattended_mode=True, stream_choice=StreamChoice(audio=3))

    converter = FakeConverter.instances[0]
    assert converter.output_file == os.path.join(get_staging_directory(config), "Movie.mp4")
    assert converter.output_file.startswith(str(tmp_path / "staging" / "adhoc"))
    assert converter.options == ("/ffmpeg", True, StreamChoice(audio=3))
    assert converter.convert_options == (settings, True, None)
    assert (final / "Movie.mp4").read_text(encoding="utf-8") == "converted /in/Movie.mkv"
    assert (final / "Movie.eng.srt").exists()
    assert os.listdir(get_staging_directory(config)) == []


def test_writes_progress_for_each_file(config, final, tmp_path):
    progress_file = str(tmp_path / "progress.json")
    (tmp_path / "progress.json.ffmpeg").write_text("stale", encoding="utf-8")
    progress = []

    def record_progress():
        with open(progress_file, encoding="utf-8") as progress_json:
            progress.append(json.load(progress_json))
        assert not os.path.exists(f"{progress_file}.ffmpeg")

    FakeConverter.failure = record_progress
    convert_staged(config, [("/in/a.mkv", str(final / "a.mp4")),
                            ("/in/b.mkv", str(final / "b.mp4"))],
                   ConversionSettings(), progress_file=progress_file)

    assert progress == [
        {"file": 1, "files": 2, "source": "/in/a.mkv", "destination": str(final / "a.mp4"),
         "duration": 60.0},
        {"file": 2, "files": 2, "source": "/in/b.mkv", "destination": str(final / "b.mp4"),
         "duration": 60.0},
    ]
    assert FakeConverter.instances[0].convert_options[2] == f"{progress_file}.ffmpeg"


def test_refuses_existing_destination(config, final):
    final.mkdir()
    (final / "b.mp4").write_text("original", encoding="utf-8")

    with pytest.raises(FileExistsError) as error:
        convert_staged(config, [("/in/a.mkv", str(final / "a.mp4")),
                                ("/in/b.mkv", str(final / "b.mp4"))], ConversionSettings())

    assert str(final / "b.mp4") in str(error.value)
    assert FakeConverter.instances == []
    assert (final / "b.mp4").read_text(encoding="utf-8") == "original"


def test_overwrites_existing_destination(config, final):
    final.mkdir()
    (final / "a.mp4").write_text("original", encoding="utf-8")

    convert_staged(config, [("/in/a.mkv", str(final / "a.mp4"))], ConversionSettings(),
                   overwrite=True)

    assert (final / "a.mp4").read_text(encoding="utf-8") == "converted /in/a.mkv"


def test_failed_conversion_removes_partial_files(config, final):
    FakeConverter.writes_subtitles = True

    def fail():
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    FakeConverter.failure = fail
    with pytest.raises(subprocess.CalledProcessError):
        convert_staged(config, [("/in/a.mkv", str(final / "a.mp4"))], ConversionSettings())

    assert os.listdir(get_staging_directory(config)) == []
    assert not final.exists()


def test_termination_cancels_and_removes_partial_files(config, final):
    previous_handler = signal.getsignal(signal.SIGTERM)
    FakeConverter.failure = lambda: os.kill(os.getpid(), signal.SIGTERM)

    with pytest.raises(ConversionCancelled):
        convert_staged(config, [("/in/a.mkv", str(final / "a.mp4"))], ConversionSettings())

    assert os.listdir(get_staging_directory(config)) == []
    assert signal.getsignal(signal.SIGTERM) == previous_handler


def test_one_conversion_at_a_time(config, final):
    with ProcessLock(config.conversion.convert_lock_file, "convert"):
        with pytest.raises(LockHeldError):
            convert_staged(config, [("/in/a.mkv", str(final / "a.mp4"))],
                           ConversionSettings())

    assert FakeConverter.instances == []


def test_write_progress_without_file():
    write_progress(None, {"file": 1})


def test_failure_before_writing_anything(config, final):
    FakeConverter.writes_subtitles = True

    def fail_without_output(*_, **__):
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    with mock.patch.object(FakeConverter, "convert_file", fail_without_output):
        with pytest.raises(subprocess.CalledProcessError):
            convert_staged(config, [("/in/a.mkv", str(final / "a.mp4"))],
                           ConversionSettings())

    assert os.listdir(get_staging_directory(config)) == []
