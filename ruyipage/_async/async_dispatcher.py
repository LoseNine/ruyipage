# -*- coding: utf-8 -*-
"""异步命令分发器 —— 对标 _base/dispatcher.py

使用 asyncio.Future 替代 Queue.get() 的阻塞等待。
每个命令创建一个 Future，recv_loop 收到响应后 set_result。

设计说明：
    此分发器运行在 asyncio 事件循环中（单线程），
    因此不需要任何 threading.Lock。
"""

import asyncio
import logging

from ..errors import BiDiError, PageDisconnectedError

logger = logging.getLogger("ruyipage")


class AsyncCommandDispatcher:
    """异步 BiDi 命令分发器

    与 AsyncBiDiTransport 配合：
      await dispatcher.dispatch(transport, method, params) → result dict
    """

    def __init__(self):
        self._cur_id = 0
        self._pending = {}  # {cmd_id: asyncio.Future}

    # ── 核心接口 ──────────────────────────────────────────────────────────

    async def dispatch(self, transport, method, params=None, timeout=30):
        """发送命令并异步等待响应

        Args:
            transport: AsyncBiDiTransport 实例
            method: BiDi 方法名，如 'browsingContext.navigate'
            params: 参数字典
            timeout: 超时（秒）

        Returns:
            响应的 result 字典

        Raises:
            BiDiError: 协议错误或超时
            PageDisconnectedError: 连接断开
        """
        cmd_id = self._next_id()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[cmd_id] = future

        msg = {"id": cmd_id, "method": method, "params": params or {}}
        try:
            await transport.send(msg)
            logger.debug("AsyncDispatcher 发送 id=%d %s", cmd_id, method)
        except Exception as e:
            self._pending.pop(cmd_id, None)
            raise PageDisconnectedError("命令发送失败: {}".format(e))

        # 异步等待响应
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise BiDiError(
                "timeout", "命令超时: {} ({}s)".format(method, timeout)
            )
        finally:
            self._pending.pop(cmd_id, None)

        if result is None:
            raise PageDisconnectedError(
                "连接已断开（命令 {} 未收到响应）".format(method)
            )

        if result.get("type") == "error":
            raise BiDiError(
                result.get("error", "unknown"),
                result.get("message", ""),
                result.get("stacktrace", ""),
            )

        return result.get("result", {})

    def on_response(self, msg):
        """接收到命令响应时调用（由 recv_loop 在同一事件循环中调用）

        Args:
            msg: 已解析的响应字典（含 'id' 字段）
        """
        cmd_id = msg.get("id")
        if cmd_id is None:
            return
        future = self._pending.get(cmd_id)
        if future and not future.done():
            future.set_result(msg)
            logger.debug("AsyncDispatcher 路由响应 id=%d", cmd_id)
        else:
            logger.debug("AsyncDispatcher 收到未知 id=%d 的响应", cmd_id)

    def wake_all(self):
        """连接断开时唤醒所有等待者（set_result(None) 触发 PageDisconnectedError）"""
        for future in self._pending.values():
            if not future.done():
                future.set_result(None)
        self._pending.clear()

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _next_id(self):
        self._cur_id += 1
        return self._cur_id
