# back_end/server/_feishu_client.py
from library import *
from ._magnus_config import magnus_config, is_local_mode


__all__ = [
    "feishu_client",
]


if is_local_mode:
    feishu_client = None  # type: ignore
else:
    _config = magnus_config["server"]["auth"]["feishu_client"]

    feishu_client = FeishuClient(
        app_id = _config["app_id"],
        app_secret = _config["app_secret"],
    )