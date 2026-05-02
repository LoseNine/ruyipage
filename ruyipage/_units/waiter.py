# -*- coding: utf-8 -*-
"""Waiter - 等待条件管理"""

import time

from .._functions.settings import Settings
from .._functions.sleep import sleep as _sleep
from ..errors import WaitTimeoutError

import logging

logger = logging.getLogger('ruyipage')


class PageWaiter(object):
    """页面级等待条件

    用法::

        page.wait.ele_displayed('#result')
        page.wait.title_contains('Dashboard')
        page.wait.doc_loaded()
        page.wait(2)  # 等待2秒
    """

    def __init__(self, owner):
        self._owner = owner

    def __call__(self, seconds):
        """等待指定秒数"""
        _sleep(seconds)
        return self._owner

    def ele_displayed(self, locator, timeout=None):
        """等待元素可见

        Args:
            locator: 定位器
            timeout: 超时时间（秒）

        Returns:
            找到的元素 或 False
        """
        return self._wait_condition(
            lambda: self._check_displayed(locator),
            timeout, '等待元素可见: {}'.format(locator)
        )

    def ele_hidden(self, locator, timeout=None):
        """等待元素隐藏或消失

        Args:
            locator: 定位器
            timeout: 超时

        Returns:
            True 或 False
        """
        return self._wait_condition(
            lambda: self._check_hidden(locator),
            timeout, '等待元素隐藏: {}'.format(locator)
        )

    def ele_deleted(self, locator, timeout=None):
        """等待元素从 DOM 中删除

        Args:
            locator: 定位器
            timeout: 超时

        Returns:
            True 或 False
        """
        return self._wait_condition(
            lambda: not self._owner.eles(locator, timeout=0.1),
            timeout, '等待元素删除: {}'.format(locator)
        )

    def ele(self, locator, timeout=None):
        """等待元素出现（在 DOM 中存在）

        Args:
            locator: 定位器
            timeout: 超时

        Returns:
            找到的元素 或 False
        """
        return self._wait_condition(
            lambda: self._owner.ele(locator, timeout=0.1) or None,
            timeout, '等待元素出现: {}'.format(locator)
        )

    def title_is(self, title, timeout=None):
        """等待标题等于指定值"""
        return self._wait_condition(
            lambda: self._owner.title == title or None,
            timeout, '等待标题为: {}'.format(title)
        )

    def title_contains(self, text, timeout=None):
        """等待标题包含指定文本"""
        return self._wait_condition(
            lambda: text in self._owner.title or None,
            timeout, '等待标题包含: {}'.format(text)
        )

    def url_contains(self, text, timeout=None):
        """等待 URL 包含指定文本"""
        return self._wait_condition(
            lambda: text in self._owner.url or None,
            timeout, '等待URL包含: {}'.format(text)
        )

    def url_change(self, current_url=None, timeout=None):
        """等待 URL 变化"""
        if current_url is None:
            current_url = self._owner.url
        return self._wait_condition(
            lambda: self._owner.url != current_url or None,
            timeout, '等待URL变化'
        )

    def doc_loaded(self, timeout=None):
        """等待页面加载完成（readyState == 'complete'）"""
        return self._wait_condition(
            lambda: self._owner.ready_state == 'complete' or None,
            timeout, '等待页面加载'
        )

    def load_start(self, timeout=None):
        """等待页面开始加载"""
        return self._wait_condition(
            lambda: self._owner.ready_state == 'loading' or None,
            timeout, '等待加载开始'
        )

    def js_result(self, script, timeout=None):
        """等待 JS 表达式返回真值

        Args:
            script: JS 表达式
            timeout: 超时

        Returns:
            JS 返回值 或 False
        """
        return self._wait_condition(
            lambda: self._owner.run_js(script) or None,
            timeout, '等待JS结果: {}...'.format(script[:30])
        )

    def _check_displayed(self, locator):
        """检查元素是否可见"""
        ele = self._owner.ele(locator, timeout=0.1)
        if ele and ele.is_displayed:
            return ele
        return None

    def _check_hidden(self, locator):
        """检查元素是否隐藏"""
        ele = self._owner.ele(locator, timeout=0.1)
        if not ele or not ele.is_displayed:
            return True
        return None

    def _wait_condition(self, condition_fn, timeout, msg=''):
        """通用等待条件

        Args:
            condition_fn: 条件函数，返回真值表示满足
            timeout: 超时
            msg: 超时错误消息

        Returns:
            条件函数的返回值 或 False
        """
        if timeout is None:
            timeout = Settings.element_find_timeout

        end_time = time.time() + timeout

        while True:
            try:
                result = condition_fn()
                if result is not None and result is not False:
                    return result
            except Exception as e:
                logger.debug("等待条件执行异常: %s", e)

            if time.time() >= end_time:
                if Settings.raise_when_wait_failed:
                    raise WaitTimeoutError(msg)
                return False

            _sleep(0.3)


class ElementWaiter(object):
    """元素级等待条件

    用法::

        ele.wait.displayed()
        ele.wait.hidden()
    """

    def __init__(self, element):
        self._ele = element

    def __call__(self, seconds):
        """等待指定秒数"""
        _sleep(seconds)
        return self._ele

    def displayed(self, timeout=None):
        """等待元素可见"""
        if timeout is None:
            timeout = Settings.element_find_timeout

        end_time = time.time() + timeout
        while True:
            if self._ele.is_displayed:
                return self._ele
            if time.time() >= end_time:
                if Settings.raise_when_wait_failed:
                    raise WaitTimeoutError('等待元素可见超时')
                return False
            _sleep(0.3)

    def hidden(self, timeout=None):
        """等待元素隐藏"""
        if timeout is None:
            timeout = Settings.element_find_timeout

        end_time = time.time() + timeout
        while True:
            if not self._ele.is_displayed:
                return True
            if time.time() >= end_time:
                if Settings.raise_when_wait_failed:
                    raise WaitTimeoutError('等待元素隐藏超时')
                return False
            _sleep(0.3)

    def enabled(self, timeout=None):
        """等待元素可用"""
        if timeout is None:
            timeout = Settings.element_find_timeout

        end_time = time.time() + timeout
        while True:
            if self._ele.is_enabled:
                return self._ele
            if time.time() >= end_time:
                return False
            _sleep(0.3)

    def disabled(self, timeout=None):
        """等待元素禁用"""
        if timeout is None:
            timeout = Settings.element_find_timeout

        end_time = time.time() + timeout
        while True:
            if not self._ele.is_enabled:
                return True
            if time.time() >= end_time:
                return False
            _sleep(0.3)
