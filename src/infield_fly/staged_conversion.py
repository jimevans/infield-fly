"""
Module converting files into the staging directory, and moving them to their destinations
once converted, so that a partly converted file never appears in a destination
"""

import json
import logging
import os
import shutil
import signal
from contextlib import contextmanager

from infield_fly.concurrency import ProcessLock, atomic_write
from infield_fly.conversion import Converter

logger = logging.getLogger(__name__)

STAGING_SUBDIRECTORY = "adhoc"


class ConversionCancelled(Exception):

    """Raised when a staged conversion is stopped by a termination signal"""


def get_staging_directory(config):
    """
    Gets the directory in which staged conversions write their files, apart from the
    files of job conversions
    """

    return os.path.join(config.conversion.staging_directory, STAGING_SUBDIRECTORY)


def write_progress(progress_file, progress):
    """Writes the progress of a staged conversion as JSON, for others to read"""

    if progress_file is not None:
        with atomic_write(progress_file) as output:
            json.dump(progress, output)


@contextmanager
def cancelled_by_termination():
    """Raises ConversionCancelled when the process is asked to terminate while the block runs"""

    def cancel(*_):
        raise ConversionCancelled()

    previous = signal.signal(signal.SIGTERM, cancel)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def convert_staged(config, file_map, settings, *, overwrite=False, progress_file=None,
                   is_unattended_mode=False):
    """
    Converts each (source, destination) pair of a file map into the staging directory, then
    moves the converted files, with any subtitle files written beside them, into the
    destination's directory. Only one staged conversion runs at a time, so LockHeldError is
    raised if another is in progress. FileExistsError is raised before converting anything
    if a destination exists, unless overwrite is set. If a conversion fails or is cancelled,
    its partly written files are deleted. When progress_file is set, the conversion's
    progress is written to it as JSON, and ffmpeg's progress to the same path with an
    .ffmpeg extension.
    """

    with ProcessLock(config.conversion.convert_lock_file, "convert"):
        existing = [destination for _, destination in file_map if os.path.exists(destination)]
        if existing and not overwrite:
            raise FileExistsError(f"Converted files already exist: {', '.join(existing)}")

        os.makedirs(get_staging_directory(config), exist_ok=True)
        with cancelled_by_termination():
            for index, (source, destination) in enumerate(file_map):
                converter = Converter(
                    source,
                    os.path.join(get_staging_directory(config), os.path.basename(destination)),
                    config.conversion.ffmpeg_location, is_unattended_mode)
                write_progress(progress_file, {
                    "file": index + 1, "files": len(file_map), "source": source,
                    "destination": destination,
                    "duration": converter.file_stream_info.duration})
                for output_file in convert_in_staging(converter, settings, progress_file):
                    move_to_destination(output_file, os.path.dirname(destination))


def convert_in_staging(converter, settings, progress_file):
    """
    Converts a file into the staging directory, returning the files written, or deleting
    them if the conversion fails or is cancelled
    """

    ffmpeg_progress_file = f"{progress_file}.ffmpeg" if progress_file is not None else None
    if ffmpeg_progress_file is not None and os.path.exists(ffmpeg_progress_file):
        os.remove(ffmpeg_progress_file)
    try:
        # The staging directory belongs to this conversion, which holds the lock, so files
        # left there by an earlier conversion are replaced.
        return converter.convert_file(settings, overwrite=True,
                                      progress_file=ffmpeg_progress_file)
    except BaseException:
        remove_staged_files(converter, settings)
        raise


def move_to_destination(output_file, destination_directory):
    """Moves a converted file from the staging directory into its destination directory"""

    final_path = os.path.join(destination_directory, os.path.basename(output_file))
    os.makedirs(destination_directory, exist_ok=True)
    shutil.move(output_file, final_path)
    logger.info("Moved converted file to %s", final_path)


def remove_staged_files(converter, settings):
    """Deletes the files a failed or cancelled conversion may have partly written"""

    paths = [converter.output_file] + [
        x.output_file for x in converter.get_track_mappings(settings) if x.output_file]
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
