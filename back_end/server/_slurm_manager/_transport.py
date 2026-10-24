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
import select
import signal
import subprocess
import tempfile
import threading
import time
from typing import Dict, List, Optional

from ._ssh_auto_connect import ensure_control_master, remaining_timeout


def _stop_transfer_process(
    process: subprocess.Popen,
) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout = subprocess.DEVNULL,
                stderr = subprocess.DEVNULL,
                timeout = 5,
            )
        finally:
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def _run_scp(
    command: List[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess:
    # A multiplexing master can retain stderr after its client is killed. Do
    # not drain a pipe to EOF on timeout, and reap scp's local SSH child too.
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(
            command,
            stdin = subprocess.DEVNULL,
            stdout = subprocess.DEVNULL,
            stderr = errors,
            start_new_session = os.name != "nt",
        ) as process:
            try:
                process.wait(timeout=timeout)
            except BaseException:
                _stop_transfer_process(process)
                raise
        errors.seek(0, os.SEEK_END)
        size = errors.tell()
        errors.seek(max(0, size - 65536))
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            "",
            errors.read().decode("utf-8", errors="replace"),
        )


def _stream_file_to_command(
    command: List[str],
    local_path: str,
    *,
    prefix: bytes,
    timeout: float,
) -> subprocess.CompletedProcess:
    """Send a small header and a binary file without buffering the file in RAM.

    Separate readers drain bounded output tails while the writer feeds stdin;
    this also supports Windows anonymous pipes and early receiver cache hits.
    """
    with open(local_path, "rb") as source:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session = os.name != "nt",
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        write_errors = []
        stop = threading.Event()
        stdout_tail = bytearray()
        stderr_tail = bytearray()

        def write_input():
            try:
                block = prefix
                while not stop.is_set() and process.poll() is None:
                    pending = memoryview(block)
                    while pending and not stop.is_set() and process.poll() is None:
                        try:
                            pending = pending[os.write(process.stdin.fileno(), pending):]
                        except BlockingIOError:
                            if os.name == "nt":
                                stop.wait(0.001)
                            else:
                                select.select([], [process.stdin], [], 0.1)
                    block = source.read(1024 * 1024)
                    if not block:
                        break
            except BrokenPipeError:
                pass
            except OSError as error:
                write_errors.append(error)
            finally:
                process.stdin.close()

        def drain(
            stream,
            tail,
        ):
            with stream:
                while not stop.is_set():
                    try:
                        block = os.read(stream.fileno(), 8192)
                    except BlockingIOError:
                        if process.poll() is not None:
                            break
                        stop.wait(0.02)
                        continue
                    if not block:
                        break
                    tail.extend(block)
                    del tail[:-65536]

        threads = [
            threading.Thread(target=write_input),
            threading.Thread(target=drain, args=(process.stdout, stdout_tail)),
            threading.Thread(target=drain, args=(process.stderr, stderr_tail)),
        ]
        try:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            for thread in threads:
                thread.start()
            process.wait(timeout=timeout)
        except BaseException:
            stop.set()
            _stop_transfer_process(process)
            raise
        finally:
            for thread in threads:
                if thread.ident is not None:
                    thread.join()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        if write_errors and process.returncode == 0:
            # Windows may report a closed receiver pipe as EINVAL, not EPIPE.
            error = write_errors[0]
            if not (os.name == "nt" and error.errno == 22):
                raise error
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            bytes(stdout_tail).decode("utf-8", errors="replace"),
            bytes(stderr_tail).decode("utf-8", errors="replace"),
        )


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
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def push(
        self,
        local_path: str,
        remote_path: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        """把本机 local_path（文件或目录树）搬到执行端的 remote_path。"""
        raise NotImplementedError

    def run_with_file_input(
        self,
        command: List[str],
        local_path: str,
        *,
        prefix: bytes = b"",
        timeout: float,
    ) -> subprocess.CompletedProcess:
        """Run a command receiving a binary file after an optional stdin header."""
        raise NotImplementedError

    def fetch(
        self,
        remote_path: str,
        local_path: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
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
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            input = input,
            capture_output = True,
            text = True,
            check = check,
            env = env,
            timeout = timeout,
        )

    def push(
        self,
        local_path: str,
        remote_path: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        # 本机执行：调用方把远端路径映射回同一个本地路径，工作区本就在原地，无需搬运。
        return

    def run_with_file_input(
        self,
        command: List[str],
        local_path: str,
        *,
        prefix: bytes = b"",
        timeout: float,
    ) -> subprocess.CompletedProcess:
        return _stream_file_to_command(
            command,
            local_path,
            prefix=prefix,
            timeout=timeout,
        )

    def fetch(
        self,
        remote_path: str,
        local_path: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
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
        auto_connect: Optional[Dict] = None,
        transfer_timeout_seconds: float = 900,
    ) -> None:
        self._control_path = control_path
        self._host = host
        self._user = user
        # auto_connect 为 None（缺省）时本类语义与历史完全一致：只骑既有 socket，
        # BatchMode=yes 在 socket 失效时快失败、不自建。配了 auto_connect 才在每次操作前
        # 确保 socket 活着、失效时用密码 + TOTP 无人值守重建。
        self._auto_connect = auto_connect
        self.transfer_timeout_seconds = transfer_timeout_seconds

    def _ensure_socket(
        self,
        *,
        deadline: Optional[float] = None,
    ) -> None:
        """配了 auto_connect 时，操作前确保 master socket 活着（失效则单飞重建）。"""
        if self._auto_connect is not None:
            ensure_control_master(
                self._control_path,
                self._host,
                self._user,
                self._auto_connect,
                deadline = deadline,
            )

    def run(
        self,
        command: List[str],
        *,
        input: Optional[str] = None,
        check: bool = False,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess:
        deadline = time.monotonic() + timeout if timeout is not None else None
        self._ensure_socket(deadline=deadline)
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
            timeout = remaining_timeout(deadline),
        )

    def run_with_file_input(
        self,
        command: List[str],
        local_path: str,
        *,
        prefix: bytes = b"",
        timeout: float,
    ) -> subprocess.CompletedProcess:
        deadline = time.monotonic() + timeout
        self._ensure_socket(deadline=deadline)
        ssh_command = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-S",
            self._control_path,
            f"{self._user}@{self._host}",
            " ".join(shlex.quote(token) for token in command),
        ]
        return _stream_file_to_command(
            ssh_command,
            local_path,
            prefix=prefix,
            timeout=remaining_timeout(deadline),
        )

    def push(
        self,
        local_path: str,
        remote_path: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        # 先在远端建好 remote_path 的父目录，scp 才能把条目落在 remote_path 本身
        # （scp 不会替你建中间目录）。父目录经 run() 骑同一条 socket 创建。
        remote_parent = posixpath.dirname(remote_path)
        deadline = time.monotonic() + (self.transfer_timeout_seconds if timeout is None else timeout)
        if remote_parent:
            made = self.run(
                ["mkdir", "-p", remote_parent],
                timeout = remaining_timeout(deadline, 30),
            )
            if made.returncode != 0:
                raise RuntimeError(
                    f"failed to create remote dir {remote_parent} "
                    f"(rc={made.returncode}): {made.stderr.strip()}"
                )
        self._scp(
            local_path,
            self._remote_endpoint(remote_path),
            timeout = remaining_timeout(deadline),
        )

    def fetch(
        self,
        remote_path: str,
        local_path: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        local_parent = os.path.dirname(local_path)
        if local_parent:
            os.makedirs(local_parent, exist_ok=True)
        self._scp(
            self._remote_endpoint(remote_path),
            local_path,
            timeout = timeout,
        )

    def _remote_endpoint(self, remote_path: str) -> str:
        """拼 scp 的 `user@host:path` 远端端点；path 段按字面传、不做 shell 引用。

        OpenSSH ≥ 9 的 scp 默认走 SFTP 协议：远端 path 是协议里的一个字面字段，不经
        远端登录 shell 二次解析，因此含空格 / 特殊字符的文件名（如用户代码写入的
        metrics 文件，名字不受控）原样传即正确。对 path 做 shell 引用反而会把引号当成
        文件名的字面字符、找不到文件 —— 引用只在传统 SCP 协议（scp -O 强制回退）下才需
        要，本实现不用 -O。"""
        return f"{self._user}@{self._host}:{remote_path}"

    def _scp(
        self,
        source: str,
        destination: str,
        *,
        timeout: Optional[float] = None,
    ) -> None:
        """骑 ControlMaster socket 跑一次 scp。-r 兼容文件与目录树；socket 已建好，
        BatchMode=yes 让 socket 失效时快失败而非挂起等密码（与 run() 一致）。"""
        deadline = time.monotonic() + (self.transfer_timeout_seconds if timeout is None else timeout)
        self._ensure_socket(deadline=deadline)
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
        try:
            result = _run_scp(
                scp_command,
                timeout = remaining_timeout(deadline),
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError("SCP transfer exceeded its time budget") from None
        if result.returncode != 0:
            # Paths and server errors can contain custody capability tokens.
            raise RuntimeError(f"SCP transfer failed (exit {result.returncode})")


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
            auto_connect = ssh.get("auto_connect"),
            transfer_timeout_seconds = ssh.get("transfer_timeout_seconds", 900),
        )
    return _LocalTransport()
