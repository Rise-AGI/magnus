# back_end/server/_slurm_manager/_ssh_auto_connect.py
"""可选：无人值守地（重）建 SSH ControlMaster socket。

当 transport.ssh.auto_connect 配置存在时，_SshControlMasterTransport 在每次跨界操作
前确保 master socket 活着；socket 失效（ControlPersist 过期 / 控制机重启）就用账号
持有人自己的登录密码 + TOTP 种子（标准 RFC 6238，pyotp 生成）回答 keyboard-interactive
的"密码 + 动态码"两段提示，自动重建。这只是把人肉一次的 2FA 登录自动化（生成验证码
是和手机 app 同一运算），不绕过任何远端安全机制；不配置则本模块零参与、transport
行为与历史完全一致。

pexpect / pyotp 仅在真正重建时惰性 import，使本模块 import 期只依赖标准库。
"""
import os
import subprocess
import threading
import time
from typing import Dict

# socket 是进程级唯一的全局资源，而 transport 实例可能每请求新建，故单飞锁必须是
# 模块级 —— 并发命中死 socket 时只让一个线程真正重建，其余在 ensure 的快路径/锁上
# 让行。
_establish_lock = threading.Lock()

# keyboard-interactive 提示词的默认匹配（覆盖常见英文 + 中文措辞）。先匹配更具体的
# 动态码提示、再匹配密码提示，故含 "otp" 的动态码提示即便也含 "password" 也会被正确
# 归为动态码。不同站点措辞不一，可经 config 的 totp_prompt / password_prompt 覆盖。
_DEFAULT_TOTP_PROMPT = (
    r"(?i)(verification code|one[- ]?time|otp|token|authenticator|动态(口令|密码)|验证码)"
)
_DEFAULT_PASSWORD_PROMPT = r"(?i)(password|口令|密码)"

_ESTABLISH_TIMEOUT_SECONDS = 45
# 防密码/动态码不对被远端反复追问而陷入死循环：应答次数超此上限即判定失败。
_MAX_PROMPT_ANSWERS = 6
# OTP 临界过期点处理：发码前若当前 TOTP 时间窗剩余不足这么多秒，就等到下一窗开始再
# 取，确保发出的码不会在远端校验前过期（不依赖远端的 ±1 窗口容差）。
_MIN_TOTP_VALIDITY_SECONDS = 5


def _read_secret(
    auto_connect: Dict,
    inline_key: str,
    file_key: str,
) -> str:
    """读取一项密钥：配了 *_file 就在用时从文件现读（使密钥不长驻 magnus_config
    字典里），否则取内联值。config 校验已保证内联与文件恰好二选一。"""
    secret_file = auto_connect.get(file_key)
    if secret_file:
        with open(secret_file, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    return str(auto_connect[inline_key]).strip()


def _totp_code_with_margin(totp) -> str:
    """取一个有足够剩余有效期的 TOTP 码。

    OTP 临界过期点：若恰在 30s 时间窗的末尾生成码，它可能在远端校验前就过期。这里在
    发码前看当前窗剩余多少，不足 _MIN_TOTP_VALIDITY_SECONDS 秒就等到下一窗开始再取，
    保证发出去的码有近满有效期 —— 不依赖远端是否容忍 ±1 窗口。周期取自 totp 自身
    （pyotp 默认 30s）。最坏只多等一个窗口，且发生在罕见的 socket 重建里、在 worker
    线程上、不碰事件循环。"""
    period = getattr(totp, "interval", 30) or 30
    remaining = period - (time.time() % period)
    if remaining < _MIN_TOTP_VALIDITY_SECONDS:
        time.sleep(remaining + 0.5)
    return totp.now()


def master_alive(
    control_path: str,
    host: str,
    user: str,
) -> bool:
    """`ssh -O check` 探活既有 master：socket 活返回 0。纯本地控制套接字操作，无网络
    往返、毫秒级。"""
    result = subprocess.run(
        [
            "ssh",
            "-O",
            "check",
            "-o",
            "BatchMode=yes",
            "-S",
            control_path,
            f"{user}@{host}",
        ],
        capture_output = True,
        text = True,
    )
    return result.returncode == 0


def ensure_control_master(
    control_path: str,
    host: str,
    user: str,
    auto_connect: Dict,
) -> None:
    """确保 master socket 活着：活则快返回（无锁），死则单飞重建（密码 + TOTP）。

    双检锁：先无锁探活走快路径（绝大多数调用 socket 都活着，不抢锁、不串行）；只有
    探到死才进锁，进锁后再探一次 —— 并发命中死 socket 时只有第一个线程真正重建，其余
    进锁发现已被重建好即返回。"""
    if master_alive(control_path, host, user):
        return
    with _establish_lock:
        if master_alive(control_path, host, user):
            return
        _establish(control_path, host, user, auto_connect)


def _establish(
    control_path: str,
    host: str,
    user: str,
    auto_connect: Dict,
) -> None:
    import pexpect
    import pyotp

    password = _read_secret(auto_connect, "password", "password_file")
    totp_secret = _read_secret(auto_connect, "totp_secret", "totp_secret_file")
    totp = pyotp.TOTP(totp_secret)
    control_persist = str(auto_connect.get("control_persist", "8h"))
    totp_prompt = auto_connect.get("totp_prompt") or _DEFAULT_TOTP_PROMPT
    password_prompt = auto_connect.get("password_prompt") or _DEFAULT_PASSWORD_PROMPT

    # 清理可能残留的死 socket 文件，否则 ssh -M 会因 "ControlSocket already exists"
    # 拒建。文件不存在（最常见）或其它 OS 错误都不阻断重建尝试 —— 真建不起来由后面的
    # master_alive 复检兜底报错。
    try:
        os.unlink(control_path)
    except OSError:
        pass

    child = pexpect.spawn(
        "ssh",
        [
            "-fNM",
            "-S",
            control_path,
            "-o",
            f"ControlPersist={control_persist}",
            "-o",
            "BatchMode=no",
            f"{user}@{host}",
        ],
        timeout = _ESTABLISH_TIMEOUT_SECONDS,
        encoding = "utf-8",
    )
    try:
        patterns = [totp_prompt, password_prompt, pexpect.EOF, pexpect.TIMEOUT]
        for _ in range(_MAX_PROMPT_ANSWERS):
            index = child.expect(patterns)
            if index == 0:
                # 动态码：取一个剩余有效期足够的码（处理 OTP 临界过期点）。两段提示在
                # 不同 read 中先后到达，按出现各自处理；列表里把 TOTP 放在 password 前，
                # 仅在二者同位匹配（实际不会发生）时作平手 tiebreak，避免含 "otp" 又含
                # "password" 的提示被误判成密码。
                child.sendline(_totp_code_with_margin(totp))
            elif index == 1:
                child.sendline(password)
            elif index == 2:
                # EOF：ssh -f 在认证成功后已 fork 到后台，前台进程退出。
                break
            else:
                raise RuntimeError(
                    "ssh ControlMaster 建立超时：未在预期时间内完成认证"
                )
        else:
            raise RuntimeError(
                "ssh ControlMaster 建立失败：提示应答次数超限（密码或动态码可能不正确）"
            )
    finally:
        # 关闭并回收前台进程；后台 master（-f fork 出去的）不受影响，持续持有 socket。
        child.close(force=True)

    if not master_alive(control_path, host, user):
        raise RuntimeError("ssh ControlMaster 建立后探活失败：socket 未就绪")
