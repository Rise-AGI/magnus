"""Bounded HTTP diagnostics shared by API and file-transfer clients."""
import json
import re
from typing import Any

import httpx


_BODY_LIMIT = 8192
_MESSAGE_LIMIT = 1024


def _read_error_prefix(
    response: httpx.Response,
) -> bytes:
    try:
        return response.content[:_BODY_LIMIT]
    except httpx.ResponseNotRead:
        # Do not decompress an untrusted error stream: its decoded size can be
        # arbitrarily larger than the bounded wire prefix.
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            return b""
        prefix = bytearray()
        try:
            for chunk in response.iter_raw(chunk_size=1024):
                prefix.extend(chunk[:_BODY_LIMIT - len(prefix)])
                if len(prefix) >= _BODY_LIMIT:
                    break
        except httpx.TransportError:
            pass
        return bytes(prefix)


def _message_values(
    value: Any,
    depth: int = 0,
) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        return [value[:_MESSAGE_LIMIT]]
    if isinstance(value, list):
        return [message for item in value[:8] for message in _message_values(item, depth + 1)]
    if isinstance(value, dict):
        # FastAPI validation errors can echo arbitrary request data in input
        # and ctx. Only diagnostic fields belong in an exception/log message.
        for key in ("detail", "message", "msg", "error"):
            if key in value:
                return _message_values(value[key], depth + 1)
    return []


def _safe_message(
    message: str,
    response: httpx.Response,
) -> str:
    if re.search(r"<\s*(?:!doctype|html|head|body|script)\b", message, re.IGNORECASE):
        return ""
    try:
        headers = response.request.headers
    except RuntimeError:
        headers = httpx.Headers()
    for name in ("authorization", "cookie", "proxy-authorization"):
        secret = headers.get(name)
        if secret:
            message = message.replace(secret, "[redacted]")
            if name.endswith("authorization") and " " in secret:
                message = message.replace(secret.split(" ", 1)[1], "[redacted]")
    message = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [redacted]", message)
    message = re.sub(r"(?i)magnus-secret:[a-z0-9-]+", "[redacted file secret]", message)
    message = re.sub(
        r"(?i)\b(?:authorization|cookie|password|(?:access[_-]?|refresh[_-]?)?token|api[_-]?key|secret)\s*[:=]\s*[^\r\n]+",
        "[redacted credentials]",
        message,
    )
    message = "".join(char for char in message if char.isprintable() or char.isspace())
    return " ".join(message.split())[:_MESSAGE_LIMIT]


def http_error_detail(
    response: httpx.Response,
) -> str:
    """Keep status/request identity without echoing request bodies or HTML pages."""
    text = _read_error_prefix(response).decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        # A truncated JSON body may include request inputs outside detail.
        value = "" if text.lstrip().startswith(("{", "[")) else text
    message = _safe_message("; ".join(_message_values(value)), response)
    detail = f"HTTP {response.status_code}"
    if message:
        detail += f": {message}"
    for header in ("x-request-id", "cf-ray"):
        request_id = response.headers.get(header, "")
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
            detail += f" [{header}: {request_id}]"
    return detail
