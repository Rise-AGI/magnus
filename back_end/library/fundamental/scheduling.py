# back_end/library/fundamental/scheduling.py
"""通用调度算法工具：多维资源向量 + EASY backfilling。

EASY backfilling 由 Lifka 等人 1995 年提出 (Lifka, "The ANL/IBM SP Scheduling
System", IPPS '95)，目前仍是 SLURM、PBS、LSF 等主流 HPC 调度器的默认
backfill 策略。核心想法：当队头任务因资源不够而等待时，仍允许后续任务旁路
启动，前提是它们不会让队头多等一秒——这一安全条件用资源向量算术即可表达，
不需要任务的 walltime 估计。
"""
from dataclasses import dataclass
from typing import Generic, List, Sequence, Tuple, TypeVar


__all__ = [
    "ResourceVector",
    "BackfillCandidate",
    "select_easy_backfill",
]


T = TypeVar("T")


@dataclass(frozen=True)
class ResourceVector:
    """非负多维资源向量，支持逐维度比较与加减。

    维度数和每个维度的含义由调用方约定（例如 ``(gpu, cpu_cores, memory_mb)``）；
    算法侧只用 ``fits_within`` / ``+`` / ``-`` 这套代数。两个 ``ResourceVector``
    参与运算时维度数必须一致，否则 ``ValueError``。
    """

    components: Tuple[int, ...]

    def __post_init__(self) -> None:
        if any(component < 0 for component in self.components):
            raise ValueError(
                f"ResourceVector components must be non-negative, got {self.components}"
            )

    def fits_within(
        self,
        capacity: "ResourceVector",
    ) -> bool:
        """``self`` 在每个维度上都 ≤ ``capacity``。"""
        self._require_same_dimension(capacity)
        return all(
            mine <= cap
            for mine, cap in zip(self.components, capacity.components)
        )

    def __add__(
        self,
        other: "ResourceVector",
    ) -> "ResourceVector":
        self._require_same_dimension(other)
        return ResourceVector(
            components = tuple(
                a + b
                for a, b in zip(self.components, other.components)
            ),
        )

    def __sub__(
        self,
        other: "ResourceVector",
    ) -> "ResourceVector":
        # 资源向量恒非负，调用方需先确认 ``other.fits_within(self)``，
        # 否则任何一维出现负数都意味着调用代码有 bug，立刻 raise 而不是
        # 静默 clamp，避免把违规继续传递下去。
        self._require_same_dimension(other)
        if not other.fits_within(self):
            raise ValueError(
                f"Cannot subtract {other} from {self}: would yield negative components"
            )
        return ResourceVector(
            components = tuple(
                a - b
                for a, b in zip(self.components, other.components)
            ),
        )

    def _require_same_dimension(
        self,
        other: "ResourceVector",
    ) -> None:
        if len(self.components) != len(other.components):
            raise ValueError(
                f"ResourceVector dimension mismatch: "
                f"{len(self.components)} vs {len(other.components)}"
            )


@dataclass(frozen=True)
class BackfillCandidate(Generic[T]):
    """优先级队列中的一项。

    ``payload`` 由调用方持有具体含义（job、task、容器规格 ...），算法只读
    ``demand`` 用于资源决策；选中后原样把 ``payload`` 还回去。
    """

    payload: T
    demand: ResourceVector


def select_easy_backfill(
    candidates: Sequence[BackfillCandidate[T]],
    cluster_total: ResourceVector,
    cluster_free: ResourceVector,
) -> List[T]:
    """挑出当下可立即启动的子集，保证不延迟队头。

    ``candidates`` 必须按优先级降序传入（队头在最前）。返回选中 payload 的
    列表，按选中顺序排列。

    选取规则分两态：

    1. 队头 ``candidates[0]`` 自身能跑（``demand ≤ cluster_free``）：进入
       严格优先级贪心。从队头依次扫，``demand ≤ remaining_free`` 就选中并
       从 remaining_free 扣除其需求；遇到第一个装不下的就停，**不**跳过它
       去捞后面优先级更低的——否则破坏 priority 语义。

    2. 队头自身跑不动（在等资源）：进入 EASY backfill。队头**不**进入选中
       集（它继续在外面等下一轮），从 ``candidates[1:]`` 中挑同时满足
       两条件的：

       - ``candidate.demand ≤ remaining_free``：候选自己当下能跑；
       - ``candidate.demand + head.demand ≤ cluster_total``：EASY 安全条件。

    EASY 安全条件背后的代数（Lifka 1995）：队头等的资源迟早从 in-use
    任务释放回来。设释放量为 ``released``，释放后空闲为
    ``F' = cluster_free + released``。若候选先占去 ``c.demand``，那
    ``F' = cluster_free + released − c.demand``；队头能跑要求
    ``head.demand ≤ F'``，整理得
    ``head.demand + c.demand ≤ cluster_free + released``。又有
    ``cluster_free + released ≤ cluster_total``（已分配的部分总有，
    总不超过容量），故 ``head.demand + c.demand ≤ cluster_total`` 已是充分
    条件——无论 in-use 任务何时结束、按什么顺序结束，候选都不会让队头
    多等一秒。代数直接生效，**不需要 walltime 估计**。

    复杂度 ``O(n)``。算法不可变，输入三向量维度数一致。
    """
    if not candidates:
        return []

    head = candidates[0]
    selected: List[T] = []
    remaining_free = cluster_free

    if head.demand.fits_within(remaining_free):
        # 态 1：队头能跑，严格优先级贪心
        for candidate in candidates:
            if not candidate.demand.fits_within(remaining_free):
                break
            selected.append(candidate.payload)
            remaining_free = remaining_free - candidate.demand
        return selected

    # 态 2：队头在等，对 candidates[1:] 跑 EASY backfill
    head_demand = head.demand
    for candidate in candidates[1:]:
        if not candidate.demand.fits_within(remaining_free):
            continue
        if not (candidate.demand + head_demand).fits_within(cluster_total):
            continue
        selected.append(candidate.payload)
        remaining_free = remaining_free - candidate.demand

    return selected
