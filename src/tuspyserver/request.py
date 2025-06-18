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
    if not file.params or not file.exists:
        return False

    # init variables
    has_chunks = False
    new_params = file.params

    # process chunk stream
    with open(f"{options.files_dir}/{uuid}", "ab") as f:
        async for chunk in request.stream():
            has_chunks = True
            # skip empty chunks but continue processing
            if len(chunk) == 0:
                continue
            # throw if max size exceeded
            if len(file) + len(chunk) > options.max_size:
                raise HTTPException(status_code=413)
            # write chunk otherwise
            f.write(chunk)
            # update upload params
            new_params.offset += len(chunk)
            new_params.upload_chunk_size = len(chunk)
            new_params.upload_part += 1
            file.params = new_params

        f.close()

    # For empty files in a POST request, we still want to return True
    # to ensure _get_and_save_the_file gets called
    if post_request and not has_chunks:
        # Update new_paramsdata for empty file
        new_params.offset = 0
        new_params.upload_chunk_size = 0
        new_params.upload_part += 1

        file.params = new_params

    return True


def get_request_headers(request: Request) -> tuple:
    proto = "http"
    host = request.headers.get("host")
    if request.headers.get("X-Forwarded-Proto") is not None:
        proto = request.headers.get("X-Forwarded-Proto")
    if request.headers.get("X-Forwarded-Host") is not None:
        host = request.headers.get("X-Forwarded-Host")
    return {
        "location": f"{proto}://{host}/{options.prefix}/{uuid}",
        "proto": proto,
        "host": host,
    }
