from __future__ import annotations

from copy import deepcopy
import datetime
import os
import typing
from typing import Callable, Optional

import inspect

if typing.TYPE_CHECKING:
    from tuspyserver.router import TusRouterOptions

from fastapi import Depends, HTTPException, Path, Request
from starlette.requests import ClientDisconnect

from tuspyserver.file import TusUploadFile
from tuspyserver.lock import acquire_upload_lock


def make_request_chunks_dep(options: TusRouterOptions):
    async def request_chunks_dep(
        request: Request,
        uuid: str = Path(...),
        post_request: bool = False,
        file_dep: Callable[[dict], None] = Depends(options.file_dep),
    ) -> Optional[bool]:
        # Create a copy of options to avoid mutating the original
        file_options = deepcopy(options)
        # call file_dep to possibly update the files_dir
        result = file_dep({})
        # if the callback returned a coroutine, await it
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            file_options.files_dir = result.get("files_dir", file_options.files_dir)

        # init file handle
        file = TusUploadFile(uid=uuid, options=file_options)
        # check if valid file exists BEFORE acquiring lock
        if not file.exists or not file.info:
            raise HTTPException(status_code=404, detail="Upload not found")
        
        # Ensure uploads directory exists before locking
        uploads_dir = file_options.files_dir
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Create locks directory path (matches tusd's .locks directory)
        locks_dir = os.path.join(uploads_dir, ".locks")
        
        # Acquire lock BEFORE any validation or reading to prevent race conditions
        # This ensures we have exclusive access during the entire write operation
        upload_path = file.path
        try:
            with acquire_upload_lock(upload_path, locks_dir=locks_dir, blocking=True):
                # Re-read info file while holding lock to get latest offset
                # This prevents TOCTOU race condition
                file._info._loaded = False  # Force reload
                current_info = file.info
                
                if current_info is None:
                    raise HTTPException(status_code=404, detail="Upload not found")
                
                # Check if upload has expired (distinguish from non-existent uploads)
                if current_info.expires:
                    expires_dt = None
                    if isinstance(current_info.expires, str):
                        try:
                            # Try RFC 7231 format first (e.g., "Mon, 02 Jan 2006 15:04:05 GMT")
                            from email.utils import parsedate_to_datetime
                            expires_dt = parsedate_to_datetime(current_info.expires)
                        except (ValueError, TypeError):
                            # Fallback to ISO format
                            try:
                                expires_dt = datetime.datetime.fromisoformat(current_info.expires.replace('Z', '+00:00'))
                            except (ValueError, AttributeError):
                                pass
                    elif isinstance(current_info.expires, (int, float)):
                        expires_dt = datetime.datetime.fromtimestamp(current_info.expires)
                    
                    if expires_dt:
                        now = datetime.datetime.now(expires_dt.tzinfo) if expires_dt.tzinfo else datetime.datetime.now()
                        if expires_dt < now:
                            raise HTTPException(status_code=410, detail="Upload expired")

                # Validate Upload-Offset header matches current file offset (required for PATCH)
                # Use the offset we just read while holding the lock
                upload_offset = request.headers.get("upload-offset")
                if upload_offset is not None:
                    try:
                        upload_offset = int(upload_offset)
                        if current_info.offset != upload_offset:
                            raise HTTPException(status_code=409, detail="Offset mismatch")
                    except (ValueError, TypeError):
                        raise HTTPException(status_code=400, detail="Invalid Upload-Offset header")
                else:
                    # Upload-Offset is required for PATCH
                    raise HTTPException(status_code=400, detail="Upload-Offset header is required")

                # Use the validated offset directly - don't re-read from file.info
                # This prevents stale offset issues
                new_params = current_info
                validated_offset = current_info.offset

                # init variables
                has_chunks = False
                total_bytes_written = 0

                # process chunk stream - lock is held during entire operation
                with open(upload_path, "ab") as f:
                    try:
                        async for chunk in request.stream():
                            has_chunks = True
                            # skip empty chunks but continue processing
                            if len(chunk) == 0:
                                continue
                            
                            # Calculate new offset based on validated offset + bytes written so far
                            new_offset = validated_offset + total_bytes_written
                            
                            # Check if upload would exceed declared size
                            if (
                                new_params.size is not None
                                and new_offset + len(chunk) > new_params.size
                            ):
                                raise HTTPException(
                                    status_code=400,
                                    detail="Upload would exceed declared Upload-Length",
                                )
                            # throw if max size exceeded
                            if new_offset + len(chunk) > options.max_size:
                                raise HTTPException(
                                    status_code=413,
                                    detail="Upload exceeds maximum allowed size",
                                )
                            # write chunk otherwise
                            f.write(chunk)
                            f.flush()  # Ensure data is written to disk immediately
                            total_bytes_written += len(chunk)
                        
                        # After all chunks are written, update params atomically
                        # Lock is still held here, so this is safe
                        new_params.offset = validated_offset + total_bytes_written
                        new_params.upload_chunk_size = total_bytes_written if total_bytes_written > 0 else 0
                        new_params.upload_part += 1
                        # Save updated params (atomic write while lock is held)
                        file.info = new_params
                        
                    except HTTPException:
                        # HTTPExceptions should propagate - don't catch them here
                        # The lock will be released by the context manager
                        raise
                    except ClientDisconnect:
                        # Save the current offset before returning, so resume works correctly
                        # Lock is still held, so this is safe
                        new_params.offset = validated_offset + total_bytes_written
                        new_params.upload_chunk_size = total_bytes_written if total_bytes_written > 0 else 0
                        new_params.upload_part += 1
                        file.info = new_params
                        return False
                    except Exception as e:
                        # save the error
                        new_params.error = str(e)
                        new_params.offset = validated_offset + total_bytes_written
                        # save updated params
                        file.info = new_params
                        return False
                    finally:
                        f.close()

                # For empty files in a POST request, we still want to return True
                # to ensure the file gets created properly
                # Lock is still held here
                if post_request and not has_chunks:
                    # update params for empty file
                    new_params.offset = validated_offset
                    new_params.upload_chunk_size = 0
                    new_params.upload_part += 1
                    # save updated params
                    file.info = new_params
        except (IOError, OSError) as e:
            # Lock acquisition or file operation failed
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

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
