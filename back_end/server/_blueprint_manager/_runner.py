"""Bounded, credential-free process execution for Python Blueprints."""

import hashlib
import importlib.util
import itertools
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import sysconfig
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BlueprintUnavailable(RuntimeError):
    pass


@dataclass(
    frozen=True,
)
class RuntimeSettings:
    backend: str = "auto"
    timeout_seconds: float = 8.0
    memory_bytes: int = 512 * 1024 * 1024
    max_concurrent: int = 4
    max_input_bytes: int = 4 * 1024 * 1024
    max_output_bytes: int = 4 * 1024 * 1024
    docker_image: str = "magnus-blueprint-runtime:1"
    bwrap_executable: str = "bwrap"

    def __post_init__(
        self,
    ) -> None:
        if self.backend not in ("auto", "bubblewrap", "docker"):
            raise ValueError(
                "Blueprint backend must be auto, bubblewrap or docker",
            )
        if (
            not math.isfinite(
                self.timeout_seconds,
            )
            or self.timeout_seconds <= 0
        ):
            raise ValueError(
                "Blueprint timeout must be finite and positive",
            )
        for field in (
            "memory_bytes",
            "max_concurrent",
            "max_input_bytes",
            "max_output_bytes",
        ):
            if (
                type(
                    getattr(
                        self,
                        field,
                    ),
                )
                is not int
                or getattr(
                    self,
                    field,
                )
                <= 0
            ):
                raise ValueError(
                    f"Blueprint {field} must be a positive integer",
                )
        if not self.docker_image or self.docker_image.startswith(
            "-",
        ):
            raise ValueError(
                "Invalid Blueprint Docker runtime image",
            )


class BlueprintRuntime:
    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        *,
        owner_key: str | None = None,
    ) -> None:
        self.settings = settings or RuntimeSettings()
        self._slots = threading.BoundedSemaphore(
            self.settings.max_concurrent,
        )
        self._blocked = threading.Event()
        identity = owner_key or str(
            Path(
                __file__,
            )
            .resolve()
            .parent,
        )
        self._docker_owner = (
            "org.magnus.blueprint.owner="
            + hashlib.sha256(
                identity.encode(),
            ).hexdigest()
        )
        self._docker_guard = threading.Lock()
        self._containers: set[str] = set()

    def _bubblewrap_command(
        self,
    ) -> list[str]:
        executable = shutil.which(
            self.settings.bwrap_executable,
        )
        if executable is None:
            raise BlueprintUnavailable(
                "Blueprint isolation requires bubblewrap; install bwrap or configure the Docker evaluator",
            )
        root = (
            Path(
                __file__,
            )
            .resolve()
            .parent
        )
        python = Path(
            getattr(
                sys,
                "_base_executable",
                sys.executable,
            ),
        ).resolve()
        command = [
            executable,
            "--unshare-all",
            "--unshare-user",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
        ]
        libraries = {
            Path(
                "/usr/lib",
            ),
            Path(
                "/lib",
            ),
            Path(
                "/lib64",
            ),
            Path(
                sysconfig.get_path(
                    "stdlib",
                ),
            ),
            Path(
                sysconfig.get_config_var(
                    "LIBDIR",
                )
                or sys.base_prefix + "/lib",
            ),
        }
        # A runtime may live under HOME, but only these interpreter/library
        # paths are exposed, never the account or checkout directory itself.
        for path in sorted(
            libraries,
            key=lambda item: (
                len(
                    item.parts,
                ),
                str(
                    item,
                ),
            ),
        ):
            if path.exists():
                command.extend(
                    [
                        "--ro-bind",
                        str(
                            path,
                        ),
                        str(
                            path,
                        ),
                    ],
                )
        command.extend(
            [
                "--ro-bind",
                str(
                    python,
                ),
                str(
                    python,
                ),
            ],
        )
        for name in (
            "pydantic",
            "pydantic_core",
            "typing_extensions",
            "typing_inspection",
            "annotated_types",
        ):
            spec = importlib.util.find_spec(
                name,
            )
            if spec is None or spec.origin is None:
                raise BlueprintUnavailable(
                    f"Missing evaluator dependency: {name}",
                )
            source = Path(
                spec.origin,
            ).resolve()
            destination = f"/runtime/site-packages/{name}.py"
            if spec.submodule_search_locations:
                source = source.parent
                destination = f"/runtime/site-packages/{name}"
            command.extend(
                [
                    "--ro-bind",
                    str(
                        source,
                    ),
                    destination,
                ],
            )
        command.extend(
            [
                "--ro-bind",
                str(
                    root,
                ),
                "/evaluator/_blueprint_manager",
                "--ro-bind",
                str(
                    root.parent / "_blueprint_protocol.py",
                ),
                "/evaluator/_blueprint_protocol.py",
                "--proc",
                "/proc",
                "--remount-ro",
                "/proc",
                "--dev",
                "/dev",
                "--remount-ro",
                "/dev",
                "--dir",
                "/tmp",
                "--chdir",
                "/tmp",
                "--remount-ro",
                "/",
                "--",
                str(
                    python,
                ),
                "-I",
                "-S",
                "-B",
                "/evaluator/_blueprint_manager/_worker.py",
            ],
        )
        return command

    def _docker_command(
        self,
        name: str,
    ) -> list[str]:
        executable = shutil.which(
            "docker",
        )
        if executable is None:
            raise BlueprintUnavailable(
                "Blueprint isolation requires Docker on this platform",
            )
        root = (
            Path(
                __file__,
            )
            .resolve()
            .parent
        )
        uid = (
            os.getuid()
            if hasattr(
                os,
                "getuid",
            )
            else 65534
        )
        gid = (
            os.getgid()
            if hasattr(
                os,
                "getgid",
            )
            else 65534
        )
        user = f"{uid}:{gid}" if uid else "65534:65534"
        return [
            executable,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network=none",
            "--ipc=none",
            "--read-only",
            "--cap-drop=ALL",
            "--log-driver=none",
            "--label",
            self._docker_owner,
            "--security-opt=no-new-privileges",
            "--pids-limit=16",
            f"--memory={self.settings.memory_bytes}",
            f"--memory-swap={self.settings.memory_bytes}",
            "--cpus=1",
            f"--user={user}",
            "--workdir=/tmp",
            "--mount",
            f"type=bind,source={root},target=/evaluator/_blueprint_manager,readonly",
            "--mount",
            f"type=bind,source={root.parent / '_blueprint_protocol.py'},target=/evaluator/_blueprint_protocol.py,readonly",
            "-i",
            self.settings.docker_image,
            "python",
            "-I",
            "-B",
            "/evaluator/_blueprint_manager/_worker.py",
        ]

    def _remove_container(
        self,
        name: str,
    ) -> None:
        cleanup = subprocess.run(
            ["docker", "rm", "--force", name],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if cleanup.returncode and b"No such container" not in cleanup.stderr:
            raise BlueprintUnavailable(
                "Could not confirm Blueprint container cleanup",
            )

    @property
    def uses_docker(
        self,
    ) -> bool:
        return self.settings.backend == "docker" or (
            self.settings.backend == "auto" and sys.platform != "linux"
        )

    def _reap_containers(
        self,
    ) -> None:
        listing = subprocess.run(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                "label=" + self._docker_owner,
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if listing.returncode:
            raise BlueprintUnavailable(
                "Cannot inspect Blueprint runtime containers",
            )
        for stale in listing.stdout.decode(
            "utf-8",
        ).splitlines():
            if stale not in self._containers:
                self._remove_container(
                    stale,
                )

    def cleanup_orphans(
        self,
    ) -> None:
        # Runs independently of admission, including when failed pipe/CLI
        # cleanup has blocked new evaluations. Docker can finish a create RPC
        # after an earlier rm reported that its name did not yet exist.
        if self.uses_docker:
            with self._docker_guard:
                self._reap_containers()

    def _claim_container(
        self,
        name: str,
    ) -> None:
        with self._docker_guard:
            self._reap_containers()
            self._containers.add(
                name,
            )

    def _encode_request(
        self,
        request: dict[str, Any],
    ) -> bytes:
        # Reject oversized historical code/preferences before the encoder can
        # allocate their escaped representation. HTTP bodies are bounded too.
        remaining = self.settings.max_input_bytes
        pending = [
            (
                iter(
                    (request,),
                ),
                0,
            )
        ]
        while pending:
            iterator, depth = pending[-1]
            try:
                value = next(
                    iterator,
                )
            except StopIteration:
                pending.pop()
                continue
            if depth > 128:
                raise ValueError(
                    "Blueprint request is nested too deeply",
                )
            if isinstance(
                value,
                str,
            ):
                remaining -= len(
                    value,
                )
            elif isinstance(
                value,
                dict,
            ):
                remaining -= len(
                    value,
                )
                pending.append(
                    (
                        itertools.chain.from_iterable(
                            value.items(),
                        ),
                        depth + 1,
                    ),
                )
            elif isinstance(
                value,
                (list, tuple),
            ):
                remaining -= len(
                    value,
                )
                pending.append(
                    (
                        iter(
                            value,
                        ),
                        depth + 1,
                    ),
                )
            else:
                remaining -= 1
            if remaining < 0:
                raise ValueError(
                    "Blueprint request exceeds the evaluator input limit",
                )
        payload = json.dumps(
            request,
            ensure_ascii=False,
            allow_nan=False,
        ).encode(
            "utf-8",
        )
        if (
            len(
                payload,
            )
            > self.settings.max_input_bytes
        ):
            raise ValueError(
                "Blueprint request exceeds the evaluator input limit",
            )
        return payload

    def evaluate(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        settings = self.settings
        payload = self._encode_request(
            request,
        )
        if self._blocked.is_set():
            raise BlueprintUnavailable(
                "Blueprint evaluator cleanup needs operator attention",
            )
        if not self._slots.acquire(
            blocking=False,
        ):
            raise BlueprintUnavailable(
                "Blueprint evaluators are busy; retry this request later",
            )
        docker_name = None
        container_claimed = False
        process = None
        try:
            backend = settings.backend
            if backend == "auto":
                backend = "bubblewrap" if sys.platform == "linux" else "docker"
            if backend == "docker":
                docker_name = "magnus-blueprint-" + uuid.uuid4().hex
                command = self._docker_command(
                    docker_name,
                )
                self._claim_container(
                    docker_name,
                )
                container_claimed = True
            else:
                command = self._bubblewrap_command()
            command.extend(
                [
                    str(
                        settings.timeout_seconds,
                    ),
                    str(
                        settings.memory_bytes,
                    ),
                    str(
                        settings.max_output_bytes,
                    ),
                    str(
                        settings.max_input_bytes,
                    ),
                ],
            )
            environment = {
                key: os.environ[key]
                for key in (
                    "PATH",
                    "SYSTEMROOT",
                    "WINDIR",
                    "DOCKER_HOST",
                    "DOCKER_CONTEXT",
                    "DOCKER_CONFIG",
                    "DOCKER_CERT_PATH",
                    "DOCKER_TLS_VERIFY",
                    "HOME",
                    "USERPROFILE",
                    "XDG_RUNTIME_DIR",
                )
                if key in os.environ
            }
            # Docker CLI configuration belongs to the trusted launcher.
            # Nothing is forwarded to the container; bwrap clears its env.
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                start_new_session=os.name != "nt",
            )
            data, errors = self._exchange(
                process,
                payload,
            )
            if process.returncode:
                detail = errors[:1500].decode(
                    "utf-8",
                    errors="replace",
                )
                if process.returncode < 0 or process.returncode in (137, 142, 153):
                    raise ValueError(
                        "Blueprint evaluation exceeded its resource limit",
                    )
                raise BlueprintUnavailable(
                    f"Blueprint evaluator failed: {detail.strip()}",
                )
            try:
                response = json.loads(
                    data,
                )
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise ValueError(
                    "Blueprint evaluator returned invalid JSON",
                ) from None
            if not isinstance(
                response,
                dict,
            ):
                raise ValueError(
                    "Blueprint evaluator returned an invalid response",
                )
            if "error" in response:
                raise ValueError(
                    str(
                        response["error"],
                    )[:2000],
                )
            return response
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BlueprintUnavailable(
                "Blueprint runtime is unavailable",
            ) from error
        finally:
            cleanup_errors = []
            try:
                if process is not None and process.poll() is None:
                    self._kill(
                        process,
                    )
            except (OSError, subprocess.TimeoutExpired) as error:
                cleanup_errors.append(
                    error,
                )
            try:
                # CLI cleanup failure must not skip the independent Docker API
                # removal. Label reconciliation also reaps late create replies
                # and containers left by a controller crash on the next call.
                if container_claimed:
                    with self._docker_guard:
                        try:
                            self._remove_container(
                                docker_name,
                            )
                        finally:
                            self._containers.discard(
                                docker_name,
                            )
            except (OSError, subprocess.TimeoutExpired, BlueprintUnavailable) as error:
                cleanup_errors.append(
                    error,
                )
            finally:
                if cleanup_errors:
                    self._blocked.set()
                self._slots.release()
            if cleanup_errors:
                raise BlueprintUnavailable(
                    "Could not confirm Blueprint evaluator cleanup",
                ) from cleanup_errors[0]

    @staticmethod
    def _kill(
        process: subprocess.Popen,
    ) -> None:
        if process.poll() is None:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(
                        process.pid,
                        signal.SIGKILL,
                    )
                except ProcessLookupError:
                    pass
        process.wait(
            timeout=5,
        )

    def _exchange(
        self,
        process: subprocess.Popen,
        payload: bytes,
    ) -> tuple[bytes, bytes]:
        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()
        failed = threading.Event()

        def read(
            stream,
            buffer: bytearray,
        ) -> None:
            try:
                while block := stream.read(
                    65536,
                ):
                    if (
                        len(
                            buffer,
                        )
                        + len(
                            block,
                        )
                        > self.settings.max_output_bytes
                    ):
                        overflow.set()
                        self._kill(
                            process,
                        )
                        return
                    buffer.extend(
                        block,
                    )
            except (OSError, subprocess.TimeoutExpired):
                failed.set()
            finally:
                stream.close()

        def write() -> None:
            try:
                process.stdin.write(
                    payload,
                )
                process.stdin.close()
            except (OSError, BrokenPipeError):
                pass

        threads = [
            threading.Thread(
                target=read,
                args=(process.stdout, buffers[0]),
                daemon=True,
            ),
            threading.Thread(
                target=read,
                args=(process.stderr, buffers[1]),
                daemon=True,
            ),
            threading.Thread(
                target=write,
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        try:
            process.wait(
                timeout=self.settings.timeout_seconds + 5,
            )
        except subprocess.TimeoutExpired:
            self._kill(
                process,
            )
            raise ValueError(
                "Blueprint evaluation exceeded its time limit",
            ) from None
        finally:
            for thread in threads:
                thread.join(
                    timeout=5,
                )
            if any(thread.is_alive() for thread in threads):
                self._blocked.set()
                raise BlueprintUnavailable(
                    "Blueprint evaluator pipes did not close",
                )
        if overflow.is_set():
            raise ValueError(
                "Blueprint response exceeds the evaluator output limit",
            )
        if failed.is_set():
            raise BlueprintUnavailable(
                "Blueprint evaluator communication failed",
            )
        return bytes(
            buffers[0],
        ), bytes(
            buffers[1],
        )
