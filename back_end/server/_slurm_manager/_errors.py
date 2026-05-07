# back_end/server/_slurm_manager/_errors.py
"""SLURM 操作相关异常。"""


class SlurmError(Exception):
    pass


class SlurmResourceError(SlurmError):
    pass
