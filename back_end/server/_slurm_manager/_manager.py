# back_end/server/_slurm_manager/_manager.py
"""SlurmManager 主类。组装 _ResourceQueryMixin + _ControlMixin。"""
from ._resource_query import _ResourceQueryMixin
from ._control import _ControlMixin


class SlurmManager(_ResourceQueryMixin, _ControlMixin):

    def __init__(self) -> None:
        pass
