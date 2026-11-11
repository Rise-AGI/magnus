"""Repository transport policy; credentials remain owned by Git configuration."""

from urllib.parse import urlsplit


def validate_github_https_url(
    value: object,
) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError("execution.github_https_url must be a string or null")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or any(character.isspace() for character in value)
    ):
        raise ValueError(
            "execution.github_https_url must be a credential-free HTTPS origin"
        )
    # Validate malformed ports before any preparation starts.
    _ = parsed.port


def github_repository_url(
    namespace: str,
    repository: str,
    *,
    local: bool,
    https_url: str | None = None,
) -> str:
    if https_url is not None:
        return f"{https_url.rstrip('/')}/{namespace}/{repository}.git"
    if local:
        return f"https://github.com/{namespace}/{repository}.git"
    return f"git@github.com:{namespace}/{repository}.git"
