"""Infield Fly: an ffmpeg wrapper for media file container conversion"""

from importlib.metadata import PackageNotFoundError, version


def get_version():
    """
    Gets the installed version of Infield Fly, which the build derives from the latest git
    tag, or "unknown" if it isn't installed
    """

    try:
        return version("infield-fly")
    except PackageNotFoundError:
        return "unknown"


__version__ = get_version()
