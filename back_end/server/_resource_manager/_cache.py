# back_end/server/_resource_manager/_cache.py
"""LRU 缓存清理 + 启动时孤儿清理。"""
import asyncio
import os
from typing import TYPE_CHECKING, List, Tuple

from .._magnus_config import is_local_mode
from . import (
    logger,
    magnus_apptainer_cache_path,
    magnus_container_cache_path,
    magnus_repo_cache_path,
)
from ._helpers import _get_dir_size, _remove_tree, _run_blocking_io
from ._config import REPO_CACHE_SIZE
from ._pull_scratch import recover_pull_scratch


if TYPE_CHECKING:
    from ._typing import _ResourceManagerProtocol
    _CacheMixinBase = _ResourceManagerProtocol
else:
    _CacheMixinBase = object


class _CacheMixin(_CacheMixinBase):

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

    def recover_stale_pull_caches(
        self,
    ) -> None:
        if is_local_mode:
            return
        recover_pull_scratch(
            cache_root=magnus_apptainer_cache_path,
            output_root=magnus_container_cache_path,
        )
