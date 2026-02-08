# back_end/server/_resource_manager.py
import os
import re
import time
import shutil
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
magnus_repo_cache_path = f"{magnus_root}/repo_cache"
guarantee_file_exist(magnus_container_cache_path, is_directory=True)
guarantee_file_exist(magnus_repo_cache_path, is_directory=True)


def _parse_size_string(size_str: str) -> int:
    """解析大小字符串，如 '200G', '1024M'，返回字节数"""
    size_str = size_str.strip().upper()
    units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * multiplier)
    return int(size_str)


def _get_dir_size(path: str) -> int:
    """递归计算目录大小"""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


CONTAINER_CACHE_SIZE = _parse_size_string(magnus_config['server']['resource_cache']['container_cache_size'])
REPO_CACHE_SIZE = _parse_size_string(magnus_config['server']['resource_cache']['repo_cache_size'])


def _image_to_sif_filename(image: str) -> str:
    """docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime -> pytorch_pytorch_2.5.1-cuda12.4-cudnn9-runtime.sif"""
    name = re.sub(r'^[a-z]+://', '', image)
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return f"{name}.sif"


def _repo_to_cache_dirname(namespace: str, repo_name: str, branch: str) -> str:
    """namespace/repo_name/branch -> namespace_repo_name_branch"""
    name = f"{namespace}_{repo_name}_{branch}"
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return name


class ResourceManager:
    """
    中心化管理镜像和仓库，由 magnus 系统用户执行。
    - 镜像：拉取到公共缓存，chmod 644，LRU 清理
    - 仓库：clone 到缓存，复制到工作目录，setfacl 授权，LRU 清理
    """

    def __init__(self):
        self.image_locks: Dict[str, asyncio.Lock] = {}
        self.repo_locks: Dict[str, asyncio.Lock] = {}

    def get_sif_path(self, image: str) -> str:
        return os.path.join(magnus_container_cache_path, _image_to_sif_filename(image))

    def _get_repo_cache_path(self, namespace: str, repo_name: str, branch: str) -> str:
        return os.path.join(magnus_repo_cache_path, _repo_to_cache_dirname(namespace, repo_name, branch))

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

    def _get_cached_repos(self) -> List[Tuple[str, int, float]]:
        """获取缓存的仓库列表，返回 [(path, size_bytes, atime), ...]"""
        repos = []
        for dirname in os.listdir(magnus_repo_cache_path):
            path = os.path.join(magnus_repo_cache_path, dirname)
            if not os.path.isdir(path):
                continue
            try:
                stat = os.stat(path)
                size = _get_dir_size(path)
                repos.append((path, size, stat.st_atime))
            except OSError:
                continue
        return repos

    def _evict_lru_images(self):
        """LRU 清理：按访问时间淘汰旧镜像"""
        images = self._get_cached_images()
        if not images:
            return

        images.sort(key=lambda x: x[2])
        total_size = sum(img[1] for img in images)

        while images and total_size > CONTAINER_CACHE_SIZE:
            path, size, _ = images.pop(0)
            try:
                os.remove(path)
                logger.info(f"LRU evicted image: {path}")
                total_size -= size
            except OSError as e:
                logger.warning(f"Failed to evict image {path}: {e}")

    def _evict_lru_repos(self):
        """LRU 清理：按访问时间淘汰旧仓库"""
        repos = self._get_cached_repos()
        if not repos:
            return

        repos.sort(key=lambda x: x[2])
        total_size = sum(repo[1] for repo in repos)

        while repos and total_size > REPO_CACHE_SIZE:
            path, size, _ = repos.pop(0)
            try:
                shutil.rmtree(path)
                logger.info(f"LRU evicted repo: {path}")
                total_size -= size
            except OSError as e:
                logger.warning(f"Failed to evict repo {path}: {e}")

    async def ensure_image(self, image: str) -> Tuple[bool, Optional[str]]:
        """
        确保镜像可用。返回 (success, error_msg)
        - 成功：(True, None)
        - 失败：(False, "error message")
        """
        sif_path = self.get_sif_path(image)

        if os.path.exists(sif_path):
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
        确保仓库可用。返回 (success, error_msg)
        1. 检查/创建 cache（带锁防止重复 clone）
        2. 复制 cache 到工作目录
        3. fetch + checkout 到指定 commit
        4. 设置 ACL
        """
        if os.path.exists(target_dir):
            return True, None

        repo_url = f"git@github.com:{namespace}/{repo_name}.git"
        cache_path = self._get_repo_cache_path(namespace, repo_name, branch)
        cache_key = f"{namespace}/{repo_name}/{branch}"

        # Phase 1: 确保 cache 存在（带锁）
        if cache_key not in self.repo_locks:
            self.repo_locks[cache_key] = asyncio.Lock()

        async with self.repo_locks[cache_key]:
            if not os.path.exists(cache_path):
                self._evict_lru_repos()

                logger.info(f"Cloning repo to cache: {repo_url} -> {cache_path}")
                start_time = time.time()

                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--branch", branch, "--single-branch", repo_url, cache_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()

                if proc.returncode != 0:
                    error_msg = stderr.decode().strip()
                    logger.error(f"Failed to clone repo {repo_url}: {error_msg}")
                    if os.path.exists(cache_path):
                        shutil.rmtree(cache_path, ignore_errors=True)
                    return False, f"git clone failed: {error_msg}"

                elapsed = time.time() - start_time
                logger.info(f"Repo cached: {cache_path} ({elapsed:.1f}s)")

            # 更新 cache 访问时间
            try:
                os.utime(cache_path, None)
            except OSError:
                pass

        # Phase 2: 复制 cache 到工作目录
        start_time = time.time()
        try:
            shutil.copytree(cache_path, target_dir)
        except Exception as e:
            logger.error(f"Failed to copy repo cache: {e}")
            return False, f"copy cache failed: {e}"

        # Phase 3: fetch + checkout 到指定 commit
        proc = await asyncio.create_subprocess_exec(
            "git", "fetch", "origin",
            cwd=target_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"git fetch failed (may be ok): {stderr.decode().strip()}")

        # HEAD 是符号引用，git checkout HEAD 只会 checkout 本地（陈旧的）HEAD；
        # 需要 checkout origin/<branch> 才能拿到 fetch 后的最新提交
        effective_sha = f"origin/{branch}" if commit_sha == "HEAD" else commit_sha
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", effective_sha,
            cwd=target_dir,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Failed to checkout {commit_sha}: {error_msg}")
            shutil.rmtree(target_dir, ignore_errors=True)
            return False, f"git checkout failed: {error_msg}"

        # Phase 4: 设置 ACL
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
