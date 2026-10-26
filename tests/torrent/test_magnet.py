"""Tests for the torrent.magnet module"""

from infield_fly.torrent.magnet import TorrentResult

MAGNET = "magnet:?xt=urn:btih:ABCDEF&dn=Show.S01E01.1080p"


class TestFromMagnetLink:

    def test_uses_explicit_title(self):
        result = TorrentResult.from_magnet_link(MAGNET, "Title")

        assert result == TorrentResult("Title", MAGNET, "ABCDEF")

    def test_uses_display_name_when_no_title(self):
        result = TorrentResult.from_magnet_link(MAGNET)

        assert result.title == "Show.S01E01.1080p"

    def test_empty_title_when_no_title_or_display_name(self):
        result = TorrentResult.from_magnet_link("magnet:?xt=urn:btih:ABCDEF")

        assert result.title == ""
        assert result.hash == "ABCDEF"

    def test_no_hash_without_btih_urn(self):
        result = TorrentResult.from_magnet_link("magnet:?xt=urn:sha1:XYZ&dn=name")

        assert result.hash is None

    def test_no_hash_without_exact_topic(self):
        result = TorrentResult.from_magnet_link("magnet:?dn=name")

        assert result.hash is None
        assert result.title == "name"

    def test_empty_magnet_with_title(self):
        result = TorrentResult.from_magnet_link("", "Title")

        assert result == TorrentResult("Title", "", None)

    def test_empty_magnet_without_title(self):
        result = TorrentResult.from_magnet_link("")

        assert result == TorrentResult("", "", None)
