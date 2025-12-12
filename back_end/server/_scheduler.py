# back_end/server/_scheduler.py
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import Job, JobStatus, JobType
from library.functional._slurm_manager import SlurmManager, SlurmResourceError


logger = logging.getLogger(__name__)

class MagnusScheduler:
    def __init__(self):
        # 初始化 SLURM 管理器 (严格模式，无 SLURM 环境会报错)
        try:
            self.slurm = SlurmManager()
            self.enabled = True
        except RuntimeError as e:
            logger.critical(f"Scheduler disabled due to missing SLURM: {e}")
            self.enabled = False

    def tick(self):
        """
        调度器心跳：同步状态 -> 决策调度
        注意：此方法是同步的，将在后台线程中运行
        """
        if not self.enabled:
            return

        # 为每次 tick 创建独立的 DB 会话
        with SessionLocal() as db:
            try:
                self._sync_reality(db)
                self._make_decisions(db)
            except Exception as e:
                logger.error(f"Scheduler tick failed: {e}", exc_info=True)

    def _sync_reality(self, db: Session):
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

    def _make_decisions(self, db: Session):
        """
        第二阶段：调度决策 (排队与抢占) - Debug Enhanced
        """
        # 1. 获取资源
        real_free_gpus = self.slurm.get_cluster_free_gpus()
        # logger.info(f"🔎 [Tick] Free GPUs: {real_free_gpus}")
        
        # 2. 获取候选者
        candidates = db.query(Job).filter(
            Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
        ).all()
        
        if not candidates:
            return

        # 排序
        priority_map = {
            JobType.A1: 4, JobType.A2: 3,
            JobType.B1: 2, JobType.B2: 1
        }
        candidates.sort(key=lambda x: (priority_map[x.job_type], -x.created_at.timestamp()), reverse=True)

        for job in candidates:
            logger.info(f"🤔 Considering Job {job.id} | Type: {job.job_type} | Need: {job.gpu_count} | Status: {job.status}")
            
            # === 情况 A: 资源充足 ===
            if real_free_gpus >= job.gpu_count:
                logger.info(f"✅ Resource sufficient ({real_free_gpus} >= {job.gpu_count}). Starting Job {job.id}...")
                if self._start_job(db, job):
                    real_free_gpus -= job.gpu_count
            
            # === 情况 B: 尝试抢占 ===
            elif job.job_type in [JobType.A1, JobType.A2]:
                needed = job.gpu_count - real_free_gpus
                logger.info(f"⚔️ Attempting preemption for Job {job.id}. Need {needed} more GPUs.")
                
                # 寻找受害者
                potential_victims = db.query(Job).filter(
                    Job.status == JobStatus.RUNNING,
                    Job.job_type.in_([JobType.B1, JobType.B2])
                ).all()
                
                logger.info(f"👀 Found {len(potential_victims)} potential victims (B-Class Running jobs).")

                # LIFO 排序
                potential_victims.sort(
                    key=lambda x: x.start_time.timestamp() if x.start_time else 0, 
                    reverse=True
                )
                
                victims = []
                recovered_gpus = 0
                
                for v in potential_victims:
                    if recovered_gpus >= needed:
                        break
                    victims.append(v)
                    recovered_gpus += v.gpu_count
                    logger.info(f"   -> Candidate Victim: {v.id} (Type: {v.job_type}, GPUs: {v.gpu_count})")
                
                if recovered_gpus >= needed:
                    logger.info(f"💀 EXECUTE PREEMPTION: Killing {len(victims)} jobs to free {recovered_gpus} GPUs.")
                    
                    # 1. 处决
                    for v in victims:
                        self._kill_and_pause(db, v)
                    
                    # 2. 模拟资源释放
                    real_free_gpus += recovered_gpus
                    
                    # 3. 启动大哥
                    # ⚠️ 关键修正：抢占后启动失败不应回滚资源，因为受害者已经死了，资源确实会空出来
                    # 这里的逻辑是：即便这次启动失败（比如Slurm还没反应过来），
                    # 下次Tick时资源就会变成 Free，大哥就能正常上位了。
                    if self._start_job(db, job):
                        real_free_gpus -= job.gpu_count
                    else:
                        logger.warning(f"⚠️ Preemption done but start failed (Slurm delay?). Job {job.id} will retry next tick.")
                else:
                    logger.info(f"❌ Preemption failed: Not enough B-Class jobs (Recoverable: {recovered_gpus}, Needed: {needed}).")

            else:
                logger.info(f"💤 Job {job.id} is Low Priority ({job.job_type}) and resources are full. Waiting.")

    def _start_job(self, db: Session, job: Job) -> bool:
        """
        原子操作：提交 SLURM + 更新 DB
        """
        try:
            # 这里的 submit_job 使用了 --immediate
            # 如果资源不足，会抛出 SlurmResourceError
            slurm_id = self.slurm.submit_job(job.entry_command, job.gpu_count)
            
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
            
        except Exception as e:
            # 其他严重错误 (比如 sbatch 命令写错)
            logger.error(f"Job {job.id} submission error: {e}")
            job.status = JobStatus.FAILED
            db.commit()
            return False

    def _kill_and_pause(self, db: Session, job: Job):
        """
        残忍操作：Kill SLURM Job -> 标记为 Paused
        """
        if job.slurm_job_id:
            logger.info(f"Killing victim job {job.id} (SLURM: {job.slurm_job_id})")
            self.slurm.kill_job(job.slurm_job_id)
        
        job.status = JobStatus.PAUSED
        job.slurm_job_id = None
        job.start_time = None # 清除运行时间
        db.commit()

# 全局单例
scheduler = MagnusScheduler()