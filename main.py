from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from tusserver.tus import create_api_router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(
    create_api_router(
        files_dir='/tmp/filezz',
        location='http://127.0.0.1:8000/files',
        max_size=128849018880
    ),
    prefix="/files"
)
