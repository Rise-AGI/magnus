# back_end/server/_blueprint_manager/_types.py
"""Blueprint 用到的类型 + 异常 + 类型工具。

- FileSecret:           magnus-secret:<token> 文件凭证类型，blueprint 参数标注用
- _BlueprintCapture:    submit_job 劫持用内部异常
- _is_*/_unwrap_*:      类型检查/拆包工具
- _type_display_name:   错误消息里展示参数类型
"""
from typing import Any, Annotated, Dict, Literal, Union, get_args, get_origin

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema


class _BlueprintCapture(Exception):
    """submit_job 劫持用内部异常，捕获用户传入的 payload"""
    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload


class FileSecret(str):
    """
    文件传输凭证类型。

    用于蓝图参数，表示该参数需要一个文件/文件夹。
    值必须以 "magnus-secret:" 开头，后跟 download token。

    示例：magnus-secret:7919-calm-boat-fire

    SDK 端支持语法糖：直接传文件路径，SDK 会自动上传并转换为 secret 格式。
    """

    MAGIC_PREFIX = "magnus-secret:"

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
        )

    @classmethod
    def _validate(cls, v: str) -> "FileSecret":
        if not v.startswith(cls.MAGIC_PREFIX):
            raise ValueError(f"FileSecret must start with '{cls.MAGIC_PREFIX}'")
        return cls(v)

    @property
    def token(self) -> str:
        return self[len(self.MAGIC_PREFIX):]


def _is_optional_type(tp) -> bool:
    """检查类型是否是 Optional[X]，即 Union[X, None]"""
    if get_origin(tp) is Union:
        args = get_args(tp)
        return type(None) in args and len(args) == 2
    return False


def _unwrap_optional(tp):
    """从 Optional[X] 中提取 X"""
    if _is_optional_type(tp):
        args = get_args(tp)
        for arg in args:
            if arg is not type(None):
                return arg
    return tp


def _is_list_type(tp) -> bool:
    """检查类型是否是 List[X]"""
    return get_origin(tp) is list


def _unwrap_list(tp):
    """从 List[X] 中提取 X"""
    if _is_list_type(tp):
        args = get_args(tp)
        return args[0] if args else Any
    return tp


def _type_display_name(tp) -> str:
    if get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    if _is_optional_type(tp):
        return f"Optional[{_type_display_name(_unwrap_optional(tp))}]"
    if _is_list_type(tp):
        return f"List[{_type_display_name(_unwrap_list(tp))}]"
    if get_origin(tp) is Literal:
        values = get_args(tp)
        return " | ".join(repr(v) for v in values)
    return getattr(tp, "__name__", str(tp))
