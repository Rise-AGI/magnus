# back_end/server/_slurm_manager/_transport.py
"""SLURM CLI 执行传输层：把"命令在哪执行"与"执行什么命令"解耦。

_LocalTransport 直接在本机 subprocess.run，行为与历史调用点一致（magnus 与
SLURM controller 同机）。抽出这一层后，可插入远程执行实现（例如经 SSH
ControlMaster 驱动外部站点的 SLURM）而无需改动 _ControlMixin / _ResourceQueryMixin。
"""
import subprocess
from typing import Dict, List, Optional


class _Transport:
    """SLURM CLI 命令的执行后端。

    run() 语义对齐 subprocess.run(..., capture_output=True, text=True)：返回带
    returncode / stdout / stderr 的 CompletedProcess；check=True 时非零退出抛
    CalledProcessError，与历史调用点一致。
    """

    def run(
        self,
        command: List[str],
        *,
        input: Optional[str] = None,
        check: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        raise NotImplementedError


class _LocalTransport(_Transport):
    """在本机直接执行 SLURM CLI。"""

    def run(
        self,
        command: List[str],
        *,
        input: Optional[str] = None,
        check: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            input = input,
            capture_output = True,
            text = True,
            check = check,
            env = env,
        )
