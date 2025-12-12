# back_end/server/_scheduler.py
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from pywheels.file_tools import guarantee_file_exist
from .database import SessionLocal
from .models import Job, JobStatus, JobType
from library.functional._slurm_manager import SlurmManager, SlurmResourceError
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
            self.slurm = SlurmManager()
            self.enabled = True
        except RuntimeError as e:
            logger.critical(f"Scheduler disabled due to missing SLURM: {e}")
            self.enabled = False

    
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
            except Exception as e:
                logger.error(f"Scheduler tick failed: {e}", exc_info=True)

    
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
            real_status = self.slurm.check_job_status(job.slurm_job_id)
            
            if real_status == "COMPLETED":
                logger.info(f"Job {job.id} completed successfully.")
                job.status = JobStatus.SUCCESS
                job.slurm_job_id = None # 清理 ID
            
            elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                logger.warning(f"Job {job.id} failed in SLURM (Status: {real_status}).")
                job.status = JobStatus.FAILED
                job.slurm_job_id = None
            
            # 如果是 PENDING/RUNNING，保持不变，信任 SLURM
            
        db.commit()

    
    def _make_decisions(
        self, 
        db: Session,
    ):
        
        """
        第二阶段：调度决策 (排队与抢占)
        """
        
        real_free_gpus = self.slurm.get_cluster_free_gpus()
        
        candidates = db.query(Job).filter(
            Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
        ).all()
        if not candidates: return

        priority_map = {
            JobType.A1: 4, JobType.A2: 3,
            JobType.B1: 2, JobType.B2: 1,
        }
        candidates.sort(
            key = lambda x: (priority_map[x.job_type], -x.created_at.timestamp()), 
            reverse = True,
        )

        for job in candidates:
            
            # 资源充足
            if real_free_gpus >= job.gpu_count:
                if self._start_job(db, job):
                    real_free_gpus -= job.gpu_count
            
            # 资源不充足，但是是 A 类，可以抢 B 类
            elif job.job_type in [JobType.A1, JobType.A2]:
                needed = job.gpu_count - real_free_gpus
                # 寻找受害者
                potential_victims = db.query(Job).filter(
                    Job.status == JobStatus.RUNNING,
                    Job.job_type.in_([JobType.B1, JobType.B2])
                ).all()
                # LIFO 排序，干掉晚上机的 B 类、而不是搞掉跑了很长时间的 B 类
                potential_victims.sort(
                    key = lambda x: x.start_time.timestamp() if x.start_time else 0, 
                    reverse = True,
                )
                
                victims = []
                recovered_gpus = 0
                
                for v in potential_victims:
                    if recovered_gpus >= needed:
                        break
                    victims.append(v)
                    recovered_gpus += v.gpu_count
                if recovered_gpus >= needed:
                    # 处决
                    for v in victims: self._kill_and_pause(db, v)
                    # 模拟资源释放
                    real_free_gpus += recovered_gpus
                    # 启动大哥
                    if self._start_job(db, job):
                        real_free_gpus -= job.gpu_count
                    else:
                        pass
                else:
                    pass
            else:
                pass
    
    
    def _start_job(
        self, 
        db: Session, 
        job: Job
    )-> bool:
        
        """
        原子操作：提交 SLURM + 更新 DB
        """
        
        job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
        guarantee_file_exist(f"{job_working_table}/slurm", is_directory=True)
        
        try:
            # 这里的 submit_job 模拟了 --immediate 模式
            # 如果资源不足，会抛出 SlurmResourceError
            slurm_id = self.slurm.submit_job(
                entry_command = job.entry_command, 
                gpus = job.gpu_count,
                job_name = job.task_name,
                gpu_type = job.gpu_type,
                output_path = f"{job_working_table}/slurm/output.txt",
                slurm_latency = magnus_config["server"]["scheduler"]["slurm_latency"],
                overwrite_output = False,
            )
            
            job.status = JobStatus.RUNNING
            job.slurm_job_id = slurm_id
            job.start_time = datetime.utcnow() # 记录开始时间，用于 LIFO 排序
            db.commit()
            
            logger.info(f"Job {job.id} started successfully (SLURM ID: {slurm_id})")
            return True
            
        except SlurmResourceError:
            # 资源竞争失败 (可能被外部人员抢了，或者刚刚 kill 的资源还没释放完)
            logger.warning(f"Job {job.id} submission failed: Resources unavailable immediately.")
            return False
            
        except Exception as error:
            # 其他严重错误 (比如 sbatch 命令写错)
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
            self.slurm.kill_job(job.slurm_job_id)
        
        job.status = JobStatus.PAUSED
        job.slurm_job_id = None
        job.start_time = None
        db.commit()


scheduler = MagnusScheduler()