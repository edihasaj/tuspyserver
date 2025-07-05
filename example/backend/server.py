from tuspyserver import create_tus_router

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


# mount the tus router to our
app.include_router(
    create_tus_router(
        files_dir="./uploads",
        on_upload_complete=on_upload_complete,
        prefix="files",
    )
)

# run the app with uvicorn
if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        reload=True,
        use_colors=True,
    )
