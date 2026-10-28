# back_end/server/_scheduler/_typing.py
"""Type-only base shared by all scheduler mixins.

`MagnusScheduler` 由若干 mixin 组装（见 _core.py）。每个 mixin 都会通过 `self.X`
访问"其他 mixin 提供的方法"或"主类 __init__ 注入的属性"，但 mixin 自身的类不
声明这些 cross-class 引用，pyright strict 会报 attribute-unknown / Optional-call
之类的 false positive。

这里集中声明所有 cross-mixin 引用的签名，让每个 mixin 在 TYPE_CHECKING 时把
`_SchedulerProtocol` 当父类，pyright 静态视角下能看到所有 attribute / method；
运行时此模块零参与，由 `MagnusScheduler` 通过实际 mixin 组合提供真实实现。
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ._custody_relay import CustodyRelay
    from sqlalchemy.orm import Session
    from .._docker_manager import DockerManager
    from .._slurm_manager import SlurmManager
    from ..models import Job
    from library.fundamental.scheduling import ResourceVector


class _SchedulerProtocol:
    """Cross-mixin attribute / method declarations for static analysis.

    Real values are set by `MagnusScheduler.__init__` and by sibling mixin
    method bodies; this class is never instantiated.
    """

    # === MagnusScheduler.__init__ 注入的属性 ===
    slurm_manager: Optional["SlurmManager"]
    docker_manager: Optional["DockerManager"]
    target: Any
    config: dict
    remote_workspace_path: str
    remote_ephemeral_workspace_path: str
    remote_container_cache_path: str
    enabled: bool
    last_snapshot_time: datetime
    last_workspace_gc_time: datetime
    preparing_jobs: Dict[str, asyncio.Task[Any]]
    _preparing_jobs_lock: "threading.Lock"
    _loop: Optional[asyncio.AbstractEventLoop]
    _image_pull_tasks: Dict[str, asyncio.Task[Any]]
    _build_pinned_jobs: set[str]
    _docker_log_cursors: Dict[str, Optional[str]]
    _finalization_retry_after: Dict[str, float]
    _artifact_recovery_tasks: Dict[str, asyncio.Task[int]]
    _artifact_recovery_slots: asyncio.Semaphore
    _custody_relay: CustodyRelay
    _custody_bulk_slots: asyncio.Semaphore
    _custody_bulk_tasks: Dict[str, asyncio.Task]
    _artifact_retry_after: Dict[str, float]
    _submission_task: Optional[asyncio.Task]
    _submitting_jobs: set[str]
    _submission_cancel_events: dict[str, threading.Event]
    _submission_stopping: bool
    _staged_job_ids: set[str]
    _submission_reconcile_after: float
    _submission_reconcile_cursor: int

    def _submission_deferred(self) -> bool: ...
    def _defer_submission(self) -> None: ...
    def _reconcile_submissions(self) -> None: ...
    def _begin_submission(
        self,
        db: "Session",
        job: "Job",
        attempt: str,
    ) -> None: ...
    def _clear_rejected_submission(
        self,
        db: "Session",
        job_id: str,
        attempt: str,
    ) -> None: ...

    def _submission_cancel_event(
        self,
        job_id: str,
    ) -> threading.Event | None: ...

    def _job_query(self, db: "Session", *entities) -> Any: ...
    def inflight_job_ids(self) -> set[str]: ...
    def terminate_job(
        self,
        db: "Session",
        job: "Job",
    ) -> None: ...
    def _record_submission(
        self,
        db: "Session",
        job: "Job",
        external_id: str,
        *,
        attempt: str | None = None,
    ) -> bool: ...

    def _queue_pending_submission(self) -> None: ...
    async def shutdown_submission(self) -> None: ...

    def _schedule_artifact_recovery(
        self,
        candidates: dict[str, str | None],
    ) -> None: ...
    async def _recover_artifacts_in_slot(
        self,
        job_id: str,
        slurm_job_id: str | None,
    ) -> int: ...
    async def _transfer_custody_bulk(
        self,
        job_id: str,
    ) -> bool: ...
    def _recover_job_artifacts(
        self,
        job_id: str,
        slurm_job_id: str | None,
    ) -> int: ...

    # === _job_lifecycle.py ===
    def _write_success_marker(self, job_id: str) -> None: ...
    def _resume_paused_job(
        self,
        job_id: str,
    ) -> bool: ...
    def _finalize_completed_job(
        self,
        job: "Job",
        *,
        cleanup: bool = True,
    ) -> None: ...
    @staticmethod
    def _format_oom_message(memory_demand: Optional[str]) -> str: ...
    @staticmethod
    def _has_oom_marker(job_id: str) -> bool: ...
    def _clean_up_working_table(self, job_id: str) -> bool: ...

    # === _decisions.py ===
    def _schedule_pending_jobs(self) -> None: ...
    def _select_authoritative_jobs(
        self,
        db: "Session",
        jobs: List["Job"],
    ) -> List[str]: ...
    def _compute_cluster_resources(
        self,
        snapshot: Any,
        tasks: List[Dict[str, Any]],
        gpu_types: Tuple[str, ...],
    ) -> Tuple["ResourceVector", "ResourceVector | None"]: ...
    def _handle_preemption_for_job(
        self,
        db: "Session",
        job: "Job",
        demand: "ResourceVector",
        total: "ResourceVector",
        free: "ResourceVector",
        tasks: List[Dict[str, Any]],
        gpu_types: Tuple[str, ...],
    ) -> bool: ...
    def _kill_and_pause(self, db: "Session", job: "Job") -> None: ...

    # === _resources.py ===
    async def _pull_image_shared(
        self,
        image_uri: str,
        user_id: Optional[str],
    ) -> Tuple[bool, Optional[str]]: ...
    async def _ensure_image_decoupled(
        self,
        image_uri: str,
        user_id: Optional[str],
    ) -> Tuple[bool, Optional[str]]: ...
    async def _prepare_job_resources(self, job_id: str) -> None: ...

    # === _submit.py ===
    def _fail_job_before_submission(
        self,
        db: "Session",
        job_id: str,
        message: str,
    ) -> None: ...
    def _built_environment_ready(self, db: "Session", job: "Job") -> bool: ...
    def _submit_to_slurm(self, db: "Session", job: "Job") -> bool: ...
    def _submit_to_docker(self, db: "Session", job: "Job") -> bool: ...
    def _extract_bind_mounts_from_system_entry_command(
        self,
        system_entry_command: str,
    ) -> List[str]: ...
    def _init_job_working_dir(self, job_working_table: str) -> None: ...

    # === _staging.py ===
    def _is_remote_execution(self) -> bool: ...
    def _remote_job_working_table(self, job_id: str) -> str: ...
    def _local_job_working_table(self, job_id: str) -> str: ...
    def _provision_platform_sdk(self, local_job_working_table: str) -> None: ...
    def _submit_jobs(self, job_ids: List[str]) -> None: ...
    def _stage_in_job(self, job_id: str, wrapper_local_path: str) -> None: ...
    def _stage_in_resources(
        self,
        job_id: str,
        local_sif_path: str,
        local_repo_dir: str,
        *,
        build_digest: str | None = None,
    ) -> None: ...
    def _stage_in_custody(self, job_id: str, *command_sources: str) -> None: ...
    def _stage_out_logs(
        self,
        job_id: str,
        *,
        timeout: float = 30,
        cancel_event: threading.Event | None = None,
    ) -> None: ...
    def _stage_out_markers(self, job_id: str) -> None: ...
    def _stage_out_final(self, job_id: str) -> bool: ...
    def _stage_out_custody(
        self,
        job_id: str,
        *,
        deadline: float | None = None,
    ) -> bool: ...
    def _cleanup_remote_job(self, job_id: str) -> None: ...

    # === _workspace_gc.py ===
    def _reclaim_workspace_over_cap(self) -> None: ...

    # === _sync.py ===
    def _record_snapshot(self) -> None: ...
    def _dump_docker_logs(
        self,
        job_id: str,
        container_name: str,
        since: Optional[str] = None,
    ) -> Optional[str]: ...
    def _sync_reality(self) -> dict[str, str | None]: ...
    def _sync_reality_docker(self) -> None: ...
    def _sync_reality_slurm(self) -> dict[str, str | None]: ...
    def _classify_slurm_job(
        self,
        job_id: str,
        slurm_job_id: str,
        real_status: str,
    ) -> bool: ...
    def _finalize_slurm_job(
        self,
        job_id: str,
        slurm_job_id: str,
        real_status: str,
    ) -> None: ...
    def _finish_job_cleanup(
        self,
        job_id: str,
        slurm_job_id: Optional[str],
    ) -> None: ...
    def _docker_job_timed_out(self, job: "Job") -> bool: ...
