"""Run isolated regressions without importing live-site examples or the server."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "backend": ROOT / "back_end/python_scripts/tests",
    "sdk": ROOT / "sdks/python/tests",
}
# Keep existing script paths usable by Jobs. These are manual runtime examples,
# not isolated regressions; test_services/ is also intentionally not traversed.
MANUAL_BACKEND_MODULES = {
    "test_github_tools",
    "test_gpu_nvlink",
    "test_magnus_basic",
    "test_signal_job",
}


def test_modules(
    suite: str,
) -> list[str]:
    excluded = MANUAL_BACKEND_MODULES if suite == "backend" else set()
    return sorted(
        path.stem
        for path in SUITES[suite].glob("test_*.py")
        if path.stem not in excluded
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
    )
    parser.add_argument(
        "--suite",
        choices=("all", *SUITES),
        default="all",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List test modules without importing or running them.",
    )
    arguments = parser.parse_args()
    suites = list(SUITES) if arguments.suite == "all" else [arguments.suite]
    if not arguments.list and "backend" in suites and sys.version_info < (3, 14):
        parser.error("The backend suite requires Python 3.14 or newer.")

    failed = False
    for suite in suites:
        modules = test_modules(
            suite,
        )
        if not modules:
            parser.error(f"No tests found for {suite} in {SUITES[suite]}")
        if arguments.list:
            for module in modules:
                print(f"{suite}: {module}")
            continue
        print(f"Running {suite}: {len(modules)} test modules", flush=True)
        # Separate interpreters avoid collisions between identically named SDK
        # and backend tests and prevent fixture modules leaking across suites.
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", *modules],
            cwd=SUITES[suite],
            stdin=subprocess.DEVNULL,
            check=False,
        )
        failed = failed or result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
