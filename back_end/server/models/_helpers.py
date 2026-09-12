# back_end/server/models/_helpers.py
"""Model 用到的小工具：ID 生成器、列类型等。"""
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def generate_hex_id() -> str:
    return secrets.token_hex(8)


class UtcDateTime(TypeDecorator):
    """进出一律是 UTC 时刻的 DateTime 列。

    底层 DateTime 不保存 tzinfo（SQLite 落成不带偏移量的字符串）：写入时 aware 值的
    偏移量被直接丢掉，读出来是个 naive datetime。于是"库里存的是 UTC"只是一句口头
    约定 —— 每个消费方都得自己记得把 UTC 补回去，漏补的地方就会把 UTC 当本地时间用。
    API 边界正是漏补的那处：序列化出去的 ISO 串没有偏移量，浏览器的 new Date() 按本地
    时区解释，于是 UTC+8 的站点上 magnus 自己的时间比真实早 8 小时，而 SLURM 侧的时间
    另有来源、显示是对的，两者就对不齐。

    这里把约定固化进列本身：写入前统一折算成 UTC 再去掉 tzinfo（naive 入参视为已是
    UTC），读出时一律带上 UTC。ORM 拿到的时间从此都是明确的时刻，序列化自带偏移量，
    消费方不必再各自补。存储格式不变，历史数据（naive UTC）原样兼容。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
