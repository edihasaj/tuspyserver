from typing import Hashable
from pydantic import BaseModel


class TusUploadParams(BaseModel):
    metadata: dict[Hashable, str]
    size: int
    offset: int = 0
    upload_part: int = 0
    created_at: str
    defer_length: bool
    upload_chunk_size: int = 0
    expires: float | str | None
