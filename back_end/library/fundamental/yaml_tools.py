
from .typing import *
from .externals import *


__all__ = [
    "load_from_yaml",
    "load_from_yaml_async",
]


def load_from_yaml(
    file_path: str,
)-> Any:
    
    yaml_loader = ruamel_yaml(typ="safe")
    with open(file_path, "r", encoding="utf-8") as file:
        data = yaml_loader.load(file)
    return data


async def load_from_yaml_async(
    file_path: str,
)-> Any:
    
    yaml_loader = ruamel_yaml(typ="safe")
    async with aiofiles.open(file_path, "r", encoding="utf-8") as file:
        content: str = await file.read()
    data = yaml_loader.load(content)
    return data, content