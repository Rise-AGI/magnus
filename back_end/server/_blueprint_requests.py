"""Bound Blueprint JSON before FastAPI parses it or holds a worker thread."""

import asyncio
import threading

from starlette.responses import JSONResponse


class BlueprintRequestLimits:
    def __init__(
        self,
        app,
        max_bytes: int,
        max_concurrent: int,
        timeout_seconds: float = 15,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.slots = threading.BoundedSemaphore(
            max_concurrent,
        )

    async def __call__(
        self,
        scope,
        receive,
        send,
    ) -> None:
        path = scope.get(
            "path",
            "",
        )
        if (
            scope["type"] != "http"
            or scope.get(
                "method",
            )
            not in {"POST", "PUT"}
            or not (
                path == "/api/blueprints"
                or path.startswith(
                    "/api/blueprints/",
                )
            )
        ):
            await self.app(
                scope,
                receive,
                send,
            )
            return
        headers = dict(
            scope.get(
                "headers",
                [],
            ),
        )
        try:
            declared = (
                int(
                    headers[b"content-length"],
                )
                if b"content-length" in headers
                else None
            )
            if declared is not None and declared < 0:
                raise ValueError
        except ValueError:
            await JSONResponse(
                {"detail": "Invalid Content-Length"},
                status_code=400,
            )(
                scope,
                receive,
                send,
            )
            return
        if declared is not None and declared > self.max_bytes:
            await JSONResponse(
                {"detail": "Blueprint request is too large"},
                status_code=413,
            )(
                scope,
                receive,
                send,
            )
            return
        if not self.slots.acquire(
            blocking=False,
        ):
            await JSONResponse(
                {"detail": "Blueprint requests are busy; retry later"},
                status_code=503,
            )(
                scope,
                receive,
                send,
            )
            return
        try:
            body = bytearray()
            try:
                async with asyncio.timeout(
                    self.timeout_seconds,
                ):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get(
                            "body",
                            b"",
                        )
                        if (
                            len(
                                body,
                            )
                            + len(
                                chunk,
                            )
                            > self.max_bytes
                        ):
                            await JSONResponse(
                                {"detail": "Blueprint request is too large"},
                                status_code=413,
                            )(
                                scope,
                                receive,
                                send,
                            )
                            return
                        body.extend(
                            chunk,
                        )
                        if not message.get(
                            "more_body",
                            False,
                        ):
                            break
            except TimeoutError:
                await JSONResponse(
                    {"detail": "Blueprint request body timed out"},
                    status_code=408,
                )(
                    scope,
                    receive,
                    send,
                )
                return
            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {
                    "type": "http.request",
                    "body": bytes(
                        body,
                    ),
                    "more_body": False,
                }

            await self.app(
                scope,
                buffered_receive,
                send,
            )
        finally:
            self.slots.release()
