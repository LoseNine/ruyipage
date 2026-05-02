# -*- coding: utf-8 -*-
"""NavigationTracker - 导航事件跟踪器。"""

import time
from queue import Empty, Queue

from .._bidi import session as bidi_session
from .._functions.queue_utils import queue_get as _queue_get

import logging

logger = logging.getLogger('ruyipage')


class NavigationEvent(object):
    """导航事件快照。

    Args:
        method: BiDi 事件名。
            常见值：``'browsingContext.navigationStarted'``、
            ``'browsingContext.fragmentNavigated'``、
            ``'browsingContext.historyUpdated'``、
            ``'browsingContext.navigationCommitted'``、
            ``'browsingContext.navigationFailed'``。
        params: 事件原始参数字典。

    Returns:
        NavigationEvent: 便于通过属性访问 URL、context、navigation id 的事件对象。

    适用场景：
        - 等待单个导航事件
        - 输出结构化导航事件结果
        - 调试 Firefox 当前版本的导航事件支持差异
    """

    def __init__(self, method, params):
        self.method = method
        self.params = dict(params or {})
        self.context = self.params.get("context")
        self.navigation = self.params.get("navigation")
        self.timestamp = self.params.get("timestamp")
        self.url = self.params.get("url")

    def __repr__(self):
        return "<NavigationEvent {} {}>".format(self.method, (self.url or "")[:80])


class NavigationTracker(object):
    """页面级导航事件跟踪器。

    它不是“执行跳转”的对象，而是“观察跳转过程”的对象。

    一般配合下面这些命令使用：
    - ``page.get(url)``
    - ``page.back()``
    - ``page.forward()``
    - ``page.refresh()``

    常见工作流：
    1. ``page.navigation.start()`` 开始监听
    2. 执行页面跳转
    3. 用 ``wait()`` / ``wait_for_load()`` 等方法等待目标事件
    4. 用 ``entries`` 查看完整事件链
    5. ``page.navigation.stop()`` 清理监听

    用法::

        page.navigation.start()
        page.get('https://example.com')
        event = page.navigation.wait('browsingContext.load', timeout=5)
        page.navigation.stop()
    """

    DEFAULT_EVENTS = [
        "browsingContext.navigationStarted",
        "browsingContext.fragmentNavigated",
        "browsingContext.historyUpdated",
        "browsingContext.domContentLoaded",
        "browsingContext.load",
        "browsingContext.navigationCommitted",
        "browsingContext.navigationFailed",
    ]

    def __init__(self, owner):
        self._owner = owner
        self._listening = False
        self._queue = Queue()
        self._entries = []
        self._subscription_id = None
        self._events = []

    @property
    def listening(self):
        """当前是否已开始导航事件跟踪。

        Returns:
            bool: ``True`` 表示已订阅并注册回调。
        """
        return self._listening

    @property
    def entries(self):
        """已捕获的导航事件列表。

        Returns:
            list[NavigationEvent]: 按接收顺序返回的事件副本。

        适用场景：
            - 示例结束后查看完整导航事件链
            - 排查某个事件为何未按预期到达
        """
        return self._entries[:]

    def start(self, events=None):
        """开始跟踪当前页面的导航事件。

        Args:
            events: 需要订阅的事件列表。
                常见值：``None`` 表示默认导航事件全集；
                或 ``['browsingContext.fragmentNavigated']`` 表示只跟踪片段导航。

        Returns:
            bool: ``True`` 表示订阅成功，``False`` 表示订阅失败。

        适用场景：
            - 页面导航前统一启动事件跟踪
            - 只关注某类导航事件时缩小订阅范围

        说明：
            - 这个方法只负责“开始监听”，不会自动触发任何导航。
            - 如果你已经在监听，重复调用会先停止旧监听，再创建新监听。
        """
        if self._listening:
            self.stop()

        self.clear()
        self._events = list(events or self.DEFAULT_EVENTS)

        try:
            result = bidi_session.subscribe(
                self._owner._driver._browser_driver,
                self._events,
                contexts=[self._owner._context_id],
            )
            self._subscription_id = result.get("subscription")
        except Exception as e:
            logger.debug("订阅导航事件失败: %s", e)
            self._subscription_id = None
            self._events = []
            self._listening = False
            return False

        for event in self._events:
            self._owner._driver.set_callback(event, self._make_handler(event))

        self._listening = True
        return True

    def stop(self):
        """停止跟踪导航事件。

        Returns:
            owner: 原页面对象，便于链式调用。

        适用场景：
            - 示例或测试结束后的统一清理
            - 切换为另一组关注事件前先关闭旧订阅
        """
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
        """清空已记录导航事件。

        Returns:
            owner: 原页面对象。

        适用场景：
            - 开始新一轮导航验证前清空旧结果
            - 避免旧事件干扰本轮 wait 判定
        """
        self._entries = []
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        return self._owner

    def wait(self, event=None, timeout=5, url_contains=None):
        """等待一个匹配条件的导航事件。

        Args:
            event: 目标事件名。
                常见值：``'browsingContext.load'``、
                ``'browsingContext.fragmentNavigated'``、
                ``'browsingContext.historyUpdated'``。
                传 ``None`` 表示接受任意已订阅导航事件。
            timeout: 最大等待时间。
                单位：秒。
                常见值：``2``、``3``、``5``、``10``。
            url_contains: URL 包含过滤字符串。
                常见值：``'#a'``、``'/dashboard'``、``'?p=2'``。

        Returns:
            NavigationEvent | None: 匹配到时返回事件对象，超时返回 ``None``。

        适用场景：
            - 等待页面进入某个标准导航阶段
            - 验证 fragment / history 导航是否落到期望 URL

        说明：
            - 这是事件等待，不是页面状态轮询。
            - 如果浏览器当前版本根本不发某个事件，超时后会返回 ``None``。
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining = end_time - time.time()
            try:
                item = _queue_get(self._queue, timeout=min(remaining, 0.2))
            except Empty:
                continue
            if self._match(item, event=event, url_contains=url_contains):
                return item
        return None

    def wait_for_fragment(self, fragment, timeout=5):
        """等待指定 fragment 的片段导航事件。

        Args:
            fragment: 目标片段字符串。
                常见值：``'a'``、``'section-2'``。
                可传 ``'a'`` 或 ``'#a'``，内部统一转成 ``'#a'``。
            timeout: 最大等待时间。
                单位：秒。

        Returns:
            NavigationEvent | None: 匹配到片段导航事件时返回事件对象，否则返回 ``None``。

        适用场景：
            - 验证 ``fragmentNavigated`` 是否真实触发
            - 测试同文档 hash 导航

        说明：
            - 这个方法依赖浏览器真实发出 ``browsingContext.fragmentNavigated``。
            - 如果 URL 虽然变了，但浏览器没有发标准事件，本方法会返回 ``None``。
        """
        fragment = str(fragment)
        if not fragment.startswith("#"):
            fragment = "#" + fragment
        return self.wait(
            event="browsingContext.fragmentNavigated",
            timeout=timeout,
            url_contains=fragment,
        )

    def wait_for_load(self, timeout=5):
        """等待当前页面的 load 事件。

        Args:
            timeout: 最大等待时间。
                单位：秒。

        Returns:
            NavigationEvent | None: ``load`` 事件对象，超时返回 ``None``。

        适用场景：
            - 页面完全加载后再做断言
            - 与 ``page.get()`` 配合确认标准 ``load`` 已到达

        说明：
            - 这比单纯看 ``document.readyState`` 更适合做 BiDi 事件验证。
        """
        return self.wait(event="browsingContext.load", timeout=timeout)

    def _make_handler(self, event):
        def _handler(params):
            item = NavigationEvent(event, params)
            self._entries.append(item)
            self._queue.put(item)

        return _handler

    @staticmethod
    def _match(item, event=None, url_contains=None):
        if event and item.method != event:
            return False
        if url_contains and url_contains not in str(item.url or ""):
            return False
        return True
