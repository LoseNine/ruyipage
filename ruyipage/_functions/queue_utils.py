# -*- coding: utf-8 -*-
"""asyncio 感知的 Queue.get 包装

在同步上下文中等价于 q.get(timeout=...)。
在 greenlet 异步上下文中通过 executor 执行，避免阻塞事件循环。

设计说明：
- 核心命令路径 (BrowserBiDiDriver.run) 不使用此函数，
  它直接通过 await_(asyncio.Future) 走原生异步，无需 executor。
- 本函数服务于辅助模块的轮询循环（listener.wait、downloads.wait 等），
  将阻塞的 Queue.get 放入线程池执行，每次最多阻塞 executor 线程而非事件循环。
"""

from queue import Empty

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


def queue_get(q, timeout=None):
    """asyncio 感知的 Queue.get

    - 同步上下文 / greenlet 未安装：等同于 q.get(timeout=...)
    - greenlet 异步上下文：在 executor 中运行，不阻塞事件循环

    Args:
        q: queue.Queue 实例
        timeout: 超时（秒），None 为无限等待

    Returns:
        队列中的元素

    Raises:
        queue.Empty: 超时未获取到元素
    """
    if _HAS_ASYNC and _in_async_greenlet():
        import asyncio

        async def _async_get():
            loop = asyncio.get_running_loop()
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: q.get(timeout=timeout)),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                raise Empty()

        return _await_(_async_get())

    return q.get(timeout=timeout)
