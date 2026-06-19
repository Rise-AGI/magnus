# back_end/server/_slurm_manager/_manager.py
"""SlurmManager 主类。组装 _ResourceQueryMixin + _ControlMixin。"""
from typing import Optional

from ._resource_query import _ResourceQueryMixin
from ._control import _ControlMixin
from ._transport import _Transport, _LocalTransport


class SlurmManager(_ResourceQueryMixin, _ControlMixin):

    def __init__(
        self,
        transport: Optional[_Transport] = None,
    ) -> None:
        self._transport = transport if transport is not None else _LocalTransport()
