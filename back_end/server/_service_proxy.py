"""Keep Magnus authentication at the proxy boundary."""
from starlette.requests import Request


def service_request_headers(
    request: Request,
) -> list[tuple[bytes, bytes]]:
    # A service is user code, including when it belongs to a different user.
    # These credentials authenticate the proxy and must never reach that code.
    excluded = {b"host", b"proxy-authorization", b"referer"}
    headers = []
    for name, value in request.headers.raw:
        if name.lower() in excluded:
            continue
        if name.lower() == b"authorization" and value.partition(b" ")[0].lower() == b"bearer":
            continue
        if name.lower() == b"cookie":
            value = b"; ".join(
                part.strip()
                for part in value.split(b";")
                if part.strip().partition(b"=")[0].strip() not in (b"token", b"access_token")
            )
            if not value:
                continue
        headers.append((name, value))
    return headers


def service_request_params(
    request: Request,
) -> list[tuple[str, str]]:
    return [
        (name, value)
        for name, value in request.query_params.multi_items()
        if name != "token"
    ]
