from copy import deepcopy
from typing import Callable
from fastapi import Depends, HTTPException, Response, status
import inspect 

from tuspyserver.file import TusUploadFile


def termination_extension_routes(router, options):
    """
    https://tus.io/protocols/resumable-upload#termination
    """

    @router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    async def extension_termination_route(
        uuid: str, response: Response, _=Depends(options.auth),
        file_dep: Callable[[dict], None] = Depends(options.file_dep),
    ) -> Response:
        # Create a copy of options to avoid mutating the original
        file_options = deepcopy(options)
        result = file_dep({})

        # if the callback returned a coroutine, await it
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            file_options.files_dir = result.get("files_dir", options.files_dir)
        file = TusUploadFile(uid=uuid, options=file_options)

        # Check if the upload ID is valid
        if not file.exists:
            raise HTTPException(status_code=404, detail="Upload not found")

        # Delete the file and metadata for the upload from the mapping
        file.delete(uuid)

        # Return a 204 No Content response
        response.headers["Tus-Resumable"] = options.tus_version
        response.status_code = status.HTTP_204_NO_CONTENT

        return response

    return router
