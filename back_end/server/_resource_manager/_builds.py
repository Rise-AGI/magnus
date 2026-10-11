"""Job-native image preparation with shared builds and pinned, disposable results.

BuildKit owns Dockerfile interpretation and step caching. This coordinator owns
the complete-request mapping, Job waiters, runtime copies and station budget.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .._magnus_config import is_local_mode, magnus_config
from .._size_utils import _parse_size_string
from ._build_progress import BuildProgress, write_json
from ._buildkit import BuildKitClient, ImageBuildError, blocking_io, run_command


logger = logging.getLogger(__name__)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class _CacheBudgetError(ImageBuildError):
    """Capacity pressure that may be resolved by evicting unused cache."""


def _directory_size(
    directory: Path,
) -> int:
    size = 0
    for parent, _, names in os.walk(directory, followlinks=False):
        for name in names:
            try:
                path = Path(parent) / name
                if not path.is_symlink():
                    size += path.stat().st_size
            except FileNotFoundError:
                continue
    return size


def _hash_file(
    path: Path,
) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _file_signature(
    information: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        information.st_dev,
        information.st_ino,
        information.st_size,
        information.st_mtime_ns,
        information.st_ctime_ns,
    )


def _process_exists(
    pid: int,
) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    # os.kill(pid, 0) is not a liveness probe on Windows: non-console signals
    # map to TerminateProcess. Query the process handle without sending signals.
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID
    try:
        return kernel.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel.CloseHandle(handle)


@dataclass
class _BuildOperation:
    key: str
    dockerfile: str
    progress: BuildProgress = field(default_factory=BuildProgress)
    waiters: dict[str, Path] = field(default_factory=dict)
    task: asyncio.Task | None = None
    closing: bool = False
    predecessor: asyncio.Task | None = None
    result: dict | None = None


class ImageBuildManager:
    def __init__(
        self,
    ) -> None:
        self.config = magnus_config["execution"].get("image_build", {"enabled": False})
        self.enabled = self.config.get("enabled", False)
        self.root = Path(magnus_config["server"]["root"]) / "image_build_cache"
        self._diagnostics_root = Path(magnus_config["server"]["root"]) / "build_diagnostics"
        self.temporary_root = Path(magnus_config["server"]["ephemeral_root"]) / "image_build_tmp"
        self.client = BuildKitClient(self.config)
        self.lock = threading.RLock()
        # Verification runs in filesystem workers. Its lock never protects Job
        # state, so hashing a large SIF cannot block API/scheduler state access.
        self.verification_lock = threading.Lock()
        self.verified_sifs: dict[str, tuple[int, int, int, int, int]] = {}
        self.operations: dict[str, _BuildOperation] = {}
        self.jobs: dict[str, _BuildOperation] = {}
        self.tasks: set[asyncio.Task] = set()
        self.pins: dict[str, str] = {}
        self.results: dict[str, dict] = {}
        self.evicting: set[str] = set()
        self.initialized = False
        self.stopping = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.initialize_lock = asyncio.Lock()
        self.maintenance_lock = asyncio.Lock()
        self.inventory_lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(self.config.get("max_concurrent", 2))
        self.budget = _parse_size_string(self.config.get("cache_size", "40G"))
        self.free_floor = _parse_size_string(self.config.get("min_free_space", "5G"))
        # Dedicated image tags prevent cleanup from touching operator-owned tags.
        self.namespace = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:16]

    def restore_pins(
        self,
        mapping: dict[str, dict],
    ) -> None:
        """Call before scheduler startup/GC, including externally active Jobs."""
        with self.lock:
            for job_id, result in mapping.items():
                digest = result.get("digest")
                if isinstance(digest, str) and _DIGEST.fullmatch(digest):
                    self.pins[job_id] = digest

    async def ensure(
        self,
        job_id: str,
        dockerfile: str,
    ) -> dict:
        if not self.enabled:
            raise ImageBuildError("Dockerfile builds are not enabled on this station")
        await self._initialize()
        specification = json.dumps(
            {"version": 1, "dockerfile": dockerfile, "platform": self.config["platform"], "local": is_local_mode},
            sort_keys=True,
            ensure_ascii=True,
        )
        key = hashlib.sha256(specification.encode()).hexdigest()
        directory = await blocking_io(self.diagnostics_directory, job_id, create=True)
        with self.lock:
            if self.stopping:
                raise ImageBuildError("Image builder is shutting down")
            if job_id in self.jobs:
                raise ImageBuildError("This Job already has an active image preparation")
            operation = self.operations.get(key)
            if operation is None or operation.closing or (operation.task and operation.task.done()):
                predecessor = operation.task if operation else None
                operation = _BuildOperation(key=key, dockerfile=dockerfile, predecessor=predecessor)
                self.operations[key] = operation
                operation.task = asyncio.create_task(self._run(operation))
                self.tasks.add(operation.task)
                # A final waiter may leave before the task raises; consume its
                # exception even when no ensure() remains to retrieve it.
                operation.task.add_done_callback(self._observe_completion)
            operation.waiters[job_id] = directory
            self.jobs[job_id] = operation
            if operation.result is not None:
                self.pins[job_id] = operation.result["digest"]
            task = operation.task
        assert task is not None
        try:
            result = await asyncio.shield(task)
            with self.lock:
                if self.jobs.get(job_id) is not operation:
                    raise asyncio.CancelledError
            await blocking_io(operation.progress.persist, directory)
            with self.lock:
                if self.jobs.get(job_id) is not operation:
                    raise asyncio.CancelledError
                self.pins[job_id] = result["digest"]
                operation.waiters.pop(job_id, None)
                self.jobs.pop(job_id, None)
            return dict(result)
        except BaseException:
            cancelled = task.cancelled() or not task.done()
            self.release(job_id)
            try:
                await blocking_io(
                    operation.progress.persist,
                    directory,
                    cancelled=cancelled,
                )
            except OSError:
                logger.exception("Cannot save build diagnostics for Job %s", job_id)
            raise

    def _observe_completion(
        self,
        task: asyncio.Task,
    ) -> None:
        with self.lock:
            self.tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def release(
        self,
        job_id: str,
    ) -> None:
        """Release a waiter or runtime pin; safe from FastAPI's thread pool."""
        with self.lock:
            self.pins.pop(job_id, None)
            operation = self.jobs.pop(job_id, None)
            if operation is None:
                return
            operation.waiters.pop(job_id, None)
            if operation.waiters or operation.closing:
                return
            operation.closing = True
            task = operation.task
            loop = self.loop
        if task is not None and loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._cancel_operation, operation)

    def _cancel_operation(
        self,
        operation: _BuildOperation,
    ) -> None:
        with self.lock:
            task = operation.task
            if not operation.waiters and task is not None and not task.done():
                task.cancel()

    @property
    def diagnostics_root(
        self,
    ) -> Path:
        self._reject_diagnostic_link(self._diagnostics_root)
        return self._diagnostics_root

    @staticmethod
    def _reject_diagnostic_link(
        path: Path,
    ) -> None:
        if path.is_symlink() or path.is_junction():
            raise OSError(f"Build diagnostics must not use a linked path: {path}")

    def diagnostics_directory(
        self,
        job_id: str,
        *,
        create: bool = False,
    ) -> Path:
        """Backend-owned storage, outside every Job's writable bind mounts."""
        if not re.fullmatch(r"[0-9a-f]{16}", job_id):
            raise ValueError("Invalid Job identifier for build diagnostics")
        root = self.diagnostics_root
        directory = root / job_id
        self._reject_diagnostic_link(directory)
        if create:
            # Only the backend creates these directories. POSIX runner accounts
            # cannot traverse them; Windows inherits the configured server ACL.
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.mkdir(mode=0o700, exist_ok=True)
            if os.name == "posix":
                root.chmod(0o700)
                directory.chmod(0o700)
        return directory

    def get_log_path(
        self,
        job_id: str,
    ) -> Path:
        path = self.diagnostics_directory(job_id) / "output.txt"
        self._reject_diagnostic_link(path)
        return path

    def get_progress(
        self,
        job_id: str,
        *,
        building: bool = False,
    ) -> dict | None:
        directory = self.diagnostics_directory(job_id)
        with self.lock:
            operation = self.jobs.get(job_id)
        if operation:
            return operation.progress.snapshot()
        if building:
            # A persisted snapshot belongs to the previous attempt while a
            # restarted backend is still recovering and has no live operation.
            return None
        path = directory / "progress.json"
        try:
            self._reject_diagnostic_link(path)
            if path.stat().st_size > 256 * 1024:
                raise ValueError("Oversized build snapshot")
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Invalid build snapshot")
            return value
        except (OSError, ValueError):
            return None

    def is_available(
        self,
        result: dict,
    ) -> bool:
        """Check a restored runtime reference; caller runs this off the event loop."""
        digest = result.get("digest", "")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            return False
        if is_local_mode:
            if result.get("docker_image") != digest:
                return False
            try:
                inspected = subprocess.run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", digest],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                    check=False,
                    text=True,
                    encoding="utf-8",
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
            return inspected.returncode == 0 and inspected.stdout.strip() == digest
        path = self._sif_path(digest)
        expected_size = result.get("size_bytes")
        if result.get("sif_path") != str(path) or type(expected_size) is not int or expected_size <= 0:
            return False
        return self._verify_sif(path, digest, expected_size)

    def _verify_sif(
        self,
        path: Path,
        digest: str,
        expected_size: int,
    ) -> bool:
        # This memo is deliberately process-local: startup revalidates every
        # restored artifact, and replacing/truncating a file invalidates it.
        with self.verification_lock:
            try:
                information = path.stat(follow_symlinks=False)
                signature = _file_signature(information)
                if not stat.S_ISREG(information.st_mode) or information.st_size != expected_size:
                    self.verified_sifs.pop(digest, None)
                    return False
                if self.verified_sifs.get(digest) == signature:
                    return True
                self.verified_sifs.pop(digest, None)
                with path.open("rb") as stream:
                    if _file_signature(os.fstat(stream.fileno())) != signature:
                        return False
                    actual_digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
                    if _file_signature(os.fstat(stream.fileno())) != signature:
                        return False
                # Compare both the open inode and the current path: a rename
                # during hashing must not validate different published bytes.
                if actual_digest != digest or _file_signature(path.stat(follow_symlinks=False)) != signature:
                    return False
                self.verified_sifs[digest] = signature
                return True
            except OSError:
                self.verified_sifs.pop(digest, None)
                return False

    def _remove_sif(
        self,
        digest: str,
    ) -> None:
        # Join in-flight verification before deletion, without taking the
        # manager state lock or waiting for a thread lock on the event loop.
        with self.verification_lock:
            self._sif_path(digest).unlink(missing_ok=True)
            self.verified_sifs.pop(digest, None)

    async def shutdown(
        self,
    ) -> None:
        with self.lock:
            self.stopping = True
            tasks = list(self.tasks)
            for operation in self.operations.values():
                operation.closing = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _initialize(
        self,
    ) -> None:
        async with self.initialize_lock:
            if self.initialized:
                return
            self.loop = asyncio.get_running_loop()
            await blocking_io(self.root.mkdir, parents=True, exist_ok=True)
            await blocking_io(self.temporary_root.mkdir, mode=0o700, parents=True, exist_ok=True)
            if os.name == "posix":
                await blocking_io(os.chmod, self.root, 0o755)
                # Scratch may live on a shared filesystem. Only completed SIFs
                # belong in the runner-readable cache; recipes/archives do not.
                await blocking_io(os.chmod, self.temporary_root, 0o700)
            loaded = await asyncio.to_thread(self._read_index)
            with self.lock:
                self.results.update(loaded)
            # A previous server may have died while a buildctl/import process was
            # alive. Keep its scratch until that PID has exited; never kill a
            # possibly reused PID or remove a live process's output directory.
            deadline = self.loop.time() + self.config["timeout_seconds"]
            while await blocking_io(self._recover_scratch):
                remaining = deadline - self.loop.time()
                if remaining <= 0:
                    raise ImageBuildError(
                        "Previous image build processes did not exit within the recovery timeout; "
                        "their scratch has been preserved",
                    )
                # The wait belongs to the cancellable preparation task, rather
                # than a sleeping filesystem worker that can outlive shutdown.
                await asyncio.sleep(min(3, remaining))
            self.initialized = True

    def _read_index(
        self,
    ) -> dict[str, dict]:
        path = self.root / "index.json"
        if not path.exists():
            return {}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            values = document["results"]
            results = {}
            for key, value in values.items():
                if not re.fullmatch(r"[0-9a-f]{64}", key) or not _DIGEST.fullmatch(value["digest"]):
                    continue
                if is_local_mode:
                    expected_tag = self._docker_tag(value["digest"])
                    if value.get("docker_tag") != expected_tag or value.get("docker_image") != value["digest"]:
                        continue
                elif Path(value.get("sif_path", "")).resolve() != self._sif_path(value["digest"]).resolve():
                    continue
                results[key] = value
            return results
        except (OSError, ValueError, TypeError, KeyError):
            # Ignoring a broken index would strand unaccounted Docker images.
            raise ImageBuildError("Image build cache index is invalid; repair it before enabling builds")

    def _recover_scratch(
        self,
    ) -> bool:
        """Reclaim verified abandoned scratch and report children still running."""
        waiting = False
        for directory in self.temporary_root.glob("build-*"):
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                owner = int(json.loads((directory / "owner.json").read_text(encoding="utf-8"))["pid"])
                lease = json.loads((directory / "process.json").read_text(encoding="utf-8"))
                if owner <= 0 or lease.get("state") not in {"idle", "running"}:
                    raise ValueError("Incomplete process lease")
                # Initialization precedes this backend's builds. Even our own
                # PID in an old lease can be a reused identifier, not ownership.
                if _process_exists(owner):
                    raise ImageBuildError("Another backend owns image-build scratch; use a separate station root")
                if lease["state"] == "running":
                    pid = int(lease["pid"])
                    if pid <= 0:
                        raise ValueError("Invalid process identifier")
                    if _process_exists(pid):
                        waiting = True
                        continue
            except (ValueError, KeyError, OSError) as error:
                raise ImageBuildError(
                    f"Cannot verify abandoned build directory {directory.name}; "
                    "an operator must check its process lease before removing it"
                ) from error
            shutil.rmtree(directory)
        return waiting

    def _docker_tag(
        self,
        digest: str,
    ) -> str:
        return f"magnus-build/{self.namespace}:{digest.split(':')[1]}"

    def _sif_path(
        self,
        digest: str,
    ) -> Path:
        return self.root / f"{digest.split(':')[1]}.sif"

    async def _save_index(
        self,
    ) -> None:
        with self.lock:
            document = {"version": 1, "results": {key: dict(value) for key, value in self.results.items()}}
        await blocking_io(write_json, self.root / "index.json", document)

    async def _cached_result(
        self,
        operation: _BuildOperation,
    ) -> dict | None:
        async with self.maintenance_lock:
            with self.lock:
                result = self.results.get(operation.key)
                if result is None or result["digest"] in self.evicting:
                    return None
                result = dict(result)
                operation.result = result
                for job_id in operation.waiters:
                    self.pins[job_id] = result["digest"]
            try:
                if is_local_mode:
                    output = await run_command(
                        ["docker", "image", "inspect", "--format", "{{.Id}}", result["docker_tag"]],
                    )
                    valid = output.strip() == result["docker_image"]
                else:
                    valid = await blocking_io(self.is_available, result)
            except ImageBuildError:
                valid = False
            if valid:
                with self.lock:
                    self.results[operation.key]["accessed_at"] = time.time()
                await self._save_index()
            else:
                with self.lock:
                    self.results.pop(operation.key, None)
                    operation.result = None
                    for job_id in operation.waiters:
                        if self.pins.get(job_id) == result["digest"]:
                            self.pins.pop(job_id, None)
                await self._save_index()
                return None
        # Waiter pins (including late arrivals via operation.result) protect the
        # selected image while collection acquires maintenance_lock itself.
        await self._ensure_budget()
        return result

    async def _run(
        self,
        operation: _BuildOperation,
    ) -> dict:
        directory: Path | None = None
        monitor: asyncio.Task | None = None
        try:
            if operation.predecessor:
                await asyncio.gather(asyncio.shield(operation.predecessor), return_exceptions=True)
            cached = await self._cached_result(operation)
            if cached is not None:
                operation.progress.append("Reusing the prepared image from cache")
                operation.progress.set_phase("ready")
                return cached
            async with self.slots:
                # Another completed operation may have published while this
                # request was waiting for its construction slot.
                cached = await self._cached_result(operation)
                if cached is not None:
                    operation.progress.set_phase("ready")
                    return cached
                await self._collect_cache()
                await self._ensure_budget()
                directory = self.temporary_root / f"build-{uuid.uuid4().hex}"
                await blocking_io(self._prepare_directory, directory, operation.dockerfile)
                monitor = asyncio.create_task(self._monitor(operation))
                try:
                    async with asyncio.timeout(self.config["timeout_seconds"]):
                        operation.progress.set_phase("building")
                        temporary_tag = f"magnus-build/{self.namespace}:pending-{directory.name}"
                        archive = await self.client.build(directory, temporary_tag, operation.progress.consume)
                        operation.progress.set_phase("exporting")
                        result = await self._import_archive(operation, directory, archive, temporary_tag)
                        operation.progress.set_phase("ready")
                        return result
                except asyncio.CancelledError:
                    if monitor.done() and not monitor.cancelled():
                        error = monitor.exception()
                        if error is not None:
                            raise error
                    raise
        except asyncio.CancelledError:
            operation.progress.set_phase("cancelled")
            raise
        except TimeoutError as error:
            message = f"Image build exceeded {self.config['timeout_seconds']} seconds"
            operation.progress.set_phase("failed", message)
            raise ImageBuildError(message) from error
        except Exception as error:
            operation.progress.set_phase("failed", str(error))
            raise
        finally:
            # A last waiter can cancel first and shutdown can cancel again.
            # Keep the complete teardown joined, not just its filesystem calls.
            cleanup = asyncio.create_task(self._finish_operation(
                operation,
                monitor,
                directory,
            ))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()

    async def _finish_operation(
        self,
        operation: _BuildOperation,
        monitor: asyncio.Task | None,
        directory: Path | None,
    ) -> None:
        try:
            if monitor:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            await self._flush(operation)
        finally:
            try:
                if directory:
                    await blocking_io(shutil.rmtree, directory, True)
            finally:
                with self.lock:
                    if self.operations.get(operation.key) is operation:
                        self.operations.pop(operation.key, None)

    @staticmethod
    def _prepare_directory(
        directory: Path,
        dockerfile: str,
    ) -> None:
        directory.mkdir(mode=0o700, parents=True)
        write_json(directory / "owner.json", {"pid": os.getpid()})
        write_json(directory / "process.json", {"state": "idle"})
        (directory / "recipe").mkdir()
        (directory / "context").mkdir()
        (directory / "docker-config").mkdir()
        # Windows text I/O would expand submitted CRLF a second time.
        (directory / "recipe" / "Dockerfile").write_bytes(dockerfile.encode("utf-8"))

    async def _flush(
        self,
        operation: _BuildOperation,
    ) -> None:
        with self.lock:
            directories = list(operation.waiters.values())
        for directory in directories:
            try:
                await blocking_io(operation.progress.persist, directory)
            except OSError:
                logger.exception("Cannot persist image build progress")

    async def _monitor(
        self,
        operation: _BuildOperation,
    ) -> None:
        try:
            while True:
                await self._flush(operation)
                await self._ensure_budget()
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise
        except Exception:
            if operation.task:
                operation.task.cancel()
            raise

    async def _import_archive(
        self,
        operation: _BuildOperation,
        directory: Path,
        archive: Path,
        temporary_tag: str,
    ) -> dict:
        # Serialization covers lookup, publication and GC. No threading lock is
        # held while importing or converting an image.
        async with self.maintenance_lock:
            digest: str | None = None
            published_result: dict | None = None
            previous_result: dict | None = None
            try:
                if is_local_mode:
                    operation.progress.set_phase("loading")
                    await run_command(
                        ["docker", "load", "--input", str(archive)],
                        on_line=operation.progress.append,
                        timeout=self.config["timeout_seconds"],
                        process_directory=directory,
                    )
                    output = await run_command(
                        ["docker", "image", "inspect", "--format", "{{json .}}", temporary_tag],
                    )
                    inspected = json.loads(output)
                    identifier = inspected["Id"]
                    if not isinstance(identifier, str) or not _DIGEST.fullmatch(identifier):
                        raise ImageBuildError("Docker returned an invalid image content identifier")
                    digest = identifier
                    tag = self._docker_tag(digest)
                    await run_command(["docker", "tag", temporary_tag, tag])
                    await self._remove_docker_tag(temporary_tag)
                    result = {
                        "key": operation.key,
                        "digest": digest,
                        "docker_image": digest,
                        "docker_tag": tag,
                        "size_bytes": int(inspected["Size"]),
                    }
                else:
                    operation.progress.set_phase("converting")
                    temporary_sif = directory / "image.sif"
                    environment = os.environ.copy()
                    environment["APPTAINER_CACHEDIR"] = str(directory / "apptainer-cache")
                    environment["APPTAINER_TMPDIR"] = str(directory / "apptainer-tmp")
                    await blocking_io((directory / "apptainer-tmp").mkdir)
                    await run_command(
                        ["apptainer", "build", str(temporary_sif), f"docker-archive:{archive}"],
                        on_line=operation.progress.append,
                        environment=environment,
                        timeout=self.config["timeout_seconds"],
                        process_directory=directory,
                    )
                    await run_command(["apptainer", "inspect", str(temporary_sif)])
                    digest = await blocking_io(_hash_file, temporary_sif)
                    path = self._sif_path(digest)
                    await blocking_io(os.chmod, temporary_sif, 0o644)
                    # root and ephemeral_root may live on different filesystems.
                    # Copy into root before atomic publication; relay uses this
                    # immutable filename rather than the Dockerfile fingerprint.
                    incoming = self.root / f".{digest.split(':')[1]}.{uuid.uuid4().hex}.tmp"
                    try:
                        await blocking_io(shutil.copyfile, temporary_sif, incoming)
                        await blocking_io(os.chmod, incoming, 0o644)
                        await blocking_io(os.replace, incoming, path)
                    finally:
                        await blocking_io(incoming.unlink, missing_ok=True)
                    result = {
                        "key": operation.key,
                        "digest": digest,
                        "sif_path": str(path),
                        "size_bytes": path.stat().st_size,
                    }
                # Count completed runtime copies, without charging their now
                # disposable conversion scratch twice at the publication boundary.
                await blocking_io(shutil.rmtree, directory, True)
                # This immutable result is visible in inventory before index.json.
                # Protect it during pressure collection until waiter pins exist.
                await self._ensure_budget_locked(frozenset({digest}))
                result["accessed_at"] = time.time()
                with self.lock:
                    previous_result = self.results.get(operation.key)
                    self.results[operation.key] = result
                    operation.result = result
                    published_result = result
                    for job_id in operation.waiters:
                        self.pins[job_id] = digest
                await self._save_index()
                return dict(result)
            except BaseException:
                # Hold publication ownership until rollback finishes. A second
                # cancellation must not strand a completed import outside the
                # index or let the next collector race its removal.
                cleanup = asyncio.create_task(self._rollback_import(
                    operation,
                    digest,
                    published_result,
                    previous_result,
                    temporary_tag,
                ))
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                cleanup.result()
                raise

    async def _rollback_import(
        self,
        operation: _BuildOperation,
        digest: str | None,
        published_result: dict | None,
        previous_result: dict | None,
        temporary_tag: str,
    ) -> None:
        """Roll back this publication while its caller holds maintenance_lock."""
        with self.lock:
            if published_result is not None:
                if self.results.get(operation.key) is published_result:
                    if previous_result is None:
                        self.results.pop(operation.key, None)
                    else:
                        self.results[operation.key] = previous_result
                if operation.result is published_result:
                    operation.result = None
                for job_id in operation.waiters:
                    if self.jobs.get(job_id) is operation and self.pins.get(job_id) == digest:
                        self.pins.pop(job_id, None)
            retained = digest in self.pins.values() or any(
                result["digest"] == digest for result in self.results.values()
            )
        if is_local_mode:
            # The unique pending tag may have been installed even when docker
            # load/tag was cancelled before returning its successful status.
            tags = [temporary_tag]
            if digest is not None and not retained:
                tags.append(self._docker_tag(digest))
            for tag in tags:
                try:
                    await self._discard_import_tag(tag)
                except (ImageBuildError, OSError):
                    logger.exception("Cannot roll back imported image tag %s", tag)
        elif digest is not None and not retained:
            try:
                await blocking_io(self._remove_sif, digest)
            except OSError:
                logger.exception("Cannot roll back imported image %s", digest)
        if published_result is not None:
            # A cancelled write may already have replaced index.json. Restore
            # it after removing the unreferenced image, which also frees space
            # for this write when capacity pressure caused the original error.
            try:
                await self._save_index()
            except OSError:
                logger.exception("Cannot persist image cache rollback")

    async def _discard_import_tag(
        self,
        tag: str,
    ) -> None:
        async with self.inventory_lock:
            output = await run_command(
                ["docker", "image", "ls", "--quiet", "--filter", f"reference={tag}"],
            )
            if output.strip():
                await run_command(["docker", "image", "rm", tag])

    def _local_usage(
        self,
    ) -> tuple[int, int]:
        size = _directory_size(self.root) + _directory_size(self.temporary_root)
        free = min(shutil.disk_usage(self.root).free, shutil.disk_usage(self.temporary_root).free)
        return size, free

    async def _docker_inventory(
        self,
    ) -> dict[str, dict]:
        # Separate from maintenance_lock: import calls budget checks while
        # holding maintenance_lock. The monitor can inspect concurrently with
        # conversion, but a GC removal cannot invalidate ls -> inspect.
        async with self.inventory_lock:
            return await self._inspect_docker_inventory()

    async def _remove_docker_tag(
        self,
        tag: str,
    ) -> None:
        async with self.inventory_lock:
            await run_command(["docker", "image", "rm", tag])

    async def _inspect_docker_inventory(
        self,
    ) -> dict[str, dict]:
        output = await run_command(
            [
                "docker", "image", "ls",
                "--no-trunc", "--quiet",
                "--filter", f"reference=magnus-build/{self.namespace}:*",
            ],
        )
        identifiers = sorted(set(output.split()))
        inventory = {}
        for start in range(0, len(identifiers), 50):
            inspected = await run_command(
                [
                    "docker", "image", "inspect",
                    "--format", "{{json .}}",
                    *identifiers[start:start + 50],
                ],
            )
            for line in inspected.splitlines():
                value = json.loads(line)
                inventory[value["Id"]] = {
                    "size": max(0, int(value["Size"])),
                    "tags": [
                        tag for tag in value.get("RepoTags", [])
                        if tag.startswith(f"magnus-build/{self.namespace}:")
                    ],
                }
        return inventory

    async def _usage(
        self,
    ) -> tuple[int, int]:
        size, free = await asyncio.to_thread(self._local_usage)
        if is_local_mode:
            inventory = await self._docker_inventory()
            # Include interrupted imports absent from index.json. Shared layers
            # can be counted more than once, making this accounting conservative.
            size += sum(value["size"] for value in inventory.values())
        return size, free

    async def _remove_orphans(
        self,
        protected: frozenset[str] = frozenset(),
    ) -> None:
        # Called under maintenance_lock, excluding cache hits and publication.
        with self.lock:
            live = protected | set(self.pins.values()) | {value["digest"] for value in self.results.values()}
        if is_local_mode:
            for digest, value in (await self._docker_inventory()).items():
                for tag in value["tags"]:
                    if digest in live and tag == self._docker_tag(digest):
                        continue
                    try:
                        await self._remove_docker_tag(tag)
                    except ImageBuildError:
                        logger.warning("Cannot remove orphaned build image %s", tag)
        else:
            for path in await asyncio.to_thread(lambda: list(self.root.glob("*.sif"))):
                digest = f"sha256:{path.stem}"
                if _DIGEST.fullmatch(digest) and digest not in live:
                    await blocking_io(self._remove_sif, digest)
            for path in await asyncio.to_thread(lambda: list(self.root.glob(".*.tmp"))):
                await blocking_io(path.unlink, missing_ok=True)

    async def _check_budget(
        self,
    ) -> None:
        local_size, free = await self._usage()
        worker_size = await self.client.disk_usage()
        if local_size + worker_size > self.budget:
            raise _CacheBudgetError("Image build cache budget exceeded; reduce the image or increase the station budget")
        if free < self.free_floor:
            raise _CacheBudgetError("Insufficient disk space for image preparation")

    async def _collect_cache(
        self,
    ) -> None:
        async with self.maintenance_lock:
            await self._collect_cache_locked()
        # GC thresholds only bound retained cache. Active writes require the
        # worker/scratch volumes' filesystem quota for a strict physical cap.

    async def _ensure_budget(
        self,
    ) -> None:
        try:
            await self._check_budget()
        except _CacheBudgetError:
            async with self.maintenance_lock:
                await self._ensure_budget_locked()

    async def _ensure_budget_locked(
        self,
        protected: frozenset[str] = frozenset(),
    ) -> None:
        # Publication already owns maintenance_lock. Recheck after acquiring it
        # so another collector can resolve pressure without duplicate pruning.
        try:
            await self._check_budget()
        except _CacheBudgetError:
            await self._collect_cache_locked(protected)
            await self._check_budget()

    async def _collect_cache_locked(
        self,
        protected: frozenset[str] = frozenset(),
    ) -> None:
        await self._remove_orphans(protected)
        worker_size = await self.client.disk_usage()
        local_size, free = await self._usage()
        target = int(self.budget * 0.8)
        with self.lock:
            products = {value["digest"]: dict(value) for value in self.results.values()}
        for digest, result in sorted(products.items(), key=lambda item: item[1].get("accessed_at", 0)):
            if local_size + worker_size <= target and free >= self.free_floor:
                break
            with self.lock:
                if digest in protected or digest in self.pins.values():
                    continue
                self.evicting.add(digest)
            removed = False
            try:
                if is_local_mode:
                    await self._remove_docker_tag(result["docker_tag"])
                else:
                    await blocking_io(self._remove_sif, digest)
                removed = True
            except (ImageBuildError, OSError):
                logger.warning("Cannot evict prepared image %s", digest)
            finally:
                with self.lock:
                    if removed:
                        self.results = {key: value for key, value in self.results.items() if value["digest"] != digest}
                    self.evicting.discard(digest)
            local_size, free = await self._usage()
        await self._save_index()
        if local_size + worker_size > target or free < self.free_floor:
            await self.client.prune(max(0, target - local_size))
