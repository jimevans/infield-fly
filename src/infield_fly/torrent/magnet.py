"""Module for describing torrents given to jobs by magnet link"""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass
class TorrentResult:

    """Describes a torrent found for a job"""

    title: str
    magnet_link: str
    hash: str = None

    @classmethod
    def from_magnet_link(cls, magnet_link, title=None):
        """
        Creates a torrent result for a magnet link, taking the title from the link's
        display name if not specified
        """

        magnet_link_query = (parse_qs(urlparse(magnet_link).query)
                                if magnet_link
                                else None)
        urn_list = ([x for x in magnet_link_query["xt"] if x.startswith("urn:btih:")]
                    if magnet_link_query is not None and "xt" in magnet_link_query
                    else [])
        torrent_hash = urn_list[0][9:] if len(urn_list) > 0 else None
        if title is None:
            title = (magnet_link_query["dn"][0]
                     if magnet_link_query is not None and "dn" in magnet_link_query
                     else "")

        return cls(title, magnet_link, torrent_hash)
