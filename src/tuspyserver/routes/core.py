import base64
from copy import deepcopy
import inspect
import logging
import os
from datetime import datetime, timedelta
from email.utils import formatdate
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request, Response, status

from tuspyserver.file import TusUploadFile
from tuspyserver.request import make_request_chunks_dep, get_request_headers


def _check_upload_expired(file: TusUploadFile) -> bool:
    """Check if upload has expired and return True if expired."""
    if not file.info or not file.info.expires:
        return False
    
    expires_dt = None
    if isinstance(file.info.expires, str):
        try:
            # Try RFC 7231 format first (e.g., "Mon, 02 Jan 2006 15:04:05 GMT")
            from email.utils import parsedate_to_datetime
            expires_dt = parsedate_to_datetime(file.info.expires)
        except (ValueError, TypeError):
            # Fallback to ISO format
            try:
                expires_dt = datetime.fromisoformat(file.info.expires.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return False
    elif isinstance(file.info.expires, (int, float)):
        expires_dt = datetime.fromtimestamp(file.info.expires)
    
    if expires_dt:
        now = datetime.now(expires_dt.tzinfo) if expires_dt.tzinfo else datetime.now()
        return expires_dt < now
    
    return False


def _format_rfc7231_date(dt: datetime) -> str:
    """Format datetime to RFC 7231 date-time format."""
    return formatdate(dt.timestamp(), usegmt=True)


def core_routes(router, options):
    """
    https://tus.io/protocols/resumable-upload#core-protocol
    """

    request_chunks_dep = make_request_chunks_dep(options)

    @router.head("/{uuid}", status_code=status.HTTP_200_OK)
    async def core_head_route(
        request: Request,
        response: Response,
        uuid: str,
        tus_resumable: str = Header(None),
        _=Depends(options.auth),
        file_dep: Callable[[dict], None] = Depends(options.file_dep),
    ) -> Response:
        # Validate Tus-Resumable header version
        if tus_resumable is None or tus_resumable != options.tus_version:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=f"Unsupported version. Expected {options.tus_version}",
                headers={"Tus-Version": options.tus_version}
            )
        # Create a copy of options to avoid mutating the global options
        file_options = deepcopy(options)

        # init file handle
        result = file_dep({})
        # if the callback returned a coroutine, await it
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            file_options.files_dir = result.get("files_dir", options.files_dir)
        # validate file
        file = TusUploadFile(uid=uuid, options=file_options)

        # DEBUG: Log file info for troubleshooting
        logger = logging.getLogger(__name__)
        logger.info(f"HEAD {uuid}: exists={file.exists}, info={file.info}, file_size={len(file) if file.exists else 0}")

        # Check if file exists and has valid info
        if not file.exists or file.info is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        
        # Check if upload has expired (distinguish from non-existent uploads)
        if _check_upload_expired(file):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload expired")

        # encode metadata
        
        # For partial uploads (chunks), metadata is optional since they're just data chunks
        # For regular uploads, metadata with filename and filetype is required
        if file.info.is_partial:
            # For partial uploads, try to get metadata but don't require it
            filename = file.info.metadata.get("filename") or file.info.metadata.get("name")
            filetype = file.info.metadata.get("filetype") or file.info.metadata.get("type")
        else:
            # For non-partial uploads, metadata is required
            filename = file.info.metadata.get("filename") or file.info.metadata.get("name")
            if filename is None:
                error_msg = f"Upload-file.metadata missing required field: filename (metadata: {file.info.metadata})"
                logger.error(f"HEAD {uuid}: {error_msg}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg,
                )

            filetype = file.info.metadata.get("filetype") or file.info.metadata.get("type")
            if filetype is None:
                error_msg = f"Upload-Metadata missing required field: filetype (metadata: {file.info.metadata})"
                logger.error(f"HEAD {uuid}: {error_msg}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg,
                )

        def b64(s: str) -> str:
            return base64.b64encode(s.encode("utf-8")).decode("ascii")

        # construct response
        response.headers["Tus-Resumable"] = file.options.tus_version
        
        # Only include Upload-Metadata header if we have metadata
        # Partial uploads may not have metadata, which is allowed by the spec
        if filename and filetype:
            response.headers["Upload-Metadata"] = (
                f"filename {b64(filename)}, filetype {b64(filetype)}"
            )
        elif filename or filetype:
            # If we have only one of them, include what we have
            metadata_parts = []
            if filename:
                metadata_parts.append(f"filename {b64(filename)}")
            if filetype:
                metadata_parts.append(f"filetype {b64(filetype)}")
            if metadata_parts:
                response.headers["Upload-Metadata"] = ", ".join(metadata_parts)
        # If no metadata at all, we can omit the header (spec allows this for partial uploads)
        
        # Handle deferred length in HEAD response
        if file.info.defer_length:
            response.headers["Upload-Defer-Length"] = "1"
        else:
            response.headers["Upload-Length"] = str(file.info.size)
            response.headers["Content-Length"] = str(file.info.size)
            
        response.headers["Upload-Offset"] = str(file.info.offset)
        response.headers["Cache-Control"] = "no-store"

        # Add Upload-Concat header for concatenation extension
        if file.info.is_partial:
            response.headers["Upload-Concat"] = "partial"
        elif file.info.is_final:
            # For final uploads, include the URLs of all partial uploads
            # Format: "final;url1 url2 url3"
            if file.info.partial_uploads:
                # Construct full URLs for each partial upload
                partial_urls = []
                for partial_uid in file.info.partial_uploads:
                    headers = get_request_headers(request=request, uuid=partial_uid, prefix=options.prefix)
                    partial_urls.append(headers["location"])
                
                # Join URLs with spaces as per tus spec
                concat_value = "final;" + " ".join(partial_urls)
                response.headers["Upload-Concat"] = concat_value
            else:
                # Fallback if partial_uploads is empty (shouldn't happen normally)
                response.headers["Upload-Concat"] = "final"

        response.status_code = status.HTTP_200_OK

        return response

    @router.patch("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    async def core_patch_route(
        request: Request,
        response: Response,
        uuid: str,
        content_length: int = Header(...),
        upload_offset: int = Header(...),
        upload_length: int = Header(None),
        content_type: str = Header(None),
        tus_resumable: str = Header(None),
        _=Depends(request_chunks_dep),
        __=Depends(options.auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
        file_dep: Callable[[dict], None] = Depends(options.file_dep),
    ) -> Response:
        # Validate Tus-Resumable header version
        if tus_resumable is None or tus_resumable != options.tus_version:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=f"Unsupported version. Expected {options.tus_version}",
                headers={"Tus-Version": options.tus_version}
            )
        
        # Validate Content-Type header for PATCH requests
        if content_type is None or content_type != "application/offset+octet-stream":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Content-Type must be application/offset+octet-stream"
            )
        # Create a copy of options to avoid mutating the original
        file_options = deepcopy(options)

        result = file_dep({})
        # if the callback returned a coroutine, await it
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            file_options.files_dir = result.get("files_dir", options.files_dir)
        
        # init file handle
        file = TusUploadFile(uid=uuid, options=file_options)
        # check if the upload ID is valid and file exists with valid info
        if not file.exists or file.info is None or uuid != file.uid:
            raise HTTPException(status_code=404)
        
        # Check if upload has expired (distinguish from non-existent uploads)
        if _check_upload_expired(file):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload expired")

        # Block PATCH on final concatenated uploads (they are created complete and immutable)
        # Partial uploads are regular uploads and CAN be PATCH'd to receive data
        if file.info.is_final:
            raise HTTPException(
                status_code=403,
                detail="Final concatenated uploads cannot be modified. They are immutable and created complete."
            )

        # init copy of params to update
        new_params = file.info

        # Handle deferred length according to TUS protocol
        if file.info.defer_length and upload_length is not None:
            # Client is setting the final upload length
            new_params.size = upload_length
            new_params.defer_length = False

        if not file.info.expires:
            date_expiry = datetime.now() + timedelta(days=options.days_to_keep)
            new_params.expires = _format_rfc7231_date(date_expiry)

        # save param changes
        file.info = new_params

        if file.info.size == file.info.offset:
            response.headers["Tus-Resumable"] = options.tus_version
            response.headers["Upload-Offset"] = str(
                str(file.info.offset) if file.info.offset > 0 else str(content_length)
            )
            if file.info.expires:
                expires_str = file.info.expires if isinstance(file.info.expires, str) else _format_rfc7231_date(datetime.fromtimestamp(file.info.expires))
                response.headers["Upload-Expires"] = expires_str
            response.status_code = status.HTTP_204_NO_CONTENT
            if options.on_upload_complete:
                options.on_upload_complete(
                    os.path.join(file_options.files_dir, f"{uuid}"),
                    file.info.metadata,
                )
        else:
            response.headers["Tus-Resumable"] = options.tus_version
            response.headers["Upload-Offset"] = str(file.info.offset)
            if file.info.expires:
                expires_str = file.info.expires if isinstance(file.info.expires, str) else _format_rfc7231_date(datetime.fromtimestamp(file.info.expires))
                response.headers["Upload-Expires"] = expires_str
            response.status_code = status.HTTP_204_NO_CONTENT

        if file.info and file.info.size == file.info.offset:
            file_path = os.path.join(file_options.files_dir, uuid)
            if options.on_upload_complete is None:
                result = on_complete(file_path, file.info.metadata)
                # if the callback returned a coroutine, await it
                if inspect.isawaitable(result):
                    await result

        return response

    @router.options("/", status_code=status.HTTP_204_NO_CONTENT)
    def core_options_route(
        request: Request,
        response: Response,
        tus_resumable: str = Header(None),
        __=Depends(options.auth)
    ) -> Response:
        # Validate Tus-Resumable header version (if provided)
        if tus_resumable is not None and tus_resumable != options.tus_version:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=f"Unsupported version. Expected {options.tus_version}",
                headers={"Tus-Version": options.tus_version}
            )
        # create response headers
        response.headers["Tus-Version"] = options.tus_version
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Tus-Extension"] = options.tus_extension
        response.headers["Tus-Max-Size"] = str(options.max_size)
        response.headers["Content-Length"] = str(0)
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    return router
