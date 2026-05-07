# back_end/server/_scheduler/_resources.py
import os
import re
import asyncio
import subprocess
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pywheels.file_tools import guarantee_file_exist
from ..database import SessionLocal
from ..models import Job, JobStatus, CachedImage
from .._magnus_config import magnus_config, is_local_mode
from .._resource_manager import resource_manager, _image_to_sif_filename
from . import logger, magnus_workspace_path, magnus_container_cache_path


def _register_image_pulling(db: Session, image_uri: str, user_id: str) -> bool:
    """Pull 开始前，标记镜像为 pulling（首次见到的 URI 才插入）。
    返回 True 表示取得了 DB 生命周期管理权（应由调用方负责 finalize）；
    返回 False 表示记录已由其他路径（如 images API）管理，调用方不应触碰 DB 状态。
    """
    existing = db.query(CachedImage).filter(CachedImage.uri == image_uri).first()
    if existing:
        if existing.status == "failed":
            existing.status = "pulling"
            db.commit()
            return True
        return False
    db.add(CachedImage(
        uri=image_uri,
        filename=_image_to_sif_filename(image_uri),
        user_id=user_id,
        status="pulling",
        size_bytes=0,
    ))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def _finalize_image_status(db: Session, image_uri: str, success: bool) -> None:
    """Pull 结束后，更新镜像状态为 cached 或 failed。"""
    img = db.query(CachedImage).filter(CachedImage.uri == image_uri).first()
    if not img:
        return
    if success:
        if is_local_mode:
            try:
                docker_image = re.sub(r'^[a-z]+://', '', image_uri)
                result = subprocess.run(
                    ["docker", "image", "inspect", "--format", "{{.Size}}", docker_image],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    img.size_bytes = int(result.stdout.strip())
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        else:
            sif_path = os.path.join(magnus_container_cache_path, _image_to_sif_filename(image_uri))
            try:
                img.size_bytes = os.stat(sif_path).st_size
            except OSError:
                img.size_bytes = 0
        img.status = "cached"
    else:
        img.status = "failed"
    db.commit()


class _ResourcesMixin:
    """Job 资源准备：镜像拉取（共享 task）、仓库 clone、Preparing → Pending 状态推进。"""

    async def _pull_image_shared(self, image_uri: str, user_id: Optional[str]) -> Tuple[bool, Optional[str]]:
        """独立于 job 的镜像拉取 task，自管 DB 状态。
        仅在本次调用取得 DB 生命周期管理权时才 finalize，避免与 images API 的 _do_pull 互踩。
        """
        owns_db_lifecycle = False
        if user_id:
            with SessionLocal() as db:
                owns_db_lifecycle = _register_image_pulling(db, image_uri, user_id)
        try:
            image_ok, image_err = await resource_manager.ensure_image(image_uri)
        except asyncio.CancelledError:
            raise  # 服务器关停，由 recover_stuck_images 善后
        except Exception as e:
            image_ok, image_err = False, str(e)
        finally:
            self._image_pull_tasks.pop(image_uri, None)
        if owns_db_lifecycle:
            with SessionLocal() as db:
                _finalize_image_status(db, image_uri, image_ok)
        return image_ok, image_err

    async def _ensure_image_decoupled(self, image_uri: str, user_id: Optional[str]) -> Tuple[bool, Optional[str]]:
        """复用或创建共享 pull task，shield 使 job cancel 不中断拉取。"""
        task = self._image_pull_tasks.get(image_uri)
        if task is None or task.done():
            task = asyncio.create_task(self._pull_image_shared(image_uri, user_id))
            self._image_pull_tasks[image_uri] = task
        return await asyncio.shield(task)

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

        # Phase 2 — 长 I/O（无 session）
        # 镜像拉取解耦：shield 保护，job cancel 不中断拉取
        (image_ok, image_err), (repo_ok, repo_result, resolved_branch) = await asyncio.gather(
            self._ensure_image_decoupled(container_image, user_id),
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
