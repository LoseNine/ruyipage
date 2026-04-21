# -*- coding: utf-8 -*-
"""异步 WebSocket 传输层 —— 对标 _base/transport.py

使用 ``websockets`` 库实现原生异步 WebSocket 收发。
职责：
- 异步建立/关闭 WebSocket 连接
- 异步发送 JSON 消息
- 后台 asyncio Task 持续接收消息，回调 on_message(raw_str)
- 连接断开时回调 on_disconnect()
"""

import asyncio
import json
import logging

logger = logging.getLogger("ruyipage")


class AsyncBiDiTransport:
    """异步 WebSocket 传输层

    只负责字节收发，不解析 BiDi 语义。
    """

    def __init__(self, ws_url, on_message, on_disconnect=None):
        """
        Args:
            ws_url: WebSocket URL，如 ws://127.0.0.1:9222/session
            on_message: 收到消息时的回调 on_message(raw: str)
                       可以是 sync 或 async callable
            on_disconnect: 连接断开时的回调（无参数），
                          可以是 sync 或 async callable
        """
        self._url = ws_url
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._ws = None
        self._recv_task = None
        self._closed = False

    # ── 连接管理 ──────────────────────────────────────────────────────────

    async def connect(self, timeout=30):
        """建立 WebSocket 连接并启动接收 Task

        Args:
            timeout: 连接超时（秒）

        Raises:
            ImportError: websockets 未安装
            ConnectionError: 连接失败
        """
        if self._ws is not None:
            return

        try:
            import websockets
        except ImportError:
            raise ImportError(
                "ruyipage 异步模式需要 websockets 库。\n"
                "请安装: pip install ruyiPage[async]"
            )

        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self._url,
                    max_size=64 * 1024 * 1024,  # 64MB，与同步版对齐
                    ping_interval=None,  # 不主动 ping，与同步版一致
                    close_timeout=5,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(
                "AsyncBiDiTransport 连接超时 ({}s): {}".format(timeout, self._url)
            )
        except Exception as e:
            raise ConnectionError(
                "AsyncBiDiTransport 连接失败 {}: {}".format(self._url, e)
            ) from e

        self._closed = False
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.debug("AsyncBiDiTransport 已连接: %s", self._url)

    async def disconnect(self):
        """关闭连接"""
        if self._closed:
            return
        self._closed = True

        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        logger.debug("AsyncBiDiTransport 已断开")

    @property
    def is_connected(self):
        """连接是否正常"""
        if self._closed or self._ws is None:
            return False
        try:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except (ImportError, AttributeError):
            # 兼容旧版 websockets
            return getattr(self._ws, "open", False)

    # ── 发送 ──────────────────────────────────────────────────────────────

    async def send(self, msg):
        """发送 BiDi 消息

        Args:
            msg: 消息字典，将被序列化为 JSON

        Raises:
            ConnectionError: 连接未建立或已断开
        """
        if not self.is_connected:
            raise ConnectionError("AsyncBiDiTransport 未连接，无法发送消息")
        raw = json.dumps(msg, ensure_ascii=False)
        await self._ws.send(raw)
        logger.debug(
            "AsyncTransport 发送 -> id=%s method=%s",
            msg.get("id"),
            msg.get("method"),
        )

    # ── 接收循环 ──────────────────────────────────────────────────────────

    async def _recv_loop(self):
        """后台接收 Task：持续读取 WebSocket 消息"""
        try:
            async for raw in self._ws:
                if not raw:
                    continue
                logger.debug("AsyncTransport 收到 %d 字节", len(raw))
                try:
                    result = self._on_message(raw)
                    # 支持 async on_message
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error("on_message 回调异常: %s", e)
        except asyncio.CancelledError:
            # 正常取消（disconnect 触发）
            raise
        except Exception as e:
            if not self._closed:
                logger.warning("AsyncBiDiTransport 接收错误: %s", e)
        finally:
            if not self._closed and self._on_disconnect:
                try:
                    result = self._on_disconnect()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as de:
                    logger.error("on_disconnect 回调异常: %s", de)
        logger.debug("AsyncBiDiTransport 接收 Task 退出")
