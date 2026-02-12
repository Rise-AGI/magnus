# back_end/server/routers/files.py
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .auth import get_current_user
from .. import models
from .._magnus_config import magnus_config
from .._file_custody_manager import file_custody_manager, FILE_SECRET_PREFIX

logger = logging.getLogger(__name__)
router = APIRouter()


class FileCustodyResponse(BaseModel):
    file_secret: str


@router.post("/files/upload", response_model=FileCustodyResponse)
async def upload_file(
    file: UploadFile,
    expire_minutes: Optional[int] = Form(default=None),
    is_directory: bool = Form(default=False),
    user: models.User = Depends(get_current_user),
) -> FileCustodyResponse:
    max_ttl = magnus_config["server"]["file_custody"]["max_ttl_minutes"]
    if expire_minutes is not None and expire_minutes > max_ttl:
        raise HTTPException(
            status_code=400,
            detail=f"expire_minutes exceeds maximum allowed ({max_ttl} min)",
        )

    filename = file.filename or "upload"
    logger.info(f"File upload from {user.name}: {filename}, expire_minutes={expire_minutes}")

    token = await asyncio.to_thread(
        file_custody_manager.store_file,
        filename,
        file.file,
        expire_minutes,
        is_directory,
    )
    return FileCustodyResponse(file_secret=f"{FILE_SECRET_PREFIX}{token}")


@router.get("/files/download/{token}")
async def download_file(token: str):
    result = file_custody_manager.get_file_path(token)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found or expired")

    file_path, filename, is_directory = result
    headers = {}
    if is_directory:
        headers["X-Magnus-Directory"] = "true"

    return FileResponse(
        path=str(file_path),
        filename=filename,
        headers=headers,
    )
