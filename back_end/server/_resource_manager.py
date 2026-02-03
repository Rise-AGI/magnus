# back_end/server/_resource_manager.py
import os
import re
import time
import asyncio
import logging
import subprocess
from typing import Dict, List, Optional, Tuple
from pywheels.file_tools import guarantee_file_exist
from ._magnus_config import magnus_config


__all__ = ["resource_manager"]


logger = logging.getLogger(__name__)


magnus_root = magnus_config['server']['root']
magnus_container_cache_path = f"{magnus_root}/container_cache"
guarantee_file_exist(magnus_container_cache_path, is_directory=True)


def _parse_size_string(size_str: str) -> int:
    """解析大小字符串，如 '200G', '1024M'，返回字节数"""
    size_str = size_str.strip().upper()
    units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * multiplier)
    return int(size_str)


CONTAINER_CACHE_SIZE = _parse_size_string(magnus_config['server']['resource_cache']['container_cache_size'])


def _image_to_sif_filename(image: str) -> str:
    """docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime -> pytorch_pytorch_2.5.1-cuda12.4-cudnn9-runtime.sif"""
    name = re.sub(r'^[a-z]+://', '', image)
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return f"{name}.sif"


class ResourceManager:
    """
    中心化管理镜像和仓库，由 magnus 系统用户执行。
    - 镜像：拉取到公共缓存，chmod 644，LRU 清理
    - 仓库：clone 到任务工作目录，setfacl 授权
    """

    def __init__(self):
        self.image_locks: Dict[str, asyncio.Lock] = {}

    def get_sif_path(self, image: str) -> str:
        return os.path.join(magnus_container_cache_path, _image_to_sif_filename(image))

    def _get_cached_images(self) -> List[Tuple[str, int, float]]:
        """获取缓存的镜像列表，返回 [(path, size_bytes, atime), ...]"""
        images = []
        for filename in os.listdir(magnus_container_cache_path):
            if not filename.endswith('.sif'):
                continue
            path = os.path.join(magnus_container_cache_path, filename)
            try:
                stat = os.stat(path)
                images.append((path, stat.st_size, stat.st_atime))
            except OSError:
                continue
        return images

    def _evict_lru_images(self):
        """LRU 清理：按访问时间淘汰旧镜像"""
        images = self._get_cached_images()
        if not images:
            return

        # 按访问时间排序（最近访问的在后面）
        images.sort(key=lambda x: x[2])

        total_size = sum(img[1] for img in images)

        # 淘汰直到满足大小限制
        while images and total_size > CONTAINER_CACHE_SIZE:
            path, size, _ = images.pop(0)  # 移除最久未访问的
            try:
                os.remove(path)
                logger.info(f"LRU evicted image: {path}")
                total_size -= size
            except OSError as e:
                logger.warning(f"Failed to evict image {path}: {e}")

    async def ensure_image(self, image: str) -> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        """
        sif_path = self.get_sif_path(image)

        if os.path.exists(sif_path):
            # 更新访问时间
            try:
                os.utime(sif_path, None)
            except OSError:
                pass
            return True, None

        if image not in self.image_locks:
            self.image_locks[image] = asyncio.Lock()

        async with self.image_locks[image]:
            if os.path.exists(sif_path):
                try:
                    os.utime(sif_path, None)
                except OSError:
                    pass
                return True, None

            # 拉取前先清理空间
            self._evict_lru_images()

            logger.info(f"Pulling container image: {image}")
            start_time = time.time()

            proc = await asyncio.create_subprocess_exec(
                "apptainer", "pull", sif_path, image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode().strip()
                logger.error(f"Failed to pull image {image}: {error_msg}")
                if os.path.exists(sif_path):
                    try:
                        os.remove(sif_path)
                    except OSError:
                        pass
                return False, error_msg

            elapsed = time.time() - start_time
            # 设置权限：所有用户可读
            os.chmod(sif_path, 0o644)
            logger.info(f"Image ready: {sif_path} ({elapsed:.1f}s)")
            return True, None

    async def ensure_repo(
        self,
        namespace: str,
        repo_name: str,
        branch: str,
        commit_sha: str,
        target_dir: str,
        runner: str,
        job_working_dir: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Clone 仓库到任务工作目录。返回 (success, error_msg)
        每个任务独立 clone，任务结束后由 _clean_up_working_table 清理。
        使用 magnus 系统用户的默认 SSH 配置。
        """
        if os.path.exists(target_dir):
            return True, None

        repo_url = f"git@github.com:{namespace}/{repo_name}.git"

        logger.info(f"Cloning repo: {repo_url} -> {target_dir}")
        start_time = time.time()

        # git clone
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--branch", branch, "--single-branch", repo_url, target_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Failed to clone repo {repo_url}: {error_msg}")
            return False, f"git clone failed: {error_msg}"

        # git checkout
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", commit_sha,
            cwd=target_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Failed to checkout {commit_sha}: {error_msg}")
            return False, f"git checkout failed: {error_msg}"

        # 设置 ACL：runner 用户可读写整个工作目录（包括 success marker 等）
        default_runner = magnus_config["cluster"]["default_runner"]
        try:
            subprocess.run([
                "setfacl", "-R",
                "-m", f"u:{runner}:rwx",
                "-d", "-m", f"u:{default_runner}:rwx",
                "-d", "-m", f"u:{runner}:rwx",
                job_working_dir,
            ], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.warning(f"setfacl failed: {e.stderr.decode()}")

        elapsed = time.time() - start_time
        logger.info(f"Repo ready: {target_dir} ({elapsed:.1f}s)")
        return True, None


resource_manager = ResourceManager()
