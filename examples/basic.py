from tuspyserver import create_tus_router

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import uvicorn

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
        "Upload-Offset",
        "Tus-Resumable",
        "Tus-Version",
        "Tus-Extension",
        "Tus-Max-Size",
        "Upload-Expires",
    ],
)

# serve an html frontend from the static folder
app.mount("/static", StaticFiles(directory="static"), name="static")


# use completion hook to log uploads
def on_upload_complete(file_path: str, metadata: dict):
    print("Upload complete")
    print(file_path)
    print(metadata)


# mount the tus router to our
app.include_router(
    create_tus_router(
        files_dir="./uploads",
        max_size=128849018880,
        on_upload_complete=on_upload_complete,
        prefix="files",
    )
)

# run the app with uvicorn
if __name__ == "__main__":
    uvicorn.run(
        "basic:app",
        reload=True,
        use_colors=True,
    )
