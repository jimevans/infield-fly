#!/usr/bin/env python3

from job_queue import JobQueue

queue = JobQueue.load_from_cache()
queue.perform_conversions()

