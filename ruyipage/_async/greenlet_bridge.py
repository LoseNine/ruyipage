# -*- coding: utf-8 -*-
"""greenlet 桥接层 —— 允许同步代码在 asyncio 事件循环中透明运行

核心机制（参考 SQLAlchemy lib/sqlalchemy/util/concurrency.py）：

1. greenlet_spawn(fn, *args)：在一个新 greenlet 中运行同步函数 fn。
   当 fn 内部需要执行异步 I/O 时，通过 await_() 挂起 greenlet，
   将 awaitable 交给父协程（asyncio 驱动），由事件循环完成 I/O。
   I/O 完成后 greenlet 恢复继续执行。

2. await_(awaitable)：从同步代码中调用，挂起当前 greenlet，
   将 awaitable 交还给 greenlet_spawn 中的 ``await result``。

3. _in_async_greenlet()：检测当前是否在异步 greenlet 上下文中。
   用于 BrowserBiDiDriver.run() 等热路径上的条件分支。
"""

import sys
import greenlet


class _AsyncIoGreenlet(greenlet.greenlet):
    """运行同步代码的 greenlet

    parent 指向 asyncio 驱动协程所在的 greenlet（即调用 greenlet_spawn 的那个）。
    """

    def __init__(self, fn, driver):
        super().__init__(fn, driver)
        self.driver = driver


async def greenlet_spawn(fn, *args, **kwargs):
    """在 greenlet 中运行同步函数，I/O 点自动切换到 asyncio

    同步函数 fn 在一个新的 _AsyncIoGreenlet 中执行。
    当 fn（或其任意深度的被调函数）调用 await_(some_awaitable) 时：
      1. greenlet 挂起，将 some_awaitable 传回此处
      2. 本函数 await some_awaitable，由事件循环执行 I/O
      3. I/O 完成后，将结果送回 greenlet，fn 继续执行

    Args:
        fn: 要运行的同步函数
        *args: 传给 fn 的位置参数
        **kwargs: 传给 fn 的关键字参数

    Returns:
        fn 的返回值

    Raises:
        fn 抛出的任何异常都会被正确传播
    """
    context = _AsyncIoGreenlet(fn, greenlet.getcurrent())

    # 启动 greenlet，执行 fn(*args, **kwargs)
    try:
        result = context.switch(*args, **kwargs)
    except BaseException:
        # greenlet 启动就失败（极少见）
        raise

    # 循环处理 greenlet 的 I/O 请求
    while not context.dead:
        try:
            # result 是 fn 通过 await_() 传出的 awaitable
            value = await result
        except BaseException:
            # 将异常送回 greenlet（fn 中的 await_() 会抛出）
            try:
                result = context.throw(*sys.exc_info())
            except BaseException:
                raise
        else:
            # 将 I/O 结果送回 greenlet（fn 中的 await_() 返回此值）
            try:
                result = context.switch(value)
            except BaseException:
                raise

    # greenlet 已执行完毕，result 是 fn 的返回值
    return result


def await_(awaitable):
    """从同步代码中调用 —— 挂起 greenlet，将 awaitable 交给 asyncio

    只能在 greenlet_spawn 创建的 greenlet 内部调用。
    在同步用户的正常代码中调用会抛出 RuntimeError。

    Args:
        awaitable: 任何可 await 的对象（coroutine, Future, Task 等）

    Returns:
        awaitable 的结果

    Raises:
        RuntimeError: 如果不在 greenlet_spawn 上下文中调用
        awaitable 抛出的任何异常
    """
    current = greenlet.getcurrent()
    if not isinstance(current, _AsyncIoGreenlet):
        raise RuntimeError(
            "await_() 只能在 greenlet_spawn 上下文中使用。"
            "请确保通过 ruyipage.aio 模块的异步入口调用。"
        )
    # 切换到 parent greenlet（greenlet_spawn 协程），传出 awaitable
    return current.parent.switch(awaitable)


def _in_async_greenlet():
    """检测当前是否在异步 greenlet 上下文中运行

    Returns:
        bool: True 表示当前在 greenlet_spawn 创建的 greenlet 内部
    """
    return isinstance(greenlet.getcurrent(), _AsyncIoGreenlet)
