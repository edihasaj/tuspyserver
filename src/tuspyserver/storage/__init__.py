"""Pluggable storage backends for tus uploads.

By default tuspyserver writes uploads to the local filesystem, which is what
every existing deployment gets and what ``TusRouterOptions.storage = None``
means. Backends here are strictly opt-in.

The protocol is async on purpose. A tus route is an ``async def``, so any
blocking filesystem or network call made from it runs on the event loop and
stalls every other request in the worker -- including the health endpoint an
orchestrator uses to decide whether the pod is alive. Backends must therefore
keep real I/O off the loop (``asyncio.to_thread`` is enough).
"""

from __future__ import annotations

import abc
from typing import AsyncIterator, Dict, List, Optional


class TusStorage(abc.ABC):
    """Backend contract for persisting tus upload data and metadata.

    Implementations own two namespaces keyed by upload id: the upload *data*
    and its *info* sidecar (the serialized ``TusUploadParams``). Both are
    addressed only by ``uid``; nothing above this layer may assume a
    filesystem path exists.
    """

    # A backend is a shared service, not a value. The routes deepcopy their
    # options to let file_dep override files_dir per request; copying the
    # backend along with them would split its in-flight buffers and locks
    # (and boto3 clients are not copyable at all).
    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self

    # --- data -----------------------------------------------------------
    @abc.abstractmethod
    async def create(self, uid: str) -> None:
        """Begin a new, empty upload. Must be idempotent."""

    @abc.abstractmethod
    async def exists(self, uid: str) -> bool: ...

    @abc.abstractmethod
    async def size(self, uid: str) -> int:
        """Bytes durably accepted so far -- the tus ``Upload-Offset``."""

    @abc.abstractmethod
    async def append(self, uid: str, chunk: bytes) -> None:
        """Append ``chunk`` at the current end of the upload.

        Called once per chunk of a streaming PATCH body, in order.
        """

    @abc.abstractmethod
    async def flush(self, uid: str) -> None:
        """Make everything passed to ``append`` durable and visible to
        ``size``. Called at the end of each PATCH, before the info sidecar is
        updated, so a resumed upload always sees a truthful offset."""

    @abc.abstractmethod
    async def finalize(self, uid: str) -> None:
        """Seal a fully-received upload. Called once, when offset == length.

        For object stores this is where a multipart upload is completed. For
        the local backend it is a no-op.
        """

    @abc.abstractmethod
    async def read(self, uid: str) -> Optional[bytes]: ...

    @abc.abstractmethod
    async def delete(self, uid: str) -> None:
        """Remove upload data, info sidecar and any backend scratch state."""

    # --- info sidecar ---------------------------------------------------
    @abc.abstractmethod
    async def read_info(self, uid: str) -> Optional[Dict]: ...

    @abc.abstractmethod
    async def write_info(self, uid: str, data: Dict) -> None:
        """Persist the info sidecar. Must be atomic: a torn sidecar makes an
        upload unresumable."""

    # --- housekeeping ---------------------------------------------------
    @abc.abstractmethod
    async def list_uids(self) -> List[str]:
        """Every known upload id, for expiry collection."""

    @abc.abstractmethod
    def location(self, uid: str) -> str:
        """Opaque handle handed to ``on_upload_complete``.

        The local backend returns a filesystem path (unchanged behaviour).
        Object stores return a URI such as ``s3://bucket/key`` -- completion
        hooks must not assume the value is openable with ``open()``.
        """

    @abc.abstractmethod
    def lock(self, uid: str) -> "AsyncIterator[None]":
        """Async context manager giving exclusive access to ``uid``.

        PATCH is read-modify-write on the offset, so concurrent requests for
        one upload must serialize or the offset silently diverges from the
        bytes actually stored.
        """


__all__ = ["TusStorage"]
