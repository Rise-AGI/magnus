# back_end/server/_slurm_manager.py
import os
import json
import time

import logging
import traceback
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple


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

    def __init__(self) -> None:
        pass


    def _get_capacity_and_usage(self)-> Tuple[int, int]:
        """
        [Internal] 复用逻辑：获取 (总容量, 当前总占用)
        """
        try:
            # --- 步骤 1: 获取总容量 (Configured) ---
            cmd_capacity = ["scontrol", "show", "node", "--future"]
            res_capacity = subprocess.run(
                cmd_capacity, capture_output=True, text=True, check=True
            )

            total_capacity = 0
            for line in res_capacity.stdout.split('\n'):
                line = line.strip()
                if line.startswith("Gres=") and "gpu" in line:
                    try:
                        gres_part = line.split("Gres=")[1].split()[0].split('(')[0]
                        count = int(gres_part.split(':')[-1])
                        total_capacity += count
                    except (ValueError, IndexError):
                        pass

            # --- 步骤 2: 获取当前占用 (Allocated) ---
            cmd_usage = ["squeue", "--states=RUNNING", "--noheader", "--format=%D %b"]
            res_usage = subprocess.run(
                cmd_usage, capture_output=True, text=True, check=True
            )

            total_allocated = 0
            for line in res_usage.stdout.strip().split('\n'):
                if not line.strip(): continue
                parts = line.split(maxsplit=1)
                if len(parts) < 2: continue

                num_nodes_str, gres_req = parts[0], parts[1]
                if "gpu" not in gres_req: continue

                try:
                    num_nodes = int(num_nodes_str)
                    gres_req = gres_req.split('(')[0]
                    gpu_per_node = int(gres_req.split(':')[-1])
                    total_allocated += (num_nodes * gpu_per_node)
                except (ValueError, IndexError):
                    pass

            return total_capacity, total_allocated

        except Exception as e:
            logger.error(f"Error querying cluster resources: {e}")
            return 0, 0


    def get_cpu_and_memory(self) -> Dict[str, int]:
        """
        从 scontrol show node 解析 CPU 和内存的总量与已分配量。
        返回 {"cpu_total", "cpu_alloc", "mem_total_mb", "mem_alloc_mb"}
        """
        try:
            res = subprocess.run(
                ["scontrol", "show", "node", "--future"],
                capture_output=True, text=True, check=True,
            )

            cpu_total = 0
            cpu_alloc = 0
            mem_total = 0
            mem_alloc = 0

            for line in res.stdout.split('\n'):
                line = line.strip()
                if "CPUTot=" in line:
                    # CPUAlloc=34 CPUEfctv=192 CPUTot=192 CPULoad=...
                    for part in line.split():
                        if part.startswith("CPUTot="):
                            cpu_total += int(part.split("=")[1])
                        elif part.startswith("CPUAlloc="):
                            cpu_alloc += int(part.split("=")[1])
                elif line.startswith("RealMemory="):
                    # RealMemory=515000 AllocMem=102400 ...
                    for part in line.split():
                        if part.startswith("RealMemory="):
                            mem_total += int(part.split("=")[1])
                        elif part.startswith("AllocMem="):
                            mem_alloc += int(part.split("=")[1])

            return {
                "cpu_total": cpu_total,
                "cpu_alloc": cpu_alloc,
                "mem_total_mb": mem_total,
                "mem_alloc_mb": mem_alloc,
            }
        except Exception as e:
            logger.error(f"Error querying CPU/memory: {e}")
            return {"cpu_total": 0, "cpu_alloc": 0, "mem_total_mb": 0, "mem_alloc_mb": 0}


    def get_cluster_free_gpus(
        self
    )-> int:
        
        cap, alloc = self._get_capacity_and_usage()
        return max(0, cap - alloc)


    def get_resource_snapshot(
        self,
    )-> Dict:

        cap, alloc = self._get_capacity_and_usage()
        return {
            "total_gpus": cap,
            "slurm_used_gpus": alloc,
        }


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
    )-> str:
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
        
    )-> str:
        
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


    def check_job_status(
        self, 
        slurm_job_id: str,
    )-> str:
        
        """
        查询 Slurm 任务状态
        
        注意：squeue 查不到时默认视为 COMPLETED，因为 FAILED 通常会残留记录。
        """
        
        command = ["squeue", "-h", "-j", slurm_job_id, "-o", "%t"]
        
        try:
            result = subprocess.run(
                command, 
                capture_output = True,
                text = True,
            )
            state = result.stdout.strip()
            
            if not state:
                return "COMPLETED"

            # R=Running, PD=Pending, CG=Completing, CD=Completed
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
        slurm_job_id: str,
        runner: str,
        token: str,
    )-> None:
        
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


    def get_all_running_tasks(
        self,
    )-> List[Dict]:
        
        """
        获取所有正在运行的 Slurm 任务详情
        
        关键坑点：Slurm Job Completion Caching
        squeue --json 可能会返回内存中残留的已结束任务（即便使用了 --states 过滤）。
        因此必须在代码层面显式检查 job_state 是否包含 "RUNNING"。
        """
        
        default_gpu_model = "rtx5090"
        command = ["squeue", "--states=RUNNING", "--json"]
        
        try:
            result = subprocess.run(
                command, 
                capture_output = True, 
                text = True,
                check = True,
            )
            data = json.loads(result.stdout)
            
            tasks = []
            
            for job in data.get("jobs", []):
                try:
                    # 严格过滤非 Running 状态的任务
                    states = job.get("job_state", [])
                    if "RUNNING" not in states:
                        continue
                    
                    # 1. 解析基础信息
                    job_id = str(job.get("job_id"))
                    user = job.get("user_name")
                    name = job.get("name")
                    raw_start_time = job.get("start_time")
                    start_ts = None
                    if isinstance(raw_start_time, dict):
                        # 新版 Slurm: {"number": 1234567890, ...}
                        start_ts = raw_start_time.get("number")
                    elif isinstance(raw_start_time, int):
                        # 旧版 Slurm: 1234567890
                        start_ts = raw_start_time 
                    if start_ts:
                        start_time_str = datetime.fromtimestamp(start_ts).isoformat()
                    else:
                        start_time_str = datetime.now().isoformat()

                    # 2. 解析 GPU 资源
                    # 优先解析 gres_detail (e.g., "gpu:rtx5090:1(IDX:0)")
                    gpu_count = 0
                    gpu_type = default_gpu_model
                    
                    gres_details = job.get("gres_detail", [])
                    
                    for item in gres_details:
                        if "gpu" not in item:
                            continue
                            
                        parts = item.split(':')
                        
                        try:
                            # 提取数量: 1(IDX:0)->1
                            count_str = parts[-1].split('(')[0]
                            count = int(count_str)
                            gpu_count += count
                        except (ValueError, IndexError):
                            pass
                            
                        # 尝试提取型号
                        if len(parts) >= 3:
                            model_raw = parts[1]
                            if model_raw.lower().startswith("rtx"):
                                gpu_type = model_raw.upper().replace("RTX", "RTX ")
                            else:
                                gpu_type = model_raw.upper()

                    # Fallback: 如果 gres_detail 为空，尝试解析 tres_per_node
                    if gpu_count == 0:
                        tres = job.get("tres_per_node", "")
                        if "gpu" in tres:
                            try:
                                count = int(tres.split(':')[-1])
                                gpu_count = count
                            except ValueError:
                                pass

                    tasks.append({
                        "id": job_id,
                        "user": user,
                        "name": name,
                        "start_time": start_time_str,
                        "gpu_count": gpu_count,
                        "gpu_type": gpu_type,
                    })
                    
                except Exception as e:
                    logger.warning(f"Failed to parse job json: {e}\nTraceback:\n{traceback.format_exc()}")
                    continue
            
            return tasks

        except Exception as e:
            logger.error(f"Failed to query running tasks (json): {e}")
            return []