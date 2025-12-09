# back_end/server/_feishu_client.py
from library import *
from ._magnus_config import magnus_config
import sys

__all__ = [
    "feishu_client",
]

# ---------------------------------------------------------
# 1. 配置读取与校验
# ---------------------------------------------------------

# 检查根节点
if "feishu_client" not in magnus_config.get("server", {}):
    error_msg = (
        "❌ 启动失败: 配置文件中缺少 'server.feishu_client' 字段。\n"
        "   请检查 configs/magnus_config.yaml 是否正确缩进。"
    )
    print(error_msg)
    sys.exit(1) # 直接终止，防止带病运行

_cfg = magnus_config["server"]["feishu_client"]

# 检查子字段
required_keys = ["app_id", "app_secret", "redirect_uri"]
missing_keys = [key for key in required_keys if not _cfg.get(key)]

if missing_keys:
    raise RuntimeError(
        f"❌ 启动失败: 飞书配置不完整。\n"
        f"   缺少字段: {missing_keys}\n"
        f"   请在 magnus_config.yaml 中补全。"
    )

# ---------------------------------------------------------
# 2. 实例化客户端
# ---------------------------------------------------------

feishu_client = FeishuClient(
    app_id = _cfg["app_id"],
    app_secret = _cfg["app_secret"],
    redirect_uri = _cfg["redirect_uri"]
)

# ---------------------------------------------------------
# 3. 调试信息输出 (关键)
# ---------------------------------------------------------
# 为了安全，把 Secret 中间部分隐藏，只显示头尾，方便核对是否填错
secret_val = _cfg['app_secret']
if len(secret_val) > 6:
    masked_secret = f"{secret_val[:4]}******{secret_val[-4:]}"
else:
    masked_secret = "******"

print("\n" + "="*50)
print(f"✅ [Feishu] Auth Client Initialized")
print(f"   -> App ID:       {_cfg['app_id']}")
print(f"   -> App Secret:   {masked_secret} (Check for spaces!)")
print(f"   -> Redirect URI: {_cfg['redirect_uri']}")
print("="*50 + "\n")