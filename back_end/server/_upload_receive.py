"""Bounded HTTP reception, before multipart parsing or custody publication."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import re
import shutil
import tempfile
import threading
import time
import uuid

from fastapi import HTTPException, Request
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse


logger = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"[A-Za-z0-9-]{8,80}\Z")


def upload_event(
    request: Request,
    phase: str,
    **values,
) -> None:
    trace = request.scope.get("magnus.upload_trace")
    if trace is not None:
        trace.event(phase, **values)


class UploadTrace:
    def __init__(
        self,
        headers,
    ):
        self.started = time.monotonic()
        self.last_report = self.started
        self.received = 0
        proposed = headers.get("x-request-id", "")
        self.request_id = proposed if _IDENTIFIER.fullmatch(proposed) else str(uuid.uuid4())
        ray = headers.get("cf-ray", "")
        self.ray = ray if _IDENTIFIER.fullmatch(ray) else None

    def event(
        self,
        phase: str,
        **values,
    ) -> None:
        logger.info("Upload %s", json.dumps({
            "utc": datetime.now(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "cf_ray": self.ray,
            "phase": phase,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "body_bytes": self.received,
            **values,
        }))


class UploadTelemetryMiddleware:
    def __init__(
        self,
        app,
    ):
        self.app = app

    async def __call__(
        self,
        scope,
        receive,
        send,
    ):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == "/api/files/upload" or path.startswith("/api/files/uploads")
        ):
            await self.app(scope, receive, send)
            return
        is_tus = path == "/api/files/uploads" or path.startswith("/api/files/uploads/")
        if is_tus:
            override = Request(scope).headers.get("x-http-method-override")
            if override is not None:
                method = override.upper()
                if method not in {"GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS"}:
                    await JSONResponse(
                        {"detail": "Unsupported upload method override"},
                        status_code=400,
                        headers={"Tus-Resumable": "1.0.0", "Cache-Control": "no-store"},
                    )(scope, receive, send)
                    return
                scope = {**scope, "method": method}
        trace = UploadTrace(Request(scope).headers)
        scope["magnus.upload_trace"] = trace
        trace.event("headers", method=scope["method"])

        async def observed_receive():
            message = await receive()
            if message["type"] == "http.request":
                trace.received += len(message.get("body", b""))
                now = time.monotonic()
                if not message.get("more_body", False) or now - trace.last_report >= 10:
                    trace.event("body_complete" if not message.get("more_body", False) else "receiving")
                    trace.last_report = now
            elif message["type"] == "http.disconnect":
                trace.event("disconnected")
            return message

        async def observed_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                headers = [(name, value) for name, value in message.get("headers", []) if name.lower() != b"x-request-id"]
                if is_tus:
                    headers = [(name, value) for name, value in headers if name.lower() not in {b"tus-resumable", b"cache-control", b"access-control-expose-headers"}]
                    headers.extend([
                        (b"tus-resumable", b"1.0.0"),
                        (b"cache-control", b"no-store"),
                        (b"access-control-expose-headers", b"Tus-Resumable,Tus-Version,Tus-Extension,Tus-Max-Size,Tus-Checksum-Algorithm,Upload-Offset,Upload-Length,Upload-Metadata,Upload-Expires,Location,Magnus-Upload-Chunk-Size,X-Request-ID"),
                    ])
                message["headers"] = [*headers, (b"x-request-id", trace.request_id.encode("ascii"))]
                trace.event("response", status=message["status"])
            await send(message)

        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException as error:
            # Exception text and URLs may contain capability tokens.
            trace.event("interrupted", error_type=type(error).__name__)
            raise


class UploadAdmission:
    def __init__(
        self,
        *,
        max_concurrent: int = 0,
        max_body_bytes: int = 0,
        max_spool_bytes: int = 0,
        min_free_bytes: int = 0,
    ):
        self.max_concurrent = max_concurrent
        self.max_body_bytes = max_body_bytes
        self.max_spool_bytes = max_spool_bytes
        self.min_free_bytes = min_free_bytes
        self._lock = threading.Lock()
        self._active = 0
        self._reserved = 0

    @asynccontextmanager
    async def receive(
        self,
        request: Request,
    ):
        declared = request.headers.get("content-length")
        try:
            length = int(declared) if declared is not None else 0
            if length < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length") from None
        if self.max_body_bytes and length > self.max_body_bytes:
            raise HTTPException(413, "Upload request exceeds the receive limit")
        with self._lock:
            if self.max_concurrent and self._active >= self.max_concurrent:
                raise HTTPException(429, "Upload receivers are busy", headers={"Retry-After": "2"})
            if self.max_spool_bytes and self._reserved + length > self.max_spool_bytes:
                raise HTTPException(503, "Upload temporary storage is busy", headers={"Retry-After": "2"})
            self._active += 1
            self._reserved += length
        reserved = length
        received = 0
        last_disk_check = 0.0
        original_receive = request._receive

        async def checked_receive():
            nonlocal reserved, received, last_disk_check
            message = await original_receive()
            if message["type"] != "http.request":
                return message
            size = len(message.get("body", b""))
            received += size
            if self.max_body_bytes and received > self.max_body_bytes:
                raise HTTPException(413, "Upload request exceeds the receive limit")
            if declared is not None and received > length:
                raise HTTPException(400, "Upload body exceeds Content-Length")
            extra = max(0, received - reserved)
            with self._lock:
                if self.max_spool_bytes and self._reserved + extra > self.max_spool_bytes:
                    raise HTTPException(503, "Upload temporary storage is busy")
                self._reserved += extra
                reserved += extra
            now = time.monotonic()
            if self.min_free_bytes and now - last_disk_check >= 1:
                free = await asyncio.to_thread(lambda: shutil.disk_usage(tempfile.gettempdir()).free)
                if free < self.min_free_bytes + size:
                    raise HTTPException(503, "Upload temporary storage is full")
                last_disk_check = now
            if not message.get("more_body", False) and declared is not None and received != length:
                raise HTTPException(400, "Incomplete upload body")
            return message

        request._receive = checked_receive
        try:
            upload_event(request, "admitted", declared_body_bytes=length if declared is not None else None)
            yield
        except ClientDisconnect:
            raise HTTPException(400, "Upload body disconnected") from None
        finally:
            request._receive = original_receive
            with self._lock:
                self._active -= 1
                self._reserved -= reserved
