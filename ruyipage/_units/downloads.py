# -*- coding: utf-8 -*-
"""DownloadsManager - 下载行为与事件管理器。

职责：
- 通过 ``browser.setDownloadBehavior`` 设置下载策略
- 订阅并缓存 ``browsingContext.downloadWillBegin`` / ``downloadEnd`` 事件
- 提供面向示例和测试的等待接口，便于区分成功、失败、未支持
"""

import os
import time
import threading
from queue import Queue, Empty

from .._bidi import browser_module as bidi_browser
from .._bidi import session as bidi_session
from .._functions.sleep import sleep as _sleep
from .._functions.queue_utils import queue_get as _queue_get


class DownloadEvent(object):
    """下载事件快照。

    Args:
        method: BiDi 事件名。
            常见值：``'browsingContext.downloadWillBegin'``、
            ``'browsingContext.downloadEnd'``。
        params: 事件原始参数字典。

    Returns:
        DownloadEvent: 便于以属性方式访问常用字段的事件对象。

    适用场景：
        - 示例脚本中打印结构化结果
        - 测试代码中等待和断言下载事件链
        - 需要同时保留原始 params 与常用字段时
    """

    def __init__(self, method, params):
        self.method = method
        self.params = dict(params or {})
        self.context = self.params.get("context")
        self.navigation = self.params.get("navigation")
        self.timestamp = self.params.get("timestamp")
        self.url = self.params.get("url")
        self.suggested_filename = (
            self.params.get("suggestedFilename")
            or self.params.get("filename")
            or self.params.get("downloadFileName")
        )
        self.status = self.params.get("status")

    def __repr__(self):
        return "<DownloadEvent {} {} {}>".format(
            self.method,
            self.status or "",
            (self.suggested_filename or self.url or "")[:60],
        )


class DownloadsManager(object):
    """页面级下载管理器。

    用法::

        page.downloads.set_behavior('allow', path='downloads')
        page.downloads.start()
        page.ele('#download').click_self()
        event = page.downloads.wait('browsingContext.downloadEnd', timeout=5)
        page.downloads.stop()

    说明：
        - 下载行为命令是 Firefox 私有扩展，非 W3C 标准命令。
        - 下载事件 ``downloadWillBegin`` / ``downloadEnd`` 是 BiDi 标准事件。
        - 若浏览器策略阻止落盘，事件仍可能成功触发，因此示例需分别报告
          "事件成功" 与 "文件落盘成功"。
    """

    EVENTS = [
        "browsingContext.downloadWillBegin",
        "browsingContext.downloadEnd",
    ]

    def __init__(self, owner):
        self._owner = owner
        self._queue = Queue()
        self._events = []
        self._lock = threading.RLock()
        self._subscription_id = None
        self._listening = False

    @property
    def events(self):
        """返回当前缓存的下载事件列表。

        Returns:
            list[DownloadEvent]: 按接收顺序返回的事件快照副本。

        适用场景：
            - 示例结束后输出完整事件链
            - 调试不同浏览器版本的下载实现差异
        """
        with self._lock:
            return self._events[:]

    @property
    def listening(self):
        """是否已订阅下载事件。

        Returns:
            bool: ``True`` 表示当前 context 已订阅下载事件。
        """
        return self._listening

    def set_behavior(
        self, behavior="allow", path=None, contexts=None, user_contexts=None
    ):
        """设置下载行为。

        Args:
            behavior: 下载策略字符串。
                常见值：``'allow'`` 允许下载、``'deny'`` 拒绝下载、
                ``'allowAndOpen'`` 允许并尝试打开。
            path: 下载目录路径。
                单位：文件系统路径字符串。
                常见值：绝对路径如 ``'E:/ruyipage/examples/downloads'``。
                当 ``behavior='allow'`` 时通常与 ``path`` 配合使用。
            contexts: 受影响的顶层 browsingContext ID 列表。
                常见值：``[page.tab_id]``。传 ``None`` 时默认作用于当前页面。
            user_contexts: 受影响的 user context ID 列表。
                常见值：Firefox 容器标签页 ID 列表。与 ``contexts`` 互斥。

        Returns:
            dict: BiDi 命令返回结果，通常为空字典。

        适用场景：
            - 示例中切换 allow / deny 策略
            - 测试特定 tab 的下载隔离行为
            - 统一替代散落的旧式下载路径设置代码
        """
        if contexts is not None and user_contexts is not None:
            raise ValueError("contexts 与 user_contexts 不能同时设置")

        if contexts is None and user_contexts is None:
            contexts = [self._owner._context_id]

        return bidi_browser.set_download_behavior(
            self._owner._driver._browser_driver,
            behavior=behavior,
            download_path=path,
            contexts=contexts,
            user_contexts=user_contexts,
        )

    def set_path(self, path):
        """把当前页面下载策略设置为允许并指定下载目录。

        Args:
            path: 下载目录路径。
                单位：文件系统路径字符串。
                常见值：项目内绝对路径或当前工程下的相对路径。

        Returns:
            owner: 原页面对象，便于链式调用。

        适用场景：
            - 新手只想快速指定下载目录
            - 替代 ``set_download_path()`` / ``set.download_path()`` 的底层细节
        """
        self.set_behavior("allow", path=path)
        return self._owner

    def start(self):
        """开始监听当前页面的下载事件。

        Returns:
            bool: ``True`` 表示已成功订阅；``False`` 表示订阅失败。

        适用场景：
            - 点击下载前预先启动事件监听
            - 示例中验证标准下载事件链是否完整
        """
        if self._listening:
            self.stop()

        self.clear()

        try:
            result = bidi_session.subscribe(
                self._owner._driver._browser_driver,
                self.EVENTS,
                contexts=[self._owner._context_id],
            )
            self._subscription_id = result.get("subscription")
        except Exception:
            self._subscription_id = None
            self._listening = False
            return False

        browser_driver = self._owner._driver._browser_driver
        browser_driver.set_callback(
            "browsingContext.downloadWillBegin",
            self._on_download_will_begin,
        )
        browser_driver.set_callback(
            "browsingContext.downloadEnd",
            self._on_download_end,
        )
        self._listening = True
        return True

    def stop(self):
        """停止监听下载事件并清理回调。

        Returns:
            owner: 原页面对象。

        适用场景：
            - 示例结尾统一清理
            - 单个测试结束后解除事件订阅，避免影响后续 case
        """
        browser_driver = self._owner._driver._browser_driver

        if self._subscription_id:
            try:
                bidi_session.unsubscribe(
                    browser_driver,
                    subscription=self._subscription_id,
                )
            except Exception:
                pass

        try:
            browser_driver.remove_callback(
                "browsingContext.downloadWillBegin",
            )
            browser_driver.remove_callback(
                "browsingContext.downloadEnd",
            )
        except Exception:
            pass

        self._subscription_id = None
        self._listening = False
        return self._owner

    def clear(self):
        """清空已缓存事件。

        Returns:
            owner: 原页面对象。

        适用场景：
            - 同一页面多次触发不同下载前重置缓存
            - 等待新一轮事件链时避免误读旧事件
        """
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break

        with self._lock:
            self._events = []
        return self._owner

    def wait(self, method=None, timeout=5, filename=None, status=None):
        """等待一个匹配条件的下载事件。

        Args:
            method: 要等待的事件名。
                常见值：``'browsingContext.downloadWillBegin'``、
                ``'browsingContext.downloadEnd'``。传 ``None`` 表示任意下载事件。
            timeout: 等待超时时间。
                单位：秒。
                常见值：``3``、``5``、``10``。
            filename: 用于过滤建议文件名的字符串。
                常见值：``'test.txt'``、``'report.pdf'``。
            status: 用于过滤结束状态。
                常见值：``'complete'``、``'canceled'``、``'pending'``。

        Returns:
            DownloadEvent | None: 匹配到事件时返回事件对象，超时返回 ``None``。

        适用场景：
            - 等待下载开始事件，确认浏览器已接受下载请求
            - 等待下载结束事件，判断完成/取消/失败状态
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            remaining = end_time - time.time()
            try:
                event = _queue_get(self._queue, timeout=min(remaining, 0.2))
            except Empty:
                continue

            if self._match(event, method=method, filename=filename, status=status):
                return event
        return None

    def wait_chain(self, filename=None, timeout=5):
        """等待一次下载的开始与结束事件链。

        Args:
            filename: 期望文件名。
                常见值：``'test.txt'``。传 ``None`` 表示接受任意文件。
            timeout: 整条事件链的总等待时间。
                单位：秒。

        Returns:
            tuple[DownloadEvent | None, DownloadEvent | None]:
            ``(downloadWillBegin, downloadEnd)``。

        适用场景：
            - 示例中验证一次下载是否具备完整标准事件链
            - 测试里对开始事件和结束事件分别做断言
        """
        begin = self.wait(
            method="browsingContext.downloadWillBegin",
            timeout=timeout,
            filename=filename,
        )
        if not begin:
            return None, None

        remaining = max(0.1, timeout)
        end = self.wait(
            method="browsingContext.downloadEnd",
            timeout=remaining,
            filename=filename,
        )
        return begin, end

    def file_exists(self, path, min_size=1):
        """检查下载文件是否已稳定落盘。

        Args:
            path: 待检查文件路径。
                单位：文件系统路径字符串。
            min_size: 文件最小字节数。
                单位：字节。
                常见值：``1`` 表示非空文件，``10`` / ``100`` 用于规避空壳文件。

        Returns:
            bool: ``True`` 表示文件存在且大小不小于 ``min_size``。

        适用场景：
            - 区分“事件已触发”与“文件已落盘”
            - 下载验证中做最小稳定性检查
        """
        return os.path.exists(path) and os.path.getsize(path) >= min_size

    def wait_file(self, path, timeout=5, min_size=1):
        """等待文件落盘。

        Args:
            path: 文件路径。
                单位：文件系统路径字符串。
            timeout: 最大等待时间。
                单位：秒。
                常见值：``3``、``5``、``10``。
            min_size: 文件最小字节数。
                单位：字节。

        Returns:
            bool: ``True`` 表示超时前已落盘，``False`` 表示未观察到稳定文件。

        适用场景：
            - 下载示例中验证真实文件输出
            - 受浏览器策略影响时把“未落盘”单独标记为失败或不支持
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            if self.file_exists(path, min_size=min_size):
                return True
            _sleep(0.1)
        return False

    def _on_download_will_begin(self, params):
        self._push("browsingContext.downloadWillBegin", params)

    def _on_download_end(self, params):
        self._push("browsingContext.downloadEnd", params)

    def _push(self, method, params):
        context = (params or {}).get("context")
        if context is not None and context != self._owner._context_id:
            return
        event = DownloadEvent(method, params)
        with self._lock:
            self._events.append(event)
        self._queue.put(event)

    @staticmethod
    def _match(event, method=None, filename=None, status=None):
        if method and event.method != method:
            return False
        if filename and event.suggested_filename != filename:
            return False
        if status and event.status != status:
            return False
        return True
