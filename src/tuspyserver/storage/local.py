"""Local-filesystem storage backend.

Reproduces the on-disk layout tuspyserver has always used -- ``<files_dir>/<uid>``
for data and ``<files_dir>/<uid>.info`` for the sidecar -- so pointing an
existing deployment at this backend picks up uploads already on disk.

The difference from the historical inline implementation is that every
syscall is dispatched with ``asyncio.to_thread``. On a network filesystem an
``os.makedirs`` or ``open`` can block for seconds; doing that on the event loop
stalls the whole worker, health endpoint included.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

from tuspyserver.file import _validate_uid_within_dir
from tuspyserver.lock import acquire_upload_lock
from tuspyserver.storage import TusStorage


class LocalFileStorage(TusStorage):
    """Store uploads as plain files under ``files_dir``."""

    def __init__(self, files_dir: str) -> None:
        self.files_dir = os.path.abspath(os.path.expanduser(files_dir))
        self._pending: Dict[str, bytearray] = defaultdict(bytearray)

    def _path(self, uid: str) -> str:
        _validate_uid_within_dir(self.files_dir, uid)
        return os.path.join(self.files_dir, uid)

    def _info_path(self, uid: str) -> str:
        return f"{self._path(uid)}.info"

    def location(self, uid: str) -> str:
        return self._path(uid)

    def _ensure_dir(self) -> None:
        os.makedirs(self.files_dir, exist_ok=True)

    # --- data -----------------------------------------------------------
    async def create(self, uid: str) -> None:
        def _do() -> None:
            self._ensure_dir()
            open(self._path(uid), "a").close()

        await asyncio.to_thread(_do)

    async def exists(self, uid: str) -> bool:
        return await asyncio.to_thread(lambda: os.path.exists(self._path(uid)))

    async def size(self, uid: str) -> int:
        def _do() -> int:
            p = self._path(uid)
            return os.path.getsize(p) if os.path.exists(p) else 0

        return await asyncio.to_thread(_do)

    async def append(self, uid: str, chunk: bytes) -> None:
        self._pending[uid].extend(chunk)

    async def flush(self, uid: str) -> None:
        pending = bytes(self._pending.pop(uid, b""))
        if not pending:
            return

        def _do() -> None:
            self._ensure_dir()
            with open(self._path(uid), "ab") as f:
                f.write(pending)
                f.flush()
                os.fsync(f.fileno())

        await asyncio.to_thread(_do)

    async def finalize(self, uid: str) -> None:
        """Nothing to seal -- the file is already complete on disk."""
        return None

    async def read(self, uid: str) -> Optional[bytes]:
        def _do() -> Optional[bytes]:
            p = self._path(uid)
            if not os.path.exists(p):
                return None
            with open(p, "rb") as f:
                return f.read()

        return await asyncio.to_thread(_do)

    async def delete(self, uid: str) -> None:
        def _do() -> None:
            for p in (self._path(uid), self._info_path(uid)):
                with contextlib.suppress(OSError):
                    if os.path.exists(p):
                        os.remove(p)

        self._pending.pop(uid, None)
        await asyncio.to_thread(_do)

    # --- info sidecar ---------------------------------------------------
    async def read_info(self, uid: str) -> Optional[Dict]:
        def _do() -> Optional[Dict]:
            p = self._info_path(uid)
            if not os.path.exists(p):
                return None
            try:
                with open(p, "r") as f:
                    content = f.read().strip()
                return json.loads(content) if content else None
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                return None

        return await asyncio.to_thread(_do)

    async def write_info(self, uid: str, data: Dict) -> None:
        def _do() -> None:
            self._ensure_dir()
            path = self._info_path(uid)
            tmp = f"{path}.tmp"
            try:
                with open(tmp, "w") as f:
                    f.write(json.dumps(data, indent=4, default=str))
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp, path)  # atomic on POSIX
            except Exception:
                with contextlib.suppress(OSError):
                    if os.path.exists(tmp):
                        os.remove(tmp)
                raise

        await asyncio.to_thread(_do)

    # --- housekeeping ---------------------------------------------------
    async def list_uids(self) -> List[str]:
        def _do() -> List[str]:
            if not os.path.isdir(self.files_dir):
                return []
            return [f for f in os.listdir(self.files_dir) if len(f) == 32]

        return await asyncio.to_thread(_do)

    @contextlib.asynccontextmanager
    async def lock(self, uid: str):
        """Advisory file lock, shared with any other process using this dir."""
        locks_dir = os.path.join(self.files_dir, ".locks")
        path = self._path(uid)
        cm = await asyncio.to_thread(
            lambda: acquire_upload_lock(path, locks_dir=locks_dir, blocking=True)
        )
        await asyncio.to_thread(cm.__enter__)
        try:
            yield
        finally:
            await asyncio.to_thread(cm.__exit__, None, None, None)


__all__ = ["LocalFileStorage"]
