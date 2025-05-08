from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from tusserver.tus import create_api_router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")


def on_upload_complete(file_path: str, metadata: dict):
    print("Upload complete")
    print(file_path)
    print(metadata)


def on_your_specific_auth():
    pass


app.include_router(
    create_api_router(
        files_dir="/tmp/different_dir",
        max_size=128849018880,
        on_upload_complete=on_upload_complete,
        auth=on_your_specific_auth,
        prefix="files",
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Location",
        "Upload-Offset",
        "Tus-Resumable",
        "Tus-Version",
        "Tus-Extension",
        "Tus-Max-Size",
        "Upload-Expires",
    ],
)
