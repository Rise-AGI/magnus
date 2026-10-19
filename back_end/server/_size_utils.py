# back_end/server/_size_utils.py
"""零依赖的 size 字符串解析与资源折算。被 _magnus_config / _slurm_manager / _resource_manager / _file_custody_manager / routers 共享。"""
from decimal import Decimal, DecimalException, ROUND_CEILING
from typing import Optional


def _parse_size_string(size_str: str) -> int:
    """解析大小字符串，如 '200G', '1024M'，返回字节数"""
    size_str = size_str.strip().upper()
    units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * multiplier)
    return int(size_str)


def parse_memory_bytes(
    memory: str,
) -> int:
    """SLURM memory quantities use MiB when no suffix is supplied.

    Keep this separate from storage parsing, whose unsuffixed unit is bytes.
    https://slurm.schedmd.com/sbatch.html#OPT_mem
    Zero remains the all-node-memory sentinel; callers must not count it as a
    zero-byte allocation. Existing explicit size suffixes keep binary units.
    """
    raw = memory.strip()
    if not raw:
        raise ValueError("Memory demand must not be empty")
    try:
        units = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
        suffix = raw[-1].upper()
        number = Decimal(raw[:-1] if suffix in units else raw)
        value = number * units.get(suffix, 1024**2)
        if not value.is_finite() or value < 0 or value > 2**63 - 1:
            raise ValueError("Memory demand is outside the supported range")
        # A positive fraction must never truncate to the --mem=0 sentinel.
        return int(value.to_integral_value(rounding=ROUND_CEILING))
    except (DecimalException, OverflowError) as error:
        raise ValueError("Memory demand must be a finite size") from error


def effective_cpu_count_per_cpu(
    cpu_count: Optional[int],
    memory_demand: Optional[str],
    mem_per_cpu_mb: int,
)-> int:
    """per_cpu 内存模式下的有效核数 = max(显式核数, ceil(内存需求 / 每核内存))。

    禁用 --mem 的共享集群（execution.slurm.mem_mode='per_cpu'）里 SLURM 按 DefMemPerCPU
    给每核固定 mem_per_cpu_mb 内存，核数足够即隐式满足内存需求；memory_demand 仅在内存
    需求超过 cpu_count 隐含内存时上调核数。memory_demand 为 None 视作无内存约束，
    cpu_count 非正视作 0。

    被 _slurm_manager.submit_job_simple（决定 --cpus-per-task）与 _magnus_config.
    normalize_per_cpu_resources（归一化 DB/UI 展示）共享同一口径，避免两处折算漂移。
    """
    cores = cpu_count if (cpu_count is not None and cpu_count > 0) else 0
    if memory_demand is not None and mem_per_cpu_mb > 0:
        memory_bytes = parse_memory_bytes(memory_demand)
        per_cpu_bytes = mem_per_cpu_mb * 1024**2
        # Round only once: dropping fractional MiB before division can allocate
        # one CPU too few (4000.5M at 4000M/CPU requires two CPUs).
        cores = max(cores, (memory_bytes + per_cpu_bytes - 1) // per_cpu_bytes)
    return cores
