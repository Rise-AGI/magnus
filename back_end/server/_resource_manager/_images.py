# back_end/server/_resource_manager/_images.py
"""镜像拉取：apptainer pull (HPC) + docker pull (local)。"""
import os
import re
import time
import shutil
import asyncio
import tempfile
from typing import Optional, Tuple

from .._magnus_config import is_local_mode
from . import logger, magnus_apptainer_cache_path
from ._config import _rewrite_image_for_mirror


class _ImagesMixin:

    async def ensure_image(self, image: str, force: bool = False) -> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        force=True 时跳过缓存检查，强制重新拉取。
        """
        if is_local_mode:
            return await self._ensure_image_docker(image, force)
        return await self._ensure_image_apptainer(image, force)

    async def _ensure_image_docker(self, image: str, force: bool = False) -> Tuple[bool, Optional[str]]:
        """Docker 模式：使用 docker pull，Docker 自身管理镜像缓存"""
        pull_image = _rewrite_image_for_mirror(image)
        docker_image = re.sub(r'^[a-z]+://', '', pull_image)

        if not force:
            # 检查本地是否已有（用重写后的名称，因为 Docker 按实际拉取名存储）
            proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", docker_image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return True, None

        if pull_image != image:
            logger.info(f"🐳 Pulling Docker image: {image} (via mirror: {docker_image})")
        else:
            logger.info(f"🐳 Pulling Docker image: {docker_image}")
        start_time = time.time()

        proc = await asyncio.create_subprocess_exec(
            "docker", "pull", docker_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await proc.communicate()
        except asyncio.CancelledError:
            proc.terminate()
            await proc.wait()
            raise

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"❌ Docker pull failed for {docker_image}: {error_msg}")
            return False, error_msg

        elapsed = time.time() - start_time
        logger.info(f"✅ Docker image ready: {docker_image} ({elapsed:.1f}s)")
        return True, None

    async def _ensure_image_apptainer(self, image: str, force: bool = False) -> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        force=True 时跳过缓存检查，强制重新拉取。
        所有拉取都写入 .tmp 再原子 rename，断电不会留下半成品 .sif。
        """
        sif_path = self.get_sif_path(image)

        if not force and os.path.exists(sif_path):
            try:
                os.utime(sif_path, None)
            except OSError:
                pass
            return True, None

        if image not in self.image_locks:
            self.image_locks[image] = asyncio.Lock()

        async with self.image_locks[image]:
            if not force and os.path.exists(sif_path):
                try:
                    os.utime(sif_path, None)
                except OSError:
                    pass
                return True, None

            self._evict_lru_images()

            # 始终写入临时文件，成功后原子 rename
            # 这样断电/OOM 只会留下 .tmp，重启时统一清理，正式 .sif 不会被污染
            pull_dest = sif_path + ".tmp"

            pull_image = _rewrite_image_for_mirror(image)
            if pull_image != image:
                logger.info(f"Pulling container image: {image} (via mirror: {pull_image})")
            else:
                logger.info(f"Pulling container image: {image}")
            start_time = time.time()

            # 非瞬态错误（镜像不存在、鉴权失败等）直接失败，不浪费时间重试
            non_transient_patterns = ["unauthorized", "not found", "manifest unknown", "denied", "invalid reference"]
            max_retries = 3
            base_retry_delay = 10

            # apptainer pull 写入的 OCI scratch（blob、oci-tmp、oras）随次单调增长，
            # 实测达到 sif 库存的 2~4 倍后仍无 GC。每次 pull 用独立 tempdir 隔离，
            # 走完 finally 清理，磁盘占用自然有界。
            pull_cache_dir = tempfile.mkdtemp(prefix="pull-", dir=magnus_apptainer_cache_path)
            try:
                for attempt in range(max_retries):
                    env = os.environ.copy()
                    env["APPTAINER_CACHEDIR"] = pull_cache_dir
                    env["GODEBUG"] = "http2client=0"
                    proc = await asyncio.create_subprocess_exec(
                        "apptainer", "pull", pull_dest, pull_image,
                        stdout = asyncio.subprocess.PIPE,
                        stderr = asyncio.subprocess.PIPE,
                        env = env,
                    )
                    try:
                        _, stderr = await proc.communicate()
                    except asyncio.CancelledError:
                        # 优雅关闭：终止子进程，避免孤儿 apptainer 进程
                        proc.terminate()
                        await proc.wait()
                        if os.path.exists(pull_dest):
                            try:
                                os.remove(pull_dest)
                            except OSError:
                                pass
                        raise

                    if proc.returncode == 0:
                        break

                    # 清理残留的不完整 .tmp
                    if os.path.exists(pull_dest):
                        try:
                            os.remove(pull_dest)
                        except OSError:
                            pass

                    error_msg = stderr.decode().strip()
                    error_lower = error_msg.lower()

                    if any(p in error_lower for p in non_transient_patterns):
                        logger.error(f"Failed to pull image {image} (non-transient): {error_msg}")
                        return False, error_msg

                    if attempt < max_retries - 1:
                        retry_delay = base_retry_delay * (2 ** attempt)
                        logger.warning(f"Pull attempt {attempt + 1}/{max_retries} failed, retrying in {retry_delay}s: {error_msg}")
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.error(f"Failed to pull image {image} after {max_retries} attempts: {error_msg}")
                        return False, error_msg
            finally:
                shutil.rmtree(pull_cache_dir, ignore_errors=True)

            # 先 chmod 再 rename：rename 保留权限，若在两者之间断电，
            # .sif 会以 umask 默认权限（可能是 0600）落盘，其他 runner 无法读取
            os.chmod(pull_dest, 0o644)
            os.rename(pull_dest, sif_path)

            elapsed = time.time() - start_time
            logger.info(f"Image ready: {sif_path} ({elapsed:.1f}s)")
            return True, None
