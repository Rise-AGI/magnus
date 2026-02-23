# back_end/server/_feishu_client.py
from library import *
from ._magnus_config import magnus_config


__all__ = [
    "feishu_client",
]


_config = magnus_config["server"]["auth"]["feishu_client"]

feishu_client = FeishuClient(
    app_id = _config["app_id"],
    app_secret = _config["app_secret"],
)