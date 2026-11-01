"""Canonical schema identity shared by form and preference consumers."""

import hashlib
import json
from typing import Any


def code_revision(
    code: str,
) -> str:
    return hashlib.sha256(
        code.encode(
            "utf-8",
        ),
    ).hexdigest()


def _normalize(
    value: Any,
) -> Any:
    if isinstance(
        value,
        dict,
    ):
        return {
            key: _normalize(
                item,
            )
            for key, item in value.items()
        }
    if isinstance(
        value,
        (list, tuple),
    ):
        return [
            _normalize(
                item,
            )
            for item in value
        ]
    if (
        isinstance(
            value,
            float,
        )
        and value.is_integer()
    ):
        return int(
            value,
        )
    return value


def signature_hash(
    schema: list[dict[str, Any]],
) -> str:
    data = json.dumps(
        _normalize(
            schema,
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(
        data.encode(
            "utf-8",
        ),
    ).hexdigest()
