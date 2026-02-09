# sdks/python/src/magnus/file_transfer.py
"""
FileSecret 文件传输支持模块。

通过 croc 实现本地文件到远程蓝图执行环境的传输。
SDK 端启动 croc send，蓝图执行时通过 croc receive 接收。
"""
import atexit
import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

FILE_SECRET_PREFIX = "magnus-secret:"


@dataclass
class CrocSender:
    """管理一个 croc send 进程"""
    path: str
    secret: Optional[str] = None
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    ready_event: threading.Event = field(default_factory=threading.Event, repr=False)
    error: Optional[str] = None

    def start(self) -> bool:
        """启动 croc send 进程，从输出中解析 croc 生成的 code，返回是否成功"""
        if not Path(self.path).exists():
            self.error = f"Path does not exist: {self.path}"
            return False

        cmd = ["croc", "send", self.path]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            self.error = (
                "croc is not installed or not in PATH.\n"
                "         Install via conda: conda install -c conda-forge croc"
            )
            return False
        except PermissionError:
            self.error = (
                "croc is not executable.\n"
                "         Install via conda: conda install -c conda-forge croc"
            )
            return False
        except Exception as e:
            self.error = f"Failed to start croc: {e}"
            return False

        def monitor():
            assert self.process is not None
            assert self.process.stdout is not None
            for line in self.process.stdout:
                # croc 输出格式: "Code is: xxxx-yyyy-zzzz"
                m = re.search(r"Code is:\s*(\S+)", line)
                if m:
                    self.secret = m.group(1)
                    self.ready_event.set()

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

        if not self.ready_event.wait(timeout=10.0):
            self.error = "croc send did not become ready in time"
            self.stop()
            return False

        return True

    def stop(self):
        """终止 croc send 进程"""
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()

    @property
    def file_secret(self) -> str:
        """返回 FileSecret 格式的值"""
        return f"{FILE_SECRET_PREFIX}{self.secret}"


class FileTransferManager:
    """
    管理多个文件传输会话。

    确保进程资源不泄露：
    - 使用 atexit 注册清理函数
    - 提供显式的 cleanup 方法
    - 支持 context manager 模式
    """

    def __init__(self):
        self._senders: List[CrocSender] = []
        self._lock = threading.Lock()
        atexit.register(self.cleanup)

    def prepare_file_secrets(
        self,
        params: Dict[str, str],
        file_secret_keys: List[str],
    ) -> Tuple[Dict[str, str], List[str]]:
        """
        处理参数中的 FileSecret 类型字段。

        对于 file_secret_keys 中的每个 key：
        - 如果值已经是 magnus-secret: 格式，保持不变
        - 如果值是文件路径，启动 croc send 并替换为 secret 格式

        返回：(处理后的参数字典, 错误列表)
        """
        result = params.copy()
        errors: List[str] = []

        for key in file_secret_keys:
            if key not in params:
                continue

            value = params[key]

            if value.startswith(FILE_SECRET_PREFIX):
                continue

            sender = CrocSender(path=value)

            if not sender.start():
                errors.append(f"[{key}] {sender.error}")
                continue

            assert sender.secret is not None

            with self._lock:
                self._senders.append(sender)

            result[key] = sender.file_secret

        return result, errors

    def cleanup(self):
        """清理所有 croc send 进程"""
        with self._lock:
            for sender in self._senders:
                sender.stop()
            self._senders.clear()

    def __enter__(self) -> "FileTransferManager":
        return self

    def __exit__(self, *args) -> None:
        self.cleanup()


_global_manager: Optional[FileTransferManager] = None


def get_file_transfer_manager() -> FileTransferManager:
    """获取全局文件传输管理器"""
    global _global_manager
    if _global_manager is None:
        _global_manager = FileTransferManager()
    return _global_manager


def is_file_secret(value: str) -> bool:
    """检查值是否是 FileSecret 格式"""
    return value.startswith(FILE_SECRET_PREFIX)


def normalize_secret(file_secret: str) -> str:
    """去掉 magnus-secret: 前缀（如果有），返回纯 croc secret"""
    if file_secret.startswith(FILE_SECRET_PREFIX):
        return file_secret[len(FILE_SECRET_PREFIX):]
    return file_secret
