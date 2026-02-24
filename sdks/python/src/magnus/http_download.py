# sdks/python/src/magnus/http_download.py
import time
import shutil
import logging
import httpx
import tempfile
from pathlib import Path
from typing import Optional

from .file_transfer import normalize_secret, get_tmp_base

logger = logging.getLogger("magnus")

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_MAX_BACKOFF = 30.0
_TRANSIENT_ERRORS = (httpx.TransportError,)


class _ServerError(Exception):
    """5xx response — transient, worth retrying."""
    pass


def _magnus_error(msg: str) -> Exception:
    from .exceptions import MagnusError
    return MagnusError(msg)


def _get_download_url(token: str) -> str:
    from . import default_client
    return f"{default_client.api_base}/files/download/{token}"


def _stream_to_file(
    url: str,
    tmp_dir: Path,
    timeout: Optional[float],
) -> tuple[Path, bool]:
    """Stream GET response to a temp file. Returns (tmp_file_path, is_directory)."""
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        if resp.status_code == 404:
            raise _magnus_error("File not found or expired")
        if resp.status_code >= 500:
            raise _ServerError(f"Server error {resp.status_code}")
        if not resp.is_success:
            raise _magnus_error(f"Download failed (HTTP {resp.status_code})")

        filename = _parse_filename(resp.headers) or "download"
        is_directory = resp.headers.get("x-magnus-directory", "").lower() == "true"

        tmp_file = tmp_dir / filename
        with open(tmp_file, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)

    return tmp_file, is_directory


def download_file(
    file_secret: str,
    target_path: Optional[str] = None,
    timeout: Optional[float] = None,
    overwrite: bool = True,
) -> Path:
    token = normalize_secret(file_secret)
    url = _get_download_url(token)

    with tempfile.TemporaryDirectory(dir=get_tmp_base()) as tmp:
        tmp_dir = Path(tmp)

        for attempt in range(_MAX_RETRIES):
            try:
                # Clean up any partial file from previous attempt
                for leftover in tmp_dir.iterdir():
                    leftover.unlink() if leftover.is_file() else shutil.rmtree(leftover)
                tmp_file, is_directory = _stream_to_file(url, tmp_dir, timeout)
                break
            except (*_TRANSIENT_ERRORS, _ServerError) as e:
                if attempt == _MAX_RETRIES - 1:
                    raise
                backoff = min(_BACKOFF_BASE * (2 ** attempt), _MAX_BACKOFF)
                logger.warning(f"Download attempt {attempt + 1} failed: {e}. Retrying in {backoff:.0f}s...")
                time.sleep(backoff)

        if is_directory:
            import tarfile
            with tarfile.open(tmp_file) as tar:
                tar.extractall(tmp_dir, filter="data")
            tmp_file.unlink()
            extracted = [p for p in tmp_dir.iterdir()]
            if len(extracted) != 1:
                raise _magnus_error(
                    f"Expected 1 item from archive, got {len(extracted)}: {extracted}"
                )
            source = extracted[0]
        else:
            source = tmp_file

        target = Path(target_path).resolve() if target_path else Path.cwd() / source.name
        if overwrite and target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))

    return target


async def download_file_async(
    file_secret: str,
    target_path: Optional[str] = None,
    timeout: Optional[float] = None,
    overwrite: bool = True,
) -> Path:
    import asyncio
    return await asyncio.to_thread(
        download_file, file_secret, target_path, timeout, overwrite,
    )


def _parse_filename(headers: httpx.Headers) -> Optional[str]:
    cd = headers.get("content-disposition", "")
    if "filename=" not in cd:
        return None
    for part in cd.split(";"):
        part = part.strip()
        if part.startswith("filename="):
            name = part[len("filename="):]
            return name.strip('"').strip("'")
    return None
