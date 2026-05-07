# back_end/server/_resource_manager/_config.py
"""配置常量 + 全局只读状态：mirror、cache size、git env、TTLs、SHA pattern。"""
import os
import re
from typing import Optional

from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string


_registry_mirror: Optional[str] = magnus_config["cluster"]["registry_mirror"]


_DOCKER_HUB_HOSTS = {"registry-1.docker.io", "index.docker.io", "docker.io"}


def _rewrite_image_for_mirror(image: str) -> str:
    if _registry_mirror is None:
        return image

    for scheme in ("docker://", "oras://"):
        if image.startswith(scheme):
            rest = image[len(scheme):]
            break
    else:
        return image

    parts = rest.split("/")
    host = parts[0] if len(parts) > 1 and ("." in parts[0] or ":" in parts[0]) else None

    if host is None:
        # docker://alpine, docker://pytorch/pytorch:tag — implicit Docker Hub
        name_part = rest.split(":")[0].split("@")[0]
        if "/" not in name_part:
            rest = "library/" + rest
        return f"{scheme}{_registry_mirror}/{rest}"

    if host in _DOCKER_HUB_HOSTS:
        # oras://registry-1.docker.io/user/repo:tag — explicit Docker Hub
        rest_without_host = "/".join(parts[1:])
        return f"{scheme}{_registry_mirror}/{rest_without_host}"

    return image


CONTAINER_CACHE_SIZE = _parse_size_string(magnus_config['execution']['resource_cache']['container_cache_size'])
REPO_CACHE_SIZE = _parse_size_string(magnus_config['execution']['resource_cache']['repo_cache_size'])


# git subprocess 使用的干净环境：禁用交互式认证，剔除 IDE 注入的 credential helper
_git_env = os.environ.copy()
_git_env["GIT_TERMINAL_PROMPT"] = "0"
for _k in ("GIT_ASKPASS", "SSH_ASKPASS"):
    _git_env.pop(_k, None)


# Fanout 聚合窗口：同一个 cache 的 origin refs 在这个 TTL 内被认为足够新，
# 避免一次 blueprint fanout 里 N 个 worker 串行打 N 次上游 fetch
# （TTL 内所有 job 共享同一次 fetch 的结果，N 次上游请求 → 1 次）
REPO_FRESHNESS_TTL_SECONDS = 30

# 默认分支解析结果的内存缓存 TTL：fanout 里所有 worker 解析的是同一个 repo
# 的默认分支，没必要每次都打 ls-remote
DEFAULT_BRANCH_CACHE_TTL_SECONDS = 300

# git fetch 重试与超时（对齐 _resolve_default_branch 的 3 次重试 + 指数退避；
# fetch 比 ls-remote 重，超时放宽到 30s）
GIT_FETCH_MAX_RETRIES = 3
GIT_FETCH_TIMEOUT_SECONDS = 30

# cat-file 只读本地 object db，正常毫秒级；这里只是为了在 NFS / 盘满等异常
# I/O 场景下避免无限期阻塞整个 fanout（此 subprocess 是在 repo_lock 内调用）
GIT_CAT_FILE_TIMEOUT_SECONDS = 5

# cache 的上次 fetch 时间戳文件名，放在 <cache>/.git/ 里避免污染 working tree
CACHE_FETCH_TIMESTAMP_FILENAME = "magnus_fetch_ts"

# 只有 40 位完整 hex SHA 被视为不可变、可以走"cache 已有则跳过 fetch"的快路径。
# tag / branch 名 / 短 SHA 都可能指向移动目标或产生歧义，必须经 TTL + 显式 fetch 把关，
# 避免把陈旧的同名 ref 悄悄当成用户想要的那一版交付出去。
# 大小写都接受：git 默认小写，但人工复制 / API 产物中大写 SHA 合法且常见
FULL_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
