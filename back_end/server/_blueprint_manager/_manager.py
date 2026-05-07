# back_end/server/_blueprint_manager/_manager.py
"""BlueprintManager 主类：解析签名、运行时执行 + 类型转换。"""
import inspect
from typing import Any, Dict, List, Optional, Annotated, Literal, get_origin, get_args

from pydantic import ValidationError, create_model, ConfigDict

from ..models import JobType
from ..schemas import JobSubmission, BlueprintParamSchema, BlueprintParamOption
from ._types import (
    FileSecret,
    _BlueprintCapture,
    _is_optional_type,
    _unwrap_optional,
    _is_list_type,
    _unwrap_list,
    _type_display_name,
)
from ._sandbox import _compile_code


class BlueprintManager:
    """
    负责解析用户编写的 Python Blueprint 代码。
    核心功能：
    1. analyze_signature: 静态分析代码签名，生成前端表单 Schema。
    2. execute: 动态编译并执行代码，包含运行时类型强制转换 (String -> Typed)。
    """

    def __init__(self):
        def _hijacked_submit_job(**kwargs: Any) -> None:
            raise _BlueprintCapture(kwargs)

        self.execution_globals = {
            "submit_job": _hijacked_submit_job,
            "JobType": JobType,
            "FileSecret": FileSecret,
            "Annotated": Annotated,
            "Literal": Literal,
            "Optional": Optional,
            "List": List,
            "Dict": Dict,
            "Any": Any,
        }

    def analyze_signature(self, code: str) -> List[BlueprintParamSchema]:
        """
        静态分析 blueprint 函数签名，提取参数元数据（包括 Annotated 中的 UI 配置）。
        支持类型：T, Optional[T], List[T], Optional[List[T]]
        其中 T 为基础类型：int, float, bool, str, Literal[...]
        """
        local_scope = _compile_code(code, self.execution_globals)
        func = local_scope["blueprint"]
        sig = inspect.signature(func)
        params_schema = []

        for name, param in sig.parameters.items():
            default_label = name.replace("_", " ").title()
            schema = BlueprintParamSchema(
                key = name,
                label = default_label,
                type = "text",
                default = param.default if param.default != inspect.Parameter.empty else None,
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
            # 支持：T, Optional[T], List[T], Optional[List[T]], List[Optional[T]]
            is_optional = _is_optional_type(base_type)
            if is_optional:
                base_type = _unwrap_optional(base_type)
                schema.is_optional = True

            is_list = _is_list_type(base_type)
            if is_list:
                base_type = _unwrap_list(base_type)
                schema.is_list = True

            if is_list and _is_optional_type(base_type):
                base_type = _unwrap_optional(base_type)
                schema.is_item_optional = True

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
                if "min" in meta_dict:
                    schema.min = meta_dict["min"]
                if "max" in meta_dict:
                    schema.max = meta_dict["max"]

            elif base_type is float:
                schema.type = "float"
                if "min" in meta_dict:
                    schema.min = meta_dict["min"]
                if "max" in meta_dict:
                    schema.max = meta_dict["max"]
                if "placeholder" in meta_dict:
                    schema.placeholder = meta_dict["placeholder"]

            elif base_type is bool:
                schema.type = "boolean"

            elif base_type is str:
                schema.type = "text"
                if "allow_empty" in meta_dict:
                    schema.allow_empty = bool(meta_dict["allow_empty"])
                if "placeholder" in meta_dict:
                    schema.placeholder = meta_dict["placeholder"]
                if "color" in meta_dict:
                    schema.color = meta_dict["color"]
                if "border_color" in meta_dict:
                    schema.border_color = meta_dict["border_color"]
                if "multi_line" in meta_dict:
                    schema.multi_line = bool(meta_dict["multi_line"])
                if "min_lines" in meta_dict:
                    schema.min_lines = int(meta_dict["min_lines"])

            elif base_type is FileSecret or (isinstance(base_type, type) and issubclass(base_type, FileSecret)):  # type: ignore[arg-type]
                schema.type = "file_secret"
                schema.allow_empty = False
                if "placeholder" in meta_dict:
                    schema.placeholder = meta_dict["placeholder"]

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
                        label = opt_label,
                        value = val,
                        description = opt_desc,
                    ))
                schema.options = schema_options

            params_schema.append(schema)

        return params_schema

    def execute(self, code: str, inputs: Dict[str, Any]) -> JobSubmission:
        """
        执行蓝图代码。
        劫持机制：blueprint() 内调用 submit_job() 时抛出 _BlueprintCapture，
        捕获传入的 kwargs，构造 JobSubmission 返回。
        """
        local_scope = _compile_code(code, self.execution_globals)
        func = local_scope.get("blueprint")

        if not func or not callable(func):
            raise ValueError("Blueprint must define a 'blueprint' function.")

        # 动态构建 Pydantic 模型做运行时类型转换
        sig = inspect.signature(func)
        field_definitions = {}

        for param_name, param in sig.parameters.items():
            annotation = param.annotation if param.annotation != inspect.Parameter.empty else Any
            default = param.default if param.default != inspect.Parameter.empty else ...
            field_definitions[param_name] = (annotation, default)

        # CLI 对 List[T] 参数可能发送标量字符串，预处理
        processed_inputs = dict(inputs)
        for param_name, param in sig.parameters.items():
            if param_name not in processed_inputs:
                continue
            annotation = param.annotation if param.annotation != inspect.Parameter.empty else Any
            if get_origin(annotation) is Annotated:
                annotation = get_args(annotation)[0]
            if _is_optional_type(annotation):
                annotation = _unwrap_optional(annotation)
            if _is_list_type(annotation):
                value = processed_inputs[param_name]
                if value is not None and not isinstance(value, list):
                    processed_inputs[param_name] = [value]

        # 参数名校验：检查是否传入了签名中不存在的参数
        expected_params = set(sig.parameters.keys())
        unknown_params = set(processed_inputs.keys()) - expected_params
        if unknown_params:
            sig_str = ", ".join(
                f"{name}: {_type_display_name(param.annotation)}"
                for name, param in sig.parameters.items()
            )
            raise ValueError(
                f"Unknown parameter(s): {', '.join(sorted(unknown_params))}\n"
                f"Expected signature: blueprint({sig_str})"
            )

        DynamicModel = create_model(
            "DynamicBlueprintModel",
            **field_definitions,
            __config__ = ConfigDict(extra='ignore'),
        )

        try:
            validated_data_obj = DynamicModel(**processed_inputs)
            validated_args = validated_data_obj.model_dump()
        except ValidationError as error:
            messages = []
            for err in error.errors():
                field = ".".join(str(x) for x in err["loc"])
                expected_type = field_definitions.get(field, (None,))[0]
                type_hint = _type_display_name(expected_type) if expected_type else "unknown"
                messages.append(f"Parameter '{field}': expected {type_hint}, {err['msg']}")
            raise ValueError("\n".join(messages))

        try:
            func(**validated_args)
            raise ValueError("Blueprint function must call submit_job()")
        except _BlueprintCapture as capture:
            # JobType enum 实例 Pydantic 不识别，转 str 后再走 schema 验证
            payload = capture.payload
            if "job_type" in payload and isinstance(payload["job_type"], JobType):
                payload["job_type"] = payload["job_type"].value
            return JobSubmission(**payload)
        except ValueError:
            raise
        except TypeError as error:
            raise ValueError(f"Blueprint logic error: {error}")
        except Exception as error:
            raise ValueError(f"Runtime Error in Blueprint: {error}")
