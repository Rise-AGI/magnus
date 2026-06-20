# back_end/server/_scheduler/_staging.py
"""跨界 workspace I/O：transport=ssh 时把 job 工作区在本机与远程站点间搬运。

magnus 在本机（如 liu），SLURM 在异机（如 wm2），两边无共享盘 —— job 的工作区
（wrapper.py / repo / SIF / overlay）必须落远端 remote_root，而 wrapper 写出的
marker（.magnus_success / .magnus_result / .magnus_action / .magnus_oom）、SLURM
stdout、metrics 要回读到本机给 scheduler / 前端端点。本 mixin 收口这条搬运链路。

本机 transport（liu/zhu/gu 本地、Docker local）下 is_remote=False，下面每个方法都
立即返回 no-op，且 _scheduler/__init__ 里的远端路径已逐字收敛回本地路径，于是整条
执行链对本机站点字节级不变、零搬运参与。

搬运一律按**文件粒度**（marker 逐个、output.txt 单文件、metrics 先列目录再逐个），
不搬目录树 —— scp -r 把源目录落进既存目标目录时是否嵌套有跨版本歧义，逐文件杜绝
该歧义。SIF / repo 等大件如何落远端是来源相关的另一回事（见后续 commit），不在此。
"""
from typing import TYPE_CHECKING
from . import (
    logger,
    magnus_workspace_path,
    magnus_remote_workspace_path,
)

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _StagingMixinBase = _SchedulerProtocol
else:
    _StagingMixinBase = object


# wrapper 在 compute node 上写进 job 工作区、host 侧 _sync / _job_lifecycle 要回读的
# marker 文件。远端执行时它们生成在远端，须在 host 侧读取前拉回。
_REMOTE_MARKERS = (
    ".magnus_success",
    ".magnus_result",
    ".magnus_action",
    ".magnus_oom",
)


class _StagingMixin(_StagingMixinBase):
    """远端执行下的工作区搬运。本机执行下全程 no-op。"""

    def _is_remote_execution(self) -> bool:
        """SLURM 后端且 transport 异机时为 True。Docker(local) 下 slurm_manager 为
        None，短路成 False。"""
        return (
            self.slurm_manager is not None
            and self.slurm_manager.transport.is_remote
        )

    def _remote_job_working_table(self, job_id: str) -> str:
        return f"{magnus_remote_workspace_path}/jobs/{job_id}"

    def _local_job_working_table(self, job_id: str) -> str:
        return f"{magnus_workspace_path}/jobs/{job_id}"

    def _stage_in_job(self, job_id: str, wrapper_local_path: str) -> None:
        """在远端建好 job 工作区骨架并推送本机生成的 wrapper.py。

        _submit 写完本机 wrapper 后调用。远端 slurm/ 目录承接 sbatch --output、
        metrics/ 目录承接 sidecar 写入（wrapper 自身不建 work_dir，假定已存在）。
        本机执行下 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        transport = self.slurm_manager.transport
        remote_table = self._remote_job_working_table(job_id)
        for subdir in ("slurm", "metrics"):
            made = transport.run(["mkdir", "-p", f"{remote_table}/{subdir}"])
            if made.returncode != 0:
                raise RuntimeError(
                    f"failed to create remote job dir {remote_table}/{subdir} "
                    f"(rc={made.returncode}): {made.stderr.strip()}"
                )
        transport.push(wrapper_local_path, f"{remote_table}/wrapper.py")

    def _stage_out_logs(self, job_id: str) -> None:
        """把远端 SLURM stdout 与 metrics 拉回本机（live 镜像）。RUNNING 期间每 tick
        best-effort 调用，让前端日志 / metrics 端点读到的本机副本保持新鲜。

        逐 tick 全量重拷（scp 无增量）—— 远端为租户站、并发有限，正确优先；增量 /
        节流 / 按需拉取留作后续优化。本机执行下 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        transport = self.slurm_manager.transport
        remote_table = self._remote_job_working_table(job_id)
        local_table = self._local_job_working_table(job_id)

        # SLURM stdout：单文件，与 Docker 模式共用 slurm/output.txt 落点。
        try:
            transport.fetch(
                f"{remote_table}/slurm/output.txt",
                f"{local_table}/slurm/output.txt",
            )
        except RuntimeError:
            # 任务尚未产出 / 文件未生成：本 tick 跳过，下个 tick 再拉。
            pass

        # metrics：文件名可变（sidecar 的 system.jsonl + SDK 写的用户指标文件），
        # 先列远端目录再逐个拉，避免目录树搬运歧义。
        listing = transport.run(["ls", "-1", f"{remote_table}/metrics"])
        if listing.returncode == 0:
            # 按行切（ls -1 一行一名），不按空白切 —— metrics 文件名可能含空格。
            for name in listing.stdout.splitlines():
                if not name:
                    continue
                try:
                    transport.fetch(
                        f"{remote_table}/metrics/{name}",
                        f"{local_table}/metrics/{name}",
                    )
                except RuntimeError:
                    pass

    def _stage_out_final(self, job_id: str) -> None:
        """job 终态时的最后一次回读：marker + 日志 + metrics 全部拉齐，供 _sync 的
        finalize / OOM 判定与前端读取。本机执行下 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        transport = self.slurm_manager.transport
        remote_table = self._remote_job_working_table(job_id)
        local_table = self._local_job_working_table(job_id)
        for marker in _REMOTE_MARKERS:
            try:
                transport.fetch(
                    f"{remote_table}/{marker}",
                    f"{local_table}/{marker}",
                )
            except RuntimeError:
                # marker 是可选的（成功才有 .magnus_success，OOM 才有 .magnus_oom）：
                # 缺失即"没发生"，与本机模式下文件不存在语义一致。
                pass
        # 末次日志 / metrics flush：补齐任务退出前最后一段、上一 tick 之后的输出。
        self._stage_out_logs(job_id)

    def _cleanup_remote_job(self, job_id: str) -> None:
        """删除远端 job 工作区（marker / 日志 / metrics 已在 _stage_out_final 拉回本机，
        本机侧 keep-whitelist 保留持久产物）。本机执行下 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        transport = self.slurm_manager.transport
        remote_table = self._remote_job_working_table(job_id)
        removed = transport.run(["rm", "-rf", remote_table])
        if removed.returncode != 0:
            logger.warning(
                f"Failed to clean up remote working table {remote_table} "
                f"(rc={removed.returncode}): {removed.stderr.strip()}"
            )
