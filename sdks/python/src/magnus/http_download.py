# sdks/python/src/magnus/http_download.py
import os
import errno
import json
import time
import shutil
import logging
import math
import httpx
import tempfile
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from typing import Optional

from .file_transfer import normalize_secret, ENV_CUSTODY_DROPIN_DIR
from .exceptions import _ServerError
from ._http_errors import http_error_detail
from ._archive import extract_custody_archive

logger = logging.getLogger("magnus")

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_MAX_BACKOFF = 30.0
_TRANSIENT_ERRORS = (httpx.TransportError,)
_WINDOWS = os.name == "nt"


def _magnus_error(msg: str) -> Exception:
    from .exceptions import MagnusError
    return MagnusError(msg)


def _get_download_url(token: str) -> str:
    from . import default_client
    return f"{default_client.api_base}/files/download/{token}"


def _target_path(
    target_path: Optional[str],
    filename: str,
) -> Path:
    # Do not follow a pre-existing destination symlink when replacing a file.
    return Path(os.path.abspath(target_path or filename))


@contextmanager
def _temporary_download(
    parent: Path,
):
    temporary = Path(tempfile.mkdtemp(prefix=".magnus-download-", dir=parent))
    try:
        yield temporary
    finally:
        previous = temporary / "previous"
        if previous.exists() or previous.is_symlink():
            # If rollback itself failed (e.g. a filesystem permission change),
            # automatic temporary cleanup must not delete the user's old data.
            logger.warning("Previous download retained at %s", previous)
        else:
            shutil.rmtree(temporary)


def _publish_download(
    source: Path,
    target: Path,
    overwrite: bool,
    temporary: Path,
) -> Path:
    """Publish complete data on the destination filesystem, retaining the old copy
    until transfer/extraction/copying has succeeded. Directory replacement uses
    a rollback name because portable rename cannot replace a nonempty directory.
    """
    exists = target.exists() or target.is_symlink()
    if exists and not overwrite:
        raise FileExistsError(f"Target path already exists: {target}")
    if not overwrite:
        _publish_without_overwrite(source, target)
    elif exists and (source.is_dir() or target.is_dir()) and not target.is_symlink():
        if target.resolve() in (Path.cwd().resolve(), *Path.cwd().resolve().parents):
            raise _magnus_error("Cannot replace the working directory or its ancestors")
        previous = temporary / "previous"
        target.replace(previous)
        try:
            source.replace(target)
        except BaseException:
            previous.replace(target)
            raise
        try:
            shutil.rmtree(previous) if previous.is_dir() else previous.unlink()
        except OSError:
            # The new output is already published. Keep the old copy for manual
            # cleanup instead of reporting a failed download and retrying it.
            logger.warning("Could not remove previous download at %s", previous)
    else:
        source.replace(target)
    return target


def _remove_created_target(
    target: Path,
    created: os.stat_result,
) -> None:
    try:
        current = target.lstat()
        if not created.st_ino or (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino):
            return
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Incomplete download retained at %s", target)


def _copy_file_exclusively(
    source: Path,
    target: Path,
) -> None:
    # Filesystems such as FAT/FUSE may not support hard links. Exclusive open
    # preserves no-overwrite semantics, but the new file is visible during copy.
    created = None
    try:
        with open(target, "xb") as output:
            created = os.fstat(output.fileno())
            with open(source, "rb") as incoming:
                shutil.copyfileobj(incoming, output, length=65536)
            output.flush()
    except BaseException:
        if created is not None:
            _remove_created_target(target, created)
        raise


def _publish_without_overwrite(
    source: Path,
    target: Path,
) -> None:
    if _WINDOWS:
        # Windows rename rejects an existing destination for files/directories.
        os.rename(source, target)
    elif source.is_dir():
        target.mkdir()
        created = target.lstat()
        try:
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
        except BaseException:
            _remove_created_target(target, created)
            raise
    else:
        try:
            os.link(source, target)
        except OSError as error:
            if error.errno not in {errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS, errno.EPERM, errno.EXDEV}:
                raise
            _copy_file_exclusively(source, target)


def _extract_directory(
    archive: Path,
    temporary: Path,
) -> Path:
    extracted_dir = temporary / "extracted"
    extracted_dir.mkdir()
    extract_custody_archive(
        archive,
        extracted_dir,
    )
    extracted = list(extracted_dir.iterdir())
    if len(extracted) != 1 or not extracted[0].is_dir() or extracted[0].is_symlink():
        raise _magnus_error("Expected one directory in the custody archive")
    source = extracted[0]
    if _safe_filename(source.name) != source.name:
        raise _magnus_error("Invalid directory name in custody archive")
    # data-filter confinement is relative to extracted_dir. Publication moves
    # only its single child, so links must stay inside that child as well.
    for entry in source.rglob("*"):
        if entry.is_symlink() and not entry.resolve().is_relative_to(source.resolve()):
            raise _magnus_error("Custody archive link escapes its directory")
    return source


def _safe_filename(
    filename: str,
) -> Optional[str]:
    # Treat both POSIX and Windows separators as paths on every client OS.
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip().rstrip(".")
    if (not name or name in (".", "..") or any(ord(char) < 32 for char in name)
            or any(char in name for char in ':<>"|?*')):
        return None
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        return None
    return name


def _read_staged_dropin(
    token: str,
    target_path: Optional[str],
    overwrite: bool,
) -> Optional[Path]:
    """Resolve a custody token from the host-staged drop-in dir instead of HTTP.

    On no-network remote-execution sites the magnus host stages the files a job's
    entry_command references into ENV_CUSTODY_DROPIN_DIR before the job runs (see
    _StagingMixin._stage_in_custody), mirroring the upload drop dir. Per entry:

        <dropin>/<token>/meta.json   {token, filename, storage_filename, is_directory}
        <dropin>/<token>/payload     the file (a .tar.gz for directories)

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
    if not isinstance(meta, dict):
        return None
    filename = _safe_filename(str(meta.get("filename", "")))
    if not filename:
        return None
    stored_name = meta.get("storage_filename", filename)
    if (
        not isinstance(stored_name, str)
        or _safe_filename(stored_name) != stored_name
        or stored_name == "meta.json"
    ):
        return None
    is_directory = bool(meta.get("is_directory", False))
    src = entry_dir / stored_name
    if not src.exists():
        return None

    parent = _target_path(target_path, filename).parent
    parent.mkdir(parents=True, exist_ok=True)
    with _temporary_download(parent) as temporary:
        if is_directory:
            source = _extract_directory(src, temporary)
        else:
            source = temporary / "payload"
            shutil.copy2(src, source)
        target = _target_path(target_path, source.name if is_directory else filename)
        return _publish_download(source, target, overwrite, temporary)


def _download_once(
    url: str,
    target_path: Optional[str],
    timeout: Optional[float],
    overwrite: bool,
) -> Path:
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        if resp.status_code >= 500:
            raise _ServerError(http_error_detail(resp))
        if not resp.is_success:
            detail = http_error_detail(resp)
            if resp.status_code == 404:
                detail = f"File not found or expired ({detail})"
            raise _magnus_error(detail)

        filename = _parse_filename(resp.headers) or "download"
        is_directory = resp.headers.get("x-magnus-directory", "").lower() == "true"
        content_length_str = resp.headers.get("content-length")

        expected_length = None
        if content_length_str is not None:
            try:
                expected_length = int(content_length_str)
            except ValueError:
                raise _magnus_error("Invalid download Content-Length") from None
            if expected_length < 0:
                raise _magnus_error("Invalid download Content-Length")

        parent = _target_path(target_path, filename).parent
        parent.mkdir(parents=True, exist_ok=True)
        # Same-filesystem staging makes publication a rename instead of an
        # unsafe delete followed by a possibly failing cross-device copy.
        with _temporary_download(parent) as tmp_dir:
            tmp_file = tmp_dir / "payload"
            with open(tmp_file, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

            # HTTP Content-Length counts encoded wire bytes, not decoded file
            # size (e.g. gzip). HTTPX tracks this before content decoding.
            if expected_length is not None and resp.num_bytes_downloaded != expected_length:
                raise httpx.RemoteProtocolError("Incomplete download response")

            if is_directory:
                source = _extract_directory(tmp_file, tmp_dir)
            else:
                source = tmp_file

            target = _target_path(target_path, source.name if is_directory else filename)
            return _publish_download(source, target, overwrite, tmp_dir)


def _download_http(
    token: str,
    target_path: Optional[str],
    timeout: Optional[float],
    overwrite: bool,
) -> Path:
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


def _validate_wait_options(
    wait_timeout: float,
    poll_interval: float,
) -> None:
    if not math.isfinite(wait_timeout) or wait_timeout < 0:
        raise ValueError("wait_timeout must be finite and nonnegative")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError("poll_interval must be finite and positive")


def _ready_or_raise(
    status: dict,
) -> bool:
    if status["status"] == "ready":
        return True
    if status["status"] == "unavailable":
        raise _magnus_error("File is unavailable: missing, expired, consumed, or not retained after restart")
    if status["status"] == "unknown" and status.get("job_transfers_pending") is not True:
        raise _magnus_error("File availability is unknown; supply the producing job_id to wait for relay imports")
    return False


def _wait_remaining(
    deadline: float,
) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _magnus_error("Timed out waiting for custody import; no download was started")
    return remaining


def _wait_for_file(
    file_secret: str,
    job_id: Optional[str],
    wait_timeout: float,
    poll_interval: float,
) -> None:
    from . import default_client
    deadline = time.monotonic() + wait_timeout
    while True:
        status = default_client.get_file_status(
            file_secret,
            job_id = job_id,
            timeout = min(10.0, _wait_remaining(deadline)),
        )
        _wait_remaining(deadline)
        if _ready_or_raise(status):
            return
        time.sleep(min(poll_interval, _wait_remaining(deadline)))


async def _wait_for_file_async(
    file_secret: str,
    job_id: Optional[str],
    wait_timeout: float,
    poll_interval: float,
) -> None:
    import asyncio
    from . import default_client
    deadline = time.monotonic() + wait_timeout
    while True:
        status = await default_client.get_file_status_async(
            file_secret,
            job_id = job_id,
            timeout = min(10.0, _wait_remaining(deadline)),
        )
        _wait_remaining(deadline)
        if _ready_or_raise(status):
            return
        await asyncio.sleep(min(poll_interval, _wait_remaining(deadline)))


def download_file(
    file_secret: str,
    target_path: Optional[str] = None,
    timeout: Optional[float] = None,
    overwrite: bool = True,
    *,
    job_id: Optional[str] = None,
    wait_timeout: float = 0.0,
    poll_interval: float = 2.0,
) -> Path:
    _validate_wait_options(
        wait_timeout,
        poll_interval,
    )
    token = normalize_secret(file_secret)
    staged = _read_staged_dropin(
        token,
        target_path,
        overwrite,
    )
    if staged is not None:
        return staged
    if wait_timeout > 0:
        _wait_for_file(
            file_secret,
            job_id,
            wait_timeout,
            poll_interval,
        )
    return _download_http(
        token,
        target_path,
        timeout,
        overwrite,
    )


async def download_file_async(
    file_secret: str,
    target_path: Optional[str] = None,
    timeout: Optional[float] = None,
    overwrite: bool = True,
    *,
    job_id: Optional[str] = None,
    wait_timeout: float = 0.0,
    poll_interval: float = 2.0,
) -> Path:
    import asyncio
    _validate_wait_options(
        wait_timeout,
        poll_interval,
    )
    token = normalize_secret(file_secret)
    staged = await asyncio.to_thread(
        _read_staged_dropin,
        token,
        target_path,
        overwrite,
    )
    if staged is not None:
        return staged
    if wait_timeout > 0:
        # Poll in the coroutine so cancellation cannot start a later download.
        await _wait_for_file_async(
            file_secret,
            job_id,
            wait_timeout,
            poll_interval,
        )
    return await asyncio.to_thread(
        _download_http,
        token,
        target_path,
        timeout,
        overwrite,
    )


def _parse_filename(headers: httpx.Headers) -> Optional[str]:
    message = Message()
    message["content-disposition"] = headers.get("content-disposition", "")
    filename = message.get_filename()
    return _safe_filename(filename) if filename else None
