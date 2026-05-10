# back_end/server/_slurm_manager/_control.py
"""SLURM 任务提交与终止：sbatch / scancel 包装。"""
import os
import subprocess
from typing import Dict, Optional

from . import logger


class _ControlMixin:

    def submit_job_simple(
        self,
        entry_command: str,
        gpus: int,
        job_name: str,
        runner: str,
        token: str,
        gpu_type: Optional[str] = None,
        output_path: Optional[str] = None,
        overwrite_output: bool = True,
        cpu_count: Optional[int] = None,
        memory_demand: Optional[str] = None,
    ) -> str:
        """简单提交：不做 sleep + 状态检查，让 SLURM 自己排队和调度。

        sbatch 把 batch script 当作 bash 脚本由 slurmstepd 拉起一个外层 bash
        解释执行 entry_command。`scancel --signal=TERM --full` 会经 proctrack
        把 SIGTERM 投递给 cgroup 全员，外层 bash 默认行为是 deferred terminate
        exit 143，会盖掉用户进程的真实退出码并让 SLURM 把 job state 标成
        FAILED / CANCELLED；wrapper.py 在自己 main 入口装的 SIG_IGN 也救不回来
        外层 bash。所以这里在 batch script 入口装 `trap '' TERM`（disposition
        变 SIG_IGN），随后 `exec` 让 wrapper.py 直接替换外层 bash 进程，POSIX
        规定 SIG_IGN 通过 exec 继承 → 整条进程链（外层 → wrapper.py →
        subprocess shell → apptainer → 容器内 bash → 用户进程）一致 SIG_IGN。
        用户代码用 `signal.signal()` 显式覆盖来响应（详见 _wrapper_template.py
        和 docs/internals/job-runtime.md "Signaling and Termination"）。

        约束：`entry_command` 必须是 single simple command（不含 `&&`、`;`、
        `|`、子壳等 shell 复合结构）。`exec <complex>` 在 bash 里只 execve 第一
        个 token，复合结构后半段会被静默吞掉。当前唯一调用点 `_scheduler/_submit.py`
        传 `python3 {wrapper_path}`，满足约束。
        """
        script_content = f"#!/bin/bash\ntrap '' TERM\n\nexec {entry_command}"

        command = [
            "sbatch",
            "--parsable",
            f"--job-name={job_name}",
        ]

        log_file = output_path if output_path else "magnus_%j.log"
        command.append(f"--output={log_file}")

        if not overwrite_output:
            command.append("--open-mode=append")

        if gpus > 0:
            if gpu_type and gpu_type != "cpu":
                command.append(f"--gres=gpu:{gpu_type}:{gpus}")
            else:
                command.append(f"--gres=gpu:{gpus}")

        if memory_demand is not None:
            command.append(f"--mem={memory_demand}")
        if cpu_count is not None and cpu_count > 0:
            command.append(f"--cpus-per-task={cpu_count}")

        env: Dict[str, str] = os.environ.copy()
        if runner is not None:
            env["MAGNUS_RUNNER"] = runner
        if token is not None:
            env["MAGNUS_TOKEN"] = token

        gpu_info = f"{gpu_type}:{gpus}" if (gpu_type and gpus > 0) else f"{gpus}"
        logger.info(f"🚀 Submitting '{job_name}' to SLURM queue (GPUs: {gpu_info})...")

        result = subprocess.run(
            command,
            input = script_content,
            capture_output = True,
            text = True,
            check = True,
            env = env,
        )

        job_id = result.stdout.strip()
        logger.info(f"✅ Job '{job_name}' queued in SLURM (ID: {job_id})")
        return job_id

    def kill_job(
        self,
        slurm_job_id: str,
        runner: str,
        token: str,
    ) -> None:
        """硬终止 SLURM job：SIGKILL 全员 + scancel 让 SLURM 把 job 移出运行。

        wrapper.py 装 SIG_IGN 让整条链路忽略 SIGTERM（为 signal_job 路径服务，
        见 _wrapper_template.py），因此默认的 scancel 会先发 SIGTERM 等到
        KillWait 才 SIGKILL，前 KillWait 秒完全空转。直接 --signal=KILL --full
        把 SIGKILL 投给所有 PID 立刻清场（SIGKILL 在内核侧不可被 ignore），再
        裸 scancel 让 SLURM 标记 job 取消，保证 terminate / 抢占的"瞬时让出
        GPU"承诺成立。
        """
        env: Dict[str, str] = os.environ.copy()
        env["MAGNUS_RUNNER"] = runner
        env["MAGNUS_TOKEN"] = token

        # Step 1: SIGKILL --full 全员立刻清场
        try:
            result = subprocess.run(
                ["scancel", "--signal=KILL", "--full", slurm_job_id],
                check = False,
                capture_output = True,
                text = True,
                env = env,
            )
            if result.returncode != 0:
                logger.error(
                    f"scancel --signal=KILL failed for job {slurm_job_id} "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )
        except Exception as error:
            logger.error(f"scancel --signal=KILL failed for job {slurm_job_id}: {error}")

        # Step 2: 裸 scancel 让 SLURM 把 job state 转为 CANCELLED
        try:
            result = subprocess.run(
                ["scancel", slurm_job_id],
                check = False,
                capture_output = True,
                text = True,
                env = env,
            )
            if result.returncode != 0:
                logger.error(
                    f"scancel failed for job {slurm_job_id} "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )
        except Exception as error:
            logger.error(f"scancel failed for job {slurm_job_id}: {error}")

    def send_signal(
        self,
        slurm_job_id: str,
        signal_name: str,
        runner: str,
        token: str,
    ) -> None:
        """向 SLURM job 内全部进程发送指定信号但不终止 job。

        --signal=<sig> 让 scancel 转为信号转发器；--full 让信号触达 batch step
        （wrapper.py 是 sbatch 直接拉起的 batch script，没有 srun 创建的额外
        step），proctrack 再按 proc 树扩散到 apptainer 子壳与用户进程。
        """
        command = [
            "scancel",
            f"--signal={signal_name}",
            "--full",
            slurm_job_id,
        ]

        env: Dict[str, str] = os.environ.copy()
        env["MAGNUS_RUNNER"] = runner
        env["MAGNUS_TOKEN"] = token

        try:
            result = subprocess.run(
                command,
                check = False,
                capture_output = True,
                text = True,
                env = env,
            )
            if result.returncode != 0:
                logger.error(
                    f"scancel --signal={signal_name} failed for job {slurm_job_id} "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )
        except Exception as error:
            logger.error(f"scancel --signal={signal_name} failed for job {slurm_job_id}: {error}")
