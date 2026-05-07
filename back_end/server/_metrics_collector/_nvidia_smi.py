# back_end/server/_metrics_collector/_nvidia_smi.py
"""单次 nvidia-smi 全节点 GPU 采样。返回 {gpu_idx: (util_pct, mem_used_bytes)}。"""
from __future__ import annotations

import subprocess
from typing import Dict, Optional, Tuple


_NVIDIA_SMI_TIMEOUT = 10


def _sample_nvidia_smi() -> Optional[Dict[str, Tuple[float, float]]]:
    """Return {gpu_idx: (util_pct, mem_used_bytes)} or None on failure."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            timeout=_NVIDIA_SMI_TIMEOUT, text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    result: Dict[str, Tuple[float, float]] = {}
    for row in out.strip().split("\n"):
        parts = [p.strip() for p in row.split(",")]
        if len(parts) != 3:
            continue
        try:
            idx = parts[0]
            util = float(parts[1])
            mem_mib = float(parts[2])
        except ValueError:
            continue
        result[idx] = (util, mem_mib * 1048576)
    return result
