# back_end/server/_scheduler/_resources.py
import os
import json
import asyncio
import subprocess
from typing import TYPE_CHECKING, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pywheels.file_tools import guarantee_file_exist
from ..database import SessionLocal
from ..models import Job, JobStatus, CachedImage
from .._magnus_config import is_local_mode
from .._resource_manager import resource_manager, _image_to_sif_filename
from .._resource_manager._helpers import _run_blocking_io
from . import logger, magnus_workspace_path
from ._preparation_inputs import PreparationInputs

if TYPE_CHECKING:
    from ._typing import _SchedulerProtocol
    _ResourcesMixinBase = _SchedulerProtocol
else:
    _ResourcesMixinBase = object


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
                docker_image = resource_manager.get_docker_image(
                    image_uri,
                )
                result = subprocess.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format", "{{.Size}}",
                        docker_image,
                    ],
                    capture_output = True,
                    text = True,
                    timeout = 10,
                )
                if result.returncode == 0:
                    img.size_bytes = int(result.stdout.strip())
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        else:
            sif_path = resource_manager.get_sif_path(image_uri)
            img.filename = os.path.basename(sif_path)
            try:
                img.size_bytes = os.stat(sif_path).st_size
            except OSError:
                img.size_bytes = 0
        img.status = "cached"
    else:
        img.status = "failed"
    db.commit()


class _ResourcesMixin(_ResourcesMixinBase):
    """并行准备 Job 环境与仓库；构建可共享，资源全部就绪后才进入 Pending。"""

    async def _pull_image_shared(
        self,
        image_uri: str,
        user_id: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        """独立于 job 的镜像拉取 task，自管 DB 状态。
        仅在本次调用取得 DB 生命周期管理权时才 finalize，避免与 images API 的 _do_pull 互踩。
        """
        owns_db_lifecycle = False
        try:
            if user_id:
                def register() -> bool:
                    with SessionLocal() as db:
                        return _register_image_pulling(
                            db,
                            image_uri,
                            user_id,
                        )

                owns_db_lifecycle = await _run_blocking_io(
                    register,
                )
            try:
                image_ok, image_err = await resource_manager.ensure_image(image_uri)
            except asyncio.CancelledError:
                raise  # 服务器关停，由 recover_stuck_images 善后
            except Exception as error:
                image_ok, image_err = False, str(error)
            if owns_db_lifecycle:
                def finalize() -> None:
                    with SessionLocal() as db:
                        _finalize_image_status(
                            db,
                            image_uri,
                            image_ok,
                        )

                await _run_blocking_io(
                    finalize,
                )
            return image_ok, image_err
        finally:
            self._image_pull_tasks.pop(image_uri, None)

    async def _ensure_image_decoupled(self, image_uri: str, user_id: Optional[str]) -> Tuple[bool, Optional[str]]:
        """复用或创建共享 pull task，shield 使 job cancel 不中断拉取。"""
        task = self._image_pull_tasks.get(image_uri)
        if task is None or task.done():
            task = asyncio.create_task(self._pull_image_shared(image_uri, user_id))
            self._image_pull_tasks[image_uri] = task
        return await asyncio.shield(task)

    async def _prepare_job_resources(
        self,
        job_id: str,
    ) -> None:
        """Join preparation and its transactions before transferring ownership."""
        preparation_states = (JobStatus.PREPARING, JobStatus.BUILDING)
        job_working_table = f"{magnus_workspace_path}/jobs/{job_id}"
        repo_dir = f"{job_working_table}/repository"
        tasks: set[asyncio.Task] = set()
        owns_preparation = False
        ready = False
        interrupted = False

        def load_inputs() -> PreparationInputs | None:
            nonlocal owns_preparation
            with SessionLocal() as db:
                job = db.query(
                    Job,
                ).filter(
                    Job.id == job_id,
                ).first()
                if not job or job.status not in preparation_states:
                    return None
                inputs = PreparationInputs(
                    container_image=job.container_image,
                    dockerfile=job.dockerfile,
                    runner=job.runner or self.config["cluster"]["default_runner"],
                    namespace=job.namespace,
                    repo_name=job.repo_name,
                    branch=job.branch,
                    commit_sha=job.commit_sha,
                    user_id=job.user_id,
                )
            # Adopt before filesystem work: cancellation can discard a worker's
            # return value, but its newly created directory still needs cleanup.
            owns_preparation = True
            guarantee_file_exist(
                job_working_table,
                is_directory=True,
            )
            return inputs

        def persist(
            values: dict[str, str | JobStatus | None],
        ) -> bool:
            with SessionLocal() as db:
                changed = db.query(
                    Job,
                ).filter(
                    Job.id == job_id,
                    Job.status.in_(preparation_states),
                ).update(
                    values,
                    synchronize_session=False,
                )
                db.commit()
            return bool(
                changed,
            )

        def publish_ready() -> None:
            nonlocal ready
            # The worker adopts the committed state even if its waiter is
            # cancelled before receiving the result. Pending keeps its pins.
            ready = persist(
                {"status": JobStatus.PENDING},
            )

        async def prepare_repository(
            inputs: PreparationInputs,
        ) -> None:
            repo_ok, repo_result, resolved_branch = await resource_manager.ensure_repo(
                namespace=inputs.namespace,
                repo_name=inputs.repo_name,
                branch=inputs.branch,
                commit_sha=inputs.commit_sha,
                target_dir=repo_dir,
                runner=inputs.runner,
                job_working_dir=job_working_table,
            )
            if not repo_ok:
                raise RuntimeError(
                    f"Failed to clone repo: {repo_result}",
                )
            # Freeze HEAD as soon as Git resolves it, not after a long build.
            await _run_blocking_io(
                persist,
                {
                    "commit_sha": repo_result,
                    "branch": resolved_branch or inputs.branch,
                },
            )

        async def prepare_environment(
            inputs: PreparationInputs,
        ) -> None:
            if inputs.dockerfile is None:
                if inputs.container_image is None:
                    raise RuntimeError(
                        "Job has neither a container image nor a Dockerfile",
                    )
                resource_manager.images.pin(
                    job_id,
                    inputs.container_image,
                )
                with self._preparing_jobs_lock:
                    self._pinned_jobs.add(
                        job_id,
                    )
                image_ok, image_error = await self._ensure_image_decoupled(
                    inputs.container_image,
                    inputs.user_id,
                )
                if not image_ok:
                    raise RuntimeError(
                        f"Failed to pull image: {image_error}",
                    )
                return
            active = await _run_blocking_io(
                persist,
                {"status": JobStatus.BUILDING},
            )
            if not active:
                raise asyncio.CancelledError()
            result = await resource_manager.builds.ensure(
                job_id=job_id,
                dockerfile=inputs.dockerfile,
                user_id=inputs.user_id,
            )
            with self._preparing_jobs_lock:
                self._pinned_jobs.add(
                    job_id,
                )
            active = await _run_blocking_io(
                persist,
                {
                    "build_result": json.dumps(
                        result,
                    ),
                    "status": JobStatus.PREPARING,
                },
            )
            if not active:
                raise asyncio.CancelledError()

        try:
            inputs = await _run_blocking_io(
                load_inputs,
            )
            if inputs is None:
                return
            tasks = {
                asyncio.create_task(
                    prepare_repository(
                        inputs,
                    ),
                ),
                asyncio.create_task(
                    prepare_environment(
                        inputs,
                    ),
                ),
            }
            pending = tasks.copy()
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    task.result()
            await _run_blocking_io(
                publish_ready,
            )
            if ready:
                logger.info(
                    f"Job {job_id} resources ready, status -> PENDING",
                )
        except asyncio.CancelledError:
            interrupted = True
            raise
        except Exception as error:
            await _run_blocking_io(
                persist,
                {
                    "status": JobStatus.FAILED,
                    "result": str(
                        error,
                    ),
                },
            )
            logger.error(
                f"Job {job_id} preparation failed: {error}",
            )
        finally:
            if not ready:
                for task in tasks:
                    task.cancel()
                drain = asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )
                while not drain.done():
                    try:
                        await asyncio.shield(
                            drain,
                        )
                    except asyncio.CancelledError:
                        continue
                if owns_preparation:
                    resource_manager.builds.release(
                        job_id,
                    )
                    resource_manager.images.release(
                        job_id,
                    )
                    with self._preparing_jobs_lock:
                        self._pinned_jobs.discard(
                            job_id,
                        )
            if not ready or interrupted:
                # Cancellation during the Pending commit may be user termination
                # or shutdown. The cleanup guard rechecks durable state, keeping
                # Pending for restart while finishing an already Terminated job.
                await _run_blocking_io(
                    self._finish_unsubmitted_job,
                    job_id,
                )
