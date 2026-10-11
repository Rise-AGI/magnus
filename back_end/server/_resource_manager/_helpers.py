# back_end/server/_resource_manager/_helpers.py
"""Resource-manager 内部的取消安全 I/O、命名规范化与目录操作。"""
import hashlib
import asyncio
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable


async def _run_blocking_io(
    function: Callable,
    *arguments,
    **keywords,
):
    """Keep filesystem workers joined before a cancelled caller releases ownership.

    Cancelling an asyncio waiter cannot stop its thread. Shield the operation,
    then finish joining it despite repeated cancellation before propagating the
    original cancellation. Cleanup may only remove its destination afterwards.
    """
    task = asyncio.create_task(asyncio.to_thread(
        function,
        *arguments,
        **keywords,
    ))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


def _get_dir_size(path: str) -> int:
    """递归计算目录大小"""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _remove_tree(path: str) -> None:
    """删除一棵目录树，出错的条目跳过；删不干净时由调用方用 os.path.exists 判断。

    git 把 object / pack 文件写成只读，而 Windows 拒删只读文件：普通 rmtree 会删掉其余
    一切、唯独剩下这些文件，留下一个既不能用、又删不掉的空壳。所以 Windows 上对删不掉
    的条目清掉它自身的只读位（不跟随链接）再重删。POSIX 上删文件只看父目录权限、只读
    位不碍事，就用普通的 rmtree、不做任何 chmod：被删的树里可能有 runner 写进来的内容，
    chmod 会顺着其中的符号链接改到树外的文件上。
    """
    if sys.platform != "win32":
        shutil.rmtree(path, ignore_errors=True)
        return

    def _clear_readonly_and_retry(function, target, _error):
        if function not in (os.unlink, os.rmdir):
            return
        try:
            os.chmod(target, stat.S_IWRITE, follow_symlinks=False)
            function(target)
        except OSError:
            pass

    shutil.rmtree(path, onexc=_clear_readonly_and_retry)


def _image_to_sif_filename(image: str) -> str:
    """docker://pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime -> pytorch_pytorch_2.5.1-cuda12.4-cudnn9-runtime.sif"""
    name = re.sub(r'^[a-z]+://', '', image)
    name = re.sub(r'[/:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return f"{name}.sif"


def _repo_to_cache_dirname(namespace: str, repo_name: str, branch: str) -> str:
    """namespace/repo_name/branch -> namespace_repo_name_branch_<hash>

    前半截只为让人看得懂：规范化把路径分隔符和 `:@` 换成 `_` 并合并连续下划线，本身
    有损（仓库 `b_c` 的分支 `d` 与仓库 `b` 的分支 `c_d`、分支 `feat/a` 与 `feat_a` 会
    得到同一个前缀）。末尾的短哈希取自原始三元组，保证不同的仓库 / 分支落进不同目录。

    三元组来自用户提交、未经校验。结果总是单独一级目录名、以哈希结尾，既不含路径分隔符
    也不可能是 `.` / `..`，所以 cache 目录一定落在 repo_cache 之下 —— ensure_repo 会整个
    删掉不能用的 cache 目录，靠的就是这一点。
    """
    name = f"{namespace}_{repo_name}_{branch}"
    name = re.sub(r'[/\\:@]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    digest = hashlib.sha256(f"{namespace}\0{repo_name}\0{branch}".encode()).hexdigest()[:12]
    return f"{name}_{digest}"
