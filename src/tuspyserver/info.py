from __future__ import annotations
import typing

if typing.TYPE_CHECKING:
    from tuspyserver.file import TusUploadFile

from tuspyserver.params import TusUploadParams

import os
import json


class TusUploadInfo:
    _params: TusUploadParams | None
    file: TusUploadFile

    def __init__(self, file: TusUploadFile, params: TusUploadParams | None = None):
        self.file = file

    @property
    def params(self):
        self.deserialize()
        return self._params

    @params.setter
    def params(self, value):
        self._params = value
        self.serialize()

    @property
    def path(self) -> str:
        return os.path.join(self.file.options.files_dir, f"{self.file.uid}.info")

    @property
    def exists(self) -> bool:
        return os.path.exists(self.path)

    def serialize(self) -> None:
        with open(self.path, "w") as f:
            json_string = json.dumps(
                self._params, indent=4, default=lambda k: k.__dict__
            )
            f.write(json_string)

    def deserialize(self) -> TusUploadParams | None:
        if self.exists:
            with open(self.path, "r") as f:
                dict = json.load(f)
                self._params = TusUploadParams(**dict)

        return None
