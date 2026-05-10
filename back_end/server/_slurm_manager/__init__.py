# back_end/server/_slurm_manager/__init__.py
"""SLURM CLI 包装：scontrol/squeue/sbatch/scancel 调用统一入口。

主类 SlurmManager 由两个 mixin 组装：

- _resource_query.py:   只读查询（scontrol/squeue → 容量、占用、任务列表、单任务状态）
- _control.py:          任务提交 / 终止 / 信号转发（sbatch / scancel）

公共面：`SlurmManager`、`NodeSnapshot`。
"""
import logging

logger = logging.getLogger(__name__)

from ._manager import SlurmManager
from ._resource_query import NodeSnapshot

__all__ = [
    "SlurmManager",
    "NodeSnapshot",
]
