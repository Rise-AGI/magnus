"""tus 1.0 client for custody; a creation key survives uncertain HTTP replies."""

import base64
import hashlib
import json
import time
from typing import Any, Callable, Optional
import uuid

import httpx

from .exceptions import MagnusError, UploadOutcomeUnknown

TUS = {"Tus-Resumable": "1.0.0"}
THRESHOLD = 8 * 1024 * 1024


def resumable_upload(
    http: httpx.Client,
    filename: str,
    stream: Any,
    options: dict,
    timeout: float,
    handle_error: Callable,
) -> Optional[httpx.Response]:
    stream.seek(
        0,
        2,
    )
    length = stream.tell()
    stream.seek(0)
    if length < THRESHOLD:
        return None
    capabilities = http.options(
        "/files/uploads",
        timeout=min(
            timeout,
            30,
        ),
    )
    if capabilities.status_code in (404, 405, 501):
        return None
    handle_error(capabilities)
    if "1.0.0" not in capabilities.headers.get(
        "tus-version",
        "",
    ).split(","):
        raise MagnusError("Server did not confirm a supported upload protocol")
    try:
        chunk_size = int(capabilities.headers["magnus-upload-chunk-size"])
        if not 0 < chunk_size <= 8 * 1024 * 1024:
            raise ValueError()
    except (KeyError, ValueError):
        raise MagnusError("Server returned an invalid upload chunk size") from None
    digest = hashlib.sha256()
    while data := stream.read(1024 * 1024):
        digest.update(data)
    stream.seek(0)
    metadata = {
        "filename": filename,
        "sha256": digest.hexdigest(),
        "options": json.dumps(
            options,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    encoded = ",".join(
        key + " " + base64.b64encode(value.encode()).decode() for key, value in metadata.items()
    )
    creation_key = f"{int(time.time())}-{uuid.uuid4().hex}"
    path = "/files/uploads"

    def request(
        method: str,
        endpoint: str,
        **kwargs,
    ) -> httpx.Response:
        for attempt in range(3):
            try:
                response = http.request(
                    method,
                    endpoint,
                    timeout=timeout,
                    **kwargs,
                )
                if response.status_code < 500 and response.status_code not in (408, 429):
                    return response
            except httpx.TransportError:
                pass
            if method == "PATCH":
                return httpx.Response(
                    409,
                    headers={"Magnus-Upload-Reconcile": "1"},
                )
            if attempt < 2:
                time.sleep(2**attempt)
        raise UploadOutcomeUnknown(
            "Resumable upload is unresolved; no replacement upload was created. "
            "The accepted bytes remain until the upload expires."
        )

    created = request(
        "POST",
        path,
        headers={
            **TUS,
            "Upload-Length": str(length),
            "Upload-Metadata": encoded,
            "Idempotency-Key": creation_key,
        },
    )
    handle_error(created)
    if created.status_code != 201:
        raise MagnusError("Server returned an invalid upload creation response")
    location = created.headers.get(
        "location",
        "",
    )
    # Never forward authentication to an origin or arbitrary URL supplied in a
    # response. Magnus creation returns this same-origin API-relative resource.
    prefix = "/api/files/uploads/"
    upload_id = location.removeprefix(prefix)
    if (
        not location.startswith(prefix)
        or len(upload_id) != 32
        or any(char not in "0123456789abcdef" for char in upload_id)
    ):
        raise MagnusError("Server returned an invalid upload location")
    path += "/" + upload_id
    offset = 0
    stalls = 0
    needs_head = True
    while offset < length:
        if needs_head:
            head = request(
                "HEAD",
                path,
                headers=TUS,
            )
            handle_error(head)
            try:
                actual = int(head.headers["upload-offset"])
                if not offset <= actual <= length:
                    raise ValueError()
            except (KeyError, ValueError):
                raise MagnusError("Server returned an invalid upload offset") from None
            offset = actual
        if offset == length:
            break
        stream.seek(offset)
        chunk = stream.read(
            min(
                chunk_size,
                length - offset,
            )
        )
        if not chunk:
            raise MagnusError("Upload source changed during transfer")
        response = request(
            "PATCH",
            path,
            headers={
                **TUS,
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": str(offset),
                "Upload-Checksum": "sha256 "
                + base64.b64encode(hashlib.sha256(chunk).digest()).decode(),
            },
            content=chunk,
        )
        if response.status_code == 409:
            stalls += 1
            needs_head = True
            if response.headers.get("magnus-upload-reconcile") == "1":
                chunk_size = max(
                    64 * 1024,
                    chunk_size // 2,
                )
            if stalls > 6:
                raise UploadOutcomeUnknown(
                    "Upload writer is still busy; no replacement upload was created"
                )
            time.sleep(1)
            continue
        handle_error(response)
        if response.status_code != 204 or response.headers.get("upload-offset") != str(
            offset + len(chunk)
        ):
            raise UploadOutcomeUnknown(
                "Upload acknowledgement is invalid; no replacement upload was created"
            )
        offset += len(chunk)
        needs_head = False
        stalls = 0
    deadline = time.monotonic() + max(
        timeout,
        300,
    )
    while time.monotonic() < deadline:
        response = request(
            "GET",
            path,
            headers=TUS,
        )
        handle_error(response)
        status = response.json()
        if status.get("state") == "complete":
            if status.get("custody_status") != "ready":
                raise MagnusError("The uploaded custody file has expired or was already consumed")
            if not isinstance(
                status.get("file_secret"),
                str,
            ) or not status[
                "file_secret"
            ].startswith("magnus-secret:"):
                raise MagnusError("Server returned an invalid custody receipt")
            return httpx.Response(
                200,
                json={"file_secret": status["file_secret"]},
                request=response.request,
            )
        if status.get("state") not in ("open", "finalizing"):
            raise MagnusError(
                "Upload did not publish: " + str(status.get("error") or status.get("state"))
            )
        time.sleep(1)
    raise UploadOutcomeUnknown("Upload is awaiting publication; no replacement upload was created")
