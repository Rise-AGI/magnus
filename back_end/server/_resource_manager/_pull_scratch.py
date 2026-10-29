"""Per-pull output and OCI scratch, reclaimed only after their writers exit."""

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ._build_progress import write_json
from ._processes import _process_exists as process_exists
from ._processes import _process_group_exists as process_group_exists
from ._processes import process_lease_is_idle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PullScratch:
    cache: Path
    output_directory: Path

    @property
    def output(
        self,
    ) -> Path:
        return self.output_directory / "image.sif"

    @classmethod
    def create(
        cls,
        cache_root: str,
        output_root: str,
    ) -> "PullScratch":
        cache = Path(
            tempfile.mkdtemp(
                prefix="pull-",
                dir=cache_root,
            ),
        )
        output_directory = Path(
            output_root,
        ) / cache.name
        output_created = False
        try:
            # A private sibling of the final SIF keeps publication on its
            # filesystem even when the OCI cache lives on a different mount.
            output_directory.mkdir(
                mode=0o700,
            )
            output_created = True
            write_json(
                cache / "owner.json",
                {"version": 1, "pid": os.getpid()},
            )
            write_json(
                cache / "process.json",
                {"state": "idle"},
            )
        except BaseException:
            if output_created:
                shutil.rmtree(
                    output_directory,
                )
            shutil.rmtree(
                cache,
            )
            raise
        return cls(
            cache=cache,
            output_directory=output_directory,
        )

    def cleanup(
        self,
    ) -> bool:
        if not process_lease_is_idle(
            self.cache,
        ):
            logger.warning(
                "Preserved pull scratch %s; subprocess exit has not been confirmed",
                self.cache.name,
            )
            return False
        # Keep the lease until both trees are gone so failed cleanup is retryable.
        for directory in (self.output_directory, self.cache):
            try:
                shutil.rmtree(
                    directory,
                )
            except FileNotFoundError:
                pass
        return True


def _positive_pid(
    value: object,
) -> int:
    limit = 0xFFFFFFFF if os.name == "nt" else 0x7FFFFFFF
    if type(value) is not int or not 0 < value <= limit:
        raise ValueError("Invalid process identifier")
    return value


def recover_pull_scratch(
    cache_root: str,
    output_root: str,
) -> None:
    for directory in Path(
        cache_root,
    ).glob(
        "pull-*",
    ):
        if directory.is_symlink() or not directory.is_dir():
            continue
        scratch = PullScratch(
            cache=directory,
            output_directory=Path(
                output_root,
            ) / directory.name,
        )
        try:
            if scratch.output_directory.is_symlink():
                raise ValueError("Linked pull output directory")
            if any((directory / name).is_symlink() for name in ("owner.json", "process.json")):
                raise ValueError("Linked pull lease")
            owner = json.loads(
                (directory / "owner.json").read_text(
                    encoding="utf-8",
                ),
            )
            lease = json.loads(
                (directory / "process.json").read_text(
                    encoding="utf-8",
                ),
            )
            if (
                not isinstance(owner, dict)
                or owner.get("version") != 1
                or not isinstance(lease, dict)
                or lease.get("state") not in {"idle", "running"}
            ):
                raise ValueError("Incomplete pull lease")
            if process_exists(
                _positive_pid(
                    owner.get("pid"),
                ),
            ):
                continue
            if lease["state"] == "running":
                pid = _positive_pid(
                    lease.get("pid"),
                )
                if process_exists(
                    pid,
                ) or (
                    os.name == "posix"
                    and process_group_exists(
                        pid,
                    )
                ):
                    continue
            write_json(
                directory / "process.json",
                {"state": "idle"},
            )
            scratch.cleanup()
        except (OSError, ValueError, TypeError):
            # Old unleased directories may belong to a surviving subprocess.
            # A filename or age alone cannot establish that it is safe to delete.
            logger.warning(
                "Preserved pull scratch %s; its lease or cleanup requires inspection",
                directory.name,
            )
        else:
            logger.info(
                "Recovered abandoned pull scratch: %s",
                directory.name,
            )
