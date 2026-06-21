# back_end/server/_size_utils.py
"""零依赖的 size 字符串解析与资源折算。被 _magnus_config / _slurm_manager / _resource_manager / _file_custody_manager / routers 共享。"""
import math
from typing import Optional


def _parse_size_string(size_str: str) -> int:
    """解析大小字符串，如 '200G', '1024M'，返回字节数"""
    size_str = size_str.strip().upper()
    units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * multiplier)
    return int(size_str)


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
        memory_demand_mb = _parse_size_string(memory_demand) // (1024 ** 2)
        cores = max(cores, math.ceil(memory_demand_mb / mem_per_cpu_mb))
    return cores
