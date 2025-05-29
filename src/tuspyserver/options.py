from typing import Callable

from pydantic import BaseModel


class TusOptions(BaseModel):
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


def init_tus_options(
    prefix: str,
    files_dir: str,
    max_size: int,
    auth: Callable[[], None] | None,
    days_to_keep: int,
    on_upload_complete: Callable[[str, dict], None] | None,
    upload_complete_dep: Callable[..., Callable[[str, dict], None]] | None,
    tags: list[str] | None,
    tus_version: str,
    tus_extension: list[str],
) -> TusOptions:
    if prefix and prefix[0] == "/":
        prefix = prefix[1:]

    async def _fallback_on_complete_dep() -> Callable[[str, dict], None]:
        return on_upload_complete or (lambda *_: None)

    upload_complete_dep = upload_complete_dep or _fallback_on_complete_dep

    return TusOptions(
        prefix=prefix,
        files_dir=files_dir,
        max_size=max_size,
        auth=auth,
        days_to_keep=days_to_keep,
        on_upload_complete=on_upload_complete,
        upload_complete_dep=upload_complete_dep,
        tags=tags,
        tus_version=tus_version,
        tus_extension=",".join(tus_extension),
    )
