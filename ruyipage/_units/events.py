# -*- coding: utf-8 -*-
"""EventTracker - 通用 BiDi 事件跟踪器。"""

import time
from queue import Empty, Queue

from .._bidi import session as bidi_session
from .._functions.queue_utils import queue_get as _queue_get

import logging

logger = logging.getLogger('ruyipage')


class BidiEvent(object):
    """单个 BiDi 事件对象。

    Args:
        method: 事件名。
        params: 事件参数字典。

    Returns:
        BidiEvent: 支持属性访问的通用事件对象。

    适用场景：
        - 统一承接 browsingContext / network / script 等模块事件
        - 让编辑器可以对事件字段做跳转和补全

    常用网络事件字段：
        - ``request``: ``network.beforeRequestSent`` 里的请求对象
        - ``response``: ``network.responseStarted/responseCompleted`` 里的响应对象
        - ``error_text``: ``network.fetchError`` 的错误文本
        - ``auth_challenge``: ``network.authRequired`` 的认证挑战信息
    """

    def __init__(self, method, params):
        self.method = method
        self.params = dict(params or {})
        self.context = self.params.get("context")
        self.navigation = self.params.get("navigation")
        self.timestamp = self.params.get("timestamp")
        self.url = self.params.get("url")
        self.request = self.params.get("request")
        self.response = self.params.get("response")
        self.is_blocked = self.params.get("isBlocked")
        self.error_text = self.params.get("errorText")
        self.auth_challenge = self.params.get("authChallenge")
        self.realm = self.params.get("realm")
        self.source = self.params.get("source")
        self.channel = self.params.get("channel")
        self.data = self.params.get("data")
        self.multiple = self.params.get("multiple")
        self.user_prompt_type = self.params.get("type")
        self.accepted = self.params.get("accepted")
        self.message = self.params.get("message")


class EventTracker(object):
    """通用事件跟踪器。

    用法::

        page.events.start(['browsingContext.contextCreated'])
        event = page.events.wait('browsingContext.contextCreated', timeout=3)
        page.events.stop()

    说明：
        - 这是面向 page 的统一事件监听入口。
        - 用它可以避免示例直接碰 ``session.subscribe`` 和 ``page._driver``。
    """

    def __init__(self, owner):
        self._owner = owner
        self._events = []
        self._entries = []
        self._queue = Queue()
        self._subscription_id = None
        self._listening = False

    @property
    def entries(self):
        """已捕获事件列表。"""
        return self._entries[:]

    @property
    def listening(self):
        """当前是否正在监听。"""
        return self._listening

    def start(self, events, contexts=None):
        """开始监听指定事件。

        Args:
            events: 事件名列表。
                常见值：``['browsingContext.contextCreated']``、
                ``['browsingContext.userPromptOpened', 'browsingContext.userPromptClosed']``。
            contexts: 可选的 browsingContext ID 列表。
                传 ``None`` 时默认监听当前页面 context。

        Returns:
            bool: ``True`` 表示订阅成功，``False`` 表示订阅失败。

        适用场景：
            - 示例中验证标准事件是否真实触发
            - 调试某次操作到底触发了哪些 BiDi 事件
        """
        if self._listening:
            self.stop()

        self.clear()
        self._events = list(events or [])
        if not self._events:
            return False

        ctxs = contexts if contexts is not None else [self._owner.tab_id]

        try:
            result = bidi_session.subscribe(
                self._owner._driver._browser_driver,
                self._events,
                contexts=ctxs,
            )
            self._subscription_id = result.get("subscription")
        except Exception as e:
            logger.debug("订阅事件失败: %s", e)
            self._subscription_id = None
            self._events = []
            self._listening = False
            return False

        for event in self._events:
            self._owner._driver.set_global_callback(event, self._make_handler(event))

        self._listening = True
        return True

    def stop(self):
        """停止监听并清理。"""
        for event in self._events:
            try:
                self._owner._driver.remove_callback(event)
            except Exception:
                pass

        if self._subscription_id:
            try:
                bidi_session.unsubscribe(
                    self._owner._driver._browser_driver,
                    subscription=self._subscription_id,
                )
            except Exception:
                pass

        self._subscription_id = None
        self._events = []
        self._listening = False
        return self._owner

    def clear(self):
        """清空已捕获事件。"""
        self._entries = []
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        return self._owner

    def wait(self, event=None, timeout=5):
        """等待一个指定事件。

        Args:
            event: 目标事件名。
                传 ``None`` 表示接受任意已监听事件。
            timeout: 最大等待时间。
                单位：秒。

        Returns:
            BidiEvent | None: 匹配到时返回事件对象，超时返回 ``None``。
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining = end_time - time.time()
            try:
                item = _queue_get(self._queue, timeout=min(remaining, 0.2))
            except Empty:
                continue
            if event is None or item.method == event:
                return item
        return None

    def _make_handler(self, event):
        def _handler(params):
            item = BidiEvent(event, params)
            self._entries.append(item)
            self._queue.put(item)

        return _handler
