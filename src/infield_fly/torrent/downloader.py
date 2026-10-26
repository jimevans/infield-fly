"""Module for downloading torrents given to jobs by magnet link using Deluge"""

from deluge_client import DelugeRPCClient

from infield_fly.downloader import Downloader
from infield_fly.job_status import JobStatus
from infield_fly.torrent.magnet import TorrentResult


class TorrentDownloader(Downloader):

    """Download track adding torrents to, and querying them from, a Deluge daemon"""

    name = "deluge"

    def recognizes_download(self, location):
        """Gets a value indicating whether a download is a magnet link"""

        return location.startswith("magnet:")

    def set_download(self, job, location):
        """Sets a magnet link as the download for a job"""

        self.set_torrent_result(job, TorrentResult.from_magnet_link(location))
        return []

    def set_torrent_result(self, job, search_result):
        """Sets the torrent found for a job"""

        job.status = JobStatus.ADDING
        job.downloader = self.name
        job.magnet_link = search_result.magnet_link
        job.title = search_result.title
        job.torrent_hash = search_result.hash
        job.save(self.logger)

    def add_downloads(self, jobs):
        """Adds the magnet links of jobs to the Deluge client"""

        if len(jobs) == 0:
            self.logger.info("No torrents to add during job processing")
            return

        self.logger.info("Adding downloads to Deluge instance on %s",
                         self.config.conversion.deluge_host)
        with self.connect() as client:
            for job in jobs:
                encoded_id, metadata = client.core.prefetch_magnet_metadata(job.magnet_link)
                if encoded_id is None:
                    # Leave the job in the adding state so it is retried on the next run.
                    self.logger.error(
                        "Magnet metadata prefetch failed for job %s, no ID returned", job.job_id)
                    continue
                if metadata is None:
                    self.logger.warning(
                        "Magnet metadata prefetch returned no metadata for job %s; "
                        "using torrent name", job.job_id)
                encoded_name = (metadata.get("name".encode(), None)
                                if metadata is not None
                                else None)
                torrent_id = encoded_id.decode()
                client.core.add_torrent_magnet(job.magnet_link, {})
                torrent = client.core.get_torrent_status(
                    torrent_id, ["name", "download_location", "is_finished"])
                job.torrent_hash = torrent_id
                torrent_name = (encoded_name.decode()
                                if encoded_name is not None
                                else torrent["name".encode()].decode())
                job.set_download_status(JobStatus.DOWNLOADING,
                                        torrent_name,
                                        torrent["download_location".encode()].decode())
                job.save(self.logger)

    def query_downloads(self, jobs):
        """Marks jobs whose torrents have finished downloading in the Deluge client as pending"""

        if len(jobs) == 0:
            self.logger.info("No downloading torrents to query for status during job "
                             "processing")
            return

        self.logger.info("Marking completed downloads on Deluge instance at %s",
                         self.config.conversion.deluge_host)
        with self.connect() as client:
            for job in jobs:
                torrent = client.core.get_torrent_status(
                    job.torrent_hash, ["name", "download_location", "is_finished"])
                if torrent.get("is_finished".encode(), False):
                    job.set_download_status(JobStatus.PENDING, torrent["name".encode()].decode())
                    job.save(self.logger)

    def connect(self):
        """Creates a client connection to the Deluge daemon"""

        return DelugeRPCClient(self.config.conversion.deluge_host,
                               self.config.conversion.deluge_port,
                               self.config.conversion.deluge_user_name,
                               self.config.conversion.deluge_password)
