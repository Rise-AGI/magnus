# back_end/server/_scheduler.py
import os
import json
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from typing import Any, List, Dict
from pywheels.file_tools import guarantee_file_exist, delete_file
from .database import SessionLocal
from .models import *
from ._slurm_manager import SlurmManager
from ._magnus_config import magnus_config
from ._resource_manager import resource_manager


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
        with SessionLocal() as db:
            try:
                self._sync_reality(db)
                await self._make_decisions(db)
                self._record_snapshot(db)
            except Exception as e:
                logger.error(f"Scheduler tick failed: {e}", exc_info=True)


    def _record_snapshot(self, db: Session):
        now = datetime.now(timezone.utc)
        if (now - self.last_snapshot_time).total_seconds() < \
            magnus_config["server"]["scheduler"]["snapshot_interval"]:
            return

        try:
            slurm_stats = self.slurm_manager.get_resource_snapshot()
            running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
            magnus_usage = sum(job.gpu_count for job in running_jobs)

            snapshot = ClusterSnapshot(
                total_gpus = slurm_stats["total_gpus"],
                slurm_used_gpus = slurm_stats["slurm_used_gpus"],
                magnus_used_gpus = magnus_usage,
                timestamp = now
            )
            db.add(snapshot)
            db.commit()
            self.last_snapshot_time = now
            logger.debug(f"Recorded Cluster Snapshot: Total={snapshot.total_gpus}, Used={snapshot.slurm_used_gpus}, Magnus={magnus_usage}")
        except Exception as e:
            logger.error(f"Failed to record cluster snapshot: {e}")


    def _sync_reality(self, db: Session):
        """同步 SLURM 真实状态到数据库"""
        # 同步 QUEUED 任务：检查是否已开始运行
        queued_jobs = db.query(Job).filter(Job.status == JobStatus.QUEUED).all()
        for job in queued_jobs:
            if not job.slurm_job_id:
                logger.warning(f"Job {job.id} is QUEUED but has no slurm_id. Marking FAILED.")
                job.status = JobStatus.FAILED
                continue

            real_status = self.slurm_manager.check_job_status(job.slurm_job_id)

            if real_status == "RUNNING":
                job.status = JobStatus.RUNNING
                job.start_time = datetime.now(timezone.utc)
                logger.info(f"Job {job.id} started running in SLURM (ID: {job.slurm_job_id})")
            elif real_status == "COMPLETED":
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
            elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                logger.warning(f"Job {job.id} failed in SLURM queue (Status: {real_status}).")
                job.status = JobStatus.FAILED
                job.slurm_job_id = None
                self._clean_up_working_table(job.id)
            # PENDING 保持 QUEUED 状态，等待 SLURM 调度

        db.commit()

        # 同步 RUNNING 任务
        running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING).all()
        for job in running_jobs:
            if not job.slurm_job_id:
                logger.warning(f"Job {job.id} is RUNNING but has no slurm_id. Marking FAILED.")
                job.status = JobStatus.FAILED
                continue

            self._harvest_job_metrics(db, job)
            real_status = self.slurm_manager.check_job_status(job.slurm_job_id)

            if real_status == "COMPLETED":
                marker_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_success"
                if os.path.exists(marker_path):
                    logger.info(f"Job {job.id} completed successfully (Marker Verified).")
                    job.status = JobStatus.SUCCESS
                    result_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_result"
                    job.result = ".magnus_result" if os.path.exists(result_path) else None
                    action_path = f"{magnus_workspace_path}/jobs/{job.id}/.magnus_action"
                    job.action = ".magnus_action" if os.path.exists(action_path) else None
                else:
                    logger.warning(f"Job {job.id} disappeared from queue but NO success marker found. Marking FAILED.")
                    job.status = JobStatus.FAILED
                job.slurm_job_id = None
                self._clean_up_working_table(job.id)

            elif real_status in ["FAILED", "CANCELLED", "TIMEOUT"]:
                logger.warning(f"Job {job.id} failed in SLURM (Status: {real_status}).")
                job.status = JobStatus.FAILED
                job.slurm_job_id = None
                self._clean_up_working_table(job.id)

            # PENDING/RUNNING 保持不变
            db.commit()


    def _harvest_job_metrics(self, db: Session, job: Job) -> None:
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
                job_id=job.id,
                timestamp=datetime.now(timezone.utc),
                status_json=json.dumps(processed_status),
            ))
        except Exception as e:
            logger.error(f"Failed to harvest metrics for job {job.id}: {e}")


    async def _make_decisions(self, db: Session):
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
            del self.preparing_jobs[jid]

        # Phase 3: 调度 Pending 任务（资源已就绪，等待提交到 SLURM）
        # 获取所有待调度任务（Pending 和 Paused）
        schedulable_jobs = db.query(Job).filter(
            Job.status.in_([JobStatus.PENDING, JobStatus.PAUSED])
        ).all()

        if not schedulable_jobs:
            return

        # 按优先级排序
        schedulable_jobs.sort(
            key=lambda x: (priority_map.get(x.job_type, 0), -x.created_at.timestamp()),
            reverse=True,
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
        with SessionLocal() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job or job.status != JobStatus.PREPARING:
                return

            effective_runner = job.runner or magnus_config["cluster"]["default_runner"]
            job_working_table = f"{magnus_workspace_path}/jobs/{job.id}"
            repo_dir = f"{job_working_table}/repository"

            # 准备工作目录
            guarantee_file_exist(job_working_table, is_directory=True)

            # 并行准备镜像和仓库
            image_task = resource_manager.ensure_image(job.container_image)
            repo_task = resource_manager.ensure_repo(
                namespace=job.namespace,
                repo_name=job.repo_name,
                branch=job.branch,
                commit_sha=job.commit_sha,
                target_dir=repo_dir,
                runner=effective_runner,
                job_working_dir=job_working_table,
            )

            (image_ok, image_err), (repo_ok, repo_result) = await asyncio.gather(image_task, repo_task)

            if not image_ok:
                job.status = JobStatus.FAILED
                job.result = f"Failed to pull image: {image_err}"
                db.commit()
                logger.error(f"Job {job.id} failed: {image_err}")
                return

            if not repo_ok:
                job.status = JobStatus.FAILED
                job.result = f"Failed to clone repo: {repo_result}"
                db.commit()
                logger.error(f"Job {job.id} failed: {repo_result}")
                return

            # 回写解析后的真实 commit SHA（将 HEAD 等符号引用固化）
            assert repo_result is not None
            job.commit_sha = repo_result

            # 资源就绪，进入待调度队列
            job.status = JobStatus.PENDING
            db.commit()
            logger.info(f"Job {job.id} resources ready, status -> PENDING")

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
            key=lambda x: (
                kill_priority.get(x.job_type, 0),
                x.start_time.timestamp() if x.start_time else 0
            ),
            reverse=True,
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

    def _submit_to_slurm(self, db: Session, job: Job) -> bool:
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
            guarantee_file_exist(f"{job_working_table}/slurm", is_directory=True)

            gpu_status_path = f"{job_working_table}/gpu_status.json"
            try:
                with open(gpu_status_path, "w", encoding="utf-8") as f:
                    f.write("[]")
                os.chmod(gpu_status_path, 0o666)
            except Exception as e:
                logger.error(f"Failed to initialize gpu_status.json: {e}.\nTraceback:\n{traceback.format_exc()}")

            success_marker_path = f"{job_working_table}/.magnus_success"
            result_marker_path = f"{job_working_table}/.magnus_result"
            action_marker_path = f"{job_working_table}/.magnus_action"
            for marker in [success_marker_path, result_marker_path, action_marker_path]:
                if os.path.exists(marker):
                    try:
                        os.remove(marker)
                    except OSError:
                        pass

            spy_gpu_interval = magnus_config["server"]["scheduler"]["spy_gpu_interval"]
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

        wrapper_content = f'''import os
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

    user_cmd_str = {repr(job.entry_command)}
    if "sudo" in user_cmd_str:
        raise RuntimeError("Error: Not privileged.")
    effective_runner = {repr(effective_runner)}
    if effective_runner == "root":
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

    # Phase 3: Create overlay + execute with --containall
    overlay_path = os.path.join(work_dir, "ephemeral_overlay.img")
    try:
        size_mb = _parse_size_to_mb(ephemeral_storage)
        subprocess.check_call(["apptainer", "overlay", "create", "--size", str(size_mb), overlay_path])
        os.makedirs(apptainer_tmp_dir, exist_ok=True)
        os.makedirs(apptainer_cache_dir, exist_ok=True)

        shell_cmd = f"""set -e
export APPTAINERENV_MAGNUS_TOKEN={{user_token}}
export APPTAINERENV_MAGNUS_ADDRESS={{magnus_address}}
export APPTAINERENV_MAGNUS_JOB_ID={{job_id}}
export APPTAINERENV_MAGNUS_HOME=/magnus
export APPTAINERENV_MAGNUS_RESULT=/magnus/workspace/.magnus_result
export APPTAINERENV_MAGNUS_ACTION=/magnus/workspace/.magnus_action

{{system_entry_command}}

export APPTAINER_TMPDIR={{apptainer_tmp_dir}}
export APPTAINER_CACHEDIR={{apptainer_cache_dir}}
export APPTAINER_BIND="${{{{APPTAINER_BIND:+${{{{APPTAINER_BIND}}}},}}}}{{work_dir}}:/magnus/workspace"

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

APPTAINER_CMD="apptainer exec --nv --containall --overlay {{overlay_path}} --pwd /magnus/workspace/repository {{sif_path}} bash /magnus/workspace/.magnus_user_script.sh"

if [ "${{{{MAGNUS_NET_MODE:-host}}}}" = "bridge" ]; then
    rootlesskit --net=slirp4netns --disable-host-loopback --port-driver=builtin --publish "$MAGNUS_PORT_MAP" $APPTAINER_CMD
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

        wrapper_path = f"{job_working_table}/wrapper.py"
        try:
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(wrapper_content)
        except IOError as e:
            logger.error(f"Failed to write wrapper script for Job {job.id}: {e}")
            return False

        try:
            slurm_id = self.slurm_manager.submit_job_simple(
                entry_command=f"python3 {wrapper_path}",
                gpus=job.gpu_count,
                job_name=job.task_name,
                gpu_type=job.gpu_type,
                output_path=f"{job_working_table}/slurm/output.txt",
                overwrite_output=False,
                runner=effective_runner,
                cpu_count=job.cpu_count,
                memory_demand=job.memory_demand,
                token=job.user.token if job.user.token is not None else "",
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


    def _kill_and_pause(self, db: Session, job: Job):
        """Kill SLURM job and mark as PAUSED for preemption"""
        if job.slurm_job_id:
            logger.info(f"Killing victim job {job.id} (SLURM: {job.slurm_job_id})")
            self.slurm_manager.kill_job(
                job.slurm_job_id,
                runner=job.runner if job.runner is not None else "magnus",
                token=job.user.token if job.user.token is not None else "",
            )

        self._clean_up_working_table(job.id)
        job.status = JobStatus.PAUSED
        job.slurm_job_id = None
        job.start_time = None
        db.commit()

    def terminate_job(self, db: Session, job: Job) -> None:
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
                runner=job.runner if job.runner is not None else "magnus",
                token=job.user.token if job.user.token is not None else "",
            )

        self._clean_up_working_table(job.id)
        job.status = JobStatus.TERMINATED
        job.slurm_job_id = None
        job.start_time = None
        db.commit()

    def _clean_up_working_table(self, job_id: str) -> None:
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
