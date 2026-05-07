# back_end/server/_feishu_client.py
"""项目相关的 FeishuClient 单例。FeishuClient 类本身在 library/functional/feishu_tools.py。"""
from library.functional.feishu_tools import FeishuClient
from ._magnus_config import magnus_config, is_local_auth


__all__ = [
    "feishu_client",
]


if is_local_auth:
    feishu_client = None  # type: ignore
else:
    _config = magnus_config["server"]["auth"]["feishu_client"]

    feishu_client = FeishuClient(
        app_id = _config["app_id"],
        app_secret = _config["app_secret"],
    )