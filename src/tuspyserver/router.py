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
        response: Response,
        uuid: str,
        tus_resumable: str = Header(None),
        _=Depends(options.auth),
    ) -> Response:
        # Validate Tus-Resumable header
        if not tus_resumable:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Tus-Resumable header is required",
            )

        if tus_resumable != options.tus_version:
            response.status_code = status.HTTP_412_PRECONDITION_FAILED
            response.headers["Tus-Version"] = options.tus_version
            return response

        file = TusUploadFile(uid=uuid, options=options)

        if not file.exists or not file.info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Upload-Offset"] = str(file.info.offset)
        response.headers["Cache-Control"] = "no-store"

        if file.info.size is not None:
            response.headers["Upload-Length"] = str(file.info.size)
        elif file.info.defer_length:
            response.headers["Upload-Defer-Length"] = "1"

        if file.info.metadata:
            metadata_parts = []
            for key, value in file.info.metadata.items():
                if value:  # Only encode non-empty values
                    b64_value = base64.b64encode(str(value).encode("utf-8")).decode(
                        "ascii"
                    )
                    metadata_parts.append(f"{key} {b64_value}")
                else:
                    metadata_parts.append(key)

            if metadata_parts:
                response.headers["Upload-Metadata"] = ",".join(metadata_parts)

        response.status_code = status.HTTP_200_OK
        return response

    # allow client to upload a chunk
    @router.patch("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    async def core_patch_route(
        response: Response,
        uuid: str,
        content_type: str = Header(None),
        content_length: int = Header(None),
        upload_offset: int = Header(None),
        upload_length: int = Header(None),
        tus_resumable: str = Header(None),
        _=Depends(get_request_chunks),
        __=Depends(options.auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
    ) -> Response:
        # Validate Tus-Resumable header
        if not tus_resumable:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Tus-Resumable header is required",
            )

        if tus_resumable != options.tus_version:
            response.status_code = status.HTTP_412_PRECONDITION_FAILED
            response.headers["Tus-Version"] = options.tus_version
            return response

        # Validate Content-Type
        if content_type != "application/offset+octet-stream":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Content-Type must be application/offset+octet-stream",
            )

        file = TusUploadFile(uid=uuid, options=options)

        # check if the upload ID is valid
        if not file.exists or not file.info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        # If length was deferred and is now provided
        if file.info.defer_length and upload_length is not None:
            if file.info.size is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Upload-Length cannot be changed once set",
                )
            if upload_length > options.max_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Upload exceeds maximum allowed size",
                )
            new_params = file.info
            new_params.size = upload_length
            new_params.defer_length = False
            file.info = new_params

        # Validate upload offset
        if upload_offset != file.info.offset:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Upload-Offset mismatch. Expected {file.info.offset}, got {upload_offset}",
            )

        # The actual chunk processing is done by get_request_chunks dependency
        # We just need to update the response headers

        # Get updated file info after chunk processing
        file = TusUploadFile(uid=uuid, options=options)

        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Upload-Offset"] = str(file.info.offset)

        if file.info.expires:
            response.headers["Upload-Expires"] = file.info.expires

        # Check if upload is complete
        if file.info.size is not None and file.info.size == file.info.offset:
            file_path = os.path.join(options.files_dir, uuid)
            result = on_complete(file_path, file.info.metadata)
            # if the callback returned a coroutine, await it
            if inspect.isawaitable(result):
                await result

        return response

    @router.options("/", status_code=status.HTTP_204_NO_CONTENT)
    def core_options_route(response: Response) -> Response:
        # create response headers
        response.headers["Tus-Extension"] = options.tus_extension
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Tus-Version"] = options.tus_version
        response.headers["Tus-Max-Size"] = str(options.max_size)
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
        content_type: str = Header(None),
        content_length: int = Header(0),
        tus_resumable: str = Header(None),
        _=Depends(auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
    ) -> Response:
        print(request)
        # Validate Tus-Resumable header
        if not tus_resumable:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Tus-Resumable header is required",
            )

        if tus_resumable != options.tus_version:
            response.status_code = status.HTTP_412_PRECONDITION_FAILED
            response.headers["Tus-Version"] = options.tus_version
            return response

        # validate upload defer length
        if upload_defer_length is not None and upload_defer_length != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload-Defer-Length must be 1",
            )

        # Must have either Upload-Length or Upload-Defer-Length
        if upload_length is None and upload_defer_length is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either Upload-Length or Upload-Defer-Length must be provided",
            )

        # Check max size
        if upload_length is not None and upload_length > options.max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Upload exceeds maximum allowed size",
            )

        # set expiry date
        date_expiry = datetime.now() + timedelta(days=options.days_to_keep)

        # create upload metadata
        metadata = {}
        if upload_metadata is not None and upload_metadata != "":
            # Decode the base64-encoded string
            for kv in upload_metadata.split(","):
                kv = kv.strip()
                if " " in kv:
                    key, value = kv.rsplit(" ", 1)
                    key = key.strip()
                    value = value.strip()
                    try:
                        decoded_value = base64.b64decode(value).decode("utf-8")
                        metadata[key] = decoded_value
                    except Exception:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Invalid base64 encoding for metadata key: {key}",
                        )
                else:
                    # Key without value
                    metadata[kv] = ""

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
        file = TusUploadFile(uid=None, options=options, params=params)
        file.create()

        # Handle creation with upload
        if content_length > 0 and content_type == "application/offset+octet-stream":
            # Process the upload data
            await get_request_chunks(request, options, file.uid, post_request=True)
            # Reload file info after processing
            file = TusUploadFile(uid=file.uid, options=options)
            response.headers["Upload-Offset"] = str(file.info.offset)

        # update request headers
        location_info = get_request_headers(request=request, uuid=file.uid)
        response.headers["Location"] = location_info["location"]
        response.headers["Tus-Resumable"] = options.tus_version

        if file.info.expires:
            response.headers["Upload-Expires"] = file.info.expires

        # set status code
        response.status_code = status.HTTP_201_CREATED

        # run completion hooks if upload is complete
        if (
            file.info
            and file.info.size is not None
            and file.info.size == file.info.offset
        ):
            file_path = os.path.join(options.files_dir, file.uid)
            result = on_complete(file_path, file.info.metadata)
            # if the callback returned a coroutine, await it
            if inspect.isawaitable(result):
                await result

        return response

    @router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    def extension_termination_route(
        uuid: str,
        response: Response,
        tus_resumable: str = Header(None),
        _=Depends(auth),
    ) -> Response:
        # Validate Tus-Resumable header
        if not tus_resumable:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Tus-Resumable header is required",
            )

        if tus_resumable != options.tus_version:
            response.status_code = status.HTTP_412_PRECONDITION_FAILED
            response.headers["Tus-Version"] = options.tus_version
            return response

        file = TusUploadFile(uid=uuid, options=options)

        # Check if the upload ID is valid
        if not file.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found"
            )

        # Delete the file and metadata for the upload from the mapping
        file.delete(uuid)

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
        if not file.exists or not file.info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        # write response headers
        response.headers["Tus-Extension"] = options.tus_extension
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Tus-Version"] = options.tus_version
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    @router.get("/{uuid}")
    def extension_get_upload(uuid: str, _=Depends(auth)) -> FileResponse:
        file = TusUploadFile(uid=uuid, options=options)

        # Check if the upload ID is valid
        if not file.info or not file.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found"
            )

        # Return the file in the response
        return FileResponse(
            os.path.join(options.files_dir, uuid),
            media_type="application/octet-stream",
            filename=file.info.metadata.get(
                "filename", file.info.metadata.get("name", uuid)
            ),
            headers={
                "Content-Length": str(file.info.offset),
                "Tus-Resumable": options.tus_version,
            },
        )

    # Add method to remove expired files
    def remove_expired_files():
        if not os.path.exists(options.files_dir):
            return

        for filename in os.listdir(options.files_dir):
            if filename.endswith(".info"):
                continue

            file = TusUploadFile(uid=filename, options=options)
            if file.info and file.info.expires:
                try:
                    expire_time = datetime.fromisoformat(
                        file.info.expires.replace("Z", "+00:00")
                    )
                    if expire_time < datetime.now():
                        file.delete(filename)
                except Exception:
                    # Skip files with invalid expiration dates
                    pass

    router.remove_expired_files = remove_expired_files

    return router
