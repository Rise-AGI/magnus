# back_end/server/_resource_manager/_cache.py
"""LRU 缓存清理 + 启动时孤儿清理。"""
import os
import shutil
from typing import List, Tuple

from .._magnus_config import is_local_mode
from . import logger, magnus_container_cache_path, magnus_repo_cache_path, magnus_apptainer_cache_path
from ._helpers import _get_dir_size
from ._config import CONTAINER_CACHE_SIZE, REPO_CACHE_SIZE


class _CacheMixin:

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
        """LRU 清理：按访问时间淘汰旧镜像。
        注：此方法在 self.image_locks[image] 内调用，不会与同一 URI 的拉取并发；
        不同 URI 的并发淘汰理论上存在 TOCTOU，但 ensure_image 入口处的 os.utime
        会刷新 atime，使正在使用的镜像不会被误淘汰，无需额外加锁。"""
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
            except OSError as error:
                logger.warning(f"Failed to evict image {path}: {error}")

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
            except OSError as error:
                logger.warning(f"Failed to evict repo {path}: {error}")

    def recover_stale_pull_caches(self) -> None:
        """启动时调用：清理上次进程异常退出残留的 per-pull tempdir。
        正常路径在 _ensure_image_apptainer 的 finally 里就 rmtree 了；
        这里只兜底 SIGKILL / OOM 这种来不及走 finally 的情况。"""
        if is_local_mode or not os.path.isdir(magnus_apptainer_cache_path):
            return
        for name in os.listdir(magnus_apptainer_cache_path):
            if not name.startswith("pull-"):
                continue
            path = os.path.join(magnus_apptainer_cache_path, name)
            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"Cleaned up stale pull cache: {name}")
