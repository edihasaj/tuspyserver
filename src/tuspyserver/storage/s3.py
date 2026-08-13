"""S3 (and S3-compatible) storage backend -- opt-in.

Uploads stream straight into an S3 multipart upload, so no shared filesystem
is involved and any replica can serve any chunk of any upload. Verified
against AWS semantics and OVH Object Storage.

Layout per upload id::

    <prefix><uid>          final object, created by CompleteMultipartUpload
    <prefix><uid>.info     tus info sidecar (JSON)
    <prefix><uid>.mpu      multipart UploadId, so a PATCH can resume
    <prefix><uid>.part     bytes not yet big enough to be a part (see below)

The awkward constraint is that S3 requires every part except the last to be at
least 5 MiB, while tus lets a client PATCH any number of bytes it likes. Bytes
below the threshold are therefore parked in the ``.part`` object and prepended
to the next PATCH, exactly as tusd does it. Clients that already send >= 5 MiB
chunks never touch that path.

boto3 is synchronous, so every call is dispatched with ``asyncio.to_thread``
to keep the event loop free.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import defaultdict
from typing import Any, AsyncContextManager, Callable, Dict, List, Optional

from tuspyserver.storage import TusStorage

#: S3 rejects CompleteMultipartUpload with EntityTooSmall if any part other
#: than the last is below this. Not configurable downwards.
S3_MIN_PART_SIZE = 5 * 1024 * 1024

#: A multipart upload may have at most this many parts, which together with
#: the part size sets the largest uploadable file.
S3_MAX_PARTS = 10_000

_SAFE_UID = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def _check_uid(uid: str) -> str:
    """Reject anything that could escape the key prefix (CWE-22)."""
    if not uid or not _SAFE_UID.match(uid):
        raise ValueError(f"unsafe upload id: {uid!r}")
    return uid


class S3Storage(TusStorage):
    """Store tus uploads as S3 multipart uploads.

    :param bucket: destination bucket, must already exist.
    :param client: a configured ``boto3`` S3 client. Passing it in keeps this
        class free of credential and endpoint handling, and lets an app reuse
        the client it already has.
    :param prefix: key prefix for every object written.
    :param part_size: bytes buffered before a part is flushed. Must be at
        least ``S3_MIN_PART_SIZE``. Larger means fewer requests but more
        memory held per in-flight upload.
    :param lock_factory: optional ``uid -> async context manager`` providing a
        cross-process lock. Without one, PATCHes are serialized only within
        this process, which is not enough when several replicas can receive
        chunks of the same upload -- see :meth:`lock`.
    """

    def __init__(
        self,
        bucket: str,
        client: Any,
        prefix: str = "tus/",
        part_size: int = S3_MIN_PART_SIZE,
        lock_factory: Optional[Callable[[str], AsyncContextManager[None]]] = None,
    ) -> None:
        if part_size < S3_MIN_PART_SIZE:
            raise ValueError(
                f"part_size must be >= {S3_MIN_PART_SIZE} bytes (S3 minimum); got {part_size}"
            )
        self.bucket = bucket
        self.client = client
        self.prefix = prefix
        self.part_size = part_size
        self._lock_factory = lock_factory
        # Bytes received during the current PATCH, not yet flushed to S3.
        self._pending: Dict[str, bytearray] = defaultdict(bytearray)
        self._locks: Dict[str, asyncio.Lock] = {}

    # --- key helpers ----------------------------------------------------
    def _key(self, uid: str) -> str:
        return f"{self.prefix}{_check_uid(uid)}"

    def _info_key(self, uid: str) -> str:
        return f"{self._key(uid)}.info"

    def _mpu_key(self, uid: str) -> str:
        return f"{self._key(uid)}.mpu"

    def _part_key(self, uid: str) -> str:
        return f"{self._key(uid)}.part"

    def location(self, uid: str) -> str:
        return f"s3://{self.bucket}/{self._key(uid)}"

    # --- low level ------------------------------------------------------
    async def _get(self, key: str) -> Optional[bytes]:
        def _do() -> Optional[bytes]:
            try:
                return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except self.client.exceptions.NoSuchKey:
                return None
            except Exception as exc:  # 404 surfaces as ClientError on some backends
                if _is_not_found(exc):
                    return None
                raise

        return await asyncio.to_thread(_do)

    async def _put(self, key: str, body: bytes) -> None:
        await asyncio.to_thread(
            lambda: self.client.put_object(Bucket=self.bucket, Key=key, Body=body)
        )

    async def _delete_key(self, key: str) -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                lambda: self.client.delete_object(Bucket=self.bucket, Key=key)
            )

    async def _upload_id(self, uid: str, create: bool = False) -> Optional[str]:
        raw = await self._get(self._mpu_key(uid))
        if raw:
            return raw.decode()
        if not create:
            return None
        key = self._key(uid)
        resp = await asyncio.to_thread(
            lambda: self.client.create_multipart_upload(Bucket=self.bucket, Key=key)
        )
        upload_id = resp["UploadId"]
        await self._put(self._mpu_key(uid), upload_id.encode())
        return upload_id

    async def _parts(self, uid: str, upload_id: str) -> List[Dict]:
        """All parts uploaded so far. S3 is the source of truth for offset,
        so nothing needs to be mirrored in the sidecar."""

        def _do() -> List[Dict]:
            out: List[Dict] = []
            marker = 0
            while True:
                resp = self.client.list_parts(
                    Bucket=self.bucket,
                    Key=self._key(uid),
                    UploadId=upload_id,
                    PartNumberMarker=marker,
                )
                out.extend(resp.get("Parts", []))
                if not resp.get("IsTruncated"):
                    return out
                marker = resp["NextPartNumberMarker"]

        return await asyncio.to_thread(_do)

    # --- TusStorage -----------------------------------------------------
    async def create(self, uid: str) -> None:
        await self._upload_id(uid, create=True)

    async def exists(self, uid: str) -> bool:
        if await self._get(self._mpu_key(uid)) is not None:
            return True

        def _head() -> bool:
            try:
                self.client.head_object(Bucket=self.bucket, Key=self._key(uid))
                return True
            except Exception as exc:
                if _is_not_found(exc):
                    return False
                raise

        return await asyncio.to_thread(_head)

    async def size(self, uid: str) -> int:
        upload_id = await self._upload_id(uid)
        if upload_id is None:
            # Already finalized: the object itself carries the length.
            def _head() -> int:
                try:
                    return self.client.head_object(
                        Bucket=self.bucket, Key=self._key(uid)
                    )["ContentLength"]
                except Exception as exc:
                    if _is_not_found(exc):
                        return 0
                    raise

            return await asyncio.to_thread(_head)

        parts = await self._parts(uid, upload_id)
        tail = await self._get(self._part_key(uid))
        return sum(p["Size"] for p in parts) + (len(tail) if tail else 0)

    async def append(self, uid: str, chunk: bytes) -> None:
        self._pending[_check_uid(uid)].extend(chunk)

    async def flush(self, uid: str) -> None:
        pending = bytes(self._pending.pop(uid, b""))
        if not pending:
            return

        upload_id = await self._upload_id(uid, create=True)
        assert upload_id is not None

        tail = await self._get(self._part_key(uid)) or b""
        buf = tail + pending

        parts = await self._parts(uid, upload_id)
        next_part = max((p["PartNumber"] for p in parts), default=0) + 1

        # Emit as many full-size parts as the buffer allows; whatever is left
        # is below the S3 minimum and has to wait for more bytes.
        offset = 0
        while len(buf) - offset >= self.part_size:
            if next_part > S3_MAX_PARTS:
                raise ValueError(
                    f"upload {uid} exceeds the {S3_MAX_PARTS}-part S3 limit at "
                    f"part_size={self.part_size}; raise part_size"
                )
            body = buf[offset : offset + self.part_size]
            pn = next_part
            await asyncio.to_thread(
                lambda b=body, p=pn: self.client.upload_part(
                    Bucket=self.bucket,
                    Key=self._key(uid),
                    PartNumber=p,
                    UploadId=upload_id,
                    Body=b,
                )
            )
            offset += self.part_size
            next_part += 1

        remainder = buf[offset:]
        if remainder:
            await self._put(self._part_key(uid), remainder)
        elif tail:
            await self._delete_key(self._part_key(uid))

    async def finalize(self, uid: str) -> None:
        upload_id = await self._upload_id(uid)
        if upload_id is None:
            return  # already finalized

        # The trailing bytes are the last part, so the 5 MiB floor does not
        # apply to them.
        tail = await self._get(self._part_key(uid))
        parts = await self._parts(uid, upload_id)
        if tail:
            pn = max((p["PartNumber"] for p in parts), default=0) + 1
            await asyncio.to_thread(
                lambda: self.client.upload_part(
                    Bucket=self.bucket,
                    Key=self._key(uid),
                    PartNumber=pn,
                    UploadId=upload_id,
                    Body=tail,
                )
            )
            parts = await self._parts(uid, upload_id)

        if not parts:
            # Zero-byte upload: multipart cannot express it, write directly.
            await asyncio.to_thread(
                lambda: self.client.abort_multipart_upload(
                    Bucket=self.bucket, Key=self._key(uid), UploadId=upload_id
                )
            )
            await self._put(self._key(uid), b"")
        else:
            ordered = sorted(parts, key=lambda p: p["PartNumber"])
            await asyncio.to_thread(
                lambda: self.client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=self._key(uid),
                    UploadId=upload_id,
                    MultipartUpload={
                        "Parts": [
                            {"PartNumber": p["PartNumber"], "ETag": p["ETag"]}
                            for p in ordered
                        ]
                    },
                )
            )

        await self._delete_key(self._part_key(uid))
        await self._delete_key(self._mpu_key(uid))

    async def read(self, uid: str) -> Optional[bytes]:
        return await self._get(self._key(uid))

    async def delete(self, uid: str) -> None:
        upload_id = await self._upload_id(uid)
        if upload_id:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    lambda: self.client.abort_multipart_upload(
                        Bucket=self.bucket, Key=self._key(uid), UploadId=upload_id
                    )
                )
        self._pending.pop(uid, None)
        for key in (
            self._part_key(uid),
            self._mpu_key(uid),
            self._info_key(uid),
            self._key(uid),
        ):
            await self._delete_key(key)

    async def read_info(self, uid: str) -> Optional[Dict]:
        raw = await self._get(self._info_key(uid))
        if not raw:
            return None
        try:
            return json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    async def write_info(self, uid: str, data: Dict) -> None:
        # A single PUT is already atomic in S3 -- no temp-and-rename needed.
        await self._put(self._info_key(uid), json.dumps(data, default=str).encode())

    async def list_uids(self) -> List[str]:
        def _do() -> List[str]:
            uids: List[str] = []
            token = None
            while True:
                kwargs = {"Bucket": self.bucket, "Prefix": self.prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = self.client.list_objects_v2(**kwargs)
                for obj in resp.get("Contents", []):
                    name = obj["Key"][len(self.prefix) :]
                    if name.endswith(".info"):
                        uids.append(name[: -len(".info")])
                if not resp.get("IsTruncated"):
                    return uids
                token = resp.get("NextContinuationToken")

        return await asyncio.to_thread(_do)

    @contextlib.asynccontextmanager
    async def lock(self, uid: str):
        """Serialize PATCHes for one upload.

        PATCH is read-modify-write on the offset, so two concurrent requests
        for one upload must not interleave. The in-process lock below is only
        enough for a single-replica deployment: S3 has no lock primitive, so
        with several replicas the tus ``Upload-Offset`` precondition is the
        only remaining guard, and that check is itself read-then-write.

        Pass ``lock_factory`` to close that gap with a real distributed lock.
        Both are taken when a factory is present -- the local one still saves a
        network round trip for same-process contention.
        """
        local = self._locks.setdefault(uid, asyncio.Lock())
        async with local:
            if self._lock_factory is None:
                yield
                return
            async with self._lock_factory(uid):
                yield


def _is_not_found(exc: Exception) -> bool:
    resp = getattr(exc, "response", None)
    if not isinstance(resp, dict):
        return False
    code = str(resp.get("Error", {}).get("Code", ""))
    status = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"404", "NoSuchKey", "NoSuchUpload", "NotFound"} or status == 404


__all__ = ["S3Storage", "S3_MIN_PART_SIZE", "S3_MAX_PARTS"]
