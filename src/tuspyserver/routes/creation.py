import base64
from copy import deepcopy
import inspect
import os
from datetime import datetime, timedelta
from email.utils import formatdate
from typing import Callable
from urllib.parse import urlparse

from fastapi import Depends, Header, HTTPException, Request, Response, status

from tuspyserver.file import TusUploadFile, TusUploadParams
from tuspyserver.request import get_request_headers


def _format_rfc7231_date(dt: datetime) -> str:
    """Format datetime to RFC 7231 date-time format."""
    return formatdate(dt.timestamp(), usegmt=True)


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
        upload_concat: str = Header(None),
        tus_resumable: str = Header(None),
        _=Depends(options.auth),
        on_complete: Callable[[str, dict], None] = Depends(options.upload_complete_dep),
        pre_create: Callable[[dict, dict], None] = Depends(options.pre_create_dep),
        file_dep: Callable[[dict], None] = Depends(options.file_dep),
    ) -> Response:
        # Validate Tus-Resumable header version
        if tus_resumable is None or tus_resumable != options.tus_version:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail=f"Unsupported version. Expected {options.tus_version}",
                headers={"Tus-Version": options.tus_version}
            )
        
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

        # Handle Upload-Concat header for concatenation extension
        is_partial = False
        is_final = False
        partial_uploads = None
        final_size = upload_length

        if upload_concat is not None:
            upload_concat = upload_concat.strip()

            if upload_concat == "partial":
                # This is a partial upload
                is_partial = True

            elif upload_concat.startswith("final;"):
                # This is a final upload created by concatenating partials
                is_final = True

                # Parse the list of partial upload URLs/UUIDs
                concat_spec = upload_concat[6:].strip()  # Remove "final;" prefix
                if not concat_spec:
                    raise HTTPException(
                        status_code=400,
                        detail="Upload-Concat final header must specify partial uploads"
                    )

                # Split by space to get individual URLs/UUIDs
                partial_url_list = [u.strip() for u in concat_spec.split() if u.strip()]
                if not partial_url_list:
                    raise HTTPException(
                        status_code=400,
                        detail="Upload-Concat final header must specify at least one partial upload"
                    )

                # Extract UUIDs from URLs (full or relative) or use bare UUIDs
                partial_uids = []
                for url_or_uid in partial_url_list:
                    if url_or_uid.startswith("http://") or url_or_uid.startswith("https://"):
                        # Parse full URL to extract UUID
                        parsed = urlparse(url_or_uid)
                        path_parts = [p for p in parsed.path.split("/") if p]
                        if not path_parts:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invalid URL in Upload-Concat header: {url_or_uid}"
                            )
                        uid = path_parts[-1]
                    elif "/" in url_or_uid:
                        # Handle relative URLs like "/files/uuid" or "files/uuid"
                        # Extract the last path component as the UUID
                        path_parts = [p for p in url_or_uid.split("/") if p]
                        if not path_parts:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invalid URL in Upload-Concat header: {url_or_uid}"
                            )
                        uid = path_parts[-1]
                    else:
                        # Assume it's a bare UUID
                        uid = url_or_uid
                    partial_uids.append(uid)

                partial_uploads = partial_uids

                # Get the file_options to use for loading partials (call file_dep once, not in loop)
                # All partials should be in the same directory as the final upload (same user)
                file_result_for_partials = file_dep(metadata)
                file_options_for_partials = deepcopy(options)
                if inspect.isawaitable(file_result_for_partials):
                    file_result_for_partials = await file_result_for_partials
                if isinstance(file_result_for_partials, dict):
                    file_options_for_partials.files_dir = file_result_for_partials.get("files_dir", options.files_dir)

                # Validate and load all partial uploads
                partial_files = []
                total_size = 0

                for uid in partial_uids:
                    # Load the partial upload from the same directory
                    partial_file = TusUploadFile(options=file_options_for_partials, uid=uid)

                    if not partial_file.exists:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Partial upload not found: {uid}"
                        )

                    if partial_file.info is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Partial upload has no metadata: {uid}"
                        )

                    # Validate it's marked as partial
                    if not partial_file.info.is_partial:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Upload {uid} is not a partial upload"
                        )

                    # Validate it's complete (production-ready validation)
                    if partial_file.info.size is not None:
                        if partial_file.info.offset != partial_file.info.size:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Partial upload {uid} is not complete (offset={partial_file.info.offset}, size={partial_file.info.size})"
                            )

                    # Get actual file size
                    file_size = len(partial_file)
                    total_size += file_size
                    partial_files.append(partial_file)

                # Final upload size is the sum of all partials
                final_size = total_size

                # Upload-Length is forbidden for final uploads (we calculate it)
                if upload_length is not None:
                    raise HTTPException(
                        status_code=400,
                        detail="Upload-Length header must not be included for final concatenated uploads"
                    )

                # Upload-Defer-Length is also forbidden for final uploads
                if upload_defer_length is not None:
                    raise HTTPException(
                        status_code=400,
                        detail="Upload-Defer-Length header must not be included for final concatenated uploads"
                    )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid Upload-Concat header value"
                )

        # create upload params
        params = TusUploadParams(
            metadata=metadata,
            size=final_size,
            offset=0,
            upload_part=0,
            created_at=str(datetime.now()),
            defer_length=upload_defer_length is not None,
            expires=_format_rfc7231_date(date_expiry),
            is_partial=is_partial,
            is_final=is_final,
            partial_uploads=partial_uploads,
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
        uid = None
        file_result = file_dep(metadata)
        file_options = deepcopy(options)
        # if the callback returned a coroutine, await it
        if inspect.isawaitable(file_result):
            file_result = await file_result
        if isinstance(file_result, dict):
            file_options.files_dir = file_result.get("files_dir", options.files_dir)
            uid = file_result.get("uid", None)
        # create the file
        file = TusUploadFile(options=file_options, uid=uid, params=params)
        
        # Handle Creation-With-Upload extension: check if POST has body with initial data
        # Note: FastAPI may have already consumed the request body, so we check Content-Length
        # and attempt to read initial data if available
        initial_data_size = 0
        content_length_header = request.headers.get("content-length")
        if content_length_header:
            try:
                content_length_value = int(content_length_header)
                if content_length_value > 0:
                    # Try to read initial upload data if present
                    # Note: This handles the Creation-With-Upload extension
                    try:
                        body = await request.body()
                        if body and len(body) > 0:
                            initial_data_size = len(body)
                            # Write initial data to file
                            with open(file.path, "ab") as f:
                                f.write(body)
                                f.flush()
                            # Update offset
                            params.offset += len(body)
                            params.upload_part += 1
                            file.info = params
                    except Exception:
                        # If body was already consumed or other error, skip
                        pass
            except (ValueError, TypeError):
                pass  # Invalid content-length, ignore

        # If this is a final concatenated upload, perform the concatenation
        if is_final and partial_files:
            final_path = file.path
            bytes_written = 0

            try:
                # Concatenate all partial files into the final file using chunked copying
                # This prevents memory issues with large files (e.g., 100GB+)
                CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks

                with open(final_path, "wb") as final_f:
                    for partial_file in partial_files:
                        partial_path = partial_file.path

                        # Copy file in chunks to avoid loading entire file into memory
                        with open(partial_path, "rb") as partial_f:
                            while True:
                                chunk = partial_f.read(CHUNK_SIZE)
                                if not chunk:
                                    break
                                final_f.write(chunk)
                                bytes_written += len(chunk)

                    # Ensure all data is written to disk before proceeding
                    final_f.flush()
                    os.fsync(final_f.fileno())

                # Verify we wrote the expected amount of data
                if bytes_written != final_size:
                    raise Exception(
                        f"Size mismatch after concatenation: expected {final_size}, wrote {bytes_written}"
                    )

                # Update the offset to indicate the upload is complete
                params.offset = final_size
                file.info = params

                # Delete all partial uploads after successful concatenation
                # Handle race conditions where partials might already be deleted
                for partial_file in partial_files:
                    try:
                        partial_file.delete(partial_file.uid)
                    except FileNotFoundError:
                        # Partial already deleted (race condition or retry), ignore
                        pass
                    except Exception as delete_error:
                        # Log but don't fail the concat - final file is already good
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning(
                            f"Failed to delete partial {partial_file.uid}: {delete_error}"
                        )

            except Exception as e:
                # Clean up the final file if concatenation fails
                # This ensures we don't leave corrupted files around
                try:
                    if file.exists:
                        file.delete(file.uid)
                except Exception:
                    pass  # Best effort cleanup

                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to concatenate uploads: {str(e)}"
                )

        # update request headers
        response.headers["Location"] = get_request_headers(
            request=request, uuid=file.uid, prefix=options.prefix
        )["location"]
        response.headers["Tus-Resumable"] = options.tus_version
        response.headers["Content-Length"] = str(0)
        # Include Upload-Offset header if Creation-With-Upload extension was used
        if initial_data_size > 0 and file.info:
            response.headers["Upload-Offset"] = str(file.info.offset)
        # Include Upload-Expires header
        if file.info and file.info.expires:
            expires_str = file.info.expires if isinstance(file.info.expires, str) else _format_rfc7231_date(datetime.fromtimestamp(file.info.expires))
            response.headers["Upload-Expires"] = expires_str
        # set status code
        response.status_code = status.HTTP_201_CREATED
        # run completion hooks for zero-byte uploads OR final concatenated uploads
        if file.info is not None and (file.info.size == 0 or (is_final and file.info.offset == file.info.size)):
            file_path = os.path.join(file_options.files_dir, file.uid)
            result = on_complete(file_path, file.info.metadata)
            # if the callback returned a coroutine, await it
            if inspect.isawaitable(result):
                await result

        return response

    return router
