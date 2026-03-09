# back_end/server/_scheduler.py
import os
import json
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Any, List, Dict
from pywheels.file_tools import guarantee_file_exist, delete_file
from .database import SessionLocal
from .models import *
from ._slurm_manager import SlurmManager
from ._magnus_config import magnus_config
from ._resource_manager import resource_manager
from ._resource_manager import _image_to_sif_filename


__all__ = [
    "scheduler",
]


magnus_root = magnus_config['server']['root']
magnus_workspace_path = f"{magnus_root}/workspace"
magnus_container_cache_path = f"{magnus_root}/container_cache"
magnus_uv_cache_path = f"{magnus_root}/uv_cache"
guarantee_file_exist(magnus_workspace_path, is_directory=True)
guarantee_file_exist(magnus_container_cache_path, is_directory=True)
guarantee_file_exist(magnus_uv_cache_path, is_directory=True)


logger = logging.getLogger(__name__)


def _register_image_pulling(db: Session, image_uri: str, user_id: str) -> None:
    """Pull 开始前，标记镜像为 pulling（首次见到的 URI 才插入）。"""
    existing = db.query(CachedImage).filter(CachedImage.uri == image_uri).first()
    if existing:
        if existing.status == "failed":
            existing.status = "pulling"
            db.commit()
        return
    db.add(CachedImage(
        uri=image_uri,
        filename=_image_to_sif_filename(image_uri),
        user_id=user_id,
        status="pulling",
        size_bytes=0,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _finalize_image_status(db: Session, image_uri: str, success: bool) -> None:
    """Pull 结束后，更新镜像状态为 cached 或 failed。"""
    img = db.query(CachedImage).filter(CachedImage.uri == image_uri).first()
    if not img:
        return
    if success:
        sif_path = os.path.join(magnus_container_cache_path, _image_to_sif_filename(image_uri))
        try:
            img.size_bytes = os.stat(sif_path).st_size
        except OSError:
            img.size_bytes = 0
        img.status = "cached"
    else:
        img.status = "failed"
    db.commit()


class MagnusScheduler:

    def __init__(self):
        try:
            self.slurm_manager = SlurmManager()
            self.enabled = True
        except RuntimeError as e:
            logger.critical(f"Scheduler disabled due to missing SLURM: {e}")
            self.enabled = False
        self.last_snapshot_time = datetime.min.replace(tzinfo=timezone.utc)
        self.preparing_jobs: Dict[str, asyncio.Task] = {}  # job_id -> Task


    async def tick(self):
        """调度器心跳：同步状态 -> 决策调度"""
        if not self.enabled: return
        try:
            self._sync_reality()
            await self._make_decisions()
            self._record_snapshot()
        except Exception as e:
            logger.error(f"Scheduler tick failed: {e}", exc_info=True)


    def _record_snapshot(self):
        now = datetime.now(timezone.utc)
        if (now - self.last_snapshot_time).total_seconds() < \
            magnus_config["server"]["scheduler"]["snapshot_interval"]:
            return

        try:
            # Phase 1 — SLURM 调用（无 session）
            slurm_stats = self.slurm_manager.get_resource_snapshot()

            # Phase 2 — 写快照（短 session）
            with SessionLocal() as db:
                running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
                magnus_usage = sum(job.gpu_count for job in running_jobs)

                snapshot = ClusterSnapshot(
                    total_gpus = slurm_stats["total_gpus"],
                    slurm_used_gpus = slurm_stats["slurm_used_gpus"],
                    magnus_used_gpus = magnus_usage,
                    timestamp = now,
                )
                db.add(snapshot)
                db.commit()
                logger.debug(f"Recorded Cluster Snapshot: Total={snapshot.total_gpus}, Used={snapshot.slurm_used_gpus}, Magnus={magnus_usage}")
            self.last_snapshot_time = now
        except Exception as e:
            logger.error(f"Failed to record cluster snapshot: {e}")


    def _finalize_completed_job(self, job: Job) -> None:
        marker_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_success"
        if os.path.exists(marker_path):
            logger.info(f"Job {job.id} completed successfully (Marker Verified).")
            job.status = JobStatus.SUCCESS
            result_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_result"
            job.result = ".magnus_result" if os.path.exists(result_path) else None
            action_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_action"
            job.action = ".magnus_action" if os.path.exists(action_path) else None
        else:
            logger.warning(f"Job {job.id} completed but NO success marker found. Marking FAILED.")
            job.status = JobStatus.FAILED
        job.slurm_job_id = None
        self._clean_up_working_table(job.id)


    def _sync_reality(self):
        """同步 SLURM 真实状态到数据库"""
        # Phase 1 — 收集 job 信息（短 session）
        with SessionLocal() as db:
            queued_info = [
                (job.id, job.slurm_job_id)
                for job in db.query(Job).filter(Job.status == JobStatus.QUEUED).all()
            ]
            running_info = [
                (job.id, job.slurm_job_id)
                for job in db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
            ]

        # Phase 2 — SLURM 状态检查（无 session）
        slurm_statuses = {}
        for job_id, slurm_job_id in queued_info + running_info:
            if slurm_job_id:
                slurm_statuses[job_id] = self.slurm_manager.check_job_status(slurm_job_id)

        # Phase 3 — 批量更新（短 session）
        with SessionLocal() as db:
            for job_id, slurm_job_id in queued_info:
                try:
                    job = db.query(Job).filter(Job.id == job_id).first()
                    if not job or job.status != JobStatus.QUEUED:
                        continue

                    if not slurm_job_id:
                        logger.warning(f"Job {job.id} is QUEUED but has no slurm_id. Marking FAILED.")
                        job.status = JobStatus.FAILED
                        continue

                    real_status = slurm_statuses.get(job_id)
                    if real_status == "RUNNING":
                        job.status = JobStatus.RUNNING
                        job.start_time = datetime.now(timezone.utc)
                        logger.info(f"Job {job.id} started running in SLURM (ID: {slurm_job_id})")
                    elif real_status == "COMPLETED":
                        self._finalize_completed_job(job)
                    elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                        logger.warning(f"Job {job.id} failed in SLURM queue (Status: {real_status}).")
                        job.status = JobStatus.FAILED
                        job.slurm_job_id = None
                        self._clean_up_working_table(job.id)
                except Exception as e:
                    logger.error(f"Failed to sync QUEUED job {job_id}: {e}")

            for job_id, slurm_job_id in running_info:
                try:
                    job = db.query(Job).filter(Job.id == job_id).first()
                    if not job or job.status != JobStatus.RUNNING:
                        continue

                    if not slurm_job_id:
                        logger.warning(f"Job {job.id} is RUNNING but has no slurm_id. Marking FAILED.")
                        job.status = JobStatus.FAILED
                        continue

                    self._harvest_job_metrics(db, job)
                    real_status = slurm_statuses.get(job_id)
                    if real_status == "COMPLETED":
                        self._finalize_completed_job(job)
                    elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                        logger.warning(f"Job {job.id} failed in SLURM (Status: {real_status}).")
                        job.status = JobStatus.FAILED
                        job.slurm_job_id = None
                        self._clean_up_working_table(job.id)
                except Exception as e:
                    logger.error(f"Failed to sync RUNNING job {job_id}: {e}")

            db.commit()


    def _harvest_job_metrics(self, db: Session, job: Job)-> None:
        status_path = f"{magnus_workspace_path}/jobs/{job.id}/gpu_status.json"
        if not os.path.exists(status_path):
            return

        try:
            with open(status_path, "r", encoding="utf-8") as f:
                raw_data: List[Dict[str, Any]] = json.load(f)

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

            db.add(JobMetric(
                job_id = job.id,
                timestamp = datetime.now(timezone.utc),
                status_json = json.dumps(processed_status),
            ))
        except Exception as e:
            logger.error(f"Failed to harvest metrics for job {job.id}: {e}")


    async def _make_decisions(self):
        """
        调度决策 - 队头挂号模式

        状态流转：Preparing → Pending → Queued → Running
        - Preparing: 系统正在准备资源（镜像、仓库）
        - Pending: 资源就绪，等待调度决策
        - Queued: 已提交到 SLURM，等待执行
        - Running: SLURM 正在执行

        核心逻辑：
        1. 新任务以 Preparing 状态进入，启动异步资源准备
        2. 资源准备完成后变为 Pending
        3. 调度器从 Pending 任务中选择队头提交到 SLURM
        4. A 类任务可以抢占 RUNNING 的 B 类任务
        """
        with SessionLocal() as db:
            priority_map = {
                JobType.A1: 4, JobType.A2: 3,
                JobType.B1: 2, JobType.B2: 1,
            }

            # Phase 1: 启动 Preparing 任务的资源准备
            preparing_jobs = db.query(Job).filter(Job.status == JobStatus.PREPARING).all()
            for job in preparing_jobs:
                if job.id not in self.preparing_jobs:
                    task = asyncio.create_task(self._prepare_job_resources(job.id))
                    self.preparing_jobs[job.id] = task
                    logger.info(f"Job {job.id} started resource preparation")

            # Phase 2: 清理已完成的 preparing tasks
            done_jobs = [jid for jid, task in self.preparing_jobs.items() if task.done()]
            for jid in done_jobs:
                task = self.preparing_jobs.pop(jid)
                exc = task.exception()
                if exc is not None:
                    logger.error(f"Job {jid} preparation crashed: {exc}")
                    self._clean_up_working_table(jid)
                    failed_job = db.query(Job).filter(Job.id == jid).first()
                    if failed_job and failed_job.status == JobStatus.PREPARING:
                        failed_job.status = JobStatus.FAILED
                        db.commit()

            # Phase 3: 调度 Pending 任务（资源已就绪，等待提交到 SLURM）
            # 获取所有待调度任务（Pending 和 Paused）
            schedulable_jobs = db.query(Job).filter(
                Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
            ).all()

            if not schedulable_jobs:
                return

            # 按优先级排序
            schedulable_jobs.sort(
                key = lambda x: (priority_map.get(x.job_type, 0), -x.created_at.timestamp()),
                reverse = True,
            )

            head_job = schedulable_jobs[0]

            # 如果队头是 A 类任务，检查是否需要抢占 RUNNING 的 B 类任务
            if head_job.job_type in [JobType.A1, JobType.A2]:
                self._handle_preemption_for_job(db, head_job)

            # 只有当没有任务在 SLURM 队列中等待时，才提交队头
            slurm_queued_count = db.query(Job).filter(Job.status == JobStatus.QUEUED).count()

            if slurm_queued_count == 0:
                self._submit_to_slurm(db, head_job)

    async def _prepare_job_resources(self, job_id: str):
        """异步准备任务资源：镜像 + 仓库（并行）"""
        # Phase 1 — 读 job 信息 + 注册 pulling 状态（短 session）
        with SessionLocal() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job or job.status != JobStatus.PREPARING:
                return

            container_image = job.container_image
            effective_runner = job.runner or magnus_config["cluster"]["default_runner"]
            namespace = job.namespace
            repo_name = job.repo_name
            branch = job.branch
            commit_sha = job.commit_sha
            user_id = job.user_id
            job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
            repo_dir = f"{job_working_table}/repository"

            guarantee_file_exist(job_working_table, is_directory=True)

            if user_id:
                _register_image_pulling(db, container_image, user_id)

        # Phase 2 — 长 I/O（无 session）
        (image_ok, image_err), (repo_ok, repo_result, resolved_branch) = await asyncio.gather(
            resource_manager.ensure_image(container_image),
            resource_manager.ensure_repo(
                namespace = namespace,
                repo_name = repo_name,
                branch = branch,
                commit_sha = commit_sha,
                target_dir = repo_dir,
                runner = effective_runner,
                job_working_dir = job_working_table,
            ),
        )

        # Phase 3 — 回写状态（短 session）
        with SessionLocal() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job or job.status != JobStatus.PREPARING:
                self._clean_up_working_table(job_id)
                return

            _finalize_image_status(db, container_image, image_ok)

            if not image_ok:
                job.status = JobStatus.FAILED
                job.result = f"Failed to pull image: {image_err}"
                db.commit()
                logger.error(f"Job {job_id} failed: {image_err}")
                return

            if not repo_ok:
                job.status = JobStatus.FAILED
                job.result = f"Failed to clone repo: {repo_result}"
                db.commit()
                logger.error(f"Job {job_id} failed: {repo_result}")
                return

            assert repo_result is not None
            job.commit_sha = repo_result

            if resolved_branch is not None:
                job.branch = resolved_branch

            job.status = JobStatus.PENDING
            db.commit()
            logger.info(f"Job {job_id} resources ready, status -> PENDING")

    def _handle_preemption_for_job(self, db: Session, job: Job):
        """为指定的 A 类任务处理抢占逻辑"""
        free_gpus = self.slurm_manager.get_cluster_free_gpus()

        if free_gpus >= job.gpu_count:
            return  # 资源充足，无需抢占

        needed = job.gpu_count - free_gpus

        running_b_jobs = db.query(Job).filter(
            Job.status == JobStatus.RUNNING,
            Job.job_type.in_([JobType.B1, JobType.B2])
        ).all()

        if not running_b_jobs:
            return

        # 优先杀 B2，同优先级先杀晚启动的 (LIFO)
        kill_priority = {JobType.B2: 1, JobType.B1: 0}
        running_b_jobs.sort(
            key = lambda x: (
                kill_priority.get(x.job_type, 0),
                x.start_time.timestamp() if x.start_time else 0
            ),
            reverse = True,
        )

        victims, recovered = [], 0
        for b_job in running_b_jobs:
            if recovered >= needed:
                break
            victims.append(b_job)
            recovered += b_job.gpu_count

        if recovered >= needed:
            logger.info(f"Preemption: Job {job.id} ({job.job_type}) reclaiming {needed} GPUs from {len(victims)} B-class jobs")
            for v in victims:
                self._kill_and_pause(db, v)

    def _submit_to_slurm(self, db: Session, job: Job)-> bool:
        """
        提交任务到 SLURM 队列
        资源（镜像、仓库）已在 Preparing 阶段准备好
        执行流程: wrapper.py → system_entry_command → apptainer exec → epilogue
        """
        try:
            user_magnus = magnus_config["cluster"]["default_runner"]
            effective_runner = job.runner if job.runner is not None else user_magnus

            job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
            repo_dir = f"{job_working_table}/repository"

            self._init_job_working_dir(job_working_table)

            spy_gpu_interval = magnus_config["execution"]["spy_gpu_interval"]
            allow_root = magnus_config["execution"]["allow_root"]
            user_token = job.user.token or ""
            magnus_address = f"{magnus_config['server']['address']}:{magnus_config['server']['front_end_port']}"
            job_id = str(job.id)

        except Exception as error:
            logger.error(f"Job {job.id} submission error: {error}\nTraceback:\n{traceback.format_exc()}")
            job.status = JobStatus.FAILED
            db.commit()
            return False

        sif_path = resource_manager.get_sif_path(job.container_image)
        default_system_entry_command = magnus_config["cluster"]["default_system_entry_command"]
        base_system_entry_command = job.system_entry_command if job.system_entry_command else default_system_entry_command
        system_entry_command = base_system_entry_command.strip()

        default_ephemeral_storage = magnus_config["cluster"]["default_ephemeral_storage"]
        ephemeral_storage = job.ephemeral_storage if job.ephemeral_storage else default_ephemeral_storage

        wrapper_content = self._build_wrapper_content(
            job_working_table = job_working_table,
            repo_dir = repo_dir,
            sif_path = sif_path,
            system_entry_command = system_entry_command,
            user_token = user_token,
            magnus_address = magnus_address,
            job_id = job_id,
            ephemeral_storage = ephemeral_storage,
            spy_gpu_interval = spy_gpu_interval,
            allow_root = allow_root,
            entry_command = job.entry_command,
            effective_runner = effective_runner,
        )

        wrapper_path = f"{job_working_table}/wrapper.py"
        try:
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(wrapper_content)
        except IOError as e:
            logger.error(f"Failed to write wrapper script for Job {job.id}: {e}")
            return False

        try:
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
            db.commit()
            return False


    def _init_job_working_dir(self, job_working_table: str)-> None:
        guarantee_file_exist(f"{job_working_table}/slurm", is_directory=True)

        gpu_status_path = f"{job_working_table}/gpu_status.json"
        try:
            with open(gpu_status_path, "w", encoding="utf-8") as f:
                f.write("[]")
            os.chmod(gpu_status_path, 0o666)
        except Exception as e:
            logger.error(f"Failed to initialize gpu_status.json: {e}.\nTraceback:\n{traceback.format_exc()}")

        for marker_name in [".magnus_success", ".magnus_result", ".magnus_action"]:
            marker_path = f"{job_working_table}/{marker_name}"
            if os.path.exists(marker_path):
                try:
                    os.remove(marker_path)
                except OSError:
                    pass


    def _build_wrapper_content(
        self,
        job_working_table: str,
        repo_dir: str,
        sif_path: str,
        system_entry_command: str,
        user_token: str,
        magnus_address: str,
        job_id: str,
        ephemeral_storage: str,
        spy_gpu_interval: int,
        allow_root: bool,
        entry_command: str,
        effective_runner: str,
    )-> str:
        success_marker_path = f"{job_working_table}/.magnus_success"
        gpu_status_path = f"{job_working_table}/gpu_status.json"

        return f'''import os
import sys
import traceback
import subprocess
import threading
import time
import json

def _parse_size_to_mb(size_str):
    size_str = size_str.strip().upper()
    if size_str.endswith("G"):
        return int(float(size_str[:-1]) * 1024)
    if size_str.endswith("M"):
        return int(float(size_str[:-1]))
    return int(size_str)

def _spy_gpu_thread(status_path):
    interval = {spy_gpu_interval}
    while True:
        try:
            cmd = ["nvidia-smi", "--query-gpu=index,utilization.gpu,utilization.memory", "--format=csv,noheader,nounits"]
            output = subprocess.check_output(cmd, encoding="utf-8", timeout=2)
            gpu_list = []
            for line in output.strip().split('\\n'):
                if not line.strip(): continue
                parts = line.split(',')
                if len(parts) == 3:
                    gpu_list.append({{"index": int(parts[0].strip()), "utilization_gpu": int(parts[1].strip()), "utilization_memory": int(parts[2].strip())}})
            temp_path = status_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(gpu_list, f)
            os.replace(temp_path, status_path)
        except Exception:
            pass
        time.sleep(interval)

def main():
    work_dir = {repr(job_working_table)}
    repo_dir = {repr(repo_dir)}
    success_marker_path = {repr(success_marker_path)}
    gpu_status_path = {repr(gpu_status_path)}
    sif_path = {repr(sif_path)}
    system_entry_command = {repr(system_entry_command)}
    user_token = {repr(user_token)}
    magnus_address = {repr(magnus_address)}
    job_id = {repr(job_id)}
    ephemeral_storage = {repr(ephemeral_storage)}
    apptainer_tmp_dir = os.path.join(work_dir, ".magnus_tmp")
    apptainer_cache_dir = os.path.join(work_dir, ".magnus_cache")

    user_cmd_str = {repr(entry_command)}
    if "sudo" in user_cmd_str:
        raise RuntimeError("Error: Not privileged.")
    effective_runner = {repr(effective_runner)}
    allow_root = {allow_root}
    if effective_runner == "root" and not allow_root:
        raise RuntimeError("Error: Not privileged.")

    # Phase 1: Start GPU Spy
    try:
        spy = threading.Thread(target=_spy_gpu_thread, args=(gpu_status_path,), daemon=True)
        spy.start()
    except Exception as e:
        print(f"Magnus Warning: Failed to start GPU spy: {{e}}", file=sys.stderr)

    # Phase 2: Prepare user script
    user_script_path = os.path.join(work_dir, ".magnus_user_script.sh")
    with open(user_script_path, "w") as f:
        f.write("set -e\\n")
        f.write("export HOME=$MAGNUS_HOME\\n")
        f.write(user_cmd_str)
        f.write("\\n")
    os.chmod(user_script_path, 0o755)

    # Phase 3: Execute with container
    overlay_path = os.path.join(work_dir, "ephemeral_overlay.img")
    try:
        os.makedirs(apptainer_tmp_dir, exist_ok=True)
        os.makedirs(apptainer_cache_dir, exist_ok=True)

        shell_cmd = f"""set -e
export APPTAINERENV_MAGNUS_TOKEN={{user_token}}
export APPTAINERENV_MAGNUS_ADDRESS={{magnus_address}}
export APPTAINERENV_MAGNUS_JOB_ID={{job_id}}

{{system_entry_command}}

export MAGNUS_HOME=${{{{MAGNUS_HOME:-/magnus}}}}
export APPTAINERENV_MAGNUS_HOME=$MAGNUS_HOME
export APPTAINERENV_MAGNUS_RESULT=$MAGNUS_HOME/workspace/.magnus_result
export APPTAINERENV_MAGNUS_ACTION=$MAGNUS_HOME/workspace/.magnus_action
export APPTAINER_TMPDIR={{apptainer_tmp_dir}}
export APPTAINER_CACHEDIR={{apptainer_cache_dir}}
# 追加 workspace bind mount: host {{work_dir}} → 容器 $MAGNUS_HOME/workspace
# SDK 的 get_tmp_base() 依赖此 bind mount 判断运行环境（MAGNUS_HOME 存在 + workspace 目录存在 → 用 $MAGNUS_HOME/.tmp/ 中转文件，位于容器可写层而非 host 磁盘）
export APPTAINER_BIND="${{{{APPTAINER_BIND:+${{{{APPTAINER_BIND}}}},}}}}{{work_dir}}:$MAGNUS_HOME/workspace"

MAGNUS_HOST_GATEWAY="${{{{MAGNUS_HOST_GATEWAY:-10.0.2.2}}}}"
for _var in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy NO_PROXY no_proxy; do
    eval _val="\\\\$$_var"
    if [ -n "$_val" ]; then
        if [ "${{{{MAGNUS_NET_MODE:-host}}}}" = "bridge" ]; then
            _val=$(echo "$_val" | sed "s/127\\\\.0\\\\.0\\\\.1/$MAGNUS_HOST_GATEWAY/g; s/localhost/$MAGNUS_HOST_GATEWAY/g")
        fi
        export "APPTAINERENV_$_var=$_val"
    fi
done

# Detect setuid apptainer: check binary setuid bit (zero I/O, instant)
if [ -u "$(command -v apptainer)" ]; then
    _setuid_apptainer=1
else
    _setuid_apptainer=
fi

# setuid apptainer: overlay root-owned (unreadable) + userns blocked → degrade to --contain
if [ -z "$_setuid_apptainer" ]; then
    APPTAINER_CONTAIN="${{{{MAGNUS_CONTAIN_LEVEL:-containall}}}}"
else
    APPTAINER_CONTAIN="${{{{MAGNUS_CONTAIN_LEVEL:-contain}}}}"
fi
# MAGNUS_CONTAIN_LEVEL=none → disable containment entirely (bare apptainer, like pre-overlay era)
[ "$APPTAINER_CONTAIN" = "none" ] && APPTAINER_CONTAIN=""

if [ -n "$APPTAINER_CONTAIN" ]; then
    APPTAINER_FLAGS="--nv --$APPTAINER_CONTAIN --no-mount tmp"
    if [ "${{{{MAGNUS_NO_OVERLAY:-0}}}}" != "1" ] && [ -z "$_setuid_apptainer" ]; then
        if ! apptainer overlay create --sparse --size {{_parse_size_to_mb(ephemeral_storage)}} {{overlay_path}} 2>/dev/null; then
            echo "[Magnus] WARNING: --sparse not supported (apptainer < 1.3?), falling back to dense overlay" >&2
            apptainer overlay create --size {{_parse_size_to_mb(ephemeral_storage)}} {{overlay_path}}
        fi
        APPTAINER_FLAGS="$APPTAINER_FLAGS --overlay {{overlay_path}}"
    else
        APPTAINER_FLAGS="$APPTAINER_FLAGS --writable-tmpfs"
        echo "[Magnus] WARNING: overlay skipped (${{{{_setuid_apptainer:+setuid apptainer}}}}${{{{MAGNUS_NO_OVERLAY:+MAGNUS_NO_OVERLAY=1}}}}), ephemeral_storage={{ephemeral_storage}} not enforced, using writable-tmpfs (RAM)" >&2
    fi
else
    APPTAINER_FLAGS="--nv"
    echo "[Magnus] WARNING: containment disabled (MAGNUS_CONTAIN_LEVEL=none), host filesystem visible, no write isolation" >&2
fi

# --containall / --contain isolates HOME, so --env HOME=... works cleanly.
# Without containment, Apptainer forbids overriding HOME via --env, so skip it.
if [ -n "$APPTAINER_CONTAIN" ]; then
    APPTAINER_FLAGS="$APPTAINER_FLAGS --env HOME=$MAGNUS_HOME"
fi

if [ "${{{{MAGNUS_FAKEROOT:-0}}}}" = "1" ]; then
    APPTAINER_FLAGS="$APPTAINER_FLAGS --fakeroot"
fi

APPTAINER_CMD="apptainer exec $APPTAINER_FLAGS --pwd $MAGNUS_HOME/workspace/repository {{sif_path}} bash $MAGNUS_HOME/workspace/.magnus_user_script.sh"

if [ "${{{{MAGNUS_NET_MODE:-host}}}}" = "bridge" ]; then
    ROOTLESSKIT_FLAGS="--net=slirp4netns --port-driver=builtin --publish $MAGNUS_PORT_MAP"
    if [ "${{{{MAGNUS_HOST_LOOPBACK:-0}}}}" != "1" ]; then
        ROOTLESSKIT_FLAGS="$ROOTLESSKIT_FLAGS --disable-host-loopback"
    fi
    rootlesskit $ROOTLESSKIT_FLAGS $APPTAINER_CMD
else
    $APPTAINER_CMD
fi
"""
        ret_code = subprocess.call(shell_cmd, shell=True, executable="/bin/bash")

        # Phase 4: Epilogue - only write success marker if user command succeeded
        if ret_code == 0:
            with open(success_marker_path, "w") as f:
                f.write("success")
        sys.exit(ret_code)

    except Exception as error:
        print(f"Magnus Execution Error: {{error}}\\nTraceback: \\n{{traceback.format_exc()}}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Clean up overlay
        try:
            if os.path.exists(overlay_path):
                os.remove(overlay_path)
        except Exception:
            pass

if __name__ == "__main__":
    main()
'''


    def _kill_and_pause(self, db: Session, job: Job):
        """Kill SLURM job and mark as PAUSED for preemption"""
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

    def terminate_job(self, db: Session, job: Job)-> None:
        """API endpoint for user-initiated job termination"""
        if not self.enabled:
            logger.warning("Scheduler disabled, skipping termination logic.")
            return

        # 取消 Preparing 状态的异步任务
        if job.id in self.preparing_jobs:
            self.preparing_jobs[job.id].cancel()
            del self.preparing_jobs[job.id]

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

    def _clean_up_working_table(self, job_id: str)-> None:
        job_working_table = f"{magnus_workspace_path}/jobs/{job_id}"
        try:
            delete_file(os.path.join(job_working_table, "repository"))
            delete_file(os.path.join(job_working_table, "wrapper.py"))
            delete_file(os.path.join(job_working_table, ".magnus_success"))
            delete_file(os.path.join(job_working_table, ".magnus_user_script.sh"))
            delete_file(os.path.join(job_working_table, "ephemeral_overlay.img"))
            delete_file(os.path.join(job_working_table, ".magnus_tmp"))
            delete_file(os.path.join(job_working_table, ".magnus_cache"))
        except Exception as error:
            logger.warning(f"Clean up working table of job {job_id} failed:\n{error}\nTraceback:\n{traceback.format_exc()}")


scheduler = MagnusScheduler()
