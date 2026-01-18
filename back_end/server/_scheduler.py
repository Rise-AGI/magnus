# back_end/server/_scheduler.py
import os
import json
import logging
import traceback
import subprocess
from datetime import datetime
from sqlalchemy.orm import Session
from typing import Any, List, Dict
from pywheels.file_tools import guarantee_file_exist, delete_file
from .database import SessionLocal
from .models import *
from ._slurm_manager import SlurmManager, SlurmResourceError
from ._magnus_config import magnus_config


__all__ = [
    "scheduler",
]


magnus_workspace_path = f"{magnus_config['server']['root']}/workspace"
guarantee_file_exist(magnus_workspace_path, is_directory=True)


logger = logging.getLogger(__name__)


class MagnusScheduler:
    
    def __init__(
        self,
    ):
        # 初始化 SLURM 管理器；严格模式，无 SLURM 环境会报错
        try:
            self.slurm_manager = SlurmManager()
            self.enabled = True
        except RuntimeError as e:
            logger.critical(f"Scheduler disabled due to missing SLURM: {e}")
            self.enabled = False
            
        self.last_snapshot_time = datetime.min


    def tick(
        self,
    ):
        """
        调度器心跳：同步状态 -> 决策调度
        此方法是同步的，将在后台线程中运行
        """
        if not self.enabled: return

        # 为每次 tick 创建独立的 DB 会话
        with SessionLocal() as db:
            try:
                self._sync_reality(db)
                self._make_decisions(db)
                self._record_snapshot(db)
            except Exception as e:
                logger.error(f"Scheduler tick failed: {e}", exc_info=True)
                
    
    def _record_snapshot(
        self, 
        db: Session,
    ):
        """
        记录集群资源快照
        """
        now = datetime.utcnow()
        if (now - self.last_snapshot_time).total_seconds() < \
            magnus_config["server"]["scheduler"]["snapshot_interval"]:
            return

        try:
            # 1. 获取 Slurm 侧的物理数据
            slurm_stats = self.slurm_manager.get_resource_snapshot()
            
            # 2. 获取 Magnus 侧的逻辑占用 (查库)
            # 统计所有 RUNNING 状态任务的 gpu_count 之和
            # 使用 func.sum 需要引入 sqlalchemy.sql.func，这里用 Python sum 简单处理避免改 import
            running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
            magnus_usage = sum(job.gpu_count for job in running_jobs)
            
            # 3. 入库
            snapshot = ClusterSnapshot(
                total_gpus = slurm_stats["total_gpus"],
                slurm_used_gpus = slurm_stats["slurm_used_gpus"],
                magnus_used_gpus = magnus_usage,
                timestamp = now
            )
            db.add(snapshot)
            db.commit()
            
            # 更新时间戳
            self.last_snapshot_time = now
            logger.debug(f"Recorded Cluster Snapshot: Total={snapshot.total_gpus}, Used={snapshot.slurm_used_gpus}, Magnus={magnus_usage}")
            
        except Exception as e:
            logger.error(f"Failed to record cluster snapshot: {e}")

    
    def _sync_reality(
        self, 
        db: Session,
    ):
        """
        同步状态并收割指标
        """
        # 获取所有我们认为正在运行的任务
        running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
        for job in running_jobs:
            if not job.slurm_job_id:
                # 异常数据修复
                logger.warning(f"Job {job.id} is RUNNING but has no slurm_id. Marking FAILED.")
                job.status = JobStatus.FAILED
                continue
            
            # 收割利用率数据入库
            self._harvest_job_metrics(db, job)

            # 询问 SLURM 真实状态
            real_status = self.slurm_manager.check_job_status(job.slurm_job_id)
            
            # 🔍 核心修改：基于信标的双重验证
            if real_status == "COMPLETED":
                # 1. 验证成功信标 (Control Flow)
                marker_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_success"
                
                if os.path.exists(marker_path):
                    # 信标存在 -> 真正的成功
                    logger.info(f"Job {job.id} completed successfully (Marker Verified).")
                    job.status = JobStatus.SUCCESS

                    result_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_result"
                    if os.path.exists(result_path):
                        # 标记结果已就绪
                        job.result = ".magnus_result" 
                        logger.info(f"Job {job.id} result file confirmed on disk.")
                    else:
                        job.result = None

                else:
                    # 信标不存在 -> 任务消失但未打卡 -> 视为失败 (scancel/crash)
                    logger.warning(f"Job {job.id} disappeared from queue but NO success marker found. Marking FAILED.")
                    job.status = JobStatus.FAILED
                
                job.slurm_job_id = None
                # 我们的爱情故事 不会再改变
                self._clean_up_working_table(job.id)
            
            elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                logger.warning(f"Job {job.id} failed in SLURM (Status: {real_status}).")
                job.status = JobStatus.FAILED
                job.slurm_job_id = None
                self._clean_up_working_table(job.id)
            
            # 如果是 PENDING/RUNNING，保持不变，信任 SLURM
            
            db.commit()
    
    
    def _harvest_job_metrics(
        self,
        db: Session,
        job: Job,
    )-> None:
        """
        持久化瞬时 GPU 状态到 JobMetric 表
        """
        status_path = f"{magnus_workspace_path}/jobs/{job.id}/gpu_status.json"
        if not os.path.exists(status_path):
            return
        
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                raw_data: List[Dict[str, Any]] = json.load(f)
            
            # For robustness
            processed_status = []
            for i in range(job.gpu_count):
                if i < len(raw_data):
                    processed_status.append(raw_data[i])
                else:
                    processed_status.append({
                        "index": i, 
                        "utilization_gpu": 0, 
                        "utilization_memory": 0,
                    })

            metric_record = JobMetric(
                job_id = job.id,
                timestamp = datetime.utcnow(),
                status_json = json.dumps(processed_status)
            )
            db.add(metric_record)
            
        except Exception as e:
            logger.error(f"Failed to harvest metrics for job {job.id}: {e}")

    
    def _make_decisions(
        self, 
        db: Session,
    ):
        """
        第二阶段：调度决策
        策略：Strict FIFO Blocking (严格队首阻塞)
        一旦高优先级任务因资源不足受阻，立即停止调度，防止后排小任务插队导致饥饿。
        """
        
        real_free_gpus = self.slurm_manager.get_cluster_free_gpus()
        
        # 1. 获取所有待调度任务
        candidates = db.query(Job).filter(
            Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
        ).all()
        if not candidates: return

        # 2. 严格排序：优先级 (A1>A2>B1>B2) > 创建时间 (FIFO)
        priority_map = {
            JobType.A1: 4, JobType.A2: 3,
            JobType.B1: 2, JobType.B2: 1,
        }
        candidates.sort(
            key = lambda x: (priority_map[x.job_type], -x.created_at.timestamp()), 
            reverse = True,
        )

        for job in candidates:
            job_launched = False
            
            # --- 尝试 A: 资源充足，直接启动 ---
            if real_free_gpus >= job.gpu_count:
                if self._start_job(db, job):
                    real_free_gpus -= job.gpu_count
                    job_launched = True
            
            # --- 尝试 B: A 类任务抢占 B 类 ---
            elif job.job_type in [JobType.A1, JobType.A2]:
                needed = job.gpu_count - real_free_gpus
                
                # 寻找受害者 (B类 Running)
                potential_victims = db.query(Job).filter(
                    Job.status == JobStatus.RUNNING,
                    Job.job_type.in_([JobType.B1, JobType.B2])
                ).all()
                
                # 优先杀 B2，同优先级先杀晚启动的 (LIFO)
                kill_priority = {JobType.B2: 1, JobType.B1: 0}
                potential_victims.sort(
                    key = lambda x: (
                        kill_priority.get(x.job_type, 0),
                        x.start_time.timestamp() if x.start_time else 0
                    ), 
                    reverse = True,
                )
                
                victims = []
                recovered_gpus = 0
                
                for v in potential_victims:
                    if recovered_gpus >= needed: break
                    victims.append(v)
                    recovered_gpus += v.gpu_count
                
                # 仅当受害者足够填补缺口时才动手
                if recovered_gpus >= needed:
                    logger.info(f"Preemption: Job {job.id} (Type {job.job_type}) reclaiming {needed} GPUs.")
                    for v in victims: self._kill_and_pause(db, v)
                    
                    real_free_gpus += recovered_gpus
                    if self._start_job(db, job):
                        real_free_gpus -= job.gpu_count
                        job_launched = True
            
            # --- 核心：阻塞逻辑 ---
            if not job_launched:
                # 队首任务受阻，立刻中断循环，禁止后续任务插队
                logger.debug(f"Queue Blocked: Job {job.id} waiting for resources. Stopping scheduling.")
                break

    
    def _start_job(
        self, 
        db: Session, 
        job: Job,
    )-> bool:
        
        """
        原子操作：提交 SLURM + 更新 DB
        使用 Python Wrapper 自动处理带认证的 Git Clone
        [Magnus Update] 内置轻量级 GPU Spy Thread
        """
        
        try:
        
            user_magnus = magnus_config["cluster"]["resources"]["runner"]["default_user"]
            effective_runner = job.runner if job.runner is not None \
                                else user_magnus
            
            job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
            guarantee_file_exist(f"{job_working_table}/slurm", is_directory=True)
            acl_cmd = [
                "setfacl", "-R",
                "-m", f"u:{effective_runner}:rwx",
                "-d", "-m", f"u:{user_magnus}:rwx",
                "-d", "-m", f"u:{effective_runner}:rwx",
                job_working_table,
            ]
            subprocess.run(acl_cmd, check=True)
            
            magnus_uv_cache = f"{magnus_config['server']['root']}/uv_cache/{effective_runner}"
            guarantee_file_exist(magnus_uv_cache, is_directory=True)
            acl_cmd_cache = [
                "setfacl",
                "-m", f"u:{effective_runner}:rwx",
                "-d", "-m", f"u:{effective_runner}:rwx",
                magnus_uv_cache,
            ]
            subprocess.run(acl_cmd_cache, check=True)
            
            gpu_status_path = f"{job_working_table}/gpu_status.json"
            try:
                with open(gpu_status_path, "w", encoding="utf-8") as f:
                    f.write("[]")
                os.chmod(gpu_status_path, 0o666)
            except Exception as e:
                logger.error(
                    f"Failed to initialize gpu_status.json: {e}.\nTraceback:\n{traceback.format_exc()}"
                )
            
            success_marker_path = f"{job_working_table}/.magnus_success"
            result_marker_path = f"{job_working_table}/.magnus_result"
            if os.path.exists(success_marker_path):
                try:
                    os.remove(success_marker_path)
                except OSError:
                    pass
            if os.path.exists(result_marker_path):
                try:
                    os.remove(result_marker_path)
                except OSError:
                    pass
            
            auth_repo_url = f"git@github.com:{job.namespace}/{job.repo_name}.git"
            
            spy_gpu_interval = magnus_config["server"]["scheduler"]["spy_gpu_interval"]
            conda_shell_script_path = magnus_config["server"]["scheduler"]["conda_shell_script_path"]
            execution_conda_environment = magnus_config["server"]["scheduler"]["execution_conda_environment"]
        
        except Exception as error:
            logger.error(f"Job {job.id} submission error: {error}\nTraceback:\n{traceback.format_exc()}")
            job.status = JobStatus.FAILED
            db.commit()
            return False

        # Python Wrapper
        wrapper_content = f"""import os
import sys
import traceback
import subprocess
import threading
import time
import json

# 轻量级 GPU 监控线程
def _spy_gpu_thread(
    status_path, 
):
    interval = {spy_gpu_interval}
    while True:
        try:
            cmd = [
                "nvidia-smi", 
                "--query-gpu=index,utilization.gpu,utilization.memory", 
                "--format=csv,noheader,nounits"
            ]
            output = subprocess.check_output(cmd, encoding="utf-8", timeout=2)
            
            gpu_list = []
            for line in output.strip().split('\\n'):
                if not line.strip(): continue
                parts = line.split(',')
                if len(parts) == 3:
                    gpu_list.append({{
                        "index": int(parts[0].strip()),
                        "utilization_gpu": int(parts[1].strip()),
                        "utilization_memory": int(parts[2].strip())
                    }})
            
            temp_path = status_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(gpu_list, f)
            
            os.replace(temp_path, status_path)
            
        except Exception:
            pass
            
        time.sleep(interval)

def main():

    repo_url = {repr(auth_repo_url)}
    branch = {repr(job.branch)}
    commit_sha = {repr(job.commit_sha)}
    
    work_dir = {repr(job_working_table)}
    repo_dir = os.path.join(work_dir, "repository")
    success_marker_path = {repr(success_marker_path)}
    result_marker_path = {repr(result_marker_path)}
    gpu_status_path = {repr(gpu_status_path)}
    
    user_cmd_str = {repr(job.entry_command)}
    if "sudo" in user_cmd_str:
        raise RuntimeError("Error: Not privileged.")
        
    effective_runner = {repr(effective_runner)}
    if effective_runner == "root":
        raise RuntimeError("Error: Not privileged.")
    
    try:
        spy = threading.Thread(
            target = _spy_gpu_thread, 
            args = (gpu_status_path, ), 
            daemon = True,
        )
        spy.start()
    except Exception as e:
        print(f"Magnus Warning: Failed to start GPU spy: {{e}}", file=sys.stderr)

    try:
    
        stealth_key_path = os.path.expanduser("~/.ssh/.sys_fallback")
        git_ssh_cmd = f"ssh -i {{stealth_key_path}} -F /dev/null -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
        git_env = os.environ.copy()
        git_env["GIT_SSH_COMMAND"] = git_ssh_cmd
        
        subprocess.check_call(
            ["git", "clone", "--branch", branch, "--single-branch", repo_url, repo_dir],
            stdout = subprocess.DEVNULL,
            stderr = subprocess.PIPE,
            env = git_env,
        )
        
        subprocess.check_call(
            ["git", "checkout", commit_sha],
            cwd = repo_dir,
            stdout = subprocess.DEVNULL,
            stderr = subprocess.PIPE,
            env = git_env,
        )
    
    except subprocess.CalledProcessError as error:
        print(f"Magnus System Error: Failed to setup repository environment.\\nTraceback: \\n{{traceback.format_exc()}}", file=sys.stderr, flush=True)
        if error.stderr:
            print(f"Git Error: {{error.stderr.decode().strip()}}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as error:
        print(f"Magnus System Error: {{error}}", file=sys.stderr)
        sys.exit(1)

    try:
        os.chdir(repo_dir)
        conda_shell_script_path = {repr(conda_shell_script_path)}
        execution_conda_environment = {repr(execution_conda_environment)}
        
        setup_commands = [
            "set -e",
            f"export HOME=$(getent passwd {effective_runner} | cut -d: -f6)",
            f"source '{{conda_shell_script_path}}'",
            f"conda activate {{execution_conda_environment}}",
            "unset VIRTUAL_ENV",
            "export UV_CACHE_DIR={magnus_uv_cache}",
        ]
        # 无论是否有结果，只要执行到这里，就写入 .magnus_success
        epilogue_command = f\"\"\"
echo -n "success" > {{success_marker_path}}
\"\"\"

        full_command = "\\n".join(setup_commands) + f"\\n\\n{{user_cmd_str}}\\n" + epilogue_command

        ret_code = subprocess.call(
            full_command,
            shell = True,
            executable = "/bin/bash",
        )
        
        sys.exit(ret_code)
    
    except Exception as error:
        print(f"Magnus Execution Error: {{error}}\\nTraceback: \\n{{traceback.format_exc()}}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":

    main()
"""
        
        # 将 Wrapper 写入文件
        wrapper_path = f"{job_working_table}/wrapper.py"
        try:
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(wrapper_content)
        except IOError as e:
            logger.error(f"Failed to write wrapper script for Job {job.id}: {e}")
            return False

        try:
            # 提交任务：运行 wrapper.py
            slurm_id = self.slurm_manager.submit_job(
                entry_command = f"python3 {wrapper_path}",
                gpus = job.gpu_count,
                job_name = job.task_name,
                gpu_type = job.gpu_type,
                output_path = f"{job_working_table}/slurm/output.txt",
                slurm_latency = magnus_config["server"]["scheduler"]["slurm_latency"],
                overwrite_output = False,
                runner = effective_runner,
                cpu_count = job.cpu_count,
                memory_demand = job.memory_demand,
                token = job.user.token if job.user.token is not None else "",
            )
            
            job.status = JobStatus.RUNNING
            job.slurm_job_id = slurm_id
            job.start_time = datetime.utcnow()
            db.commit()
            
            logger.info(f"Job {job.id} started (SLURM: {slurm_id}, Branch: {job.branch})")
            return True
            
        except SlurmResourceError:
            logger.warning(f"Job {job.id} submission failed: Resources unavailable immediately.")
            return False
            
        except Exception as error:
            logger.error(f"Job {job.id} submission error: {error}")
            job.status = JobStatus.FAILED
            db.commit()
            return False

    
    def _kill_and_pause(
        self,
        db: Session,
        job: Job,
    ):
        """
        残忍操作：Kill SLURM Job -> 标记为 Paused
        """
        if job.slurm_job_id:
            logger.info(f"Killing victim job {job.id} (SLURM: {job.slurm_job_id})")
            self.slurm_manager.kill_job(
                job.slurm_job_id,
                runner = job.runner if job.runner is not None else "magnus",
                token = job.user.token if job.user.token is not None else "",
            )
            
        self._clean_up_working_table(job.id)
        
        job.status = JobStatus.PAUSED
        job.slurm_job_id = None
        job.start_time = None
        db.commit()
        
        
    def terminate_job(
        self,
        db: Session,
        job: Job,
    ) -> None:
        """
        供 API 调用的主动终止接口
        动作：Kill Slurm -> 清理工作区 -> 标记 TERMINATED
        """
        if not self.enabled:
            logger.warning("Scheduler disabled, skipping termination logic.")
            return

        if job.slurm_job_id:
            logger.info(f"Terminating job {job.id} (SLURM: {job.slurm_job_id}) by user request.")
            self.slurm_manager.kill_job(
                job.slurm_job_id,
                runner = job.runner if job.runner is not None else "magnus",
                token = job.user.token if job.user.token is not None else "",
            )

        self._clean_up_working_table(job.id)
        
        job.status = JobStatus.TERMINATED
        job.slurm_job_id = None
        job.start_time = None 
        
        db.commit()
        
        
    def _clean_up_working_table(
        self,
        job_id: str,
    )-> None:
        
        job_working_table = f"{magnus_workspace_path}/jobs/{job_id}"
        try:
            delete_file(os.path.join(job_working_table, "repository"))
            delete_file(os.path.join(job_working_table, "wrapper.py"))
            delete_file(os.path.join(job_working_table, ".magnus_success"))
        except Exception as error:
            logger.warning(
                f"Clean up working table of job {job_id} failed:\n{error}\n"
                f"Traceback:\n{traceback.format_exc()}"
            )
            

scheduler = MagnusScheduler()