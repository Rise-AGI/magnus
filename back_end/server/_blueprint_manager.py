import inspect
import logging
from typing import Any, Dict, List, Annotated, get_origin, get_args
from datetime import datetime
from pydantic import ValidationError
from .models import JobType
from .schemas import JobSubmission, BlueprintParamSchema

logger = logging.getLogger(__name__)

class BlueprintManager:
    """
    负责解析用户编写的 Python Blueprint 代码，
    提取参数元数据（用于前端渲染），并执行代码生成 Job。
    """

    def __init__(self):
        # 定义 Blueprint 代码运行时的全局命名空间
        # 仅注入必要的类型和 helper，做最小限度的沙盒隔离
        self.execution_globals = {
            "JobSubmission": JobSubmission,
            "JobType": JobType,
            "Annotated": Annotated,
            "List": List,
            "Dict": Dict,
            "Any": Any,
        }

    def _compile_code(self, code: str, extra_globals: Dict[str, Any]) -> dict:
        """
        编译并执行代码定义。
        关键修改：exec 的 globals 和 locals 使用同一个字典，
        确保函数定义时能捕获到同一层级定义的 Type Alias (如 UserName)。
        """

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
        静态分析 generate_job 函数的签名，生成前端表单 Schema。
        """
        local_scope = self._compile_code(code, extra_globals={})
        func = local_scope["generate_job"]
        
        sig = inspect.signature(func)
        
        params_schema = []

        for name, param in sig.parameters.items():

            schema = BlueprintParamSchema(
                key=name,
                label=name.replace("_", " ").title(),
                type="text",
                default=param.default if param.default != inspect.Parameter.empty else None
            )

            annotation = param.annotation
            
            origin = get_origin(annotation)
            
            if origin is Annotated:
                base_type, meta = get_args(annotation)
                
                # 基础类型判断
                if base_type is int:
                    schema.type = "number"
                elif base_type is bool:
                    schema.type = "boolean"
                elif base_type is str:
                    schema.type = "text"
                
                # 元数据提取
                if isinstance(meta, dict):
                    if "label" in meta: schema.label = meta["label"]
                    if "description" in meta: schema.description = meta["description"]
                    if "min" in meta: schema.min = meta["min"]
                    if "max" in meta: schema.max = meta["max"]
                    if "step" in meta: schema.step = meta["step"]
                    if "placeholder" in meta: schema.placeholder = meta["placeholder"]
                    if "options" in meta: 
                        schema.type = "select"
                        schema.options = meta["options"]
            
            # 3. 普通类型兜底
            elif annotation is int:
                schema.type = "number"
            elif annotation is bool:
                schema.type = "boolean"

            params_schema.append(schema)
            
        return params_schema

    def execute(self, code: str, inputs: Dict[str, Any]) -> JobSubmission:
        
        local_scope = self._compile_code(code, extra_globals={})
        func = local_scope["generate_job"]
        
        sig = inspect.signature(func)
        call_args = {k: v for k, v in inputs.items() if k in sig.parameters}
        
        try:
            result = func(**call_args)
            if isinstance(result, dict):
                return JobSubmission(**result)
            elif isinstance(result, JobSubmission):
                return result
            else:
                raise ValueError(f"Blueprint returned {type(result)}, expected dict or JobSubmission")
                
        except TypeError as e:
            raise ValueError(f"Parameter mismatch: {e}")
        except ValidationError as e:
            raise ValueError(f"Generated Job data is invalid: {e}")
        except Exception as e:
            raise ValueError(f"Runtime Error in Blueprint: {e}")

blueprint_manager = BlueprintManager()