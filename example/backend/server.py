import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from tuspyserver import create_tus_router

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


# Pre-Create Hook: validate metadata and upload parameters before file creation
def pre_create_hook(metadata: dict, upload_info: dict):
    print("Pre-Create Hook called")
    print(f"Metadata: {metadata}")
    print(f"Upload info: {upload_info}")

    # Example: Validate required metadata
    if "filename" not in metadata:
        raise HTTPException(status_code=400, detail="Filename is required")

    # Example: Check file size limits (100MB)
    if upload_info["size"] and upload_info["size"] > 100_000_000:
        raise HTTPException(status_code=413, detail="File too large (max 100MB)")

    # Example: Validate file type if provided
    if "filetype" in metadata:
        allowed_types = ["image/jpeg", "image/png", "application/pdf", "text/plain"]
        if metadata["filetype"] not in allowed_types:
            raise HTTPException(
                status_code=400, detail=f"File type {metadata['filetype']} not allowed"
            )

    print("Pre-Create Hook validation passed")


# use completion hook to log uploads
def on_upload_complete(file_path: str, metadata: dict):
    print("Upload complete")
    print(file_path)
    print(metadata)


# mount the tus router to our
app.include_router(
    create_tus_router(
        files_dir="./uploads",
        pre_create_hook=pre_create_hook,
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
