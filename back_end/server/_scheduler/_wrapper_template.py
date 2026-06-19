# back_end/server/_scheduler/_wrapper_template.py
"""SLURM 模式下生成 compute node 上跑的 wrapper.py 源码。

wrapper.py 在每个 SLURM job 启动时由 sbatch 调起，负责：
- 启动 metrics sidecar 线程（与 _metrics_collector 的 host-side Docker 收集器对偶）
- 起 apptainer 容器执行用户脚本
- 写 success / OOM marker
- 清理 ephemeral overlay

这里是一个巨大的 f-string 模板，纯输入→字符串转换，不依赖 scheduler 实例状态。
"""
def _build_wrapper_content(
    job_working_table: str,
    job_ephemeral_table: str,
    repo_dir: str,
    sif_path: str,
    system_entry_command: str,
    user_token: str,
    magnus_address: str,
    job_id: str,
    ephemeral_storage: str,
    allow_root: bool,
    entry_command: str,
    effective_runner: str,
    container_runtime: str,
) -> str:
    success_marker_path = f"{job_working_table}/.magnus_success"

    # 容器运行时方言：apptainer 与 singularity(CE) 的 CLI（exec / overlay create /
    # --nv / --contain[all] / --overlay / --writable-tmpfs / --env / --fakeroot /
    # --pwd / --no-mount）大体兼容，但 magnus 注入的环境变量前缀不同 —— apptainer
    # 读 APPTAINER_* / APPTAINERENV_*，singularity(CE) 只读 SINGULARITY_* /
    # SINGULARITYENV_*（apptainer 兼容读 SINGULARITY_*，反之不成立）。按
    # container_runtime 推导出二进制名与这两个前缀；container_runtime='apptainer'
    # 时全部回到历史字面量，生成的 wrapper 字节级一致。注意：未 export 的纯局部
    # shell 变量（APPTAINER_CONTAIN / APPTAINER_FLAGS / APPTAINER_CMD /
    # _setuid_apptainer）保持原名，它们不进环境、不被任何 runtime 读，singularity
    # 下只是普通局部变量。
    runtime_binary = container_runtime
    runtime_var_prefix = container_runtime.upper()
    runtime_env_prefix = f"{runtime_var_prefix}ENV"

    return f'''import os
import signal
import sys
import traceback
import subprocess
import threading
import time
import json

# NOTE: cgroup parsing here is duplicated from
# back_end/server/_metrics_collector/_cgroup.py (host-side Docker collector).
# Wrapper.py is built by f-string templating and cannot import that module
# at runtime on the compute node. Keep both impls behaviorally equivalent.
def _is_pure_v2():
    """True when /sys/fs/cgroup is the cgroup v2 mount AND no v1 cgroup is mounted.

    On hybrid systems v2 is mounted elsewhere (e.g. /sys/fs/cgroup/unified) and
    SLURM / systemd typically place tasks under v1 controllers; the "0::" line in
    /proc/self/cgroup there points to a v2 path that isn't authoritative for the
    task's cpu/memory accounting. Caller must read v1 controller paths in that case.
    """
    v2_at = None
    has_v1 = False
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                if parts[2] == "cgroup2":
                    v2_at = parts[1]
                elif parts[2] == "cgroup":
                    has_v1 = True
    except Exception:
        return True
    return v2_at == "/sys/fs/cgroup" and not has_v1

def _cg_paths():
    """Return (v2_rel, v1_mem_rel, v1_cpu_rel) for /proc/self. Any may be None.
    Both v2_rel and v1_* can be populated on hybrid systems; caller picks based
    on _is_pure_v2().
    """
    v2_rel = None
    v1_mem = None
    v1_cpu = None
    try:
        with open("/proc/self/cgroup") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("0::"):
                    v2_rel = line[3:]
                    continue
                cols = line.split(":", 2)
                if len(cols) != 3:
                    continue
                ctrls = cols[1].split(",")
                if "memory" in ctrls and v1_mem is None:
                    v1_mem = cols[2]
                if v1_cpu is None and ("cpuacct" in ctrls or "cpu" in ctrls):
                    v1_cpu = cols[2]
    except Exception:
        return None, None, None
    return v2_rel, v1_mem, v1_cpu

def _read_cpu_usage_usec():
    """Cumulative cpu usage in microseconds for our cgroup. None on failure."""
    v2_rel, _, v1_cpu = _cg_paths()
    if _is_pure_v2():
        if v2_rel is None:
            return None
        path = "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/cpu.stat"
        try:
            with open(path) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "usage_usec":
                        return int(parts[1])
        except Exception:
            return None
        return None
    if v1_cpu is None:
        return None
    # cpuacct.usage is in nanoseconds; convert to microseconds for parity with v2.
    path = "/sys/fs/cgroup/cpuacct" + v1_cpu.rstrip("/") + "/cpuacct.usage"
    try:
        with open(path) as f:
            return int(f.read().strip()) // 1000
    except Exception:
        return None

def _read_memory_used_bytes():
    """Memory usage in bytes for our cgroup, minus reclaimable file-backed page
    cache (mirrors `docker stats`). Without this, IO-heavy tasks look like
    they're near their memory limit just from cache."""
    v2_rel, v1_mem, _ = _cg_paths()
    if _is_pure_v2():
        if v2_rel is None:
            return None
        cgroup_dir = "/sys/fs/cgroup" + v2_rel.rstrip("/")
        usage_file = "memory.current"
    else:
        if v1_mem is None:
            return None
        cgroup_dir = "/sys/fs/cgroup/memory" + v1_mem.rstrip("/")
        usage_file = "memory.usage_in_bytes"

    try:
        with open(cgroup_dir + "/" + usage_file) as f:
            usage = int(f.read().strip())
    except Exception:
        return None

    inactive_file = 0
    try:
        with open(cgroup_dir + "/memory.stat") as f:
            for line in f:
                if line.startswith("inactive_file "):
                    inactive_file = int(line.split()[1])
                    break
    except Exception:
        pass
    return max(0, usage - inactive_file)

def _allocated_cpus():
    """Return CPU count this task is entitled to. Falls back conservatively."""
    for var in ("SLURM_CPUS_PER_TASK", "SLURM_CPUS_ON_NODE"):
        v = os.environ.get(var)
        if v and v.isdigit() and int(v) > 0:
            return int(v)
    return os.cpu_count() or 1

def _metrics_sidecar(metrics_dir, stop_event):
    try:
        import socket
        path = os.path.join(metrics_dir, "system.jsonl")
        hostname = socket.gethostname()
        # Only sample GPU indices that SLURM allocated to this job via CUDA_VISIBLE_DEVICES.
        # An unset/empty value previously disabled the filter and let nvidia-smi attribute
        # every visible GPU to a CPU-only job.
        _cvd = os.environ.get("CUDA_VISIBLE_DEVICES") or ""
        allowed_gpus = set(s.strip() for s in _cvd.split(",") if s.strip().isdigit())
        # CPU utilization is now task-quota relative (cgroup) rather than node-wide
        # (/proc/stat). 100% = task fully using its allocated cores.
        alloc_cpus = float(_allocated_cpus())
        prev_usage_usec = _read_cpu_usage_usec()
        prev_wall_ms = int(time.time() * 1000)
        while not stop_event.wait(5.0):
            now_ms = int(time.time() * 1000)
            lines = []
            node_labels = {{"node": hostname}}
            if allowed_gpus:
                try:
                    out = subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used",
                         "--format=csv,noheader,nounits"],
                        timeout=10, text=True, stderr=subprocess.DEVNULL,
                    )
                    for row in out.strip().split("\\n"):
                        parts = [p.strip() for p in row.split(",")]
                        if len(parts) != 3:
                            continue
                        idx, util, mem_mib = parts
                        if idx.strip() not in allowed_gpus:
                            continue
                        labels = {{"device": f"cuda:{{idx}}", "node": hostname}}
                        lines.append(json.dumps({{
                            "name": "system.gpu.utilization", "kind": "gauge",
                            "value": float(util), "time_unix_ms": now_ms,
                            "unit": "percent", "labels": labels,
                        }}))
                        lines.append(json.dumps({{
                            "name": "system.gpu.memory.used_bytes", "kind": "gauge",
                            "value": float(mem_mib) * 1048576, "time_unix_ms": now_ms,
                            "unit": "bytes", "labels": labels,
                        }}))
                except Exception:
                    pass
            # CPU metrics (cgroup-based, task-quota relative)
            try:
                cur_usage_usec = _read_cpu_usage_usec()
                if cur_usage_usec is not None and prev_usage_usec is not None:
                    d_usage = cur_usage_usec - prev_usage_usec
                    d_wall_ms = now_ms - prev_wall_ms
                    if d_wall_ms > 0 and alloc_cpus > 0 and d_usage >= 0:
                        cpu_pct = round((d_usage / (d_wall_ms * 1000.0) / alloc_cpus) * 100, 1)
                        if cpu_pct > 100.0:
                            cpu_pct = 100.0
                        lines.append(json.dumps({{
                            "name": "system.cpu.utilization", "kind": "gauge",
                            "value": cpu_pct, "time_unix_ms": now_ms,
                            "unit": "percent", "labels": node_labels,
                        }}))
                if cur_usage_usec is not None:
                    prev_usage_usec = cur_usage_usec
                    prev_wall_ms = now_ms
            except Exception:
                pass
            # Memory metrics
            try:
                mem_bytes = _read_memory_used_bytes()
                if mem_bytes is not None:
                    lines.append(json.dumps({{
                        "name": "system.memory.used_bytes", "kind": "gauge",
                        "value": float(mem_bytes), "time_unix_ms": now_ms,
                        "unit": "bytes", "labels": node_labels,
                    }}))
            except Exception:
                pass
            if lines:
                try:
                    with open(path, "a") as f:
                        f.write("\\n".join(lines) + "\\n")
                except Exception:
                    pass
    except Exception:
        pass

def _parse_size_to_mb(size_str):
    size_str = size_str.strip().upper()
    if size_str.endswith("G"):
        return int(float(size_str[:-1]) * 1024)
    if size_str.endswith("M"):
        return int(float(size_str[:-1]))
    return int(size_str)

def _check_oom():
    # Synchronous (not a daemon thread): if wrapper.py is itself OOM-killed the
    # marker is lost, but in a same-cgroup OOM the kernel kills the largest
    # victim first, and wrapper.py memory footprint is tiny. Fail-open on every
    # error so OOM detection cannot break the wrapper main flow.
    try:
        v2_rel, v1_mem, _ = _cg_paths()
        if _is_pure_v2():
            if v2_rel is None:
                return False, 0
            events_path = "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/memory.events"
            try:
                with open(events_path) as ef:
                    for ev_line in ef:
                        parts = ev_line.split()
                        if len(parts) >= 2 and parts[0] == "oom_kill":
                            count = int(parts[1])
                            return count > 0, count
            except Exception:
                return False, 0
            return False, 0
        if v1_mem is None:
            return False, 0
        oom_ctrl_path = "/sys/fs/cgroup/memory" + v1_mem.rstrip("/") + "/memory.oom_control"
        try:
            with open(oom_ctrl_path) as ef:
                for ev_line in ef:
                    parts = ev_line.split()
                    if len(parts) >= 2 and parts[0] == "oom_kill":
                        count = int(parts[1])
                        return count > 0, count
        except Exception:
            # Old kernels lack oom_kill on v1 (only under_oom) — fail-open.
            return False, 0
        return False, 0
    except Exception:
        return False, 0

def _cgroup_procs_path():
    """绝对路径定位本进程所属 cgroup 的 cgroup.procs 文件。

    与本文件 _cg_paths() 同源解析 /proc/self/cgroup：hybrid 系统优先 v1 freezer
    （SLURM 21.08 在 hybrid Ubuntu 默认 attach 这层 controller 做 proctrack），
    无 v1 时退到 pure v2 unified hierarchy。任一步失败返 None。
    """
    v2_rel = None
    freezer_rel = None
    try:
        with open("/proc/self/cgroup") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("0::"):
                    v2_rel = line[3:]
                    continue
                cols = line.split(":", 2)
                if len(cols) != 3:
                    continue
                if "freezer" in cols[1].split(","):
                    freezer_rel = cols[2]
    except Exception:
        return None
    if freezer_rel is not None:
        return "/sys/fs/cgroup/freezer" + freezer_rel.rstrip("/") + "/cgroup.procs"
    if v2_rel is not None:
        return "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/cgroup.procs"
    return None

def _signal_user_processes(sig):
    """SIGTERM handler 内调用：向 user 容器内进程全员转发 sig。

    Linux PID namespace 是 host ns 后代树的不对称视图 —— host ns 看得见全部
    task，每个 task 的 host PID 都是 host-global 标识，kill(2) 用 host PID
    跨 PID namespace 直接生效。`/proc/<host_pid>/status` 的 NSpid 字段把每
    层 namespace 内的 PID 列出来：

        NSpid: <outer_host_pid> [<inner_pid> [...]]

    本方案靠它在 cgroup.procs 全员里区分 user 容器内进程与基础设施：
    * NSpid 单列  → 进程只在 host PID namespace：wrapper 自己 / subprocess
                     shell / apptainer starter / fuse-overlayfs /
                     squashfuse_ll 等 host-only 进程，跳过。
    * NSpid 双列 + inner==1 → 容器 init (apptainer appinit)，跳过 ——
                              给容器 PID 1 发 SIGTERM 会触发整个容器生命
                              周期的 teardown，意外把 user 进程一起带走。
    * NSpid 双列 + inner!=1 → user 容器内进程（user bash / timeout / python
                              / workers 等），目标。

    跟现有 magnus marker 协议（.magnus_success / .magnus_result / .magnus_oom）
    不一样的是：本方案**零 user-script 改动、零 marker file、零协议表面**，
    完全靠 kernel procfs 既有字段 runtime 识别。systemd `KillMode=cgroup` /
    Kubernetes 优雅终止走同一思路。

    Apptainer 不同模式（liu rootless / zhu setuid）实测 NSpid 字段行为一致。
    单 PID kill 失败 / status 读失败任一步 fail-open continue，handler 不抛
    回 main；Phase 3 marker 决策仍能凭 _signaled[0] 触发。
    """
    procs_file = _cgroup_procs_path()
    if procs_file is None:
        return
    try:
        with open(procs_file) as f:
            cgroup_pids = []
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    cgroup_pids.append(int(raw))
                except ValueError:
                    continue
    except OSError:
        return

    for pid in cgroup_pids:
        try:
            with open(f"/proc/{{pid}}/status") as f:
                inner_pid = None
                for line in f:
                    if line.startswith("NSpid:"):
                        cols = line.split()
                        if len(cols) >= 3:
                            try:
                                inner_pid = int(cols[2])
                            except ValueError:
                                inner_pid = None
                        break
        except OSError:
            continue
        if inner_pid is None or inner_pid == 1:
            continue
        try:
            os.kill(pid, sig)
        except OSError:
            continue

def main():
    # SIGTERM 处理：wrapper SIGTERM handler 内枚举本 job cgroup，按
    # /proc/<host_pid>/status 的 NSpid 字段筛选出"在子 PID namespace 内、
    # 且不是容器 PID 1"的 user 容器内进程，对全员发 SIGTERM —— 详见
    # _signal_user_processes。user 视角下"我 entry_command 的所有进程都
    # 收到 SIGTERM"（main / fork 出的 workers / 任何中间命令 fork 的子进
    # 程），跨语言一致；apptainer starter / appinit / subprocess shell /
    # FUSE helpers 因 NSpid 单列或 inner==1 自然落在 targets 外。
    #
    # 为什么由 wrapper 自己 fan-out：signal_job 用 `scancel --signal=TERM --batch`
    # 把信号收紧到 batch step 的 shell（exec 替换后即 wrapper.py 本身）一个 PID，
    # 不让 SLURM 把 SIGTERM 广播给 cgroup 内的 apptainer starter / FUSE helpers
    # 等容器基础设施 —— 它们没装 handler，被 SIGTERM 默认 disposition 终止会
    # 拆掉 squashfs / overlay mount，user 进程访问内存映射时 SIGBUS。代价是
    # 信号到 user 进程这一程要由 wrapper 自己枚举 cgroup + NSpid 接力，
    # 语义等价 systemd `KillMode=cgroup` / Kubernetes 优雅终止。
    #
    # 分工：
    # * _on_sigterm 先置 _signaled[0] 再 forward —— forward 抛错（procfs
    #   读失败等）也不影响 Phase 3 fallback 能不能触发。
    # * _signal_user_processes 全程 fail-open，每个 os.kill 独立 try/except。
    # * 用户代码 signal.signal(SIGTERM, handler) 自己装 handler 做收尾、写
    #   .magnus_result 表达 partial state、sys.exit(0)。
    # * Phase 3 marker 决策：ret==0 写 marker；ret!=0 + _signaled + .magnus_result
    #   作 fallback 仍写 marker → magnus 标 SUCCESS。
    #
    # 上下游各装一层 `trap '' TERM` 防默认 disposition 误杀：
    # * sbatch script 入口 `trap '' TERM\\nexec wrapper.py` 把 SIG_IGN 通过
    #   exec 继承到 wrapper.py，覆盖 wrapper 启动到下面 signal.signal 装上
    #   handler 之间的几毫秒窗口。
    # * .magnus_user_script.sh 入口同样 `trap '' TERM`（Phase 1 渲染写入），
    #   fan-out 命中 user-script bash 时它 SIG_IGN 不被打死、外层 wait 不
    #   提前返回；SIG_IGN 通过 POSIX exec 继承给 user 进程，user 代码装的
    #   handler 自然覆盖回去触发。
    # 强终止走 kill_job 的 scancel --signal=KILL --full：SIGKILL 内核侧不可
    # ignore，由 proctrack 广播 cgroup 全员瞬间清场；详见
    # docs/internals/job-runtime.md "Signaling and Termination"。
    work_dir = {repr(job_working_table)}
    # ephemeral_dir：ephemeral overlay + apptainer tmp/cache 的落脚处。
    # 缺省等于 work_dir；server.ephemeral_root 配成独立快盘时落到那侧。
    ephemeral_dir = {repr(job_ephemeral_table)}
    os.makedirs(ephemeral_dir, exist_ok=True)
    _signaled = [False]
    def _on_sigterm(_signum, _frame):
        _signaled[0] = True
        try:
            _signal_user_processes(signal.SIGTERM)
        except Exception:
            pass
    signal.signal(signal.SIGTERM, _on_sigterm)

    repo_dir = {repr(repo_dir)}
    success_marker_path = {repr(success_marker_path)}
    sif_path = {repr(sif_path)}
    system_entry_command = {repr(system_entry_command)}
    user_token = {repr(user_token)}
    magnus_address = {repr(magnus_address)}
    job_id = {repr(job_id)}
    ephemeral_storage = {repr(ephemeral_storage)}
    apptainer_tmp_dir = os.path.join(ephemeral_dir, ".magnus_tmp")
    apptainer_cache_dir = os.path.join(ephemeral_dir, ".magnus_cache")

    user_cmd_str = {repr(entry_command)}
    if "sudo" in user_cmd_str:
        raise RuntimeError("Error: Not privileged.")
    effective_runner = {repr(effective_runner)}
    allow_root = {allow_root}
    if effective_runner == "root" and not allow_root:
        raise RuntimeError("Error: Not privileged.")

    # Start metrics sidecar (fail-open)
    metrics_dir = os.path.join(work_dir, "metrics")
    _stop_metrics = threading.Event()
    try:
        _metrics_thread = threading.Thread(
            target=_metrics_sidecar, args=(metrics_dir, _stop_metrics), daemon=True)
        _metrics_thread.start()
    except Exception:
        _metrics_thread = None

    # Phase 1: Prepare user script
    #
    # 入口 `trap '' TERM` 让跑 user entry 的 bash 把 SIGTERM 设为 SIG_IGN ——
    # wrapper 的 fan-out 会把 NSpid 双列 inner!=1 的 PID 都信号一遍，bash 自己
    # 也在其中；SIG_IGN 让它不被默认 disposition 杀，外层 wait 也就不会提前返
    # 回拖垮整条进程链。SIG_IGN 通过 POSIX exec 继承到 user 进程，user 代码
    # 用 signal.signal(SIGTERM, …) 装 handler 自然覆盖回去触发。
    user_script_path = os.path.join(work_dir, ".magnus_user_script.sh")
    with open(user_script_path, "w") as f:
        f.write("set -e\\n")
        f.write("trap '' TERM\\n")
        f.write("export HOME=$MAGNUS_HOME\\n")
        f.write(user_cmd_str)
        f.write("\\n")
    os.chmod(user_script_path, 0o755)

    # Phase 2: Execute with container
    overlay_path = os.path.join(ephemeral_dir, "ephemeral_overlay.img")
    try:
        os.makedirs(apptainer_tmp_dir, exist_ok=True)
        os.makedirs(apptainer_cache_dir, exist_ok=True)

        shell_cmd = f"""set -e
export {runtime_env_prefix}_MAGNUS_TOKEN={{user_token}}
export {runtime_env_prefix}_MAGNUS_ADDRESS={{magnus_address}}
export {runtime_env_prefix}_MAGNUS_JOB_ID={{job_id}}
export {runtime_env_prefix}_PYTHONUNBUFFERED=1
if [ -n "$CUDA_VISIBLE_DEVICES" ]; then
    export {runtime_env_prefix}_CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES"
fi

{{system_entry_command}}

export MAGNUS_HOME=${{{{MAGNUS_HOME:-/magnus}}}}
export {runtime_env_prefix}_MAGNUS_HOME=$MAGNUS_HOME
export {runtime_env_prefix}_MAGNUS_RESULT=$MAGNUS_HOME/workspace/.magnus_result
export {runtime_env_prefix}_MAGNUS_ACTION=$MAGNUS_HOME/workspace/.magnus_action
export {runtime_env_prefix}_MAGNUS_METRICS_DIR=$MAGNUS_HOME/workspace/metrics
export {runtime_env_prefix}_MAGNUS_METRICS_PROTO=metrics.v1
export {runtime_var_prefix}_TMPDIR={{apptainer_tmp_dir}}
export {runtime_var_prefix}_CACHEDIR={{apptainer_cache_dir}}
# 追加 workspace bind mount: host {{work_dir}} → 容器 $MAGNUS_HOME/workspace
# SDK 的 get_tmp_base() 依赖此 bind mount 判断运行环境（MAGNUS_HOME 存在 + workspace 目录存在 → 用 $MAGNUS_HOME/.tmp/ 中转文件，位于容器可写层而非 host 磁盘）
export {runtime_var_prefix}_BIND="${{{{{runtime_var_prefix}_BIND:+${{{{{runtime_var_prefix}_BIND}}}},}}}}{{work_dir}}:$MAGNUS_HOME/workspace"

MAGNUS_HOST_GATEWAY="${{{{MAGNUS_HOST_GATEWAY:-10.0.2.2}}}}"
for _var in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy; do
    eval _val="\\\\$$_var"
    if [ -n "$_val" ]; then
        if [ "${{{{MAGNUS_NET_MODE:-host}}}}" = "bridge" ]; then
            _val=$(echo "$_val" | sed "s/127\\\\.0\\\\.0\\\\.1/$MAGNUS_HOST_GATEWAY/g; s/localhost/$MAGNUS_HOST_GATEWAY/g")
        fi
        export "{runtime_env_prefix}_$_var=$_val"
    fi
done

# Detect setuid apptainer: check binary setuid bit (zero I/O, instant)
if [ -u "$(command -v {runtime_binary})" ]; then
    _setuid_apptainer=1
else
    _setuid_apptainer=
fi

# setuid apptainer: overlay root-owned (unreadable) + userns blocked → degrade to --contain
if [ -z "$_setuid_apptainer" ]; then
    APPTAINER_CONTAIN="${{{{MAGNUS_CONTAIN_LEVEL:-containall}}}}"
else
    APPTAINER_CONTAIN="${{{{MAGNUS_CONTAIN_LEVEL:-contain}}}}"
fi
# MAGNUS_CONTAIN_LEVEL=none → disable containment entirely (bare apptainer, like pre-overlay era)
[ "$APPTAINER_CONTAIN" = "none" ] && APPTAINER_CONTAIN=""

if [ -n "$APPTAINER_CONTAIN" ]; then
    APPTAINER_FLAGS="--nv --$APPTAINER_CONTAIN --no-mount tmp"
    if [ "${{{{MAGNUS_NO_OVERLAY:-0}}}}" != "1" ] && [ -z "$_setuid_apptainer" ]; then
        # An unclean prior run (power loss / SIGKILL) can leave a stale overlay image
        # whose finally-block cleanup never ran; apptainer refuses to create over an
        # existing image. The overlay is strictly per-run scratch, so any leftover is
        # garbage -- drop it before creating so a re-run isn't blocked by it.
        rm -f {{overlay_path}} {{overlay_path}}.ext3
        if ! {runtime_binary} overlay create --sparse --size {{_parse_size_to_mb(ephemeral_storage)}} {{overlay_path}} 2>/dev/null; then
            echo "[Magnus] WARNING: --sparse not supported (apptainer < 1.3?), falling back to dense overlay" >&2
            if ! {runtime_binary} overlay create --size {{_parse_size_to_mb(ephemeral_storage)}} {{overlay_path}}; then
                echo "[Magnus] ERROR: failed to create ephemeral overlay at {{overlay_path}}; the ephemeral disk may be full -- check free space on its volume" >&2
                exit 1
            fi
        fi
        APPTAINER_FLAGS="$APPTAINER_FLAGS --overlay {{overlay_path}}"
    else
        APPTAINER_FLAGS="$APPTAINER_FLAGS --writable-tmpfs"
        echo "[Magnus] WARNING: overlay skipped (${{{{_setuid_apptainer:+setuid apptainer}}}}${{{{MAGNUS_NO_OVERLAY:+MAGNUS_NO_OVERLAY=1}}}}), ephemeral_storage={{ephemeral_storage}} not enforced, using writable-tmpfs (RAM)" >&2
    fi
else
    APPTAINER_FLAGS="--nv"
    echo "[Magnus] WARNING: containment disabled (MAGNUS_CONTAIN_LEVEL=none), host filesystem visible, no write isolation" >&2
fi

# --containall / --contain isolates HOME, so --env HOME=... works cleanly.
# Without containment, Apptainer forbids overriding HOME via --env, so skip it.
if [ -n "$APPTAINER_CONTAIN" ]; then
    APPTAINER_FLAGS="$APPTAINER_FLAGS --env HOME=$MAGNUS_HOME"
fi

if [ "${{{{MAGNUS_FAKEROOT:-0}}}}" = "1" ]; then
    APPTAINER_FLAGS="$APPTAINER_FLAGS --fakeroot"
fi

APPTAINER_CMD="{runtime_binary} exec $APPTAINER_FLAGS --pwd $MAGNUS_HOME/workspace/repository {{sif_path}} bash $MAGNUS_HOME/workspace/.magnus_user_script.sh"

if [ "${{{{MAGNUS_NET_MODE:-host}}}}" = "bridge" ]; then
    ROOTLESSKIT_FLAGS="--net=slirp4netns --port-driver=builtin --publish $MAGNUS_PORT_MAP"
    if [ "${{{{MAGNUS_HOST_LOOPBACK:-0}}}}" != "1" ]; then
        ROOTLESSKIT_FLAGS="$ROOTLESSKIT_FLAGS --disable-host-loopback"
    fi
    rootlesskit $ROOTLESSKIT_FLAGS $APPTAINER_CMD
else
    $APPTAINER_CMD
fi
"""
        ret_code = subprocess.call(
            shell_cmd, shell=True, executable="/bin/bash",
        )

        # Stop metrics sidecar
        _stop_metrics.set()
        if _metrics_thread is not None:
            _metrics_thread.join(timeout=5)

        # OOM 探测：被 SIGTERM 转发后用户 handler 自行 exit non-zero 不是 OOM，
        # _signaled[0] 为 True 时跳过避免误写 .magnus_oom（误写会让 sync 端把
        # 用户主动 signal 的 job 在结果里展示成 OOM 杀）。
        if ret_code != 0 and not _signaled[0]:
            try:
                _oom, _oom_count = _check_oom()
                if _oom:
                    with open(os.path.join(work_dir, ".magnus_oom"), "w") as _omf:
                        _omf.write(f"oom_kill_count={{_oom_count}}\\n")
            except Exception:
                pass

        # Phase 3: Epilogue — success marker decision.
        #
        # 1) ret_code == 0：正常完成，写 marker、wrapper 退 0 → SLURM COMPLETED →
        #    _finalize_completed_job 读 marker → SUCCESS。典型路径：scancel TERM
        #    → handler forward 到 user leaves → user handler `sys.exit(0)` →
        #    timeout/bash/apptainer cascade ret==0 → wrapper 写 marker。
        # 2) ret_code != 0 + _signaled[0] + .magnus_result 存在：
        #    用户进程收到 SIGTERM 时 handler 处理完写了 .magnus_result 表达"已成功
        #    保存 partial state"，但 cascade 上来 ret 非 0（如 timeout 自死或 user
        #    handler 自己 exit non-zero）。写 marker 并 wrapper 自己退 0 让 SLURM
        #    优先收敛 COMPLETED。某些 SLURM 版本 / 配置下 batch step 收过 SIGTERM
        #    会被标 FAILED / CANCELLED，_sync_reality_slurm 的 FAILED / CANCELLED
        #    分支也先看 marker，构成 wrapper 与 sync 双层 defense-in-depth。
        # 3) 其它情况：不写 marker，原样透传 ret_code → SLURM FAILED → magnus FAILED。
        result_path = os.path.join(work_dir, ".magnus_result")
        if ret_code == 0:
            with open(success_marker_path, "w") as f:
                f.write("success")
            sys.exit(0)
        if _signaled[0] and os.path.exists(result_path):
            with open(success_marker_path, "w") as f:
                f.write("success-after-signal")
            sys.exit(0)
        sys.exit(ret_code)

    except Exception as error:
        print(f"Magnus Execution Error: {{error}}\\nTraceback: \\n{{traceback.format_exc()}}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Clean up overlay (.img 与 .ext3 两种 apptainer 落盘命名都尝试删)
        for _candidate in (overlay_path, overlay_path + ".ext3"):
            try:
                if os.path.exists(_candidate):
                    os.remove(_candidate)
            except Exception:
                pass

if __name__ == "__main__":
    main()
'''
