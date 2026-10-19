# back_end/library/fundamental/scheduling.py
"""Resource vectors and conservative backfill without runtime estimates.

True time-based backfill reserves when and where a waiting job can start:
https://slurm.schedmd.com/sched_config.html. Here we only spend resources that
are already free in excess of the head's demand. Callers must separately
respect placement constraints; aggregate vectors cannot prove node placement.
"""
from dataclasses import dataclass
from typing import Generic, List, Sequence, Tuple, TypeVar


__all__ = [
    "ResourceVector",
    "BackfillCandidate",
    "select_conservative_backfill",
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


def select_conservative_backfill(
    candidates: Sequence[BackfillCandidate[T]],
    cluster_total: ResourceVector,
    cluster_free: ResourceVector,
) -> List[T]:
    """Select in priority order, reserving the waiting head's current resources.

    When the head fits, admit a strict priority prefix. Otherwise a later job
    may consume only the per-dimension surplus ``max(free - head, 0)``. Spend
    that surplus cumulatively across selected candidates. Future releases can
    then satisfy the head at the same point as without these admissions.

    ``head + candidate <= total`` is only necessary, not sufficient: with
    total=8, free=2, head=4, admitting a two-unit candidate delays the head when
    an existing job releases two units. No completion-time inference is made.
    A head exceeding configured capacity blocks rather than being bypassed.
    """
    cluster_free._require_same_dimension(cluster_total)
    if not cluster_free.fits_within(cluster_total):
        raise ValueError("Free resources cannot exceed total resources")
    for candidate in candidates:
        candidate.demand._require_same_dimension(cluster_total)
    if not candidates:
        return []

    head = candidates[0]
    if not head.demand.fits_within(cluster_total):
        return []
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

    # Preserve the head's current reservation in every dimension, including
    # dimensions it already has enough of. Never spend that reservation twice.
    remaining_surplus = ResourceVector(
        components = tuple(
            max(0, free - needed)
            for free, needed in zip(cluster_free.components, head.demand.components)
        ),
    )
    for candidate in candidates[1:]:
        if not candidate.demand.fits_within(remaining_surplus):
            continue
        selected.append(candidate.payload)
        remaining_surplus = remaining_surplus - candidate.demand

    return selected


# Compatibility for existing callers; this is not a time-based EASY scheduler.
select_easy_backfill = select_conservative_backfill
