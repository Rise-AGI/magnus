# back_end/server/_blueprint_manager.py
import inspect
import logging
from typing import Any, Dict, List, Annotated, Literal, get_origin, get_args

from pydantic import ValidationError, create_model, ConfigDict

from .models import JobType
from .schemas import JobSubmission, BlueprintParamSchema, BlueprintParamOption

logger = logging.getLogger(__name__)


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
            "Annotated": Annotated,
            "Literal": Literal,
            "List": List,
            "Dict": Dict,
            "Any": Any,
        }

    def _compile_code(self, code: str, extra_globals: Dict[str, Any]) -> dict:
        scope = self.execution_globals.copy()
        if extra_globals:
            scope.update(extra_globals)

        try:
            exec(code, scope, scope)
        except Exception as e:
            raise ValueError(f"Syntax Error in Blueprint: {e}")

        if "generate_job" not in scope:
            raise ValueError("Blueprint must define a function named 'generate_job'")

        return scope

    def analyze_signature(self, code: str) -> List[BlueprintParamSchema]:
        """
        静态分析 generate_job 函数签名，提取参数元数据（包括 Annotated 中的 UI 配置）。
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

            # 应用元数据到 Schema
            if "label" in meta_dict:
                schema.label = meta_dict["label"]
            if "description" in meta_dict:
                schema.description = meta_dict["description"]
            if "scope" in meta_dict:
                schema.scope = meta_dict["scope"]
            
            # 默认允许为空，除非另有指定
            schema.allow_empty = True

            # 类型映射逻辑
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