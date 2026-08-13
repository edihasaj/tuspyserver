"""Storage-backed equivalent of :class:`~tuspyserver.file.TusUploadFile`.

The routes were written against a synchronous, filesystem-shaped object:
``file.exists``, ``file.info``, ``len(file)``. A storage backend is async, so
this class loads that state once in :meth:`open` and then answers the sync
accessors from memory. Mutations are buffered and written by :meth:`save`.

Only used when ``TusRouterOptions.storage`` is set; the default filesystem
path still goes through ``TusUploadFile`` unchanged.
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from tuspyserver.params import TusUploadParams
from tuspyserver.storage import TusStorage


class StorageUploadFile:
    """Mirrors the ``TusUploadFile`` surface on top of a ``TusStorage``."""

    def __init__(self, storage: TusStorage, options, uid: str) -> None:
        self._storage = storage
        self._options = options
        self.uid = uid
        self._params: Optional[TusUploadParams] = None
        self._exists = False
        self._size = 0

    # --- construction ---------------------------------------------------
    @classmethod
    async def open(
        cls,
        storage: TusStorage,
        options,
        uid: Optional[str] = None,
        params: Optional[TusUploadParams] = None,
    ) -> "StorageUploadFile":
        creating = uid is None
        self = cls(storage, options, uid or uuid4().hex)

        if creating:
            await storage.create(self.uid)
            self._exists = True
        else:
            self._exists = await storage.exists(self.uid)
            if params is not None and not self._exists:
                await storage.create(self.uid)
                self._exists = True

        if params is not None:
            self._params = params
            await storage.write_info(self.uid, params.model_dump())
        else:
            raw = await storage.read_info(self.uid)
            if raw is not None:
                try:
                    self._params = TusUploadParams(**raw)
                except (TypeError, ValueError):
                    self._params = None

        self._size = await storage.size(self.uid)
        return self

    async def reload(self) -> None:
        """Re-read info and size, e.g. after taking the lock."""
        raw = await self._storage.read_info(self.uid)
        self._params = TusUploadParams(**raw) if raw else None
        self._size = await self._storage.size(self.uid)

    # --- TusUploadFile-compatible surface -------------------------------
    @property
    def storage(self) -> TusStorage:
        return self._storage

    @property
    def options(self):
        return self._options

    @property
    def path(self) -> str:
        """Backend handle, not necessarily a filesystem path."""
        return self._storage.location(self.uid)

    @property
    def exists(self) -> bool:
        return self._exists

    @property
    def info(self) -> Optional[TusUploadParams]:
        return self._params

    @info.setter
    def info(self, value) -> None:
        # Buffered: callers must await save() to persist.
        self._params = value

    def __len__(self) -> int:
        return self._size

    # --- async operations -----------------------------------------------
    async def save(self) -> None:
        if self._params is not None:
            await self._storage.write_info(self.uid, self._params.model_dump())

    async def read(self) -> Optional[bytes]:
        return await self._storage.read(self.uid)

    async def remove(self) -> None:
        await self._storage.delete(self.uid)
        self._exists = False

    async def finalize(self) -> None:
        await self._storage.finalize(self.uid)


__all__ = ["StorageUploadFile"]
