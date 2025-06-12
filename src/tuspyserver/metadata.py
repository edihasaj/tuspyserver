from typing import Any, Hashable

from pydantic import BaseModel


class FileMetadata(BaseModel):
    uid: str
    metadata: dict[Hashable, str]
    size: int
    offset: int = 0
    upload_part: int = 0
    created_at: str
    defer_length: bool
    upload_chunk_size: int = 0
    expires: float | str | None

    @classmethod
    def from_request(
        cls,
        uid: str,
        metadata: dict[Any, str],
        size: int,
        created_at: str,
        defer_length: bool,
        expires: float | str | None,
    ):
        return FileMetadata(
            uid=uid,
            metadata=metadata,
            size=size,
            created_at=created_at,
            defer_length=defer_length,
            expires=expires,
        )
