# back_end/server/_resource_manager/_manager.py
"""ResourceManager 主类。组装 _CacheMixin + _ImagesMixin + _ReposMixin。"""
import asyncio
import os
from typing import Dict, Tuple

from . import magnus_container_cache_path, magnus_repo_cache_path
from ._helpers import _repo_to_cache_dirname
from ._image_cache import ImageCache
from ._config import CONTAINER_CACHE_SIZE
from ._cache import _CacheMixin
from ._images import _ImagesMixin
from ._repos import _ReposMixin
from ._builds import ImageBuildManager


class ResourceManager(_CacheMixin, _ImagesMixin, _ReposMixin):
    """
    中心化管理镜像和仓库，由 magnus 系统用户执行。
    - 镜像：拉取到公共缓存，chmod 644，LRU 清理
    - 仓库：clone 到缓存，复制到工作目录，setfacl 授权，LRU 清理
    """

    def __init__(self):
        self.images = ImageCache(
            root=magnus_container_cache_path,
            size_limit=CONTAINER_CACHE_SIZE,
        )
        self.image_locks: Dict[str, asyncio.Lock] = {}
        # repo cache 目录 -> asyncio.Lock，一个目录一把锁
        self.repo_locks: Dict[str, asyncio.Lock] = {}
        # repo_url -> (default_branch, expires_at_epoch_seconds)
        self._default_branch_cache: Dict[str, Tuple[str, float]] = {}
        # repo_url -> asyncio.Lock, 用于串行化同 repo 的 ls-remote，
        # 让 fanout 里先到的解析结果被后到者复用
        self._default_branch_locks: Dict[str, asyncio.Lock] = {}
        self.builds = ImageBuildManager()

    def get_sif_path(self, image: str) -> str:
        return self.images.path(
            image,
        )

    def _get_repo_cache_path(self, namespace: str, repo_name: str, branch: str) -> str:
        return os.path.join(magnus_repo_cache_path, _repo_to_cache_dirname(namespace, repo_name, branch))
