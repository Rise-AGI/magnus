# back_end/server/_github_client.py
from library import *
from ._magnus_config import *


__all__ = [
    "github_client",
]


if is_local_mode:
    github_client = GitHubClient(token="")
else:
    github_client = GitHubClient(
        token = magnus_config["server"]["github_client"]["token"],
    )