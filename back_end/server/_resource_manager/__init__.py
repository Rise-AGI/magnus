# back_end/server/_resource_manager/__init__.py
"""Centralized image / repo cache: pull, clone, LRU 淘汰、ACL。

主类 ResourceManager 由若干 mixin 组装：

- _helpers.py:    零依赖纯函数（目录大小、镜像/仓库名规范化）
- _config.py:     全局常量与全局只读状态（mirror 改写、cache 上限、git env、TTL、SHA pattern）
- _cache.py:      LRU cache 清理 + 启动时孤儿清理
- _images.py:     镜像拉取（apptainer pull on HPC / docker pull on local）
- _repos.py:      仓库 clone/fetch/checkout + ensure_repo 主流程

公共面：单例 `resource_manager`、`_image_to_sif_filename`。
"""
import logging
from pywheels.file_tools import guarantee_file_exist
from .._magnus_config import magnus_config

logger = logging.getLogger(__name__)

# 模块级路径常量 + import-time 副作用：必须在子模块（mixin）import 之前完成。
magnus_root = magnus_config['server']['root']
magnus_container_cache_path = f"{magnus_root}/container_cache"
magnus_repo_cache_path = f"{magnus_root}/repo_cache"
magnus_apptainer_cache_path = f"{magnus_root}/apptainer_cache"
guarantee_file_exist(magnus_container_cache_path, is_directory=True)
guarantee_file_exist(magnus_repo_cache_path, is_directory=True)
guarantee_file_exist(magnus_apptainer_cache_path, is_directory=True)

from ._helpers import _image_to_sif_filename
from ._manager import ResourceManager

__all__ = ["resource_manager"]

resource_manager = ResourceManager()
