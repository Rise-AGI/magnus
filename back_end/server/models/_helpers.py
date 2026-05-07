# back_end/server/models/_helpers.py
"""Model 用到的小工具：ID 生成器等。"""
import secrets


def generate_hex_id() -> str:
    return secrets.token_hex(8)
