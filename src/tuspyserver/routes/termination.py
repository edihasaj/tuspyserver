from fastapi import Depends, HTTPException, Response, status

from tuspyserver.file import TusUploadFile


def termination_extension_routes(router, options):
    """
    https://tus.io/protocols/resumable-upload#termination
    """

    @router.delete("/{uuid}", status_code=status.HTTP_204_NO_CONTENT)
    def extension_termination_route(
        uuid: str, response: Response, _=Depends(options.auth)
    ) -> Response:
        file = TusUploadFile(uid=uuid, options=options)

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
