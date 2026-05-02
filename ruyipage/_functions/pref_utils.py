# -*- coding: utf-8 -*-
"""Firefox user.js pref 值的序列化与反序列化工具"""


def parse_pref_value(v):
    """解析 user.js 中的 pref 值字符串

    Args:
        v: 原始值字符串，如 'true', '"some string"', '42'

    Returns:
        解析后的 Python 值 (bool, int, float, str)
    """
    v = v.strip()
    if v == 'true':
        return True
    if v == 'false':
        return False
    if v.startswith('"') or v.startswith("'"):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def format_pref_value(value):
    """将 Python 值格式化为 user.js pref 值字符串

    Args:
        value: Python 值 (bool, int, float, str)

    Returns:
        格式化后的字符串
    """
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return '"{}"'.format(str(value).replace('\\', '\\\\').replace('"', '\\"'))
