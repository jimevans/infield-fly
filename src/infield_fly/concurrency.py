"""
Module containing helpers for sharing files safely between processes, such as a scheduled
job processing run and a manual one
"""

import contextlib
import json
import os
import stat
import uuid
from datetime import datetime

try:
    import fcntl
    msvcrt = None  # pylint: disable=invalid-name
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # pylint: disable=invalid-name
    import msvcrt  # pylint: disable=import-error


@contextlib.contextmanager
def atomic_write(path, binary=False, encoding="utf-8"):
    """
    Opens a temporary file alongside a path for writing, and replaces the path with it
    once the block completes, so that other processes reading the path see either its old
    or its new contents, never a partially written file. If the block raises, the path is
    left unchanged. The permissions of an existing file at the path are kept. The name of
    the temporary file starts with a dot, so readers of the directory can skip it.
    """

    directory, name = os.path.split(os.path.abspath(path))
    temp_path = os.path.join(directory, f".{name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "xb" if binary else "x",
                  encoding=None if binary else encoding) as temp_file:
            yield temp_file
            temp_file.flush()
            os.fsync(temp_file.fileno())
        with contextlib.suppress(FileNotFoundError):
            os.chmod(temp_path, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(temp_path, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(temp_path)
        raise


class LockHeldError(Exception):

    """Raised when a process lock is already held by another holder"""

    def __init__(self, holder):
        self.holder = holder
        super().__init__(f"Lock is held by {describe_lock_holder(holder)}")


def describe_lock_holder(holder):
    """Gets a textual description of the holder of a lock, as recorded in its lock file"""

    if not holder:
        return "another process"
    return (f"'{holder.get('description')}' (process {holder.get('pid')}, "
            f"started {holder.get('started')})")


class ProcessLock:

    """
    An exclusive lock shared between processes, acquired without waiting, using a lock
    file. The operating system releases the lock when the holding process exits, so a
    crashed holder never leaves it stale. While held, the lock file describes the holder.
    """

    def __init__(self, path, description):
        self.path = path
        self.description = description
        self.lock_file = None

    def __enter__(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        # Opening for appending leaves the description of any current holder intact.
        lock_file = open(self.path, "a+", encoding="utf-8")  # pylint: disable=consider-using-with
        try:
            _lock(lock_file)
        except OSError:
            holder = _read_holder(lock_file)
            lock_file.close()
            raise LockHeldError(holder) from None

        lock_file.truncate(0)
        json.dump({"pid": os.getpid(),
                   "description": self.description,
                   "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, lock_file)
        lock_file.flush()
        self.lock_file = lock_file
        return self

    def __exit__(self, *_):
        try:
            self.lock_file.truncate(0)
            _unlock(self.lock_file)
        finally:
            self.lock_file.close()
            self.lock_file = None


def _lock(lock_file):
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:  # pragma: no cover - Windows
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock(lock_file):
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    else:  # pragma: no cover - Windows
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _read_holder(lock_file):
    """Reads the description of the holder of a lock, or None if it cannot be read"""

    try:
        lock_file.seek(0)
        return json.loads(lock_file.read())
    except (OSError, ValueError):
        return None
