"""End-to-end tus protocol tests over the pluggable storage backends.

LocalFileStorage runs everywhere. The S3 backend needs real credentials and is
skipped without them -- it is exercised against OVH Object Storage, whose
multipart semantics (including the 5 MiB minimum part size) match AWS.
"""

import base64
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tuspyserver import create_tus_router
from tuspyserver.storage.local import LocalFileStorage

TUS = {"Tus-Resumable": "1.0.0"}
MiB = 1024 * 1024


def _meta(name="test.bin", ftype="application/octet-stream"):
    b = lambda s: base64.b64encode(s.encode()).decode()
    return f"filename {b(name)},filetype {b(ftype)}"


def _app(storage, completed):
    async def on_complete(path, metadata):
        completed.append((path, metadata))

    app = FastAPI()
    app.include_router(
        create_tus_router(
            prefix="files",
            storage=storage,
            days_to_keep=1,
            on_upload_complete=on_complete,
        )
    )
    return app


def _upload(client, payload, chunk_size):
    r = client.post(
        "/files/",
        headers={**TUS, "Upload-Length": str(len(payload)), "Upload-Metadata": _meta()},
    )
    assert r.status_code == 201, r.text
    uid = r.headers["location"].rstrip("/").split("/")[-1]

    offset = 0
    while offset < len(payload):
        piece = payload[offset : offset + chunk_size]
        r = client.patch(
            f"/files/{uid}",
            headers={
                **TUS,
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            },
            content=piece,
        )
        assert r.status_code == 204, r.text
        offset += len(piece)
        head = client.head(f"/files/{uid}", headers=TUS)
        assert head.status_code == 200
        assert int(head.headers["Upload-Offset"]) == offset, "offset diverged"
    return uid


@pytest.fixture
def local_storage(tmp_path):
    return LocalFileStorage(str(tmp_path / "uploads"))


@pytest.mark.parametrize(
    "size,chunk",
    [
        (12 * MiB, 5 * MiB),   # the new-panel shape: 5 MiB chunks
        (7 * MiB, 1 * MiB),    # third-party client, chunks under the S3 minimum
        (100 * 1024, 100 * 1024),  # single small upload
        (3 * MiB, 64 * 1024),  # many tiny chunks
    ],
)
def test_local_storage_roundtrip(local_storage, size, chunk):
    completed = []
    payload = os.urandom(size)
    with TestClient(_app(local_storage, completed)) as client:
        uid = _upload(client, payload, chunk)

    assert len(completed) == 1, "on_upload_complete should fire exactly once"
    with open(local_storage.location(uid), "rb") as f:
        assert f.read() == payload


def test_local_storage_offset_conflict(local_storage):
    with TestClient(_app(local_storage, [])) as client:
        r = client.post(
            "/files/",
            headers={**TUS, "Upload-Length": "1024", "Upload-Metadata": _meta()},
        )
        uid = r.headers["location"].rstrip("/").split("/")[-1]
        r = client.patch(
            f"/files/{uid}",
            headers={
                **TUS,
                "Upload-Offset": "512",  # wrong: nothing written yet
                "Content-Type": "application/offset+octet-stream",
            },
            content=b"x" * 512,
        )
        assert r.status_code == 409


def test_local_storage_termination(local_storage):
    with TestClient(_app(local_storage, [])) as client:
        payload = b"z" * 2048
        uid = _upload(client, payload, 2048)
        assert client.delete(f"/files/{uid}", headers=TUS).status_code == 204
        assert client.head(f"/files/{uid}", headers=TUS).status_code == 404


def test_default_storage_is_unchanged(tmp_path):
    """No storage= argument must keep the historical filesystem behaviour."""
    from tuspyserver.router import TusRouterOptions

    router = create_tus_router(prefix="files", files_dir=str(tmp_path))
    assert router is not None
    opts = TusRouterOptions(
        prefix="files", files_dir=str(tmp_path), max_size=1, auth=None,
        days_to_keep=1, on_upload_complete=None, upload_complete_dep=None,
        pre_create_hook=None, pre_create_dep=None, file_dep=None, tags=None,
        tus_version="1.0.0", tus_extension="", strict_offset_validation=False,
    )
    assert opts.storage is None


# --- S3 ---------------------------------------------------------------------
_S3_CREDS = os.environ.get("OVH_S3_ACCESS_KEY") and os.environ.get("OVH_S3_SECRET_KEY")


@pytest.mark.skipif(not _S3_CREDS, reason="no S3 credentials in environment")
@pytest.mark.parametrize("size,chunk", [(12 * MiB, 5 * MiB), (7 * MiB, 1 * MiB)])
def test_s3_storage_roundtrip(size, chunk):
    import boto3

    from tuspyserver.storage.s3 import S3Storage

    client = boto3.client(
        "s3",
        endpoint_url="https://s3.eu-west-par.io.cloud.ovh.net",
        region_name="eu-west-par",
        aws_access_key_id=os.environ["OVH_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["OVH_S3_SECRET_KEY"],
    )
    storage = S3Storage(
        bucket="scriptix-staging", client=client, prefix="_verify/tus-e2e/"
    )
    completed = []
    payload = os.urandom(size)
    with TestClient(_app(storage, completed)) as http:
        uid = _upload(http, payload, chunk)

    assert len(completed) == 1
    assert completed[0][0] == f"s3://scriptix-staging/_verify/tus-e2e/{uid}"
    body = client.get_object(Bucket="scriptix-staging", Key=f"_verify/tus-e2e/{uid}")[
        "Body"
    ].read()
    assert body == payload
    client.delete_object(Bucket="scriptix-staging", Key=f"_verify/tus-e2e/{uid}")


# --- lock_factory seam ------------------------------------------------------
def test_s3_storage_uses_injected_lock():
    """A distributed lock must actually be taken, and released, per PATCH.

    S3 has no lock primitive, so a multi-replica deployment depends entirely on
    this hook being honoured; if it silently were not, concurrent PATCHes for
    one upload would interleave and corrupt the offset.
    """
    import contextlib

    from tuspyserver.storage.s3 import S3Storage

    events = []

    @contextlib.asynccontextmanager
    async def factory(uid):
        events.append(("enter", uid))
        try:
            yield
        finally:
            events.append(("exit", uid))

    storage = S3Storage(bucket="b", client=object(), lock_factory=factory)

    async def use():
        async with storage.lock("abc"):
            events.append(("body", "abc"))

    import asyncio

    asyncio.run(use())
    assert events == [("enter", "abc"), ("body", "abc"), ("exit", "abc")]


def test_s3_storage_without_lock_factory_still_works():
    """Omitting the factory keeps the previous in-process behaviour."""
    import asyncio

    from tuspyserver.storage.s3 import S3Storage

    storage = S3Storage(bucket="b", client=object())
    ran = []

    async def use():
        async with storage.lock("abc"):
            ran.append(True)

    asyncio.run(use())
    assert ran == [True]
