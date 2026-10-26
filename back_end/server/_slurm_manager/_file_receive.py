"""Receive a fixed-size SSH payload without an unbounded pipe or disk buffer."""
import os
import subprocess
import threading
import time


def receive_file(
    command: list[str],
    destination: str,
    size: int,
    timeout: float,
    stop_process,
    cancel_event: threading.Event | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    stop = threading.Event()
    failure = []
    with subprocess.Popen(
        command,
        stdin = subprocess.DEVNULL,
        stdout = subprocess.PIPE,
        stderr = subprocess.DEVNULL,
        start_new_session = os.name != "nt",
    ) as process:
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)

        def copy():
            received = 0
            try:
                with open(destination, "wb") as target:
                    while not stop.is_set():
                        try:
                            block = os.read(process.stdout.fileno(), min(1024 * 1024, size - received + 1))
                        except BlockingIOError:
                            if process.poll() is not None:
                                break
                            stop.wait(0.01)
                            continue
                        if not block:
                            break
                        if received + len(block) > size:
                            raise ValueError("Remote payload exceeds reserved size")
                        target.write(block)
                        received += len(block)
                if received != size:
                    raise ValueError("Incomplete remote payload")
            except BaseException as error:
                failure.append(error)

        reader = threading.Thread(target=copy)
        reader.start()
        try:
            while reader.is_alive():
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Custody transfer cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Custody transfer exceeded its time budget")
                reader.join(min(0.1, remaining))
            if failure:
                raise failure[0]
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Custody transfer cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Custody transfer exceeded its time budget")
                try:
                    process.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode != 0:
                raise RuntimeError("Custody transfer failed")
        except BaseException:
            stop.set()
            stop_process(process)
            raise
        finally:
            stop.set()
            reader.join()
