# back_end/server/_resource_manager/_typing.py
"""Type-only base shared by all resource-manager mixins.

`ResourceManager` 由 _CacheMixin + _ImagesMixin + _ReposMixin 组装（见 _manager.py）。
每个 mixin 都会通过 `self.X` 访问"其他 mixin 提供的方法"或"主类 __init__ 注入的
属性"，但 mixin 自身的类不声明这些 cross-class 引用，pyright 会报 attribute-unknown
之类的 false positive。

这里集中声明所有 cross-mixin 引用的签名，让每个 mixin 在 TYPE_CHECKING 时把
`_ResourceManagerProtocol` 当父类，pyright 静态视角下能看到所有 attribute / method；
运行时此模块零参与，由 `ResourceManager` 通过实际 mixin 组合提供真实实现。
"""
from __future__ import annotations

import asyncio
from typing import Dict, Tuple


class _ResourceManagerProtocol:
    """Cross-mixin attribute / method declarations for static analysis.

    Real values are set by `ResourceManager.__init__` and by sibling mixin
    method bodies; this class is never instantiated.
    """

    # === ResourceManager.__init__ 注入的属性 ===
    image_locks: Dict[str, asyncio.Lock]
    repo_locks: Dict[str, asyncio.Lock]
    _default_branch_cache: Dict[str, Tuple[str, float]]
    _default_branch_locks: Dict[str, asyncio.Lock]

    # === _manager.py ===
    def get_sif_path(self, image: str) -> str: ...
    def _get_repo_cache_path(self, namespace: str, repo_name: str, branch: str) -> str: ...

    # === _cache.py ===
    def _evict_lru_images(self, target_free_bytes: int = 0) -> None: ...
    def _evict_lru_repos(self) -> None: ...
