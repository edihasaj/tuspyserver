import base64
import inspect
import os
from datetime import datetime, timedelta
from typing import Callable


from fastapi import (
    Depends,
    Header,
    HTTPException,
    Response,
    status,
)

from tuspyserver.file import TusUploadFile
from tuspyserver.request import make_request_chunks_dep


def core_routes(router, options):
    """
    https://tus.io/protocols/resumable-upload#core-protocol
    """

    request_chunks_dep = make_request_chunks_dep(options)

    @router.head("/{uuid}", status_code=status.HTTP_200_OK)
    def core_head_route(
        response: Response, uuid: str, _=Depends(options.auth)
    ) -> Response:
        # validate file

        file = TusUploadFile(uid=uuid, options=options)
        if file.info is None or not file.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        # encode metadata

        filename = file.info.metadata.get("filename") or file.info.metadata.get("name")
        if filename is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload-file.metadata missing required field: filename",
            )

        filetype = file.info.metadata.get("filetype") or file.info.metadata.get("type")
        if filetype is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload-Metadata missing required field: filetype",
            )

        def b64(s: str) -> str:
            return base64.b64encode(s.encode("utf-8")).decode("ascii")

        # construct response

        response.headers["Tus-Resumable"] = file.options.tus_version
        response.headers["Upload-Metadata"] = (
            f"filename {b64(filename)}, filetype {b64(filetype)}"
        )
        response.headers["Upload-Length"] = str(file.info.size)
        response.headers["Upload-Offset"] = str(file.info.offset)
        response.headers["Content-Length"] = str(file.info.size)
        response.headers["Cache-Control"] = "no-store"

        response.status_code = status.HTTP_200_OK

        return response

    @router.patch("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    async def core_patch_route(
        response: Response,
        uuid: str,
        content_length: int = Header(None),
        upload_offset: int = Header(None),
        _=Depends(request_chunks_dep),
        __=Depends(options.auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
    ) -> Response:
        file = TusUploadFile(uid=uuid, options=options)

        # check if the upload ID is valid
        if not file.info or uuid != file.uid:
            raise HTTPException(status_code=404)

        # check if the Upload Offset with Content-Length header is correct
        if file.info.offset != upload_offset + content_length:
            raise HTTPException(status_code=409)

        # init copy of params to update
        new_params = file.info

        if file.info.defer_length:
            new_params.size = upload_offset

        if not file.info.expires:
            date_expiry = datetime.now() + timedelta(days=options.days_to_keep)
            new_params.expires = str(date_expiry.isoformat())

        # save param changes
        file.info = new_params

        if file.info.size == file.info.offset:
            response.headers["Tus-Resumable"] = options.tus_version
            response.headers["Upload-Offset"] = str(
                str(file.info.offset) if file.info.offset > 0 else str(content_length)
            )
            response.headers["Upload-Expires"] = str(file.info.expires)
            response.status_code = status.HTTP_204_NO_CONTENT
            if options.on_upload_complete:
                options.on_upload_complete(
                    os.path.join(options.files_dir, f"{uuid}"),
                    file.info.metadata,
                )
        else:
            response.headers["Tus-Resumable"] = options.tus_version
            response.headers["Upload-Offset"] = str(file.info.offset)
            response.headers["Upload-Expires"] = str(file.info.expires)
            response.status_code = status.HTTP_204_NO_CONTENT

        if file.info and file.info.size == file.info.offset:
            file_path = os.path.join(options.files_dir, uuid)
            if options.on_upload_complete is None:
                result = on_complete(file_path, file.info.metadata)
                # if the callback returned a coroutine, await it
                if inspect.isawaitable(result):
                    await result

        return response

    @router.options("/", status_code=status.HTTP_204_NO_CONTENT)
    def core_options_route(response: Response, __=Depends(options.auth)) -> Response:
        # create response headers
        response.headers["Tus-Version"] = options.tus_version
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Tus-Extension"] = options.tus_extension
        response.headers["Tus-Max-Size"] = str(options.max_size)
        response.headers["Content-Length"] = str(0)
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    return router
