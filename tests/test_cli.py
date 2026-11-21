"""Tests for the cli module"""

# The tests of the commands share this module's fixtures, so they are kept together.
# pylint: disable=too-many-lines

import json
import logging
import os
import runpy
import subprocess
import sys
from argparse import Namespace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import TRACKED_SERIES, make_episode_db
from infield_fly import cli
from infield_fly.concurrency import LockHeldError, ProcessLock
from infield_fly.configuration import DEFAULT_CONFIG_DIRECTORY, EpisodeSearch
from infield_fly.database import EpisodeInfo
from infield_fly.jobs import Job, JobQueue, JobStatus
from infield_fly.staged_conversion import ConversionCancelled

MAGNET = "magnet:?xt=urn:btih:ABCDEF&dn=Show.S01E02"
VALID_NZB = (b'<nzb><file subject=\'"Show.S01E02.mkv" yEnc (1/1)\'><segments>'
             b'<segment bytes="10" number="1">a@b</segment></segments></file></nzb>')


@pytest.fixture
def config(make_config):
    return make_config(
        conversion={"substitutions": {":": " -"}, "ffmpeg_location": "/ffmpeg"},
        metadata={"tracked_series": TRACKED_SERIES},
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
    job.save()
    return job


def load_jobs(config):
    return JobQueue(config).load_jobs()


class TestGetSearchStrings:

    def test_builds_searches_for_episodes_in_range(self, config, episode_db, capsys):
        searches = cli.get_search_strings(
            datetime(2020, 1, 1), datetime(2020, 1, 7), episode_db, config.metadata)

        assert searches == [
            {"keyword": "show", "search": EpisodeSearch(["show 1080p s01e01"], False)},
            {"keyword": "show", "search": EpisodeSearch(["show s01e01"], True)},
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
                               "show 1080p s01e01\nshow s01e01 (download only)\n"
                               "show 1080p s01e02\nshow s01e02 (download only)\n")
        assert load_jobs(config) == []

    def test_prints_fallback_search_strings(self, make_config, capsys):
        config = make_config(metadata={"tracked_series": {"show": {
            "id": 1, "description": "The Show", "enable_search": True,
            "search_configs": [{"search_terms": ["2160p"], "release_groups": ["flux", "ntb"]},
                               {"search_terms": ["1080p"]},
                               {"search_terms": ["4k"], "download_only": True,
                                "release_groups": ["grp"]}]}}})
        database = make_episode_db(config, [
            EpisodeInfo(11, "The Show", season_number=1, episode_number=1, title="Pilot",
                        airdate=datetime(2020, 1, 1))])

        with mock.patch("infield_fly.database.EpisodeDatabase.load_from_cache",
                        return_value=database):
            cli.find_downloads(search_args(), config)

        assert capsys.readouterr().out.endswith(
            "Search strings:\n"
            "show 2160p s01e01 [release groups: flux, ntb]\n"
            "  then: show 1080p s01e01\n"
            "show 4k s01e01 [release groups: grp] (download only)\n")

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

    def test_skips_metadata_update_while_another_is_in_progress(self, config, episode_db,
                                                                 capsys, caplog):
        with mock.patch.object(episode_db, "update_all_tracked_series") as update, \
                ProcessLock(config.conversion.database_lock_file, "database update"):
            cli.find_downloads(search_args(update_metadata=True), config)

        update.assert_not_called()
        assert "Not updating the episode database" in caplog.text
        assert "Finding episodes aired between" in capsys.readouterr().out

    def test_create_jobs_refused_while_processing(self, config, episode_db, capsys):
        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            cli.find_downloads(search_args(create_jobs=True), config)

        assert "Job processing is running" in capsys.readouterr().out
        assert load_jobs(config) == []

    def test_creates_jobs(self, config, episode_db, capsys):
        cli.find_downloads(search_args(create_jobs=True), config)

        jobs = sorted((x.query, x.is_download_only) for x in load_jobs(config))
        assert jobs == [("show 1080p s01e01", False), ("show 1080p s01e02", False),
                        ("show s01e01", True), ("show s01e02", True)]
        assert "Search strings" not in capsys.readouterr().out

    def test_does_not_duplicate_existing_jobs(self, config, episode_db):
        cli.find_downloads(search_args(create_jobs=True), config)
        cli.find_downloads(search_args(create_jobs=True), config)

        assert len(load_jobs(config)) == 4


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

    def test_holds_lock_while_updating(self, config, episode_db):
        def check_lock_held(**_):
            with pytest.raises(LockHeldError):
                with ProcessLock(config.conversion.database_lock_file, "other"):
                    pass

        with mock.patch.object(episode_db, "update_all_tracked_series",
                               side_effect=check_lock_held) as update:
            cli.update_database(Namespace(force_updates=False, unattended=True), config)

        update.assert_called_once()
        with ProcessLock(config.conversion.database_lock_file, "after"):
            pass

    def test_skips_update_while_another_is_in_progress(self, config, episode_db, caplog):
        with mock.patch.object(episode_db, "update_all_tracked_series") as update, \
                ProcessLock(config.conversion.database_lock_file, "database update"):
            cli.update_database(Namespace(force_updates=False, unattended=True), config)

        update.assert_not_called()
        assert ("Not updating the episode database; an update by 'database update' "
                "(process ") in caplog.text


def convert_args(**overrides):
    values = {"source": "/media/in.mkv", "destination": "/media/out.mp4", "keyword": None,
              "convert_video": False, "convert_audio": True, "convert_subtitles": True,
              "aspect_ratio": None, "crf": None, "dry_run": False, "notify": False,
              "unattended": False, "stage": False, "overwrite": False, "progress_file": None}
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
        ({"aspect_ratio": 2.4}, "An aspect ratio can only be given when re-encoding video."),
        ({"aspect_ratio": 0.0, "convert_video": True},
         "The aspect ratio must be a positive number"),
        ({"crf": 20.0}, "A constant rate factor (CRF) can only be given when re-encoding"),
        ({"crf": -1.0, "convert_video": True},
         "The constant rate factor (CRF) must be between 0 and 51."),
        ({"crf": 52.0, "convert_video": True},
         "The constant rate factor (CRF) must be between 0 and 51."),
    ])
    def test_invalid_arguments(self, config, episode_db, converter_class, capsys, overrides,
                               message):
        cli.convert(convert_args(**overrides), config)

        assert capsys.readouterr().out.startswith(message)
        converter_class.assert_not_called()

    def test_converts_with_default_settings(self, config, episode_db, converter_class):
        cli.convert(convert_args(), config)

        converter_class.assert_called_once_with("/media/in.mkv", "/media/out.mp4", "/ffmpeg",
                                                False)
        settings = converter_class.return_value.convert_file.call_args[0][0]
        assert (settings.convert_video, settings.convert_audio, settings.convert_subtitles,
                settings.aspect_ratio, settings.crf) == (False, True, True, None, 17)
        assert converter_class.return_value.convert_file.call_args.kwargs == {
            "dry_run": False, "overwrite": False}

    def test_unattended_and_overwrite(self, config, episode_db, converter_class):
        cli.convert(convert_args(unattended=True, overwrite=True), config)

        assert converter_class.call_args[0][3] is True
        assert converter_class.return_value.convert_file.call_args.kwargs["overwrite"] is True

    def test_empty_directory(self, config, episode_db, converter_class, tmp_path, caplog):
        cli.convert(convert_args(source=str(tmp_path), destination=str(tmp_path)), config)

        converter_class.assert_not_called()
        assert f"No files to convert in {tmp_path}" in caplog.text

    @pytest.fixture
    def stage(self):
        with mock.patch("infield_fly.staged_conversion.convert_staged") as convert_staged:
            yield convert_staged

    def test_stage(self, config, episode_db, converter_class, stage, caplog):
        caplog.set_level("INFO")

        cli.convert(convert_args(stage=True, overwrite=True, progress_file="/p",
                                 unattended=True), config)

        converter_class.assert_not_called()
        args, options = stage.call_args
        assert args[0] is config
        assert args[1] == [("/media/in.mkv", "/media/out.mp4")]
        assert options == {"overwrite": True, "progress_file": "/p",
                           "is_unattended_mode": True}
        assert "Conversion of /media/in.mkv complete" in caplog.text

    def test_dry_run_is_not_staged(self, config, episode_db, converter_class, stage):
        cli.convert(convert_args(stage=True, dry_run=True), config)

        stage.assert_not_called()
        converter_class.assert_called_once()

    @pytest.mark.parametrize("error, message", [
        (LockHeldError({"pid": 5, "description": "convert", "started": "now"}),
         "Not converting; a conversion by 'convert' (process 5, started now) is still in "
         "progress"),
        (FileExistsError("Converted files already exist: /x.mp4"),
         "Not converting: Converted files already exist: /x.mp4; use --overwrite"),
        (subprocess.CalledProcessError(1, ["ffmpeg"]), "Conversion of /media/in.mkv failed"),
    ])
    def test_stage_failures(self, config, episode_db, stage, notifier_factory, caplog,
                            error, message):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        stage.side_effect = error

        cli.convert(convert_args(stage=True, notify=True), config)

        assert message in caplog.text
        notifier_factory.assert_not_called()

    def test_stage_cancelled(self, config, episode_db, stage, caplog):
        stage.side_effect = ConversionCancelled()

        cli.convert(convert_args(stage=True), config)

        assert "Conversion of /media/in.mkv cancelled" in caplog.text

    def test_converts_mapped_directory_with_video_settings(self, config, episode_db,
                                                           converter_class, tmp_path):
        (tmp_path / "x.s01e02.mkv").touch()
        args = convert_args(source=str(tmp_path), destination=str(tmp_path), keyword="show",
                            convert_video=True, aspect_ratio=2.4, crf=20.0, dry_run=True)

        cli.convert(args, config)

        converter_class.assert_called_once_with(
            str(tmp_path / "x.s01e02.mkv"), str(tmp_path / "The Show - s01e02 - Part - 2.mp4"),
            "/ffmpeg", False)
        call = converter_class.return_value.convert_file.call_args
        assert (call[0][0].aspect_ratio, call[0][0].crf, call.kwargs) == (
            2.4, 20.0, {"dry_run": True, "overwrite": False})

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

    def test_job_with_fallback_search_strings(self, config, capsys):
        job = add_job(config, query="show 2160p s01e01",
                      fallback_queries=["show 1080p s01e01", "show 720p s01e01"],
                      release_groups={"show 2160p s01e01": ["flux", "ntb"],
                                      "show 720p s01e01": ["grp"]})

        cli.show_job(Namespace(id=job.job_id), config)

        assert ("Search string: show 2160p s01e01 [release groups: flux, ntb]\n"
                "Fallback search string: show 1080p s01e01\n"
                "Fallback search string: show 720p s01e01 [release groups: grp]\n"
                in capsys.readouterr().out)

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
        assert "NZB" not in output

    def test_nzb_job(self, config, capsys):
        job = add_job(config, JobStatus.DOWNLOADING, title="Show.S01E02", nzb_guid="guid",
                      nzb_file="/nzb/job.nzb", nzo_id="nzo1", rejected=["a", "b"])

        cli.show_job(Namespace(id=job.job_id), config)

        output = capsys.readouterr().out
        assert ("NZB title: Show.S01E02\n"
                "NZB indexer ID: guid\n"
                "NZB file: /nzb/job.nzb\n"
                "SABnzbd ID: nzo1\n"
                "Rejected NZB indexer IDs: a, b\n") in output


class TestJobCommands:

    def test_create_job(self, config, capsys):
        cli.create_job(Namespace(keyword="show", search_term="show s01e01"), config)

        jobs = load_jobs(config)
        assert [(x.keyword, x.query, x.status) for x in jobs] == [
            ("show", "show s01e01", JobStatus.WAITING)]
        assert capsys.readouterr().out == f"Added job {jobs[0].job_id}\n"

    def test_create_job_for_untracked_series(self, config, capsys):
        cli.create_job(Namespace(keyword="nope", search_term="nope s01e01"), config)

        assert load_jobs(config) == []
        assert capsys.readouterr().out == (
            "'nope' is not the main keyword of a tracked series.\n")

    @pytest.mark.parametrize("command, make_args", [
        (cli.create_job, lambda job: Namespace(keyword="show", search_term="show s01e09")),
        (cli.delete_job, lambda job: Namespace(id=job.job_id)),
        (cli.update_job, lambda job: Namespace(id=job.job_id, status="completed",
                                               update_data=None)),
        (cli.reset_job, lambda job: Namespace(id=job.job_id)),
        (cli.clear_jobs, lambda job: Namespace(status=None)),
    ])
    def test_refused_while_processing(self, config, capsys, command, make_args):
        job = add_job(config, keyword="show", query="show s01e02", rejected=["a"])
        before = Path(job.file_path).read_text(encoding="utf-8")

        with ProcessLock(config.conversion.job_lock_file, "jobs process"):
            command(make_args(job), config)

        assert "Job processing is running" in capsys.readouterr().out
        assert [x.job_id for x in load_jobs(config)] == [job.job_id]
        assert Path(job.file_path).read_text(encoding="utf-8") == before

    def test_delete_job(self, config):
        job = add_job(config)

        cli.delete_job(Namespace(id=job.job_id), config)

        assert load_jobs(config) == []

    def test_delete_missing_job(self, config, capsys):
        cli.delete_job(Namespace(id="nope"), config)

        assert capsys.readouterr().out == "No existing job with ID 'nope'\n"


    def test_reset_job(self, config, capsys):
        job = add_job(config, keyword="show", query="show s01e02", rejected=["a"])
        sibling = add_job(config, keyword="show", query="show 2160p s01e02", rejected=["a"])

        cli.reset_job(Namespace(id=job.job_id), config)

        assert [x.rejected for x in load_jobs(config)] == [[], []]
        assert sorted(capsys.readouterr().out.splitlines()) == sorted(
            f"Cleared rejected downloads for job {x.job_id}" for x in (job, sibling))

    def test_reset_missing_job(self, config, capsys):
        cli.reset_job(Namespace(id="nope"), config)

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

    def test_adding_with_nzb_file(self, config, tmp_path, capsys):
        job = add_job(config)
        nzb_file = tmp_path / "Show.S01E02.nzb"
        nzb_file.write_bytes(VALID_NZB)

        cli.update_job(update_args(job.job_id, "adding", str(nzb_file)), config)

        job = load_jobs(config)[0]
        assert (job.status, job.downloader, job.title, job.nzb_guid) == (
            JobStatus.ADDING, "sabnzbd", "Show.S01E02", None)
        with open(job.nzb_file, "rb") as saved:
            assert saved.read() == VALID_NZB
        assert "Warning: NZB file lists no par2 files" in capsys.readouterr().out

    def test_adding_with_nzb_url(self, config):
        job = add_job(config)
        url = "https://indexer/getnzb/Show.S01E02.nzb?apikey=key"

        with mock.patch("infield_fly.usenet.newznab.NewznabSearcher.fetch_nzb",
                        return_value=VALID_NZB) as fetch_nzb:
            cli.update_job(update_args(job.job_id, "adding", url), config)

        assert fetch_nzb.call_args[0][0].link == url
        job = load_jobs(config)[0]
        assert (job.status, job.title) == (JobStatus.ADDING, "Show.S01E02")

    def test_adding_with_unavailable_nzb_url(self, config, capsys):
        job = add_job(config)

        with mock.patch("infield_fly.usenet.newznab.NewznabSearcher.fetch_nzb", return_value=None):
            cli.update_job(update_args(job.job_id, "adding", "https://indexer/x.nzb"), config)

        assert "Could not download NZB file" in capsys.readouterr().out
        assert load_jobs(config)[0].status == JobStatus.WAITING

    def test_adding_with_invalid_nzb_file(self, config, tmp_path, capsys):
        job = add_job(config)
        nzb_file = tmp_path / "bad.nzb"
        nzb_file.write_bytes(b"<nzb/>")

        cli.update_job(update_args(job.job_id, "adding", str(nzb_file)), config)

        assert ("NZB file failed validation: NZB file lists no files"
                in capsys.readouterr().out)
        assert load_jobs(config)[0].status == JobStatus.WAITING

    def test_adding_with_unrecognized_data(self, config, capsys):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, "adding", "nonsense"), config)

        assert "give a magnet link, or the path or URL of an NZB file" in (
            capsys.readouterr().out)
        assert load_jobs(config)[0].status == JobStatus.WAITING

    def test_pending_with_invalid_file(self, config, tmp_path, capsys):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, "pending", str(tmp_path / "nope")), config)

        assert "does not exist" in capsys.readouterr().out
        assert load_jobs(config)[0].status == JobStatus.WAITING

    def test_pending_with_folder(self, config, tmp_path):
        job = add_job(config, keyword="show", query="show s01e01")
        folder = tmp_path / "Show.S01E01.1080p"
        folder.mkdir()
        (folder / "show.s01e01.mkv").touch()

        cli.update_job(update_args(job.job_id, "pending", str(folder)), config)

        updated = load_jobs(config)[0]
        assert (updated.status, updated.name) == (JobStatus.PENDING, "Show.S01E01.1080p")

    def test_download_with_another_status(self, config, capsys):
        job = add_job(config)

        cli.update_job(update_args(job.job_id, "waiting", "magnet:?x"), config)

        assert "only be given for the adding or pending status" in capsys.readouterr().out

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
        args = Namespace(search=True, search_downloads=True, add_downloads=True,
                         query_downloads=True, convert=True, unattended=True)

        cli.process_jobs(args, config)

        today = datetime.now()
        job_queue.refresh_jobs.assert_called_once_with(
            datetime(today.year, today.month, today.day))
        job_queue.search_downloads.assert_called_once_with()
        job_queue.add_downloads.assert_called_once_with()
        job_queue.query_downloads_status.assert_called_once_with()
        job_queue.perform_conversions.assert_called_once_with(True)

    def test_skips_disabled_phases(self, config, job_queue):
        args = Namespace(search=False, search_downloads=False, add_downloads=False,
                         query_downloads=False, convert=False, unattended=False)

        cli.process_jobs(args, config)

        assert job_queue.method_calls == []


class TestProcessJobsLock:

    def test_holds_lock_while_processing(self, config):
        def check_lock_held(*_):
            with pytest.raises(LockHeldError):
                with ProcessLock(config.conversion.job_lock_file, "other"):
                    pass

        with mock.patch("infield_fly.cli.run_processing_phases",
                        side_effect=check_lock_held) as run_phases:
            cli.process_jobs(Namespace(), config)

        run_phases.assert_called_once()
        with ProcessLock(config.conversion.job_lock_file, "after"):
            pass

    def test_skips_processing_while_another_run_is_in_progress(self, config, caplog):
        with mock.patch("infield_fly.jobs.JobQueue") as queue_class:
            with ProcessLock(config.conversion.job_lock_file, "jobs process"):
                cli.process_jobs(Namespace(search=True, search_downloads=True,
                                           add_downloads=True, query_downloads=True,
                                           convert=True, unattended=True), config)

        queue_class.assert_not_called()
        assert "Not processing jobs; job processing by 'jobs process' (process " in caplog.text
        assert "is still in progress" in caplog.text


class TestRunWeb:

    @staticmethod
    def web_args(**overrides):
        return Namespace(**{"config": None, "host": "127.0.0.1", "port": 8642,
                            "root_path": "/infield-fly", "allowed_user": None,
                            "pam_service": "infield-fly", "allow_http": False,
                            "browse_roots": [], **overrides})

    def test_runs_app_behind_local_proxy(self, config):
        with mock.patch("uvicorn.run") as run, \
                mock.patch("infield_fly.web.app.create_app") as create_app, \
                mock.patch("getpass.getuser", return_value="me"):
            cli.run_web(self.web_args(), config)

        load_config, authenticator, secret_key = create_app.call_args[0]
        settings_file = create_app.call_args[1].pop("settings_file")
        assert settings_file.path == cli.resolve_config_path(None)[0]
        assert create_app.call_args[1] == {"https_only": True, "root_path": "/infield-fly",
                                           "browse_roots": []}
        assert (authenticator.allowed_user, authenticator.pam_service) == ("me", "infield-fly")
        assert secret_key == Path(config.conversion.infield_fly_directory,
                                  ".web_secret").read_text(encoding="utf-8")
        with mock.patch("infield_fly.cli.load_config") as load:
            assert load_config() is load.return_value
        load.assert_called_once_with(None)
        run.assert_called_once_with(create_app.return_value, host="127.0.0.1", port=8642,
                                    root_path="/infield-fly", proxy_headers=True,
                                    forwarded_allow_ips="127.0.0.1", log_config=None)

    def test_allowed_user_and_http(self, config):
        with mock.patch("uvicorn.run"), \
                mock.patch("infield_fly.web.app.create_app") as create_app:
            cli.run_web(self.web_args(allowed_user="someone", allow_http=True), config)

        assert create_app.call_args[0][1].allowed_user == "someone"
        assert create_app.call_args[1]["https_only"] is False

    def test_missing_dependencies_are_reported(self, config, capsys):
        with mock.patch.dict(sys.modules, {"uvicorn": None}):
            cli.run_web(self.web_args(), config)

        assert "requires the 'uvicorn' package" in capsys.readouterr().out


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

    def test_completes_job_ids_without_dot_files(self, config):
        job = add_job(config)
        with open(config.conversion.job_lock_file, "w", encoding="utf-8"):
            pass

        with mock.patch("infield_fly.cli.load_config", return_value=config):
            assert cli.complete_job_id(parsed_args=Namespace(config=None)) == [job.job_id]

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

    def test_completes_series_keywords(self, make_config):
        config = make_config(metadata={"tracked_series": {
            "The Show": {"id": 1, "description": "The Show", "keywords": ["show", "Other"]},
            "undescribed": {"id": 2, "keywords": ["show"]},
            "untracked": {"description": "No ID", "keywords": ["unused"]}
        }})

        with mock.patch("infield_fly.cli.load_config", return_value=config) as load_config:
            keywords = cli.complete_series_keyword(parsed_args=Namespace(config="/settings.json"))

        load_config.assert_called_once_with("/settings.json")
        assert keywords == ["other", "show", "the show"]

    def test_missing_config_file_completes_no_series_keywords(self, tmp_path):
        missing = str(tmp_path / "missing.json")

        assert cli.complete_series_keyword(parsed_args=Namespace(config=missing)) == []


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

    def test_convert_staging_options(self):
        args = self.run_main("convert", "src", "dest", "--stage", "--overwrite",
                             "--progress-file", "/p", command="convert")

        assert (args.stage, args.overwrite, args.progress_file) == (True, True, "/p")

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
         {"search": True, "search_downloads": True, "add_downloads": True,
          "query_downloads": True, "convert": True}),
        (["process", "--no-search", "--no-search-downloads", "--no-add-downloads",
          "--no-query-downloads", "--no-convert"], "process_jobs",
         {"search": False, "search_downloads": False, "add_downloads": False,
          "query_downloads": False, "convert": False}),
        (["process", "--search", "--search-downloads", "--add-downloads", "--query-downloads",
          "--convert"], "process_jobs",
         {"search": True, "search_downloads": True, "add_downloads": True,
          "query_downloads": True, "convert": True}),
    ])
    def test_jobs_commands(self, argv, command, expected):
        args = self.run_main("jobs", *argv, command=command)

        assert {name: getattr(args, name) for name in expected} == expected

    def test_web_defaults(self):
        args = self.run_main("web", command="run_web")

        assert (args.host, args.port, args.root_path, args.allowed_user, args.pam_service,
                args.allow_http, args.browse_roots) == (
                    "127.0.0.1", 8642, "", None, "infield-fly", False, [])

    def test_web_options(self):
        args = self.run_main("web", "--host", "0.0.0.0", "--port", "9000", "--root-path",
                             "/infield-fly", "--allowed-user", "someone", "--pam-service",
                             "login", "--allow-http", "--browse-root", "/a", "--browse-root",
                             "/b", command="run_web")

        assert (args.host, args.port, args.root_path, args.allowed_user, args.pam_service,
                args.allow_http, args.browse_roots) == (
                    "0.0.0.0", 9000, "/infield-fly", "someone", "login", True, ["/a", "/b"])

    def test_missing_settings_file_is_reported(self, tmp_path, capsys):
        missing = str(tmp_path / "missing.json")
        with mock.patch.object(sys, "argv", ["infield-fly", "-c", missing, "jobs", "list"]), \
                mock.patch("infield_fly.cli.list_jobs") as list_jobs, \
                pytest.raises(SystemExit) as exit_info:
            cli.main()

        assert exit_info.value.code == 2
        assert f"Settings file '{missing}' does not exist" in capsys.readouterr().err
        list_jobs.assert_not_called()

    def test_version(self, capsys):
        with mock.patch.object(sys, "argv", ["infield-fly", "--version"]), \
                mock.patch.object(cli, "__version__", "1.2.3"), \
                pytest.raises(SystemExit) as exit_info:
            # The version string is set when the parser is created, inside main.
            cli.main()

        assert exit_info.value.code == 0
        assert capsys.readouterr().out == "infield-fly 1.2.3\n"

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
