import os
import time

import pytest

from tuspyserver.lock import (
    DEFAULT_LOCK_TIMEOUT,
    FileLock,
    LockTimeoutError,
    acquire_upload_lock,
)


def test_lock_basic_acquire_release(tmp_path):
    upload = tmp_path / "u1"
    upload.write_bytes(b"")
    with acquire_upload_lock(str(upload), locks_dir=str(tmp_path / ".locks")):
        pass
    # lock file should be cleaned up
    assert not (tmp_path / ".locks" / "u1.lock").exists()


def test_lock_times_out_when_held(tmp_path):
    upload = tmp_path / "u2"
    upload.write_bytes(b"")
    locks = str(tmp_path / ".locks")
    holder = FileLock(str(upload), locks_dir=locks)
    holder.acquire(blocking=True)
    try:
        contender = FileLock(str(upload), locks_dir=locks)
        start = time.monotonic()
        with pytest.raises(LockTimeoutError):
            contender.acquire(blocking=True, timeout=0.5)
        elapsed = time.monotonic() - start
        assert 0.4 <= elapsed < 2.0
    finally:
        holder.release()


def test_lock_non_blocking_returns_false(tmp_path):
    upload = tmp_path / "u3"
    upload.write_bytes(b"")
    locks = str(tmp_path / ".locks")
    holder = FileLock(str(upload), locks_dir=locks)
    holder.acquire(blocking=True)
    try:
        contender = FileLock(str(upload), locks_dir=locks)
        assert contender.acquire(blocking=False) is False
    finally:
        holder.release()


def test_lock_best_effort_when_dir_unwritable(tmp_path):
    # Make a read-only locks dir parent so makedirs/open fails with EACCES
    ro_root = tmp_path / "ro"
    ro_root.mkdir()
    locks = ro_root / ".locks"
    upload = tmp_path / "u4"
    upload.write_bytes(b"")
    os.chmod(ro_root, 0o500)  # r-x: cannot create children
    try:
        lock = FileLock(str(upload), locks_dir=str(locks))
        # Should NOT raise; falls back to best-effort.
        assert lock.acquire(blocking=True, timeout=0.5) is True
        assert lock.get_fd() is None
        lock.release()  # must not raise
    finally:
        os.chmod(ro_root, 0o700)


def test_default_timeout_is_bounded():
    assert DEFAULT_LOCK_TIMEOUT < 120
