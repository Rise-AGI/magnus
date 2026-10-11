# back_end/server/_resource_manager/_cache.py
"""LRU 缓存清理 + 启动时孤儿清理。"""
import asyncio
import os
import shutil
from typing import TYPE_CHECKING, List, Tuple

from library import disk_free_bytes

from .._magnus_config import is_local_mode
from . import logger, magnus_container_cache_path, magnus_repo_cache_path, magnus_apptainer_cache_path
from ._helpers import _get_dir_size, _remove_tree, _run_blocking_io
from ._config import CONTAINER_CACHE_SIZE, REPO_CACHE_SIZE


if TYPE_CHECKING:
    from ._typing import _ResourceManagerProtocol
    _CacheMixinBase = _ResourceManagerProtocol
else:
    _CacheMixinBase = object


class _CacheMixin(_CacheMixinBase):

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

    def _evict_lru_images(self, target_free_bytes: int = 0):
        """LRU 清理：按访问时间淘汰旧镜像。

        淘汰直到镜像缓存回到 size 配额以内；当 target_free_bytes > 0 时，额外淘汰
        直到 cache 卷的真实剩余空间不低于该目标——这样磁盘被缓存以外的数据填满时，
        pull 也能靠淘汰旧镜像腾出空间，而不是只在缓存自身超配额时才淘汰。

        注：此方法在 self.image_locks[image] 内调用，不会与同一 URI 的拉取并发；
        不同 URI 的并发淘汰理论上存在 TOCTOU，但 ensure_image 入口处的 os.utime
        会刷新 atime，使正在使用的镜像不会被误淘汰，无需额外加锁。"""
        images = self._get_cached_images()
        if not images:
            return

        images.sort(key=lambda x: x[2])
        total_size = sum(img[1] for img in images)

        def _needs_eviction() -> bool:
            if total_size > CONTAINER_CACHE_SIZE:
                return True
            if target_free_bytes > 0 and disk_free_bytes(magnus_container_cache_path) < target_free_bytes:
                return True
            return False

        while images and _needs_eviction():
            path, size, _ = images.pop(0)
            try:
                os.remove(path)
                logger.info(f"LRU evicted image: {path}")
                total_size -= size
            except OSError as error:
                logger.warning(f"Failed to evict image {path}: {error}")

    async def _evict_lru_repos(
        self,
    ) -> None:
        """淘汰空闲仓库，扫描和删除离开 event loop，删除期间持有仓库锁。

        调用方已经持有自己的 repo_lock，不能等待其它仓库的锁，否则两个 clone
        可互相等待。只尝试立即获取候选锁；锁忙或有已唤醒的等待者时都跳过。
        """
        repos = await _run_blocking_io(
            self._get_cached_repos,
        )
        if not repos:
            return

        repos.sort(key=lambda x: x[2])
        total_size = sum(repo[1] for repo in repos)

        while repos and total_size > REPO_CACHE_SIZE:
            path, size, _ = repos.pop(0)
            lock = self.repo_locks.setdefault(path, asyncio.Lock())
            if lock.locked():
                continue
            try:
                # An unlocked asyncio.Lock can still have a woken waiter. A
                # zero deadline allows the immediate acquire path but cancels
                # any suspended acquire instead of waiting while owning a repo.
                async with asyncio.timeout(0):
                    await lock.acquire()
            except TimeoutError:
                continue
            try:
                # Cancellation must join deletion before releasing the lock;
                # otherwise a new clone can race the still-running worker.
                await _run_blocking_io(
                    _remove_tree,
                    path,
                )
                if os.path.exists(path):
                    logger.warning(f"Failed to evict repo {path}: it could not be removed completely")
                    continue
                logger.info(f"LRU evicted repo: {path}")
                total_size -= size
            finally:
                lock.release()

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
