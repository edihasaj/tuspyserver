"""Regression tests for path traversal via the Upload-Concat header (CWE-22).

See https://github.com/edihasaj/tuspyserver/issues/93

An attacker could supply a crafted ``Upload-Concat: final; <uid>`` header where the
partial upload UID contained ``../`` sequences (or otherwise escaped the upload
directory), causing the server to read arbitrary files and expose their contents.
"""
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tuspyserver import create_tus_router
from tuspyserver.file import TusUploadFile
from tuspyserver.router import TusRouterOptions


TUS_VERSION = "1.0.0"


@pytest.fixture
def temp_dir() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def secret_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A sensitive file living *outside* the upload directory."""
    secret_dir = tmp_path_factory.mktemp("secret")
    secret = secret_dir / "passwd"
    secret.write_text("root:x:0:0:root:/root:/bin/bash\n")
    return secret


@pytest.fixture
def app(temp_dir: str) -> FastAPI:
    app = FastAPI()
    router = create_tus_router(
        prefix="/files",
        files_dir=temp_dir,
        max_size=100 * 1024 * 1024,
    )
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _options(files_dir: str) -> TusRouterOptions:
    return TusRouterOptions(
        prefix="files",
        files_dir=files_dir,
        max_size=1024,
        auth=lambda: None,
        days_to_keep=5,
        on_upload_complete=None,
        upload_complete_dep=lambda: (lambda *_: None),
        pre_create_hook=None,
        pre_create_dep=lambda: (lambda *_: None),
        file_dep=lambda: (lambda metadata: None),
        tags=None,
        tus_version=TUS_VERSION,
        tus_extension="creation,concatenation",
        strict_offset_validation=False,
    )


class TestTusUploadFileUidValidation:
    """Unit-level checks on the TusUploadFile guard."""

    @pytest.mark.parametrize(
        "malicious_uid",
        [
            "..",
            "../secret",
            "../../etc/passwd",
            "../../../etc/passwd",
            "/etc/passwd",
        ],
    )
    def test_traversal_uid_rejected(self, temp_dir: str, malicious_uid: str) -> None:
        options = _options(temp_dir)
        with pytest.raises(HTTPException) as exc:
            TusUploadFile(options=options, uid=malicious_uid)
        assert exc.value.status_code == 400

    def test_empty_uid_rejected(self, temp_dir: str) -> None:
        options = _options(temp_dir)
        with pytest.raises(HTTPException) as exc:
            TusUploadFile(options=options, uid="")
        assert exc.value.status_code == 400

    def test_valid_uid_accepted(self, temp_dir: str) -> None:
        options = _options(temp_dir)
        # A normal 32-char hex uid must still work.
        file = TusUploadFile(options=options, uid="0123456789abcdef0123456789abcdef")
        assert file.uid == "0123456789abcdef0123456789abcdef"


class TestUploadConcatPathTraversal:
    """End-to-end checks through the HTTP creation route."""

    def test_final_concat_traversal_uid_is_rejected(
        self, client: TestClient, secret_file: Path
    ) -> None:
        # Craft an Upload-Concat header whose partial "uid" escapes files_dir.
        resp = client.post(
            "/files",
            headers={
                "Tus-Resumable": TUS_VERSION,
                "Upload-Concat": "final; ../../secret/passwd",
            },
        )
        # Must not succeed in creating a final upload from an out-of-tree file.
        assert resp.status_code in (400, 404)
        assert resp.status_code != 201

    def test_bare_dotdot_uid_is_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/files",
            headers={
                "Tus-Resumable": TUS_VERSION,
                "Upload-Concat": "final; ..",
            },
        )
        assert resp.status_code in (400, 404)
        assert resp.status_code != 201
