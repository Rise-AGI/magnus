# back_end/server/_resource_manager/_images.py
"""镜像拉取：apptainer pull (HPC) + docker pull (local)。"""

import asyncio
import os
import re
import shutil
import tempfile
import time
from typing import TYPE_CHECKING, Optional, Tuple

from library import disk_free_bytes, disk_full_message, is_disk_full_stderr

from .._magnus_config import is_local_mode
from . import logger, magnus_apptainer_cache_path, magnus_container_cache_path
from ._buildkit import ImageBuildError, run_command
from ._config import MIN_FREE_BYTES_FOR_PULL, _rewrite_image_for_mirror
from ._helpers import _run_blocking_io

if TYPE_CHECKING:
    from ._typing import _ResourceManagerProtocol

    _ImagesMixinBase = _ResourceManagerProtocol
else:
    _ImagesMixinBase = object


class _ImagesMixin(_ImagesMixinBase):
    def get_docker_image(
        self,
        image: str,
    ) -> str:
        return re.sub(
            r"^[a-z]+://",
            "",
            _rewrite_image_for_mirror(
                image,
            ),
        )

    async def ensure_image(
        self,
        image: str,
        force: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        force=True 时跳过缓存检查，强制重新拉取。
        """
        lock = self.image_locks.setdefault(
            image,
            asyncio.Lock(),
        )
        async with lock:
            if is_local_mode:
                return await self._ensure_image_docker(
                    image,
                    force,
                )
            # Manual pulls and refreshes also own the path until publication.
            with self.images.hold(
                image,
            ):
                return await self._ensure_image_apptainer(
                    image,
                    force,
                )

    async def _ensure_image_docker(
        self,
        image: str,
        force: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Docker 模式：使用 docker pull，Docker 自身管理镜像缓存"""
        docker_image = self.get_docker_image(
            image,
        )

        if not force:
            try:
                await run_command(
                    ["docker", "image", "inspect", docker_image],
                    on_line=logger.debug,
                    timeout=None,
                )
            except ImageBuildError:
                pass
            else:
                return True, None

        logger.info(
            f"🐳 Pulling Docker image: {docker_image}",
        )
        start_time = time.time()

        try:
            await run_command(
                ["docker", "pull", docker_image],
                on_line=logger.debug,
                timeout=None,
            )
        except ImageBuildError as error:
            logger.error(
                "Docker pull failed for %s: %s",
                docker_image,
                error,
            )
            return False, str(
                error,
            )

        elapsed = time.time() - start_time
        logger.info(
            f"✅ Docker image ready: {docker_image} ({elapsed:.1f}s)",
        )
        return True, None

    async def _ensure_image_apptainer(
        self,
        image: str,
        force: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        force=True 时跳过缓存检查，强制重新拉取。
        所有拉取都写入 .tmp 再原子 rename，断电不会留下半成品 .sif。
        """
        sif_path = self.get_sif_path(
            image,
        )

        if not force and os.path.exists(
            sif_path,
        ):
            try:
                os.utime(
                    sif_path,
                    None,
                )
            except OSError:
                pass
            return True, None

        # 先按真实剩余空间淘汰旧镜像腾地；若腾完仍低于下限，pull 必败，直接
        # fail-fast 给清晰提示，不浪费 retry/backoff。
        await _run_blocking_io(
            self.images.collect,
            MIN_FREE_BYTES_FOR_PULL,
        )
        if (
            disk_free_bytes(
                magnus_container_cache_path,
            )
            < MIN_FREE_BYTES_FOR_PULL
        ):
            message = disk_full_message(
                magnus_container_cache_path,
            )
            logger.error(
                f"Refusing to pull image {image}: {message}",
            )
            return False, message

        # 始终写入临时文件，成功后原子 rename
        # 这样断电/OOM 只会留下 .tmp，重启时统一清理，正式 .sif 不会被污染
        pull_dest = sif_path + ".tmp"

        pull_image = _rewrite_image_for_mirror(
            image,
        )
        if pull_image != image:
            logger.info(
                f"Pulling container image: {image} (via mirror: {pull_image})",
            )
        else:
            logger.info(
                f"Pulling container image: {image}",
            )
        start_time = time.time()

        # 非瞬态错误（镜像不存在、鉴权失败等）直接失败，不浪费时间重试
        non_transient_patterns = [
            "unauthorized",
            "not found",
            "manifest unknown",
            "denied",
            "invalid reference",
        ]
        max_retries = 3
        base_retry_delay = 10

        # apptainer pull 写入的 OCI scratch（blob、oci-tmp、oras）随次单调增长，
        # 实测达到 sif 库存的 2~4 倍后仍无 GC。每次 pull 用独立 tempdir 隔离，
        # 走完 finally 清理，磁盘占用自然有界。
        pull_cache_dir = tempfile.mkdtemp(
            prefix="pull-",
            dir=magnus_apptainer_cache_path,
        )
        try:
            for attempt in range(
                max_retries,
            ):
                env = os.environ.copy()
                env["APPTAINER_CACHEDIR"] = pull_cache_dir
                env["GODEBUG"] = "http2client=0"
                try:
                    await run_command(
                        ["apptainer", "pull", pull_dest, pull_image],
                        on_line=logger.debug,
                        environment=env,
                        timeout=None,
                    )
                except ImageBuildError as error:
                    error_msg = str(
                        error,
                    )
                else:
                    break

                # 清理残留的不完整 .tmp
                if os.path.exists(
                    pull_dest,
                ):
                    try:
                        os.remove(
                            pull_dest,
                        )
                    except OSError:
                        pass

                error_lower = error_msg.lower()

                # 磁盘满不是瞬态错误——磁盘不会在 backoff 期间自己变空，重试纯属
                # 浪费时间。直接 fail-fast 并给出可操作的剩余空间提示。
                if is_disk_full_stderr(
                    error_msg,
                ):
                    message = disk_full_message(
                        magnus_container_cache_path,
                    )
                    logger.error(
                        f"Failed to pull image {image} (disk full): {error_msg} | {message}",
                    )
                    return False, message

                if any(p in error_lower for p in non_transient_patterns):
                    logger.error(
                        f"Failed to pull image {image} (non-transient): {error_msg}",
                    )
                    return False, error_msg

                if attempt < max_retries - 1:
                    retry_delay = base_retry_delay * (2**attempt)
                    logger.warning(
                        f"Pull attempt {attempt + 1}/{max_retries} failed, retrying in {retry_delay}s: {error_msg}",
                    )
                    await asyncio.sleep(
                        retry_delay,
                    )
                else:
                    logger.error(
                        f"Failed to pull image {image} after {max_retries} attempts: {error_msg}",
                    )
                    return False, error_msg
            # Publication preserves reader permissions, including on refresh.
            os.chmod(
                pull_dest,
                0o644,
            )
            os.replace(
                pull_dest,
                sif_path,
            )
        finally:
            try:
                os.unlink(
                    pull_dest,
                )
            except FileNotFoundError:
                pass
            finally:
                await _run_blocking_io(
                    shutil.rmtree,
                    pull_cache_dir,
                    ignore_errors=True,
                )

        elapsed = time.time() - start_time
        logger.info(
            f"Image ready: {sif_path} ({elapsed:.1f}s)",
        )
        return True, None
