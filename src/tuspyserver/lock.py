"""
File locking utilities for tus uploads.

Provides advisory file locking using fcntl (Unix) to prevent race conditions
during concurrent upload operations. This implementation matches tusd's approach
by using separate lock files stored in a .locks directory, where each lock file
contains the PID of the process holding the lock.
"""
import fcntl
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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
            # Ensure locks directory exists
            os.makedirs(locks_dir, exist_ok=True)
            lock_filename = os.path.basename(file_path) + ".lock"
            self.lock_file_path = os.path.join(locks_dir, lock_filename)
        else:
            # Default: create .locks directory next to upload file
            upload_dir = os.path.dirname(file_path)
            locks_dir = os.path.join(upload_dir, ".locks")
            os.makedirs(locks_dir, exist_ok=True)
            lock_filename = os.path.basename(file_path) + ".lock"
            self.lock_file_path = os.path.join(locks_dir, lock_filename)
        
        self._lock_fd: Optional[int] = None
    
    def acquire(self, blocking: bool = True) -> bool:
        """
        Acquire an exclusive lock on the upload file.
        
        Creates a separate lock file and applies an exclusive lock on it.
        The lock file contains the PID of the process holding the lock.
        This matches tusd's filelocker approach.
        
        Args:
            blocking: If True, block until lock is acquired. If False, return immediately.
            
        Returns:
            True if lock was acquired, False otherwise (only when blocking=False)
        """
        try:
            # Ensure locks directory exists
            os.makedirs(os.path.dirname(self.lock_file_path), exist_ok=True)
            
            # Open the lock file for read-write, create if it doesn't exist
            # Use O_RDWR to allow both reading and writing
            # Use O_CREAT to create the file if it doesn't exist
            self._lock_fd = os.open(self.lock_file_path, os.O_RDWR | os.O_CREAT, 0o644)
            
            # Ensure the lock file contains only the current PID before writing
            try:
                os.ftruncate(self._lock_fd, 0)
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
            except OSError as e:
                logger.warning(f"Failed to truncate lock file {self.lock_file_path}: {e}")

            # Write PID to lock file (like tusd does)
            try:
                pid_str = str(os.getpid()).encode('utf-8')
                os.write(self._lock_fd, pid_str)
                os.fsync(self._lock_fd)  # Ensure PID is written to disk
                # Seek back to beginning for reading
                os.lseek(self._lock_fd, 0, os.SEEK_SET)
            except (IOError, OSError) as e:
                # If writing PID fails, continue anyway - lock is still valid
                logger.warning(f"Failed to write PID to lock file {self.lock_file_path}: {e}")
            
            # Try to acquire exclusive lock (LOCK_EX) on the lock file
            # If blocking=False, use LOCK_NB (non-blocking)
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            
            fcntl.flock(self._lock_fd, flags)
            return True
        except (IOError, OSError) as e:
            # Lock acquisition failed
            if self._lock_fd is not None:
                try:
                    os.close(self._lock_fd)
                except Exception:
                    pass
                self._lock_fd = None
            
            if not blocking and e.errno in (11, 35):  # EAGAIN/EWOULDBLOCK
                return False
            raise
    
    def release(self) -> None:
        """Release the lock and close the file descriptor."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except Exception as e:
                logger.warning(f"Error releasing lock file descriptor: {e}")
            finally:
                self._lock_fd = None
            
            # Remove the lock file (tusd does this)
            # Note: We remove the lock file but keep the .locks directory
            try:
                if os.path.exists(self.lock_file_path):
                    os.remove(self.lock_file_path)
            except Exception as e:
                logger.warning(f"Error removing lock file {self.lock_file_path}: {e}")
    
    def get_fd(self) -> Optional[int]:
        """
        Get the file descriptor for the lock file.
        
        Returns:
            File descriptor if lock is acquired, None otherwise
        """
        return self._lock_fd
    
    def __enter__(self):
        """Context manager entry."""
        self.acquire(blocking=True)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.release()


@contextmanager
def acquire_upload_lock(upload_path: str, locks_dir: Optional[str] = None, blocking: bool = True):
    """
    Context manager for acquiring an upload lock.
    
    This matches tusd's filelocker pattern by creating separate lock files
    in a .locks directory. Each lock file contains the PID of the process
    holding the lock.
    
    Args:
        upload_path: Path to the upload file to lock
        locks_dir: Directory to store lock files (defaults to {upload_dir}/.locks)
        blocking: If True, block until lock is acquired
        
    Yields:
        FileLock instance with the locked file descriptor
        
    Example:
        with acquire_upload_lock("/path/to/upload") as lock:
            # Lock is held via lock file in .locks directory
            # Lock file contains PID of this process
            pass
    """
    lock = FileLock(upload_path, locks_dir=locks_dir)
    try:
        acquired = lock.acquire(blocking=blocking)
        if not acquired:
            raise IOError("Could not acquire lock")
        yield lock
    finally:
        lock.release()
