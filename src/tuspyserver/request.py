from __future__ import annotations
import typing

if typing.TYPE_CHECKING:
    from tuspyserver.router import TusRouterOptions


from fastapi import (
    HTTPException,
    Path,
    Request,
)

from starlette.requests import ClientDisconnect

from tuspyserver.file import TusUploadFile


def make_request_chunks_dep(options: TusRouterOptions):
    async def request_chunks_dep(
        request: Request,
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
    if request.headers.get("X-Forwarded-Proto") is not None:
        proto = request.headers.get("X-Forwarded-Proto")
    if request.headers.get("X-Forwarded-Host") is not None:
        host = request.headers.get("X-Forwarded-Host")

    # Use the provided prefix parameter
    clean_prefix = prefix.lstrip("/").rstrip("/")

    return {
        "location": f"{proto}://{host}/{clean_prefix}/{uuid}",
        "proto": proto,
        "host": host,
    }
