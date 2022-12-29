import base64
import json
import os
from datetime import datetime
from typing import Any
from uuid import uuid4

import boto3 as boto3
from fastapi import FastAPI, Header, HTTPException, Response, Request, status, Depends, Path
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")

s3_client = boto3.client("s3")
FILES_DIR = '/tmp/files'
MAX_SIZE = 128849018880


class FileMetadata(BaseModel):
    uid: str
    metadata: dict[Any, str]
    length: int
    offset: int
    upload_part: int
    created_at: str

    @classmethod
    def from_request(
            cls,
            uid: str,
            metadata: dict[Any, str],
            length: int,
            offset: int,
            upload_part: int,
            created_at: str,
    ):
        return FileMetadata(
            uid=uid,
            metadata=metadata,
            length=length,
            offset=offset,
            upload_part=upload_part,
            created_at=created_at
        )


async def get_request_chunk(request: Request, uuid: str = Path(...)):
    # Read the chunk of data from the request body
    body = b''

    meta = read_metadata(uuid)

    with open(f"{FILES_DIR}/{uuid}", "wb") as f:
        async for chunk in request.stream():
            body += chunk
            f.write(chunk)
            meta.offset += len(chunk)
            meta.upload_part += 1
            write_metadata(meta)

        f.close()

    return body


@app.options("/files")
def options_create_upload(response: Response):
    response.headers["Tus-Extension"] = "creation,creation-with-upload,termination,concatenation,creation-defer-length"
    response.headers["Tus-Resumable"] = "1.0.0"
    response.headers["Tus-Version"] = "1.0.0"
    response.headers["Tus-Max-Size"] = "128849018880"


@app.options("/files/{uuid}")
def options_upload_chunk(response: Response, uuid: str):
    meta = read_metadata(uuid)
    if meta is None or not file_exists(uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    response.headers["Tus-Extension"] = "creation,creation-with-upload,termination,concatenation,creation-defer-length"
    response.headers["Tus-Resumable"] = "1.0.0"
    response.headers["Tus-Version"] = "1.0.0"
    response.headers["Tus-Max-Size"] = str(MAX_SIZE)
    response.headers["Content-Length"] = str(meta.offset)


@app.head("/files/{uuid}", status_code=status.HTTP_200_OK)
def get_upload_metadata(response: Response, uuid: str):
    meta = read_metadata(uuid)
    if meta is None or not file_exists(uuid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")

    file_length = get_file_length(uuid)

    response.headers["Tus-Resumable"] = "1.0.0"
    response.headers["Content-Length"] = str(file_length)
    response.headers["Upload-Length"] = str(meta.length)
    response.headers["Upload-Offset"] = str(meta.offset)
    response.headers["Upload-Metadata"] = f"filename {meta.metadata['name']}, filetype dmlkZW8vbXA0"
    response.status_code = status.HTTP_200_OK
    return response


@app.post("/files", status_code=status.HTTP_201_CREATED)
def create_upload(
        request: Request,
        response: Response,
        upload_metadata: str = Header(None),
        upload_length: int = Header(None),
):
    # Create a new upload and store the file and metadata in the mapping
    metadata = {}
    if upload_metadata is not None:
        # Decode the base64-encoded string
        for kv in upload_metadata.split(","):
            key, value = kv.split(" ")
            metadata[key] = base64.b64decode(value).decode("utf-8")

    uuid = str(uuid4().hex)
    write_metadata(FileMetadata.from_request(uuid, metadata, upload_length, 0, 0, str(datetime.now())))
    initialize_file(uuid)

    response.headers["Location"] = f"http://127.0.0.1:8000/files/{uuid}"
    response.headers["Tus-Resumable"] = "1.0.0"
    response.headers["Content-Length"] = "0"
    response.status_code = status.HTTP_201_CREATED
    return response


@app.patch("/files/{uuid}", status_code=status.HTTP_200_OK)
def upload_chunk(
        response: Response,
        uuid: str,
        content_type: str = Header(None),
        content_length: int = Header(None),
        upload_offset: int = Header(None),
        chunk: bytes = Depends(get_request_chunk),
):
    # Check if the Content-Type header is set to "application/offset+octet-stream"
    if content_type != "application/offset+octet-stream":
        raise HTTPException(status_code=415, detail="Unsupported Media Type")

    meta = read_metadata(uuid)
    # Check if the upload ID is valid
    if not meta or uuid != meta.uid:
        raise HTTPException(status_code=404, detail="Upload not found")

    # Check if the Content-Length header is set and matches the chunk size
    if content_length != len(chunk):
        raise HTTPException(status_code=400, detail="Invalid Content-Length")

    # Check if the Upload-Offset header is correct
    if meta.offset != upload_offset:
        raise HTTPException(status_code=409, detail="Conflict")

    file_length = get_file_length(uuid)
    if file_length == 0:
        meta.offset = file_length + len(chunk)
    else:
        meta.offset += len(chunk)
    write_metadata(meta)

    response.headers["Tus-Resumable"] = "1.0.0"
    response.headers["Upload-Offset"] = str(meta.offset)
    response.status_code = status.HTTP_200_OK
    return response


# @app.get("/files/{uuid}")
# def get_upload(uuid: str):
#     # Check if the upload ID is valid
#     if uuid not in upload_files:
#         raise HTTPException(status_code=404, detail="Upload not found")
#     file, filename, file_offset = upload_files[uuid]
#     # Return the file as a streaming response
#     return FileResponse(file, media_type="application/octet-stream", filename=filename)


def write_metadata(meta: FileMetadata) -> None:
    if not os.path.exists(FILES_DIR):
        os.mkdir(FILES_DIR)

    with open(os.path.join(FILES_DIR, f'{meta.uid}.info'), 'w') as f:
        f.write(json.dumps(meta, indent=4, default=lambda k: k.__dict__))


def initialize_file(uid: str) -> None:
    if not os.path.exists(FILES_DIR):
        os.mkdir(FILES_DIR)

    open(os.path.join(FILES_DIR, f'{uid}'), 'a').close()


def read_metadata(uid: str) -> FileMetadata | None:
    fpath = os.path.join(FILES_DIR, f'{uid}.info')
    if os.path.exists(fpath):
        with open(fpath, 'r') as f:
            return FileMetadata(**json.load(f))

    return None


def get_file(uid: str) -> str | None:
    fpath = os.path.join(FILES_DIR, uid)
    if os.path.exists(fpath):
        with open(fpath, 'r') as f:
            return f.read()

    return None


def file_exists(uid: str) -> bool:
    return os.path.exists(os.path.join(FILES_DIR, uid))


def get_file_length(uid: str) -> int:
    return os.path.getsize(os.path.join(FILES_DIR, uid))
