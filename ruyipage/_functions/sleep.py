# -*- coding: utf-8 -*-
"""asyncio 感知的 sleep 函数

在同步上下文中等价于 time.sleep()。
在 greenlet 异步上下文中自动 yield 给事件循环，避免阻塞。

同步用户完全不受影响 —— 当 greenlet 未安装时，
_HAS_ASYNC 为 False，直接走 time.sleep()，
额外开销仅为一次布尔判断。
"""

import time as _time

_HAS_ASYNC = False
_in_async_greenlet = None
_await_ = None

try:
    from .._async.greenlet_bridge import _in_async_greenlet as _iag, await_ as _aw

    _HAS_ASYNC = True
    _in_async_greenlet = _iag
    _await_ = _aw
except ImportError:
    pass


def sleep(seconds):
    """asyncio 感知的 sleep

    - 同步上下文 / greenlet 未安装：等同于 time.sleep(seconds)
    - greenlet 异步上下文：await asyncio.sleep(seconds)，让出事件循环

    Args:
        seconds: 休眠时长（秒）
    """
    if _HAS_ASYNC and _in_async_greenlet():
        import asyncio

        return _await_(asyncio.sleep(seconds))
    _time.sleep(seconds)
