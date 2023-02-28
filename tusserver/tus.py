import base64
import json
import os
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from fastapi import Header, HTTPException, Response, Request, status, Depends, Path, APIRouter
from starlette.responses import FileResponse

from tusserver.metadata import FileMetadata


def create_api_router(
        files_dir='/tmp/files',
        location='http://127.0.0.1:8000/files',
        max_size=128849018880,
        on_upload_complete: Callable[[str], None] = None,
):
    router = APIRouter()

    tus_version = '1.0.0'
    tus_extension = 'creation,creation-defer-length,creation-with-upload,expiration,termination'
    days_to_keep = 5

    async def _get_request_chunk(request: Request, uuid: str = Path(...), post_request: bool = False) -> bool | None:
        meta = _read_metadata(uuid)
        if not meta or not _file_exists(uuid):
            return False

        with open(f"{files_dir}/{uuid}", "ab") as f:
            async for chunk in request.stream():
                if post_request and chunk is None or len(chunk) == 0:
                    return None

                if _get_file_length(uuid) + len(chunk) > max_size:
                    raise HTTPException(status_code=413)

                f.write(chunk)
                meta.offset += len(chunk)
                meta.upload_chunk_size = len(chunk)
                meta.upload_part += 1
                _write_metadata(meta)

            f.close()

        return True

    @router.head("/{uuid}", status_code=status.HTTP_200_OK)
    def get_upload_metadata(response: Response, uuid: str) -> Response:
        meta = _read_metadata(uuid)
        if meta is None or not _file_exists(uuid):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        response.headers["Tus-Resumable"] = tus_version
        response.headers["Content-Length"] = str(meta.size)
        response.headers["Upload-Length"] = str(meta.size)
        response.headers["Upload-Offset"] = str(meta.offset)
        response.headers["Cache-Control"] = "no-store"
        response.headers[
            "Upload-Metadata"] = f"filename {base64.b64encode(bytes(meta.metadata['name'], 'utf-8'))}, " \
                                 f"filetype {base64.b64encode(bytes(meta.metadata['type'], 'utf-8'))}"
        response.status_code = status.HTTP_200_OK
        return response

    @router.patch("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    def upload_chunk(
            response: Response,
            uuid: str,
            content_type: str = Header(None),
            content_length: int = Header(None),
            upload_offset: int = Header(None),
            _=Depends(_get_request_chunk),
    ) -> Response:
        response_headers = _get_and_save_the_file(
            response,
            uuid,
            content_type,
            content_length,
            upload_offset,
        )

        if on_upload_complete:
            on_upload_complete(os.path.join(files_dir, f'{uuid}'))

        return response_headers

    @router.options("/", status_code=status.HTTP_204_NO_CONTENT)
    def options_create_upload(response: Response) -> Response:
        response.headers["Tus-Extension"] = tus_extension
        response.headers["Tus-Resumable"] = tus_version
        response.headers["Tus-Version"] = tus_version
        response.headers["Tus-Max-Size"] = str(max_size)
        response.headers["Content-Length"] = str(0)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.post("/", status_code=status.HTTP_201_CREATED)
    async def create_upload(
            request: Request,
            response: Response,
            upload_metadata: str = Header(None),
            upload_length: int = Header(None),
            upload_defer_length: int = Header(None),
    ) -> Response:
        if upload_defer_length is not None and upload_defer_length != 1:
            raise HTTPException(status_code=400, detail="Invalid Upload-Defer-Length")

        defer_length = upload_defer_length is not None

        # Create a new upload and store the file and metadata in the mapping
        metadata = {}
        if upload_metadata is not None and upload_metadata != '':
            # Decode the base64-encoded string
            for kv in upload_metadata.split(","):
                key, value = kv.rsplit(" ", 1)
                decoded_value = base64.b64decode(value.strip()).decode("utf-8")
                metadata[key.strip()] = decoded_value

        uuid = str(uuid4().hex)

        date_expiry = datetime.now() + timedelta(days=days_to_keep)
        saved_meta_data = FileMetadata.from_request(
            uuid, metadata, upload_length, str(datetime.now()), defer_length, str(date_expiry.isoformat())
        )
        _write_metadata(saved_meta_data)

        _initialize_file(uuid)

        chunk: bool | None = await _get_request_chunk(request, uuid, True)
        if chunk:
            response = _get_and_save_the_file(
                response,
                uuid,
            )
            response.headers["Location"] = f"{location}/{uuid}"
            return response

        response.headers["Location"] = f"{location}/{uuid}"
        response.headers["Tus-Resumable"] = tus_version
        response.headers["Content-Length"] = str(0)
        response.status_code = status.HTTP_201_CREATED
        return response

    @router.options("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    def options_upload_chunk(response: Response, uuid: str) -> Response:
        meta = _read_metadata(uuid)
        if meta is None or not _file_exists(uuid):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        response.headers["Tus-Extension"] = tus_extension
        response.headers["Tus-Resumable"] = tus_version
        response.headers["Tus-Version"] = tus_version
        response.headers["Content-Length"] = str(0)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.get("/{uuid}")
    def get_upload(uuid: str) -> FileResponse:
        meta = _read_metadata(uuid)

        # Check if the upload ID is valid
        if not meta or uuid != meta.uid or not _file_exists(uuid):
            raise HTTPException(status_code=404, detail="Upload not found")

        # Return the file in the response
        return FileResponse(
            os.path.join(files_dir, uuid),
            media_type="application/octet-stream",
            filename=meta.metadata["name"],
            headers={
                "Content-Length": str(meta.offset),
                "Tus-Resumable": tus_version
            }
        )

    @router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_upload(uuid: str, response: Response) -> Response:
        meta = _read_metadata(uuid)

        # Check if the upload ID is valid
        if not meta or uuid != meta.uid or not _file_exists(uuid):
            raise HTTPException(status_code=404, detail="Upload not found")

        # Delete the file and metadata for the upload from the mapping
        _delete_files(uuid)

        # Return a 204 No Content response
        response.headers["Tus-Resumable"] = tus_version
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    def _write_metadata(meta: FileMetadata) -> None:
        if not os.path.exists(files_dir):
            os.makedirs(files_dir)

        with open(os.path.join(files_dir, f'{meta.uid}.info'), 'w') as f:
            f.write(json.dumps(meta, indent=4, default=lambda k: k.__dict__))

    def _initialize_file(uid: str) -> None:
        if not os.path.exists(files_dir):
            os.makedirs(files_dir)

        open(os.path.join(files_dir, f'{uid}'), 'a').close()

    def _read_metadata(uid: str) -> FileMetadata | None:
        fpath = os.path.join(files_dir, f'{uid}.info')
        if os.path.exists(fpath):
            with open(fpath, 'r') as f:
                return FileMetadata(**json.load(f))

        return None

    def _get_file(uid: str) -> bytes | None:
        fpath = os.path.join(files_dir, uid)
        if os.path.exists(fpath):
            with open(fpath, 'rb') as f:
                return f.read()

        return None

    def _file_exists(uid: str) -> bool:
        return os.path.exists(os.path.join(files_dir, uid))

    def _get_file_length(uid: str) -> int:
        return os.path.getsize(os.path.join(files_dir, uid))

    def _delete_files(uid: str) -> None:
        fpath = os.path.join(files_dir, uid)
        if os.path.exists(fpath):
            os.remove(fpath)

        meta_path = os.path.join(files_dir, f"{uid}.info")
        if os.path.exists(meta_path):
            os.remove(meta_path)

    def _get_and_save_the_file(
            response: Response,
            uuid: str,
            content_type: str = Header(None),
            content_length: int = Header(None),
            upload_length: int = Header(None),
    ):
        # Check if the Content-Type header is set to "application/offset+octet-stream"
        if content_type != "application/offset+octet-stream":
            raise HTTPException(status_code=415)

        meta = _read_metadata(uuid)
        # Check if the upload ID is valid
        if not meta or uuid != meta.uid:
            raise HTTPException(status_code=404)

        if meta.defer_length and upload_length is None:
            raise HTTPException(status_code=400, detail="Upload-Length header is required")

        # Check if the Upload Offset with Content-Length header is correct
        if meta.offset != upload_length + content_length:
            raise HTTPException(status_code=409)

        if meta.defer_length:
            meta.size = upload_length

        if not meta.expires:
            date_expiry = datetime.now() + timedelta(days=days_to_keep)
            meta.expires = str(date_expiry.isoformat())
        _write_metadata(meta)

        response.headers["Tus-Resumable"] = tus_version
        response.headers["Upload-Offset"] = str(meta.offset)
        # response.headers["Upload-Expires"] = str(datetime.fromisoformat(meta.expires).strftime("%a, %d %b %G %T %Z"))
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    def remove_expired_files():
        file_list = os.listdir(files_dir)

        file_list_to_remove = []

        for f in file_list:
            if len(f) == 32:
                file_list_to_remove.append(f)

        for f in file_list_to_remove:
            meta = _read_metadata(f)
            if meta.expires and datetime.fromisoformat(meta.expires) < datetime.now():
                _delete_files(f)

    # # Create a scheduler for deleting the files
    # scheduler = sched.scheduler(time.time, time.sleep)
    # run_time = time.mktime(time.strptime("01:00", "%H:%M"))
    # scheduler.enterabs(run_time, 1, remove_expired_files)
    # scheduler.run()

    return router
