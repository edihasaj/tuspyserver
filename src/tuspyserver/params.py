from typing import Dict, Hashable, List, Optional, Union

from pydantic import BaseModel


class TusUploadParams(BaseModel):
    metadata: Dict[Hashable, str]
    size: Optional[int]
    offset: int = 0
    upload_part: int = 0
    created_at: str
    defer_length: bool = False
    upload_chunk_size: int = 0
    expires: Optional[Union[float, str]]
    error: Optional[str] = None
    is_partial: bool = False
    is_final: bool = False
    partial_uploads: Optional[List[str]] = None
