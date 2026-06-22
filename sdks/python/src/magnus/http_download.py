# sdks/python/src/magnus/http_download.py
import os
import json
import time
import shutil
import logging
import httpx
import tempfile
from pathlib import Path
from typing import Optional

from .file_transfer import normalize_secret, get_tmp_base, ENV_CUSTODY_DROPIN_DIR
from .exceptions import _ServerError

logger = logging.getLogger("magnus")

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_MAX_BACKOFF = 30.0
_TRANSIENT_ERRORS = (httpx.TransportError,)
_SMALL_FILE_THRESHOLD = 5 * 1024 * 1024  # 5 MB


def _magnus_error(msg: str) -> Exception:
    from .exceptions import MagnusError
    return MagnusError(msg)


def _get_download_url(token: str) -> str:
    from . import default_client
    return f"{default_client.api_base}/files/download/{token}"


def _prepare_target(target: Path, overwrite: bool) -> None:
    """Clear an existing target (honoring overwrite) and ensure its parent exists."""
    if target.exists():
        if not overwrite:
            raise FileExistsError(f"Target path already exists: {target}")
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)


def _read_staged_dropin(
    token: str,
    target_path: Optional[str],
    overwrite: bool,
) -> Optional[Path]:
    """Resolve a custody token from the host-staged drop-in dir instead of HTTP.

    On no-network remote-execution sites the magnus host stages the files a job's
    entry_command references into ENV_CUSTODY_DROPIN_DIR before the job runs (see
    _StagingMixin._stage_in_custody), mirroring the upload drop dir. Per entry:

        <dropin>/<token>/meta.json   {token, filename, is_directory}
        <dropin>/<token>/<filename>  the file (a .tar.gz for directories)

    Returns the materialized target Path when the token is staged here, or None to
    fall through to the HTTP path (env unset on local/owned sites, or token not staged).
    """
    dropin = os.environ.get(ENV_CUSTODY_DROPIN_DIR)
    if not dropin:
        return None
    entry_dir = Path(dropin) / token
    meta_path = entry_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    # meta.filename was sanitized host-side at upload; basename again defensively
    # before joining, since this materializes a file on the caller's disk.
    filename = os.path.basename(str(meta.get("filename", "")))
    if not filename or filename in (".", ".."):
        return None
    is_directory = bool(meta.get("is_directory", False))
    src = entry_dir / filename
    if not src.exists():
        return None

    if is_directory:
        import tarfile
        with tempfile.TemporaryDirectory(dir=get_tmp_base()) as tmp:
            tmp_dir = Path(tmp)
            with tarfile.open(src) as tar:
                tar.extractall(tmp_dir, filter="data")
            extracted = list(tmp_dir.iterdir())
            if len(extracted) != 1:
                raise _magnus_error(
                    f"Expected 1 item from staged archive, got {len(extracted)}: {extracted}"
                )
            source = extracted[0]
            target = Path(target_path).resolve() if target_path else Path.cwd() / source.name
            _prepare_target(target, overwrite)
            shutil.move(str(source), str(target))
            return target

    target = Path(target_path).resolve() if target_path else Path.cwd() / filename
    _prepare_target(target, overwrite)
    shutil.copy2(str(src), str(target))
    return target


def _download_once(
    url: str,
    target_path: Optional[str],
    timeout: Optional[float],
    overwrite: bool,
) -> Path:
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        if resp.status_code == 404:
            raise _magnus_error("File not found or expired")
        if resp.status_code >= 500:
            raise _ServerError(f"Server error {resp.status_code}")
        if not resp.is_success:
            raise _magnus_error(f"Download failed (HTTP {resp.status_code})")

        filename = _parse_filename(resp.headers) or "download"
        is_directory = resp.headers.get("x-magnus-directory", "").lower() == "true"
        content_length_str = resp.headers.get("content-length")

        # Fast path: small non-directory files read into memory, skip temp dir
        if (not is_directory
            and content_length_str is not None
            and int(content_length_str) < _SMALL_FILE_THRESHOLD):
            target = Path(target_path).resolve() if target_path else Path.cwd() / filename
            _prepare_target(target, overwrite)
            data = resp.read()
            target.write_bytes(data)
            return target

        # Standard path: stream to temp dir, then move
        with tempfile.TemporaryDirectory(dir=get_tmp_base()) as tmp:
            tmp_dir = Path(tmp)
            tmp_file = tmp_dir / filename
            with open(tmp_file, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

            if is_directory:
                import tarfile
                with tarfile.open(tmp_file) as tar:
                    tar.extractall(tmp_dir, filter="data")
                tmp_file.unlink()
                extracted = list(tmp_dir.iterdir())
                if len(extracted) != 1:
                    raise _magnus_error(
                        f"Expected 1 item from archive, got {len(extracted)}: {extracted}"
                    )
                source = extracted[0]
            else:
                source = tmp_file

            target = Path(target_path).resolve() if target_path else Path.cwd() / source.name
            _prepare_target(target, overwrite)
            shutil.move(str(source), str(target))
            return target


def download_file(
    file_secret: str,
    target_path: Optional[str] = None,
    timeout: Optional[float] = None,
    overwrite: bool = True,
) -> Path:
    token = normalize_secret(file_secret)

    # No-network remote-execution sites: the host has pre-staged the file into the
    # drop-in dir (see _StagingMixin._stage_in_custody). Resolve it from the filesystem
    # instead of hitting the backend, which compute nodes there cannot reach. Falls
    # through to HTTP when the dir is unset (local / owned sites) or the token isn't staged.
    staged = _read_staged_dropin(token, target_path, overwrite)
    if staged is not None:
        return staged

    url = _get_download_url(token)

    for attempt in range(_MAX_RETRIES):
        try:
            return _download_once(url, target_path, timeout, overwrite)
        except (*_TRANSIENT_ERRORS, _ServerError) as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            backoff = min(_BACKOFF_BASE * (2 ** attempt), _MAX_BACKOFF)
            logger.warning(f"Download attempt {attempt + 1} failed: {e}. Retrying in {backoff:.0f}s...")
            time.sleep(backoff)
    assert False, "unreachable"


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
