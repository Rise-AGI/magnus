# back_end/server/_scheduler/_staging.py
"""跨界 workspace I/O：transport=ssh 时把 job 工作区在本机与远程站点间搬运。

magnus 在本机，SLURM 在异机（远端站点），两边无共享盘 —— job 的工作区
（wrapper.py / repo / SIF / overlay）必须落远端 remote_root，而 wrapper 写出的
marker（.magnus_success / .magnus_result / .magnus_action / .magnus_oom）、SLURM
stdout、metrics 要回读到本机给 scheduler / 前端端点。本 mixin 收口这条搬运链路。

本机 transport（独占集群本地、Docker local）下 is_remote=False，下面每个方法都
立即返回 no-op，且 _scheduler/__init__ 里的远端路径已逐字收敛回本地路径，于是整条
执行链对本机站点字节级不变、零搬运参与。

结果按协议文件回读；SDK 与仓库目录通过 transport 的单流归档原子发布。
SIF / repo 的 relay 搬运也在此收口；native build 的有界缓存由独立模块管理。
"""
import os
import re
import json
import shutil
import tempfile
import threading
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING
from .._protocol_files import replace_protocol_file
from .._file_custody_manager import file_custody_manager
from .._custody_receipts import (
    _RELAY_TOKEN_RE,
    _read_custody_receipts,
    _write_custody_receipts,
)
from . import (
    logger,
    magnus_workspace_path,
)
from ._relay_build_cache import stage_built_sif
from .._slurm_manager._ssh_auto_connect import remaining_timeout
from .._slurm_manager._transport import check_transfer_cancelled
from .._staging_diagnostics import StagingProgress

# Directory (inside the job working tree) where the in-container SDK drops custody
# uploads on no-network remote execution, paired with the SDK's MAGNUS_CUSTODY_DROP_DIR.
# Each <token>/ holds the file (a .tar.gz for directories) plus a meta.json; the host
# pulls them back and registers each in custody under its token. Must match the path
# the wrapper exports into the container.
_CUSTODY_DROP_SUBDIR = ".magnus_custody_drop"

# Inbound counterpart of _CUSTODY_DROP_SUBDIR: before sbatch, the host stages the custody
# files a job's entry_command references into <remote_table>/<this>/<token>/ (file +
# meta.json, same layout as the drop dir), exposed to the container as the SDK's
# MAGNUS_CUSTODY_DROPIN_DIR so in-job `magnus receive` reads them from the filesystem
# instead of HTTP. Must match the path the wrapper exports into the container.
_CUSTODY_DROPIN_SUBDIR = ".magnus_custody_dropin"

# magnus-secret:<token> literals in a job's entry_command tell the host which custody
# files to stage in. The token charset covers both the server's
# <prime>-<word>-<word>-<word> and the SDK's relay-<hex> shapes; over-capture is
# self-correcting since an unknown token simply isn't found in custody and is skipped.
# The prefix mirrors the SDK's FILE_SECRET_PREFIX (a stable wire constant).
_FILE_SECRET_RE = re.compile(r"magnus-secret:([0-9A-Za-z_-]+)")

# The platform SDK source (this repo's sdks/python/src/magnus). Provisioned into each
# job's .magnus_sdk so the container runs the platform's current SDK instead of the one
# baked into the image. parents: [0]=_scheduler [1]=server [2]=back_end [3]=repo root.
_PLATFORM_SDK_SRC = Path(__file__).resolve().parents[3] / "sdks" / "python" / "src" / "magnus"
_PLATFORM_SDK_SUBDIR = ".magnus_sdk"
# Shim so the bash `magnus` command also resolves to the platform SDK. The python
# `import magnus` path is covered by PYTHONPATH (the wrapper points it at .magnus_sdk);
# this shim covers the CLI. It must run under an interpreter that actually carries the
# SDK's runtime deps (httpx, pydantic, typer, ...). The injected SDK is source only, so
# those deps come from whichever interpreter runs it — and on uv-based images the ambient
# python3 / python is a project venv synced from a lockfile that need not name them, while
# the image bakes magnus-sdk (hence its deps) into a different interpreter, commonly the
# base /usr/local/bin/python. A shim that hardcodes `python3` therefore lands on the
# dep-less venv and dies on `import httpx`, even though the image did provide the deps.
# So probe candidates in order and exec the first whose interpreter can import the SDK:
# ambient first (a venv that does carry the deps still wins), then the usual base
# locations (found even when a venv shadows them on PATH). The probe imports the very
# module the shim runs, so it accepts an interpreter only if the CLI will actually run
# there. If none qualifies (an image that bakes the SDK nowhere), fall back to python3 so
# the failure is the original clear ImportError rather than a bare "command not found".
_MAGNUS_CLI_SHIM = (
    "#!/bin/sh\n"
    "for _py in python3 python /usr/local/bin/python3 /usr/local/bin/python /usr/bin/python3 /usr/bin/python; do\n"
    "    if \"$_py\" -c 'import magnus.cli.main' >/dev/null 2>&1; then\n"
    "        exec \"$_py\" -m magnus.cli.main \"$@\"\n"
    "    fi\n"
    "done\n"
    "if command -v python3 >/dev/null 2>&1; then exec python3 -m magnus.cli.main \"$@\"; fi\n"
    "exec python -m magnus.cli.main \"$@\"\n"
)

# The platform SDK is injected as source only; its sole third-party runtime import is httpx
# (client.py / http_download.py — the SDK never imports pydantic, and typer / rich are used
# only by the CLI). The bash `magnus` shim above already routes the CLI to an interpreter that
# carries those deps, but a user entry_command that imports magnus directly — e.g.
# `python3 -c "import magnus; magnus.download_file(...)"` — runs under whatever python it chose
# (on uv images the project venv, synced from a lockfile that need not carry httpx) and dies at
# `import httpx`, even though PYTHONPATH resolves the magnus source. So vendor httpx and its
# pure-Python runtime dependencies next to the source, on the same PYTHONPATH, so any
# interpreter can import magnus without relying on the image's site-packages. Only these
# pure-Python packages are vendored — they import cleanly across interpreters and Python
# versions, unlike a compiled extension (there is none in this closure). socksio is optional
# (httpx[socks]) and skipped when absent.
_VENDORED_SDK_DEPS = (
    # Sync closure (import magnus + download_file / custody_file): httpx and its sync stack.
    "httpx", "httpcore", "h11", "certifi", "idna",
    # Async closure (download_file_async / call_service_async): httpx's anyio backend, plus the
    # deps anyio hard-imports at its module top — typing_extensions on every Python, and
    # exceptiongroup on < 3.11 (a stdlib backport, so absent — and unneeded — on >= 3.11, where
    # the best-effort copy simply skips it). socksio is httpx[socks], optional.
    "anyio", "sniffio", "socksio", "typing_extensions", "exceptiongroup",
)


def _vendor_pure_python_deps(sdk_root: str) -> None:
    """Copy httpx and its pure-Python runtime deps into sdk_root (alongside the magnus source,
    both on the container's PYTHONPATH) so `import magnus` works under any interpreter, not just
    one whose site-packages happen to carry them. Resolved from the backend interpreter, which
    runs httpx itself and therefore has them. Best-effort per package: one that can't be located
    or copied is skipped, degrading to the prior behavior rather than blocking job submission."""
    import importlib.util
    for dep in _VENDORED_SDK_DEPS:
        try:
            spec = importlib.util.find_spec(dep)
            if spec is None:
                continue
            locations = list(spec.submodule_search_locations or [])
            if locations:
                shutil.copytree(
                    locations[0],
                    os.path.join(sdk_root, dep),
                    ignore = shutil.ignore_patterns("__pycache__", "*.pyc"),
                    dirs_exist_ok = True,
                )
            elif spec.origin and spec.origin.endswith(".py"):
                shutil.copy2(spec.origin, os.path.join(sdk_root, os.path.basename(spec.origin)))
        except Exception as error:
            logger.warning(f"platform SDK: could not vendor dependency {dep}: {error}")


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

_OPTIONAL_DIRECTORY_LIST = """import json, os, sys
try:
    entries = os.listdir(sys.argv[1])
except FileNotFoundError:
    entries = None
print(json.dumps(entries))
"""


def _remote_optional_entries(
    transport,
    path: str,
    *,
    timeout: float = 30,
    cancel_event: threading.Event | None = None,
) -> list[str]:
    # Shell test/ls exit codes conflate absence with permissions or SSH failure.
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Artifact discovery cancelled")
    result = transport.run(
        ["python3", "-c", _OPTIONAL_DIRECTORY_LIST, path],
        check = True,
        timeout = min(timeout, 30),
    )
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Artifact discovery cancelled")
    if result.returncode != 0:
        raise RuntimeError("Remote artifact directory query failed")
    entries = json.loads(result.stdout)
    if entries is None:
        return []
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        raise ValueError("Invalid remote artifact directory response")
    return entries


def _fetch_artifact_file(
    transport,
    remote_path: str,
    local_path: str,
    *,
    timeout: float = 30,
    cancel_event: threading.Event | None = None,
) -> None:
    # Receive outside the Job-owned tree, then publish through its anchored fd.
    descriptor, temporary = tempfile.mkstemp(prefix=".stage-out-", dir=magnus_workspace_path)
    os.close(descriptor)
    try:
        transport.fetch(
            remote_path,
            temporary,
            timeout = timeout,
            **({"cancel_event": cancel_event} if cancel_event is not None else {}),
        )
        root = os.path.join(magnus_workspace_path, "jobs")
        replace_protocol_file(
            root,
            os.path.relpath(local_path, root),
            temporary,
        )
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _is_safe_leaf(name: str) -> bool:
    """name 是否为安全的单层路径名。远端无网计算节点（信任边界外）经 `ls` 列出的
    文件 / 目录名，在 host 侧拼进 fetch 落点前必须过这关 —— 否则 `../x` 之类会把
    文件写到目标目录之外（与 custody filename 的 basename 防护同类）。"""
    return (
        isinstance(name, str)
        and bool(name)
        and name not in (".", "..")
        and not any(character in name for character in ("/", "\\", "\0"))
        and os.path.basename(name) == name
    )


def _validate_custody_metadata(
    meta: object,
    token: str,
) -> tuple[str, str]:
    if not isinstance(meta, dict) or meta.get("token") != token:
        raise ValueError("Invalid remote custody metadata")
    filename = meta.get("filename")
    stored_name = meta.get("storage_filename", filename)
    if not _is_safe_leaf(filename) or not _is_safe_leaf(stored_name) or stored_name == "meta.json":
        raise ValueError("Invalid remote custody filename")
    if not isinstance(meta.get("is_directory", False), bool):
        raise ValueError("Invalid remote custody directory flag")
    for name in ("expire_minutes", "max_downloads", "size_bytes"):
        value = meta.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"Invalid remote custody {name}")
    if meta.get("max_downloads") is not None and meta["max_downloads"] < 1:
        raise ValueError("Invalid remote custody download limit")
    if meta.get("size_bytes") is not None and meta["size_bytes"] < 0:
        raise ValueError("Invalid remote custody size")
    return filename, stored_name


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
        return f"{self.remote_workspace_path}/jobs/{job_id}"

    def _local_job_working_table(self, job_id: str) -> str:
        return f"{magnus_workspace_path}/jobs/{job_id}"

    def _provision_platform_sdk(self, local_job_working_table: str) -> None:
        """把平台当前 SDK 复制进 job 工作区的 .magnus_sdk，让容器跑平台 SDK 而非镜像里
        baked 的那份（wrapper 前导把它顶到 PYTHONPATH/PATH）。

        覆盖 **SLURM 执行的两种 transport**：本机（owned 独占集群，经 workspace bind 直接
        进容器）与远端（共享集群租户，由 _stage_in_job 推过去）。**非** remote-gated —— 这
        两者都要。Docker/local 模式不经此（走 _submit_to_docker，沿用镜像 baked SDK；本地
        有网、custody 走 HTTP，没有"无网写回 / SDK 演进要重 build 镜像"的痛点）。

        含一个 bin/magnus shim 给 bash `magnus` 链路，以及 vendored 的 httpx 全家桶让容器内任意
        解释器都能 `import magnus`（见 _vendor_pure_python_deps）。源缺失时静默跳过（容器回退 baked SDK）。"""
        if not _PLATFORM_SDK_SRC.is_dir():
            return
        sdk_root = os.path.join(local_job_working_table, _PLATFORM_SDK_SUBDIR)
        shutil.rmtree(sdk_root, ignore_errors=True)
        shutil.copytree(
            str(_PLATFORM_SDK_SRC),
            os.path.join(sdk_root, "magnus"),
            ignore = shutil.ignore_patterns("__pycache__"),
        )
        _vendor_pure_python_deps(sdk_root)
        bin_dir = os.path.join(sdk_root, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        shim_path = os.path.join(bin_dir, "magnus")
        with open(shim_path, "w", newline="\n", encoding="utf-8") as shim:
            shim.write(_MAGNUS_CLI_SHIM)
        os.chmod(shim_path, 0o755)

    def _submission_cancel_event(
        self,
        job_id: str,
    ) -> threading.Event | None:
        with self._preparing_jobs_lock:
            event = self._submission_cancel_events.get(job_id)
        check_transfer_cancelled(event)
        return event

    def _stage_in_job(self, job_id: str, wrapper_local_path: str) -> None:
        """在远端建好 job 工作区骨架并推送本机生成的 wrapper.py。

        _submit 写完本机 wrapper 后调用。远端 slurm/ 目录承接 sbatch --output、
        metrics/ 目录承接 sidecar 写入（wrapper 自身不建 work_dir，假定已存在）。
        本机执行下 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        local_table = self._local_job_working_table(job_id)
        receipts = _read_custody_receipts(local_table)
        if receipts["logs_complete"]:
            receipts["logs_complete"] = False
            _write_custody_receipts(local_table, receipts)
        cancel_event = self._submission_cancel_event(job_id)
        transport = self.slurm_manager.transport
        progress = StagingProgress(os.path.dirname(magnus_workspace_path), job_id)
        remote_table = self._remote_job_working_table(job_id)
        progress.call(
            "workspace",
            transport.run,
            ["mkdir", "-p", f"{remote_table}/slurm", f"{remote_table}/metrics"],
            require_success=True,
            timeout=30,
            check=True,
        )
        # A restarted, never-submitted attempt must not reuse expired inputs
        # from an interrupted upload. Uncertain submissions never reach staging.
        progress.call(
            "custody_reset",
            transport.run,
            ["rm", "-rf", f"{remote_table}/{_CUSTODY_DROPIN_SUBDIR}"],
            require_success=True,
            timeout=30,
            check=True,
        )
        progress.call(
            "wrapper",
            transport.push,
            wrapper_local_path,
            f"{remote_table}/wrapper.py",
            size=os.path.getsize(wrapper_local_path),
            budget=getattr(transport, "transfer_timeout_seconds", None),
            cancel_event=cancel_event,
        )
        # 平台 SDK 已由 _provision_platform_sdk 落在本机工作区的 .magnus_sdk，推到远端
        # （本机执行下它已在 workspace bind 里，无需推）。
        local_sdk = os.path.join(self._local_job_working_table(job_id), _PLATFORM_SDK_SUBDIR)
        if os.path.isdir(local_sdk):
            remote_sdk = f"{remote_table}/{_PLATFORM_SDK_SUBDIR}"
            # Only an unsubmitted Job reaches staging; replace its prior SDK
            # before atomically publishing the complete archive.
            check_transfer_cancelled(cancel_event)
            progress.call(
                "platform_sdk_setup",
                transport.run,
                ["rm", "-rf", remote_sdk],
                timeout=30,
                check=True,
            )
            progress.call(
                "platform_sdk",
                transport.push,
                local_sdk,
                remote_sdk,
                budget=getattr(transport, "transfer_timeout_seconds", None),
                cancel_event=cancel_event,
            )
            # Windows controllers cannot preserve POSIX executable bits locally.
            # The runtime shim must remain executable on the Linux target.
            check_transfer_cancelled(cancel_event)
            progress.call(
                "platform_sdk_permissions",
                transport.run,
                ["chmod", "+x", f"{remote_table}/{_PLATFORM_SDK_SUBDIR}/bin/magnus"],
                timeout=30,
                check=True,
            )

    def _stage_in_resources(
        self,
        job_id: str,
        local_sif_path: str,
        local_repo_dir: str,
        *,
        build_digest: str | None = None,
    ) -> None:
        """relay 模式：把控制机本地拉好的 SIF + 仓库推到远端站点。

        - Dockerfile 构建产物使用独立的有界缓存，保护活跃 Job 并在传输前预留空间。
        - 既有 container_image SIF 落远端 container_cache：先 `test -f` 探在不在，**命中即跳过传输**
          （满足"命中镜像的后续 job 本地级延迟"）；未命中先推到 `.partial.<job>` 临时名
          再原子 `mv` 就位 —— 半路失败不会在缓存留下残缺 SIF 被后续 job 当命中误用。
        - 仓库每任务推到远端 job 工作区（与本地每任务 clone 的模型一致；持久 repo 缓存 +
          增量是后续优化）。

        remote 模式（远端自拉）此处不该被调到（config 已 gate）；本机执行 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        if self.config["transport"]["ssh"]["resource_staging"] != "relay":
            return
        cancel_event = self._submission_cancel_event(job_id)
        transport = self.slurm_manager.transport
        progress = StagingProgress(os.path.dirname(magnus_workspace_path), job_id)

        if build_digest is not None:
            progress.call(
                "image_cache",
                stage_built_sif,
                transport,
                local_sif_path,
                build_digest,
                size=os.path.getsize(local_sif_path),
                job_id = job_id,
                target = self.target,
                cancel_event = cancel_event,
            )
        else:
            remote_sif_path = (
                f"{self.remote_container_cache_path}/{os.path.basename(local_sif_path)}"
            )
            already_cached = progress.call("image_cache_lookup", transport.run, ["test", "-f", remote_sif_path], timeout=30)
            if already_cached.returncode != 0:
                staged_sif_path = f"{remote_sif_path}.partial.{job_id}"
                try:
                    progress.call(
                        "image_upload",
                        transport.push,
                        local_sif_path,
                        staged_sif_path,
                        size=os.path.getsize(local_sif_path),
                        budget=getattr(transport, "transfer_timeout_seconds", None),
                        count_bytes=False,
                        cancel_event=cancel_event,
                    )
                    check_transfer_cancelled(cancel_event)
                    progress.call(
                        "image_publish",
                        transport.run,
                        ["mv", "-f", staged_sif_path, remote_sif_path],
                        size=os.path.getsize(local_sif_path),
                        timeout=30,
                        budget=30,
                        require_success=True,
                    )
                except BaseException:
                    try:
                        transport.run(["rm", "-f", staged_sif_path], timeout=30, check=True)
                    except Exception:
                        logger.warning("Interrupted image upload could not be removed for Job %s", job_id)
                    raise

        # Directory publication requires an absent destination. A restarted
        # pre-submission attempt may still have its previous complete tree.
        remote_repo_dir = f"{self._remote_job_working_table(job_id)}/repository"
        check_transfer_cancelled(cancel_event)
        progress.call("repository_setup", transport.run, ["rm", "-rf", remote_repo_dir], timeout=30, check=True)
        progress.call(
            "repository",
            transport.push,
            local_repo_dir,
            remote_repo_dir,
            budget=getattr(transport, "transfer_timeout_seconds", None),
            cancel_event=cancel_event,
        )

    def _stage_in_custody(self, job_id: str, *command_sources: str) -> None:
        """把 job 的 entry_command 引用的 custody 文件预先 stage 进远端 job 工作区的 dropin
        目录，让无网计算节点上的 in-job `magnus receive` / download_file 读文件系统而非打
        HTTP —— 镜像 _stage_out_custody 的反向。本机执行下 no-op。

        扫 command_sources（entry_command + system_entry_command）里的 `magnus-secret:<token>`
        字面量，逐个从**本地** custody 取文件（host 读自身磁盘上的 custody 存储，不走网络）
        推到 `{remote}/.magnus_custody_dropin/<token>/<filename>` + meta.json（布局同 SDK
        上传侧 drop）。token 在 custody 里查不到（无效 / 过期）则跳过 —— in-job 取不到时与
        本机模式"文件不存在"语义一致。

        stage-in 持有不计下载次数的读取租约：host 把文件**预置**进 job 工作区，
        不是用户那次"下载"，不消耗 max_downloads —— 否则默认 max_downloads=1 会让 fan-out
        （一次上传 → N 个 job receive 同一 secret）在第二个 job 就拿不到文件。用户那次 download
        从 dropin 读文件系统，本就不碰 custody 计数。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        tokens = set()
        for source in command_sources:
            if source:
                tokens.update(_FILE_SECRET_RE.findall(source))
        if not tokens:
            return
        cancel_event = self._submission_cancel_event(job_id)
        transport = self.slurm_manager.transport
        progress = StagingProgress(os.path.dirname(magnus_workspace_path), job_id)
        remote_dropin = f"{self._remote_job_working_table(job_id)}/{_CUSTODY_DROPIN_SUBDIR}"
        local_dropin = os.path.join(self._local_job_working_table(job_id), _CUSTODY_DROPIN_SUBDIR)
        for index, token in enumerate(sorted(tokens), start=1):
            check_transfer_cancelled(cancel_event)
            acquired = file_custody_manager.acquire_file(
                token,
                count_download = False,
            )
            if acquired is None:
                logger.info(
                    "Job %s stage-in: custody input %d/%d unavailable; skipped",
                    job_id, index, len(tokens),
                )
                continue
            file_path, filename, is_directory, _ = acquired
            try:
                remote_entry = f"{remote_dropin}/{token}"
                progress.call(
                    "custody_directory",
                    transport.run,
                    ["mkdir", "-p", remote_entry],
                    timeout=30,
                    index=index,
                    total=len(tokens),
                    require_success=True,
                )
                progress.call(
                    "custody_payload",
                    transport.push,
                    str(file_path),
                    f"{remote_entry}/payload",
                    size=os.path.getsize(file_path),
                    index=index,
                    total=len(tokens),
                    budget=getattr(transport, "transfer_timeout_seconds", None),
                    cancel_event=cancel_event,
                )
                # meta.json follows the SDK's offline drop-dir format.
                local_entry = os.path.join(local_dropin, token)
                os.makedirs(local_entry, exist_ok=True)
                meta_path = os.path.join(local_entry, "meta.json")
                with open(meta_path, "w", encoding="utf-8") as meta_file:
                    json.dump(
                        {
                            "token": token,
                            "filename": filename,
                            "storage_filename": "payload",
                            "is_directory": is_directory,
                        },
                        meta_file,
                    )
                progress.call(
                    "custody_metadata",
                    transport.push,
                    meta_path,
                    f"{remote_entry}/meta.json",
                    size=os.path.getsize(meta_path),
                    index=index,
                    total=len(tokens),
                    budget=getattr(transport, "transfer_timeout_seconds", None),
                    cancel_event=cancel_event,
                )
            finally:
                file_custody_manager.release_file(token)

    def _stage_out_logs(
        self,
        job_id: str,
        *,
        timeout: float = 30,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        deadline = monotonic() + timeout
        transport = self.slurm_manager.transport
        remote_table = self._remote_job_working_table(job_id)
        local_table = self._local_job_working_table(job_id)
        if "output.txt" in _remote_optional_entries(
            transport,
            f"{remote_table}/slurm",
            timeout = remaining_timeout(deadline),
            cancel_event = cancel_event,
        ):
            _fetch_artifact_file(
                transport,
                f"{remote_table}/slurm/output.txt",
                f"{local_table}/slurm/output.txt",
                timeout = remaining_timeout(deadline),
                cancel_event = cancel_event,
            )
        for name in _remote_optional_entries(
            transport,
            f"{remote_table}/metrics",
            timeout = remaining_timeout(deadline),
            cancel_event = cancel_event,
        ):
            if not _is_safe_leaf(name):
                raise ValueError("Invalid remote metric filename")
            _fetch_artifact_file(
                transport,
                f"{remote_table}/metrics/{name}",
                f"{local_table}/metrics/{name}",
                timeout = remaining_timeout(deadline),
                cancel_event = cancel_event,
            )

    def _stage_out_markers(
        self,
        job_id: str,
    ) -> None:
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        deadline = monotonic() + 30
        transport = self.slurm_manager.transport
        remote_table = self._remote_job_working_table(job_id)
        local_table = self._local_job_working_table(job_id)
        entries = _remote_optional_entries(
            transport,
            remote_table,
            timeout = remaining_timeout(deadline),
        )
        for marker in _REMOTE_MARKERS:
            if marker in entries:
                _fetch_artifact_file(
                    transport,
                    f"{remote_table}/{marker}",
                    f"{local_table}/{marker}",
                    timeout = remaining_timeout(deadline),
                )

    def _stage_out_final(
        self,
        job_id: str,
    ) -> bool:
        if not self._is_remote_execution():
            return True
        complete = True
        try:
            if not self._stage_out_custody(job_id):
                complete = False
        except Exception:
            logger.warning("Job %s custody transfer is pending", job_id, exc_info=True)
            complete = False
        try:
            if not self._custody_relay.request_final_logs(job_id):
                complete = False
        except Exception:
            logger.warning("Job %s final log transfer is pending", job_id, exc_info=True)
            complete = False
        return complete

    def _stage_out_custody(
        self,
        job_id: str,
        *,
        deadline: float | None = None,
    ) -> bool:
        if not self._is_remote_execution():
            return True
        return self._custody_relay.poll(job_id, deadline or monotonic() + 30)

    def _cleanup_remote_job(self, job_id: str) -> None:
        """删除远端 job 工作区（marker / 日志 / metrics 已在 _stage_out_final 拉回本机，
        本机侧 keep-whitelist 保留持久产物）。本机执行下 no-op。"""
        if not self._is_remote_execution():
            return
        assert self.slurm_manager is not None
        transport = self.slurm_manager.transport
        remote_table = self._remote_job_working_table(job_id)
        removed = transport.run(
            [
                "python3", "-c",
                (Path(__file__).resolve().parents[1] / "_workspace_cleanup.py").read_text(encoding="utf-8")
                + "\nimport sys\nremove_job_path(sys.argv[1], timeout_seconds=20)\n",
                remote_table,
            ],
            timeout = 30,
        )
        if removed.returncode != 0:
            raise RuntimeError(
                f"Failed to clean up remote working table {remote_table} "
                f"(rc={removed.returncode}): {removed.stderr.strip()}"
            )
