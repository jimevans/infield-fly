"""Module for recognizing episode files and the episode numbers in names"""

import os
import re
from typing import NamedTuple

EPISODE_NUMBER_PATTERN = re.compile(r"s([0-9]+)e([0-9]+)", re.IGNORECASE)
EPISODE_FILE_PATTERN = re.compile(r"(.*)s([0-9]+)e([0-9]+)(.*)(\.mkv|\.mp4)$", re.IGNORECASE)


class EpisodeNumber(NamedTuple):

    """An episode number found in text, and where in the text it starts"""

    season: int
    episode: int
    start: int

    def __str__(self):
        return f"s{self.season:02d}e{self.episode:02d}"


def find_episode_number(text):
    """
    Finds the first episode number in text, such as a search string or file name, in the
    form s01e02, ignoring case, or returns None if it has none
    """

    match = EPISODE_NUMBER_PATTERN.search(text or "")
    if match is None:
        return None
    return EpisodeNumber(int(match.group(1)), int(match.group(2)), match.start())


def get_episode_number(text):
    """
    Gets the first episode number in text, such as a search string or file name, in the
    form "s01e02", or None if it has none
    """

    episode_number = find_episode_number(text)
    return str(episode_number) if episode_number is not None else None


def get_converted_file_name(title, substitutions):
    """
    Gets the name of a converted episode file, without its extension, from the episode's
    title, replacing characters as the conversion settings' substitutions say, such as a
    colon with a dash, since some characters can't be used in file names
    """

    # str.translate replaces each character with its entry in the table; maketrans only
    # accepts single characters, which are all a substitution can apply to.
    table = str.maketrans({key: value for key, value in substitutions.items() if len(key) == 1})
    return title.translate(table).strip()


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
