"""
File locking utilities for tus uploads.

Provides advisory file locking using fcntl (Unix) to prevent race conditions
during concurrent upload operations. This implementation matches tusd's approach
by using separate lock files stored in a .locks directory, where each lock file
contains the PID of the process holding the lock.

The lock acquisition is bounded by a timeout so a wedged shared filesystem
(e.g. flapping Azure Files / SMB) cannot hang request workers indefinitely.
If the lock directory is unwritable (EACCES / EROFS), we fall back to a
best-effort no-op lock so a single request fails fast instead of dragging
all workers down.
"""
import errno
import fcntl
import logging
import os
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


# Bounded waits for filesystem operations. Tuned for shared SMB mounts where
# operations can briefly stall during reconnects, but must never block forever.
DEFAULT_LOCK_TIMEOUT = 30.0
LOCK_POLL_INTERVAL = 0.1

_FS_UNWRITABLE_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOSPC}


class LockTimeoutError(IOError):
    """Raised when a lock cannot be acquired within the configured timeout."""


class FileLock:
    """
    Advisory file lock using fcntl (Unix) with separate lock files.

    Matches tusd's filelocker approach by creating separate .lock files
    in a .locks directory. Each lock file contains the PID of the process
    holding the lock, allowing for lock cleanup if a process crashes.
    """

    def __init__(self, file_path: str, locks_dir: Optional[str] = None):
        """
        Initialize a file lock.

        Args:
            file_path: Path to the upload file to lock
            locks_dir: Directory to store lock files (defaults to {files_dir}/.locks)
        """
        self.file_path = file_path
        self.locks_dir = locks_dir

        # Derive lock file path from upload file path
        if locks_dir:
            lock_filename = os.path.basename(file_path) + ".lock"
            self.lock_file_path = os.path.join(locks_dir, lock_filename)
        else:
            # Default: create .locks directory next to upload file
            upload_dir = os.path.dirname(file_path)
            self.locks_dir = os.path.join(upload_dir, ".locks")
            lock_filename = os.path.basename(file_path) + ".lock"
            self.lock_file_path = os.path.join(self.locks_dir, lock_filename)

        self._lock_fd: Optional[int] = None
        # Set when the underlying filesystem rejected lock-file creation.
        # Release becomes a no-op so we don't crash on cleanup.
        self._best_effort: bool = False

    def acquire(
        self,
        blocking: bool = True,
        timeout: float = DEFAULT_LOCK_TIMEOUT,
    ) -> bool:
        """
        Acquire an exclusive lock on the upload file.

        Creates a separate lock file and applies an exclusive lock on it.
        The lock file contains the PID of the process holding the lock.
        This matches tusd's filelocker approach.

        Args:
            blocking: If True, wait up to ``timeout`` seconds for the lock.
                If False, return immediately when the lock is contended.
            timeout: Max seconds to wait for the exclusive lock when
                ``blocking=True``. Bounded so a wedged shared filesystem
                cannot hang request workers.

        Returns:
            True if lock was acquired (or running in best-effort mode),
            False only when ``blocking=False`` and the lock was held
            elsewhere.

        Raises:
            LockTimeoutError: ``blocking=True`` and the lock could not be
                acquired within ``timeout`` seconds.
            IOError / OSError: unexpected filesystem failure.
        """
        # Open the lock file. If the lock directory is unwritable (EACCES on
        # a flapping SMB share, read-only filesystem, ...), degrade to
        # best-effort: do not block the request, just proceed without a
        # cross-process lock. Single-pod correctness still holds via the
        # per-process Python state above this layer; multi-pod contention on
        # the same upload UUID is extremely rare in practice.
        try:
            os.makedirs(self.locks_dir, exist_ok=True)
            self._lock_fd = os.open(
                self.lock_file_path, os.O_RDWR | os.O_CREAT, 0o644
            )
        except OSError as e:
            if e.errno in _FS_UNWRITABLE_ERRNOS:
                logger.warning(
                    "Lock dir %s unwritable (%s); proceeding without lock",
                    self.locks_dir, e,
                )
                self._best_effort = True
                self._lock_fd = None
                return True
            raise

        # Best-effort PID write (purely informational, like tusd).
        try:
            os.ftruncate(self._lock_fd, 0)
            os.lseek(self._lock_fd, 0, os.SEEK_SET)
            os.write(self._lock_fd, str(os.getpid()).encode("utf-8"))
            os.lseek(self._lock_fd, 0, os.SEEK_SET)
        except OSError as e:
            logger.warning(
                "Failed to write PID to lock file %s: %s",
                self.lock_file_path, e,
            )

        # Acquire exclusive lock. Use non-blocking + polling so we always have
        # a hard upper bound, even on a misbehaving network filesystem.
        deadline = time.monotonic() + max(timeout, 0.0) if blocking else None
        while True:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    self._close_fd()
                    raise
                if not blocking:
                    self._close_fd()
                    return False
                if time.monotonic() >= deadline:
                    self._close_fd()
                    raise LockTimeoutError(
                        f"Timed out after {timeout:.1f}s waiting for lock "
                        f"{self.lock_file_path}"
                    )
                time.sleep(LOCK_POLL_INTERVAL)

    def _close_fd(self) -> None:
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except Exception:
                pass
            self._lock_fd = None

    def release(self) -> None:
        """Release the lock and close the file descriptor."""
        if self._best_effort:
            self._best_effort = False
            return
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            except Exception as e:
                logger.warning("Error unlocking %s: %s", self.lock_file_path, e)
            self._close_fd()
            try:
                if os.path.exists(self.lock_file_path):
                    os.remove(self.lock_file_path)
            except Exception as e:
                logger.warning(
                    "Error removing lock file %s: %s",
                    self.lock_file_path, e,
                )

    def get_fd(self) -> Optional[int]:
        """File descriptor for the lock file, or None when unlocked / best-effort."""
        return self._lock_fd

    def __enter__(self):
        self.acquire(blocking=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


@contextmanager
def acquire_upload_lock(
    upload_path: str,
    locks_dir: Optional[str] = None,
    blocking: bool = True,
    timeout: float = DEFAULT_LOCK_TIMEOUT,
):
    """
    Context manager for acquiring an upload lock.

    Args:
        upload_path: Path to the upload file to lock.
        locks_dir: Directory to store lock files (defaults to ``{upload_dir}/.locks``).
        blocking: If True, wait up to ``timeout`` seconds for the lock.
        timeout: Max seconds to wait when blocking. Prevents indefinite
            hangs on misbehaving shared filesystems.

    Yields:
        FileLock instance with the locked file descriptor.

    Raises:
        LockTimeoutError: Could not acquire the lock within ``timeout``.
        IOError: ``blocking=False`` and the lock is held elsewhere.
    """
    lock = FileLock(upload_path, locks_dir=locks_dir)
    try:
        acquired = lock.acquire(blocking=blocking, timeout=timeout)
        if not acquired:
            raise IOError("Could not acquire lock")
        yield lock
    finally:
        lock.release()
