"""tus 1.0 reception with existing custody receipts as the final result."""

import asyncio
import base64
import binascii
from email.utils import formatdate
import json
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.requests import ClientDisconnect
from sqlalchemy.orm import Session

from .auth import get_current_user
from .files import UploadOptions, custody_upload_job
from .. import database, models
from .._blocking_io import _run_blocking_io
from .._file_custody_manager import file_custody_manager, FILE_SECRET_PREFIX
from .._file_custody_manager._uploads import UploadStore, UploadError, CHUNK_SIZE

router = APIRouter()
upload_store = UploadStore(file_custody_manager)
TUS = {"Tus-Resumable": "1.0.0", "Cache-Control": "no-store"}


def require_version(
    request: Request,
) -> None:
    if request.headers.get("tus-resumable") != "1.0.0":
        raise HTTPException(
            412,
            "Unsupported tus version",
            headers={**TUS, "Tus-Version": "1.0.0"},
        )


def upload_headers(
    row: dict,
) -> dict:
    return {
        **TUS,
        "Upload-Offset": str(row["offset"]),
        "Upload-Length": str(row["length"]),
        "Upload-Expires": formatdate(
            row["expires"],
            usegmt=True,
        ),
        "Upload-Metadata": ",".join(
            key + " " + base64.b64encode(value.encode()).decode()
            for key, value in row["spec"]
            .get(
                "metadata",
                {},
            )
            .items()
        ),
    }


def checked(
    function,
    *arguments,
):
    try:
        return function(*arguments)
    except UploadError as error:
        raise HTTPException(
            error.status,
            str(error),
            headers=TUS,
        ) from None
    except OSError:
        raise HTTPException(
            503,
            "Upload storage is temporarily unavailable",
            headers=TUS,
        ) from None


@router.options("/files/uploads")
def upload_capabilities(
    _: models.User = Depends(get_current_user),
):
    return Response(
        status_code=204,
        headers={
            **TUS,
            "Tus-Version": "1.0.0",
            "Tus-Extension": "creation,expiration,checksum,termination",
            "Tus-Checksum-Algorithm": "sha1,sha256",
            "Tus-Max-Size": str(
                min(
                    file_custody_manager._max_size,
                    file_custody_manager._max_file_size or file_custody_manager._max_size,
                )
            ),
            "Magnus-Upload-Chunk-Size": str(CHUNK_SIZE),
        },
    )


@router.post(
    "/files/uploads",
    dependencies=[Depends(require_version)],
)
def create_upload(
    request: Request,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(database.get_db),
):
    raw = request.headers.get(
        "upload-metadata",
        "",
    )
    if len(raw) > 12000:
        raise HTTPException(
            400,
            "Upload metadata is too large",
            headers=TUS,
        )
    try:
        length_text = request.headers["upload-length"]
        if not re.fullmatch(
            r"[0-9]{1,18}",
            length_text,
        ):
            raise ValueError()
        length = int(length_text)
        metadata = {}
        for item in raw.split(",") if raw else []:
            key, _, value = item.partition(" ")
            if key in metadata or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}",
                key,
            ):
                raise ValueError()
            metadata[key] = base64.b64decode(
                value,
                validate=True,
            ).decode("utf-8")
        filename = metadata.get(
            "filename",
            "upload",
        )
        if (
            not filename
            or len(filename.encode()) > 255
            or any(char in filename for char in ("/", "\\", "\0"))
            or filename in {".", ".."}
        ):
            raise ValueError()
        checksum = metadata.get("sha256")
        if checksum is not None and not re.fullmatch(
            r"[0-9a-f]{64}",
            checksum,
        ):
            raise ValueError()
        options = UploadOptions.model_validate(
            json.loads(
                metadata.get(
                    "options",
                    "{}",
                )
            )
        )
        if (
            options.expire_minutes is not None
            and not 0 < options.expire_minutes <= file_custody_manager._max_ttl_minutes
        ):
            raise ValueError()
        if options.max_downloads is not None and options.max_downloads < 1:
            raise ValueError()
    except (ValueError, KeyError, UnicodeError, binascii.Error):
        raise HTTPException(
            400,
            "Invalid upload length or metadata",
            headers=TUS,
        ) from None
    key = request.headers.get(
        "idempotency-key",
        f"{int(time.time())}-{uuid.uuid4().hex}",
    )
    if request.headers.get("idempotency-key") and checksum is None:
        raise HTTPException(
            400, "An idempotency key requires content SHA-256 metadata", headers=TUS
        )
    # Replays are checked before Job liveness: a finished Job must not make a
    # previously accepted publication impossible to reconcile after a restart.
    with upload_store.lock:
        with upload_store.index.transaction() as index:
            replay = index.execute(
                "SELECT 1 FROM uploads WHERE owner=? AND creation_key=?",
                (str(user.id), key),
            ).fetchone()
        if replay is None:
            custody_upload_job(
                options.job_id,
                db,
                user,
            )
        row = checked(
            upload_store.create,
            user.id,
            key,
            length,
            {
                "filename": filename,
                "sha256": checksum,
                "options": options.model_dump(),
                "metadata": metadata,
            },
        )
    return Response(
        status_code=201,
        headers={
            **upload_headers(row),
            "Location": f"/api/files/uploads/{row['id']}",
        },
    )


@router.head(
    "/files/uploads/{upload_id}",
    dependencies=[Depends(require_version)],
)
def upload_offset(
    upload_id: str,
    user: models.User = Depends(get_current_user),
):
    row = checked(
        upload_store.status,
        upload_id,
        user.id,
    )
    if row["state"] in {"expired", "cancelled", "failed"}:
        raise HTTPException(
            410,
            "Upload is no longer available",
            headers=TUS,
        )
    return Response(
        status_code=200,
        headers=upload_headers(row),
    )


@router.get(
    "/files/uploads/{upload_id}",
    dependencies=[Depends(require_version)],
)
def upload_status(
    upload_id: str,
    response: Response,
    user: models.User = Depends(get_current_user),
):
    row = checked(
        upload_store.status,
        upload_id,
        user.id,
    )
    response.headers.update(upload_headers(row))
    result = {
        "state": row["state"],
        "offset": row["offset"],
        "length": row["length"],
        "error": row["error"],
    }
    if row["token"] is not None:
        result["file_secret"] = FILE_SECRET_PREFIX + row["token"]
        result["custody_status"] = file_custody_manager.get_file_status(row["token"])
    return result


@router.patch(
    "/files/uploads/{upload_id}",
    dependencies=[Depends(require_version)],
)
async def upload_chunk(
    upload_id: str,
    request: Request,
    user: models.User = Depends(get_current_user),
):
    if request.headers.get("content-type") != "application/offset+octet-stream":
        raise HTTPException(
            415,
            "Expected application/offset+octet-stream",
            headers=TUS,
        )
    try:
        offset_text = request.headers["upload-offset"]
        if not re.fullmatch(
            r"[0-9]{1,18}",
            offset_text,
        ):
            raise ValueError()
        offset = int(offset_text)
        checksum = None
        if header := request.headers.get("upload-checksum"):
            algorithm, encoded = header.split(
                " ",
                1,
            )
            checksum = base64.b64decode(
                encoded,
                validate=True,
            )
            if (
                algorithm not in {"sha1", "sha256"}
                or len(checksum) != {"sha1": 20, "sha256": 32}[algorithm]
            ):
                raise ValueError()
            checksum = (algorithm, checksum)
        if (
            int(
                request.headers.get(
                    "content-length",
                    "0",
                )
            )
            > CHUNK_SIZE
        ):
            raise HTTPException(
                413,
                "Upload chunk exceeds its bound",
                headers=TUS,
            )
    except (KeyError, ValueError, binascii.Error):
        raise HTTPException(
            400,
            "Invalid upload offset or checksum",
            headers=TUS,
        ) from None
    lease = uuid.uuid4().hex
    try:
        row = await _run_blocking_io(
            checked,
            upload_store.claim,
            upload_id,
            user.id,
            offset,
            lease,
        )
        payload = bytearray()
        async with asyncio.timeout(90):
            async for part in request.stream():
                if len(payload) + len(part) > CHUNK_SIZE:
                    raise HTTPException(
                        413,
                        "Upload chunk exceeds its bound",
                        headers=TUS,
                    )
                payload.extend(part)
        offset = await _run_blocking_io(
            checked,
            upload_store.append,
            row,
            payload,
            checksum,
        )
    except TimeoutError:
        raise HTTPException(
            408,
            "Upload chunk receive timeout",
            headers=TUS,
        ) from None
    except ClientDisconnect:
        raise HTTPException(
            400,
            "Upload connection closed",
            headers=TUS,
        ) from None
    finally:
        await _run_blocking_io(
            upload_store.release,
            upload_id,
            lease,
        )
    await _run_blocking_io(
        checked,
        upload_store.status,
        upload_id,
        user.id,
    )
    return Response(
        status_code=204,
        headers={
            **TUS,
            "Upload-Offset": str(offset),
            "Upload-Expires": formatdate(
                row["expires"],
                usegmt=True,
            ),
        },
    )


@router.delete(
    "/files/uploads/{upload_id}",
    dependencies=[Depends(require_version)],
)
def cancel_upload(
    upload_id: str,
    user: models.User = Depends(get_current_user),
):
    checked(
        upload_store.terminate,
        upload_id,
        user.id,
    )
    return Response(
        status_code=204,
        headers=TUS,
    )


async def cleanup_uploads():
    while True:
        await asyncio.sleep(30)
        try:
            await _run_blocking_io(upload_store.cleanup)
        except OSError:
            # Keep reservations if cleanup cannot prove the bytes are gone.
            continue
