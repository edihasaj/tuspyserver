"""Regression: when on_upload_complete is an async (coroutine) function,
the PATCH route must await it. Previously the result was discarded, so
the coroutine ran only up to its first await point and the rest of the
hook body never executed."""
import asyncio
import tempfile
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tuspyserver import create_tus_router


@pytest.fixture
def tmp_files_dir() -> Any:
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_app(files_dir: str, hook: Any) -> FastAPI:
    app = FastAPI()
    router = create_tus_router(
        prefix="/files",
        files_dir=files_dir,
        max_size=10 * 1024 * 1024,
        on_upload_complete=hook,
    )
    app.include_router(router)
    return app


def _do_upload(client: TestClient, payload: bytes) -> str:
    resp = client.post(
        "/files",
        headers={
            "Tus-Resumable": "1.0.0",
            "Upload-Length": str(len(payload)),
            "Upload-Metadata": "filename ZmlsZW5hbWU=",
        },
    )
    assert resp.status_code == 201, resp.text
    location = resp.headers["Location"]
    upload_id = location.rstrip("/").rsplit("/", 1)[-1]

    resp = client.patch(
        f"/files/{upload_id}",
        content=payload,
        headers={
            "Tus-Resumable": "1.0.0",
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
        },
    )
    assert resp.status_code == 204, resp.text
    return upload_id


def test_sync_on_upload_complete_is_called(tmp_files_dir: str) -> None:
    """Baseline: a synchronous hook still works."""
    called: list[tuple[str, dict]] = []

    def hook(file_path: str, metadata: dict) -> None:
        called.append((file_path, metadata))

    app = _make_app(tmp_files_dir, hook)
    with TestClient(app) as client:
        _do_upload(client, b"hello world")

    assert len(called) == 1, "sync hook should be called exactly once"


def test_async_on_upload_complete_is_awaited(tmp_files_dir: str) -> None:
    """Async on_upload_complete must run to completion, not just to its
    first await point. Before the fix, the returned coroutine was
    silently discarded and `done` was never set."""
    done = asyncio.Event()
    invocations: list[tuple[str, dict]] = []

    async def hook(file_path: str, metadata: dict) -> None:
        # Yield once so the bug (no await) would skip everything after this.
        await asyncio.sleep(0)
        invocations.append((file_path, metadata))
        done.set()

    app = _make_app(tmp_files_dir, hook)
    with TestClient(app) as client:
        _do_upload(client, b"hello world")

    assert done.is_set(), (
        "async on_upload_complete was not awaited — the request returned "
        "before the hook finished. This was the bug."
    )
    assert len(invocations) == 1
