# -*- coding: utf-8 -*-
"""new_tab 并发竞态回归测试

覆盖 issue #32：多线程开关容器 tab 时，new_tab 偶发
``no such frame: DiscardedBrowsingContextError: BrowsingContext does no longer exist``。

前台 browsingContext.create 会在 Firefox 内部等待「当前选中 tab」变为 hidden，
该 tab 被并发关闭就报错，且新 tab 已建好却拿不到 ID。现在总是后台 create，
前台需求改由 activate 完成；缓存里过期的 referenceContext 也会去掉后重试。

全部使用假 driver，不依赖真实 Firefox。
"""

import threading

import pytest

from ruyipage._base.browser import Firefox
from ruyipage.errors import BiDiError


_DISCARDED = BiDiError(
    "no such frame",
    "DiscardedBrowsingContextError: BrowsingContext does no longer exist",
)


class _FakeDriver:
    """按方法名排队返回结果或抛错的假 driver。"""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = {k: list(v) for k, v in (responses or {}).items()}

    def run(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        queue = self._responses.get(method)
        if queue:
            result = queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        if method == "browsingContext.create":
            return {"context": "new-ctx"}
        return {}


class _FakeTab:
    def __init__(self, ctx_id):
        self.ctx_id = ctx_id
        self.visited = []

    def get(self, url):
        self.visited.append(url)


def _bare_browser(driver, context_ids=()):
    browser = Firefox.__new__(Firefox)
    browser._driver = driver
    browser._context_ids = list(context_ids)
    browser._context_ids_lock = threading.Lock()
    browser._contexts = {}
    browser._xpath_picker_last_reinject = {}
    browser._context_nav_locks = {}
    browser._context_nav_locks_lock = threading.Lock()
    browser._get_or_create_tab = _FakeTab
    return browser


def test_foreground_tab_is_created_in_background_then_activated():
    driver = _FakeDriver()
    browser = _bare_browser(driver, ["first"])

    tab = browser.new_tab()

    assert tab.ctx_id == "new-ctx"
    assert driver.calls == [
        (
            "browsingContext.create",
            {"type": "tab", "background": True, "referenceContext": "first"},
        ),
        ("browsingContext.activate", {"context": "new-ctx"}),
    ]
    assert browser._context_ids == ["first", "new-ctx"]


def test_background_tab_is_not_activated():
    driver = _FakeDriver()
    browser = _bare_browser(driver, ["first"])

    browser.new_tab(background=True)

    assert [m for m, _ in driver.calls] == ["browsingContext.create"]


def test_user_context_and_url_are_passed_through():
    driver = _FakeDriver()
    browser = _bare_browser(driver, ["first"])

    tab = browser.new_tab(url="https://example.com", user_context="uc-1")

    assert driver.calls[0][1]["userContext"] == "uc-1"
    assert tab.visited == ["https://example.com"]


def test_selected_tab_closed_during_activate_still_returns_the_tab():
    driver = _FakeDriver({"browsingContext.activate": [_DISCARDED]})
    browser = _bare_browser(driver, ["first"])

    tab = browser.new_tab(url="https://example.com")

    assert tab.ctx_id == "new-ctx"
    assert tab.visited == ["https://example.com"]
    # 只建了一个 tab，没有重试 create 而多开孤儿 tab
    assert [m for m, _ in driver.calls].count("browsingContext.create") == 1


def test_other_activate_errors_still_propagate():
    driver = _FakeDriver(
        {"browsingContext.activate": [BiDiError("unknown error", "boom")]}
    )
    browser = _bare_browser(driver, ["first"])

    with pytest.raises(BiDiError, match="boom"):
        browser.new_tab()


def test_stale_reference_context_is_dropped_and_create_retried():
    stale = BiDiError(
        "no such frame", "Browsing Context with id gone-ctx not found"
    )
    driver = _FakeDriver({"browsingContext.create": [stale]})
    browser = _bare_browser(driver, ["gone-ctx", "other"])
    browser._contexts["gone-ctx"] = object()

    tab = browser.new_tab(background=True, user_context="uc-1")

    assert tab.ctx_id == "new-ctx"
    assert driver.calls == [
        (
            "browsingContext.create",
            {
                "type": "tab",
                "background": True,
                "userContext": "uc-1",
                "referenceContext": "gone-ctx",
            },
        ),
        (
            "browsingContext.create",
            {"type": "tab", "background": True, "userContext": "uc-1"},
        ),
    ]
    assert browser._context_ids == ["other", "new-ctx"]
    assert "gone-ctx" not in browser._contexts


def test_other_create_errors_are_not_retried():
    driver = _FakeDriver(
        {
            "browsingContext.create": [
                BiDiError("no such user context", "User Context with id x was not found")
            ]
        }
    )
    browser = _bare_browser(driver, ["first"])

    with pytest.raises(BiDiError, match="no such user context"):
        browser.new_tab(user_context="x")

    assert len(driver.calls) == 1


def test_create_without_known_tabs_sends_no_reference():
    driver = _FakeDriver()
    browser = _bare_browser(driver)

    browser.new_tab(background=True)

    assert driver.calls == [
        ("browsingContext.create", {"type": "tab", "background": True})
    ]
