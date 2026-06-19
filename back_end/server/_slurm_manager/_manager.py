# back_end/server/_slurm_manager/_manager.py
"""SlurmManager 主类。组装 _ResourceQueryMixin + _ControlMixin。"""
from typing import Optional

from .._magnus_config import magnus_config
from ._resource_query import _ResourceQueryMixin
from ._control import _ControlMixin
from ._transport import _Transport, build_transport


class SlurmManager(_ResourceQueryMixin, _ControlMixin):

    def __init__(
        self,
        transport: Optional[_Transport] = None,
    ) -> None:
        # 显式传入的 transport 优先（测试 / 特殊调用）；否则按站点 transport 配置
        # 构造 —— local 站点回落本机 subprocess，wm2 这类租户站点骑 SSH socket。
        self._transport = (
            transport
            if transport is not None
            else build_transport(magnus_config["transport"])
        )
