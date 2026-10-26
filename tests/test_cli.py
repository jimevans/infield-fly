"""Tests for the cli module"""

import json
import logging
import os
import runpy
import sys
from argparse import Namespace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import make_episode_db
from infield_fly import cli
from infield_fly.configuration import DEFAULT_CONFIG_DIRECTORY
from infield_fly.database import EpisodeInfo
from infield_fly.jobs import Job, JobQueue, JobStatus

MAGNET = "magnet:?xt=urn:btih:ABCDEF&dn=Show.S01E02"


@pytest.fixture
def config(make_config):
    return make_config(
        conversion={"substitutions": {":": " -"}, "ffmpeg_location": "/ffmpeg"},
        metadata={"tracked_series": {
            "show": {"id": 1, "description": "The Show", "enable_search": True,
                     "search_configs": [{"search_terms": ["1080p"]}, {"download_only": True}]},
            "missing": {"id": 2, "description": "Missing", "enable_search": True}
        }},
        notification={"sid": "sid", "auth_token": "token", "sending_number": "+1555",
                      "receiving_number": "+1666"})


@pytest.fixture
def episode_db(config):
    """Patches the cached episode database with a known series"""

    database = make_episode_db(config, [
        EpisodeInfo(11, "The Show", season_number=1, episode_number=1, title="Pilot",
                    airdate=datetime(2020, 1, 1)),
        EpisodeInfo(12, "The Show", season_number=1, episode_number=2, title="Part: 2",
                    airdate=datetime(2020, 1, 8)),
        EpisodeInfo(13, "The Show", season_number=1, episode_number=3, title="Future"),
    ])
    with mock.patch("infield_fly.database.EpisodeDatabase.load_from_cache", return_value=database):
        yield database


def add_job(config, status=JobStatus.WAITING, **values):
    job = Job(config.conversion.job_directory, {"status": status.value, **values})
    job.save(logging.getLogger())
    return job


def load_jobs(config):
    return JobQueue(config).load_jobs()


class TestGetSearchStrings:

    def test_builds_searches_for_episodes_in_range(self, config, episode_db, capsys):
        searches = cli.get_search_strings(
            datetime(2020, 1, 1), datetime(2020, 1, 7), episode_db, config.metadata)

        assert searches == [
            {"keyword": "show", "query": "show 1080p s01e01", "download_only": False},
            {"keyword": "show", "query": "show s01e01", "download_only": True},
        ]
        assert capsys.readouterr().out == (
            "Episodes found:\nThe Show - s01e01 - Pilot (airdate 2020-01-01)\n")


def search_args(**overrides):
    values = {"fromdate": "2020-01-01", "todate": "2020-01-31", "update_metadata": False,
              "dry_run": False, "create_jobs": False}
    return Namespace(**{**values, **overrides})


class TestFindDownloads:

    def test_prints_search_strings(self, config, episode_db, capsys):
        with mock.patch.object(episode_db, "update_all_tracked_series") as update:
            cli.find_downloads(search_args(), config)

        update.assert_not_called()
        output = capsys.readouterr().out
        assert "Finding episodes aired between 2020-01-01 and 2020-01-31" in output
        assert "Dry run requested" not in output
        assert output.endswith("Search strings:\n"
                               "show 1080p s01e01\nshow s01e01\n"
                               "show 1080p s01e02\nshow s01e02\n")
        assert load_jobs(config) == []

    def test_dry_run_does_not_create_jobs(self, config, episode_db, capsys):
        cli.find_downloads(search_args(create_jobs=True, dry_run=True), config)

        output = capsys.readouterr().out
        assert "Dry run requested. Not creating jobs.\nSearch strings:\n" in output
        assert load_jobs(config) == []

    def test_updates_metadata(self, config, episode_db, capsys):
        with mock.patch.object(episode_db, "update_all_tracked_series") as update:
            cli.find_downloads(search_args(update_metadata=True), config)

        update.assert_called_once_with()
        assert os.path.exists(episode_db.cache_file_path)
        capsys.readouterr()

    def test_creates_jobs(self, config, episode_db, capsys):
        cli.find_downloads(search_args(create_jobs=True), config)

        jobs = sorted((x.query, x.is_download_only) for x in load_jobs(config))
        assert jobs == [("show 1080p s01e01", False), ("show 1080p s01e02", False),
                        ("show s01e01", True), ("show s01e02", True)]
        assert "Search strings" not in capsys.readouterr().out


class TestListSeries:

    def test_unknown_keyword(self, config, episode_db, capsys):
        cli.list_series(Namespace(keyword="nope"), config)

        assert capsys.readouterr().out == "Keyword 'nope' was not found in the database\n"

    def test_lists_episodes(self, config, episode_db, capsys):
        cli.list_series(Namespace(keyword="show"), config)

        assert capsys.readouterr().out == (
            "s01e01 (aired: 2020-01-01) - Pilot\n"
            "s01e02 (aired: 2020-01-08) - Part: 2\n"
            "s01e03 (aired: not aired) - Future\n")


class TestUpdateDatabase:

    def test_updates_and_saves(self, config, episode_db):
        with mock.patch.object(episode_db, "update_all_tracked_series") as update:
            cli.update_database(Namespace(force_updates=True, unattended=True), config)

        update.assert_called_once_with(force_updates=True, is_unattended_mode=True)
        assert os.path.exists(episode_db.cache_file_path)


def convert_args(**overrides):
    values = {"source": "/media/in.mkv", "destination": "/media/out.mp4", "keyword": None,
              "convert_video": False, "convert_audio": True, "convert_subtitles": True,
              "aspect_ratio": None, "crf": None, "dry_run": False, "notify": False}
    return Namespace(**{**values, **overrides})


class TestConvert:

    @pytest.fixture
    def converter_class(self):
        with mock.patch("infield_fly.conversion.Converter") as converter_class:
            yield converter_class

    @pytest.fixture
    def notifier_factory(self):
        with mock.patch("infield_fly.notification.Notifier.create_default_notifier") as factory:
            yield factory

    @pytest.mark.parametrize("overrides, message", [
        ({"keyword": "nope"}, "Keyword 'nope' was not found in the database"),
        ({"aspect_ratio": 2.4}, "Aspect ratio can only be specified when converting video"),
        ({"aspect_ratio": 0.0, "convert_video": True}, "Aspect ratio must be a positive number"),
        ({"crf": 20.0}, "Constant rate factor can only be specified when converting video"),
        ({"crf": -1.0, "convert_video": True}, "Constant rate factor must be between 0 and 51"),
        ({"crf": 52.0, "convert_video": True}, "Constant rate factor must be between 0 and 51"),
    ])
    def test_invalid_arguments(self, config, episode_db, converter_class, capsys, overrides,
                               message):
        cli.convert(convert_args(**overrides), config)

        assert capsys.readouterr().out.startswith(message)
        converter_class.assert_not_called()

    def test_converts_with_default_settings(self, config, episode_db, converter_class):
        cli.convert(convert_args(), config)

        converter_class.assert_called_once_with("/media/in.mkv", "/media/out.mp4", "/ffmpeg")
        settings = converter_class.return_value.convert_file.call_args[0][0]
        assert (settings.convert_video, settings.convert_audio, settings.convert_subtitles,
                settings.aspect_ratio, settings.crf) == (False, True, True, None, 17)
        assert converter_class.return_value.convert_file.call_args.kwargs == {"dry_run": False}

    def test_converts_mapped_directory_with_video_settings(self, config, episode_db,
                                                           converter_class, tmp_path):
        (tmp_path / "x.s01e02.mkv").touch()
        args = convert_args(source=str(tmp_path), destination=str(tmp_path), keyword="show",
                            convert_video=True, aspect_ratio=2.4, crf=20.0, dry_run=True)

        cli.convert(args, config)

        converter_class.assert_called_once_with(
            str(tmp_path / "x.s01e02.mkv"), str(tmp_path / "The Show - s01e02 - Part - 2.mp4"),
            "/ffmpeg")
        call = converter_class.return_value.convert_file.call_args
        assert (call[0][0].aspect_ratio, call[0][0].crf, call.kwargs) == (
            2.4, 20.0, {"dry_run": True})

    def test_notify_skipped_on_dry_run(self, config, episode_db, converter_class,
                                       notifier_factory, capsys):
        cli.convert(convert_args(notify=True, dry_run=True), config)

        assert "Not sending notification on dry run" in capsys.readouterr().out
        notifier_factory.assert_not_called()

    def test_notify_without_notification_settings(self, config, episode_db, converter_class,
                                                  notifier_factory, capsys):
        config_without_notification = SimpleNamespace(conversion=config.conversion,
                                                       notification=None)

        cli.convert(convert_args(notify=True), config_without_notification)

        assert "No config notification settings" in capsys.readouterr().out
        notifier_factory.assert_not_called()

    @pytest.mark.parametrize("receiving_number", [None, ""])
    def test_notify_without_recipient(self, make_config, episode_db, converter_class,
                                      notifier_factory, capsys, receiving_number):
        config = make_config(notification={"receiving_number": receiving_number})

        cli.convert(convert_args(notify=True), config)

        assert "No recipient number specified" in capsys.readouterr().out
        notifier_factory.assert_not_called()

    def test_notify_with_unconfigured_notifier(self, config, episode_db, converter_class,
                                               notifier_factory):
        notifier_factory.return_value = None

        cli.convert(convert_args(notify=True), config)

        notifier_factory.assert_called_once_with(config)

    def test_notify_sends_message(self, config, episode_db, converter_class, notifier_factory):
        cli.convert(convert_args(notify=True), config)

        notifier_factory.return_value.notify.assert_called_once_with(
            "+1666", "Conversion of /media/in.mkv complete.")


class TestShowJob:

    def test_missing_job(self, config, capsys):
        cli.show_job(Namespace(id="nope"), config)

        assert capsys.readouterr().out == "No existing job with ID 'nope'\n"

    def test_minimal_job(self, config, capsys):
        job = add_job(config, keyword="show", query="show s01e01", added="2020-01-01")

        cli.show_job(Namespace(id=job.job_id), config)

        assert capsys.readouterr().out == (
            f"ID: {job.job_id}\n"
            "Status: waiting\n"
            "Date added: 2020-01-01\n"
            "Series keyword: show\n"
            "Search string: show s01e01\n"
            "Is download-only job: False\n")

    def test_job_with_null_download_only(self, config, capsys):
        job = add_job(config, download_only=None)

        cli.show_job(Namespace(id=job.job_id), config)

        assert "Is download-only job" not in capsys.readouterr().out

    def test_full_job(self, config, capsys):
        job = add_job(config, JobStatus.PENDING, keyword="show", query="q", added="2020-01-01",
                      title="Title", magnet_link=MAGNET, torrent_hash="ABCDEF",
                      download_directory="/downloads", name="Show.S01E01",
                      download_only=True, converted_file_name="Converted")

        cli.show_job(Namespace(id=job.job_id), config)

        output = capsys.readouterr().out
        assert "Torrent title: Title\n" in output
        assert f"Magnet link: {MAGNET}\n" in output
        assert "Torrent hash: ABCDEF\n" in output
        assert f"Torrent directory: {os.path.join('/downloads', 'Show.S01E01')}\n" in output
        assert "Is download-only job: True\n" in output
        assert "File name of converted file: Converted.mp4\n" in output


class TestJobCommands:

    def test_create_job(self, config):
        cli.create_job(Namespace(keyword="show", search_term="show s01e01"), config)

        jobs = load_jobs(config)
        assert [(x.keyword, x.query, x.status) for x in jobs] == [
            ("show", "show s01e01", JobStatus.WAITING)]

    def test_delete_job(self, config):
        job = add_job(config)

        cli.delete_job(Namespace(id=job.job_id), config)

        assert load_jobs(config) == []

    def test_delete_missing_job(self, config, capsys):
        cli.delete_job(Namespace(id="nope"), config)

        assert capsys.readouterr().out == "No existing job with ID 'nope'\n"


def update_args(job_id, status, update_data=None):
    return Namespace(id=job_id, status=status, update_data=update_data)


class TestUpdateJob:

    def test_missing_job(self, config, capsys):
        cli.update_job(update_args("nope", "pending"), config)

        assert capsys.readouterr().out == "No existing job with ID 'nope'\n"

    def test_unknown_status(self, config, capsys):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, "bogus"), config)

        assert capsys.readouterr().out == "Unknown status value 'bogus'\n"
        assert load_jobs(config)[0].status == JobStatus.WAITING

    def test_adding_with_magnet_link(self, config):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, "adding", MAGNET), config)

        job = load_jobs(config)[0]
        assert (job.status, job.magnet_link, job.title, job.torrent_hash) == (
            JobStatus.ADDING, MAGNET, "Show.S01E02", "ABCDEF")

    def test_pending_with_invalid_file(self, config, tmp_path, capsys):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, "pending", str(tmp_path / "nope")), config)

        assert "must include a path a to valid file" in capsys.readouterr().out
        assert load_jobs(config)[0].status == JobStatus.WAITING

    def test_pending_with_file(self, config, tmp_path):
        job = add_job(config)
        download = tmp_path / "Show.S01E01.mkv"
        download.touch()

        cli.update_job(update_args(job.job_id, "pending", str(download)), config)

        job = load_jobs(config)[0]
        assert (job.status, job.name, job.download_directory) == (
            JobStatus.PENDING, "Show.S01E01.mkv", str(tmp_path))

    @pytest.mark.parametrize("status, expected", [
        ("completed", JobStatus.COMPLETED),
        ("adding", JobStatus.ADDING),
        ("pending", JobStatus.PENDING),
        ("Completed", JobStatus.COMPLETED),
        ("PENDING", JobStatus.PENDING),
    ])
    def test_sets_status_without_update_data(self, config, status, expected):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, status), config)

        assert load_jobs(config)[0].status == expected

    def test_status_with_update_data_is_case_insensitive(self, config):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, "ADDING", MAGNET), config)

        job = load_jobs(config)[0]
        assert (job.status, job.magnet_link) == (JobStatus.ADDING, MAGNET)


class TestParseJobStatus:

    @pytest.mark.parametrize("value, expected", [
        (None, None), ("bogus", None), ("pending", JobStatus.PENDING),
        ("COMPLETED", JobStatus.COMPLETED)
    ])
    def test_parse(self, value, expected):
        assert cli.parse_job_status(value) == expected


class TestListAndClearJobs:

    @pytest.fixture
    def jobs(self, config):
        return [add_job(config, JobStatus.WAITING, query="a"),
                add_job(config, JobStatus.PENDING, name="b")]

    def test_list_all(self, config, jobs, capsys):
        cli.list_jobs(Namespace(status=None), config)

        output = capsys.readouterr().out
        assert f"{jobs[0].job_id} Waiting to search for 'a'\n" in output
        assert f"{jobs[1].job_id} Pending conversion of finished download 'b'\n" in output

    def test_list_by_status(self, config, jobs, capsys):
        cli.list_jobs(Namespace(status="Pending"), config)

        assert capsys.readouterr().out == (
            f"{jobs[1].job_id} Pending conversion of finished download 'b'\n")

    @pytest.mark.parametrize("command", [cli.list_jobs, cli.clear_jobs])
    def test_unknown_status(self, config, jobs, capsys, command):
        command(Namespace(status="bogus"), config)

        assert capsys.readouterr().out == "Unknown status value 'bogus'\n"
        assert len(load_jobs(config)) == 2

    def test_clear_all(self, config, jobs):
        cli.clear_jobs(Namespace(status=None), config)

        assert load_jobs(config) == []

    def test_clear_by_status(self, config, jobs):
        cli.clear_jobs(Namespace(status="WAITING"), config)

        assert [x.job_id for x in load_jobs(config)] == [jobs[1].job_id]


class TestProcessJobs:

    @pytest.fixture
    def job_queue(self):
        with mock.patch("infield_fly.jobs.JobQueue") as queue_class:
            yield queue_class.return_value

    def test_runs_all_phases(self, config, job_queue):
        args = Namespace(search=True, add_downloads=True, query_downloads=True, convert=True,
                         unattended=True)

        cli.process_jobs(args, config)

        today = datetime.now()
        job_queue.refresh_jobs.assert_called_once_with(
            datetime(today.year, today.month, today.day))
        job_queue.add_torrents.assert_called_once_with()
        job_queue.query_torrents_status.assert_called_once_with()
        job_queue.perform_conversions.assert_called_once_with(True)

    def test_skips_disabled_phases(self, config, job_queue):
        args = Namespace(search=False, add_downloads=False, query_downloads=False,
                         convert=False, unattended=False)

        cli.process_jobs(args, config)

        assert job_queue.method_calls == []


class TestCompletion:

    def test_completes_job_statuses_except_unknown(self):
        assert cli.complete_job_status() == [
            "waiting", "searching", "adding", "downloading", "pending", "converting",
            "completed"]

    def test_completes_job_ids(self, config):
        first = add_job(config)
        second = add_job(config)

        with mock.patch("infield_fly.cli.load_config", return_value=config) as load_config:
            job_ids = cli.complete_job_id(parsed_args=Namespace(config="/settings.json"))

        load_config.assert_called_once_with("/settings.json")
        assert job_ids == sorted([first.job_id, second.job_id])

    def test_missing_job_directory_completes_nothing(self, config):
        with mock.patch("infield_fly.cli.load_config", return_value=config):
            assert cli.complete_job_id(parsed_args=Namespace(config=None)) == []

        assert not os.path.exists(config.conversion.job_directory)

    def test_missing_config_file_completes_nothing(self, tmp_path):
        missing = str(tmp_path / "missing.json")

        assert cli.complete_job_id(parsed_args=Namespace(config=missing)) == []

    def test_invalid_config_file_completes_nothing(self, tmp_path):
        config_file = tmp_path / "settings.json"
        config_file.write_text("{", encoding="utf-8")

        assert cli.complete_job_id(parsed_args=Namespace(config=str(config_file))) == []


class TestSetupLogging:

    def test_unattended_logs_to_rotating_file(self, make_config, tmp_path):
        config = make_config(conversion={"log_level": "warning"})

        cli.setup_logging(config, True)
        cli.setup_logging(config, True)

        handler = logging.getLogger().handlers[-1]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.baseFilename == str(tmp_path / "logs" / "infieldfly.log")
        assert handler.level == logging.WARNING
        assert logging.getLogger().level == logging.DEBUG

    def test_attended_logs_to_stdout(self, make_config):
        cli.setup_logging(make_config(), False)

        handler = logging.getLogger().handlers[-1]
        assert type(handler) is logging.StreamHandler  # pylint: disable=unidiomatic-typecheck
        assert handler.stream is sys.stdout
        assert handler.level == logging.INFO


class TestLoadConfig:

    @pytest.fixture(autouse=True)
    def no_config_environment_variable(self, monkeypatch):
        monkeypatch.delenv("INFIELD_FLY_CONFIG", raising=False)

    def test_loads_specified_file(self, tmp_path):
        config_file = tmp_path / "settings.json"
        config_file.write_text(json.dumps({"conversion": {"ffmpeg_location": "/ffmpeg"}}),
                               encoding="utf-8")

        config = cli.load_config(str(config_file))

        assert config.conversion.ffmpeg_location == "/ffmpeg"
        assert config.conversion.infield_fly_directory == str(tmp_path)
        assert config.conversion.job_directory == str(tmp_path / ".jobs")

    def test_relative_path_is_made_absolute(self, tmp_path, monkeypatch):
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        config = cli.load_config("settings.json")

        assert config.conversion.infield_fly_directory == str(tmp_path)

    def test_expands_home_directory(self, tmp_path, monkeypatch):
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("HOME", str(tmp_path))

        config = cli.load_config("~/settings.json")

        assert config.conversion.infield_fly_directory == str(tmp_path)

    def test_uses_environment_variable(self, tmp_path, monkeypatch):
        config_file = tmp_path / "env.json"
        config_file.write_text(json.dumps({"conversion": {"ffmpeg_location": "/env"}}),
                               encoding="utf-8")
        monkeypatch.setenv("INFIELD_FLY_CONFIG", str(config_file))

        config = cli.load_config(None)

        assert config.conversion.ffmpeg_location == "/env"
        assert config.conversion.infield_fly_directory == str(tmp_path)

    def test_argument_overrides_environment_variable(self, tmp_path, monkeypatch):
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("INFIELD_FLY_CONFIG", "/elsewhere/env.json")

        config = cli.load_config(str(tmp_path / "settings.json"))

        assert config.conversion.infield_fly_directory == str(tmp_path)

    def test_missing_specified_file_is_an_error(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="missing.json' does not exist"):
            cli.load_config(str(tmp_path / "missing.json"))

    def test_missing_environment_variable_file_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INFIELD_FLY_CONFIG", str(tmp_path / "missing.json"))

        with pytest.raises(FileNotFoundError, match="missing.json' does not exist"):
            cli.load_config(None)

    def test_missing_default_file_uses_defaults(self):
        with mock.patch("infield_fly.cli.os.path.exists", return_value=False) as exists:
            config = cli.load_config(None)

        exists.assert_called_once_with(os.path.join(DEFAULT_CONFIG_DIRECTORY, "settings.json"))
        assert config.conversion.settings == {}
        assert config.conversion.infield_fly_directory == DEFAULT_CONFIG_DIRECTORY


class TestMain:

    def run_main(self, *argv, command):
        """Runs main with the specified arguments, returning the args passed to the command"""

        with mock.patch.object(sys, "argv", ["infield-fly", *argv]), \
                mock.patch("infield_fly.cli.load_config") as load_config, \
                mock.patch("infield_fly.cli.setup_logging") as setup_logging, \
                mock.patch(f"infield_fly.cli.{command}") as command_function:
            cli.main()

        args, config = command_function.call_args[0]
        assert config is load_config.return_value
        load_config.assert_called_once_with(args.config)
        setup_logging.assert_called_once_with(config, args.unattended)
        return args

    def test_convert_defaults(self):
        args = self.run_main("convert", "src", "dest", command="convert")

        assert (args.source, args.destination, args.keyword, args.convert_video,
                args.aspect_ratio, args.crf, args.convert_audio, args.convert_subtitles,
                args.dry_run, args.notify, args.unattended, args.config) == (
            "src", "dest", None, False, None, None, True, True, False, False, False, None)

    def test_convert_options(self):
        args = self.run_main("-u", "-c", "conf.json", "convert", "src", "dest", "-k", "show",
                             "--convert-video", "-a", "2.4", "--crf", "20",
                             "--no-convert-audio", "--no-convert-subtitles", "-x", "-n",
                             command="convert")

        assert (args.keyword, args.convert_video, args.aspect_ratio, args.crf,
                args.convert_audio, args.convert_subtitles, args.dry_run, args.notify,
                args.unattended, args.config) == (
            "show", True, 2.4, 20.0, False, False, True, True, True, "conf.json")

    def test_search(self):
        args = self.run_main("search", "2020-01-01", "2020-01-31", "-u", "-x", "-c",
                             command="find_downloads")

        assert (args.fromdate, args.todate, args.update_metadata, args.dry_run,
                args.create_jobs) == ("2020-01-01", "2020-01-31", True, True, True)

    def test_search_defaults(self):
        args = self.run_main("search", command="find_downloads")

        assert args.todate == datetime.now().strftime("%Y-%m-%d")
        assert args.fromdate < args.todate
        assert not args.dry_run
        assert not args.create_jobs

    def test_database_commands(self):
        assert self.run_main("database", "list", "show", command="list_series").keyword == "show"
        assert self.run_main("database", "update", "-f",
                             command="update_database").force_updates

    @pytest.mark.parametrize("argv, command, expected", [
        (["add", "show", "show s01e01"], "create_job",
         {"keyword": "show", "search_term": "show s01e01"}),
        (["show", "abc"], "show_job", {"id": "abc"}),
        (["update", "abc", "pending"], "update_job",
         {"id": "abc", "status": "pending", "update_data": None}),
        (["update", "abc", "adding", "magnet"], "update_job", {"update_data": "magnet"}),
        (["remove", "abc"], "delete_job", {"id": "abc"}),
        (["list"], "list_jobs", {"status": None}),
        (["list", "pending"], "list_jobs", {"status": "pending"}),
        (["clear"], "clear_jobs", {"status": None}),
        (["clear", "completed"], "clear_jobs", {"status": "completed"}),
        (["process"], "process_jobs",
         {"search": True, "add_downloads": True, "query_downloads": True, "convert": True}),
        (["process", "--no-search", "--no-add-downloads", "--no-query-downloads",
          "--no-convert"], "process_jobs",
         {"search": False, "add_downloads": False, "query_downloads": False, "convert": False}),
        (["process", "--search", "--add-downloads", "--query-downloads", "--convert"],
         "process_jobs",
         {"search": True, "add_downloads": True, "query_downloads": True, "convert": True}),
    ])
    def test_jobs_commands(self, argv, command, expected):
        args = self.run_main("jobs", *argv, command=command)

        assert {name: getattr(args, name) for name in expected} == expected

    def test_missing_settings_file_is_reported(self, tmp_path, capsys):
        missing = str(tmp_path / "missing.json")
        with mock.patch.object(sys, "argv", ["infield-fly", "-c", missing, "jobs", "list"]), \
                mock.patch("infield_fly.cli.list_jobs") as list_jobs, \
                pytest.raises(SystemExit) as exit_info:
            cli.main()

        assert exit_info.value.code == 2
        assert f"Settings file '{missing}' does not exist" in capsys.readouterr().err
        list_jobs.assert_not_called()

    def test_requires_command(self, capsys):
        with mock.patch.object(sys, "argv", ["infield-fly"]), pytest.raises(SystemExit):
            cli.main()

        assert "required" in capsys.readouterr().err

    def test_runs_as_module(self, capsys):
        with mock.patch.object(sys, "argv", ["infield-fly", "--help"]), \
                pytest.raises(SystemExit) as exit_info:
            runpy.run_module("infield_fly", run_name="__main__")

        assert exit_info.value.code == 0
        assert "usage:" in capsys.readouterr().out
