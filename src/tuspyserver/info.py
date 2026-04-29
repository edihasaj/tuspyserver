from __future__ import annotations

import typing
from typing import Optional

if typing.TYPE_CHECKING:
    from tuspyserver.file import TusUploadFile

import json
import os

from tuspyserver.params import TusUploadParams


class TusUploadInfo:
    _params: Optional[TusUploadParams]
    _loaded: bool
    file: TusUploadFile

    def __init__(self, file: TusUploadFile, params: Optional[TusUploadParams] = None):
        self.file = file
        self._params = params
        self._loaded = params is not None  # If params provided, consider them loaded
        # create if doesn't exist
        if params and not self.exists:
            self.serialize()

    @property
    def params(self):
        # Only deserialize if we haven't loaded params yet
        # This prevents overwriting in-memory params on every access
        if not self._loaded:
            self.deserialize()
            self._loaded = True
        return self._params

    @params.setter
    def params(self, value):
        self._params = value
        self._loaded = True  # Mark as loaded since we're explicitly setting params
        self.serialize()

    @property
    def path(self) -> str:
        return os.path.join(self.file.options.files_dir, f"{self.file.uid}.info")

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)

    def serialize(self) -> None:
        """
        Atomically serialize params to info file.
        
        Uses a temporary file and atomic rename to prevent corruption
        from concurrent writes.
        """
        # Write to temporary file first
        temp_path = f"{self.path}.tmp"
        try:
            with open(temp_path, "w") as f:
                json_string = json.dumps(
                    self._params, indent=4, default=lambda k: k.__dict__
                )
                f.write(json_string)
                f.flush()
                # Ensure data is written to disk
                os.fsync(f.fileno())
            
            # Atomic rename - this is atomic on Unix systems
            os.rename(temp_path, self.path)
        except Exception:
            # Clean up temp file if rename fails
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            raise

    def deserialize(self) -> Optional[TusUploadParams]:
        if self.exists:
            try:
                with open(self.path, "r") as f:
                    content = f.read().strip()
                    if not content:  # Handle empty files
                        return None
                    json_dict = json.loads(content)
                    if json_dict:  # Only create params if we have valid data
                        self._params = TusUploadParams(**json_dict)
                    else:
                        self._params = None
            except (json.JSONDecodeError, FileNotFoundError, KeyError, TypeError):
                # Handle corrupted JSON or missing required fields
                self._params = None
        else:
            self._params = None

        return self._params
