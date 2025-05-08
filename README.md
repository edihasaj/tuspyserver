# FastAPI Tus

A FastAPI extension implementing the [Tus.io](https://tus.io/) upload protocol, with optional dependency-injected hooks for post-upload processing.

## Prerequisites

* **Python** 3.8+
* **FastAPI** 0.70+
* **Starlette** (installed as FastAPI dependency)

## Installation

Install the latest stable release from PyPI:

```bash
pip install tuspyserver
```

Or install directly from source:

```bash
git clone https://github.com/your-org/tuspyserver.git
cd tuspyserver
pip install .
```

## Usage

### Basic setup

In your `main.py`:

```python
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

# Optional: define a simple completion hook
def on_upload_complete(file_path: str, metadata: dict):
    print("Upload complete:", file_path)
    print("Metadata:", metadata)

# Include the router
app.include_router(
    create_api_router(
        files_dir="/tmp/uploads",        # OPTIONAL: directory to store files
        max_size=128_849_018_880,         # OPTIONAL: max upload size in bytes (~120GB)
        on_upload_complete=on_upload_complete,  # OPTIONAL: callback when upload finishes
        prefix="files",                 # OPTIONAL: URL prefix (default: 'files')
    ),
)
```

### Dependency‑Injected Hook (Advanced)

For applications using FastAPI's dependency injection, you can supply a factory that returns a DI‑enabled callback. This factory can `Depends()` on any of your services (database session, current user, etc.).

```python
# Define a factory dependency that injects your own services
from fastapi import Depends
from your_app.dependencies import get_db, get_current_user

def get_upload_handler(
    db=Depends(get_db),
    current_user=Depends(get_current_user),
) -> Callable[[str, dict], None]:
    def handler(file_path: str, metadata: dict):
        # perform validation or post-processing
        db.log_upload(current_user.id, metadata)
        process_file(file_path)
    return handler

# Include router with the DI hook
app.include_router(
    create_api_router(
        on_upload_complete=None,            # keep default
        upload_complete_dep=get_upload_handler,  # factory dependency
    )
)
```

### Features

* **Resumable uploads** via TUS protocol
* **Chunked transfer** with configurable max size
* **Metadata storage** (filename, filetype)
* **Expiration & cleanup** of old uploads (default retention: 5 days)
* **Download**, **HEAD**, **DELETE**, and **OPTIONS** endpoints
* **Optional** DI‑friendly `upload_complete_dep` for seamless integration with FastAPI

## Scheduler (Cleanup)

Expired files are removed when `remove_expired_files()` is called. You can schedule it using your preferred background scheduler (e.g., `APScheduler`, `cron`).

```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(
    lambda: router.remove_expired_files(),
    trigger='cron',
    hour=1,
)
scheduler.start()
```

## Versioning & Publishing

**Before releasing:**

1. Update the version in `setup.py`:

   * **Patch** for bug fixes
   * **Minor** for new features
   * **Major** for breaking changes
2. Commit and push to `main`.
3. Open a PR to merge `main` → `production`.
4. Upon merge, CI/CD will publish to PyPI.

## Contributing

Contributions welcome! Please open issues or PRs on [GitHub](https://github.com/your-org/tuspyserver).

*© 2025 Edi Hasaj [X](https://x.com/hasajedi)*
