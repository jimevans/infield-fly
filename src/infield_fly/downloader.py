"""Module containing the interface of a download track, such as BitTorrent or Usenet"""

import logging


class Downloader:

    """
    Base class for a download track, which finds downloads for jobs, adds them to its
    download client, and marks jobs pending conversion once their downloads finish. Jobs
    are assigned to a track by their downloader value, which matches the track's name.
    """

    name = None

    def __init__(self, configuration):
        super().__init__()
        self.config = configuration
        self.logger = logging.getLogger()

    def search_downloads(self, jobs):
        """
        Searches for downloads for waiting jobs, moving the jobs for which one is found to
        adding. Tracks that cannot search leave the jobs unchanged.
        """

    def recognizes_download(self, location):
        """
        Gets a value indicating whether a download specified by the user, such as a magnet
        link or the path or URL of a file, belongs to this track
        """

        raise NotImplementedError

    def set_download(self, job, location):
        """
        Sets a download specified by the user as the download for a job, moving the job to
        adding. Returns a list of warnings about the download, or raises ValueError if it
        cannot be used.
        """

        raise NotImplementedError

    def add_downloads(self, jobs):
        """Adds the downloads of adding jobs to the download client, moving them to downloading"""

        raise NotImplementedError

    def query_downloads(self, jobs):
        """Moves downloading jobs whose downloads have finished in the download client to pending"""

        raise NotImplementedError
