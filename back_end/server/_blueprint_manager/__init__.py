# back_end/server/_blueprint_manager/__init__.py
"""Evaluate the existing Python Blueprint protocol in an isolated process."""

from .._magnus_config import magnus_config
from ._manager import BlueprintManager
from ._runner import BlueprintUnavailable, RuntimeSettings
from ._types import FileSecret

__all__ = [
    "blueprint_manager",
    "FileSecret",
    "BlueprintManager",
    "BlueprintUnavailable",
]

blueprint_manager = BlueprintManager(
    owner_key=magnus_config["server"]["root"],
    settings=RuntimeSettings(
        **magnus_config["server"].get("blueprint", {}),
    ),
)
