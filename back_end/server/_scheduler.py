# back_end/server/_scheduler.py
import os
import logging
import traceback
import subprocess
from datetime import datetime
from sqlalchemy.orm import Session
from pywheels.file_tools import guarantee_file_exist, delete_file
from .database import SessionLocal
from .models import Job, JobStatus, JobType, ClusterSnapshot
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
        第一阶段：同步现实世界 (SLURM) 的状态到数据库
        """
        # 获取所有我们认为正在运行的任务
        running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
        for job in running_jobs:
            if not job.slurm_job_id:
                # 异常数据修复
                logger.warning(f"Job {job.id} is RUNNING but has no slurm_id. Marking FAILED.")
                job.status = JobStatus.FAILED
                continue

            # 询问 SLURM 真实状态
            real_status = self.slurm_manager.check_job_status(job.slurm_job_id)
            
            # 🔍 核心修改：基于信标的双重验证
            if real_status == "COMPLETED":
                # 构造信标路径 (Magnus 业务逻辑)
                marker_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_success"
                
                if os.path.exists(marker_path):
                    # 信标存在 -> 真正的成功
                    logger.info(f"Job {job.id} completed successfully (Marker Verified).")
                    job.status = JobStatus.SUCCESS
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
                
                # 优先杀晚启动的 (LIFO)
                potential_victims.sort(
                    key = lambda x: x.start_time.timestamp() if x.start_time else 0, 
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
    ) -> bool:
        """
        原子操作：提交 SLURM + 更新 DB
        使用 Python Wrapper 自动处理带认证的 Git Clone
        """
        
        # magnus 这个用户名写死就挺好，效仿 slurm
        user_magnus = "magnus"
        effective_runner = job.runner if job.runner is not None \
                            else user_magnus
        
        job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
        guarantee_file_exist(f"{job_working_table}/slurm", is_directory=True)
        
        magnus_uv_cache = f"{magnus_config['server']['root']}/uv_cache"
        guarantee_file_exist(magnus_uv_cache, is_directory=True)
        
        acl_cmd = [
            "setfacl", "-R",
            "-m", f"u:{effective_runner}:rwx",
            "-d", "-m", f"u:{user_magnus}:rwx",
            "-d", "-m", f"u:{effective_runner}:rwx",
            job_working_table
        ]
        subprocess.run(acl_cmd, check=True)
        
        # 定义成功信标路径
        success_marker_path = f"{job_working_table}/.magnus_success"
        
        # 清理旧信标
        if os.path.exists(success_marker_path):
            try:
                os.remove(success_marker_path)
            except OSError:
                pass
        
        # 用 ssh 连 github
        auth_repo_url = f"git@github.com:{job.namespace}/{job.repo_name}.git"
        
        conda_shell_script_path = magnus_config["server"]["scheduler"]["conda_shell_script_path"]
        execution_conda_environment = magnus_config["server"]["scheduler"]["execution_conda_environment"]

        # 构造 Python Wrapper 脚本内容
        wrapper_content = f"""import os
import sys
import traceback
import subprocess


def main():

    repo_url = {repr(auth_repo_url)}
    branch = {repr(job.branch)}
    commit_sha = {repr(job.commit_sha)}
    
    work_dir = {repr(job_working_table)}
    repo_dir = os.path.join(work_dir, "repository")
    marker_path = {repr(success_marker_path)}
    
    # --- 阶段 1: 准备代码环境 ---
    # 保持 stdout 干净，除非出错才打印到 stderr
    try:
        # Clone 指定分支，--single-branch 减少下载量
        subprocess.check_call(
            ["git", "clone", "--branch", branch, "--single-branch", repo_url, repo_dir],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        
        # Checkout 到指定 Commit
        subprocess.check_call(
            ["git", "checkout", commit_sha],
            cwd=repo_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    
    except subprocess.CalledProcessError as error:
        print(f"Magnus System Error: Failed to setup repository environment.\\nTraceback: \\n{{traceback.format_exc()}}", file=sys.stderr, flush=True)
        if error.stderr:
            print(f"Git Error: {{error.stderr.decode().strip()}}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as error:
        print(f"Magnus System Error: {{error}}", file=sys.stderr)
        sys.exit(1)

    # --- 阶段 2: 执行用户命令 ---
    try:
        # 切换到仓库根目录
        os.chdir(repo_dir)
        
        # 拿到原始命令
        user_cmd_str = {repr(job.entry_command)}
        if "sudo" in user_cmd_str:
            raise RuntimeError("Error: Not privileged.")
        
        # 拿到环境配置 (由外部注入)
        conda_shell_script_path = {repr(conda_shell_script_path)}
        execution_conda_environment = {repr(execution_conda_environment)}
        
        # 构造组合拳命令：source 脚本 -> 激活环境 -> 执行用户命令
        full_command = " && ".join([
            f"export HOME=/home/{effective_runner}",
            f"source '{{conda_shell_script_path}}'",
            f"conda activate {{execution_conda_environment}}",
            "unset VIRTUAL_ENV",
            "export UV_CACHE_DIR={magnus_uv_cache}",
            f"{{user_cmd_str}}"
        ])
        
        # 显式调用 /bin/bash 执行（sh 不支持 source）
        ret_code = subprocess.call(
            full_command,
            shell = True,
            executable = "/bin/bash",
        )
        
        if ret_code == 0:
            # 成功：创建信标
            with open(marker_path, "w") as f:
                f.write("success")
            sys.exit(0)
        else:
            # 失败：原样返回退出码，SLURM 会捕获
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
            self.slurm_manager.kill_job(job.slurm_job_id)
            
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
            self.slurm_manager.kill_job(job.slurm_job_id)

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