# back_end/server/_scheduler/_staging.py
"""跨界 workspace I/O：transport=ssh 时把 job 工作区在本机与远程站点间搬运。

magnus 在本机，SLURM 在异机（远端站点），两边无共享盘 —— job 的工作区
（wrapper.py / repo / SIF / overlay）必须落远端 remote_root，而 wrapper 写出的
marker（.magnus_success / .magnus_result / .magnus_action / .magnus_oom）、SLURM
stdout、metrics 要回读到本机给 scheduler / 前端端点。本 mixin 收口这条搬运链路。

本机 transport（独占集群本地、Docker local）下 is_remote=False，下面每个方法都
立即返回 no-op，且 _scheduler/__init__ 里的远端路径已逐字收敛回本地路径，于是整条
执行链对本机站点字节级不变、零搬运参与。

搬运一律按**文件粒度**（marker 逐个、output.txt 单文件、metrics 先列目录再逐个），
不搬目录树 —— scp -r 把源目录落进既存目标目录时是否嵌套有跨版本歧义，逐文件杜绝
该歧义。SIF / repo 等大件如何落远端是来源相关的另一回事（见后续 commit），不在此。
"""
import os
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from .._magnus_config import magnus_config
from .._file_custody_manager import file_custody_manager
from . import (
    logger,
    magnus_workspace_path,
    magnus_remote_workspace_path,
    magnus_remote_container_cache_path,
)

# Directory (inside the job working tree) where the in-container SDK drops custody
# uploads on no-network remote execution, paired with the SDK's MAGNUS_CUSTODY_DROP_DIR.
# Each <token>/ holds the file (a .tar.gz for directories) plus a meta.json; the host
# pulls them back and registers each in custody under its token. Must match the path
# the wrapper exports into the container.
_CUSTODY_DROP_SUBDIR = ".magnus_custody_drop"

# The platform SDK source (this repo's sdks/python/src/magnus). Provisioned into each
# job's .magnus_sdk so the container runs the platform's current SDK instead of the one
# baked into the image. parents: [0]=_scheduler [1]=server [2]=back_end [3]=repo root.
_PLATFORM_SDK_SRC = Path(__file__).resolve().parents[3] / "sdks" / "python" / "src" / "magnus"
_PLATFORM_SDK_SUBDIR = ".magnus_sdk"
# Shim so the bash `magnus` command also resolves to the platform SDK (the python
# `import magnus` path is covered by PYTHONPATH, which the wrapper points at .magnus_sdk).
# Prefer python3 but fall back to python for images that ship only the latter.
_MAGNUS_CLI_SHIM = (
    "#!/bin/sh\n"
    "if command -v python3 >/dev/null 2>&1; then exec python3 -m magnus.cli.main \"$@\"; fi\n"
    "exec python -m magnus.cli.main \"$@\"\n"
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


def _is_safe_leaf(name: str) -> bool:
    """name 是否为安全的单层路径名。远端无网计算节点（信任边界外）经 `ls` 列出的
    文件 / 目录名，在 host 侧拼进 fetch 落点前必须过这关 —— 否则 `../x` 之类会把
    文件写到目标目录之外（与 custody filename 的 basename 防护同类）。"""
    return bool(name) and name not in (".", "..") and os.path.basename(name) == name


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

    def _provision_platform_sdk(self, local_job_working_table: str) -> None:
        """把平台当前 SDK 复制进 job 工作区的 .magnus_sdk，让容器跑平台 SDK 而非镜像里
        baked 的那份（wrapper 前导把它顶到 PYTHONPATH/PATH）。

        覆盖 **SLURM 执行的两种 transport**：本机（owned 独占集群，经 workspace bind 直接
        进容器）与远端（共享集群租户，由 _stage_in_job 推过去）。**非** remote-gated —— 这
        两者都要。Docker/local 模式不经此（走 _submit_to_docker，沿用镜像 baked SDK；本地
        有网、custody 走 HTTP，没有"无网写回 / SDK 演进要重 build 镜像"的痛点）。

        含一个 bin/magnus shim 给 bash `magnus` 链路。源缺失时静默跳过（容器回退 baked SDK）。"""
        if not _PLATFORM_SDK_SRC.is_dir():
            return
        sdk_root = os.path.join(local_job_working_table, _PLATFORM_SDK_SUBDIR)
        shutil.rmtree(sdk_root, ignore_errors=True)
        shutil.copytree(
            str(_PLATFORM_SDK_SRC),
            os.path.join(sdk_root, "magnus"),
            ignore = shutil.ignore_patterns("__pycache__"),
        )
        bin_dir = os.path.join(sdk_root, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        shim_path = os.path.join(bin_dir, "magnus")
        with open(shim_path, "w", newline="\n", encoding="utf-8") as shim:
            shim.write(_MAGNUS_CLI_SHIM)
        os.chmod(shim_path, 0o755)

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
        # 平台 SDK 已由 _provision_platform_sdk 落在本机工作区的 .magnus_sdk，推到远端
        # （本机执行下它已在 workspace bind 里，无需推）。
        local_sdk = os.path.join(self._local_job_working_table(job_id), _PLATFORM_SDK_SUBDIR)
        if os.path.isdir(local_sdk):
            transport.push(local_sdk, f"{remote_table}/{_PLATFORM_SDK_SUBDIR}")
            # scp 不保模式位，远端 bash `magnus` shim 的 +x 须显式补回，否则 PATH 上
            # 这个 shim 不可执行、bash 链路 hijack 失效。
            transport.run(["chmod", "+x", f"{remote_table}/{_PLATFORM_SDK_SUBDIR}/bin/magnus"])

    def _stage_in_resources(
        self,
        job_id: str,
        local_sif_path: str,
        local_repo_dir: str,
    ) -> None:
        """relay 模式：把控制机本地拉好的 SIF + 仓库推到远端站点。

        - SIF 落远端 container_cache 持久缓存：先 `test -f` 探在不在，**命中即跳过传输**
          （满足"命中镜像的后续 job 本地级延迟"）；未命中先推到 `.partial.<job>` 临时名
          再原子 `mv` 就位 —— 半路失败不会在缓存留下残缺 SIF 被后续 job 当命中误用。
        - 仓库每任务推到远端 job 工作区（与本地每任务 clone 的模型一致；持久 repo 缓存 +
          增量是后续优化）。

        remote 模式（远端自拉）此处不该被调到（config 已 gate）；本机执行 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        if magnus_config["transport"]["ssh"]["resource_staging"] != "relay":
            return
        transport = self.slurm_manager.transport

        remote_sif_path = (
            f"{magnus_remote_container_cache_path}/{os.path.basename(local_sif_path)}"
        )
        already_cached = transport.run(["test", "-f", remote_sif_path])
        if already_cached.returncode != 0:
            staged_sif_path = f"{remote_sif_path}.partial.{job_id}"
            transport.push(local_sif_path, staged_sif_path)
            promoted = transport.run(["mv", "-f", staged_sif_path, remote_sif_path])
            if promoted.returncode != 0:
                raise RuntimeError(
                    f"failed to promote staged SIF {staged_sif_path} -> {remote_sif_path} "
                    f"(rc={promoted.returncode}): {promoted.stderr.strip()}"
                )

        # 仓库是目录：scp -r 落进一个**已存在**的同名目录会嵌套（remote/repository/
        # repository）。unique job-id + pause 时清远端使它通常不预存在，这里仍先 rm -rf
        # 清一遍兜底，保证 scp 把仓库落在 remote_repo_dir 本身。
        remote_repo_dir = f"{self._remote_job_working_table(job_id)}/repository"
        transport.run(["rm", "-rf", remote_repo_dir])
        transport.push(local_repo_dir, remote_repo_dir)

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
                # metrics 文件名来自远端无网计算节点（信任边界）：拼 host 侧落点前必须
                # 确认是安全单层名，否则 `../x` 之类会把文件写到 metrics 目录外。
                if not _is_safe_leaf(name):
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
        # custody 产物回读：把 SDK 无网模式 drop 的文件拉回并注册进 custody，让 caller
        # 凭 SDK 返回的 token 下载得到（终态拉一次即可，drop 在 job 退出前已落盘）。
        self._stage_out_custody(job_id)

    def _stage_out_custody(self, job_id: str) -> None:
        """把远端 drop 目录里的 custody 产物拉回本机并注册进 custody。

        远端无网执行时,容器内 SDK 的 `custody_file()` 不走 HTTP,而是把文件 + meta.json
        按 `relay-<uuid>` token 落到 job 工作区的 drop 子目录(见 SDK Client._drop_file)。
        这里逐 token 拉回 + 用 `explicit_token` 注册,于是蓝图当时同步拿到的 FileSecret
        到这一刻就能被 caller 下载。重复注册幂等。本机执行下 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        transport = self.slurm_manager.transport
        remote_drop = f"{self._remote_job_working_table(job_id)}/{_CUSTODY_DROP_SUBDIR}"
        local_drop = f"{self._local_job_working_table(job_id)}/{_CUSTODY_DROP_SUBDIR}"

        # drop 目录可能根本不存在(job 没产出 custody 文件):列不到就跳过。
        listing = transport.run(["ls", "-1", remote_drop])
        if listing.returncode != 0:
            return
        for token in listing.stdout.splitlines():
            token = token.strip()
            # token 是远端 drop 目录里的子目录名（job 端造，信任边界外）：拼 host 侧
            # 落点前过安全单层名校验，与 metrics 拉取同防。真正按 token 注册进 custody
            # 时 store_file 还会用 _RELAY_TOKEN_RE 严格校验形状。
            if not _is_safe_leaf(token):
                continue
            try:
                # 逐 token 拉整个条目目录(meta.json + 文件/.tar.gz),scp -r。
                transport.fetch(f"{remote_drop}/{token}", f"{local_drop}/{token}")
            except RuntimeError:
                continue
            meta_path = os.path.join(local_drop, token, "meta.json")
            if not os.path.exists(meta_path):
                continue
            try:
                with open(meta_path, encoding="utf-8") as meta_file:
                    meta = json.load(meta_file)
                # meta.json 是 job 端(无网计算节点 = 信任边界)写的,filename 不可信:
                # 在 host 侧 join+open 前先 basename,杜绝 `../../etc/passwd` 这类穿越读
                # 任意主机文件再注册成可下载 custody。token 已由 store_file 按 relay 形状校验。
                safe_filename = os.path.basename(meta["filename"])
                if not safe_filename or safe_filename in (".", ".."):
                    continue
                file_path = os.path.join(local_drop, token, safe_filename)
                if not os.path.exists(file_path):
                    continue
                with open(file_path, "rb") as file_obj:
                    file_custody_manager.store_file(
                        filename = safe_filename,
                        file_obj = file_obj,
                        expire_minutes = meta.get("expire_minutes"),
                        is_directory = meta.get("is_directory", False),
                        max_downloads = meta.get("max_downloads"),
                        explicit_token = meta["token"],
                    )
            except Exception as error:
                # 单个产物注册失败不连累其余 / 不挂 job 终态收敛(marker 已回读)。
                logger.warning(
                    f"Failed to register staged custody entry {token} for job {job_id}: {error}"
                )

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
