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
from . import logger, magnus_workspace_path
from ._wrapper_template import _build_wrapper_content

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _SubmitMixinBase = _SchedulerProtocol
else:
    _SubmitMixinBase = object


def _render_docker_user_script(entry_command: str) -> str:
    """生成 Docker 模式的 .magnus_user_script.sh 内容。

    设计目标：跟 SLURM 模式 SIGTERM 语义对等 —— 没装 handler 的用户进程沉默继续跑、
    装了 handler 的进程响应自己处理。两层接力：

    1. 子壳里装 `trap '' TERM` 把 disposition 设为 SIG_IGN。子壳内单一长跑命令
       会被 bash exec 优化成"子壳 PID 直接替换为用户进程"（典型 entry 模式
       `pip install ... && python train.py` 的最后命令满足此条件）；POSIX 规定
       SIG_IGN 通过 exec 继承，所以用户进程默认 disposition 是 SIG_IGN。用户代码
       用 `signal.signal()` 显式覆盖来响应（Python 直接调 sigaction，不受继承的
       SIG_IGN 影响）。
    2. 外层 bash 装 `trap 'kill -TERM "$_magnus_pid"' TERM`：tini 把 SIGTERM 转发
       给 PID 1 的 bash，bash trap 主动把信号转发给子壳/用户进程 PID（tini 默认
       不广播给容器内全员，所以这层显式转发不可省）。bash 装了 trap 不会 deferred
       terminate，会等 wait 完成后按 trap 行为继续（即 forward 后继续等子壳真退）。

    子壳后台跑 + 外层 wait + while 循环：wait 被信号中断时返回 128+sig，重发 wait
    直到子壳真退；bash 在子进程已 reap 后仍 remember 该 PID 的 exit status，最后一次
    wait 拿到的 `$?` 就是用户进程真实退出码（已 bash 5.x 实测）。

    entry_command 整体逐行原样嵌入子壳，不做缩进 —— bash heredoc 的结束符
    （`EOF` 等）必须出现在行首（非 `<<-` 形式），任何前导空格都会让结束符失效；
    用户 entry_command 包含 heredoc 的场景虽不常见，但回归不可接受。视觉对齐让位
    于语义透传。

    SLURM 模式信号链路不在这里，那边由 sbatch batch script 入口装 `trap '' TERM`
    + `exec wrapper.py`、wrapper.py main() 再装一次 SIG_IGN 共同覆盖（见
    _slurm_manager/_control.py:submit_job_simple 与 _wrapper_template.py）。两边
    能力对等。
    """
    return (
        "set -e\n"
        "export HOME=$MAGNUS_HOME\n"
        "\n"
        "(\n"
        "trap '' TERM\n"
        "set -e\n"
        f"{entry_command}\n"
        ") &\n"
        "_magnus_pid=$!\n"
        "trap 'kill -TERM \"$_magnus_pid\" 2>/dev/null || true' TERM\n"
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

            job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
            repo_dir = f"{job_working_table}/repository"

            self._init_job_working_dir(job_working_table)

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

        sif_path = resource_manager.get_sif_path(job.container_image)
        default_system_entry_command = magnus_config["cluster"]["default_system_entry_command"]
        base_system_entry_command = job.system_entry_command if job.system_entry_command else default_system_entry_command
        system_entry_command = base_system_entry_command.strip()

        default_ephemeral_storage = magnus_config["cluster"]["default_ephemeral_storage"]
        ephemeral_storage = job.ephemeral_storage if job.ephemeral_storage else default_ephemeral_storage

        wrapper_content = _build_wrapper_content(
            job_working_table = job_working_table,
            repo_dir = repo_dir,
            sif_path = sif_path,
            system_entry_command = system_entry_command,
            user_token = user_token,
            magnus_address = magnus_address,
            job_id = job_id,
            ephemeral_storage = ephemeral_storage,
            allow_root = allow_root,
            entry_command = job.entry_command,
            effective_runner = effective_runner,
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

        try:
            assert self.slurm_manager is not None
            slurm_id = self.slurm_manager.submit_job_simple(
                entry_command = f"python3 {wrapper_path}",
                gpus = job.gpu_count,
                job_name = job.task_name,
                gpu_type = job.gpu_type,
                output_path = f"{job_working_table}/slurm/output.txt",
                overwrite_output = False,
                runner = effective_runner,
                cpu_count = job.cpu_count,
                memory_demand = job.memory_demand,
                token = job.user.token if job.user.token is not None else "",
            )

            job.status = JobStatus.QUEUED
            job.slurm_job_id = slurm_id
            db.commit()

            logger.info(f"Job {job.id} submitted to SLURM (ID: {slurm_id}, Branch: {job.branch})")
            return True

        except Exception as error:
            logger.error(f"Job {job.id} submission error: {error}")
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
