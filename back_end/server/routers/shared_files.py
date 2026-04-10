import os
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import models
from .._magnus_config import is_admin_user
from .._shared_file_manager import (
    shared_file_manager,
    SharedFileRateLimitError,
    SharedFileValidationError,
    SharedFileNotFoundError,
    SharedFileInvalidatedError,
)
from .auth import get_current_user


router = APIRouter()


class SharedFileCreateRequest(BaseModel):
    expire_days: int = Field(..., ge=7, le=90)
    expected_size_gb: int = Field(..., ge=1, le=800)


class SharedFileCreateResponse(BaseModel):
    token: str
    expire_at: str
    expected_size_gb: int


class SharedFileUpdateRequest(BaseModel):
    expected_size_gb: Optional[int] = Field(None, ge=1, le=800)
    extend_days: Optional[int] = Field(None, ge=1, le=90)


class SharedFileRestoreRequest(BaseModel):
    new_expire_days: Optional[int] = Field(None, ge=7, le=90)


@router.post("/shared-files", response_model=SharedFileCreateResponse)
def create_shared_file(
    payload: SharedFileCreateRequest,
    current_user: models.User = Depends(get_current_user),
):
    """创建共享文件夹"""
    try:
        created = shared_file_manager.create_shared_folder(current_user.id, payload.expire_days, payload.expected_size_gb)
    except SharedFileRateLimitError as error:
        raise HTTPException(status_code=429, detail=str(error))
    except SharedFileValidationError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {
        "token": created["token"],
        "expire_at": created["expire_at"],
        "expected_size_gb": created["expected_size_gb"],
    }


@router.get("/shared-files/{token}")
def get_shared_file_info(
    token: str,
    current_user: models.User = Depends(get_current_user),
):
    """获取共享文件夹信息"""
    try:
        info = shared_file_manager.get_shared_folder_info(token)
    except SharedFileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    
    # 检查权限：创建者或管理员可以看到更多信息
    is_creator = info.get("created_by") == current_user.id
    is_admin = is_admin_user(current_user)
    
    # 公共信息
    result = {
        "token": info.get("token"),
        "status": info.get("status"),
    }
    
    # 创建者/管理员可以看到详细信息
    if is_creator or is_admin:
        result.update({
            "created_at": info.get("created_at"),
            "expire_at": info.get("expire_at"),
            "expected_size_gb": info.get("expected_size_gb"),
            "actual_size_bytes": info.get("actual_size_bytes"),
            "is_creator": is_creator,
            "is_admin": is_admin,
        })
        if info.get("status") == "archived":
            result["archive_path"] = info.get("archive_path")
    
    return result


@router.get("/shared-files/{token}/files")
def list_shared_files(
    token: str,
    path: str = "",
    current_user: models.User = Depends(get_current_user),
):
    """列出共享文件夹中的文件"""
    try:
        files = shared_file_manager.list_files(token, path)
    except SharedFileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except SharedFileInvalidatedError as error:
        raise HTTPException(status_code=410, detail=str(error))
    except SharedFileValidationError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"files": files}


@router.get("/shared-files/{token}/download")
def download_shared_file(
    token: str,
    path: str,
    current_user: models.User = Depends(get_current_user),
):
    """下载共享文件夹中的文件"""
    try:
        file_path = shared_file_manager.get_file_path(token, path)
    except SharedFileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except SharedFileInvalidatedError as error:
        raise HTTPException(status_code=410, detail=str(error))
    except SharedFileValidationError as error:
        raise HTTPException(status_code=400, detail=str(error))
    
    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    
    return FileResponse(
        path=file_path,
        filename=file_path.name,
    )


@router.patch("/shared-files/{token}")
def update_shared_file(
    token: str,
    payload: SharedFileUpdateRequest,
    current_user: models.User = Depends(get_current_user),
):
    """更新共享文件夹属性（仅创建者或管理员）"""
    is_admin = is_admin_user(current_user)
    
    try:
        updated = shared_file_manager.update_properties(
            token=token,
            user_id=current_user.id,
            is_admin=is_admin,
            expected_size_gb=payload.expected_size_gb,
            extend_days=payload.extend_days,
        )
    except SharedFileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except SharedFileValidationError as error:
        raise HTTPException(status_code=403, detail=str(error))
    
    return {
        "token": updated["token"],
        "expire_at": updated["expire_at"],
        "expected_size_gb": updated["expected_size_gb"],
        "actual_size_bytes": updated.get("actual_size_bytes"),
    }


@router.post("/shared-files/{token}/restore")
def restore_shared_file(
    token: str,
    payload: Optional[SharedFileRestoreRequest] = None,
    current_user: models.User = Depends(get_current_user),
):
    """从归档恢复共享文件夹（仅创建者或管理员）"""
    is_admin = is_admin_user(current_user)
    new_expire_days = payload.new_expire_days if payload else None
    
    try:
        restored = shared_file_manager.restore_archived(
            token=token,
            user_id=current_user.id,
            is_admin=is_admin,
            new_expire_days=new_expire_days,
        )
    except SharedFileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except SharedFileValidationError as error:
        raise HTTPException(status_code=403, detail=str(error))
    
    return {
        "token": restored["token"],
        "expire_at": restored["expire_at"],
        "status": "active",
    }