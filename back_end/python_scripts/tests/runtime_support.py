"""Import wrapper helpers without the scheduler's live-site initialization."""

import importlib
import sys
from pathlib import Path
from types import ModuleType

SERVER = Path(__file__).resolve().parents[2] / "server"
for name, path in (
    ("isolated_runtime_server", SERVER),
    ("isolated_runtime_server._scheduler", SERVER / "_scheduler"),
):
    package = ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules.setdefault(name, package)

WRAPPER = importlib.import_module("isolated_runtime_server._scheduler._wrapper_template")
RUNTIME_IMAGE = importlib.import_module("isolated_runtime_server._runtime_image")
