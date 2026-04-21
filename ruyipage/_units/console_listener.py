# -*- coding: utf-8 -*-
"""ConsoleListener - 控制台日志监听器

通过 BiDi log.entryAdded 事件捕获 console 输出。
"""

import time
import threading
from queue import Queue, Empty

from .._bidi import session as bidi_session
from .._bidi.log import LogEntry
from .._functions.queue_utils import queue_get as _queue_get

import logging

logger = logging.getLogger("ruyipage")


class ConsoleListener(object):
    """控制台日志监听器

    用法::

        page.console.start()
        page.get('https://example.com')
        entries = page.console.get()
        page.console.stop()

        # 或等待特定日志
        entry = page.console.wait('error', timeout=5)
    """

    def __init__(self, owner):
        self._owner = owner
        self._listening = False
        self._queue = Queue()
        self._entries = []
        self._subscription_id = None
        self._level_filter = None

    @property
    def listening(self):
        return self._listening

    @property
    def entries(self):
        """已捕获的所有日志条目"""
        self._drain()
        return self._entries[:]

    def start(self, level=None):
        """开始监听

        Args:
            level: 过滤级别 'debug'/'info'/'warn'/'error'，None 监听全部
        """
        if self._listening:
            self.stop()

        self._level_filter = level
        self._queue = Queue()
        self._entries = []

        try:
            result = bidi_session.subscribe(
                self._owner._driver._browser_driver,
                ["log.entryAdded"],
                contexts=[self._owner._context_id],
            )
            self._subscription_id = result.get("subscription")
        except Exception as e:
            logger.warning("订阅 log.entryAdded 失败: %s", e)
            return self

        # log.entryAdded 是全局事件，context 信息在 source.context。
        # 因此这里使用全局回调，再在 _on_entry 中按当前页面 context 过滤。
        self._owner._driver.set_global_callback("log.entryAdded", self._on_entry)
        self._listening = True
        return self

    def stop(self):
        """停止监听"""
        if not self._listening:
            return
        self._listening = False

        if self._subscription_id:
            try:
                bidi_session.unsubscribe(
                    self._owner._driver._browser_driver,
                    subscription=self._subscription_id,
                )
            except Exception:
                pass
            self._subscription_id = None

        self._owner._driver.set_global_callback("log.entryAdded", None)
        return self

    def wait(self, level=None, text=None, timeout=10):
        """等待特定日志条目

        Args:
            level: 日志级别过滤
            text: 文本包含过滤
            timeout: 超时秒数

        Returns:
            LogEntry 或 None
        """
        end = time.time() + timeout
        while time.time() < end:
            remaining = end - time.time()
            try:
                entry = _queue_get(self._queue, timeout=min(remaining, 0.5))
                if self._match(entry, level, text):
                    return entry
            except Empty:
                continue
        return None

    def get(self, level=None, text=None):
        """获取已捕获的日志（可过滤）

        Args:
            level: 级别过滤
            text: 文本过滤

        Returns:
            LogEntry 列表
        """
        self._drain()
        result = self._entries[:]
        if level:
            result = [e for e in result if e.level == level]
        if text:
            result = [e for e in result if text in (e.text or "")]
        return result

    def clear(self):
        """清空已捕获的日志"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        self._entries.clear()
        return self

    def on_entry(self, callback):
        """注册实时回调，每条日志立即触发

        Args:
            callback: fn(LogEntry)，None 则移除
        """
        self._realtime_cb = callback
        return self

    def _on_entry(self, params):
        """处理 log.entryAdded 事件。

        处理顺序：
        1) 过滤非当前 context 的日志
        2) 过滤 level
        3) 入队 + 持久列表
        4) 触发实时回调（如果已注册）
        """
        ctx = params.get("source", {}).get("context", "")
        if ctx and ctx != self._owner._context_id:
            return
        entry = LogEntry.from_params(params)
        if self._level_filter and entry.level != self._level_filter:
            return
        self._queue.put(entry)
        self._entries.append(entry)
        cb = getattr(self, "_realtime_cb", None)
        if cb:
            try:
                cb(entry)
            except Exception:
                pass

    def _drain(self):
        while not self._queue.empty():
            try:
                e = self._queue.get_nowait()
                if e not in self._entries:
                    self._entries.append(e)
            except Empty:
                break

    def _match(self, entry, level, text):
        if level and entry.level != level:
            return False
        if text and text not in (entry.text or ""):
            return False
        return True
