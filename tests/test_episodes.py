"""Tests for the episodes module"""

import pytest

from infield_fly.episodes import find_episode_file, get_episode_number
from infield_fly.usenet.downloader import is_job_folder


@pytest.mark.parametrize("text, expected", [
    ("show 1080p s01e02", "s01e02"),
    ("Show.S1E2.mkv", "s01e02"),
    ("show S10E120", "s10e120"),
    ("show special", None),
    ("", None),
    (None, None),
])
def test_get_episode_number(text, expected):
    assert get_episode_number(text) == expected


class TestFindEpisodeFile:

    def test_largest_episode_file_in_folder(self, tmp_path):
        (tmp_path / "show.s01e02.mkv").write_bytes(b"x" * 10)
        (tmp_path / "sample.show.s01e02.mkv").write_bytes(b"x")
        (tmp_path / "show.s01e02.nfo").write_bytes(b"x" * 100)
        (tmp_path / "Subs.s01e02.mp4").mkdir()

        assert find_episode_file(str(tmp_path)) == str(tmp_path / "show.s01e02.mkv")

    def test_episode_file(self, tmp_path):
        (tmp_path / "Show.S01E02.mp4").touch()

        assert find_episode_file(str(tmp_path / "Show.S01E02.mp4")) == str(
            tmp_path / "Show.S01E02.mp4")

    @pytest.mark.parametrize("name", ["notes.txt", "show.mkv", "missing.s01e02.mkv"])
    def test_no_episode_file(self, tmp_path, name):
        (tmp_path / "notes.txt").touch()
        (tmp_path / "show.mkv").touch()

        assert find_episode_file(str(tmp_path / name)) is None


@pytest.mark.parametrize("folder, job_name, expected", [
    ("/complete/Show.S01E02-GROUP", "Show.S01E02-GROUP", True),
    ("/complete/Show.S01E02-GROUP/", "Show.S01E02-GROUP", True),
    ("/complete/Show.S01E02-GROUP.1", "Show.S01E02-GROUP", True),
    ("/complete/Show.S01E02-GROUP.12", "Show.S01E02-GROUP", True),
    ("/complete/Show.S01E02-GROUP.extra", "Show.S01E02-GROUP", False),
    ("/complete/tv", "Show.S01E02-GROUP", False),
    ("/complete/Show.S01E02-GROUP", "", False),
    ("/complete/Show.S01E02-GROUP", None, False),
    ("/complete/ShowXS01E02-GROUP", "Show.S01E02-GROUP", False),
])
def test_is_job_folder(folder, job_name, expected):
    assert is_job_folder(folder, job_name) == expected
