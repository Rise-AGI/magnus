# back_end/python_scripts/tests/test_signal_job.py
import os
import signal
import sys
import time


_received = [0]


def _on_sigterm(signum, frame):
    _received[0] += 1
    n = _received[0]
    if n < 4:
        print(
            f"[Magnus signal demo] pid={os.getpid()} received SIGTERM "
            f"#{n} (signal={signum}), still running",
            flush=True,
        )
        return
    print(
        f"[Magnus signal demo] pid={os.getpid()} received SIGTERM "
        f"#{n} (signal={signum}), exiting gracefully",
        flush=True,
    )
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)
    print(
        f"[Magnus signal demo] pid={os.getpid()} ready, "
        f"send SIGTERM 4 times — first 3 are reported, the 4th exits cleanly",
        flush=True,
    )
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
