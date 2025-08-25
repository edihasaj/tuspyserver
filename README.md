<a href="https://pypi.org/project/tuspyserver/"><img alt="PyPI - Version" src="https://img.shields.io/pypi/v/tuspyserver" align="right"></a>

# tuspyserver

A FastAPI router implementing a [tus upload protocol](https://tus.io/) server, with optional dependency-injected hooks for post-upload processing.

Only depends on `fastapi>=0.110` and `python>=3.8`.

## Features

* **⏸️ Resumable uploads** via TUS protocol
* **🍰 Chunked transfer** with configurable max size
* **🗃️ Metadata storage** (filename, filetype)
* **🧹 Expiration & cleanup** of old uploads (default retention: 5 days)
* **💉 Dependency injection** for seamless validation (optional)
* **📡 Comprehensive API** with *download*, *HEAD*, *DELETE*, and *OPTIONS* endpoints

## Installation

Install the [latest release from PyPI](https://pypi.org/project/tuspyserver/):

```bash
# with uv
uv add tuspyserver
# with poetry
poetry add tuspyserver
# with pip
pip install tuspyserver
```

Or install directly from source:

```bash
git clone https://github.com/edihasaj/tuspyserver
cd tuspyserver
pip install .
```

## Usage

### API

The main API is a single constructor that initializes the tus router. All arguments are optional, and these are their default values:

```python
from tuspyserver import create_tus_router

tus_router = create_tus_router(
    prefix="files",                                   # route prefix (default: 'files')
    files_dir="/tmp/files",                  # path to store files
    max_size=128_849_018_880,             # max upload size in bytes (default is ~128GB)
    auth=noop,                                              # authentication dependency
    days_to_keep=5,                                   # retention period
    on_upload_complete=None,               # upload callback
    upload_complete_dep=None,             # upload callback (dependency injector)
    pre_create_hook=None,                 # pre-creation callback
    pre_create_dep=None,                  # pre-creation callback (dependency injector)
)
```

### Pre-Create Hook

The Pre-Create Hook allows you to validate metadata and perform authentication **before** a file is created on the server. This is useful for:

- **Metadata validation**: Check if required fields are present, validate file types, etc.
- **User authentication**: Verify user permissions before allowing upload creation
- **Business logic**: Apply custom rules before file creation

The hook receives two parameters:
- `metadata`: A dictionary containing the decoded upload metadata
- `upload_info`: A dictionary with upload parameters (size, defer_length, expires)

```python
def validate_upload(metadata: dict, upload_info: dict):
    # Validate required metadata
    if "filename" not in metadata:
        raise HTTPException(status_code=400, detail="Filename is required")
    
    # Check file size limits
    if upload_info["size"] and upload_info["size"] > 100_000_000:  # 100MB
        raise HTTPException(status_code=413, detail="File too large")
    
    # Validate file type
    if "filetype" in metadata:
        allowed_types = ["image/jpeg", "image/png", "application/pdf"]
        if metadata["filetype"] not in allowed_types:
            raise HTTPException(status_code=400, detail="File type not allowed")

# Use the hook
tus_router = create_tus_router(
    files_dir="./uploads",
    pre_create_hook=validate_upload,
)
```

### Basic setup

In your `main.py`:

```python
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
        "Upload-Length",
    ],
)

# use completion hook to log uploads
def log_upload(file_path: str, metadata: dict):
    print("Upload complete")
    print(file_path)
    print(metadata)


# mount the tus router to our
app.include_router(
    create_tus_router(
        files_dir="./uploads",
        on_upload_complete=log_upload,
    )
)
```

>[!IMPORTANT]
>Headers must be exposed for chunked uploads to work correctly.

For a comprehensive working example, see the [tuspyserver example](#example).

### Dependency injection

For applications using FastAPI's [dependency injection](https://fastapi.tiangolo.com/tutorial/dependencies/), you can supply a factory function that returns a callback with injected dependencies. The factory can `Depends()` on any of your services (database session, current user, etc.).

```python
# Define a factory dependency that injects your own services
from fastapi import Depends
from your_app.dependencies import get_db, get_current_user

# factory function
def log_user_upload(
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> Callable[[str, dict], None]:
    # callback function
    async def handler(file_path: str, metadata: dict):
        # perform validation or post-processing
        await db.log_upload(current_user.id, metadata)
        await process_file(file_path)
    return handler

# Include router with the DI hook
app.include_router(
    create_api_router(
        upload_complete_dep=log_user_upload,
    )
)
```

#### Pre-Create Hook with Dependency Injection

You can also use dependency injection with the Pre-Create Hook for authentication and validation:

```python
from fastapi import Depends, HTTPException
from your_app.dependencies import get_db, get_current_user

def validate_user_upload(
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> Callable[[dict, dict], None]:
    # callback function
    async def handler(metadata: dict, upload_info: dict):
        # Check user permissions
        if not current_user.can_upload:
            raise HTTPException(status_code=403, detail="Upload not allowed")
        
        # Validate against user's quota
        user_uploads = await db.get_user_uploads(current_user.id)
        if len(user_uploads) >= current_user.upload_limit:
            raise HTTPException(status_code=429, detail="Upload quota exceeded")
        
        # Log the upload attempt
        await db.log_upload_attempt(current_user.id, metadata, upload_info)
    
    return handler

# Include router with the pre-create DI hook
app.include_router(
    create_tus_router(
        pre_create_dep=validate_user_upload,
    )
)
```

### Expiration & cleanup

Expired files are removed when `remove_expired_files()` is called. You can schedule it using your preferred background scheduler (e.g., `APScheduler`, `cron`).

```python
from tuspyserver import create_tus_router

from apscheduler.schedulers.background import BackgroundScheduler

tus_router = create_tus_router(
    days_to_keep = 23  # configure retention period; defaults to 5 days
)

scheduler = BackgroundScheduler()
scheduler.add_job(
    lambda: tus_router.remove_expired_files(),
    trigger='cron',
    hour=1,
)
scheduler.start()
```

## Example

You can find a complete working basic example in the [example](https://github/edihasaj/tuspyserver/tree/main/examples) folder.

the example consists of a `backend` serving fastapi with uvicorn, and a `frontend` npm project.

### Running the example

To run the example, you need to install [`uv`](https://docs.astral.sh/uv/) and run the following in the `example/backend` folder:
```bash
uv run server.py
```

Then, in another terminal window, run the following in `example/frontend`:
```bash
npm run dev
```

This should launch the server, and you should now be able to test uploads by browsing to http://localhost:5173.

Uploaded files get placed in the `example/backend/uploads` folder.

## Developing

Contributions welcome! Please open issues or PRs on [GitHub](https://github.com/edihasaj/tuspyserver).

You need [`uv`](https://docs.astral.sh/uv/) to develop the project. The project is setup as a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
where the root is the [library](https://docs.astral.sh/uv/concepts/projects/init/#libraries) and the example directory is an [unpackaged app](https://docs.astral.sh/uv/concepts/projects/init/#applications)

### Releasing

To release the package, follow the following steps:

1. Update the version in `pyproject.toml` using [semver](https://semver.org/)
2. Merge PR to main or push directly to main
3. Open a PR to merge `main` → `production`.
4. Upon merge, CI/CD will publish to PyPI.


*© 2025 Edi Hasaj [X](https://x.com/hasajedi)*
