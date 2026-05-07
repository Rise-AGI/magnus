# back_end/server/_scheduler/__init__.py
"""Magnus job scheduler.

主类 MagnusScheduler 由若干 mixin 组装，每个 mixin 一个文件：

- _core.py:             MagnusScheduler 主类（组装所有 mixin、tick、terminate_job）
- _decisions.py:        队头挂号 + 抢占决策
- _sync.py:             SLURM/Docker 真实状态同步到 DB + 集群快照
- _submit.py:           PENDING job 提交到 SLURM/Docker
- _resources.py:        镜像拉取 + 仓库 clone（Preparing → Pending）
- _job_lifecycle.py:    success/OOM marker、working table 清理
- _wrapper_template.py: SLURM compute node 上 wrapper.py 源码生成器

公共面：单例 `scheduler`、`scheduler.tick()`、`scheduler.terminate_job()`。
"""
import logging
from pywheels.file_tools import guarantee_file_exist
from .._magnus_config import magnus_config

logger = logging.getLogger(__name__)

# 模块级路径常量 + import-time 副作用：必须在子模块 import 之前完成。
magnus_root = magnus_config['server']['root']
magnus_workspace_path = f"{magnus_root}/workspace"
magnus_container_cache_path = f"{magnus_root}/container_cache"
magnus_uv_cache_path = f"{magnus_root}/uv_cache"
guarantee_file_exist(magnus_workspace_path, is_directory=True)
guarantee_file_exist(magnus_container_cache_path, is_directory=True)
guarantee_file_exist(magnus_uv_cache_path, is_directory=True)

# 子模块通过 `from . import logger, magnus_workspace_path, ...` 拿到上面定义的属性，
# 因此这里的 import 必须放在常量定义之后。
from ._core import MagnusScheduler

__all__ = [
    "scheduler",
]

scheduler = MagnusScheduler()
