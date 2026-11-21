"""Module for recognizing episode files and the episode numbers in names"""

import os
import re

EPISODE_NUMBER_PATTERN = re.compile(r"s([0-9]+)e([0-9]+)", re.IGNORECASE)
EPISODE_FILE_PATTERN = re.compile(r"(.*)s([0-9]+)e([0-9]+)(.*)(\.mkv|\.mp4)$", re.IGNORECASE)


def get_episode_number(text):
    """
    Gets the episode number in text, such as a search string or file name, in the form
    "s01e02", or None if it has none
    """

    match = EPISODE_NUMBER_PATTERN.search(text or "")
    if match is None:
        return None
    return f"s{int(match.group(1)):02d}e{int(match.group(2)):02d}"


def find_episode_file(download_path):
    """
    Gets the episode file to convert from a download, which is a file, or a folder whose
    largest episode file is converted, since downloads can include samples or other extras
    whose names also match. Episode files are .mkv or .mp4 files named with their episode
    number, such as s01e02. Returns None if the download has no episode file.
    """

    if os.path.isdir(download_path):
        directory = download_path
        names = os.listdir(download_path)
    else:
        directory, name = os.path.split(download_path)
        names = [name]
    episode_files = [os.path.join(directory, x) for x in names
                     if EPISODE_FILE_PATTERN.match(x)
                     and os.path.isfile(os.path.join(directory, x))]
    return max(episode_files, key=os.path.getsize, default=None)
