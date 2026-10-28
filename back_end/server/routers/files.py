# back_end/server/routers/files.py
import logging
import re
from pathlib import Path
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from .auth import get_current_user
from .. import database, models
from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string
from .._upload_receive import UploadAdmission, upload_event
from .._blocking_io import _run_blocking_io
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
_receive_config = magnus_config["server"]["file_custody"].get("receive", {})
_upload_admission = UploadAdmission(
    max_concurrent=_receive_config.get("max_concurrent", 0),
    max_body_bytes=_parse_size_string(_receive_config["max_body_size"]) if _receive_config.get("max_body_size") else 0,
    max_spool_bytes=_parse_size_string(_receive_config["max_spool_size"]) if _receive_config.get("max_spool_size") else 0,
    min_free_bytes=_parse_size_string(_receive_config["min_free_space"]) if _receive_config.get("min_free_space") else 0,
)


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
            models.Job.submission_attempt,
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
            or job.submission_attempt is not None
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


class UploadOptions(BaseModel):
    expire_minutes: Optional[int] = None
    max_downloads: Optional[int] = None
    is_directory: bool = False
    job_id: Optional[str] = None


def custody_upload_job(
    job_id: Optional[str],
    db: Session,
    user: models.User,
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


@router.post(
    "/files/upload",
    response_model=FileCustodyResponse,
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["file"], "properties": {
            **UploadOptions.model_json_schema()["properties"],
            "file": {"type": "string", "format": "binary"},
        }},
    }}}},
)
async def upload_file(
    request: Request,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
) -> FileCustodyResponse:
    # Declaring Form/UploadFile would let FastAPI spool the body before auth.
    async with _upload_admission.receive(request):
        async with request.form(max_files=1, max_fields=4, max_part_size=65536) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(422, "An uploaded file is required")
            try:
                options = UploadOptions.model_validate({
                    name: form[name]
                    for name in UploadOptions.model_fields
                    if name in form and form[name] != ""
                })
            except ValidationError as error:
                raise RequestValidationError(error.errors()) from None
            max_ttl = magnus_config["server"]["file_custody"]["max_ttl_minutes"]
            if options.expire_minutes is not None and options.expire_minutes > max_ttl:
                raise HTTPException(400, f"expire_minutes exceeds maximum allowed ({max_ttl} min)")
            if options.max_downloads is not None and options.max_downloads < 1:
                raise HTTPException(400, "max_downloads must be at least 1")
            # The SQLite session belongs to the dependency's worker; do not run
            # its blocking lookup on the event loop or concurrently use it.
            job_id = await _run_blocking_io(custody_upload_job, options.job_id, db, user)
            upload_event(request, "parsed", file_bytes=file.size)
            try:
                upload_event(request, "storing")
                token = await file_custody_manager.store_file_async(
                    filename=file.filename or "upload",
                    file_obj=file.file,
                    expire_minutes=options.expire_minutes,
                    is_directory=options.is_directory,
                    max_downloads=options.max_downloads,
                    job_id=job_id,
                )
            except CustodyLimitError as error:
                raise HTTPException(429, str(error)) from None
            except CustodyStorageFullError as error:
                raise HTTPException(503, str(error)) from None
            except FileTooLargeError as error:
                raise HTTPException(413, str(error)) from None
            upload_event(request, "receipt_committed")
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
