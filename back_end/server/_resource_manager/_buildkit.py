"""BuildKit and image-import subprocesses; no shell or daemon lifecycle ownership."""
from __future__ import annotations

import asyncio
import codecs
import csv
import io
import json
import os
import signal
from collections.abc import Callable
from pathlib import Path

from ._helpers import _run_blocking_io as blocking_io
from ._build_progress import write_json
from ._processes import _process_group_exists

_PROCESS_STOP_GRACE_SECONDS = 5


class ImageBuildError(RuntimeError):
    pass


def _usage_filter(
    path: tuple[bool, ...],
) -> str:
    expression = ""
    accepts_end = True
    for lower in reversed(path):
        if lower:
            expression = "[0-9a-m]" + expression
            accepts_end = False
        else:
            expression = "[^0-9a-m]" + expression
            if accepts_end:
                expression = "(" + expression + "|$)"
    return "id~=^" + expression


def _build_error_detail(
    stderr: str,
) -> str:
    """Keep solve errors and Dockerfile locations without replaying progress."""
    diagnostics = io.StringIO()
    saw_progress = False
    has_diagnostic = False
    identifiers = {"vertexes": "digest", "statuses": "vertex", "logs": "vertex", "warnings": "vertex"}

    def append(
        line: str,
    ) -> None:
        nonlocal has_diagnostic
        diagnostics.write(line)
        diagnostics.write("\n")
        has_diagnostic = has_diagnostic or bool(line.strip())

    for line in io.StringIO(stderr, newline=None):
        line = line.removesuffix("\n")
        if not line.lstrip().startswith("{"):
            append(line)
            continue
        try:
            event = json.loads(line)
        except ValueError:
            append(line)
            continue
        if not isinstance(event, dict) or not event or not event.keys() <= identifiers.keys() or any(
            not isinstance(items, list) or any(
                not isinstance(item, dict) or not isinstance(item.get(identifiers[key]), str)
                or ("error" in item and (key != "vertexes" or not isinstance(item["error"], str)))
                for item in items
            )
            for key, items in event.items()
        ):
            append(line)
            continue
        saw_progress = True
        for vertex in event.get("vertexes", []):
            if vertex.get("error"):
                append(vertex["error"])
    # Preserve the original tail when the stream contains no usable diagnostic.
    detail = diagnostics.getvalue().removesuffix("\n") if saw_progress and has_diagnostic else stderr
    return detail[-8192:]


async def _wait_for_group_exit(
    process_group: int,
) -> None:
    if os.name != "posix":
        return
    while _process_group_exists(
        process_group,
    ):
        await asyncio.sleep(
            0.05,
        )


async def _stop_process(
    process: asyncio.subprocess.Process,
) -> None:
    if process.returncode is not None and os.name != "posix":
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(
            process.wait(),
            timeout=_PROCESS_STOP_GRACE_SECONDS,
        )
    except TimeoutError:
        pass
    finally:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
    # Sending a signal does not prove every writer has released its files.
    async with asyncio.timeout(
        _PROCESS_STOP_GRACE_SECONDS,
    ):
        await process.wait()
        await _wait_for_group_exit(
            process.pid,
        )


async def run_command(
    arguments: list[str],
    *,
    on_line: Callable[[str], None] | None = None,
    environment: dict[str, str] | None = None,
    timeout: float | None = 60,
    process_directory: Path | None = None,
) -> str:
    """Drain both pipes concurrently and retain a bounded diagnostic tail.

    POSIX process groups cover Apptainer's local children. BuildKit executes on
    its worker; closing buildctl's solve connection requests solve cancellation.
    """
    marker = process_directory / "process.json" if process_directory else None
    if marker:
        await blocking_io(
            write_json,
            marker,
            {"state": "spawning"},
        )
    # Keep process creation alive until we obtain its handle. Cancelling spawn
    # itself can reap only the parent, leaving Apptainer helpers in its group.
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        start_new_session=os.name == "posix",
        limit=1024 * 1024,
    ))
    output = bytearray()
    errors = bytearray()
    output_overflow = False
    readers: list[asyncio.Task] = []

    async def drain(
        stream: asyncio.StreamReader,
        destination: bytearray,
    ) -> None:
        nonlocal output_overflow
        pending = bytearray()
        decoder = None
        while chunk := await stream.read(65536):
            destination.extend(chunk)
            if destination is output and len(destination) > 4 * 1024 * 1024:
                output_overflow = True
            del destination[:-4 * 1024 * 1024]
            if on_line is None:
                await asyncio.sleep(0)
                continue
            pending.extend(chunk)
            start = 0
            lines = 0
            while (end := pending.find(b"\n", start)) >= 0:
                if decoder is not None:
                    on_line(decoder.decode(pending[start:end], final=True))
                    decoder = None
                else:
                    on_line(pending[start:end].decode("utf-8", errors="replace") if end > start else "")
                start = end + 1
                lines += 1
                if lines % 256 == 0:
                    # Already-buffered reads need not suspend. Bound each batch
                    # so cancellation and other Jobs can run during noisy output.
                    await asyncio.sleep(0)
            if start:
                del pending[:start]
            # Keep bounded fragments for unbroken lines, preserving UTF-8 bytes
            # split at the limit. Delete only the portion actually delivered.
            if len(pending) > 1024 * 1024:
                if decoder is None:
                    decoder = codecs.getincrementaldecoder("utf-8")("replace")
                on_line(decoder.decode(pending[:1024 * 1024]))
                del pending[:1024 * 1024]
            await asyncio.sleep(0)
        if on_line:
            final = decoder.decode(pending, final=True) if decoder else pending.decode("utf-8", errors="replace")
            if final:
                on_line(final)

    async def cleanup_command() -> None:
        try:
            process = await spawning
        except Exception:
            # Transport setup can fail after creating a process. Without its
            # handle we cannot disprove surviving helpers; retain "spawning".
            return

        assert process.stdout is not None and process.stderr is not None
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

        async def discard(
            stream: asyncio.StreamReader,
        ) -> None:
            while await stream.read(65536):
                pass

        # Cancelled gather readers stop consuming their pipes. Resume draining
        # before reaping, including when termination requests arrive repeatedly.
        drains = [
            asyncio.create_task(discard(process.stdout)),
            asyncio.create_task(discard(process.stderr)),
        ]
        try:
            await _stop_process(process)
        except TimeoutError as error:
            raise ImageBuildError(
                "Subprocesses did not exit after termination; process lease and scratch were preserved",
            ) from error
        finally:
            for drain_task in drains:
                drain_task.cancel()
            await asyncio.gather(*drains, return_exceptions=True)
        if marker:
            await blocking_io(
                write_json,
                marker,
                {"state": "idle"},
            )

    try:
        process = await asyncio.shield(spawning)
        assert process.stdout is not None and process.stderr is not None
        readers.extend([
            asyncio.create_task(drain(process.stdout, output)),
            asyncio.create_task(drain(process.stderr, errors)),
        ])
        if marker:
            await blocking_io(
                write_json,
                marker,
                {"state": "running", "pid": process.pid},
            )
        async with asyncio.timeout(timeout):
            await asyncio.gather(*readers)
            await process.wait()
            # EOF and the leader's exit do not imply that same-group helpers
            # finished writing. Publication and retries require their exit too.
            await _wait_for_group_exit(
                process.pid,
            )
        if marker:
            await blocking_io(
                write_json,
                marker,
                {"state": "idle"},
            )
    except BaseException:
        # Cleanup runs to completion even if callers cancel this operation again.
        cleanup = asyncio.create_task(cleanup_command())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        raise
    if process.returncode:
        detail = errors.decode("utf-8", errors="replace")
        detail = await blocking_io(_build_error_detail, detail) if on_line is not None else detail[-8192:]
        raise ImageBuildError(f"{Path(arguments[0]).name} exited with {process.returncode}: {detail}")
    if output_overflow and on_line is None:
        raise ImageBuildError(f"{Path(arguments[0]).name} returned too much data to inspect safely")
    return output.decode("utf-8", errors="replace")


class BuildKitClient:
    def __init__(
        self,
        config: dict,
    ) -> None:
        self.config = config
        self._usage_partitions: tuple[tuple[bool, ...], ...] = ((),)

    def arguments(
        self,
        *command: str,
    ) -> list[str]:
        arguments = [self.config.get("buildctl", "buildctl")]
        if self.config.get("address"):
            arguments.extend(["--addr", self.config["address"]])
        for key, option in (
            ("tls_ca", "--tlscacert"),
            ("tls_cert", "--tlscert"),
            ("tls_key", "--tlskey"),
            ("tls_server_name", "--tlsservername"),
        ):
            if self.config.get(key):
                arguments.extend([option, self.config[key]])
        return [*arguments, *command]

    async def build(
        self,
        directory: Path,
        image_name: str,
        on_line: Callable[[str], None],
    ) -> Path:
        archive = directory / "image.tar"
        # Exporter attributes use CSV, independently of OS command-line quoting.
        attributes = io.StringIO()
        csv.writer(attributes, lineterminator="").writerow([
            "type=docker",
            f"name={image_name}",
            f"dest={archive}",
        ])
        environment = os.environ.copy()
        # buildctl automatically reads Docker's auth store. A build must not
        # silently inherit the backend operator's personal registry credentials.
        environment["DOCKER_CONFIG"] = str(directory / "docker-config")
        # Registry challenges supply arbitrary token realms. Keep that HTTP
        # exchange inside the builder's network boundary, not on the backend.
        environment["BUILDKIT_NO_CLIENT_TOKEN"] = "true"
        await run_command(
            self.arguments(
                "build",
                "--frontend", "dockerfile.v0",
                "--local", f"dockerfile={directory / 'recipe'}",
                "--local", f"context={directory / 'context'}",
                "--opt", f"platform={self.config['platform']}",
                "--output", attributes.getvalue(),
                "--metadata-file", str(directory / "metadata.json"),
                "--progress", "rawjson",
            ),
            on_line=on_line,
            environment=environment,
            timeout=self.config["timeout_seconds"],
            process_directory=directory,
        )
        if not archive.is_file() or not archive.stat().st_size:
            raise ImageBuildError("BuildKit completed without a Docker image archive")
        return archive

    async def disk_usage(
        self,
    ) -> int:
        pending = list(self._usage_partitions)
        completed = []
        total = 0
        queries = 0
        # --format runs after the full unary RPC, including descriptions. Split
        # at the worker when metadata exceeds gRPC's limit; never prune to read.
        # Complementary ID filters include short and non-base36 IDs as well.
        async with asyncio.timeout(30):
            while pending:
                path = pending.pop()
                command = ["du", "--format", '{{range .}}{{.Size}}{{"\\n"}}{{end}}']
                if path:
                    command.extend(["--filter", _usage_filter(path)])
                queries += 1
                try:
                    output = await run_command(
                        self.arguments(*command),
                        timeout=30,
                    )
                except ImageBuildError as error:
                    detail = str(error)
                    oversized = (
                        "ResourceExhausted" in detail
                        and "message larger than max" in detail
                    ) or "returned too much data to inspect safely" in detail
                    if not oversized:
                        raise
                    if len(path) >= 16 or queries + len(pending) + 2 > 64:
                        raise ImageBuildError(
                            "BuildKit cache metadata is too large to inspect within bounded queries"
                        ) from error
                    pending.extend([(*path, False), (*path, True)])
                    continue
                try:
                    total += sum(max(0, int(value)) for value in output.split())
                except ValueError as error:
                    raise ImageBuildError("Cannot determine BuildKit cache usage") from error
                completed.append(path)
        # Publish only a complete partition, so concurrent readers can reuse
        # successful filters without ever observing a partially counted cache.
        self._usage_partitions = tuple(completed)
        return total

    async def prune(
        self,
        keep_bytes: int,
    ) -> None:
        # This is the station's dedicated worker. BuildKit protects active solve
        # references; its internal state directory is never modified by Magnus.
        await run_command(
            self.arguments(
                "prune",
                "--all",
                "--keep-storage", str(max(0, keep_bytes) / 1_000_000),
            ),
            timeout=120,
        )
