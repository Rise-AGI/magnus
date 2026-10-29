"""SIF cache identity and ownership, independent of the scheduler and database."""

import hashlib
import logging
import os
import re
import shutil
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(
    __name__,
)


def image_filename(
    image: str,
) -> str:
    digest = hashlib.sha256(
        image.encode(
            "utf-8",
        ),
    ).hexdigest()
    # Legacy names collapse repeated underscores, so this namespace is disjoint.
    return f"image__{digest}.sif"


def legacy_image_filename(
    image: str,
) -> str:
    name = re.sub(
        r"^[a-z]+://",
        "",
        image,
    )
    name = re.sub(
        r"[/:@]",
        "_",
        name,
    )
    name = re.sub(
        r"_+",
        "_",
        name,
    ).strip(
        "_",
    )
    return f"{name}.sif"


class ImageCache:
    def __init__(
        self,
        root: str,
        size_limit: int,
    ) -> None:
        self.root = Path(
            root,
        )
        self.size_limit = size_limit
        self._lock = threading.RLock()
        self._collection_lock = threading.Lock()
        self._legacy: dict[str, str] = {}
        self._pins: dict[object, set[str]] = {}

    def restore_legacy_names(
        self,
        records: Iterable[tuple[str, str]],
    ) -> None:
        """Only reuse legacy names whose URI is recorded by the cache owner."""
        with self._lock:
            for image, filename in records:
                if (
                    filename
                    == legacy_image_filename(
                        image,
                    )
                    and "/" not in filename
                    and "\\" not in filename
                    and len(
                        filename.encode(
                            "utf-8",
                        ),
                    )
                    <= 255
                ):
                    self._legacy[image] = filename

    def path(
        self,
        image: str,
    ) -> str:
        with self._lock:
            filename = self._legacy.get(
                image,
            )
            if filename is not None:
                return str(
                    self.root / filename,
                )
            return str(
                self.root
                / image_filename(
                    image,
                ),
            )

    def forget(
        self,
        image: str,
    ) -> None:
        with self._lock:
            self._legacy.pop(
                image,
                None,
            )

    def pin(
        self,
        owner: object,
        image: str,
        *,
        preserve_legacy: bool = False,
    ) -> str:
        with self._lock:
            path = self.path(
                image,
            )
            paths = {path}
            if preserve_legacy:
                # Old wrappers may still name a legacy file whose cache record
                # was deleted. Protect it without guessing its image identity.
                filename = legacy_image_filename(
                    image,
                )
                if "/" not in filename and "\\" not in filename:
                    paths.add(
                        str(
                            self.root / filename,
                        ),
                    )
            self._pins[owner] = paths
            return path

    def release(
        self,
        owner: object,
    ) -> None:
        with self._lock:
            self._pins.pop(
                owner,
                None,
            )

    @contextmanager
    def hold(
        self,
        image: str,
    ) -> Iterator[str]:
        owner = object()
        path = self.pin(
            owner,
            image,
        )
        try:
            yield path
        finally:
            self.release(
                owner,
            )

    def inventory(
        self,
    ) -> list[tuple[str, int, float]]:
        images = []
        for path in self.root.glob(
            "*.sif",
        ):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                info = path.stat()
            except OSError:
                continue
            images.append(
                (
                    str(
                        path,
                    ),
                    info.st_size,
                    info.st_atime,
                ),
            )
        return images

    def remove(
        self,
        filename: str,
    ) -> bool:
        """Remove an idle entry; a Job or a publisher keeps ownership until done."""
        if (
            "/" in filename
            or "\\" in filename
            or not filename.endswith(
                ".sif",
            )
        ):
            raise ValueError(
                "Invalid image cache filename",
            )
        path = str(
            self.root / filename,
        )
        with self._lock:
            if any(path in paths for paths in self._pins.values()):
                return False
            try:
                os.unlink(
                    path,
                )
            except FileNotFoundError:
                pass
            return True

    def collect(
        self,
        target_free_bytes: int = 0,
    ) -> None:
        # Pins and unlink share one lock. A refreshed access time is not a lease:
        # a queued Job may need the same path long after preparation finishes.
        with self._collection_lock:
            images = sorted(
                self.inventory(),
                key=lambda item: item[2],
            )
            total_size = sum(item[1] for item in images)
            for path, size, _ in images:
                if total_size <= self.size_limit and (
                    target_free_bytes <= 0
                    or shutil.disk_usage(
                        self.root,
                    ).free
                    >= target_free_bytes
                ):
                    break
                with self._lock:
                    if any(path in paths for paths in self._pins.values()):
                        continue
                    try:
                        os.remove(
                            path,
                        )
                    except OSError as error:
                        logger.warning(
                            "Failed to evict image %s: %s",
                            path,
                            error,
                        )
                    else:
                        total_size -= size
                        logger.info(
                            "LRU evicted image: %s",
                            path,
                        )
