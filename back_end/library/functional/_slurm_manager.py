# back_end/library/functional/_slurm_manager.py
import time
import shutil
import logging
import traceback
import subprocess
from typing import Optional


__all__ = [
    "SlurmManager",
    "SlurmError",
    "SlurmResourceError",
]


logger = logging.getLogger(__name__)


class SlurmError(Exception):
    pass


class SlurmResourceError(SlurmError):
    pass


class SlurmManager:

    def __init__(
        self
    )-> None:
        
        # 严格环境检查
        required_commands = ["sbatch", "squeue", "scancel", "sinfo"]
        missing_commands = [command for command in required_commands if shutil.which(command) is None]
        if missing_commands:
            error_msg = (
                f"CRITICAL: SLURM commands not found: {', '.join(missing_commands)}. "
                "Magnus requires a valid SLURM environment to operate."
            )
            logger.critical(error_msg)
            raise RuntimeError(error_msg)

    
    def get_cluster_free_gpus(
        self
    )-> int:
        
        try:
            # 获取所有 idle 或 mixed 节点的 GPU 信息
            command = ["sinfo", "-h", "-o", "%G %t"]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            total_free = 0
            for line in result.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) < 2:
                    continue
                gres, state = parts[0], parts[1]
                
                # 简单粗暴的解析：如果是 idle，假设该节点所有 GPU 空闲
                # 这是一个简化策略，实际生产环境可能需要根据 Site Configuration 调整
                if "gpu" in gres and state == "idle":
                    try:
                        # 解析 "gpu:A100:8" 或 "gpu:8" 中的最后一个数字
                        count = int(gres.split(':')[-1]) 
                        total_free += count
                    except ValueError:
                        pass
                        
            return total_free
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to execute sinfo: {e.stderr}")
            return 0
        except Exception as e:
            logger.error(f"Error querying cluster resources: {e}\n调用栈：\n{traceback.format_exc()}")
            return 0
    
    
    def submit_job(
        self,
        entry_command: str, 
        gpus: int,
        job_name: str,
        gpu_type: Optional[str] = None,
        output_path: Optional[str] = None,
        slurm_latency: int = 1,
    ) -> str:
        
        """
        提交任务 (通过 Stdin 管道)
        
        策略:
        1. 构造完整的 Shell 脚本内容。
        2. 构造 sbatch 参数 (支持指定型号、自定义日志路径)。
        3. 通过 Stdin 管道传给 sbatch。
        4. 模拟 Immediate 模式：提交后等待检查，若 PENDING 则强制取消。
        """
        
        entry_command = f"sleep {slurm_latency + 1}" + "\n" + entry_command
        script_content = f"#!/bin/bash\n\n{entry_command}"
        
        command = [
            "sbatch",
            "--parsable",
            f"--job-name={job_name}",
        ]

        # 利用默认行为：不设置 error 则 stderr 合并到 output
        log_file = output_path if output_path else "magnus_%j.log"
        command.append(f"--output={log_file}")

        # 处理 GPU 资源
        if gpus > 0:
            if gpu_type and gpu_type != "cpu":
                command.append(f"--gres=gpu:{gpu_type}:{gpus}")
            else:
                command.append(f"--gres=gpu:{gpus}")

        job_id = None
        try:
            gpu_info = f"{gpu_type}:{gpus}" if (gpu_type and gpus > 0) else f"{gpus}"
            logger.info(f"🚀 Submitting '{job_name}' via stdin (GPUs: {gpu_info})...")
            
            result = subprocess.run(
                command, 
                input=script_content,
                capture_output=True, 
                text=True, 
                check=True
            )
            
            job_id = result.stdout.strip()

            time.sleep(slurm_latency)
            
            status = self.check_job_status(job_id)
            
            if status == "PENDING":
                logger.warning(f"⚠️ Job {job_id} is PENDING (Resource unavailable). Triggering Immediate Kill...")
                self.kill_job(job_id) 
                raise SlurmResourceError("Resources unavailable immediately (Simulated)")
            
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
                    self.kill_job(job_id)
                except:
                    pass
            raise SlurmError(f"Unexpected error: {e}")

    
    def check_job_status(
        self, 
        slurm_job_id: str,
    )-> str:
        
        """
        查询 slurm 任务状态
        返回: PENDING | RUNNING | COMPLETED | FAILED | UNKNOWN
        """
        
        command = ["squeue", "-h", "-j", slurm_job_id, "-o", "%t"]
        try:
            result = subprocess.run(command, capture_output=True, text=True)
            state = result.stdout.strip()
            
            if not state:
                # squeue 查不到，说明任务已经不在队列中（结束了）
                # 这里默认它 COMPLETED，因为如果是 FAILED 通常会有记录
                return "COMPLETED"

            # 映射 SLURM 状态码
            # R=Running, PD=Pending, CG=Completing, CD=Completed, 
            # F=Failed, CA=Cancelled, TO=Timeout
            mapping = {
                "R": "RUNNING",
                "PD": "PENDING",
                "CG": "RUNNING",
                "CD": "COMPLETED",
                "F": "FAILED",
                "CA": "FAILED",
                "TO": "FAILED",
            }
            return mapping.get(state, "UNKNOWN")
        
        except Exception as e:
            logger.error(f"Failed to check job status {slurm_job_id}: {e}")
            return "UNKNOWN"

    
    def kill_job(
        self, 
        slurm_job_id: str
    )-> None:
        
        """
        终止任务 (scancel)
        """
        
        command = [
            "scancel",
            "--signal=KILL",
            slurm_job_id,
        ]
        
        try:
            subprocess.run(command, check=False)
        except Exception as error:
            logger.error(f"scancel failed for job {slurm_job_id}: {error}")