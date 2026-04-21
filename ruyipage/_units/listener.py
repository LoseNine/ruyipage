# -*- coding: utf-8 -*-
"""Listener - 网络事件被动监听器

通过 BiDi network 模块事件 **被动** 监听网络请求/响应，不拦截、不阻塞请求流程。

与 Interceptor 的区别
--------------------
- ``Listener`` 是只读观察者，不能修改/阻止/Mock 请求。
- ``Interceptor`` 会暂停请求直到你调用 continue/fail/mock。

适用场景
--------
- 等待某个 API 响应完成后再继续操作
- 监控页面加载过程中的所有网络请求
- 统计某类请求的响应状态码

快速开始::

    page.listen.start('/api/data')
    page.ele('#load-btn').click()
    packet = page.listen.wait(timeout=10)
    print(packet.url, packet.status)
    page.listen.stop()
"""

import re
import time
import logging
import threading
from queue import Queue, Empty

from .._bidi import session as bidi_session
from .._functions.queue_utils import queue_get as _queue_get

logger = logging.getLogger('ruyipage')


class DataPacket(object):
    """网络数据包 — Listener 捕获的单次网络事件。

    每次 ``page.listen.wait()`` 返回一个 ``DataPacket``，
    包含该次网络请求/响应的基本信息。

    Attributes:
        url (str): 请求 URL，如 ``"https://api.example.com/data"``。
        method (str): 请求方法，如 ``"GET"``、``"POST"``。
        status (int): 响应状态码，如 ``200``、``404``。请求失败时为 ``0``。
        headers (dict): 响应头字典 ``{name: value}``，key 已转小写。
        event_type (str): 事件类型，``"responseCompleted"`` 或 ``"fetchError"``。
        body: 响应体（当前版本始终为 ``None``，需通过 DataCollector 获取）。
        request (dict): BiDi 原始 request 对象。
        response (dict): BiDi 原始 response 对象。
        timestamp (float): 事件时间戳。

    Examples::

        packet = page.listen.wait(timeout=10)
        if packet:
            print(f"URL: {packet.url}")
            print(f"状态码: {packet.status}")
            print(f"Content-Type: {packet.headers.get('content-type')}")
            print(f"是否失败: {packet.is_failed}")
    """

    def __init__(self, request=None, response=None, event_type='',
                 url='', method='', status=0, headers=None, body=None,
                 timestamp=0):
        self.request = request or {}
        self.response = response or {}
        self.event_type = event_type
        self.url = url
        self.method = method
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.timestamp = timestamp

    @property
    def is_failed(self):
        """请求是否失败（fetchError）。

        Returns:
            bool: ``True`` 表示请求因网络错误或被拦截器 fail() 而失败。

        Examples::

            packet = page.listen.wait(timeout=5)
            if packet and packet.is_failed:
                print(f"请求失败: {packet.url}")
        """
        return self.event_type == 'fetchError'

    def __repr__(self):
        return '<DataPacket {} {} {}>'.format(self.method, self.status, self.url[:60])


class Listener(object):
    """网络事件被动监听器。

    通过 ``page.listen`` 访问。被动观察网络事件，不拦截、不阻塞请求。

    快速开始::

        # 监听特定 URL
        page.listen.start('/api/data')
        page.ele('#load-btn').click()
        packet = page.listen.wait(timeout=10)
        print(packet.url, packet.status)
        page.listen.stop()

    URL 匹配规则::

        # 监听所有请求
        page.listen.start()
        page.listen.start(True)

        # 子串匹配（URL 包含该字符串即命中）
        page.listen.start('/api/data')

        # 多个 URL 模式
        page.listen.start(['/api/data', '/api/user'])

        # 正则匹配
        page.listen.start(r'/api/v\\d+/', is_regex=True)

    HTTP 方法过滤::

        # 只监听 POST 请求
        page.listen.start('/api/', method='POST')

    批量等待::

        # 等待 3 个数据包
        packets = page.listen.wait(timeout=10, count=3)
        for p in packets:
            print(p.url, p.status)

    历史记录::

        page.listen.start('/api/')
        # ... 多次操作 ...
        all_packets = page.listen.steps  # 获取所有已捕获的数据包
    """

    def __init__(self, owner):
        self._owner = owner
        self._listening = False
        self._targets = None  # True=全部, set=URL模式匹配
        self._is_regex = False
        self._method_filter = None
        self._caught = Queue()
        self._packets = []
        self._subscription_id = None
        self._subscribed_events = []

    @property
    def listening(self):
        """当前是否正在监听。

        Returns:
            bool: ``True`` 表示监听已启动且未停止。
        """
        return self._listening

    @property
    def steps(self):
        """获取所有已捕获的数据包。

        返回从 ``start()`` 到当前时刻所有命中的数据包列表（副本）。

        Returns:
            list[DataPacket]: 所有已捕获的数据包。

        Examples::

            page.listen.start('/api/')
            page.get("https://example.com")
            page.wait(2)

            for packet in page.listen.steps:
                print(f"[{packet.status}] {packet.method} {packet.url}")
        """
        self._drain_queue()
        return self._packets[:]

    def start(self, targets=True, is_regex=False, method=None):
        """开始监听网络事件。

        Args:
            targets: URL 匹配规则。

                - ``True`` — 监听所有请求（默认）。
                - ``str`` — 子串匹配，URL 包含该字符串即命中。
                  如 ``'/api/data'`` 会匹配 ``https://example.com/api/data?page=1``。
                - ``list[str]`` — 多个 URL 模式，任一命中即可。
                  如 ``['/api/data', '/api/user']``。

                示例::

                    page.listen.start('/api/')          # 子串匹配
                    page.listen.start(['/a', '/b'])     # 多个模式
                    page.listen.start(r'/v\\d+/', is_regex=True)  # 正则

            is_regex: URL 模式是否为正则表达式。默认 ``False``。

                设为 ``True`` 时，``targets`` 中的字符串作为正则表达式匹配
                （使用 ``re.search``）::

                    page.listen.start(r'api/v\\d+/users', is_regex=True)

            method: HTTP 方法过滤。传 ``None`` 不过滤（默认）。

                常见值：``'GET'``、``'POST'``、``'PUT'``、``'DELETE'``。
                大小写不敏感::

                    page.listen.start('/api/', method='POST')

        Examples::

            # 最简单：监听所有请求
            page.listen.start()

            # 监听特定 API 的 POST 请求
            page.listen.start('/api/submit', method='POST')
            page.ele('#submit-btn').click()
            packet = page.listen.wait(timeout=10)
            print(f"提交结果: {packet.status}")
            page.listen.stop()
        """
        if self._listening:
            self.stop()

        self._is_regex = is_regex
        self._method_filter = method.upper() if method else None

        if targets is True:
            self._targets = True
        elif isinstance(targets, str):
            self._targets = {targets}
        elif isinstance(targets, (list, tuple)):
            self._targets = set(targets)
        else:
            self._targets = True

        self._caught = Queue()
        self._packets = []

        # 订阅网络事件
        events = [
            'network.beforeRequestSent',
            'network.responseCompleted',
            'network.fetchError',
        ]

        try:
            result = bidi_session.subscribe(
                self._owner._driver._browser_driver,
                events,
                contexts=[self._owner._context_id]
            )
            self._subscription_id = result.get('subscription')
            self._subscribed_events = events
        except Exception as e:
            logger.warning('订阅网络事件失败: %s', e)
            return

        # 注册回调
        driver = self._owner._driver
        driver.set_callback('network.responseCompleted', self._on_response)
        driver.set_callback('network.fetchError', self._on_fetch_error)

        self._listening = True
        logger.debug('开始监听网络事件')

    def stop(self):
        """停止监听，清理资源。

        可安全重复调用（幂等）。

        Examples::

            page.listen.start('/api/')
            # ...
            page.listen.stop()
        """
        if not self._listening:
            return

        self._listening = False

        # 取消订阅
        if self._subscription_id:
            try:
                bidi_session.unsubscribe(
                    self._owner._driver._browser_driver,
                    subscription=self._subscription_id
                )
            except Exception:
                pass
            self._subscription_id = None

        # 移除回调
        driver = self._owner._driver
        driver.remove_callback('network.responseCompleted')
        driver.remove_callback('network.fetchError')

        logger.debug('停止监听网络事件')

    def wait(self, timeout=None, count=1):
        """等待捕获数据包。

        阻塞当前线程直到捕获指定数量的数据包或超时。

        Args:
            timeout: 最大等待时间（秒）。默认使用 ``Settings.bidi_timeout``。

                示例::

                    packet = page.listen.wait(timeout=10)
                    packet = page.listen.wait(timeout=0.5)  # 快速检查

            count: 期望等待的数据包数量。默认 ``1``。

                - ``count=1`` — 返回单个 ``DataPacket`` 或 ``None``。
                - ``count>1`` — 返回 ``list[DataPacket]``（可能不足 count 个）。

                示例::

                    # 等待 3 个数据包
                    packets = page.listen.wait(timeout=10, count=3)
                    print(f"捕获到 {len(packets)} 个数据包")

        Returns:
            DataPacket / list[DataPacket] / None:

            - ``count=1`` 时：返回 ``DataPacket`` 或 ``None``（超时）。
            - ``count>1`` 时：返回 ``list[DataPacket]``（可能为空列表）。

        Examples::

            # 等待单个响应
            page.listen.start('/api/data')
            page.ele('#load').click()
            packet = page.listen.wait(timeout=10)
            if packet:
                print(f"[{packet.status}] {packet.url}")
            else:
                print("超时未捕获到响应")

            # 等待多个响应
            page.listen.start('/api/')
            page.get("https://example.com")
            packets = page.listen.wait(timeout=10, count=5)
            for p in packets:
                print(f"[{p.status}] {p.url}")
        """
        if timeout is None:
            from .._functions.settings import Settings
            timeout = Settings.bidi_timeout

        end_time = time.time() + timeout
        results = []

        while len(results) < count:
            remaining = end_time - time.time()
            if remaining <= 0:
                break

            try:
                packet = _queue_get(self._caught, timeout=min(remaining, 0.5))
                results.append(packet)
            except Empty:
                continue

        if count == 1:
            return results[0] if results else None
        return results

    def clear(self):
        """清空所有已捕获的数据包。

        同时清空等待队列和历史列表。

        Examples::

            page.listen.start('/api/')
            page.get("https://example.com")
            page.wait(2)

            print(f"已捕获 {len(page.listen.steps)} 个包")
            page.listen.clear()
            print(f"清空后: {len(page.listen.steps)} 个包")  # 0
        """
        while not self._caught.empty():
            try:
                self._caught.get_nowait()
            except Empty:
                break
        self._packets.clear()

    def _on_response(self, params):
        """处理响应完成事件"""
        if not self._listening:
            return

        request = params.get('request', {})
        response = params.get('response', {})
        url = request.get('url', '')
        method = request.get('method', '')

        if not self._match(url, method):
            return

        headers = {}
        for h in response.get('headers', []):
            name = h.get('name', '')
            value_obj = h.get('value', {})
            value = value_obj.get('value', '') if isinstance(value_obj, dict) else str(value_obj)
            headers[name.lower()] = value

        packet = DataPacket(
            request=request,
            response=response,
            event_type='responseCompleted',
            url=url,
            method=method,
            status=response.get('status', 0),
            headers=headers,
            timestamp=params.get('timestamp', 0),
        )

        self._caught.put(packet)
        self._packets.append(packet)

    def _on_fetch_error(self, params):
        """处理请求失败事件"""
        if not self._listening:
            return

        request = params.get('request', {})
        url = request.get('url', '')
        method = request.get('method', '')

        if not self._match(url, method):
            return

        packet = DataPacket(
            request=request,
            event_type='fetchError',
            url=url,
            method=method,
            timestamp=params.get('timestamp', 0),
        )

        self._caught.put(packet)
        self._packets.append(packet)

    def _match(self, url, method):
        """检查 URL 和方法是否匹配"""
        if self._method_filter and method.upper() != self._method_filter:
            return False

        if self._targets is True:
            return True

        for pattern in self._targets:
            if self._is_regex:
                if re.search(pattern, url):
                    return True
            else:
                if pattern in url:
                    return True

        return False

    def _drain_queue(self):
        """将队列中的数据包转移到列表"""
        while not self._caught.empty():
            try:
                packet = self._caught.get_nowait()
                if packet not in self._packets:
                    self._packets.append(packet)
            except Empty:
                break
