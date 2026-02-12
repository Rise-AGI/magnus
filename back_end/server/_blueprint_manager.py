# back_end/server/_blueprint_manager.py
import inspect
import logging
from typing import Any, Dict, List, Optional, Union, Annotated, Literal, get_origin, get_args

from pydantic import ValidationError, create_model, ConfigDict, GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from .models import JobType
from .schemas import JobSubmission, BlueprintParamSchema, BlueprintParamOption

logger = logging.getLogger(__name__)


class FileSecret(str):
    """
    文件传输凭证类型。

    用于蓝图参数，表示该参数需要一个文件/文件夹。
    值必须以 "magnus-secret:" 开头，后跟 download token。

    示例：magnus-secret:7453-calm-boat-fire

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


class BlueprintManager:
    """
    负责解析用户编写的 Python Blueprint 代码。
    核心功能：
    1. analyze_signature: 静态分析代码签名，生成前端表单 Schema。
    2. execute: 动态编译并执行代码，包含运行时类型强制转换 (String -> Typed)。
    """

    def __init__(self):
        self.execution_globals = {
            "JobSubmission": JobSubmission,
            "JobType": JobType,
            "FileSecret": FileSecret,
            "Annotated": Annotated,
            "Literal": Literal,
            "Optional": Optional,
            "List": List,
            "Dict": Dict,
            "Any": Any,
        }

    def _compile_code(
        self,
        code: str,
        extra_globals: Dict[str, Any],
    ) -> Dict[str, Any]:
        # 允许导入的模块白名单
        allowed_modules = {
            "typing": __import__("typing"),
        }

        def restricted_import(
            name: str,
            _globals: Optional[Dict[str, Any]] = None,
            _locals: Optional[Dict[str, Any]] = None,
            _fromlist: tuple = (),
            _level: int = 0,
        ) -> Any:
            if name in allowed_modules:
                return allowed_modules[name]
            raise ImportError(f"Import of '{name}' is not allowed in Blueprint")

        # 受限 builtins：允许安全操作，阻止危险操作
        safe_builtins: Dict[str, Any] = {
            # 常量
            "True": True,
            "False": False,
            "None": None,
            # 类型
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "frozenset": frozenset,
            "type": type,
            "object": object,
            # 函数
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sorted": sorted,
            "reversed": reversed,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "pow": pow,
            "divmod": divmod,
            "any": any,
            "all": all,
            "isinstance": isinstance,
            "issubclass": issubclass,
            "hasattr": hasattr,
            "getattr": getattr,
            "setattr": setattr,
            "callable": callable,
            "repr": repr,
            "hash": hash,
            "id": id,
            "print": print,
            # 受限 import
            "__import__": restricted_import,
            # 异常
            "Exception": Exception,
            "ValueError": ValueError,
            "TypeError": TypeError,
            "KeyError": KeyError,
            "IndexError": IndexError,
            "AttributeError": AttributeError,
            "RuntimeError": RuntimeError,
        }

        scope: Dict[str, Any] = {"__builtins__": safe_builtins}
        scope.update(self.execution_globals)
        if extra_globals:
            scope.update(extra_globals)

        try:
            exec(code, scope)
        except SyntaxError as e:
            raise ValueError(f"Syntax Error in Blueprint: {e}")
        except Exception as e:
            raise ValueError(f"Runtime Error in Blueprint: {e}")

        if "generate_job" not in scope:
            raise ValueError("Blueprint must define a function named 'generate_job'")

        return scope

    def analyze_signature(self, code: str) -> List[BlueprintParamSchema]:
        """
        静态分析 generate_job 函数签名，提取参数元数据（包括 Annotated 中的 UI 配置）。
        支持类型：T, Optional[T], List[T], Optional[List[T]]
        其中 T 为基础类型：int, float, bool, str, Literal[...]
        """
        local_scope = self._compile_code(code, extra_globals={})
        func = local_scope["generate_job"]
        sig = inspect.signature(func)
        params_schema = []

        for name, param in sig.parameters.items():
            default_label = name.replace("_", " ").title()
            schema = BlueprintParamSchema(
                key=name,
                label=default_label,
                type="text",
                default=param.default if param.default != inspect.Parameter.empty else None,
            )

            # 解析 Annotated 元数据 (e.g., Annotated[int, {"min": 1}])
            annotation = param.annotation
            base_type = annotation
            meta_dict = {}

            if get_origin(annotation) is Annotated:
                args = get_args(annotation)
                base_type = args[0]
                for arg in args[1:]:
                    if isinstance(arg, dict):
                        meta_dict.update(arg)

            # 解包 Optional 和 List 包装
            # 支持：T, Optional[T], List[T], Optional[List[T]]
            is_optional = _is_optional_type(base_type)
            if is_optional:
                base_type = _unwrap_optional(base_type)
                schema.is_optional = True

            is_list = _is_list_type(base_type)
            if is_list:
                base_type = _unwrap_list(base_type)
                schema.is_list = True

            # 应用元数据到 Schema
            if "label" in meta_dict:
                schema.label = meta_dict["label"]
            if "description" in meta_dict:
                schema.description = meta_dict["description"]
            if "scope" in meta_dict:
                schema.scope = meta_dict["scope"]

            # 默认允许为空，除非另有指定
            schema.allow_empty = True

            # 类型映射逻辑 - 针对解包后的基础类型
            origin_base = get_origin(base_type)

            if base_type is int:
                schema.type = "number"
                if "min" in meta_dict: schema.min = meta_dict["min"]
                if "max" in meta_dict: schema.max = meta_dict["max"]

            elif base_type is float:
                schema.type = "float"
                if "min" in meta_dict: schema.min = meta_dict["min"]
                if "max" in meta_dict: schema.max = meta_dict["max"]
                if "placeholder" in meta_dict: schema.placeholder = meta_dict["placeholder"]

            elif base_type is bool:
                schema.type = "boolean"

            elif base_type is str:
                schema.type = "text"
                if "allow_empty" in meta_dict: schema.allow_empty = bool(meta_dict["allow_empty"])
                if "placeholder" in meta_dict: schema.placeholder = meta_dict["placeholder"]
                if "color" in meta_dict: schema.color = meta_dict["color"]
                if "border_color" in meta_dict: schema.border_color = meta_dict["border_color"]
                if "multi_line" in meta_dict: schema.multi_line = bool(meta_dict["multi_line"])
                if "min_lines" in meta_dict: schema.min_lines = int(meta_dict["min_lines"])

            elif base_type is FileSecret or (isinstance(base_type, type) and issubclass(base_type, FileSecret)):  # type: ignore[arg-type]
                schema.type = "file_secret"
                schema.allow_empty = False
                if "placeholder" in meta_dict: schema.placeholder = meta_dict["placeholder"]

            elif origin_base is Literal:
                schema.type = "select"
                allowed_values = get_args(base_type)
                meta_options = meta_dict.get("options", {})
                schema_options = []

                for val in allowed_values:
                    opt_label = str(val)
                    opt_desc = None

                    # 处理 select 选项的额外显示信息
                    if isinstance(meta_options, dict) and val in meta_options:
                        info = meta_options[val]
                        if isinstance(info, dict):
                            opt_label = info.get("label", str(val))
                            opt_desc = info.get("description")
                        elif isinstance(info, str):
                            opt_label = info

                    schema_options.append(BlueprintParamOption(
                        label=opt_label,
                        value=val,
                        description=opt_desc
                    ))
                schema.options = schema_options

            params_schema.append(schema)

        return params_schema

    def execute(self, code: str, inputs: Dict[str, Any]) -> JobSubmission:
        """
        执行蓝图代码。
        关键逻辑：利用 Pydantic 动态模型进行运行时类型转换 (Runtime Type Coercion)，
        解决 CLI/API 传入全字符串参数与 Blueprint 强类型定义之间的 Gap。
        """
        local_scope = self._compile_code(code, extra_globals={})
        func = local_scope.get("generate_job")
        
        if not func or not callable(func):
            raise ValueError("Blueprint must define a 'generate_job' function.")

        # 动态构建 Pydantic 模型
        sig = inspect.signature(func)
        field_definitions = {}
        
        for param_name, param in sig.parameters.items():
            annotation = param.annotation if param.annotation != inspect.Parameter.empty else Any
            default = param.default if param.default != inspect.Parameter.empty else ...
            field_definitions[param_name] = (annotation, default)

        DynamicModel = create_model(
            "DynamicBlueprintModel",
            **field_definitions,
            __config__=ConfigDict(extra='ignore') 
        )

        try:
            # 这里的 **inputs 包含了 CLI 传来的原始字符串
            # DynamicModel 初始化时会自动执行类型转换 (如 "10" -> 10, "true" -> True)
            validated_data_obj = DynamicModel(**inputs)
            validated_args = validated_data_obj.model_dump()

        except ValidationError as e:
            raise ValueError(f"Invalid parameters for blueprint: {e}")

        try:
            result = func(**validated_args)
            
            if isinstance(result, dict):
                return JobSubmission(**result)
            elif isinstance(result, JobSubmission):
                return result
            else:
                raise ValueError(f"Blueprint returned {type(result)}, expected dict or JobSubmission")

        except TypeError as e:
            # 捕获蓝图内部的逻辑错误，而非传参错误
            raise ValueError(f"Blueprint logic error: {e}")
        except Exception as e:
            raise ValueError(f"Runtime Error in Blueprint: {e}")


blueprint_manager = BlueprintManager()