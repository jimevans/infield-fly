"""Tests for the usenet.validation module"""

import pytest

from infield_fly.usenet.validation import validate_nzb


def make_file(name, total, numbers=None, segment_bytes=1000):
    """Creates an NZB file element for a file posted in the specified number of segments"""

    numbers = range(1, total + 1) if numbers is None else numbers
    segments = "".join(f'<segment bytes="{segment_bytes}" number="{x}">{name}{x}@news</segment>'
                       for x in numbers)
    subject = f'[1/2] - "{name}" yEnc (1/{total})' if total else name
    return (f'<file poster="poster" date="0" subject=\'{subject}\'>'
            f"<groups><group>alt.binaries.test</group></groups>"
            f"<segments>{segments}</segments></file>")


def make_nzb(*files):
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
            f'{"".join(files)}</nzb>').encode()


COMPLETE_NZB = make_nzb(make_file("Show.S01E02.mkv", 3), make_file("Show.S01E02.par2", 1))


class TestValidateNzb:

    def test_complete_nzb(self):
        result = validate_nzb(COMPLETE_NZB)

        assert result.is_valid
        assert (result.problems, result.warnings, result.total_bytes) == ([], [], 4000)

    def test_without_namespace(self):
        content = f"<nzb>{make_file('Show.S01E02.mkv', 1)}</nzb>".encode()

        assert validate_nzb(content).is_valid

    @pytest.mark.parametrize("content, expected", [
        (b"not xml", "Not a valid NZB file"),
        (make_nzb(), "NZB file lists no files"),
        (b"<rss><file/></rss>", "NZB file lists no files"),
        (make_nzb(make_file("Show.S01E02.mkv", 3, [1, 3])),
         "'Show.S01E02.mkv' is missing 1 of 3 segments"),
        (make_nzb(make_file("Show.S01E02.mkv", 3, [1, 2, 2, 3])),
         "'Show.S01E02.mkv' lists duplicate segments"),
        (make_nzb(make_file("Show.S01E02.mkv", 2, segment_bytes=0)),
         "'Show.S01E02.mkv' has an empty segment 1"),
        (make_nzb(make_file("Show.S01E02.mkv", 2, ["x"])),
         "'Show.S01E02.mkv' has a segment with an invalid number or size"),
        (make_nzb(make_file("Show.S01E02.mkv", 2, [])), "'Show.S01E02.mkv' lists no segments"),
        (make_nzb(make_file("Show.S01E02.nfo", 1), make_file("Show.S01E02.par2", 1)),
         "NZB file lists no video or archive files"),
    ])
    def test_problems(self, content, expected):
        result = validate_nzb(content)

        assert not result.is_valid
        assert any(x.startswith(expected) for x in result.problems), result.problems

    def test_detects_gap_without_part_count_in_subject(self):
        result = validate_nzb(make_nzb(make_file("obfuscated", 0, [1, 2, 4])))

        assert result.problems == ["'file 1' is missing 1 of 4 segments"]

    def test_ignores_randomized_part_count_in_subject(self):
        # Obfuscated posts claim more parts in their subjects than they were posted in.
        file_element = make_file("a1b2c3d4", 3).replace("(1/3)", "(1/83)")

        assert validate_nzb(make_nzb(file_element, make_file("x.par2", 1))).is_valid

    def test_obfuscated_names_are_not_rejected_as_non_video(self):
        result = validate_nzb(make_nzb(make_file("a1b2c3d4", 2), make_file("x.par2", 1)))

        assert result.is_valid

    def test_missing_par2_is_only_a_warning(self):
        result = validate_nzb(make_nzb(make_file("Show.S01E02.mkv", 2)))

        assert result.is_valid
        assert result.warnings == [
            "NZB file lists no par2 files, so damage cannot be repaired"]

    @pytest.mark.parametrize("expected_size, is_valid", [
        (0, True), (4000, True), (4400, True), (4500, False), (3500, False)])
    def test_compares_size_to_expected_size(self, expected_size, is_valid):
        result = validate_nzb(COMPLETE_NZB, expected_size)

        assert result.is_valid == is_valid
