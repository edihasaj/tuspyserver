from __future__ import annotations
import typing

if typing.TYPE_CHECKING:
    from tuspyserver.router import TusRouterOptions


from fastapi import (
    HTTPException,
    Path,
    Request,
)

from tuspyserver.file import TusUploadFile


async def get_request_chunks(
    request: Request,
    options: TusRouterOptions,
    uuid: str = Path(...),
    post_request: bool = False,
) -> bool | None:
    # init file handle
    file = TusUploadFile(uid=uuid, options=options)

    # check if valid file
    if not file.exists or not file.info:
        raise HTTPException(status_code=404, detail="Upload not found")

    # init variables
    has_chunks = False
    new_params = file.info

    # process chunk stream
    with open(f"{options.files_dir}/{uuid}", "ab") as f:
        async for chunk in request.stream():
            has_chunks = True
            # skip empty chunks but continue processing
            if len(chunk) == 0:
                continue
            # Check if upload would exceed declared size
            if new_params.size is not None and new_params.offset + len(chunk) > new_params.size:
                raise HTTPException(
                    status_code=400,
                    detail="Upload would exceed declared Upload-Length"
                )
            # throw if max size exceeded
            if new_params.offset + len(chunk) > options.max_size:
                raise HTTPException(status_code=413, detail="Upload exceeds maximum allowed size")
            # write chunk otherwise
            f.write(chunk)
            # update upload params
            new_params.offset += len(chunk)
            new_params.upload_chunk_size = len(chunk)
            new_params.upload_part += 1

        f.close()

    # For empty files in a POST request, we still want to return True
    # to ensure the file gets created properly
    if post_request and not has_chunks and new_params.size == 0:
        # Update params for empty file
        new_params.offset = 0
        new_params.upload_chunk_size = 0
        new_params.upload_part += 1

    # Save updated params
    file.info = new_params

    return True


def get_request_headers(request: Request, uuid: str) -> dict:
    proto = "http"
    host = request.headers.get("host")
    if request.headers.get("X-Forwarded-Proto") is not None:
        proto = request.headers.get("X-Forwarded-Proto")
    if request.headers.get("X-Forwarded-Host") is not None:
        host = request.headers.get("X-Forwarded-Host")
    
    # Get the prefix from the request path
    path_parts = request.url.path.split('/')
    prefix = path_parts[1] if len(path_parts) > 1 else "files"
    
    return {
        "location": f"{proto}://{host}/{prefix}/{uuid}",
        "proto": proto,
        "host": host,
    }
