from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from tusserver.tus import router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router, prefix="/files")
