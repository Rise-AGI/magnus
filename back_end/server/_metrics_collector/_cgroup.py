# back_end/server/_metrics_collector/_cgroup.py
"""Host-side cgroup parsing helpers.

Mirror of the parsing logic embedded in wrapper.py's _metrics_sidecar / _check_oom
(see `_build_wrapper_content` in `_scheduler/_wrapper_template.py`). Wrapper.py is
built by f-string templating and cannot import from this module — keep the two
impls behaviorally equivalent.

Layout:
- _is_pure_v2:                pure-v2 vs hybrid detection
- _read_proc_cgroup_for_pid:  v2_rel + v1_memory_rel parsing
- _read_v1_cpu_rel:           v1 cpuacct/cpu controller path
- _read_cpu_usage_usec:       cumulative CPU usage in microseconds
- _read_memory_used_bytes:    memory usage minus reclaimable page cache
- _read_allocated_cpus:       cpu quota in fractional cores
"""
from __future__ import annotations

from typing import Optional, Tuple


def _is_pure_v2() -> bool:
    """True when /sys/fs/cgroup is the cgroup v2 mount AND no v1 cgroup is mounted.

    On hybrid systems v2 is mounted elsewhere (e.g. /sys/fs/cgroup/unified) and
    SLURM / systemd typically place tasks under v1 controllers; the "0::" line in
    /proc/<pid>/cgroup there points to a v2 path that isn't authoritative for the
    task's cpu/memory accounting. Caller must read v1 controller paths in that case.
    """
    v2_at: Optional[str] = None
    has_v1 = False
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                if parts[2] == "cgroup2":
                    v2_at = parts[1]
                elif parts[2] == "cgroup":
                    has_v1 = True
    except OSError:
        return True
    return v2_at == "/sys/fs/cgroup" and not has_v1


def _read_proc_cgroup_for_pid(pid: int) -> Tuple[Optional[str], Optional[str]]:
    """Return (v2_rel, v1_memory_rel) from /proc/<pid>/cgroup. Either may be None.

    Both can be populated on hybrid systems; caller picks based on _is_pure_v2().
    """
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
    except OSError:
        return None, None

    v2_rel: Optional[str] = None
    v1_mem: Optional[str] = None
    for line in lines:
        if line.startswith("0::"):
            v2_rel = line[3:]
        else:
            cols = line.split(":", 2)
            if len(cols) != 3:
                continue
            controllers = cols[1].split(",")
            if "memory" in controllers and v1_mem is None:
                v1_mem = cols[2]

    return v2_rel, v1_mem


def _read_v1_cpu_rel(pid: int) -> Optional[str]:
    """Return cpuacct (preferred) or cpu controller relative path for v1; None on v2/error."""
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("0::"):
                    continue
                cols = line.split(":", 2)
                if len(cols) != 3:
                    continue
                controllers = cols[1].split(",")
                if "cpuacct" in controllers:
                    return cols[2]
    except OSError:
        return None
    # Fallback: any line with "cpu"
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("0::"):
                    continue
                cols = line.split(":", 2)
                if len(cols) != 3:
                    continue
                controllers = cols[1].split(",")
                if "cpu" in controllers:
                    return cols[2]
    except OSError:
        return None
    return None


def _read_cpu_usage_usec(pid: int) -> Optional[int]:
    """Return cumulative cpu usage in microseconds for the cgroup containing pid."""
    if _is_pure_v2():
        v2_rel, _ = _read_proc_cgroup_for_pid(pid)
        if v2_rel is None:
            return None
        path = "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/cpu.stat"
        try:
            with open(path, "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "usage_usec":
                        return int(parts[1])
        except (OSError, ValueError):
            return None
        return None

    cpu_rel = _read_v1_cpu_rel(pid)
    if cpu_rel is None:
        return None
    # cpuacct.usage is in nanoseconds
    path = "/sys/fs/cgroup/cpuacct" + cpu_rel.rstrip("/") + "/cpuacct.usage"
    try:
        with open(path, "r") as f:
            ns = int(f.read().strip())
        return ns // 1000
    except (OSError, ValueError):
        return None


def _read_memory_used_bytes(pid: int) -> Optional[int]:
    """Return memory usage in bytes for the cgroup containing pid, minus
    reclaimable file-backed page cache. Mirrors `docker stats` semantics so
    IO-heavy tasks don't appear to be near their memory limit just from cache."""
    v2_rel, v1_mem = _read_proc_cgroup_for_pid(pid)
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
        with open(cgroup_dir + "/" + usage_file, "r") as f:
            usage = int(f.read().strip())
    except (OSError, ValueError):
        return None

    inactive_file = 0
    try:
        with open(cgroup_dir + "/memory.stat", "r") as f:
            for line in f:
                if line.startswith("inactive_file "):
                    inactive_file = int(line.split()[1])
                    break
    except (OSError, ValueError):
        pass
    return max(0, usage - inactive_file)


def _read_allocated_cpus(pid: int) -> Optional[float]:
    """Read CPU quota allocated to the cgroup, in fractional cores. None if unlimited/error."""
    if _is_pure_v2():
        v2_rel, _ = _read_proc_cgroup_for_pid(pid)
        if v2_rel is None:
            return None
        path = "/sys/fs/cgroup" + v2_rel.rstrip("/") + "/cpu.max"
        try:
            with open(path, "r") as f:
                content = f.read().strip().split()
            if len(content) != 2:
                return None
            quota_s, period_s = content
            if quota_s == "max":
                return None
            quota = int(quota_s)
            period = int(period_s)
            if period <= 0 or quota <= 0:
                return None
            return quota / period
        except (OSError, ValueError):
            return None

    cpu_rel = _read_v1_cpu_rel(pid)
    if cpu_rel is None:
        return None
    base = "/sys/fs/cgroup/cpu" + cpu_rel.rstrip("/")
    try:
        with open(base + "/cpu.cfs_quota_us", "r") as f:
            quota = int(f.read().strip())
        with open(base + "/cpu.cfs_period_us", "r") as f:
            period = int(f.read().strip())
        if quota <= 0 or period <= 0:
            return None
        return quota / period
    except (OSError, ValueError):
        return None
