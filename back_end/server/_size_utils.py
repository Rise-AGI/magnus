# back_end/server/_size_utils.py
"""零依赖的 size 字符串解析。被 _magnus_config / _resource_manager / _file_custody_manager / routers 共享。"""


def _parse_size_string(size_str: str) -> int:
    """解析大小字符串，如 '200G', '1024M'，返回字节数"""
    size_str = size_str.strip().upper()
    units = {'B': 1, 'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
    for unit, multiplier in units.items():
        if size_str.endswith(unit):
            return int(float(size_str[:-1]) * multiplier)
    return int(size_str)
