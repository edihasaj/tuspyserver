import base64
import inspect
import os
from datetime import datetime, timedelta
from typing import Callable, Optional

from pydantic import BaseModel

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse


from tuspyserver.file import TusUploadFile, TusUploadParams
from tuspyserver.request import get_request_headers, get_request_chunks


class TusRouterOptions(BaseModel):
    prefix: str
    files_dir: str
    max_size: int
    auth: Callable[[], None] | None
    days_to_keep: int
    on_upload_complete: Callable[[str, dict], None] | None
    upload_complete_dep: Callable[..., Callable[[str, dict], None]] | None
    tags: list[str] | None
    tus_version: str
    tus_extension: str


async def noop():
    pass


def create_tus_router(
    prefix: str = "files",
    files_dir="/tmp/files",
    max_size=128849018880,
    auth: Optional[Callable[[], None]] = noop,
    days_to_keep: int = 5,
    on_upload_complete: Optional[Callable[[str, dict], None]] = None,
    upload_complete_dep: Optional[Callable[..., Callable[[str, dict], None]]] = None,
    tags: Optional[list[str]] = None,
):
    if prefix and prefix[0] == "/":
        prefix = prefix[1:]

    upload_complete_dep = upload_complete_dep or (
        lambda _: on_upload_complete or (lambda *_: None)
    )

    options = TusRouterOptions(
        prefix=prefix,
        files_dir=files_dir,
        max_size=max_size,
        auth=auth,
        days_to_keep=days_to_keep,
        on_upload_complete=on_upload_complete,
        upload_complete_dep=upload_complete_dep,
        tags=tags,
        tus_version="1.0.0",
        tus_extension=",".join(
            [
                "creation",
                "creation-defer-length",
                "creation-with-upload",
                "expiration",
                "termination",
            ]
        ),
    )

    router = APIRouter(
        prefix=f"/{options.prefix}",
        redirect_slashes=True,
        tags=options.tags or ["Tus"],
    )

    # CORE ROUTES

    # inform client of upload status
    @router.head("/{uuid}", status_code=status.HTTP_200_OK)
    def core_head_route(
        response: Response, uuid: str, _=Depends(options.auth)
    ) -> Response:
        file = TusUploadFile(uid=uuid, options=options)

        if file.options is None or not file.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        response.headers["Tus-Resumable"] = file.options.tus_version
        response.headers["Content-Length"] = str(file.info.size)
        response.headers["Upload-Length"] = str(file.info.size)
        response.headers["Upload-Offset"] = str(file.info.offset)
        response.headers["Cache-Control"] = "no-store"

        if "filename" in file.info.metadata:
            fn = file.info.metadata["filename"]
        elif "name" in file.info.metadata:
            fn = file.info.metadata["name"]
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload-file.metadata missing required field: filename",
            )

        if "filetype" in file.info.metadata:
            ft = file.info.metadata["filetype"]
        elif "type" in file.info.metadata:
            ft = file.info.metadata["type"]
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload-Metadata missing required field: filetype",
            )

        def b64(s: str) -> str:
            return base64.b64encode(s.encode("utf-8")).decode("ascii")

        response.headers["Upload-Metadata"] = f"filename {b64(fn)}, filetype {b64(ft)}"

        response.status_code = status.HTTP_200_OK
        return response

    # allow client to upload a chunk
    @router.patch("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    async def core_patch_route(
        response: Response,
        uuid: str,
        content_length: int = Header(None),
        upload_offset: int = Header(None),
        _=Depends(get_request_chunks),
        __=Depends(options.auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
    ) -> Response:
        file = TusUploadFile(uid=uuid, options=options)

        # check if the upload ID is valid
        if not file.params or uuid != file.uid:
            raise HTTPException(status_code=404)

        # check if the Upload Offset with Content-Length header is correct
        if file.params.offset != upload_offset + content_length:
            raise HTTPException(status_code=409)

        # init copy of params to update
        new_params = file.params

        if file.params.defer_length:
            new_params.size = upload_offset

        if not file.params.expires:
            date_expiry = datetime.now() + timedelta(days=options.days_to_keep)
            new_params.expires = str(date_expiry.isoformat())

        # save param changes
        file.params = new_params

        if file.params.size == file.params.offset:
            response.headers["Tus-Resumable"] = options.tus_version
            response.headers["Upload-Offset"] = str(
                str(file.params.offset)
                if file.params.offset > 0
                else str(content_length)
            )
            response.headers["Upload-Expires"] = str(file.params.expires)
            response.status_code = status.HTTP_204_NO_CONTENT
            if options.on_upload_complete:
                options.on_upload_complete(
                    os.path.join(options.files_dir, f"{uuid}"),
                    file.params.file.paramsdata,
                )
        else:
            response.headers["Tus-Resumable"] = options.tus_version
            response.headers["Upload-Offset"] = str(file.params.offset)
            response.headers["Upload-Expires"] = str(file.params.expires)
            response.status_code = status.HTTP_204_NO_CONTENT

        if file.params and file.params.size == file.params.offset:
            file_path = os.path.join(options.files_dir, uuid)
            result = on_complete(file_path, file.params.file.paramsdata)
            # if the callback returned a coroutine, await it
            if inspect.isawaitable(result):
                await result

        return response

    @router.options("/", status_code=status.HTTP_204_NO_CONTENT)
    def core_options_route(response: Response, __=Depends(auth)) -> Response:
        # create response headers
        response.headers["Tus-Extension"] = options.tus_extension
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Tus-Version"] = options.tus_version
        response.headers["Tus-Max-Size"] = str(options.max_size)
        response.headers["Content-Length"] = str(0)
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    # EXTENSION ROUTES

    @router.post("/", status_code=status.HTTP_201_CREATED)
    async def extension_creation_route(
        request: Request,
        response: Response,
        upload_metadata: str = Header(None),
        upload_length: int = Header(None),
        upload_defer_length: int = Header(None),
        _=Depends(auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
    ) -> Response:
        # validate upload defer length
        if upload_defer_length is not None and upload_defer_length != 1:
            raise HTTPException(status_code=400, detail="Invalid Upload-Defer-Length")
        # set expiry date
        date_expiry = datetime.now() + timedelta(days=options.days_to_keep)
        # create upload metadata
        metadata = {}
        if upload_metadata is not None and upload_metadata != "":
            # Decode the base64-encoded string
            for kv in upload_metadata.split(","):
                key, value = kv.rsplit(" ", 1)
                decoded_value = base64.b64decode(value.strip()).decode("utf-8")
                metadata[key.strip()] = decoded_value
        # create upload params
        params = TusUploadParams(
            metadata=metadata,
            size=upload_length,
            offset=0,
            upload_part=0,
            created_at=str(datetime.now()),
            defer_length=upload_defer_length is not None,
            expires=str(date_expiry.isoformat()),
        )
        # create the file
        file = TusUploadFile(options=options, params=params)
        # update request headers
        response.headers["Location"] = get_request_headers(
            request=request, uuid=file.uid
        )["location"]
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Content-Length"] = str(0)
        # set status code
        response.status_code = status.HTTP_201_CREATED
        # run completion hooks
        if file.params and file.params.size == 0:
            file_path = os.path.join(options.files_dir, file.uid)
            result = on_complete(file_path, file.params.metadata)
            # if the callback returned a coroutine, await it
            if inspect.isawaitable(result):
                await result

        return response

    @router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    def extension_termination_route(
        uuid: str, response: Response, _=Depends(auth)
    ) -> Response:
        file = TusUploadFile(uid=uuid, options=options)

        # Check if the upload ID is valid
        if not file.exists:
            raise HTTPException(status_code=404, detail="Upload not found")

        # Delete the file and metadata for the upload from the mapping
        file.delete()

        # Return a 204 No Content response
        response.headers["Tus-Resumable"] = options.tus_version
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    # UNKNOWN

    @router.options("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    def options_upload_chunk(
        response: Response, uuid: str, _=Depends(auth)
    ) -> Response:
        file = TusUploadFile(uid=uuid, options=options)

        # validate
        if file.info is None or not file.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        # write response headers
        response.headers["Tus-Extension"] = options.tus_extension
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Tus-Version"] = options.tus_version
        response.headers["Content-Length"] = str(0)
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    @router.get("/{uuid}")
    def extension_get_upload(uuid: str) -> FileResponse:
        file = TusUploadFile(uid=uuid, options=options)

        # Check if the upload ID is valid
        if not file.info or not file.exists:
            raise HTTPException(status_code=404, detail="Upload not found")

        # Return the file in the response
        return FileResponse(
            os.path.join(options.files_dir, uuid),
            media_type="application/octet-stream",
            filename=file.info.metadata["name"],
            headers={
                "Content-Length": str(file.info.offset),
                "Tus-Resumable": options.tus_version,
            },
        )

    return router
