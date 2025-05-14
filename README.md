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

You can find a complete working basic example in the [examples](https://github/edihasaj/tuspyserver/tree/main/examples) folder.

The example consists of the following:

```bash
basic.py           # backend: a tus router added to a fastapi app and runs it with uvicorn
static/index.html  # frontend: a simple static HTML file using uppy (based on tus-js-client)
```

To run it, you need to install [`uv`](https://docs.astral.sh/uv/) and run:
```bash
uv run basic.py
```

This should launch the server, and you should now be able to test uploads by browsing to http://localhost:8000/static/index.html.

Uploaded files get placed in the `examples/uploads` folder.

## Developing

Contributions welcome! Please open issues or PRs on [GitHub](https://github.com/edihasaj/tuspyserver).

You need [`uv`](https://docs.astral.sh/uv/) to develop the project. The project is setup as a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
where the root is the [library](https://docs.astral.sh/uv/concepts/projects/init/#libraries) and the examples directory is an [unpackagedapplication](https://docs.astral.sh/uv/concepts/projects/init/#applications)

### Releasing

To release the package, follow the following steps:

1. Update the version in `pyproject.toml` using [semver](https://semver.org/)
2. Merge PR to main or push directly to main
3. Open a PR to merge `main` → `production`.
4. Upon merge, CI/CD will publish to PyPI.


*© 2025 Edi Hasaj [X](https://x.com/hasajedi)*
