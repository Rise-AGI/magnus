"""Bounded build diagnostics persisted in backend-owned, per-Job directories."""
from __future__ import annotations

import base64
import binascii
import codecs
import json
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path


LOG_LIMIT = 4 * 1024 * 1024
LOG_CHUNK_LIMIT = 16 * 1024
LOG_CHUNK_COUNT = LOG_LIMIT // LOG_CHUNK_LIMIT + 1
TAIL_LIMIT = 16 * 1024
STEP_LIMIT = 128
TRUNCATION_MARKER = b"[Earlier build output was truncated]\n"


def _timestamp(
    value: float,
) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def write_json(
    path: Path,
    value: dict,
) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class BuildProgress:
    def __init__(
        self,
        *,
        started_at: float | None = None,
    ) -> None:
        self.lock = threading.RLock()
        self.phase = "waiting"
        self.started = time.time() if started_at is None else started_at
        self.updated = self.started
        self.finished: float | None = None
        self.error: str | None = None
        self.steps: OrderedDict[str, dict] = OrderedDict()
        # Offsets address bytes, independently of append calls and block merges.
        # Even millions of empty lines retain at most LOG_CHUNK_COUNT objects.
        self.logs: deque[tuple[int, bytearray]] = deque()
        self.log_size = 0
        self.sequence = 0
        self.log_decoders: OrderedDict[tuple[str, str], codecs.IncrementalDecoder] = OrderedDict()
        self.written: dict[str, int] = {}
        self.cancelled_directories: set[str] = set()
        self.disk_lock = threading.Lock()

    def set_phase(
        self,
        phase: str,
        error: str | None = None,
    ) -> None:
        with self.lock:
            self.phase = phase
            self.error = error[:8192] if error else None
            self.updated = time.time()
            if phase in {"ready", "failed", "cancelled"}:
                self.finished = self.updated
                for decoder in self.log_decoders.values():
                    final = decoder.decode(b"", final=True)
                    if final:
                        self.append(final)
                self.log_decoders.clear()
        if error:
            self.append(error)

    def append(
        self,
        text: str,
    ) -> None:
        chunk = text.encode("utf-8", errors="replace")
        if not chunk.endswith(b"\n"):
            chunk += b"\n"
        if len(chunk) > LOG_LIMIT:
            chunk = TRUNCATION_MARKER + chunk[-(LOG_LIMIT - len(TRUNCATION_MARKER)):]
        with self.lock:
            if self.logs and len(self.logs[-1][1]) + len(chunk) <= LOG_CHUNK_LIMIT:
                self.logs[-1][1].extend(chunk)
                self.sequence += len(chunk)
            else:
                remaining = memoryview(chunk)
                while remaining:
                    if not self.logs or len(self.logs[-1][1]) == LOG_CHUNK_LIMIT:
                        self.logs.append((self.sequence, bytearray()))
                    block = self.logs[-1][1]
                    count = min(LOG_CHUNK_LIMIT - len(block), len(remaining))
                    block.extend(remaining[:count])
                    self.sequence += count
                    remaining = remaining[count:]
            self.log_size += len(chunk)
            while self.log_size > LOG_LIMIT or len(self.logs) > LOG_CHUNK_COUNT:
                self.log_size -= len(self.logs.popleft()[1])
            self.updated = time.time()

    def consume(
        self,
        line: str,
    ) -> None:
        if not line.lstrip().startswith("{"):
            self.append(line)
            return
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            self.append(line)
            return
        if not isinstance(event, dict) or not event or not event.keys() <= {
            "vertexes", "statuses", "logs", "warnings",
        } or any(
            not isinstance(items, list) or any(not isinstance(item, dict) for item in items)
            for items in event.values()
        ):
            self.append(line)
            return
        with self.lock:
            for vertex in event.get("vertexes", []):
                identifier = str(vertex.get("digest", ""))[:128]
                previous = self.steps.get(identifier, {})
                step = {
                    "id": identifier,
                    "name": str(vertex.get("name", previous.get("name", "")))[:1024],
                    # Vertex updates are complete; BuildKit omits false booleans.
                    "cached": bool(vertex.get("cached", False)),
                    "completed": bool(vertex.get("completed")),
                    "error": str(vertex["error"])[:2048] if vertex.get("error") else None,
                }
                self.steps[identifier] = step
                self.steps.move_to_end(identifier)
                while len(self.steps) > STEP_LIMIT:
                    self.steps.popitem(last=False)
                if step != previous:
                    suffix = " (cached)" if step["cached"] else ""
                    self.append(f"{step['name']}{suffix}")
                if step["error"]:
                    self.append(step["error"])
            invalid_log = False
            for log in event.get("logs", []):
                try:
                    decoded = base64.b64decode(log.get("data", ""), validate=True)
                except (ValueError, TypeError, binascii.Error):
                    invalid_log = True
                    continue
                stream = (str(log.get("vertex", ""))[:128], str(log.get("stream", ""))[:16])
                decoder = self.log_decoders.get(stream)
                if decoder is None:
                    decoder = codecs.getincrementaldecoder("utf-8")("replace")
                    self.log_decoders[stream] = decoder
                self.log_decoders.move_to_end(stream)
                text = decoder.decode(decoded)
                if text:
                    self.append(text)
                while len(self.log_decoders) > STEP_LIMIT * 2:
                    _, expired = self.log_decoders.popitem(last=False)
                    final = expired.decode(b"", final=True)
                    if final:
                        self.append(final)
            if invalid_log:
                self.append(line)
            self.updated = time.time()

    def snapshot(
        self,
        *,
        cancelled: bool = False,
    ) -> dict:
        with self.lock:
            steps = [dict(step) for step in self.steps.values()]
            active = [step for step in steps if not step["completed"]]
            tail_chunks = []
            remaining = TAIL_LIMIT
            for _, chunk in reversed(self.logs):
                tail_chunks.append(bytes(chunk[-remaining:]))
                remaining -= len(tail_chunks[-1])
                if remaining <= 0:
                    break
            tail = b"".join(reversed(tail_chunks))
            finished = time.time() if cancelled else self.finished
            return {
                "phase": "cancelled" if cancelled else self.phase,
                "started_at": _timestamp(self.started),
                "updated_at": _timestamp(self.updated),
                "finished_at": _timestamp(finished) if finished else None,
                "elapsed_seconds": max(0, (finished or time.time()) - self.started),
                "terminal": cancelled or self.finished is not None,
                "current_step": active[-1]["name"] if active else None,
                "steps": steps,
                "cached_steps": sum(step["cached"] for step in steps),
                "completed_steps": sum(step["completed"] for step in steps),
                "total_steps": len(steps),
                "log_tail": tail.decode("utf-8", errors="replace"),
                "error": self.error,
            }

    def persist(
        self,
        directory: Path,
        *,
        cancelled: bool = False,
    ) -> None:
        # Flushes from the monitor, completing waiter and cancelled waiter may
        # run in different executor threads. Serialize them per operation.
        with self.disk_lock:
            # ImageBuildManager creates this private directory outside the Job
            # workspace. Reject links defensively; never reuse Job-owned logs.
            for path in (directory, directory / "output.txt", directory / "progress.json"):
                if path.is_symlink() or path.is_junction():
                    raise OSError(f"Build diagnostics must not use a linked path: {path}")
            if cancelled:
                self.cancelled_directories.add(str(directory))
            cancelled = str(directory) in self.cancelled_directories
            destination = directory / "output.txt"
            with self.lock:
                previous = self.written.get(str(directory), 0)
                chunks = [
                    bytes(chunk[max(0, previous - offset):])
                    for offset, chunk in self.logs if offset + len(chunk) > previous
                ]
                last_sequence = self.sequence
                missed_output = bool(self.logs and self.logs[0][0] > previous)
            addition = b"".join(chunks)
            # The memory queue may have wrapped before a waiter's first flush,
            # or between flushes. Mark that gap even if the disk file is small.
            if missed_output:
                addition = TRUNCATION_MARKER + addition
            if str(directory) not in self.written:
                destination.write_bytes(b"")
            size = destination.stat().st_size if destination.exists() else 0
            if size + len(addition) > LOG_LIMIT:
                retained_limit = LOG_LIMIT - len(TRUNCATION_MARKER)
                with destination.open("rb") as stream:
                    stream.seek(max(0, size - retained_limit))
                    retained = stream.read(retained_limit)
                destination.write_bytes(TRUNCATION_MARKER + (retained + addition)[-retained_limit:])
            elif addition:
                with destination.open("ab") as stream:
                    stream.write(addition)
            self.written[str(directory)] = last_sequence
            write_json(directory / "progress.json", self.snapshot(cancelled=cancelled))
