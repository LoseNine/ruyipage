# -*- coding: utf-8 -*-
"""BiDi Transport — WebSocket 收发层

职责：
- 建立/关闭 WebSocket 连接
- 线程安全发送原始 JSON 消息
- 后台线程持续接收消息，回调 on_message(raw_str)
- 连接断开时回调 on_disconnect()
"""

import json
import threading
import logging

logger = logging.getLogger("ruyipage")


class BiDiTransport:
    """WebSocket 传输层

    只负责字节收发，不解析 BiDi 语义。
    """

    def __init__(self, ws_url: str, on_message, on_disconnect=None):
        """
        Args:
            ws_url: WebSocket URL，如 ws://127.0.0.1:9222/session
            on_message: 收到消息时的回调 on_message(raw: str)
            on_disconnect: 连接断开时的回调（无参数）
        """
        self._url = ws_url
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._ws = None
        self._send_lock = threading.Lock()
        self._running = False
        self._recv_thread = None

    # ── 连接管理 ──────────────────────────────────────────────────────────

    def connect(self, timeout: float = 30):
        """建立 WebSocket 连接并启动接收线程

        Args:
            timeout: 连接超时（秒）

        Raises:
            ConnectionError: 连接失败
        """
        if self._running:
            return
        try:
            import websocket

            self._ws = websocket.create_connection(
                self._url,
                timeout=timeout,
                suppress_origin=True,
                enable_multithread=True,
            )
            # 连接成功后改为阻塞读取，避免空闲期 socket timeout 被当作断线。
            self._ws.settimeout(None)
        except Exception as e:
            raise ConnectionError(
                "BiDi WebSocket 连接失败 {}: {}".format(self._url, e)
            ) from e

        self._running = True
        self._recv_thread = threading.Thread(
            target=self._recv_loop,
            name="bidi-transport-recv",
            daemon=True,
        )
        self._recv_thread.start()
        logger.debug("BiDiTransport 已连接: %s", self._url)

    def disconnect(self):
        """关闭连接"""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.debug("BiDiTransport 已断开")

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None

    # ── 发送 ──────────────────────────────────────────────────────────────

    def send(self, msg: dict):
        """线程安全发送 BiDi 消息

        Args:
            msg: 消息字典，将被序列化为 JSON

        Raises:
            ConnectionError: 连接未建立或已断开
            RuntimeError: 发送失败
        """
        if not self.is_connected:
            raise ConnectionError("BiDiTransport 未连接，无法发送消息")
        raw = json.dumps(msg, ensure_ascii=False)
        try:
            with self._send_lock:
                self._ws.send(raw)
            logger.debug(
                "Transport 发送 -> id=%s method=%s", msg.get("id"), msg.get("method")
            )
        except Exception as e:
            raise RuntimeError("BiDi 消息发送失败: {}".format(e)) from e

    # ── 接收循环 ──────────────────────────────────────────────────────────

    def _recv_loop(self):
        """后台接收线程：持续读取 WebSocket 消息"""
        while self._running:
            try:
                raw = self._ws.recv()
                if not raw:
                    continue
                logger.debug("Transport 收到 %d 字节", len(raw))
                try:
                    self._on_message(raw)
                except Exception as e:
                    logger.error("on_message 回调异常: %s", e)
            except Exception as e:
                if self._running:
                    logger.warning("BiDiTransport 接收错误（连接断开）: %s", e)
                    self._running = False
                    if self._on_disconnect:
                        try:
                            self._on_disconnect()
                        except Exception as de:
                            logger.error("on_disconnect 回调异常: %s", de)
                break
        logger.debug("BiDiTransport 接收线程退出")
