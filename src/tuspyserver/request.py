from __future__ import annotations

import typing
from typing import Optional

if typing.TYPE_CHECKING:
    from tuspyserver.router import TusRouterOptions

from fastapi import HTTPException, Path, Request
from starlette.requests import ClientDisconnect

from tuspyserver.file import TusUploadFile


def make_request_chunks_dep(options: TusRouterOptions):
    async def request_chunks_dep(
        request: Request,
        uuid: str = Path(...),
        post_request: bool = False,
    ) -> Optional[bool]:
        # init file handle
        file = TusUploadFile(uid=uuid, options=options)

        # check if valid file
        if not file.exists or not file.info:
            raise HTTPException(status_code=404, detail="Upload not found")

        # Validate Upload-Offset header matches current file offset
        upload_offset = request.headers.get("upload-offset")
        if upload_offset is not None:
            upload_offset = int(upload_offset)
            if file.info.offset != upload_offset:
                raise HTTPException(status_code=409, detail="Conflict")

        # init variables
        has_chunks = False
        new_params = file.info

        # process chunk stream
        with open(f"{options.files_dir}/{uuid}", "ab") as f:
            try:
                async for chunk in request.stream():
                    has_chunks = True
                    # skip empty chunks but continue processing
                    if len(chunk) == 0:
                        continue
                    # Check if upload would exceed declared size
                    if (
                        new_params.size is not None
                        and new_params.offset + len(chunk) > new_params.size
                    ):
                        raise HTTPException(
                            status_code=400,
                            detail="Upload would exceed declared Upload-Length",
                        )
                    # throw if max size exceeded
                    if new_params.offset + len(chunk) > options.max_size:
                        raise HTTPException(
                            status_code=413,
                            detail="Upload exceeds maximum allowed size",
                        )
                    # write chunk otherwise
                    f.write(chunk)
                    # update upload params
                    new_params.offset += len(chunk)
                    new_params.upload_chunk_size = len(chunk)
                    new_params.upload_part += 1
                    # save updated params
                    file.info = new_params
            except ClientDisconnect:
                return False
            except Exception as e:
                # save the error
                new_params.error = str(e)
                # save updated params
                file.info = new_params

                return False
            finally:
                f.close()

        # For empty files in a POST request, we still want to return True
        # to ensure the file gets created properly
        if post_request and not has_chunks:
            # update params for empty file
            new_params.offset = 0
            new_params.upload_chunk_size = 0
            new_params.upload_part += 1
            # save updated params
            file.info = new_params

        return True

    return request_chunks_dep


def get_request_headers(request: Request, uuid: str, prefix: str = "files") -> dict:
    proto = "http"
    host = request.headers.get("host")

    # Check for forwarded headers first (for proxy setups)
    if request.headers.get("X-Forwarded-Proto") is not None:
        proto = request.headers.get("X-Forwarded-Proto")
    if request.headers.get("X-Forwarded-Host") is not None:
        host = request.headers.get("X-Forwarded-Host")

    # If no forwarded protocol, try to detect scheme from request URL
    # This handles direct HTTPS connections (e.g., Uvicorn with SSL)
    if proto == "http" and not request.headers.get("X-Forwarded-Proto"):
        try:
            # Use request.url.scheme to detect the actual protocol
            if hasattr(request, 'url') and request.url.scheme:
                proto = request.url.scheme
        except Exception:
            # Fallback to default if URL parsing fails
            pass

    # Ensure we have a host
    if not host:
        host = "localhost:8000"  # fallback host

    # Build the full path including root_path and router prefixes
    # FastAPI includes the full mount path in request.url.path
    # We need to construct the base path from the current request path
    current_path = request.url.path.rstrip("/")

    # The current path should end with our prefix (e.g., "/api/files")
    # We want to extract everything before our prefix to build the location header
    clean_prefix = prefix.lstrip("/").rstrip("/")

    # Find where our prefix appears in the current path
    if current_path == f"/{clean_prefix}":
        # Handle case where current_path is exactly our prefix (e.g., "/files")
        # This means there's no parent path in the URL, check for root_path
        root_path = request.scope.get("root_path", "")
        base_path = root_path.rstrip("/") if root_path else ""
    elif current_path.endswith(f"/{clean_prefix}"):
        # Extract the base path (everything before our prefix)
        base_path = current_path[:-len(f"/{clean_prefix}")]
    elif current_path.endswith(clean_prefix) and current_path != clean_prefix:
        # Handle case where current_path doesn't have leading slash
        base_path = current_path[:-len(clean_prefix)].rstrip("/")
    else:
        # Fallback: check for root_path in request scope
        # This handles FastAPI root_path that might not be included in request.url.path
        root_path = request.scope.get("root_path", "")
        if root_path:
            base_path = root_path.rstrip("/")
        else:
            base_path = ""

    # Construct the full path
    if base_path:
        full_path = f"{base_path.rstrip('/')}/{clean_prefix}"
    else:
        full_path = clean_prefix

    # Ensure path starts with /
    if not full_path.startswith("/"):
        full_path = "/" + full_path

    return {
        "location": f"{proto}://{host}{full_path}/{uuid}",
        "proto": proto,
        "host": host,
    }
