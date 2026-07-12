# back_end/server/_job_dir_access.py
import logging
import subprocess

from ._magnus_config import magnus_config

logger = logging.getLogger(__name__)


def grant_job_dir_runner_access(
    directory: str,
    runner: str,
)-> None:
    """
    放开一个 job 目录的跨用户写权限。

    job 可能以有别于后端账户的 OS 用户（runner）运行，需要能在其目录下读写。递归授予
    runner 与 default_runner 读写执行，并用 default ACL 让目录内后续新建的文件继承同样
    权限——后端账户（default_runner）据此在 cleanup 时回收 runner 建的内容。

    依赖文件系统的 ACL 支持；不可用时降级为 warning（单盘 / 单 runner 站点不受影响）。
    """
    default_runner = magnus_config["cluster"]["default_runner"]
    try:
        subprocess.run(
            [
                "setfacl", "-R",
                "-m", f"u:{runner}:rwx",
                "-d", "-m", f"u:{default_runner}:rwx",
                "-d", "-m", f"u:{runner}:rwx",
                directory,
            ],
            check = True,
            capture_output = True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        logger.warning(f"setfacl failed on {directory}: {error}")
