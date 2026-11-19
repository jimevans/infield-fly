"""Tests for the concurrency module"""

import json
import os
import stat
import subprocess
import sys

import pytest

from infield_fly.concurrency import (LockHeldError, ProcessLock, atomic_write,
                                     describe_lock_holder)


class TestAtomicWrite:

    def test_writes_new_file(self, tmp_path):
        path = tmp_path / "file.json"

        with atomic_write(str(path)) as output:
            output.write("new")

        assert path.read_text(encoding="utf-8") == "new"
        assert os.listdir(tmp_path) == ["file.json"]

    def test_writes_binary_file(self, tmp_path):
        path = tmp_path / "file.nzb"

        with atomic_write(str(path), binary=True) as output:
            output.write(b"\x00nzb")

        assert path.read_bytes() == b"\x00nzb"

    def test_original_unchanged_until_complete(self, tmp_path):
        path = tmp_path / "file.json"
        path.write_text("old", encoding="utf-8")

        with atomic_write(str(path)) as output:
            output.write("new")
            output.flush()
            assert path.read_text(encoding="utf-8") == "old"

        assert path.read_text(encoding="utf-8") == "new"

    def test_failure_leaves_original_and_removes_temporary_file(self, tmp_path):
        path = tmp_path / "file.json"
        path.write_text("old", encoding="utf-8")

        with pytest.raises(RuntimeError):
            with atomic_write(str(path)) as output:
                output.write("partial")
                raise RuntimeError("failed")

        assert path.read_text(encoding="utf-8") == "old"
        assert os.listdir(tmp_path) == ["file.json"]

    def test_temporary_file_name_starts_with_dot(self, tmp_path):
        with atomic_write(str(tmp_path / "file.json")):
            temporary_files = [x for x in os.listdir(tmp_path) if x != "file.json"]

        assert len(temporary_files) == 1
        assert temporary_files[0].startswith(".file.json.")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
    def test_keeps_permissions_of_existing_file(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("old", encoding="utf-8")
        os.chmod(path, 0o600)

        with atomic_write(str(path)) as output:
            output.write("new")

        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


class TestProcessLock:

    def test_records_holder_while_held(self, tmp_path):
        path = tmp_path / "jobs" / ".lock"

        with ProcessLock(str(path), "jobs process"):
            holder = json.loads(path.read_text(encoding="utf-8"))

        assert holder["pid"] == os.getpid()
        assert holder["description"] == "jobs process"
        assert holder["started"] != ""
        assert path.read_text(encoding="utf-8") == ""

    def test_second_holder_is_refused(self, tmp_path):
        path = str(tmp_path / ".lock")

        with ProcessLock(path, "first"):
            with pytest.raises(LockHeldError) as error:
                with ProcessLock(path, "second"):
                    pytest.fail("Lock acquired twice")

        assert error.value.holder["description"] == "first"
        assert error.value.holder["pid"] == os.getpid()

    def test_unreadable_holder_is_described_as_unknown(self, tmp_path):
        path = str(tmp_path / ".lock")

        with ProcessLock(path, "first") as lock:
            lock.lock_file.truncate(0)
            lock.lock_file.write("not json")
            lock.lock_file.flush()
            with pytest.raises(LockHeldError) as error:
                with ProcessLock(path, "second"):
                    pytest.fail("Lock acquired twice")

        assert error.value.holder is None
        assert str(error.value) == "Lock is held by another process"

    def test_released_lock_can_be_reacquired(self, tmp_path):
        path = str(tmp_path / ".lock")

        with ProcessLock(path, "first"):
            pass
        with ProcessLock(path, "second"):
            pass

    def test_lock_held_by_another_process_is_refused(self, tmp_path):
        path = str(tmp_path / ".lock")
        holder_script = ("import sys\n"
                         "from infield_fly.concurrency import ProcessLock\n"
                         "with ProcessLock(sys.argv[1], 'other'):\n"
                         "    print('locked', flush=True)\n"
                         "    sys.stdin.readline()\n")
        with subprocess.Popen([sys.executable, "-c", holder_script, path],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True) as other:
            try:
                assert other.stdout.readline() == "locked\n"
                with pytest.raises(LockHeldError) as error:
                    with ProcessLock(path, "this"):
                        pytest.fail("Lock held by another process acquired")
            finally:
                other.communicate("\n", timeout=10)

        assert error.value.holder["pid"] == other.pid
        with ProcessLock(path, "this"):
            pass

    @pytest.mark.parametrize("holder, expected", [
        (None, "another process"),
        ({"pid": 12, "description": "jobs process", "started": "2026-01-02 03:04:05"},
         "'jobs process' (process 12, started 2026-01-02 03:04:05)"),
    ])
    def test_describe_lock_holder(self, holder, expected):
        assert describe_lock_holder(holder) == expected
