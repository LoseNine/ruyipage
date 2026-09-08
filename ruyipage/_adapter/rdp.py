# -*- coding: utf-8 -*-
"""Firefox DevTools 远程调试协议（RDP）传输层

RDP 是 Firefox 私有协议，提供 WebDriver BiDi 没有的能力：断点、暂停、单步、
调用栈与作用域检查。BiDi 规范里没有 debugger 模块，CDP 又已在 Firefox 141
被彻底移除，因此断点级调试只能走这条通道。

与 BiDi 的关系：两者可以同时连接、互不干扰。这一点和 Marionette 不同——
Marionette 的 newSession 会踢掉 BiDi 连接（见 _adapter/marionette.py）。

协议要点：
- 帧格式 ``<字节长度>:<UTF-8 JSON>``
- 请求 ``{"to": actor, "type": method, ...}``，应答 ``{"from": actor, ...}``
- **没有请求 id**，靠同一 actor 的先进先出顺序配对，因此必须能区分
  「应答」和「服务端主动推送的事件」

稳定性提示：RDP 没有跨版本兼容承诺，升级 Firefox 大版本时可能需要跟进调整。
"""

import json
import logging
import socket
import threading
from queue import Empty, Queue

from ..errors import DebuggerError

logger = logging.getLogger("ruyipage")

DEFAULT_RDP_PORT = 6000

# 服务端主动推送的事件类型。
#
# RDP 的应答和事件都只有 ``from`` 字段，无法靠结构区分，而某些应答本身就带
# ``type``（例如 environment form 带 ``"type": "function"``），所以不能用
# 「是否含 type」来判断。这里维护事件白名单，其余一律视为应答。
EVENT_TYPES = frozenset(
    [
        # thread actor
        "paused",
        "resumed",
        "newSource",
        # root actor
        "tabListChanged",
        "addonListChanged",
        "workerListChanged",
        "serviceWorkerRegistrationListChanged",
        "processListChanged",
        # console actor（日志断点的输出走这里）
        "consoleAPICall",
        # watcher actor
        "target-available-form",
        "target-destroyed-form",
        "resources-available-array",
        "resources-updated-array",
        "resources-destroyed-array",
        # target actor
        "frameUpdate",
        "tabNavigated",
        "willNavigate",
        "navigate",
        "documentEvent",
        # console actor
        "consoleAPICall",
        "pageError",
        "networkEvent",
        "networkEventUpdate",
        "evaluationResult",
    ]
)

_HEADER_MAX = 200


class RdpConnection(object):
    """最小化 RDP 客户端。

    只实现断点调试所需的部分：请求/应答配对、事件分发。
    """

    def __init__(self, host="127.0.0.1", port=DEFAULT_RDP_PORT, timeout=20):
        self.host = host
        self.port = int(port)
        self.timeout = timeout

        self._sock = None
        self._buf = b""
        self._running = False
        self._reader = None

        self._lock = threading.Lock()
        self._replies = {}  # {actor: Queue}
        self._event_handlers = {}  # {(actor_or_None, event_type): [callback]}
        self.greeting = None

    # ── 连接管理 ──

    def connect(self):
        """建立连接并读取 root actor 的问候包。"""
        self._sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        # 收包由读线程负责，避免 recv 超时打断长时间等待暂停事件。
        self._sock.settimeout(None)
        self._running = True
        self._reader = threading.Thread(
            target=self._read_loop, name="ruyipage-rdp", daemon=True
        )
        self._reader.start()

        self.greeting = self._take_reply("root", timeout=self.timeout)
        if self.greeting is None:
            self.close()
            raise DebuggerError(
                "已连接 {}:{} 但未收到 RDP 问候包".format(self.host, self.port)
            )
        return self

    def close(self):
        self._running = False
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        # 唤醒所有等待应答的调用方
        with self._lock:
            queues = list(self._replies.values())
            self._replies.clear()
        for queue in queues:
            try:
                queue.put_nowait(None)
            except Exception:
                pass

    @property
    def connected(self):
        return bool(self._running and self._sock is not None)

    # ── 收发 ──

    def _read_loop(self):
        while self._running:
            try:
                chunk = self._sock.recv(65536)
            except Exception:
                break
            if not chunk:
                break
            self._buf += chunk
            self._drain_buffer()

        self._running = False
        with self._lock:
            queues = list(self._replies.values())
        for queue in queues:
            try:
                queue.put_nowait(None)
            except Exception:
                pass

    def _drain_buffer(self):
        while True:
            head, sep, rest = self._buf.partition(b":")
            if not sep:
                if len(self._buf) > _HEADER_MAX:
                    logger.warning("RDP 帧头异常，丢弃缓冲")
                    self._buf = b""
                return
            if not head.isdigit():
                logger.warning("RDP 帧头非数字，丢弃缓冲")
                self._buf = b""
                return
            length = int(head)
            if len(rest) < length:
                return
            body, self._buf = rest[:length], rest[length:]
            try:
                packet = json.loads(body.decode("utf-8"))
            except Exception as exc:
                logger.warning("RDP 包解析失败: %s", exc)
                continue
            self._dispatch(packet)

    def _dispatch(self, packet):
        actor = packet.get("from")
        event_type = packet.get("type")

        if event_type in EVENT_TYPES:
            for key in ((actor, event_type), (None, event_type)):
                for callback in self._handlers_for(key):
                    try:
                        callback(packet)
                    except Exception as exc:
                        logger.warning("RDP 事件回调异常 %s: %s", event_type, exc)
            return

        self._reply_queue(actor).put(packet)

    def _handlers_for(self, key):
        with self._lock:
            return list(self._event_handlers.get(key, ()))

    def _reply_queue(self, actor):
        with self._lock:
            queue = self._replies.get(actor)
            if queue is None:
                queue = Queue()
                self._replies[actor] = queue
            return queue

    def _take_reply(self, actor, timeout):
        queue = self._reply_queue(actor)
        try:
            return queue.get(timeout=timeout)
        except Empty:
            return None

    def send(self, packet):
        if not self.connected:
            raise DebuggerError("RDP 连接已关闭")
        body = json.dumps(packet).encode("utf-8")
        frame = str(len(body)).encode("ascii") + b":" + body
        self._sock.sendall(frame)

    def request(self, actor, type_, timeout=None, **params):
        """发送请求并等待该 actor 的应答。

        Raises:
            DebuggerError: 连接断开、超时或服务端返回错误
        """
        timeout = self.timeout if timeout is None else timeout
        payload = {"to": actor, "type": type_}
        payload.update(params)

        # 先建好应答队列再发送，避免应答比等待更早到达而丢失。
        self._reply_queue(actor)
        self.send(payload)

        reply = self._take_reply(actor, timeout)
        if reply is None:
            if not self.connected:
                raise DebuggerError("RDP 连接在等待 {} 应答时断开".format(type_))
            raise DebuggerError("RDP 请求超时: {} -> {}".format(actor, type_))
        if "error" in reply:
            raise DebuggerError(
                "RDP 错误 {}: {} {}".format(
                    type_, reply.get("error"), reply.get("message", "")
                )
            )
        return reply

    # ── 事件 ──

    def on_event(self, event_type, callback, actor=None):
        """注册事件回调；``callback=None`` 表示移除该 actor 上的全部回调。"""
        key = (actor, event_type)
        with self._lock:
            if callback is None:
                self._event_handlers.pop(key, None)
            else:
                self._event_handlers.setdefault(key, []).append(callback)
        return self
