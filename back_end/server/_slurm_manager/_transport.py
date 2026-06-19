# back_end/server/_slurm_manager/_transport.py
"""SLURM CLI 执行传输层：把"命令在哪执行"与"执行什么命令"解耦。

_LocalTransport 直接在本机 subprocess.run，行为与历史调用点一致（magnus 与
SLURM controller 同机）。_SshControlMasterTransport 经一条预先建立的 SSH
ControlMaster socket 把同样的命令送到远端站点执行（magnus 作为无特权租户驱动外部
超算的 SLURM）。两者都满足 _Transport 接口，_ControlMixin / _ResourceQueryMixin
对在哪执行无感知。

本模块刻意不 import magnus_config —— 保持成一个只依赖标准库的叶子。具体选哪种
transport 由 build_transport() 按传入的 transport 配置块决定，配置的读取留给调用方
（_manager.py）。
"""
import os
import shlex
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


class _SshControlMasterTransport(_Transport):
    """经一条已建立的 SSH ControlMaster socket 在远端站点执行 SLURM CLI。

    复用人肉一次 2FA 建好的长连 master socket（control_path），后续命令骑这条
    socket 不再认证。run() 的可观察语义与 _LocalTransport 对齐（返回带
    returncode / stdout / stderr 的 CompletedProcess，check=True 时非零退出抛
    CalledProcessError），差异只在命令实际在远端 shell 执行：

    - 命令逐 token shlex.quote 后拼成单串交给 ssh，远端 shell 重新解析还原出原
      argv（避免带空格 / 特殊字符的参数在远端被二次切词）。
    - 环境注入：只把调用方相对本进程 os.environ **新增或改写** 的变量（即
      submit / kill 路径显式附加的 MAGNUS_RUNNER / MAGNUS_TOKEN）经
      `env KEY=VAL ... cmd` 前缀带到远端。本进程自身的 os.environ（本机 PATH 等）
      不外泄 —— 远端登录 shell 自带正确的 PATH / module 环境，用本机环境覆盖远端
      反而错（两侧路径不同）。env 为 None（只读查询）时不加任何前缀。
    - stdin：input 非 None 时（如 sbatch 从 stdin 读 batch script）转发到远端命令；
      为 None 时加 -n 让 ssh 从 /dev/null 取 stdin，杜绝吞掉本进程 stdin（历史踩坑：
      裸 ssh 不加 -n 会吃掉后续输入看似卡死）。
    - BatchMode=yes：socket 失效时 ssh 快速失败（退 255）而非挂起等密码；socket
      需人肉 OTP 重建，这里只负责骑既有 socket，建不了不假装能建。
    """

    def __init__(
        self,
        control_path: str,
        host: str,
        user: str,
    ) -> None:
        self._control_path = control_path
        self._host = host
        self._user = user

    def run(
        self,
        command: List[str],
        *,
        input: Optional[str] = None,
        check: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        remote_tokens: List[str] = []
        if env is not None:
            injected = [
                f"{key}={value}"
                for key, value in env.items()
                if os.environ.get(key) != value
            ]
            if injected:
                remote_tokens.append("env")
                remote_tokens.extend(injected)
        remote_tokens.extend(command)
        remote_command = " ".join(shlex.quote(token) for token in remote_tokens)

        ssh_command = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-S",
            self._control_path,
        ]
        if input is None:
            ssh_command.append("-n")
        ssh_command.append(f"{self._user}@{self._host}")
        ssh_command.append(remote_command)

        return subprocess.run(
            ssh_command,
            input = input,
            capture_output = True,
            text = True,
            check = check,
        )


def build_transport(transport_config: Dict) -> _Transport:
    """按 transport 配置块构造执行后端。

    mode='ssh' 时骑 ControlMaster socket 驱动远端站点；其余（含缺省 'local'）回落
    本机 subprocess。配置已在 _magnus_config 启动校验过结构，这里直接索引。
    """
    mode = transport_config.get("mode", "local")
    if mode == "ssh":
        ssh = transport_config["ssh"]
        return _SshControlMasterTransport(
            control_path = ssh["control_path"],
            host = ssh["host"],
            user = ssh["user"],
        )
    return _LocalTransport()
