# back_end/server/_blueprint_manager/__init__.py
"""Blueprint manager: parse user-defined Python blueprints into JobSubmission.

- _types.py:    FileSecret 文件凭证类型 + 类型 introspection 工具 + 劫持异常
- _sandbox.py:  受限 builtins + 单一 import 白名单（typing）的 exec 沙箱
- _manager.py:  BlueprintManager 主类（analyze_signature / execute）

公共面：单例 `blueprint_manager`、`FileSecret`、`BlueprintManager`。
"""
import logging

logger = logging.getLogger(__name__)

from ._types import FileSecret
from ._manager import BlueprintManager

__all__ = ["blueprint_manager", "FileSecret", "BlueprintManager"]

blueprint_manager = BlueprintManager()
