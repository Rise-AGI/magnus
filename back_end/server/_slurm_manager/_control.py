# back_end/server/_slurm_manager/_control.py
"""SLURM 任务提交与终止：sbatch / scancel 包装。"""
import os
import time
import subprocess
from typing import Dict, Optional

from . import logger
from ._errors import SlurmError, SlurmResourceError


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
        """
        简单提交任务（不含 sleep 和状态检查）
        让 SLURM 自己管理队列和调度
        """
        script_content = f"#!/bin/bash\n\n{entry_command}"

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

    def submit_job(
        self,
        entry_command: str,
        gpus: int,
        job_name: str,
        runner: str,
        token: str,
        gpu_type: Optional[str] = None,
        output_path: Optional[str] = None,
        slurm_latency: int = 1,
        overwrite_output: bool = True,
        cpu_count: Optional[int] = None,
        memory_demand: Optional[str] = None,

    ) -> str:

        """
        提交任务 (Mock Immediate Mode)

        设计思路：
        Slurm 原生不支持严格的 "Immediate Fail if Resource Unavailable" (即 --immediate 往往只针对 backlog)。
        此处采用 "Submit -> Sleep -> Check" 的模拟策略：
        1. 提交任务，并注入 sleep 延迟确保调度器有时间处理
        2. 检查状态，若仍为 PENDING (资源不足)，则手动 Kill 并抛出异常
        """

        # 注入 Sleep 以便给调度器反应时间
        entry_command = f"sleep {slurm_latency + 1}" + "\n" + entry_command
        script_content = f"#!/bin/bash\n\n{entry_command}"

        command = [
            "sbatch",
            "--parsable",
            f"--job-name={job_name}",
        ]

        # 默认将 stderr 合并到 stdout
        log_file = output_path if output_path else "magnus_%j.log"
        command.append(f"--output={log_file}")

        if not overwrite_output:
            command.append("--open-mode=append")

        # 构造 GPU 请求参数
        if gpus > 0:
            if gpu_type and gpu_type != "cpu":
                command.append(f"--gres=gpu:{gpu_type}:{gpus}")
            else:
                command.append(f"--gres=gpu:{gpus}")

        if memory_demand is not None: command.append(f"--mem={memory_demand}")
        if cpu_count is not None and cpu_count > 0: command.append(f"--cpus-per-task={cpu_count}")

        job_id = None

        env: Dict[str, str] = os.environ.copy()
        if runner is not None: env["MAGNUS_RUNNER"] = runner
        if token is not None: env["MAGNUS_TOKEN"] = token

        try:
            gpu_info = f"{gpu_type}:{gpus}" if (gpu_type and gpus > 0) else f"{gpus}"
            logger.info(f"🚀 Submitting '{job_name}' via stdin (GPUs: {gpu_info})...")

            result = subprocess.run(
                command,
                input = script_content,
                capture_output = True,
                text = True,
                check = True,
                env = env,
            )

            job_id = result.stdout.strip()

            # 等待 Slurm 调度决策
            time.sleep(slurm_latency)

            status = self.check_job_status(job_id)

            # 模拟 Immediate 模式的核心逻辑
            if status == "PENDING":
                detailed_reason = "Unknown Reason"
                partition_info = "Unknown Partition"
                try:
                    info_cmd = [
                        "squeue",
                        "--job", str(job_id),
                        "--noheader",
                        "--format=%r|%P"
                    ]
                    output = subprocess.check_output(
                        info_cmd,
                        text = True,
                        stderr = subprocess.DEVNULL,
                    ).strip()

                    if output:
                        detailed_reason, partition_info = output.split("|", 1)
                except Exception:
                    pass

                logger.warning(
                    f"⚠️ [Immediate Mode] Job {job_id} Rejected.\n"
                    f"   - Status: PENDING\n"
                    f"   - Reason: [{detailed_reason}] (Critically Important)\n"
                    f"   - Partition: [{partition_info}]\n"
                    f"   - Action: Killing job immediately due to strict resource policy."
                )

                self.kill_job(job_id, runner, token)
                raise SlurmResourceError(f"Resources unavailable immediately (Slurm Reason: {detailed_reason})")

            elif status in ["FAILED", "UNKNOWN", "BOOT_FAIL", "NODE_FAIL"]:
                raise SlurmError(f"Job failed immediately after submission (Status: {status})")

            return job_id

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ sbatch execution failed: {e.stderr}")
            raise SlurmError(f"Submission failed: {e.stderr}")

        except SlurmResourceError:
            raise

        except Exception as e:
            logger.error(f"❌ Unexpected submission error: {e}")
            if job_id:
                logger.warning(f"🧹 Cleaning up job {job_id} due to unexpected error...")
                try:
                    self.kill_job(job_id, runner, token)
                except Exception:
                    pass
            raise SlurmError(f"Unexpected error: {e}")

    def kill_job(
        self,
        slurm_job_id: str,
        runner: str,
        token: str,
    ) -> None:

        command = [
            "scancel",
            slurm_job_id,
        ]

        env: Dict[str, str] = os.environ.copy()
        env["MAGNUS_RUNNER"] = runner
        env["MAGNUS_TOKEN"] = token

        try:
            subprocess.run(
                command,
                check = False,
                env = env,
            )
        except Exception as error:
            logger.error(f"scancel failed for job {slurm_job_id}: {error}")
