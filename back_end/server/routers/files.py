# back_end/server/routers/files.py
import logging
import re
from pathlib import Path
from typing import Literal, Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import get_current_user
from .. import database, models
from .._magnus_config import magnus_config
from .._custody_receipts import _read_custody_receipts, _RELAY_TOKEN_RE
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


class FileCustodyStatusResponse(BaseModel):
    status: Literal["ready", "pending", "unavailable", "unknown"]
    job_transfers_pending: Optional[bool] = None


@router.get("/files/status/{token}", response_model=FileCustodyStatusResponse)
def get_file_status(
    token: str,
    response: Response,
    job_id: Optional[str] = Query(default=None),
    db: Session = Depends(database.get_db),
    _: models.User = Depends(get_current_user),
) -> FileCustodyStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    job = None
    transfers_pending = None
    if job_id is not None:
        job = db.query(
            models.Job.id,
            models.Job.status,
            models.Job.slurm_job_id,
            models.Job.finalization_status,
        ).filter(models.Job.id == job_id).first()
        if job is None:
            raise HTTPException(
                status_code = 404,
                detail = "Job not found",
                headers = {"Cache-Control": "no-store"},
            )
        transfers_pending = job.finalization_status != "CLEANUP" and (
            job.slurm_job_id is not None
            or job.finalization_status is not None
            or job.status not in (models.JobStatus.SUCCESS, models.JobStatus.FAILED, models.JobStatus.TERMINATED)
        )

    status = file_custody_manager.get_file_status(token)
    if status == "unknown" and job is not None:
        # The producer is caller-supplied context, not proof this token exists.
        # A durable receipt prevents waiting for an import that already expired
        # or was consumed, even while other outputs are still transferring.
        try:
            local_table = Path(magnus_config["server"]["root"]) / "workspace" / "jobs" / job.id
            imported = bool(_RELAY_TOKEN_RE.fullmatch(token)) and token in _read_custody_receipts(str(local_table))["entries"]
            if not imported and _RELAY_TOKEN_RE.fullmatch(token):
                try:
                    imported = file_custody_manager.import_receipt(job.id, token) is not None
                except ValueError:
                    # Caller-supplied Job context is not proof of token ownership.
                    pass
        except (OSError, ValueError):
            raise HTTPException(
                status_code = 503,
                detail = "Custody import status is temporarily unavailable",
                headers = {"Cache-Control": "no-store"},
            ) from None
        # Import and observation may overlap; recheck after reading the ledger.
        status = file_custody_manager.get_file_status(token)
        if status == "unknown" and (imported or not transfers_pending):
            status = "unavailable"
    return FileCustodyStatusResponse(
        status = status,
        job_transfers_pending = transfers_pending,
    )


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


def custody_upload_job(
    job_id: Optional[str] = Form(default=None),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(get_current_user),
) -> Optional[str]:
    if job_id is None:
        return None
    if not re.fullmatch(r"[0-9a-f]{16}", job_id):
        raise HTTPException(status_code=400, detail="Invalid Job ID")
    job = db.query(models.Job.user_id, models.Job.status).filter(models.Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Custody upload must belong to your Job")
    if job.status in (models.JobStatus.SUCCESS, models.JobStatus.FAILED, models.JobStatus.TERMINATED):
        raise HTTPException(status_code=409, detail="Job no longer accepts new custody publications")
    return job_id


@router.post("/files/upload", response_model=FileCustodyResponse)
async def upload_file(
    file: UploadFile,
    expire_minutes: Optional[int] = Form(default=None),
    max_downloads: Optional[int] = Form(default=None),
    is_directory: bool = Form(default=False),
    user: models.User = Depends(get_current_user),
    job_id: Optional[str] = Depends(custody_upload_job),
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
            job_id = job_id,
        )
    except CustodyLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except CustodyStorageFullError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e))
    return FileCustodyResponse(file_secret=f"{FILE_SECRET_PREFIX}{token}")


@router.get("/files/download/{token}")
def download_file(
    token: str,
):
    result = file_custody_manager.get_file_path(token)
    if result is None:
        raise HTTPException(
            status_code = 404,
            detail = "File not found or expired",
            headers = {"Cache-Control": "no-store"},
        )

    file_path, filename, is_directory, _ = result
    headers = {"Cache-Control": "no-store"}
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
        file_custody_manager.release_file(token)
        raise
