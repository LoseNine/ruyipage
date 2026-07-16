# -*- coding: utf-8 -*-
"""Regression tests for natural set_window_size() behavior."""

from types import SimpleNamespace

from ruyipage._pages.firefox_base import FirefoxBase

class _FakeWindow:
    def __init__(self):
        self.sizes = []

    def _set_size_only(self, width, height):
        self.sizes.append((width, height))


class _FakeBrowserDriver:
    def __init__(self):
        self.calls = []

    def run(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}, timeout))
        return {}


def _make_page():
    page = FirefoxBase.__new__(FirefoxBase)
    FirefoxBase.__init__(page)
    browser_driver = _FakeBrowserDriver()
    page._driver = SimpleNamespace(_browser_driver=browser_driver)
    page._context_id = "context-1"
    page._window = _FakeWindow()
    return page, browser_driver


def test_set_window_size_only_resizes_outer_window():
    page, browser_driver = _make_page()

    page.set_window_size(1360, 700)

    assert page._window.sizes == [(1360, 700)]
    assert browser_driver.calls == []
