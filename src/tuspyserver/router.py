import inspect
from typing import Callable, List, Optional

from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from tuspyserver.routes.core import core_routes
from tuspyserver.routes.creation import creation_extension_routes
from tuspyserver.routes.termination import termination_extension_routes


class TusRouterOptions(BaseModel):
    prefix: str
    files_dir: str
    max_size: int
    auth: Optional[Callable[[], None]]
    days_to_keep: int
    on_upload_complete: Optional[Callable[[str, dict], None]]
    upload_complete_dep: Optional[Callable[..., Callable[[str, dict], None]]]
    pre_create_hook: Optional[Callable[[dict, dict], None]]
    pre_create_dep: Optional[Callable[..., Callable[[dict, dict], None]]]
    file_dep: Optional[Callable[..., Callable[[dict], None]]]
    tags: Optional[List[str]]
    tus_version: str
    tus_extension: str
    strict_offset_validation: bool


async def noop():
    pass


class TusAPIRoute(APIRoute):
    """Route that honors tus X-HTTP-Method-Override before dispatch."""

    _method_override_header = b"x-http-method-override"

    @classmethod
    def _scope_with_method_override(cls, scope: Scope) -> Scope:
        if scope["type"] != "http":
            return scope

        for name, value in scope.get("headers", []):
            if name.lower() == cls._method_override_header:
                override = value.decode("latin-1").strip().upper()
                override_scope = dict(scope)
                override_scope["method"] = override
                return override_scope

        return scope

    def matches(self, scope: Scope):
        return super().matches(self._scope_with_method_override(scope))

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        await super().handle(self._scope_with_method_override(scope), receive, send)


def create_tus_router(
    prefix: str = "files",
    files_dir="/tmp/files",
    max_size=128849018880,
    auth: Optional[Callable[[], None]] = noop,
    days_to_keep: int = 5,
    on_upload_complete: Optional[Callable[[str, dict], None]] = None,
    upload_complete_dep: Optional[Callable[..., Callable[[str, dict], None]]] = None,
    pre_create_hook: Optional[Callable[[dict, dict], None]] = None,
    pre_create_dep: Optional[Callable[..., Callable[[dict, dict], None]]] = None,
    file_dep: Optional[Callable[..., Callable[[dict], None]]] = None,
    tags: Optional[List[str]] = None,
    strict_offset_validation: bool = False,
):
    async def _fallback_on_complete_dep() -> Callable[[str, dict], None]:
        return on_upload_complete or (lambda *_: None)

    async def _fallback_pre_create_dep() -> Callable[[dict, dict], None]:
        return pre_create_hook or (lambda *_: None)

    async def _fallback_file_dep() -> Callable[[dict], None]:
        return lambda metadata: None

    upload_complete_dep = upload_complete_dep or _fallback_on_complete_dep
    pre_create_dep = pre_create_dep or _fallback_pre_create_dep
    file_dep = file_dep or _fallback_file_dep

    options = TusRouterOptions(
        prefix=prefix[1:] if prefix and prefix[0] == "/" else prefix,
        files_dir=files_dir,
        max_size=max_size,
        auth=auth,
        days_to_keep=days_to_keep,
        on_upload_complete=on_upload_complete,
        upload_complete_dep=upload_complete_dep
        or (lambda _: on_upload_complete or (lambda *_: None)),
        pre_create_hook=pre_create_hook,
        pre_create_dep=pre_create_dep
        or (lambda _: pre_create_hook or (lambda *_: None)),
        file_dep=file_dep,
        tags=tags,
        tus_version="1.0.0",
        tus_extension=",".join(
            [
                "creation",
                "creation-defer-length",
                "creation-with-upload",
                "expiration",
                "termination",
                "concatenation",
            ]
        ),
        strict_offset_validation=strict_offset_validation,
    )

    clean_prefix = prefix.lstrip("/").rstrip("/")
    router = APIRouter(
        prefix=f"/{clean_prefix}" if clean_prefix else "",
        redirect_slashes=True,
        route_class=TusAPIRoute,
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
