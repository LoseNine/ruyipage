# -*- coding: utf-8 -*-
"""异步 BiDi 驱动层 —— 对标 _base/driver.py

组合 AsyncBiDiTransport + AsyncCommandDispatcher，提供：
- 异步 WebSocket 连接管理
- 异步命令发送/接收
- 异步事件分发

设计说明：
    此模块不是 BrowserBiDiDriver 的替代品，而是其异步扩展。
    通过 BrowserBiDiDriver.switch_to_async() 挂载到现有 driver 上，
    使得 driver.run() 在 greenlet 异步上下文中走异步路径。
"""

import asyncio
import json
import logging

from .async_transport import AsyncBiDiTransport
from .async_dispatcher import AsyncCommandDispatcher

logger = logging.getLogger("ruyipage")


class AsyncBiDiDriverBridge:
    """异步驱动桥接器

    持有异步传输层和命令分发器，为 BrowserBiDiDriver 提供异步能力。
    不独立使用 —— 由 BrowserBiDiDriver.switch_to_async() 创建并挂载。
    """

    def __init__(self):
        self.transport = None
        self.dispatcher = AsyncCommandDispatcher()
        self._event_task = None
        self._event_queue = None
        self._event_handlers = {}  # {(method, context): callback}
        self._immediate_handlers = {}  # {(method, context): callback}
        self._running = False
        self._host_driver = None  # 指向 BrowserBiDiDriver 实例

    async def start(self, ws_url, host_driver):
        """建立异步 WebSocket 连接并启动事件分发

        Args:
            ws_url: WebSocket URL
            host_driver: BrowserBiDiDriver 实例（用于共享 alert_flag 等状态）
        """
        self._host_driver = host_driver
        self._event_queue = asyncio.Queue()

        self.transport = AsyncBiDiTransport(
            ws_url,
            on_message=self._on_message,
            on_disconnect=self._on_disconnect,
        )
        await self.transport.connect()

        self._running = True
        self._event_task = asyncio.create_task(self._event_dispatch_loop())

        logger.debug("AsyncBiDiDriverBridge 已启动: %s", ws_url)

    async def stop(self):
        """关闭异步连接和事件分发"""
        self._running = False

        # 停止事件分发
        if self._event_queue:
            await self._event_queue.put(None)
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

        # 唤醒所有等待中的命令
        self.dispatcher.wake_all()

        # 关闭传输层
        if self.transport:
            await self.transport.disconnect()
            self.transport = None

        logger.debug("AsyncBiDiDriverBridge 已停止")

    async def run(self, method, params=None, timeout=None):
        """异步发送 BiDi 命令并等待响应

        Args:
            method: BiDi 方法名
            params: 参数字典
            timeout: 超时（秒）

        Returns:
            响应的 result 字典
        """
        if timeout is None:
            from .._functions.settings import Settings

            timeout = Settings.bidi_timeout

        return await self.dispatcher.dispatch(
            self.transport, method, params, timeout
        )

    # ── 事件处理 ──────────────────────────────────────────────────────────

    def set_callback(self, event, callback, context=None, immediate=False):
        """注册事件回调（同步方法，可从任何线程调用）"""
        key = (event, context)
        target = self._immediate_handlers if immediate else self._event_handlers
        if callback is None:
            target.pop(key, None)
        else:
            target[key] = callback

    def remove_callback(self, event, context=None, immediate=False):
        """移除事件回调"""
        self.set_callback(event, None, context, immediate)

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _on_message(self, raw):
        """收到 WebSocket 消息时的回调（由 recv_loop 调用，同一事件循环）"""
        msg = json.loads(raw)

        # 命令响应
        if "id" in msg and msg["id"] is not None:
            self.dispatcher.on_response(msg)
            return

        # 事件消息
        msg_type = msg.get("type")
        has_method = "method" in msg

        if msg_type == "event" or has_method:
            event_method = msg.get("method", "")
            event_params = msg.get("params", {})
            event_context = event_params.get("context")

            logger.debug(
                "异步事件 <- %s (context=%s)", event_method, event_context
            )

            # 更新 alert_flag（与同步版一致）
            if self._host_driver:
                if event_method == "browsingContext.userPromptOpened":
                    self._host_driver.alert_flag = True
                elif event_method == "browsingContext.userPromptClosed":
                    self._host_driver.alert_flag = False

            # immediate 回调：立即在当前事件循环中创建 task
            for (evt, ctx), cb in list(self._immediate_handlers.items()):
                if evt == event_method and (ctx is None or ctx == event_context):
                    asyncio.create_task(
                        self._safe_call(cb, event_params, event_method)
                    )

            # 普通回调：入队
            try:
                self._event_queue.put_nowait(
                    (event_method, event_context, event_params)
                )
            except Exception:
                pass

    async def _on_disconnect(self):
        """连接断开时的回调"""
        self.dispatcher.wake_all()
        logger.warning("AsyncBiDiDriverBridge 连接断开")

    async def _event_dispatch_loop(self):
        """异步事件分发循环 —— 对标 _handle_event_loop"""
        while self._running:
            try:
                item = await self._event_queue.get()
            except asyncio.CancelledError:
                break

            if item is None:
                break

            event_method, event_context, event_params = item

            for (evt, ctx), cb in list(self._event_handlers.items()):
                if evt == event_method and (
                    ctx is None or ctx == event_context
                ):
                    await self._safe_call(cb, event_params, event_method)

        logger.debug("异步事件分发循环退出")

    async def _safe_call(self, cb, params, event_name):
        """安全执行回调，支持 sync 和 async callable"""
        try:
            result = cb(params)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error("异步事件回调异常 %s: %s", event_name, e)
