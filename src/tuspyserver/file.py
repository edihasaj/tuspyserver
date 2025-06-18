from __future__ import annotations
import typing

if typing.TYPE_CHECKING:
    from tuspyserver.router import TusRouterOptions

import os
import datetime

from uuid import uuid4

from tuspyserver.params import TusUploadParams
from tuspyserver.info import TusUploadInfo


class TusUploadFile:
    uid: str
    _info: TusUploadInfo
    _options: TusRouterOptions

    def __init__(
        self,
        uid: str | None,
        options: TusRouterOptions,
        params: TusUploadParams | None = None,
    ):
        # generate uuid if not provided
        self.uid = uid or str(uuid4().hex)
        self._options = options
        # create the files dir if necessary
        if not os.path.exists(self._options.files_dir):
            os.makedirs(self._options.files_dir)
        # instantiate upload info
        self._info = TusUploadInfo(file=self, params=params)

    @property
    def path(self) -> str:
        return os.path.join(self._options.files_dir, f"{self.uid}")

    @property
    def options(self) -> TusRouterOptions:
        return self._options

    @property
    def info(self) -> TusUploadParams:
        return self._info.params

    @info.setter
    def info(self, value) -> None:
        self._info.params = value

    def create(self) -> None:
        open(self.path, "a").close()

    def read(self) -> bytes | None:
        fpath = os.path.join(self._options.files_dir, self.uid)
        if os.path.exists(fpath):
            with open(fpath, "rb") as f:
                return f.read()
        return None

    @property
    def exists(self) -> bool:
        return os.path.exists(os.path.join(self._options.files_dir, self.uid))

    def delete(self, uid: str) -> None:
        fpath = os.path.join(self._options.files_dir, uid)
        if os.path.exists(fpath):
            os.remove(fpath)

        meta_path = os.path.join(self._options.files_dir, f"{uid}.info")
        if os.path.exists(meta_path):
            os.remove(meta_path)

    def __len__(self, uid: str) -> int:
        return os.path.getsize(os.path.join(self._options.files_dir, uid))

    # info


def gc_files(options: TusRouterOptions):
    # use filename length as heuristic to determine uuid files
    uids = [f for f in os.listdir(options.files_dir) if len(f) == 32]

    for uid in uids:
        file = TusUploadFile(uid=uid)
        if (
            file.params.expires
            and datetime.fromisoformat(file.info.expires) < datetime.now()
        ):
            file.delete()
