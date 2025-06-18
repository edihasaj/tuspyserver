from tuspyserver import create_tus_router

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# initialize a FastAPI app
app = FastAPI()

# configure cross-origin middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Location",
        "Tus-Resumable",
        "Tus-Version",
        "Tus-Extension",
        "Tus-Max-Size",
        "Upload-Offset",
        "Upload-Length",
        "Upload-Expires",
    ],
)


# use completion hook to log uploads
def on_upload_complete(file_path: str, metadata: dict):
    print("Upload complete")
    print(file_path)
    print(metadata)


# mount the tus router to our app
app.include_router(
    create_tus_router(
        files_dir="/app/uploads",
        max_size=128849018880,
        on_upload_complete=on_upload_complete,
        prefix="files",
    )
)
