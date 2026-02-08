# sdks/python/src/magnus/croc_tools.py
"""
Croc 文件传输工具封装。

提供简洁的 API 用于通过 croc 接收文件。
"""
import os
import sys
import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from .file_transfer import FILE_SECRET_PREFIX


def _croc_not_found_error() -> Exception:
    from . import MagnusError
    return MagnusError(
        "croc is not installed or not in PATH.\n"
        "         Install via conda: conda install -c conda-forge croc"
    )


def _croc_error(msg: str) -> Exception:
    from . import MagnusError
    return MagnusError(msg)


def download_file(
    file_secret: str,
    target_path: str,
    timeout: Optional[float] = None,
    overwrite: bool = True,
) -> Path:
    """
    通过 croc 接收文件。

    :param file_secret: croc secret，可以带或不带 "magnus-secret:" 前缀
    :param target_path: 目标路径（文件或目录）
    :param timeout: 超时时间（秒），None 表示无限等待
    :param overwrite: 是否覆盖已存在的文件
    :return: 下载后的文件路径
    """
    secret = _normalize_secret(file_secret)
    target = Path(target_path)

    if target.is_dir():
        out_dir = target
    else:
        out_dir = target.parent
        out_dir.mkdir(parents=True, exist_ok=True)

    cmd, env = _build_croc_receive(secret, out_dir, overwrite)

    try:
        result = subprocess.run(
            cmd,
            env=env,
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise _croc_not_found_error()
    except subprocess.TimeoutExpired:
        raise _croc_error(f"croc download timed out after {timeout}s")

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise _croc_error(f"croc failed: {stderr}")

    return target


async def download_file_async(
    file_secret: str,
    target_path: str,
    timeout: Optional[float] = None,
    overwrite: bool = True,
) -> Path:
    """
    异步版本的 download_file。

    :param file_secret: croc secret，可以带或不带 "magnus-secret:" 前缀
    :param target_path: 目标路径（文件或目录）
    :param timeout: 超时时间（秒），None 表示无限等待
    :param overwrite: 是否覆盖已存在的文件
    :return: 下载后的文件路径
    """
    secret = _normalize_secret(file_secret)
    target = Path(target_path)

    if target.is_dir():
        out_dir = target
    else:
        out_dir = target.parent
        out_dir.mkdir(parents=True, exist_ok=True)

    cmd, env = _build_croc_receive(secret, out_dir, overwrite)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(out_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise _croc_error(f"croc download timed out after {timeout}s")

    except FileNotFoundError:
        raise _croc_not_found_error()

    if proc.returncode != 0:
        err_msg = stderr.decode().strip() or stdout.decode().strip()
        raise _croc_error(f"croc failed: {err_msg}")

    return target


def _normalize_secret(file_secret: str) -> str:
    """去掉 magnus-secret: 前缀（如果有）"""
    if file_secret.startswith(FILE_SECRET_PREFIX):
        return file_secret[len(FILE_SECRET_PREFIX):]
    return file_secret


def _build_croc_receive(secret: str, out_dir: Path, overwrite: bool) -> tuple:
    """构建 croc 接收命令，返回 (cmd, env)"""
    is_windows = sys.platform == "win32"

    cmd = ["croc", "--yes"]
    if overwrite:
        cmd.append("--overwrite")
    cmd.extend(["--out", str(out_dir)])

    if is_windows:
        cmd.append(secret)
        return cmd, None
    else:
        env = os.environ.copy()
        env["CROC_SECRET"] = secret
        return cmd, env
