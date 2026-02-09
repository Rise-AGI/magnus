# back_end/server/_file_custody_manager.py
import os
import re
import uuid
import time
import shutil
import asyncio
import logging
import threading
import subprocess
from pathlib import Path
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass, field

from ._magnus_config import magnus_config
from ._resource_manager import _parse_size_string

logger = logging.getLogger(__name__)

FILE_SECRET_PREFIX = "magnus-secret:"


@dataclass
class CustodyEntry:
    entry_id: str
    file_dir: Path
    send_process: Optional[subprocess.Popen] = field(default=None, repr=False)
    file_secret: Optional[str] = None
    expires_at: float = 0.0


class FileCustodyManager:

    def __init__(self):
        config = magnus_config["server"]["file_custody"]
        self._max_size: int = _parse_size_string(config["max_size"])
        self._max_processes: int = config["max_processes"]
        self._default_ttl_minutes: int = config["default_ttl_minutes"]
        self._max_ttl_minutes: int = config["max_ttl_minutes"]

        self._storage_root = Path(magnus_config["server"]["root"]) / "file_custody"
        self._storage_root.mkdir(parents=True, exist_ok=True)

        # 启动时清理上次残留（重启后 croc send 进程已死，文件无用）
        for child in self._storage_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                logger.info(f"Cleaned up stale custody dir: {child.name}")

        self._entries: Dict[str, CustodyEntry] = {}
        # threading.Lock 而非 asyncio.Lock：watcher 线程、to_thread 线程池
        # 都会访问 _entries，asyncio.Lock 只保护协程间并发，对 OS 线程无效
        self._lock = threading.Lock()

    def _normalize_secret(self, file_secret: str) -> str:
        if file_secret.startswith(FILE_SECRET_PREFIX):
            return file_secret[len(FILE_SECRET_PREFIX):]
        return file_secret

    def _get_storage_size(self) -> int:
        total = 0
        for dirpath, _, filenames in os.walk(self._storage_root):
            for f in filenames:
                total += os.path.getsize(os.path.join(dirpath, f))
        return total

    def _croc_receive_sync(
        self,
        secret: str,
        out_dir: Path,
        timeout: float = 120.0,
    ) -> None:
        cmd = ["croc", "--yes", "--overwrite", "--out", str(out_dir)]
        env = os.environ.copy()
        env["CROC_SECRET"] = secret

        result = subprocess.run(
            cmd,
            env=env,
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            output = (result.stderr.strip() + "\n" + result.stdout.strip()).strip()
            raise RuntimeError(f"croc receive failed (exit {result.returncode}): {output}")

    def _croc_send_start(
        self,
        path: str,
    ) -> Tuple[subprocess.Popen, str]:
        cmd = ["croc", "send", path]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        secret_holder: List[str] = []
        ready = threading.Event()

        def monitor():
            assert proc.stdout is not None
            for line in proc.stdout:
                m = re.search(r"Code is:\s*(\S+)", line)
                if m:
                    secret_holder.append(m.group(1))
                    ready.set()

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

        if not ready.wait(timeout=15.0):
            # terminate → wait → kill → wait：必须 wait 收尸，否则产生僵尸进程
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise RuntimeError("croc send did not produce a secret in time")

        return proc, secret_holder[0]

    def custody_sync(
        self,
        file_secret: str,
        expire_minutes: Optional[int] = None,
    ) -> str:
        if expire_minutes is None:
            expire_minutes = self._default_ttl_minutes
        expire_minutes = min(expire_minutes, self._max_ttl_minutes)

        with self._lock:
            if len(self._entries) >= self._max_processes:
                raise RuntimeError(
                    f"File custody limit reached ({self._max_processes}). "
                    "Try again later or increase max_processes."
                )

        # 早停：当前已用空间超限则直接拒绝
        if self._get_storage_size() >= self._max_size:
            raise RuntimeError(
                "File custody storage full. "
                "Wait for entries to expire or increase max_size."
            )

        secret = self._normalize_secret(file_secret)
        entry_id = uuid.uuid4().hex[:12]
        file_dir = self._storage_root / entry_id
        file_dir.mkdir(parents=True, exist_ok=True)

        # try 块覆盖从 croc receive 到 watcher 启动的完整生命周期。
        # 任何一步失败都必须清理已分配的资源（进程、目录、字典条目）。
        send_proc: Optional[subprocess.Popen] = None
        try:
            # Step 1: croc receive
            self._croc_receive_sync(secret, file_dir)

            # 硬检查：receive 落盘后总量超限
            if self._get_storage_size() > self._max_size:
                raise RuntimeError(
                    "File custody storage exceeded after receive. "
                    "File removed."
                )

            # 找到接收到的文件
            received = list(file_dir.iterdir())
            if not received:
                raise RuntimeError("croc receive produced no files")

            target_path = str(received[0])

            # Step 2: croc send
            send_proc, new_secret = self._croc_send_start(target_path)

            entry = CustodyEntry(
                entry_id=entry_id,
                file_dir=file_dir,
                send_process=send_proc,
                file_secret=f"{FILE_SECRET_PREFIX}{new_secret}",
                expires_at=time.time() + expire_minutes * 60,
            )
            with self._lock:
                self._entries[entry_id] = entry
            self._start_exit_watcher(entry_id, send_proc)
        except Exception:
            # 清理可能已写入的字典条目。看起来多余但不是：如果 _entries 赋值
            # 成功但 _start_exit_watcher 失败（如线程资源耗尽），entry_id 已在
            # 字典中，却无 watcher 看管，且下方会杀掉 send_proc 并删除 file_dir，
            # 不 pop 就是一个指向已清理资源的 dirty entry。
            with self._lock:
                self._entries.pop(entry_id, None)
            if send_proc and send_proc.poll() is None:
                send_proc.terminate()
                try:
                    send_proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    send_proc.kill()
                    send_proc.wait()
            shutil.rmtree(file_dir, ignore_errors=True)
            raise

        logger.info(f"File custody created: {entry_id}, expire_minutes={expire_minutes}")
        assert entry.file_secret is not None
        return entry.file_secret

    async def custody_async(
        self,
        file_secret: str,
        expire_minutes: Optional[int] = None,
    ) -> str:
        return await asyncio.to_thread(self.custody_sync, file_secret, expire_minutes)

    def _cleanup_entry(self, entry: CustodyEntry) -> None:
        """同步阻塞方法。从 cleanup_loop 调用时必须用 asyncio.to_thread 包裹。"""
        if entry.send_process and entry.send_process.poll() is None:
            entry.send_process.terminate()
            try:
                entry.send_process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                entry.send_process.kill()
                entry.send_process.wait()
        if entry.file_dir.exists():
            shutil.rmtree(entry.file_dir, ignore_errors=True)

    def _start_exit_watcher(self, entry_id: str, proc: subprocess.Popen) -> None:
        def watcher():
            proc.wait()
            with self._lock:
                entry = self._entries.pop(entry_id, None)
            if entry:
                self._cleanup_entry(entry)
                logger.info(f"File custody completed (received): {entry_id}")

        threading.Thread(target=watcher, daemon=True).start()

    async def cleanup_loop(self) -> None:
        logger.info("File custody cleanup loop started.")
        while True:
            await asyncio.sleep(30)
            now = time.time()
            # 必须在锁内生成快照：watcher 线程随时可能 pop，直接迭代
            # _entries.items() 会触发 RuntimeError: dictionary changed size
            with self._lock:
                snapshot = list(self._entries.items())
            expired_ids = [
                eid for eid, entry in snapshot
                if now >= entry.expires_at
                or (entry.send_process is not None and entry.send_process.poll() is not None)
            ]
            for eid in expired_ids:
                with self._lock:
                    entry = self._entries.pop(eid, None)
                if entry is None:
                    continue
                # to_thread：_cleanup_entry 含 wait() 和 rmtree，直接调用会阻塞事件循环
                await asyncio.to_thread(self._cleanup_entry, entry)
                logger.info(f"File custody expired: {eid}")

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        logger.info(f"Shutting down file custody manager ({len(entries)} entries)...")
        for entry in entries:
            self._cleanup_entry(entry)


file_custody_manager = FileCustodyManager()
