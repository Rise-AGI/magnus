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
import posixpath
import shlex
import subprocess
from typing import Dict, List, Optional


class _Transport:
    """SLURM CLI 命令 + 跨界文件搬运的执行后端。

    run() 语义对齐 subprocess.run(..., capture_output=True, text=True)：返回带
    returncode / stdout / stderr 的 CompletedProcess；check=True 时非零退出抛
    CalledProcessError，与历史调用点一致。

    push() / fetch() 把 job 工作区在本机与执行端之间搬运。is_remote 标识执行端是否
    与 magnus 异机：本机执行（_LocalTransport）时为 False，调用方据此把"远端路径"
    收敛回同一个本地路径、push/fetch 退化成 no-op，搬运链路对本机站点零参与。
    """

    is_remote: bool = False

    def run(
        self,
        command: List[str],
        *,
        input: Optional[str] = None,
        check: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def push(self, local_path: str, remote_path: str) -> None:
        """把本机 local_path（文件或目录树）搬到执行端的 remote_path。"""
        raise NotImplementedError

    def fetch(self, remote_path: str, local_path: str) -> None:
        """把执行端 remote_path（文件或目录树）搬回本机 local_path。"""
        raise NotImplementedError


class _LocalTransport(_Transport):
    """在本机直接执行 SLURM CLI。"""

    is_remote = False

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

    def push(self, local_path: str, remote_path: str) -> None:
        # 本机执行：调用方把远端路径映射回同一个本地路径，工作区本就在原地，无需搬运。
        return

    def fetch(self, remote_path: str, local_path: str) -> None:
        # 同 push：本机执行端与 magnus 同机同盘，没有要搬回的东西。
        return


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

    is_remote = True

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

    def push(self, local_path: str, remote_path: str) -> None:
        # 先在远端建好 remote_path 的父目录，scp 才能把条目落在 remote_path 本身
        # （scp 不会替你建中间目录）。父目录经 run() 骑同一条 socket 创建。
        remote_parent = posixpath.dirname(remote_path)
        if remote_parent:
            made = self.run(["mkdir", "-p", remote_parent])
            if made.returncode != 0:
                raise RuntimeError(
                    f"failed to create remote dir {remote_parent} "
                    f"(rc={made.returncode}): {made.stderr.strip()}"
                )
        self._scp(local_path, self._remote_endpoint(remote_path))

    def fetch(self, remote_path: str, local_path: str) -> None:
        local_parent = os.path.dirname(local_path)
        if local_parent:
            os.makedirs(local_parent, exist_ok=True)
        self._scp(self._remote_endpoint(remote_path), local_path)

    def _remote_endpoint(self, remote_path: str) -> str:
        """拼 scp 的 `user@host:path` 远端端点；path 段按字面传、不做 shell 引用。

        OpenSSH ≥ 9 的 scp 默认走 SFTP 协议：远端 path 是协议里的一个字面字段，不经
        远端登录 shell 二次解析，因此含空格 / 特殊字符的文件名（如用户代码写入的
        metrics 文件，名字不受控）原样传即正确。对 path 做 shell 引用反而会把引号当成
        文件名的字面字符、找不到文件 —— 引用只在传统 SCP 协议（scp -O 强制回退）下才需
        要，本实现不用 -O。"""
        return f"{self._user}@{self._host}:{remote_path}"

    def _scp(self, source: str, destination: str) -> None:
        """骑 ControlMaster socket 跑一次 scp。-r 兼容文件与目录树；socket 已建好，
        BatchMode=yes 让 socket 失效时快失败而非挂起等密码（与 run() 一致）。"""
        scp_command = [
            "scp",
            "-r",
            "-q",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ControlPath={self._control_path}",
            source,
            destination,
        ]
        result = subprocess.run(
            scp_command,
            capture_output = True,
            text = True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"scp {source} -> {destination} failed "
                f"(rc={result.returncode}): {result.stderr.strip()}"
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
