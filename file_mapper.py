"""Module for mapping file names"""

import os
import re


class FileMapper:

    """Maps file names to a Plex-friendly format"""

    def __init__(self,
                 series_metadata,
                 file_name_match_regex = r"(.*)s([0-9]+)e([0-9]+)(.*)\.mkv"):
        super().__init__()
        self.file_name_match_regex = file_name_match_regex
        self.series_metadata = series_metadata

    def map_files(self, source, destination):
        """Maps a file given a source and destination, handling individual files and directories"""

        file_map = []
        if os.path.isdir(source):
            src_dir = os.path.dirname(source)
            dest_dir = destination
            if not os.path.isdir(destination):
                dest_dir = os.path.dirname(destination)
            file_list = os.listdir(src_dir)
            file_list.sort()
            for input_file in file_list:
                match = re.match(self.file_name_match_regex, input_file, re.IGNORECASE)
                if match is not None:
                    episode_metadata = self.series_metadata.get_episode(
                        int(match.group(2)), int(match.group(3)))
                    if episode_metadata is not None:
                        converted_file_name = "{}.mp4".format(episode_metadata.plex_title)
                        file_map.append((os.path.join(src_dir, input_file),
                                         os.path.join(dest_dir, converted_file_name)))
        else:
            dest_file = destination
            if os.path.isdir(destination):
                source_file_name = os.path.basename(source)
                source_file_base, _ = os.path.splitext(source_file_name)
                dest_file = os.path.join(destination, source_file_base + ".mp4")
            file_map.append((source, dest_file))

        return file_map
