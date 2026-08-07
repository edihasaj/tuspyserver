import os
import subprocess
import sys
import textwrap
import time

import pytest

import tuspyserver.lock as lock_module
from tuspyserver.lock import (
    DEFAULT_LOCK_TIMEOUT,
    FileLock,
    LockTimeoutError,
    acquire_upload_lock,
    locking_available,
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


def test_package_imports_without_fcntl():
    """Regression for #99: fcntl does not exist on Windows."""
    code = textwrap.dedent(
        """
        import sys

        class BlockFcntl:
            def find_spec(self, name, path=None, target=None):
                if name == "fcntl":
                    raise ImportError("No module named 'fcntl'")
                return None

        sys.modules.pop("fcntl", None)
        sys.meta_path.insert(0, BlockFcntl())

        import tuspyserver  # noqa: F401
        import tuspyserver.lock as lock

        assert lock.fcntl is None
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


class _FakeMsvcrt:
    """Minimal stand-in for the Windows msvcrt locking API."""

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self):
        self.calls = []
        self.locked_fds = set()

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, nbytes))
        if mode == self.LK_NBLCK:
            if self.locked_fds:
                raise OSError(13, "Permission denied")
            self.locked_fds.add(fd)
        elif mode == self.LK_UNLCK:
            self.locked_fds.discard(fd)


def test_windows_backend_acquires_and_releases(tmp_path, monkeypatch):
    fake = _FakeMsvcrt()
    monkeypatch.setattr(lock_module, "fcntl", None)
    monkeypatch.setattr(lock_module, "msvcrt", fake)

    upload = tmp_path / "win1"
    upload.write_bytes(b"")
    lock = FileLock(str(upload), locks_dir=str(tmp_path / ".locks"))
    assert lock.acquire(blocking=True, timeout=0.5) is True
    assert fake.locked_fds
    lock.release()
    assert not fake.locked_fds
    assert (fake.LK_NBLCK, 1) in fake.calls
    assert (fake.LK_UNLCK, 1) in fake.calls


def test_windows_backend_reports_contention(tmp_path, monkeypatch):
    fake = _FakeMsvcrt()
    monkeypatch.setattr(lock_module, "fcntl", None)
    monkeypatch.setattr(lock_module, "msvcrt", fake)

    upload = tmp_path / "win2"
    upload.write_bytes(b"")
    locks = str(tmp_path / ".locks")
    holder = FileLock(str(upload), locks_dir=locks)
    holder.acquire(blocking=True)
    try:
        contender = FileLock(str(upload), locks_dir=locks)
        assert contender.acquire(blocking=False) is False
        with pytest.raises(LockTimeoutError):
            contender.acquire(blocking=True, timeout=0.3)
    finally:
        holder.release()


def test_lock_works_without_any_locking_primitive(tmp_path, monkeypatch):
    monkeypatch.setattr(lock_module, "fcntl", None)
    monkeypatch.setattr(lock_module, "msvcrt", None)
    assert lock_module.locking_available() is False

    upload = tmp_path / "nolock"
    upload.write_bytes(b"")
    with acquire_upload_lock(str(upload), locks_dir=str(tmp_path / ".locks")):
        pass
    assert not (tmp_path / ".locks" / "nolock.lock").exists()


def test_locking_available_on_this_platform():
    assert locking_available() is True
