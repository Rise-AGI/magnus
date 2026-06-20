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
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
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
    enabled: bool
    last_snapshot_time: datetime
    preparing_jobs: Dict[str, asyncio.Task[Any]]
    _image_pull_tasks: Dict[str, asyncio.Task[Any]]
    _docker_log_cursors: Dict[str, Optional[str]]

    # === _job_lifecycle.py ===
    def _write_success_marker(self, job_id: str) -> None: ...
    def _finalize_completed_job(self, job: "Job") -> None: ...
    @staticmethod
    def _format_oom_message(memory_demand: Optional[str]) -> str: ...
    @staticmethod
    def _has_oom_marker(job_id: str) -> bool: ...
    def _clean_up_working_table(self, job_id: str) -> None: ...

    # === _decisions.py ===
    def _compute_cluster_resources(self) -> Tuple["ResourceVector", "ResourceVector"]: ...
    def _handle_preemption_for_job(self, db: "Session", job: "Job") -> None: ...
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
    def _stage_in_job(self, job_id: str, wrapper_local_path: str) -> None: ...
    def _stage_out_logs(self, job_id: str) -> None: ...
    def _stage_out_final(self, job_id: str) -> None: ...
    def _cleanup_remote_job(self, job_id: str) -> None: ...

    # === _sync.py ===
    def _record_snapshot(self) -> None: ...
    def _dump_docker_logs(
        self,
        job_id: str,
        container_name: str,
        since: Optional[str] = None,
    ) -> Optional[str]: ...
    def _sync_reality(self) -> None: ...
    def _sync_reality_docker(self) -> None: ...
    def _sync_reality_slurm(self) -> None: ...
