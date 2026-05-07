# back_end/server/_blueprint_manager/_sandbox.py
"""Blueprint 代码的受限编译沙箱。

`_compile_code(code, execution_globals)` 在受限 builtins + 单一 import 白名单
（typing）下 exec 用户代码，要求返回的 scope 中含 `blueprint` 函数。
"""
from typing import Any, Dict, Optional


def _compile_code(
    code: str,
    execution_globals: Dict[str, Any],
    extra_globals: Optional[Dict[str, Any]] = None,
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
    scope.update(execution_globals)
    if extra_globals:
        scope.update(extra_globals)

    try:
        exec(code, scope)
    except SyntaxError as e:
        raise ValueError(f"Syntax Error in Blueprint: {e}")
    except Exception as e:
        raise ValueError(f"Runtime Error in Blueprint: {e}")

    if "blueprint" not in scope:
        raise ValueError("Blueprint must define a function named 'blueprint'")

    return scope
