from copy import deepcopy
from typing import Callable
from fastapi import Depends, Header, HTTPException, Response, status
import inspect
import datetime
from email.utils import parsedate_to_datetime

from tuspyserver.file import TusUploadFile


def _check_upload_expired(file: TusUploadFile) -> bool:
    """Check if upload has expired and return True if expired."""
    if not file.info or not file.info.expires:
        return False
    
    expires_dt = None
    if isinstance(file.info.expires, str):
        try:
            # Try RFC 7231 format first (e.g., "Mon, 02 Jan 2006 15:04:05 GMT")
            expires_dt = parsedate_to_datetime(file.info.expires)
        except (ValueError, TypeError):
            # Fallback to ISO format
            try:
                expires_dt = datetime.datetime.fromisoformat(file.info.expires.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return False
    elif isinstance(file.info.expires, (int, float)):
        expires_dt = datetime.datetime.fromtimestamp(file.info.expires)
    
    if expires_dt:
        now = datetime.datetime.now(expires_dt.tzinfo) if expires_dt.tzinfo else datetime.datetime.now()
        return expires_dt < now
    
    return False


def termination_extension_routes(router, options):
    """
    https://tus.io/protocols/resumable-upload#termination
    """

    @router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    async def extension_termination_route(
        uuid: str,
        response: Response,
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
        
        # Create a copy of options to avoid mutating the original
        file_options = deepcopy(options)
        result = file_dep({})

        # if the callback returned a coroutine, await it
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            file_options.files_dir = result.get("files_dir", options.files_dir)
        file = TusUploadFile(uid=uuid, options=file_options)

        # Check if the upload ID is valid
        if not file.exists:
            raise HTTPException(status_code=404, detail="Upload not found")
        
        # Check if upload has expired (distinguish from non-existent uploads)
        if file.info and _check_upload_expired(file):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload expired")

        # Delete the file and metadata for the upload from the mapping
        file.delete(uuid)

        # Return a 204 No Content response
        response.headers["Tus-Resumable"] = options.tus_version
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    return router
