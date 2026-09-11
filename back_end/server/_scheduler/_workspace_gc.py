# back_end/server/_scheduler/_workspace_gc.py
"""持久 job 工作区的容量上限滚动回收（低频后台 janitor）。

每个 job 收尾后（见 _job_lifecycle._clean_up_working_table）其工作区被裁到只剩
日志 / metrics / 结果标记，但这些保留产物会随 job 累积无限增长。本 janitor 把
`{root}/workspace/jobs` 里**终态** job 目录的总量维持在 `server.scheduler.
workspace_size_cap` 之内：超限时按 mtime 从最旧的终态目录起滚动淘汰（LRU），保住
近期历史、绝不触碰仍活跃的 job。与 per-job 收尾正交（那是每 job 一次的裁剪，这是
跨 job 的总量控制），故独立成 mixin。淘汰只删磁盘工作区,不动 DB 里的 job 记录。

计量口径 == 淘汰口径：只统计并回收**终态** job 目录，活跃 job 的工作树（repository /
用户 dump 进 workspace 的产物）既不计入总量、也不淘汰 —— 否则活跃体量会抬高总量、
把终态历史删得比预期激进，甚至活跃体量 >= cap 时每轮空删全部历史仍回不到 cap。活跃
job 的磁盘压力由写入链路的 fail-fast 兜（见 disk-space awareness），不由本 cap 兼任。
"""
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Set, Tuple
from pywheels.file_tools import delete_file
from ..database import SessionLocal
from ..models import Job, JobStatus
from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string
from . import logger, magnus_workspace_path

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _WorkspaceGCMixinBase = _SchedulerProtocol
else:
    _WorkspaceGCMixinBase = object


# 工作区还活跃、其目录绝不能被容量回收淘汰、也不计入总量的 job 状态：仍在准备 / 排队 /
# 运行，或被抢占等待重提交（PAUSED）。其余为终态（SUCCESS / FAILED / TERMINATED），可淘汰。
_ACTIVE_JOB_STATUSES = (
    JobStatus.PENDING,
    JobStatus.PREPARING,
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.PAUSED,
)

# 回收的低频节流间隔（秒）。总量核算要遍历终态目录做一次 du，成本随历史规模增长，故低频
# 跑（对偶 _record_snapshot 的 snapshot_interval 节流）。
_WORKSPACE_GC_INTERVAL_SECONDS = 3600


class _WorkspaceGCMixin(_WorkspaceGCMixinBase):
    """把持久 job 工作区（终态部分）总量维持在配置上限内的低频滚动回收。"""

    def _reclaim_workspace_over_cap(self) -> None:
        now = datetime.now(timezone.utc)
        if (now - self.last_workspace_gc_time).total_seconds() < _WORKSPACE_GC_INTERVAL_SECONDS:
            return
        # 先认领本轮时隙（无论后续成败），避免出错时每个 tick 反复重扫打爆磁盘。
        self.last_workspace_gc_time = now

        cap_config = magnus_config["server"]["scheduler"]["workspace_size_cap"]
        if not cap_config:
            return  # None / 空串 = 关闭上限，无限保留
        cap_bytes = _parse_size_string(cap_config)

        jobs_root = f"{magnus_workspace_path}/jobs"
        if not os.path.isdir(jobs_root):
            return

        # 先读活跃 job 集合，再据它把活跃目录从计量与淘汰里一并排除 —— 计量口径与淘汰口径
        # 严格一致。活跃态在查询后至多迁往终态（终态不可逆），这类目录本轮被跳过、下轮再收，
        # 绝不会淘汰到本轮未计量的目录，无 race。
        with SessionLocal() as db:
            active_job_ids = {
                row[0]
                for row in db.query(Job.id).filter(Job.status.in_(_ACTIVE_JOB_STATUSES)).all()
            }

        try:
            terminal_dirs = _measure_terminal_job_dirs(jobs_root, active_job_ids)
        except OSError as error:
            logger.warning(f"Workspace GC skipped: cannot scan {jobs_root}: {error}")
            return

        total_bytes = sum(size for _, size, _ in terminal_dirs)
        if total_bytes <= cap_bytes:
            return

        # 最旧优先：mtime 记录最后一次写入（job 完成 / 最后产物落盘），据此做历史的 LRU
        # 淘汰，删到终态总量回落到上限以下为止。
        terminal_dirs.sort(key=lambda entry: entry[0])
        reclaimed_bytes = 0
        removed_count = 0
        for _, size, path in terminal_dirs:
            if total_bytes - reclaimed_bytes <= cap_bytes:
                break
            # delete_file(pywheels) 自己吞掉所有异常、从不 raise，所以删完必须回看目录是否
            # 真没了再记账 —— 否则一次失败的 unlink 会被误记成已回收，可能让循环提前 break、
            # 把仍超上限的 workspace 当成已回落，并在日志里谎报成功。
            delete_file(path)
            if os.path.exists(path):
                logger.warning(f"Workspace GC: failed to remove {path}; still over cap")
                continue
            reclaimed_bytes += size
            removed_count += 1

        if removed_count:
            logger.info(
                f"Workspace GC: evicted {removed_count} old terminal job dir(s), "
                f"reclaimed ~{reclaimed_bytes // (1024 * 1024)} MB "
                f"(terminal total was {total_bytes // (1024 * 1024)} MB, cap {cap_config})"
            )


def _measure_terminal_job_dirs(
    jobs_root: str,
    active_job_ids: Set[str],
) -> List[Tuple[float, int, str]]:
    """遍历 workspace/jobs，返回每个**终态** job 目录的 (mtime, 字节数, 绝对路径)。

    活跃 job（id 在 active_job_ids 里）在 du 之前就跳过 —— 既让计量口径等于淘汰口径，也
    省掉对体量最大、还在高频变动的活跃工作树的 du。单个条目 stat / 度量失败（与并发 finalize
    的 TOCTOU、瞬时不可读等）只跳过该项，不中止整轮。符号链接目录不纳入（既不计量也不误删）。
    """
    measured: List[Tuple[float, int, str]] = []
    for entry in os.scandir(jobs_root):
        try:
            if entry.name in active_job_ids:
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            measured.append(
                (
                    entry.stat().st_mtime,
                    _directory_size_bytes(entry.path),
                    entry.path,
                )
            )
        except OSError:
            continue
    return measured


def _directory_size_bytes(path: str) -> int:
    total_bytes = 0
    for dir_path, _, file_names in os.walk(path):
        for file_name in file_names:
            try:
                total_bytes += os.lstat(os.path.join(dir_path, file_name)).st_size
            except OSError:
                continue
    return total_bytes
