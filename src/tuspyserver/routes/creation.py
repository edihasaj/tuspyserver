import base64
import inspect
import os
from datetime import datetime, timedelta
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request, Response, status

from tuspyserver.file import TusUploadFile, TusUploadParams
from tuspyserver.request import get_request_headers


def creation_extension_routes(router, options):
    """
    https://tus.io/protocols/resumable-upload#creation
    """

    @router.post("", status_code=status.HTTP_201_CREATED)
    @router.post("/", status_code=status.HTTP_201_CREATED)
    async def extension_creation_route(
        request: Request,
        response: Response,
        upload_metadata: str = Header(None),
        upload_length: int = Header(None),
        upload_defer_length: int = Header(None),
        _=Depends(options.auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
        pre_create: Callable[[dict, dict], None] = Depends(options.pre_create_dep),
    ) -> Response:
        # validate upload defer length
        if upload_defer_length is not None and upload_defer_length != 1:
            raise HTTPException(status_code=400, detail="Invalid Upload-Defer-Length")
        # set expiry date
        date_expiry = datetime.now() + timedelta(days=options.days_to_keep)
        # create upload metadata
        metadata = {}
        if upload_metadata is not None and upload_metadata != "":
            # Decode the base64-encoded metadata
            # Format: "key1 base64value1,key2 base64value2" (gracefully handle missing values)
            for kv in upload_metadata.split(","):
                kv = kv.strip()  # Remove any surrounding whitespace
                if not kv:  # Skip empty entries
                    continue

                split = kv.rsplit(" ", 1)
                if len(split) == 2:
                    key, value = split
                    key = key.strip()
                    if not key:  # Skip entries with empty keys
                        continue
                    try:
                        decoded_value = base64.b64decode(value.strip()).decode("utf-8")
                        metadata[key] = decoded_value
                    except Exception:
                        # Skip invalid base64 values gracefully
                        continue
                elif len(split) == 1:
                    key = split[0].strip()
                    if key:  # Only add non-empty keys
                        metadata[key] = ""
                else:
                    # This case should never happen with rsplit(" ", 1), but keeping for safety
                    raise HTTPException(
                        status_code=400, detail="Unexpected format in metadata"
                    )

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

        # run pre-create hook before creating the file
        # The hook receives the metadata and a dict with upload parameters
        upload_info = {
            "size": upload_length,
            "defer_length": upload_defer_length is not None,
            "expires": str(date_expiry.isoformat()),
        }
        result = pre_create(metadata, upload_info)
        # if the callback returned a coroutine, await it
        if inspect.isawaitable(result):
            await result

        # create the file
        file = TusUploadFile(options=options, params=params)
        # update request headers
        response.headers["Location"] = get_request_headers(
            request=request, uuid=file.uid, prefix=options.prefix
        )["location"]
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Content-Length"] = str(0)
        # set status code
        response.status_code = status.HTTP_201_CREATED
        # run completion hooks
        if file.info is not None and file.info.size == 0:
            file_path = os.path.join(options.files_dir, file.uid)
            result = on_complete(file_path, file.info.metadata)
            # if the callback returned a coroutine, await it
            if inspect.isawaitable(result):
                await result

        return response

    return router
