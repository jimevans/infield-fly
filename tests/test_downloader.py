"""Tests for the downloader module"""

import pytest

from infield_fly.configuration import Configuration
from infield_fly.downloader import Downloader


class TestDownloader:

    def test_search_downloads_does_nothing_by_default(self):
        Downloader(Configuration("/install")).search_downloads([object()])

    @pytest.mark.parametrize("method, args", [
        ("recognizes_download", ("location",)),
        ("set_download", (None, "location")),
        ("add_downloads", ([],)),
        ("query_downloads", ([],)),
    ])
    def test_track_methods_must_be_implemented(self, method, args):
        with pytest.raises(NotImplementedError):
            getattr(Downloader(Configuration("/install")), method)(*args)
