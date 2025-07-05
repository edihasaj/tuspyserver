from typing import Callable, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from tuspyserver.routes.core import core_routes
from tuspyserver.routes.creation import creation_extension_routes
from tuspyserver.routes.termination import termination_extension_routes


class TusRouterOptions(BaseModel):
    prefix: str
    files_dir: str
    max_size: int
    auth: Callable[[], None] | None
    days_to_keep: int
    on_upload_complete: Callable[[str, dict], None] | None
    upload_complete_dep: Callable[..., Callable[[str, dict], None]] | None
    tags: list[str] | None
    tus_version: str
    tus_extension: str


async def noop():
    pass


def create_tus_router(
    prefix: str = "files",
    files_dir="/tmp/files",
    max_size=128849018880,
    auth: Optional[Callable[[], None]] = noop,
    days_to_keep: int = 5,
    on_upload_complete: Optional[Callable[[str, dict], None]] = None,
    upload_complete_dep: Optional[Callable[..., Callable[[str, dict], None]]] = None,
    tags: Optional[list[str]] = None,
):
    async def _fallback_on_complete_dep() -> Callable[[str, dict], None]:
        return on_upload_complete or (lambda *_: None)

    upload_complete_dep = upload_complete_dep or _fallback_on_complete_dep

    options = TusRouterOptions(
        prefix=prefix[1:] if prefix and prefix[0] == "/" else prefix,
        files_dir=files_dir,
        max_size=max_size,
        auth=auth,
        days_to_keep=days_to_keep,
        on_upload_complete=on_upload_complete,
        upload_complete_dep=upload_complete_dep
        or (lambda _: on_upload_complete or (lambda *_: None)),
        tags=tags,
        tus_version="1.0.0",
        tus_extension=",".join(
            [
                "creation",
                "creation-defer-length",
                "creation-with-upload",
                "expiration",
                "termination",
            ]
        ),
    )

    clean_prefix = prefix.lstrip("/").rstrip("/")
    router = APIRouter(
        prefix=f"/{clean_prefix}" if clean_prefix else "",
        redirect_slashes=True,
        tags=options.tags or ["Tus"],
    )

    modules = [
        core_routes,
        # extensions
        creation_extension_routes,
        termination_extension_routes,
    ]

    for mod in modules:
        router = mod(router, options)

    return router
