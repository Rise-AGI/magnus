# back_end/server/_scheduler/__init__.py
"""Magnus job scheduler.

主类 MagnusScheduler 由若干 mixin 组装，每个 mixin 一个文件：

- _core.py:             MagnusScheduler 主类（组装所有 mixin、tick、terminate_job）
- _decisions.py:        EASY backfill + 抢占决策
- _sync.py:             SLURM/Docker 真实状态同步到 DB + 集群快照
- _submit.py:           PENDING job 提交到 SLURM/Docker
- _resources.py:        镜像拉取 + 仓库 clone（Preparing → Pending）
- _job_lifecycle.py:    success/OOM marker、working table 清理
- _staging.py:          远端执行（transport=ssh）下 job 工作区的跨界搬运（本机执行 no-op）
- _wrapper_template.py: SLURM compute node 上 wrapper.py 源码生成器

公共面：单例 `scheduler`、`scheduler.tick()`、`scheduler.terminate_job()`、`scheduler.signal_job()`。
"""
import logging
from pywheels.file_tools import guarantee_file_exist
from .._magnus_config import magnus_config

logger = logging.getLogger(__name__)

# 模块级路径常量 + import-time 副作用：必须在子模块 import 之前完成。
magnus_root = magnus_config['server']['root']
# ephemeral_root 缺省等于 root（_magnus_config 已 setdefault）。配成独立快盘时，
# 只有 ephemeral overlay + apptainer tmp/cache 落到这里，持久数据仍在 root。
magnus_ephemeral_root = magnus_config['server']['ephemeral_root']
magnus_workspace_path = f"{magnus_root}/workspace"
magnus_ephemeral_workspace_path = f"{magnus_ephemeral_root}/workspace"
magnus_container_cache_path = f"{magnus_root}/container_cache"
magnus_uv_cache_path = f"{magnus_root}/uv_cache"
guarantee_file_exist(magnus_workspace_path, is_directory=True)
guarantee_file_exist(magnus_ephemeral_workspace_path, is_directory=True)
guarantee_file_exist(magnus_container_cache_path, is_directory=True)
guarantee_file_exist(magnus_uv_cache_path, is_directory=True)

# 远端执行（transport=ssh）下，job 的工作区物理上落在远程站点（wm2 Lustre），由
# remote_root 寻址；wrapper / repo / SIF 推过去，marker / SLURM stdout / metrics 拉
# 回来（搬运逻辑见 _staging.py）。默认的本机 transport 下，下面的远端路径**逐字**
# 收敛回本地 workspace 路径，于是 _submit / _sync / _job_lifecycle 的所有消费点字节
# 级不变，本机站点（liu/zhu/gu 本地、Docker local）零参与搬运。
# 远端路径仅作字符串烘进 wrapper / 传给 sbatch / 作搬运端点，故这里**不**对其
# guarantee_file_exist（那是远端目录，由 _staging 经 transport 远程 mkdir 建）。
_transport_config = magnus_config["transport"]
if _transport_config["mode"] == "ssh":
    _remote_root = _transport_config["ssh"]["remote_root"]
    # 远端站点单一共享盘：working 与 ephemeral 同根（job_working_table 与
    # job_ephemeral_table 重合，wrapper 的 makedirs 幂等、cleanup 里
    # `job_ephemeral_table != job_working_table` 判定为假、跳过那步删除）。
    magnus_remote_workspace_path = f"{_remote_root}/workspace"
    magnus_remote_ephemeral_workspace_path = f"{_remote_root}/workspace"
    magnus_remote_container_cache_path = f"{_remote_root}/container_cache"
else:
    magnus_remote_workspace_path = magnus_workspace_path
    magnus_remote_ephemeral_workspace_path = magnus_ephemeral_workspace_path
    magnus_remote_container_cache_path = magnus_container_cache_path

# 子模块通过 `from . import logger, magnus_workspace_path, ...` 拿到上面定义的属性，
# 因此这里的 import 必须放在常量定义之后。
from ._core import MagnusScheduler

__all__ = [
    "scheduler",
]

scheduler = MagnusScheduler()
