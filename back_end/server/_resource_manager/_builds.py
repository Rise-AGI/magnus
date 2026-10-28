"""Job-native image preparation with shared builds and pinned, disposable results.

BuildKit owns Dockerfile interpretation and step caching. This coordinator owns
the complete-request mapping, Job waiters, runtime copies and station budget.
"""
from __future__ import annotations

import asyncio
import errno
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
from ._scratch_windows import prepare_private_directory as _prepare_windows_scratch_root


logger = logging.getLogger(__name__)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _stage_sif(
    source: Path,
    destination: Path,
) -> None:
    try:
        os.replace(
            source,
            destination,
        )
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        shutil.copyfile(
            source,
            destination,
        )


class _CacheBudgetError(ImageBuildError):
    """Capacity pressure that may be resolved by evicting unused cache."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        usage: dict[str, int | None],
    ) -> None:
        self.reason = reason
        self.usage = usage
        super().__init__(f"{message}; storage_bytes={json.dumps(usage, sort_keys=True)}")


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
        # Windows stat() and fstat() can disagree on deprecated st_ctime after
        # a rename. Both expose the same creation time through st_birthtime.
        information.st_birthtime_ns if os.name == "nt" else information.st_ctime_ns,
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


def _process_group_exists(
    process_group: int,
) -> bool:
    try:
        os.killpg(
            process_group,
            0,
        )
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _prepare_scratch_root(
    directory: Path,
) -> Path:
    """Create a private leaf without following a pre-created link.

    Configured parent symlinks are resolved once. On POSIX, descriptor-relative
    traversal validates every ancestor that can rename a descendant; sticky
    shared directories such as /dev/shm remain usable.
    """
    directory = directory.parent.resolve() / directory.name
    if os.name == "nt":
        try:
            return _prepare_windows_scratch_root(directory)
        except OSError as error:
            raise ImageBuildError(str(error)) from error

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(directory.anchor, flags)
    try:
        for component in directory.parts[1:-1]:
            parent = os.fstat(descriptor)
            if parent.st_uid not in {0, os.geteuid()} or (
                parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX
            ):
                raise ImageBuildError("Image build scratch has an untrusted writable parent directory")
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        parent = os.fstat(descriptor)
        if parent.st_uid not in {0, os.geteuid()} or (
            parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX
        ):
            raise ImageBuildError("Image build scratch has an untrusted writable parent directory")
        try:
            os.mkdir(directory.name, mode=0o700, dir_fd=descriptor)
        except FileExistsError:
            pass
        leaf = os.open(directory.name, flags, dir_fd=descriptor)
        try:
            information = os.fstat(leaf)
            if information.st_uid != os.geteuid():
                raise ImageBuildError("Image build scratch directory belongs to another account")
            os.fchmod(leaf, 0o700)
            current = os.stat(directory.name, dir_fd=descriptor, follow_symlinks=False)
            if (information.st_dev, information.st_ino) != (current.st_dev, current.st_ino):
                raise ImageBuildError("Image build scratch directory changed during initialization")
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)
    return directory


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
        # Tags and explicitly shared scratch parents are partitioned by station.
        self.namespace = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:16]
        self.legacy_temporary_root = Path(magnus_config["server"]["ephemeral_root"]) / "image_build_tmp"
        scratch_root = self.config.get("scratch_root")
        self.temporary_root = (
            Path(scratch_root) / f"magnus-build-{self.namespace}"
            if scratch_root else self.legacy_temporary_root
        )
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
        # Event-loop-only reservations, serialized with GC and publication.
        self.incoming_paths: set[Path] = set()
        self.pending_tags: set[str] = set()
        self.initialized = False
        self.stopping = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.initialize_lock = asyncio.Lock()
        self.maintenance_lock = asyncio.Lock()
        self.inventory_lock = asyncio.Lock()
        self.cached_budget_lock = asyncio.Lock()
        self.budget_generation = 0
        self.cached_budget_generation = -1
        self.cached_budget_checked_at = 0.0
        self.budget_check_sequence = 0
        self.budget_volumes: tuple[Path, ...] = (self.root, self.temporary_root)
        self.slots = asyncio.Semaphore(self.config.get("max_concurrent", 2))
        self.budget = _parse_size_string(self.config.get("cache_size", "40G"))
        self.scratch_budget = self.config.get("scratch_size_bytes") or _parse_size_string(
            self.config.get("scratch_size") or self.config.get("cache_size", "40G"),
        )
        self.free_floor = _parse_size_string(self.config.get("min_free_space", "5G"))

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
            previous_root = self.temporary_root
            self.temporary_root = await blocking_io(_prepare_scratch_root, self.temporary_root)
            if self.legacy_temporary_root == previous_root:
                self.legacy_temporary_root = self.temporary_root
            elif os.path.lexists(self.legacy_temporary_root):
                self.legacy_temporary_root = await blocking_io(_prepare_scratch_root, self.legacy_temporary_root)
            if os.name == "posix":
                await blocking_io(os.chmod, self.root, 0o755)
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
        for directory in (
            directory for root in self._scratch_roots() for directory in root.glob("build-*")
        ):
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
                    # run_command starts a POSIX session: its PID also names
                    # the group whose writers can outlive the recorded leader.
                    if _process_exists(pid) or (os.name == "posix" and _process_group_exists(pid)):
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
        # Pins protect the selected artifact while validation runs. A first
        # SIF checksum can be a large read and must not serialize other hits.
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
        async with self.maintenance_lock:
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
        await self._ensure_cached_budget()
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
                self.budget_generation += 1
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
                if directory:
                    self.budget_generation += 1
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
        incoming: Path | None = None
        digest: str | None = None
        published_digest: str | None = None
        published_result: dict | None = None
        previous_result: dict | None = None
        self.budget_generation += 1
        try:
            await self._ensure_budget()
            # Register ownership before Docker can install its temporary tag.
            # Orphan GC may run throughout load, conversion and copying.
            if is_local_mode:
                async with self.maintenance_lock:
                    self.pending_tags.add(temporary_tag)
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
                result = {
                    "key": operation.key,
                    "digest": digest,
                    "docker_image": digest,
                    "docker_tag": self._docker_tag(digest),
                    "size_bytes": int(inspected["Size"]),
                }
            else:
                operation.progress.set_phase("converting")
                temporary_sif = directory / "image.sif"
                environment = os.environ.copy()
                environment["APPTAINER_CACHEDIR"] = str(directory / "apptainer-cache")
                environment["APPTAINER_TMPDIR"] = str(directory / "apptainer-tmp")
                await blocking_io((directory / "apptainer-tmp").mkdir)
                memory = self.config.get("conversion_memory_bytes") or _parse_size_string(
                    self.config.get("conversion_memory", "1G"),
                )
                # mksquashfs -mem bounds its cache allocation, not the whole
                # conversion process. The explicit suffix is portable across
                # the supported mksquashfs versions.
                squashfs_arguments = (
                    f"-processors {self.config.get('conversion_cpus', 2)} "
                    f"-mem {max(1, memory // (1024 * 1024))}M"
                )
                await run_command(
                    [
                        "apptainer", "build", "--mksquashfs-args", squashfs_arguments,
                        str(temporary_sif), f"docker-archive:{archive}",
                    ],
                    on_line=operation.progress.append,
                    environment=environment,
                    timeout=self.config["timeout_seconds"],
                    process_directory=directory,
                )
                await run_command(["apptainer", "inspect", str(temporary_sif)])
                await self._ensure_budget()
                digest = await blocking_io(_hash_file, temporary_sif)
                path = self._sif_path(digest)
                incoming = self.root / f".{digest.split(':')[1]}.{uuid.uuid4().hex}.tmp"
                async with self.maintenance_lock:
                    self.incoming_paths.add(incoming)
                # Same-volume staging needs no second SIF. Cross-volume copies
                # retain their reservation and joined cancellation semantics.
                await blocking_io(
                    _stage_sif,
                    temporary_sif,
                    incoming,
                )
                await blocking_io(os.chmod, incoming, 0o644)
                result = {
                    "key": operation.key,
                    "digest": digest,
                    "sif_path": str(path),
                    "size_bytes": (await blocking_io(incoming.stat)).st_size,
                }
            # Account for the live archive, conversion scratch and incoming
            # runtime copy before cleanup, even if conversion finished between
            # monitor samples. This remains a soft, sampled station budget.
            await self._ensure_budget()
            await blocking_io(self._clear_scratch_payload, directory)
            async with self.maintenance_lock:
                try:
                    # A cancelled subprocess/rename can already have published
                    # its output, so rollback ownership starts before the call.
                    published_digest = digest
                    if is_local_mode:
                        await run_command(
                            ["docker", "tag", temporary_tag, result["docker_tag"]],
                            process_directory=directory,
                        )
                        await self._remove_docker_tag(temporary_tag)
                    else:
                        await blocking_io(os.replace, incoming, path)
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
                    # Rollback must finish before another publisher/collector
                    # can observe the canonical tag/file or index.
                    cleanup = asyncio.create_task(self._rollback_import(
                        operation,
                        published_digest,
                        published_result,
                        previous_result,
                        temporary_tag,
                    ))
                    await self._join_cleanup(cleanup)
                    raise
        finally:
            cleanup = asyncio.create_task(self._release_import_reservations(
                incoming,
                temporary_tag,
            ))
            await self._join_cleanup(cleanup)
            self.budget_generation += 1

    @staticmethod
    def _clear_scratch_payload(
        directory: Path,
    ) -> None:
        # Keep the ownership/process lease through the publication transaction.
        # Startup must wait for an interrupted Docker tag command before GC.
        for path in directory.iterdir():
            if path.name in {"owner.json", "process.json"}:
                continue
            if path.is_dir() and not path.is_symlink() and not path.is_junction():
                shutil.rmtree(path)
            else:
                path.unlink()

    @staticmethod
    async def _join_cleanup(
        cleanup: asyncio.Task,
    ) -> None:
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()

    async def _release_import_reservations(
        self,
        incoming: Path | None,
        temporary_tag: str,
    ) -> None:
        async with self.maintenance_lock:
            try:
                if is_local_mode:
                    await self._discard_import_tag(temporary_tag)
                elif incoming is not None:
                    await blocking_io(incoming.unlink, missing_ok=True)
            except (ImageBuildError, OSError):
                # A failed cleanup becomes an ordinary orphan for a later GC.
                logger.exception("Cannot remove temporary imported image")
            finally:
                self.pending_tags.discard(temporary_tag)
                if incoming is not None:
                    self.incoming_paths.discard(incoming)

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

    def _scratch_roots(
        self,
    ) -> tuple[Path, ...]:
        roots = (self.temporary_root,)
        if self.legacy_temporary_root != self.temporary_root and self.legacy_temporary_root.exists():
            roots += (self.legacy_temporary_root,)
        return roots

    def _local_usage(
        self,
    ) -> tuple[int, int, int]:
        size = _directory_size(self.root)
        scratch_size = 0
        free = shutil.disk_usage(self.root).free
        volumes = [self.root]
        for root in self._scratch_roots():
            occupied = _directory_size(root)
            scratch_size += occupied
            # An empty legacy directory is no longer used after a configured
            # move and need not meet the new scratch volume's free-space floor.
            if root == self.temporary_root or occupied:
                free = min(free, shutil.disk_usage(root).free)
                volumes.append(root)
            if not root.is_relative_to(self.root):
                size += occupied
        self.budget_volumes = tuple(volumes)
        return size, free, scratch_size

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
    ) -> tuple[int, int, int]:
        size, free, scratch_size = await asyncio.to_thread(self._local_usage)
        if is_local_mode:
            inventory = await self._docker_inventory()
            # Include interrupted imports absent from index.json. Shared layers
            # can be counted more than once, making this accounting conservative.
            size += sum(value["size"] for value in inventory.values())
        return size, free, scratch_size

    async def _remove_orphans(
        self,
        protected: frozenset[str] = frozenset(),
    ) -> int:
        # Called under maintenance_lock, excluding cache hits and publication.
        removed_count = 0
        with self.lock:
            live = protected | set(self.pins.values()) | {value["digest"] for value in self.results.values()}
        if is_local_mode:
            for digest, value in (await self._docker_inventory()).items():
                for tag in value["tags"]:
                    if tag in self.pending_tags:
                        continue
                    if digest in live and tag == self._docker_tag(digest):
                        continue
                    try:
                        await self._remove_docker_tag(tag)
                        removed_count += 1
                    except ImageBuildError:
                        logger.warning("Cannot remove orphaned build image %s", tag)
        else:
            for path in await asyncio.to_thread(lambda: list(self.root.glob("*.sif"))):
                digest = f"sha256:{path.stem}"
                if _DIGEST.fullmatch(digest) and digest not in live:
                    await blocking_io(self._remove_sif, digest)
                    removed_count += 1
            for path in await asyncio.to_thread(lambda: list(self.root.glob(".*.tmp"))):
                if path not in self.incoming_paths:
                    await blocking_io(path.unlink, missing_ok=True)
                    removed_count += 1
        return removed_count

    def _storage_usage(
        self,
        *,
        local_size: int,
        free: int,
        scratch_size: int,
        worker_size: int | None,
    ) -> dict[str, int | None]:
        return {
            "local_cache": max(0, local_size - scratch_size),
            "scratch": scratch_size,
            "worker": worker_size,
            "total": local_size + worker_size if worker_size is not None else None,
            "cache_limit": self.budget,
            "scratch_limit": self.scratch_budget,
            "free": free,
            "free_floor": self.free_floor,
        }

    async def _check_budget(
        self,
    ) -> int:
        self.budget_check_sequence += 1
        sequence = self.budget_check_sequence
        self.cached_budget_generation = -1
        local_size, free, scratch_size = await self._usage()
        if scratch_size > self.scratch_budget:
            raise _CacheBudgetError(
                "Image build scratch budget exceeded; reduce the image or increase the scratch budget",
                reason="scratch",
                usage=self._storage_usage(
                    local_size=local_size,
                    free=free,
                    scratch_size=scratch_size,
                    worker_size=None,
                ),
            )
        worker_size = await self.client.disk_usage()
        usage = self._storage_usage(
            local_size=local_size,
            free=free,
            scratch_size=scratch_size,
            worker_size=worker_size,
        )
        if local_size + worker_size > self.budget:
            raise _CacheBudgetError(
                "Image build cache budget exceeded; reduce the image or increase the station budget",
                reason="cache",
                usage=usage,
            )
        if free < self.free_floor:
            raise _CacheBudgetError(
                "Insufficient disk space for image preparation",
                reason="free",
                usage=usage,
            )
        return sequence

    async def _collect_cache(
        self,
    ) -> None:
        async with self.maintenance_lock:
            await self._collect_cache_locked()
        # GC thresholds only bound retained cache. Active writes require the
        # worker/scratch volumes' filesystem quota for a strict physical cap.

    async def _ensure_cached_budget(
        self,
    ) -> None:
        # Reuse does not add image bytes. Coalesce it within the same three-
        # second sampling window as active-build monitoring; writes still check
        # independently. A cancelled waiter never cancels another check owner.
        async with self.cached_budget_lock:
            generation = self.budget_generation
            if (
                self.cached_budget_generation == generation
                and time.monotonic() - self.cached_budget_checked_at < 3
            ):
                roots = self.budget_volumes
                free = await blocking_io(
                    lambda: min(shutil.disk_usage(root).free for root in roots),
                )
                if (
                    free >= self.free_floor
                    and self.budget_generation == generation
                    and self.cached_budget_generation == generation
                ):
                    return
            self.cached_budget_generation = -1
            sequence = await self._ensure_budget()
            if self.budget_generation == generation and sequence == self.budget_check_sequence:
                self.cached_budget_generation = generation
                self.cached_budget_checked_at = time.monotonic()

    async def _ensure_budget(
        self,
    ) -> int:
        try:
            return await self._check_budget()
        except _CacheBudgetError as error:
            if error.reason == "scratch":
                raise
            async with self.maintenance_lock:
                return await self._ensure_budget_locked()

    async def _ensure_budget_locked(
        self,
        protected: frozenset[str] = frozenset(),
    ) -> int:
        # Publication already owns maintenance_lock. Recheck after acquiring it
        # so another collector can resolve pressure without duplicate pruning.
        try:
            return await self._check_budget()
        except _CacheBudgetError as error:
            if error.reason == "scratch":
                raise
            await self._collect_cache_locked(protected)
            return await self._check_budget()

    async def _collect_cache_locked(
        self,
        protected: frozenset[str] = frozenset(),
    ) -> None:
        self.budget_generation += 1
        local_size, free, scratch_size = await self._usage()
        if scratch_size > self.scratch_budget:
            return
        worker_size = await self.client.disk_usage()
        before = self._storage_usage(
            local_size=local_size,
            free=free,
            scratch_size=scratch_size,
            worker_size=worker_size,
        )
        removed_orphans = await self._remove_orphans(protected)
        target = int(self.budget * 0.8)
        if not removed_orphans and local_size + worker_size <= target and free >= self.free_floor:
            return
        local_size, free, scratch_size = await self._usage()
        removed_count = 0
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
                        removed_count += 1
                    self.evicting.discard(digest)
            local_size, free, scratch_size = await self._usage()
        await self._save_index()
        local_size, free, scratch_size = await self._usage()
        worker_size = await self.client.disk_usage()
        keep_bytes = None
        # The worker owns its GC policy. Reclaim only the aggregate excess,
        # not a scratch-driven 80% target that can turn into prune(0).
        # Worker data may be remote or on a different filesystem, so pruning
        # it is not a remedy for a local free-space-only failure.
        if scratch_size <= self.scratch_budget and local_size < self.budget < local_size + worker_size:
            keep_bytes = self.budget - local_size
            logger.info(
                "Pruning dedicated BuildKit cache: before=%s worker_keep_bytes=%s",
                self._storage_usage(
                    local_size=local_size,
                    free=free,
                    scratch_size=scratch_size,
                    worker_size=worker_size,
                ),
                keep_bytes,
            )
            await self.client.prune(keep_bytes)
        if removed_count or removed_orphans or keep_bytes is not None:
            local_size, free, scratch_size = await self._usage()
            worker_size = await self.client.disk_usage()
            with self.lock:
                jobs = sorted(self.jobs)
            logger.info(
                "Image build cache reclamation: jobs=%s before=%s after=%s "
                "worker_keep_bytes=%s removed_runtime_copies=%s removed_orphan_entries=%s",
                jobs,
                before,
                self._storage_usage(
                    local_size=local_size,
                    free=free,
                    scratch_size=scratch_size,
                    worker_size=worker_size,
                ),
                keep_bytes,
                removed_count,
                removed_orphans,
            )
