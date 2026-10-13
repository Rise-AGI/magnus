# back_end/server/routers/files.py
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .auth import get_current_user
from .. import models
from .._magnus_config import magnus_config
from .._file_custody_manager import (
    file_custody_manager,
    FILE_SECRET_PREFIX,
    CustodyLimitError,
    CustodyStorageFullError,
    FileTooLargeError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class FileCustodyResponse(BaseModel):
    file_secret: str


class CustodyFileResponse(FileResponse):
    """Keep custody bytes leased until streaming finishes or the client leaves."""

    def __init__(
        self,
        token: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._custody_token = token

    async def __call__(
        self,
        scope,
        receive,
        send,
    ) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await file_custody_manager.release_file_async(self._custody_token)


@router.post("/files/upload", response_model=FileCustodyResponse)
async def upload_file(
    file: UploadFile,
    expire_minutes: Optional[int] = Form(default=None),
    max_downloads: Optional[int] = Form(default=None),
    is_directory: bool = Form(default=False),
    user: models.User = Depends(get_current_user),
)-> FileCustodyResponse:
    max_ttl = magnus_config["server"]["file_custody"]["max_ttl_minutes"]
    if expire_minutes is not None and expire_minutes > max_ttl:
        raise HTTPException(
            status_code = 400,
            detail = f"expire_minutes exceeds maximum allowed ({max_ttl} min)",
        )
    if max_downloads is not None and max_downloads < 1:
        raise HTTPException(
            status_code = 400,
            detail = "max_downloads must be at least 1",
        )

    filename = file.filename or "upload"
    logger.info(f"File upload from {user.name}: {filename}, expire_minutes={expire_minutes}, max_downloads={max_downloads}")

    try:
        token = await file_custody_manager.store_file_async(
            filename = filename,
            file_obj = file.file,
            expire_minutes = expire_minutes,
            is_directory = is_directory,
            max_downloads = max_downloads,
        )
    except CustodyLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except CustodyStorageFullError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    return FileCustodyResponse(file_secret=f"{FILE_SECRET_PREFIX}{token}")


@router.get("/files/download/{token}")
async def download_file(
    token: str,
):
    result = file_custody_manager.get_file_path(token)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found or expired")

    file_path, filename, is_directory, _ = result
    headers = {}
    if is_directory:
        headers["X-Magnus-Directory"] = "true"

    try:
        return CustodyFileResponse(
            token = token,
            path = str(file_path),
            filename = filename,
            headers = headers,
        )
    except BaseException:
        await file_custody_manager.release_file_async(token)
        raise
