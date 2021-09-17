#!/usr/bin/env python3

"""
Module used to update a job's download status.
Note: This script must be callable directly from the shell.
"""

import argparse
from job_queue import JobQueue

parser = argparse.ArgumentParser()
parser.add_argument("torrent_hash", help="Torrent hash")
parser.add_argument("torrent_name", help="Torrent name")
parser.add_argument("torrent_directory", help="Torrent directory")

args = parser.parse_args()

queue = JobQueue()
queue.update_download_job(args.torrent_hash, args.torrent_name, args.torrent_directory)
