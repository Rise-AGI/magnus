# back_end/server/_slurm_manager/_control.py
"""SLURM 任务提交与终止：sbatch / scancel 包装。"""
import os
import time
import subprocess
from typing import (
    TYPE_CHECKING,
    Dict,
    Optional,
)

from . import logger
from ._errors import (
    SlurmError,
    SlurmResourceError,
)


# `_ControlMixin.submit_job` 调用 `_ResourceQueryMixin.check_job_status`，运行时由
# `SlurmManager(_ResourceQueryMixin, _ControlMixin)` 组装两边。type-only 继承让
# Pylance 在不修改运行时 MRO 的前提下看到 cross-mixin 属性。
if TYPE_CHECKING:
    from ._resource_query import _ResourceQueryMixin
    _ControlMixinBase = _ResourceQueryMixin
else:
    _ControlMixinBase = object


class _ControlMixin(_ControlMixinBase):

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
        """简单提交：不做 sleep + 状态检查，让 SLURM 自己排队和调度。"""
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
        提交任务（Mock Immediate Mode）。

        SLURM 原生的 --immediate 一般只针对 backlog，没有严格的
        "Immediate Fail if Resource Unavailable"。此处采用
        "Submit -> Sleep -> Check"：
        1. 提交任务，注入 sleep 给调度器留处理时间
        2. sleep 醒后查状态，若仍 PENDING（资源不足）则 scancel 并抛 SlurmResourceError
        """
        entry_command = f"sleep {slurm_latency + 1}\n" + entry_command
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

        job_id: Optional[str] = None

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

            time.sleep(slurm_latency)
            status = self.check_job_status(job_id)

            if status == "PENDING":
                detailed_reason = "Unknown Reason"
                partition_info = "Unknown Partition"
                try:
                    info_command = [
                        "squeue",
                        "--job", str(job_id),
                        "--noheader",
                        "--format=%r|%P",
                    ]
                    output = subprocess.check_output(
                        info_command,
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
                raise SlurmResourceError(
                    f"Resources unavailable immediately (Slurm Reason: {detailed_reason})"
                )

            elif status in ["FAILED", "UNKNOWN", "BOOT_FAIL", "NODE_FAIL"]:
                raise SlurmError(
                    f"Job failed immediately after submission (Status: {status})"
                )

            return job_id

        except subprocess.CalledProcessError as error:
            logger.error(f"❌ sbatch execution failed: {error.stderr}")
            raise SlurmError(f"Submission failed: {error.stderr}")

        except SlurmResourceError:
            raise

        except Exception as error:
            logger.error(f"❌ Unexpected submission error: {error}")
            if job_id:
                logger.warning(f"🧹 Cleaning up job {job_id} due to unexpected error...")
                try:
                    self.kill_job(job_id, runner, token)
                except Exception:
                    pass
            raise SlurmError(f"Unexpected error: {error}")

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
