"""BuildKit and image-import subprocesses; no shell or daemon lifecycle ownership."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import signal
from collections.abc import Callable
from pathlib import Path

from ._helpers import _run_blocking_io as blocking_io


class ImageBuildError(RuntimeError):
    pass


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
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
    finally:
        if os.name == "posix":
            # Apptainer's parent can exit before all namespace/helper children.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


async def run_command(
    arguments: list[str],
    *,
    on_line: Callable[[str], None] | None = None,
    environment: dict[str, str] | None = None,
    timeout: float = 60,
    process_directory: Path | None = None,
) -> str:
    """Drain both pipes concurrently and retain a bounded diagnostic tail.

    POSIX process groups cover Apptainer's local children. BuildKit executes on
    its worker; closing buildctl's solve connection requests solve cancellation.
    """
    marker = process_directory / "process.json" if process_directory else None
    if marker:
        await blocking_io(marker.write_text, json.dumps({"state": "spawning"}), encoding="utf-8")
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
        while chunk := await stream.read(65536):
            destination.extend(chunk)
            if destination is output and len(destination) > 4 * 1024 * 1024:
                output_overflow = True
            del destination[:-4 * 1024 * 1024]
            if on_line is None:
                continue
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, remainder = pending.partition(b"\n")
                pending = bytearray(remainder)
                on_line(line.decode("utf-8", errors="replace"))
            # Unbounded output without a newline cannot exhaust server memory.
            if len(pending) > 1024 * 1024:
                on_line(pending[:1024 * 1024].decode("utf-8", errors="replace"))
                pending.clear()
        if pending and on_line:
            on_line(pending.decode("utf-8", errors="replace"))

    async def cleanup_command() -> None:
        try:
            process = await spawning
        except Exception:
            # A failed spawn acquired no process; the original error propagates.
            if marker:
                await blocking_io(marker.write_text, json.dumps({"state": "idle"}), encoding="utf-8")
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
        finally:
            for drain_task in drains:
                drain_task.cancel()
            await asyncio.gather(*drains, return_exceptions=True)
        if marker:
            await blocking_io(marker.write_text, json.dumps({"state": "idle"}), encoding="utf-8")

    try:
        process = await asyncio.shield(spawning)
        assert process.stdout is not None and process.stderr is not None
        readers.extend([
            asyncio.create_task(drain(process.stdout, output)),
            asyncio.create_task(drain(process.stderr, errors)),
        ])
        if marker:
            await blocking_io(
                marker.write_text,
                json.dumps({"state": "running", "pid": process.pid}),
                encoding="utf-8",
            )
        async with asyncio.timeout(timeout):
            await asyncio.gather(*readers)
            await process.wait()
        if marker:
            await blocking_io(marker.write_text, json.dumps({"state": "idle"}), encoding="utf-8")
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
        detail = errors.decode("utf-8", errors="replace")[-8192:]
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
        # Format one integer per record, keeping parsing independent of the
        # version-specific UsageInfo JSON schema and human-readable units.
        output = await run_command(
            self.arguments("du", "--format", '{{range .}}{{.Size}}{{"\\n"}}{{end}}'),
            timeout=30,
        )
        try:
            return sum(max(0, int(value)) for value in output.split())
        except ValueError as error:
            raise ImageBuildError("Cannot determine BuildKit cache usage") from error

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
