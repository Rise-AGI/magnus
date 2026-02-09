# back_end/server/routers/files.py
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from .auth import get_current_user
from .. import models
from .._magnus_config import magnus_config
from .._file_custody_manager import file_custody_manager

logger = logging.getLogger(__name__)
router = APIRouter()


class FileCustodyRequest(BaseModel):
    file_secret: str
    expire_minutes: Optional[int] = None


class FileCustodyResponse(BaseModel):
    file_secret: str


@router.post("/files/custody", response_model=FileCustodyResponse)
async def custody_file(
    request: FileCustodyRequest,
    user: models.User = Depends(get_current_user),
) -> FileCustodyResponse:
    max_ttl_minutes = magnus_config["server"]["file_custody"]["max_ttl_minutes"]
    expire_minutes = request.expire_minutes
    if expire_minutes is not None and expire_minutes > max_ttl_minutes:
        raise HTTPException(
            status_code=400,
            detail=f"expire_minutes exceeds maximum allowed ({max_ttl_minutes} min)",
        )

    logger.info(f"File custody request from {user.name}: secret={request.file_secret[:20]}..., expire_minutes={expire_minutes}")

    new_secret = await file_custody_manager.custody_async(
        file_secret=request.file_secret,
        expire_minutes=expire_minutes,
    )
    return FileCustodyResponse(file_secret=new_secret)
