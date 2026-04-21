# -*- coding: utf-8 -*-
"""BiDi WebSocket 驱动核心

实现同步阻塞式 API 的关键：
- BrowserBiDiDriver: 管理单一 WebSocket 连接，后台线程接收消息
- ContextDriver: 轻量包装器，为每个 tab/frame 注入 context 参数

异步支持（可选）：
  安装 ruyiPage[async] 后，run() 方法在 greenlet 异步上下文中
  自动切换到异步路径（await_ → asyncio.Future），不阻塞事件循环。
  同步路径完全不受影响。
"""

import json
import threading
import logging

from queue import Queue, Empty

from ..errors import BiDiError, PageDisconnectedError

logger = logging.getLogger("ruyipage")

# 异步支持检测（greenlet 未安装时为 False，同步路径零开销）
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


class BrowserBiDiDriver(object):
    """浏览器级 BiDi 驱动

    管理 ws://host:port/session 的 WebSocket 连接。
    所有 tab 共用此连接，通过 context 参数区分。
    """

    _BROWSERS = {}  # {address: BrowserBiDiDriver}
    _lock = threading.Lock()

    def __new__(cls, address):
        with cls._lock:
            if address in cls._BROWSERS:
                return cls._BROWSERS[address]
            instance = super(BrowserBiDiDriver, cls).__new__(cls)
            instance._initialized = False
            cls._BROWSERS[address] = instance
            return instance

    def __init__(self, address):
        if self._initialized:
            return
        self._initialized = True

        self.address = address  # host:port
        self._ws = None
        self._cur_id = 0
        self._id_lock = threading.Lock()
        self._ws_send_lock = threading.Lock()  # WebSocket 发送锁（线程安全）

        # 响应等待: {cmd_id: Queue()}
        self._method_results = {}
        self._results_lock = threading.Lock()

        # 事件处理
        # key=(event_method, context_or_None) -> callback
        self._event_handlers = {}
        self._immediate_event_handlers = {}
        self._event_queue = Queue()
        self._handlers_lock = threading.Lock()

        # 线程
        self._recv_th = None
        self._event_th = None
        self._is_running = False
        self._closing = False

        # 状态
        self.session_id = None
        self.alert_flag = False

        # 异步支持（可选，由 switch_to_async() 设置）
        self._mode = "sync"  # "sync" | "async"

    @property
    def is_running(self):
        return self._is_running

    def start(self, ws_url=None):
        """连接 WebSocket 并启动后台线程

        Args:
            ws_url: 完整的 WebSocket URL，如 ws://localhost:9222/session
                    若为 None 则从 address 构建
        """
        if self._is_running:
            return

        import websocket

        if not hasattr(websocket, "create_connection"):
            raise ImportError(
                "当前导入的 websocket 模块不正确，缺少 create_connection。\n"
                "请卸载错误的 websocket 包并安装 websocket-client：\n"
                "  pip uninstall -y websocket websocket-client\n"
                "  pip install websocket-client"
            )

        if ws_url is None:
            ws_url = "ws://{}/session".format(self.address)

        self._ws = websocket.create_connection(
            ws_url, timeout=30, suppress_origin=True, enable_multithread=True
        )
        # 连接建立后切回阻塞模式，避免页面空闲时 recv() 因 socket timeout
        # 被误判为 WebSocket 已断开。
        self._ws.settimeout(None)

        self._closing = False
        self._is_running = True

        # 启动接收线程
        self._recv_th = threading.Thread(
            target=self._recv_loop, name="ruyipage-recv", daemon=True
        )
        self._recv_th.start()

        # 启动事件处理线程
        self._event_th = threading.Thread(
            target=self._handle_event_loop, name="ruyipage-events", daemon=True
        )
        self._event_th.start()

    def stop(self):
        """关闭连接和线程（公共方法）"""
        self._stop()

        # 清理单例
        with self._lock:
            self._BROWSERS.pop(self.address, None)

        self._initialized = False

    def mark_closing(self):
        """标记当前连接即将被主动关闭，避免记录预期断链日志。"""
        self._closing = True

    def _stop(self):
        """内部停止方法：关闭连接和线程，但不清理单例注册

        用于 reconnect() 等需要重建连接但保留单例引用的场景。
        """
        self._closing = True
        self._is_running = False
        self._mode = "sync"

        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        # 唤醒所有等待中的 Queue
        with self._results_lock:
            for q in self._method_results.values():
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            self._method_results.clear()

        # 唤醒事件线程
        self._event_queue.put(None)

    def reconnect(self, ws_url=None):
        """重新连接 WebSocket

        先关闭现有连接（不清理单例），再重新建立连接。

        Args:
            ws_url: 完整的 WebSocket URL，若为 None 则从 address 构建
        """
        self._stop()

        # 重置状态以便重新 start
        self._cur_id = 0
        self._event_handlers.clear()
        self._immediate_event_handlers.clear()

        # 清空事件队列（原子替换，线程安全）
        from queue import Queue

        self._event_queue = Queue()

        self.alert_flag = False

        self.start(ws_url)

    async def switch_to_async(self):
        """将驱动标记为异步模式

        不关闭现有的同步 WebSocket 连接——session 和事件订阅都绑定在上面。
        异步模式下，driver.run() 在 greenlet 内检测到异步上下文后，
        会通过 await_() 挂起 greenlet 而非阻塞线程。

        核心机制：
        - 同步 recv_loop 线程继续运行，接收消息并将命令响应放入 Queue
        - driver.run() 在 greenlet 内通过 await_(queue_get_async()) 等待响应
        - queue_get_async 在 executor 中运行，不阻塞事件循环
        - 事件回调由同步 event_loop 线程照常分发
        """
        if self._mode == "async":
            return
        self._mode = "async"
        logger.debug("BrowserBiDiDriver 已切换到异步模式: %s", self.address)

    def run(self, method, params=None, timeout=None):
        """发送 BiDi 命令并等待响应

        同步上下文：阻塞等待（Queue.get），使用 websocket-client。
        异步上下文（greenlet 内）：通过 await_ 走 asyncio.Future，
        使用 websockets 库，不阻塞事件循环。

        Args:
            method: BiDi 方法名，如 'browsingContext.navigate'
            params: 参数字典
            timeout: 超时时间(秒)，None 使用默认值

        Returns:
            响应的 result 字典

        Raises:
            BiDiError: BiDi 协议错误
            PageDisconnectedError: 连接断开
        """
        if not self._is_running:
            raise PageDisconnectedError("WebSocket 连接未建立")

        if timeout is None:
            from .._functions.settings import Settings

            timeout = Settings.bidi_timeout

        # ── 异步路径（greenlet 内，走 asyncio）──
        if (
            _HAS_ASYNC
            and self._mode == "async"
            and _in_async_greenlet()
        ):
            return self._run_in_greenlet(method, params, timeout)

        # ── 同步路径（原有逻辑，一字不改）──

        # 生成唯一 ID
        with self._id_lock:
            self._cur_id += 1
            cmd_id = self._cur_id

        # 创建响应队列
        response_queue = Queue()
        with self._results_lock:
            self._method_results[cmd_id] = response_queue

        # 构建并发送消息
        msg = {"id": cmd_id, "method": method, "params": params or {}}
        try:
            with self._ws_send_lock:
                self._ws.send(json.dumps(msg))
            logger.debug("发送 -> %d %s", cmd_id, method)
        except Exception as e:
            with self._results_lock:
                self._method_results.pop(cmd_id, None)
            raise PageDisconnectedError("发送消息失败: {}".format(e))

        # 阻塞等待响应
        try:
            result = response_queue.get(timeout=timeout)
        except Empty:
            with self._results_lock:
                self._method_results.pop(cmd_id, None)
            raise BiDiError("timeout", "命令超时: {} ({}s)".format(method, timeout))

        if result is None:
            raise PageDisconnectedError("连接已断开")

        with self._results_lock:
            self._method_results.pop(cmd_id, None)

        # 处理错误响应
        if result.get("type") == "error":
            raise BiDiError(
                result.get("error", "unknown error"),
                result.get("message", ""),
                result.get("stacktrace", ""),
            )

        return result.get("result", {})

    def set_callback(self, event, callback, context=None, immediate=False):
        """注册事件回调

        Args:
            event: 事件方法名，如 'network.responseCompleted'
            callback: 回调函数，接收 params 字典
            context: 可选，限定只接收特定 context 的事件
            immediate: 是否在短生命周期线程中立即执行（用于关键事件如 alert）
        """
        key = (event, context)
        with self._handlers_lock:
            handlers = (
                self._immediate_event_handlers if immediate else self._event_handlers
            )
            if callback is None:
                handlers.pop(key, None)
            else:
                handlers[key] = callback

        # 异步模式下同步路由到异步桥

    def remove_callback(self, event, context=None, immediate=False):
        """移除事件回调"""
        self.set_callback(event, None, context, immediate)

    def _run_in_greenlet(self, method, params, timeout):
        """在 greenlet 异步上下文中发送命令并等待响应

        复用现有的同步 WebSocket 连接和 recv_loop 线程。
        命令通过同步 ws.send() 发出（在 executor 中执行以避免阻塞事件循环），
        响应由 recv_loop 放入 Queue，通过 await_(queue_get_async) 异步等待。

        这样不需要新建 WebSocket 连接，session 和事件订阅都保持有效。
        """
        import asyncio

        # 生成唯一 ID（锁在 executor 中持有，不阻塞事件循环 — 极短操作）
        with self._id_lock:
            self._cur_id += 1
            cmd_id = self._cur_id

        # 创建响应队列
        response_queue = Queue()
        with self._results_lock:
            self._method_results[cmd_id] = response_queue

        # 构建并发送消息 — 通过 executor 执行同步 ws.send()
        msg = {"id": cmd_id, "method": method, "params": params or {}}
        try:
            raw = json.dumps(msg)

            async def _send():
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._sync_send, raw)

            _await_(_send())
            logger.debug("发送(异步) -> %d %s", cmd_id, method)
        except Exception as e:
            with self._results_lock:
                self._method_results.pop(cmd_id, None)
            raise PageDisconnectedError("发送消息失败: {}".format(e))

        # 异步等待响应 — 通过 executor 运行 Queue.get() 避免阻塞事件循环
        async def _wait():
            loop = asyncio.get_running_loop()
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(
                        None, lambda: response_queue.get(timeout=timeout)
                    ),
                    timeout=timeout + 1,  # 外层 timeout 比内层宽松，优先让 Queue.get 超时
                )
            except asyncio.TimeoutError:
                raise Empty()

        try:
            result = _await_(_wait())
        except Empty:
            with self._results_lock:
                self._method_results.pop(cmd_id, None)
            raise BiDiError("timeout", "命令超时: {} ({}s)".format(method, timeout))

        if result is None:
            raise PageDisconnectedError("连接已断开")

        with self._results_lock:
            self._method_results.pop(cmd_id, None)

        # 处理错误响应
        if result.get("type") == "error":
            raise BiDiError(
                result.get("error", "unknown error"),
                result.get("message", ""),
                result.get("stacktrace", ""),
            )

        return result.get("result", {})

    def _sync_send(self, raw):
        """线程安全的同步发送（供 executor 调用）"""
        with self._ws_send_lock:
            self._ws.send(raw)

    def _recv_loop(self):
        """后台线程：接收所有 WebSocket 消息并分发

        处理三种消息类型：
        1. 命令响应 - 有 'id' 字段，放入对应的等待队列
        2. 事件消息 - 有 'type'='event' 或有 'method' 字段（Firefox 有时不带 type 字段）
        3. 未知消息 - 记录日志并忽略
        """
        while self._is_running:
            try:
                raw = self._ws.recv()
                if not raw:
                    continue

                msg = json.loads(raw)

                # 命令响应
                if "id" in msg and msg["id"] is not None:
                    cmd_id = msg["id"]
                    with self._results_lock:
                        q = self._method_results.get(cmd_id)
                    if q:
                        q.put(msg)
                    else:
                        logger.debug("收到未知ID的响应: %d", cmd_id)
                    continue

                # 事件消息 - 处理 type='event' 或无 type 但有 method 的情况
                # Firefox 有时发送不带 'type' 字段的原始事件
                msg_type = msg.get("type")
                has_method = "method" in msg

                if msg_type == "event" or has_method:
                    event_method = msg.get("method", "")
                    event_params = msg.get("params", {})
                    event_context = event_params.get("context")

                    logger.debug(
                        "事件 <- %s (context=%s, type=%s)",
                        event_method,
                        event_context,
                        msg_type,
                    )

                    # alert_flag 处理
                    if event_method == "browsingContext.userPromptOpened":
                        self.alert_flag = True
                    elif event_method == "browsingContext.userPromptClosed":
                        self.alert_flag = False

                    # 处理 immediate 回调（在短生命周期线程中执行，避免阻塞 recv 循环）
                    with self._handlers_lock:
                        handlers_to_check = list(self._immediate_event_handlers.items())

                    for key, handler in handlers_to_check:
                        evt, ctx = key
                        if evt == event_method and (
                            ctx is None or ctx == event_context
                        ):
                            self._handle_immediate_event(handler, event_params)

                    # 放入事件队列（由事件线程处理）
                    self._event_queue.put((event_method, event_context, event_params))
                else:
                    # 未知消息类型
                    if msg_type is not None:
                        logger.debug("忽略未知消息类型: %s", msg_type)

            except Exception as e:
                if self._is_running and not self._closing:
                    logger.warning("WebSocket 接收错误: %s", e)
                self._is_running = False
                # 唤醒所有等待中的命令
                with self._results_lock:
                    for q in self._method_results.values():
                        try:
                            q.put_nowait(None)
                        except Exception:
                            pass
                break

    def _handle_immediate_event(self, handler, event_params):
        """在短生命周期线程中执行 immediate 回调

        避免在 recv 循环中直接执行可能耗时的回调导致消息积压。
        每个回调启动一个独立短线程执行。

        Args:
            handler: 回调函数
            event_params: 事件参数字典
        """

        def _run():
            try:
                handler(event_params)
            except Exception as e:
                logger.error("Immediate 事件处理错误: %s", e)

        t = threading.Thread(target=_run, name="ruyipage-immediate-evt", daemon=True)
        t.start()

    def _handle_event_loop(self):
        """后台线程：处理事件队列"""
        while self._is_running:
            try:
                item = self._event_queue.get(timeout=1)
            except Empty:
                continue

            if item is None:
                break

            event_method, event_context, event_params = item

            with self._handlers_lock:
                handlers_to_check = list(self._event_handlers.items())

            for key, handler in handlers_to_check:
                evt, ctx = key
                if evt == event_method and (ctx is None or ctx == event_context):
                    try:
                        handler(event_params)
                    except Exception as e:
                        logger.error("事件处理错误 %s: %s", event_method, e)


class ContextDriver(object):
    """上下文级驱动包装器

    为特定 tab/frame 的 BiDi 命令自动注入 context 参数。
    """

    def __init__(self, browser_driver, context_id):
        """
        Args:
            browser_driver: BrowserBiDiDriver 实例
            context_id: browsingContext ID
        """
        self._browser_driver = browser_driver
        self.context_id = context_id

    @property
    def is_running(self):
        return self._browser_driver.is_running

    @property
    def alert_flag(self):
        """获取浏览器级 alert 状态"""
        return self._browser_driver.alert_flag

    def run(self, method, params=None, timeout=None):
        """发送命令，自动注入 context 参数

        以下 BiDi 方法需要 context 参数：
        - browsingContext.* (大部分)
        - script.evaluate / script.callFunction
        - input.performActions / input.releaseActions / input.setFiles
        - emulation.*

        以下需要 partition.context:
        - storage.getCookies / storage.setCookie / storage.deleteCookies

        以下不需要 context（浏览器级）：
        - session.*, browser.*, script.addPreloadScript, script.removePreloadScript
        """
        if params is None:
            params = {}

        # 需要注入 context 的方法前缀
        needs_context = (
            "browsingContext.",
            "input.",
            "emulation.",
        )
        needs_target_context = ("script.evaluate", "script.callFunction")
        needs_partition_context = (
            "storage.getCookies",
            "storage.setCookie",
            "storage.deleteCookies",
        )

        if method.startswith(needs_context) and "context" not in params:
            params["context"] = self.context_id
        elif method in needs_target_context:
            if "target" not in params:
                params["target"] = {"context": self.context_id}
            elif "context" not in params.get("target", {}):
                params.setdefault("target", {})["context"] = self.context_id
        elif method in needs_partition_context:
            # storage 方法需要 partition.context 来限定上下文
            if "partition" not in params:
                params["partition"] = {"type": "context", "context": self.context_id}
            elif "context" not in params.get("partition", {}):
                params.setdefault("partition", {}).update(
                    {"type": "context", "context": self.context_id}
                )

        return self._browser_driver.run(method, params, timeout)

    def set_callback(self, event, callback, immediate=False):
        """注册限定于当前 context 的事件回调"""
        self._browser_driver.set_callback(event, callback, self.context_id, immediate)

    def set_global_callback(self, event, callback, immediate=False):
        """注册全局事件回调（不限 context）"""
        self._browser_driver.set_callback(event, callback, None, immediate)

    def remove_global_callback(self, event, immediate=False):
        """移除全局事件回调（不限 context）。"""
        self._browser_driver.remove_callback(event, None, immediate)

    def remove_callback(self, event, immediate=False):
        """移除当前 context 的事件回调"""
        self._browser_driver.remove_callback(event, self.context_id, immediate)
