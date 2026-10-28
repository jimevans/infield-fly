"""Module for validating the structure of NZB files before downloading them"""

import re
from dataclasses import dataclass, field
from typing import List
from xml.etree import ElementTree

FILE_NAME_PATTERN = re.compile(r'"([^"]+)"')
CONTENT_FILE_PATTERN = re.compile(
    r"\.(mkv|mp4|m4v|avi|ts|wmv|rar|r\d{2,3}|7z|zip|\d{3})$", re.IGNORECASE)
NON_CONTENT_FILE_PATTERN = re.compile(
    r"\.(par2|nfo|sfv|srr|nzb|txt|jpe?g|png|srt|sub|idx|md5|url|exe)$", re.IGNORECASE)
SIZE_TOLERANCE = 0.1


@dataclass
class NzbValidationResult:

    """The result of validating the structure of an NZB file"""

    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    total_bytes: int = 0

    @property
    def is_valid(self):
        """Gets a value indicating whether the NZB file can be downloaded"""

        return len(self.problems) == 0


def validate_nzb(content, expected_size=0):
    """
    Validates that an NZB file has no gaps in the segments of the files it describes, and
    that it describes video content. This catches NZB files that are structurally broken,
    but not articles that have since been removed from Usenet servers, which the download
    client detects when the download fails.
    """

    result = NzbValidationResult()
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as ex:
        result.problems.append(f"Not a valid NZB file: {ex}")
        return result

    files = [x for x in root if _local_name(x.tag) == "file"]
    if _local_name(root.tag) != "nzb" or len(files) == 0:
        result.problems.append("NZB file lists no files")
        return result

    file_names = []
    for index, file_element in enumerate(files):
        subject = file_element.get("subject", "")
        name_match = FILE_NAME_PATTERN.search(subject)
        name = name_match.group(1) if name_match is not None else None
        file_names.append(name)
        _validate_segments(file_element, name or f"file {index + 1}", result)

    if all(name is not None and NON_CONTENT_FILE_PATTERN.search(name) for name in file_names):
        result.problems.append("NZB file lists no video or archive files")
    if not any(name is not None and name.lower().endswith(".par2") for name in file_names):
        result.warnings.append("NZB file lists no par2 files, so damage cannot be repaired")

    if (expected_size > 0 and result.total_bytes > 0
            and abs(result.total_bytes - expected_size) > expected_size * SIZE_TOLERANCE):
        result.problems.append(
            f"NZB file lists {result.total_bytes} bytes, but the indexer reports "
            f"{expected_size} bytes")

    return result


def _validate_segments(file_element, name, result):
    segments = [x for x in file_element.iter() if _local_name(x.tag) == "segment"]
    if len(segments) == 0:
        result.problems.append(f"'{name}' lists no segments")
        return

    numbers = []
    for segment in segments:
        try:
            numbers.append(int(segment.get("number")))
            segment_bytes = int(segment.get("bytes"))
        except (TypeError, ValueError):
            result.problems.append(f"'{name}' has a segment with an invalid number or size")
            return
        if segment_bytes <= 0:
            result.problems.append(f"'{name}' has an empty segment {numbers[-1]}")
        result.total_bytes += segment_bytes

    if len(set(numbers)) != len(numbers):
        result.problems.append(f"'{name}' lists duplicate segments")

    # The part counts in subjects, as in '"Show.S01E02.mkv" yEnc (1/3412)', are not used,
    # because obfuscated posts randomize them. Only gaps before the last segment are checked.
    missing = set(range(1, max(numbers) + 1)) - set(numbers)
    if len(missing) > 0:
        result.problems.append(
            f"'{name}' is missing {len(missing)} of {max(numbers)} segments")


def _local_name(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""
