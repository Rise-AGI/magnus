# sdks/python/src/magnus/croc_tools.py
"""
Croc 文件传输工具封装。

提供简洁的 API 用于通过 croc 接收文件。
"""
import os
import shutil
import sys
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .file_transfer import FILE_SECRET_PREFIX, normalize_secret


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
    target_path: Optional[str] = None,
    timeout: Optional[float] = None,
    overwrite: bool = True,
) -> Path:
    """
    通过 croc 接收文件/文件夹。

    :param file_secret: croc secret，可以带或不带 "magnus-secret:" 前缀
    :param target_path: 下载后的目标路径；None 时使用原始文件名存到当前目录
    :param timeout: 超时时间（秒），None 表示无限等待
    :param overwrite: 是否覆盖已存在的文件
    :return: 最终文件路径的 Path
    """
    secret = normalize_secret(file_secret)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        cmd = _build_croc_cmd(secret, tmp_dir, overwrite)

        try:
            result = subprocess.run(
                cmd,
                env=_build_env(secret),
                cwd=str(tmp_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise _croc_not_found_error()
        except subprocess.TimeoutExpired:
            raise _croc_error(f"croc download timed out after {timeout}s")

        if result.returncode != 0:
            all_output = (result.stderr.strip() + "\n" + result.stdout.strip()).strip()
            raise _croc_error(f"croc failed (exit {result.returncode}): {all_output}")

        downloaded = list(tmp_dir.iterdir())
        if len(downloaded) != 1:
            raise _croc_error(
                f"Expected 1 item from croc, got {len(downloaded)}: {downloaded}"
            )

        target = Path(target_path).resolve() if target_path else Path.cwd() / downloaded[0].name
        if overwrite and target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(downloaded[0]), str(target))

    return target


async def download_file_async(
    file_secret: str,
    target_path: Optional[str] = None,
    timeout: Optional[float] = None,
    overwrite: bool = True,
) -> Path:
    """
    异步版本的 download_file。

    :param file_secret: croc secret，可以带或不带 "magnus-secret:" 前缀
    :param target_path: 下载后的目标路径；None 时使用原始文件名存到当前目录
    :param timeout: 超时时间（秒），None 表示无限等待
    :param overwrite: 是否覆盖已存在的文件
    :return: 最终文件路径的 Path
    """
    secret = normalize_secret(file_secret)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        cmd = _build_croc_cmd(secret, tmp_dir, overwrite)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(tmp_dir),
                env=_build_env(secret),
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
            all_output = (stderr.decode().strip() + "\n" + stdout.decode().strip()).strip()
            raise _croc_error(f"croc failed (exit {proc.returncode}): {all_output}")

        downloaded = list(tmp_dir.iterdir())
        if len(downloaded) != 1:
            raise _croc_error(
                f"Expected 1 item from croc, got {len(downloaded)}: {downloaded}"
            )

        target = Path(target_path).resolve() if target_path else Path.cwd() / downloaded[0].name
        if overwrite and target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(downloaded[0]), str(target))

    return target


def _build_croc_cmd(secret: str, out_dir: Path, overwrite: bool) -> list:
    """构建 croc 接收命令"""
    cmd = ["croc", "--yes"]
    if overwrite:
        cmd.append("--overwrite")
    cmd.extend(["--out", str(out_dir)])
    if sys.platform == "win32":
        cmd.append(secret)
    return cmd


def _build_env(secret: str) -> Optional[dict]:
    """Linux/macOS 通过环境变量传递 secret"""
    if sys.platform == "win32":
        return None
    env = os.environ.copy()
    env["CROC_SECRET"] = secret
    return env
