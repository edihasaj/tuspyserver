# FastAPI Tus
FastAPI Extension implementing the Tus.io server protocol

### Prerequisites `FastAPI`

## Installation

Installation from PyPi repository (recommended for latest stable release)

```
pip install tuspy-fast-api
```

## Usage

### main.py

```python
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from tus import router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router, prefix="/files")
```

This package has the ability to upload, download, delete (including a scheduler) files.

Any contribution is welcomed.

<script type="text/javascript" src="https://cdnjs.buymeacoffee.com/1.0.0/button.prod.min.js" data-name="bmc-button" data-slug="edihasaj" data-color="#804040" data-emoji="" data-font="Cookie" data-text="Buy me a coffee" data-outline-color="#ffffff" data-font-color="#ffffff" data-coffee-color="#FFDD00" ></script>
