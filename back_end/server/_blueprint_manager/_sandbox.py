# back_end/server/_blueprint_manager/_sandbox.py
"""Compatibility scope inside the OS sandbox, not a Python security boundary."""

import types
from typing import Any, Dict, Optional


def _compile_code(
    code: str,
    execution_globals: Dict[str, Any],
    extra_globals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # 允许导入的模块白名单
    allowed_modules = {
        "typing": __import__("typing"),
        "__future__": __import__("__future__"),
    }
    magnus = types.ModuleType("magnus")
    for name in ("submit_job", "JobType", "FileSecret"):
        setattr(magnus, name, execution_globals[name])
    allowed_modules["magnus"] = magnus

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

    # Preserve the Blueprint language surface. The worker's OS isolation is
    # responsible for security, including access through Python reflection.
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
    scope.update(execution_globals)
    if extra_globals:
        scope.update(extra_globals)

    try:
        exec(code, scope)
    except SyntaxError as error:
        raise ValueError(f"Syntax Error in Blueprint: {error}")
    except Exception as error:
        raise ValueError(f"Runtime Error in Blueprint: {error}")

    if "blueprint" not in scope:
        raise ValueError("Blueprint must define a function named 'blueprint'")

    return scope
