# back_end/server/_resource_manager/_manager.py
"""ResourceManager 主类。组装 _CacheMixin + _ImagesMixin + _ReposMixin。"""
import asyncio
import os
from typing import Dict, Tuple

from . import magnus_container_cache_path, magnus_repo_cache_path
from ._helpers import _image_to_sif_filename, _repo_to_cache_dirname
from ._cache import _CacheMixin
from ._images import _ImagesMixin
from ._repos import _ReposMixin


class ResourceManager(_CacheMixin, _ImagesMixin, _ReposMixin):
    """
    中心化管理镜像和仓库，由 magnus 系统用户执行。
    - 镜像：拉取到公共缓存，chmod 644，LRU 清理
    - 仓库：clone 到缓存，复制到工作目录，setfacl 授权，LRU 清理
    """

    def __init__(self):
        self.image_locks: Dict[str, asyncio.Lock] = {}
        self.repo_locks: Dict[str, asyncio.Lock] = {}
        # repo_url -> (default_branch, expires_at_epoch_seconds)
        self._default_branch_cache: Dict[str, Tuple[str, float]] = {}
        # repo_url -> asyncio.Lock, 用于串行化同 repo 的 ls-remote，
        # 让 fanout 里先到的解析结果被后到者复用
        self._default_branch_locks: Dict[str, asyncio.Lock] = {}

    def get_sif_path(self, image: str) -> str:
        return os.path.join(magnus_container_cache_path, _image_to_sif_filename(image))

    def _get_repo_cache_path(self, namespace: str, repo_name: str, branch: str) -> str:
        return os.path.join(magnus_repo_cache_path, _repo_to_cache_dirname(namespace, repo_name, branch))
