# back_end/server/models/_job.py
import enum
from datetime import datetime, timezone
from sqlalchemy import DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from ._helpers import generate_hex_id


class JobType(str, enum.Enum):
    A1 = "A1"  # 高优稳定
    A2 = "A2"  # 次优稳定
    B1 = "B1"  # 高优可抢
    B2 = "B2"  # 次优可抢
    EXTERNAL = "N/A"  # 外部任务


class JobStatus(str, enum.Enum):
    """Job 生命周期状态。与 ``Job.slurm_job_id`` 字段联合表达 magnus 与外部
    资源（SLURM step / Docker container）的关联状态。

    `slurm_job_id` 非 NULL ⇔ 外部资源仍占用中（详见 `_job.py` 字段注释）。
    `PAUSED` / `TERMINATED` 在不同的 `slurm_job_id` 取值下表达 4 个子态：

      status     | slurm_job_id | 含义
      -----------|--------------|---------------------------------------------
      TERMINATED | NULL         | 用户 cancel + 资源已释放（终态）
      TERMINATED | NOT NULL     | 用户 cancel + SLURM CG 收尾中（inflight）
      PAUSED     | NULL         | 被抢占 + 资源已释放（等待 Phase 2.5 resubmit）
      PAUSED     | NOT NULL     | 被抢占 + SLURM CG 收尾中（inflight）

    新加查询 / UI 渲染时务必区分子态：cluster endpoint 反查 magnus_job_map 会
    命中 inflight 子态的 job；前端要据此把"释放中"的视觉/操作语义跟"已终态"
    分开（如 disable 重复终止按钮）。其它状态下 slurm_job_id invariant 由
    _submit_to_slurm / _finalize_completed_job / _sync_reality_slurm 维护。
    """

    PENDING = "Pending"
    PREPARING = "Preparing"
    QUEUED = "Queued"
    RUNNING = "Running"
    PAUSED  = "Paused"
    SUCCESS = "Success"
    FAILED  = "Failed"
    TERMINATED = "Terminated"


class Job(Base):
    __tablename__ = "jobs"
    # jobs 是全站增长最快、被最多热点路径读取的表：/api/jobs 列表分页、cluster 视图、
    # 调度心跳循环都反复查询它，而终态行会无限累积。给这些读路径的过滤 / 排序列建索引，
    # 避免在整张表上做全表扫描 + 临时排序：
    #   created_at / (user_id, created_at) —— 列表按创建时间倒序分页（可选按 owner 过滤）
    #   status / slurm_job_id              —— 调度循环与 cluster 视图按活跃态 / SLURM id 反查
    #                                         （活跃态在海量终态行里稀疏，索引直接命中而非扫全表）
    # 单列索引就近声明在字段上（index=True）；跨列的复合索引在此声明。
    __table_args__ = (
        Index("ix_jobs_user_id_created_at", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_hex_id)
    task_name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    user: Mapped["User"] = relationship(back_populates="jobs")
    namespace: Mapped[str] = mapped_column(String)
    repo_name: Mapped[str] = mapped_column(String)
    branch: Mapped[str | None] = mapped_column(String, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_command: Mapped[str] = mapped_column(Text)
    container_image: Mapped[str] = mapped_column(String)
    system_entry_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    gpu_count: Mapped[int] = mapped_column(Integer)
    gpu_type: Mapped[str] = mapped_column(String)
    cpu_count: Mapped[int | None] = mapped_column(Integer, default=None)
    memory_demand: Mapped[str | None] = mapped_column(String, default=None)
    # 期望最大墙钟（分钟）。None = 不下发 --time，由站点 SLURM 分区默认墙钟决定（保持现状）。
    # 设了它，短任务声明短墙钟即可被共享集群的 backfill 优先插队（否则按分区默认墙钟算、
    # 几乎无法 backfill）。Docker/local 模式由调度器按此超时 kill（与 SLURM --time 对偶）。
    time_limit: Mapped[int | None] = mapped_column(Integer, default=None)
    ephemeral_storage: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[JobStatus] = mapped_column(SQLEnum(JobStatus), default=JobStatus.PREPARING, index=True)
    job_type: Mapped[JobType] = mapped_column(SQLEnum(JobType), default=JobType.A2)
    # SLURM 模式存 sbatch 返回的 SLURM job id；Docker (local) 模式复用此列存
    # container_name。语义为 "magnus 与外部资源的当前/last-known 关联"：在
    # TERMINATED / PAUSED 状态下也可能非 NULL —— SLURM 模式 scancel 后 job 进
    # 入 CG (COMPLETING) 阶段还在持有 GPU，由 _sync_reality_slurm 在 SLURM 报
    # 终态后才清空。新加 query 引用此列时（如 cluster endpoint 用 slurm_id 反
    # 查 magnus_job_map）必须考虑该字段在非 RUNNING 状态下也可能命中，否则
    # 会把仍在 SLURM CG 的 magnus job 误判为 external SLURM job。
    slurm_job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    runner: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClusterSnapshot(Base):
    __tablename__ = "cluster_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    total_gpus: Mapped[int] = mapped_column(Integer)
    slurm_used_gpus: Mapped[int] = mapped_column(Integer)
    magnus_used_gpus: Mapped[int] = mapped_column(Integer)
