"""
Comprehensive tests for TUS protocol resume/pause functionality.

These tests simulate real-world scenarios including:
- Basic upload flow
- Pause and resume with multiple chunks
- Large file uploads
- Network interruptions
- Offset validation
"""
import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tuspyserver import create_tus_router


class TestTusResumePause:
    """Test TUS protocol resume and pause functionality."""

    @pytest.fixture
    def temp_dir(self) -> Generator[str, None, None]:
        """Create a temporary directory for uploads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def app(self, temp_dir: str) -> FastAPI:
        """Create a FastAPI app with TUS router."""
        app = FastAPI()

        # Track completed uploads for testing
        app.state.completed_uploads = []

        def on_complete_sync(file_path: str, metadata: dict) -> None:
            """Track completed uploads (synchronous version)."""
            app.state.completed_uploads.append({
                "file_path": file_path,
                "metadata": metadata
            })

        router = create_tus_router(
            prefix="/files",
            files_dir=temp_dir,
            max_size=100 * 1024 * 1024,  # 100 MB
            on_upload_complete=on_complete_sync,
            strict_offset_validation=True,  # Enable strict validation
        )

        app.include_router(router)
        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create a test client."""
        return TestClient(app)

    @pytest.fixture
    def large_file_content(self) -> bytes:
        """Generate large file content (10 MB)."""
        # Create 10 MB of data with a pattern for verification
        chunk = b"A" * 1024  # 1 KB
        return chunk * 10 * 1024  # 10 MB

    def create_upload(
        self,
        client: TestClient,
        filename: str,
        file_size: int,
        file_type: str = "audio/wav"
    ) -> tuple[str, dict]:
        """
        Helper to create a new TUS upload.

        Returns:
            Tuple of (upload_id, headers)
        """
        import base64

        # Encode metadata
        metadata_parts = [
            f"filename {base64.b64encode(filename.encode()).decode()}",
            f"filetype {base64.b64encode(file_type.encode()).decode()}",
        ]

        response = client.post(
            "/files",
            headers={
                "Upload-Length": str(file_size),
                "Upload-Metadata": ", ".join(metadata_parts),
                "Tus-Resumable": "1.0.0",
            },
        )

        assert response.status_code == 201, f"Failed to create upload: {response.text}"
        location = response.headers["Location"]
        upload_id = location.split("/")[-1]

        return upload_id, response.headers

    def upload_chunk(
        self,
        client: TestClient,
        upload_id: str,
        chunk: bytes,
        offset: int,
        expected_status: int = 204
    ) -> dict:
        """
        Helper to upload a chunk of data.

        Returns:
            Response headers (lowercase keys for consistency)
        """
        response = client.patch(
            f"/files/{upload_id}",
            content=chunk,
            headers={
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
                "Content-Length": str(len(chunk)),
                "Tus-Resumable": "1.0.0",
            },
        )

        assert response.status_code == expected_status, \
            f"Expected {expected_status}, got {response.status_code}: {response.text}"

        # Convert headers to lowercase for consistent access
        return {k.lower(): v for k, v in response.headers.items()}

    def get_upload_info(self, client: TestClient, upload_id: str) -> dict:
        """
        Get upload information via HEAD request.

        Returns:
            Response headers (lowercase keys for consistency)
        """
        response = client.head(
            f"/files/{upload_id}",
            headers={"Tus-Resumable": "1.0.0"},
        )

        assert response.status_code == 200, f"Failed to get upload info: {response.text}"
        # Convert headers to lowercase for consistent access
        return {k.lower(): v for k, v in response.headers.items()}

    def test_basic_upload_flow(self, client: TestClient):
        """Test basic upload without interruption."""
        # Create upload
        file_content = b"Hello, World!"
        upload_id, _ = self.create_upload(
            client, "test.txt", len(file_content), "text/plain"
        )

        # Upload in one chunk
        headers = self.upload_chunk(client, upload_id, file_content, 0)

        # Verify upload completed
        assert int(headers["upload-offset"]) == len(file_content)

    def test_resume_after_pause(self, client: TestClient):
        """Test resuming upload after pause (simulated)."""
        # Create a 1MB file
        file_content = b"X" * (1024 * 1024)
        upload_id, _ = self.create_upload(client, "large.bin", len(file_content))

        # Upload first chunk (500KB)
        chunk_size = 500 * 1024
        chunk1 = file_content[:chunk_size]
        headers = self.upload_chunk(client, upload_id, chunk1, 0)
        assert int(headers["upload-offset"]) == chunk_size

        # Simulate pause - get current offset
        info = self.get_upload_info(client, upload_id)
        current_offset = int(info["upload-offset"])
        assert current_offset == chunk_size

        # Resume - upload remaining data
        chunk2 = file_content[chunk_size:]
        headers = self.upload_chunk(client, upload_id, chunk2, current_offset)
        assert int(headers["upload-offset"]) == len(file_content)

    def test_multiple_resume_cycles(self, client: TestClient):
        """Test multiple pause/resume cycles."""
        # Create a 2MB file
        file_content = b"Y" * (2 * 1024 * 1024)
        upload_id, _ = self.create_upload(client, "multi.bin", len(file_content))

        # Upload in 4 chunks of 512KB each (totaling 2MB)
        chunk_size = 512 * 1024
        offset = 0
        num_chunks = 4

        for i in range(num_chunks):
            start = i * chunk_size
            end = min(start + chunk_size, len(file_content))
            chunk = file_content[start:end]

            # Verify current offset before uploading
            if i > 0:
                info = self.get_upload_info(client, upload_id)
                assert int(info["upload-offset"]) == offset

            # Upload chunk
            headers = self.upload_chunk(client, upload_id, chunk, offset)
            offset += len(chunk)
            assert int(headers["upload-offset"]) == offset

        # Verify final upload
        assert offset == len(file_content)

    def test_offset_mismatch_rejected(self, client: TestClient):
        """Test that mismatched offsets are rejected with strict validation."""
        file_content = b"Z" * (1024 * 1024)
        upload_id, _ = self.create_upload(client, "offset.bin", len(file_content))

        # Upload first chunk
        chunk_size = 500 * 1024
        chunk1 = file_content[:chunk_size]
        self.upload_chunk(client, upload_id, chunk1, 0)

        # Try to upload with wrong offset - should fail with 409 Conflict
        chunk2 = file_content[chunk_size:]
        wrong_offset = chunk_size + 100  # Incorrect offset

        response = client.patch(
            f"/files/{upload_id}",
            content=chunk2,
            headers={
                "Upload-Offset": str(wrong_offset),
                "Content-Type": "application/offset+octet-stream",
                "Content-Length": str(len(chunk2)),
                "Tus-Resumable": "1.0.0",
            },
        )

        assert response.status_code == 409, \
            "Expected 409 Conflict for offset mismatch"

    def test_large_file_with_pauses(self, client: TestClient, large_file_content: bytes):
        """Test uploading a large file with multiple pauses."""
        file_size = len(large_file_content)
        upload_id, _ = self.create_upload(client, "large_audio.wav", file_size)

        # Upload in chunks with pauses between
        chunk_size = 1024 * 1024  # 1 MB chunks
        offset = 0

        while offset < file_size:
            # Get current offset
            if offset > 0:
                info = self.get_upload_info(client, upload_id)
                current_offset = int(info["upload-offset"])
                assert current_offset == offset, \
                    f"Offset mismatch: expected {offset}, got {current_offset}"

            # Upload next chunk
            end = min(offset + chunk_size, file_size)
            chunk = large_file_content[offset:end]
            headers = self.upload_chunk(client, upload_id, chunk, offset)

            offset = int(headers["upload-offset"])

        # Verify complete upload
        assert offset == file_size

    def test_resume_after_network_interruption(
        self, client: TestClient, app: FastAPI, temp_dir: str
    ):
        """Test resuming after simulated network interruption."""
        file_content = b"N" * (5 * 1024 * 1024)  # 5 MB
        upload_id, _ = self.create_upload(client, "interrupted.bin", len(file_content))

        # Upload first part
        chunk1_size = 2 * 1024 * 1024  # 2 MB
        chunk1 = file_content[:chunk1_size]
        self.upload_chunk(client, upload_id, chunk1, 0)

        # Simulate server restart by creating new client (simulates connection loss)
        # The file and metadata should persist
        new_client = TestClient(app)

        # Check that upload state was preserved
        info = self.get_upload_info(new_client, upload_id)
        assert int(info["upload-offset"]) == chunk1_size
        assert int(info["upload-length"]) == len(file_content)

        # Resume upload with new client
        chunk2 = file_content[chunk1_size:]
        headers = self.upload_chunk(new_client, upload_id, chunk2, chunk1_size)
        assert int(headers["upload-offset"]) == len(file_content)

    def test_concurrent_chunk_uploads(self, client: TestClient):
        """Test that sequential chunks work correctly (no concurrent support)."""
        file_content = b"C" * (2 * 1024 * 1024)
        upload_id, _ = self.create_upload(client, "sequential.bin", len(file_content))

        # Upload chunks sequentially
        chunk_size = 1024 * 1024

        # First chunk
        chunk1 = file_content[:chunk_size]
        headers1 = self.upload_chunk(client, upload_id, chunk1, 0)
        assert int(headers1["upload-offset"]) == chunk_size

        # Second chunk
        chunk2 = file_content[chunk_size:]
        headers2 = self.upload_chunk(client, upload_id, chunk2, chunk_size)
        assert int(headers2["upload-offset"]) == len(file_content)

    def test_zero_byte_file(self, client: TestClient):
        """Test uploading an empty file."""
        upload_id, _ = self.create_upload(client, "empty.txt", 0, "text/plain")

        # For zero-byte files, no PATCH is needed according to TUS spec
        # Just verify the upload exists
        info = self.get_upload_info(client, upload_id)
        assert int(info["upload-length"]) == 0
        assert int(info["upload-offset"]) == 0

    def test_file_integrity_after_resume(
        self, client: TestClient, app: FastAPI, temp_dir: str
    ):
        """Test that file content is correct after multiple resumes."""
        # Create file with recognizable pattern
        pattern = b"ABCDEFGHIJ"
        file_content = pattern * (100 * 1024)  # ~1MB with pattern

        upload_id, _ = self.create_upload(client, "pattern.bin", len(file_content))

        # Upload in 3 chunks
        chunk1 = file_content[:300 * 1024]
        chunk2 = file_content[300 * 1024:700 * 1024]
        chunk3 = file_content[700 * 1024:]

        self.upload_chunk(client, upload_id, chunk1, 0)
        self.upload_chunk(client, upload_id, chunk2, len(chunk1))
        self.upload_chunk(client, upload_id, chunk3, len(chunk1) + len(chunk2))

        # Verify file content
        file_path = Path(temp_dir) / upload_id
        assert file_path.exists()

        with open(file_path, "rb") as f:
            uploaded_content = f.read()

        assert uploaded_content == file_content, "File content mismatch after resume"

    def test_upload_completion_callback(
        self, client: TestClient, app: FastAPI
    ):
        """Test that completion callback is called correctly."""
        file_content = b"Callback test content"
        upload_id, _ = self.create_upload(
            client, "callback.txt", len(file_content), "text/plain"
        )

        # Upload complete file
        self.upload_chunk(client, upload_id, file_content, 0)

        # Verify callback was called
        assert len(app.state.completed_uploads) == 1
        completed = app.state.completed_uploads[0]
        assert upload_id in completed["file_path"]
        assert completed["metadata"]["filename"] == "callback.txt"

    def test_options_endpoint(self, client: TestClient):
        """Test OPTIONS endpoint returns correct TUS capabilities."""
        response = client.options(
            "/files/",
            headers={"Tus-Resumable": "1.0.0"},
        )

        assert response.status_code == 204
        assert "Tus-Version" in response.headers
        assert "Tus-Extension" in response.headers
        assert "Tus-Max-Size" in response.headers
        assert "creation" in response.headers["Tus-Extension"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])