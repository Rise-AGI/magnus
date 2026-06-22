# back_end/server/_scheduler/_submit.py
import os
import re
import sys
import traceback
from typing import TYPE_CHECKING, List
from sqlalchemy.orm import Session
from pywheels.file_tools import guarantee_file_exist
from ..models import Job, JobStatus
from .._magnus_config import magnus_config
from .._resource_manager import resource_manager
from . import (
    logger,
    magnus_workspace_path,
    magnus_ephemeral_workspace_path,
    magnus_remote_workspace_path,
    magnus_remote_ephemeral_workspace_path,
    magnus_remote_container_cache_path,
)
from ._wrapper_template import _build_wrapper_content

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _SubmitMixinBase = _SchedulerProtocol
else:
    _SubmitMixinBase = object


def _render_docker_user_script(entry_command: str) -> str:
    """生成 Docker 模式的 .magnus_user_script.sh 内容。

    设计目标：与 SLURM 模式 SIGTERM 语义对等 —— "user entry_command 的所有
    进程都收到 SIGTERM"，跨语言透明：子壳入口 `trap '' TERM` 让 user-script
    bash SIG_IGN，handler-aware 进程用 signal.signal / sigaction 装 handler
    覆盖 SIG_IGN、自己处理收尾（写 .magnus_result 表达成功收尾、sys.exit(0)）；
    handler-less 进程因继承 SIG_IGN 把 SIGTERM 当 no-op，想强杀走 terminate
    (docker stop -t 0 → SIGKILL) 路径。

    两层信号传递（外层 bash 主动转发 user pgrp + 子壳作 pgrp leader）：

    1. `set -m` 启用 bash monitor mode —— 非交互 bash 默认 `set +m`，
       backgrounded 子壳继承外层 bash 的 pgid，`$!` 仅是 PID 不是 pgid；
       `set -m` 让 `( ... ) &` 自动 setpgid 让子壳成为新 pgrp leader，
       `_magnus_pid` 同时是 PID 和 pgid。entry_command 内部的 fork / exec
       子孙默认 inherit 这个 pgid，构成 user 进程组。
    2. 外层 bash 装 `trap 'kill -TERM -- -$_magnus_pid' TERM`：tini 把 SIGTERM
       转发给 PID 1 的 bash（tini 不广播容器全员，这层显式转发不可省），
       bash trap 用负 PID 给整个 user pgrp 投递 SIGTERM —— main / workers / 嵌
       套 shell 全员收到。bash 自己装了 trap 不 deferred terminate，会等 wait
       完成后按 trap 行为继续（即转发后继续等子壳真退）。

    子壳后台跑 + 外层 wait + while 循环：wait 被信号中断时返回 128+sig，重发 wait
    直到子壳真退；bash 在子进程已 reap 后仍 remember 该 PID 的 exit status，最后一次
    wait 拿到的 `$?` 就是用户进程真实退出码（已 bash 5.x 实测）。

    entry_command 整体逐行原样嵌入子壳，不做缩进 —— bash heredoc 的结束符
    （`EOF` 等）必须出现在行首（非 `<<-` 形式），任何前导空格都会让结束符失效；
    用户 entry_command 包含 heredoc 的场景虽不常见，但回归不可接受。视觉对齐让位
    于语义透传。

    SLURM 一侧通过 wrapper.py 枚举 cgroup + NSpid 筛选 user 容器内进程 + kill(2)
    跨 PID namespace 达到同样语义（详见 _wrapper_template.py:_signal_user_processes
    与 _slurm_manager/_control.py:send_signal）；docker 一侧不需要 wrapper 中间层，
    bash pgrp 加 trap 已经够，magnus DB 收敛直接看容器 exit code + .magnus_result。
    """
    return (
        "set -e\n"
        "set -m\n"
        "export HOME=$MAGNUS_HOME\n"
        "\n"
        "(\n"
        "set -e\n"
        # 子壳 SIG_IGN SIGTERM —— 与 SLURM 模式 .magnus_user_script.sh 的
        # `trap '' TERM` 对等。外层 trap 把 SIGTERM killpg 给整个 user pgrp 时
        # 子壳 bash 自己也在 pgrp 里，SIG_IGN 让它不被默认 disposition 杀掉，
        # 外层 wait 也就不会提前返回拖垮容器。SIG_IGN 通过 POSIX exec 继承给
        # user 进程；用户代码 signal.signal(SIGTERM, …) / sigaction(2) 装
        # handler 自然覆盖。
        "trap '' TERM\n"
        f"{entry_command}\n"
        ") &\n"
        "_magnus_pid=$!\n"
        "trap 'kill -TERM -- -$_magnus_pid 2>/dev/null || true' TERM\n"
        "while kill -0 \"$_magnus_pid\" 2>/dev/null; do\n"
        "  wait \"$_magnus_pid\" 2>/dev/null || true\n"
        "done\n"
        "wait \"$_magnus_pid\" 2>/dev/null\n"
        "exit $?\n"
    )


class _SubmitMixin(_SubmitMixinBase):
    """提交决策已经做完的 PENDING job 到 SLURM 或 Docker 后端。"""

    def _submit_to_slurm(self, db: Session, job: Job) -> bool:
        """
        提交任务到 SLURM 队列
        资源（镜像、仓库）已在 Preparing 阶段准备好
        执行流程: wrapper.py → system_entry_command → apptainer exec → epilogue
        """
        # 乐观锁：防止与 terminate_job（线程池）的竞态
        db.refresh(job)
        if job.status != JobStatus.PENDING:
            logger.info(f"Job {job.id} status changed to {job.status} before submission, skipping")
            return False

        try:
            user_magnus = magnus_config["cluster"]["default_runner"]
            effective_runner = job.runner if job.runner is not None else user_magnus

            # 本机路径：wrapper 在本机生成、本机 init 工作区骨架、回读 marker/产物的
            # 落点；local_repo_dir 是 Preparing 阶段本地 clone 的落点（relay 模式下推往远端）。
            job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
            job_ephemeral_table = f"{magnus_ephemeral_workspace_path}/jobs/{job.id}"
            local_repo_dir = f"{job_working_table}/repository"
            # 执行端路径：job 真正运行处。本机执行下逐字等于上面的本机路径（下方
            # wrapper 内容与 sbatch 参数字节级不变）；远端执行（transport=ssh）下指向
            # remote_root 下的工作区，由 _stage_in_job 在远端建好并推送 wrapper。
            remote_job_working_table = f"{magnus_remote_workspace_path}/jobs/{job.id}"
            remote_job_ephemeral_table = f"{magnus_remote_ephemeral_workspace_path}/jobs/{job.id}"
            remote_repo_dir = f"{remote_job_working_table}/repository"

            self._init_job_working_dir(job_working_table)
            # 平台 SDK 落进工作区（容器跑平台 SDK 而非镜像 baked 版）。SLURM 执行通用：
            # owned 本机经 workspace bind 进容器，远端租户由 _stage_in_job 推过去。
            self._provision_platform_sdk(job_working_table)
            # ephemeral_root == root 时与 job_working_table 同路径（幂等）；
            # 配成独立快盘时在那侧建好 ephemeral overlay / apptainer tmp 的落脚目录。
            guarantee_file_exist(job_ephemeral_table, is_directory=True)

            allow_root = magnus_config["execution"]["allow_root"]
            user_token = job.user.token or ""
            magnus_address = f"{magnus_config['server']['address']}:{magnus_config['server']['front_end_port']}"
            job_id = str(job.id)

        except Exception as error:
            logger.error(f"Job {job.id} submission error: {error}\nTraceback:\n{traceback.format_exc()}")
            job.status = JobStatus.FAILED
            job.result = f"Job submission setup failed: {error}"
            db.commit()
            return False

        local_sif_path = resource_manager.get_sif_path(job.container_image)
        # 执行端 SIF 路径：本机执行下逐字等于本机路径；远端执行下指向 remote_root 下
        # 的 container_cache（同名 SIF，文件本身如何落远端是来源相关的另一回事）。
        remote_sif_path = (
            f"{magnus_remote_container_cache_path}/{os.path.basename(local_sif_path)}"
            if self._is_remote_execution()
            else local_sif_path
        )
        default_system_entry_command = magnus_config["cluster"]["default_system_entry_command"]
        base_system_entry_command = job.system_entry_command if job.system_entry_command else default_system_entry_command
        system_entry_command = base_system_entry_command.strip()

        default_ephemeral_storage = magnus_config["cluster"]["default_ephemeral_storage"]
        ephemeral_storage = job.ephemeral_storage if job.ephemeral_storage else default_ephemeral_storage

        container_runtime = magnus_config["execution"]["container_runtime"]

        wrapper_content = _build_wrapper_content(
            job_working_table = remote_job_working_table,
            job_ephemeral_table = remote_job_ephemeral_table,
            repo_dir = remote_repo_dir,
            sif_path = remote_sif_path,
            system_entry_command = system_entry_command,
            user_token = user_token,
            magnus_address = magnus_address,
            job_id = job_id,
            ephemeral_storage = ephemeral_storage,
            allow_root = allow_root,
            entry_command = job.entry_command,
            effective_runner = effective_runner,
            container_runtime = container_runtime,
            enable_custody_drop = self._is_remote_execution(),
        )

        wrapper_path = f"{job_working_table}/wrapper.py"
        try:
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(wrapper_content)
        except IOError as error:
            logger.error(f"Failed to write wrapper script for Job {job.id}: {error}")
            job.status = JobStatus.FAILED
            job.result = f"Failed to write wrapper script: {error}"
            db.commit()
            return False

        # 远端执行（transport=ssh）：在远端建好工作区骨架并把本机生成的 wrapper 推过去；
        # 本机执行下 no-op。失败按提交失败处理，不把无 wrapper 的 job 交给 sbatch。
        try:
            self._stage_in_job(job_id, wrapper_path)
            self._stage_in_resources(job_id, local_sif_path, local_repo_dir)
            # 无网计算节点：把 entry_command / system_entry_command 引用的 custody 文件预置
            # 进远端 dropin，让 in-job `magnus receive` 读盘而非打 HTTP（download 侧的离线通道，
            # 对称于上传侧的 drop + _stage_out_custody）。本机执行下 no-op。
            self._stage_in_custody(job_id, job.entry_command, system_entry_command)
        except Exception as error:
            logger.error(f"Failed to stage Job {job.id} to remote site: {error}")
            self._cleanup_remote_job(job_id)
            job.status = JobStatus.FAILED
            job.result = f"Failed to stage job to remote execution site: {error}"
            db.commit()
            return False

        # 暂存（远端站点 scp SIF/repo 可达数秒）期间 job 可能被 terminate：sbatch 前重读
        # 状态，已非 PENDING 就放弃并清理远端 —— 否则会把已 terminate 的 job 交给 SLURM
        # 跑成孤儿，且随后写 QUEUED 覆盖掉 TERMINATED。
        db.refresh(job)
        if job.status != JobStatus.PENDING:
            logger.info(f"Job {job.id} no longer PENDING ({job.status}) after staging; aborting submit")
            self._cleanup_remote_job(job_id)
            return False

        # 执行端 wrapper 路径：本机执行下逐字等于本机 wrapper_path，sbatch 参数字节级不变。
        remote_wrapper_path = f"{remote_job_working_table}/wrapper.py"
        try:
            assert self.slurm_manager is not None
            slurm_id = self.slurm_manager.submit_job_simple(
                entry_command = f"python3 {remote_wrapper_path}",
                gpus = job.gpu_count,
                job_name = job.task_name,
                gpu_type = job.gpu_type,
                output_path = f"{remote_job_working_table}/slurm/output.txt",
                overwrite_output = False,
                runner = effective_runner,
                cpu_count = job.cpu_count,
                memory_demand = job.memory_demand,
                time_limit = job.time_limit,
                token = job.user.token if job.user.token is not None else "",
            )

            job.status = JobStatus.QUEUED
            job.slurm_job_id = slurm_id
            db.commit()

            logger.info(f"Job {job.id} submitted to SLURM (ID: {slurm_id}, Branch: {job.branch})")
            return True

        except Exception as error:
            logger.error(f"Job {job.id} submission error: {error}")
            self._cleanup_remote_job(job_id)
            job.status = JobStatus.FAILED
            job.result = f"Job submission to scheduler failed: {error}"
            db.commit()
            return False

    def _submit_to_docker(self, db: Session, job: Job) -> bool:
        """
        提交任务到 Docker 容器（local 模式）。
        不生成 wrapper.py，直接 docker run。
        资源属性（gpu_count, memory_demand, cpu_count）在 local 模式下不生效。
        """
        assert self.docker_manager is not None

        db.refresh(job)
        if job.status != JobStatus.PENDING:
            logger.info(f"Job {job.id} status changed to {job.status} before submission, skipping")
            return False

        try:
            job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
            self._init_job_working_dir(job_working_table)

            user_token = job.user.token or ""
            magnus_address = f"{magnus_config['server']['address']}:{magnus_config['server']['back_end_port']}"
            job_id = str(job.id)

            # 准备用户脚本（强制 LF：脚本在 Linux 容器内执行）
            user_script_path = os.path.join(job_working_table, ".magnus_user_script.sh")
            with open(user_script_path, "w", newline="\n") as f:
                f.write(_render_docker_user_script(job.entry_command))
            os.chmod(user_script_path, 0o755)

            # 构造 bind mounts
            magnus_home = "/magnus"
            bind_mounts = [
                f"{job_working_table}:{magnus_home}/workspace",
            ]

            # 解析 system_entry_command 中的 APPTAINER_BIND（如有）
            default_system_entry_command = magnus_config["cluster"]["default_system_entry_command"]
            base_system_entry_command = job.system_entry_command if job.system_entry_command else default_system_entry_command
            system_entry_command = base_system_entry_command.strip()
            if system_entry_command:
                extra_binds = self._extract_bind_mounts_from_system_entry_command(system_entry_command)
                bind_mounts.extend(extra_binds)

            # Docker 网络模式：Linux 用 host（容器直接访问 localhost），
            # Windows/macOS 用 bridge + host.docker.internal
            if sys.platform == "linux":
                network_mode = "host"
                container_magnus_address = magnus_address
            else:
                network_mode = None  # Docker Desktop default bridge
                back_end_port = magnus_config["server"]["back_end_port"]
                container_magnus_address = f"http://host.docker.internal:{back_end_port}"

            # 环境变量
            env_vars = {
                "MAGNUS_TOKEN": user_token,
                "MAGNUS_ADDRESS": container_magnus_address,
                "MAGNUS_JOB_ID": job_id,
                "MAGNUS_HOME": magnus_home,
                "MAGNUS_RESULT": f"{magnus_home}/workspace/.magnus_result",
                "MAGNUS_ACTION": f"{magnus_home}/workspace/.magnus_action",
                "MAGNUS_METRICS_DIR": f"{magnus_home}/workspace/metrics",
                "MAGNUS_METRICS_PROTO": "metrics.v1",
                "PYTHONUNBUFFERED": "1",
                "HOME": magnus_home,
            }

            # 容器内执行命令：运行用户脚本（成功标记由宿主机在检测 exit 0 后写入）
            container_cmd = f"bash {magnus_home}/workspace/.magnus_user_script.sh"

            container_name = f"magnus-job-{job.id}"

            # GPU: 如果 job 请求了 GPU 且本机有 GPU，尝试启用
            gpu_enabled = job.gpu_count > 0

            self.docker_manager.run_container(
                container_name=container_name,
                image=job.container_image,
                entry_command=container_cmd,
                bind_mounts=bind_mounts,
                env_vars=env_vars,
                working_dir=f"{magnus_home}/workspace/repository",
                gpu_enabled=gpu_enabled,
                network_mode=network_mode,
            )

            job.status = JobStatus.QUEUED
            job.slurm_job_id = container_name  # 复用字段存储 container name
            db.commit()

            logger.info(f"Job {job.id} submitted to Docker (container: {container_name})")
            return True

        except Exception as error:
            logger.error(f"Job {job.id} Docker submission error: {error}\n{traceback.format_exc()}")
            job.status = JobStatus.FAILED
            job.result = f"Docker container launch failed: {error}"
            db.commit()
            return False

    def _extract_bind_mounts_from_system_entry_command(self, system_entry_command: str) -> List[str]:
        """
        从 system_entry_command 中提取 bind mount 列表，用于 Docker -v。
        返回 ["host:container", ...] 列表。

        有损转换——约定与局限：
        1. 只解析 bash array `mounts=(...)` 中的挂载对，忽略一切其他逻辑
           （环境变量设置、module load、条件分支等在 Docker 模式下丢弃）
        2. 仅展开 $HOME / ${HOME}；host 侧使用 os.path.expanduser，
           container 侧硬编码 /root（Docker 容器默认以 root 运行）；
           不支持 $PWD、命令替换 $(...) 等动态值
        3. 纯 Python 实现，不依赖 bash，全平台行为一致
        4. Windows 盘符（C:\\）会被转为 Docker mount 格式（/c/），
           仅转换 host 侧；container 侧是容器内 Linux 路径，不转换
        """
        if not system_entry_command:
            return []

        # 提取 mounts=( ... ) 块内的所有双引号字符串
        # re.DOTALL 让 . 匹配换行，容忍任意 whitespace
        array_match = re.search(r'mounts\s*=\s*\((.*?)\)', system_entry_command, re.DOTALL)
        if array_match is None:
            return []

        body = array_match.group(1)
        entries = re.findall(r'"([^"]+)"', body)

        host_home = os.path.expanduser("~")
        container_home = "/root"  # Docker 容器默认以 root 运行
        binds = []
        for entry in entries:
            # 展开前拆分：raw entry 里只有 1 个冒号（mount 分隔符），
            # 展开后 Windows 盘符会引入额外冒号，所以必须先拆再展开
            if ":" not in entry:
                continue
            host_raw, container_raw = entry.split(":", 1)
            host_path = host_raw.replace("${HOME}", host_home).replace("$HOME", host_home)
            container_path = container_raw.replace("${HOME}", container_home).replace("$HOME", container_home)
            # Windows 盘符转 Docker mount 格式（C:\Users\... → /c/Users/...）
            # 仅 host 侧需要；container 侧是容器内 Linux 路径
            if len(host_path) >= 2 and host_path[1] == ":":
                host_path = "/" + host_path[0].lower() + host_path[2:].replace("\\", "/")
            binds.append(f"{host_path}:{container_path}")
        return binds

    def _init_job_working_dir(self, job_working_table: str) -> None:
        guarantee_file_exist(f"{job_working_table}/slurm", is_directory=True)
        guarantee_file_exist(f"{job_working_table}/metrics", is_directory=True)

        for marker_name in [".magnus_success", ".magnus_result", ".magnus_action", ".magnus_oom"]:
            marker_path = f"{job_working_table}/{marker_name}"
            if os.path.exists(marker_path):
                try:
                    os.remove(marker_path)
                except OSError:
                    pass
