# -*- coding: utf-8 -*-
"""异步 API 基线测试 —— 导入与类结构验证。

这些测试不需要浏览器，验证异步模块的导入、类型、API 完整性。
"""

import inspect
import pytest


# ── 导入测试 ──────────────────────────────────────────────────────────────


class TestAsyncImport:
    """验证异步模块可以正常导入。"""

    def test_aio_module_imports(self):
        from ruyipage.aio import launch, attach, AsyncFirefoxPage
        assert callable(launch)
        assert callable(attach)
        assert AsyncFirefoxPage is not None

    def test_async_classes_importable(self):
        from ruyipage.aio import (
            AsyncFirefoxPage,
            AsyncFirefoxTab,
            AsyncFirefoxFrame,
            AsyncFirefoxElement,
            AsyncNoneElement,
        )
        for cls in [AsyncFirefoxPage, AsyncFirefoxTab, AsyncFirefoxFrame, AsyncFirefoxElement]:
            assert isinstance(cls, type)
        assert isinstance(AsyncNoneElement, type)

    def test_sync_import_unaffected(self):
        """确保异步模块的存在不影响同步导入。"""
        import ruyipage
        assert ruyipage.__version__
        assert hasattr(ruyipage, "launch")
        assert hasattr(ruyipage, "FirefoxPage")
        assert hasattr(ruyipage, "FirefoxElement")

    def test_reexports_available(self):
        """验证 aio 模块重导出了必要的非异步类型。"""
        from ruyipage.aio import FirefoxOptions, Settings, Keys, StaticElement
        assert FirefoxOptions is not None
        assert Settings is not None
        assert Keys is not None
        assert StaticElement is not None


# ── API 完整性测试 ────────────────────────────────────────────────────────


class TestApiCompleteness:
    """验证异步 API 覆盖了同步 API 的所有公共方法。"""

    @staticmethod
    def _public_api(cls):
        return {n for n in dir(cls) if not n.startswith("_")}

    @staticmethod
    def _mapped_api(async_api):
        """将异步 API 映射回同步名称（get_xxx → xxx）"""
        result = set(async_api)
        for name in async_api:
            if name.startswith("get_"):
                result.add(name[4:])
        return result

    def test_firefox_base_complete(self):
        from ruyipage._pages.firefox_base import FirefoxBase
        from ruyipage._async._generated import AsyncFirefoxBase

        sync_api = self._public_api(FirefoxBase)
        async_api = self._mapped_api(self._public_api(AsyncFirefoxBase))
        missing = sync_api - async_api
        assert not missing, "AsyncFirefoxBase missing: {}".format(missing)

    def test_firefox_page_complete(self):
        from ruyipage._pages.firefox_page import FirefoxPage
        from ruyipage._async._generated import AsyncFirefoxPage

        sync_api = self._public_api(FirefoxPage)
        async_api = self._mapped_api(self._public_api(AsyncFirefoxPage))
        missing = sync_api - async_api
        assert not missing, "AsyncFirefoxPage missing: {}".format(missing)

    def test_firefox_tab_complete(self):
        from ruyipage._pages.firefox_tab import FirefoxTab
        from ruyipage._async._generated import AsyncFirefoxTab

        sync_api = self._public_api(FirefoxTab)
        async_api = self._mapped_api(self._public_api(AsyncFirefoxTab))
        missing = sync_api - async_api
        assert not missing, "AsyncFirefoxTab missing: {}".format(missing)

    def test_firefox_frame_complete(self):
        from ruyipage._pages.firefox_frame import FirefoxFrame
        from ruyipage._async._generated import AsyncFirefoxFrame

        sync_api = self._public_api(FirefoxFrame)
        async_api = self._mapped_api(self._public_api(AsyncFirefoxFrame))
        missing = sync_api - async_api
        assert not missing, "AsyncFirefoxFrame missing: {}".format(missing)

    def test_firefox_element_complete(self):
        from ruyipage._elements.firefox_element import FirefoxElement
        from ruyipage._async._generated import AsyncFirefoxElement

        sync_api = self._public_api(FirefoxElement)
        async_api = self._mapped_api(self._public_api(AsyncFirefoxElement))
        missing = sync_api - async_api
        assert not missing, "AsyncFirefoxElement missing: {}".format(missing)


# ── 方法签名测试 ──────────────────────────────────────────────────────────


class TestMethodSignatures:
    """验证异步方法是 coroutine function。"""

    def test_launch_is_coroutine(self):
        from ruyipage.aio import launch
        assert inspect.iscoroutinefunction(launch)

    def test_attach_is_coroutine(self):
        from ruyipage.aio import attach
        assert inspect.iscoroutinefunction(attach)

    def test_page_methods_are_coroutines(self):
        from ruyipage._async._generated import AsyncFirefoxBase

        expected_coros = [
            "get", "back", "forward", "refresh", "ele", "eles",
            "run_js", "screenshot", "handle_alert", "set_viewport",
            "get_title", "get_url", "get_html",
        ]
        for name in expected_coros:
            method = getattr(AsyncFirefoxBase, name, None)
            assert method is not None, "AsyncFirefoxBase.{} not found".format(name)
            assert inspect.iscoroutinefunction(method), (
                "AsyncFirefoxBase.{} should be a coroutine function".format(name)
            )

    def test_element_methods_are_coroutines(self):
        from ruyipage._async._generated import AsyncFirefoxElement

        expected_coros = [
            "click_self", "input", "clear", "ele", "eles",
            "get_text", "get_tag", "get_html",
            "attr", "hover",
        ]
        for name in expected_coros:
            method = getattr(AsyncFirefoxElement, name, None)
            assert method is not None, "AsyncFirefoxElement.{} not found".format(name)
            assert inspect.iscoroutinefunction(method), (
                "AsyncFirefoxElement.{} should be a coroutine function".format(name)
            )

    def test_unit_proxies_are_sync_properties(self):
        """Unit 属性（scroll, actions 等）应该是同步 property，不是 coroutine。"""
        from ruyipage._async._generated import AsyncFirefoxBase

        unit_names = [
            "scroll", "actions", "touch", "wait", "listen", "intercept",
            "downloads", "emulation", "window",
        ]
        for name in unit_names:
            attr = inspect.getattr_static(AsyncFirefoxBase, name, None)
            assert isinstance(attr, property), (
                "AsyncFirefoxBase.{} should be a property, got {}".format(name, type(attr))
            )


# ── NoneElement 测试 ──────────────────────────────────────────────────────


class TestAsyncNoneElement:
    """验证 AsyncNoneElement 的行为。"""

    def test_bool_is_false(self):
        from ruyipage._async._generated import AsyncNoneElement
        ne = AsyncNoneElement()
        assert bool(ne) is False

    def test_repr(self):
        from ruyipage._async._generated import AsyncNoneElement
        ne = AsyncNoneElement()
        assert "AsyncNoneElement" in repr(ne)


# ── greenlet bridge 测试 ──────────────────────────────────────────────────


class TestGreenletBridge:
    """验证 greenlet 桥接的基本功能。"""

    @pytest.mark.asyncio
    async def test_greenlet_spawn_runs_sync_function(self):
        from ruyipage._async.greenlet_bridge import greenlet_spawn

        def add(a, b):
            return a + b

        result = await greenlet_spawn(add, 3, 4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_greenlet_spawn_propagates_exception(self):
        from ruyipage._async.greenlet_bridge import greenlet_spawn

        def fail():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await greenlet_spawn(fail)

    @pytest.mark.asyncio
    async def test_await_yields_to_event_loop(self):
        import asyncio
        from ruyipage._async.greenlet_bridge import greenlet_spawn, await_

        def sync_with_async_sleep():
            await_(asyncio.sleep(0.01))
            return "done"

        result = await greenlet_spawn(sync_with_async_sleep)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_await_outside_greenlet_raises(self):
        import asyncio
        from ruyipage._async.greenlet_bridge import await_

        with pytest.raises(RuntimeError, match="greenlet_spawn"):
            await_(asyncio.sleep(0))

    @pytest.mark.asyncio
    async def test_in_async_greenlet_detection(self):
        from ruyipage._async.greenlet_bridge import greenlet_spawn, _in_async_greenlet

        assert not _in_async_greenlet()

        def check_inside():
            return _in_async_greenlet()

        result = await greenlet_spawn(check_inside)
        assert result is True

    @pytest.mark.asyncio
    async def test_concurrent_greenlet_spawns(self):
        import asyncio
        from ruyipage._async.greenlet_bridge import greenlet_spawn, await_

        results = []

        def task(name, delay):
            await_(asyncio.sleep(delay))
            results.append(name)
            return name

        r1, r2 = await asyncio.gather(
            greenlet_spawn(task, "A", 0.02),
            greenlet_spawn(task, "B", 0.01),
        )
        assert r1 == "A"
        assert r2 == "B"
        # B should finish first because it sleeps less
        assert results[0] == "B"


# ── interruptible sleep 测试 ─────────────────────────────────────────────


class TestInterruptibleSleep:
    """验证 sleep 辅助函数的行为。"""

    def test_sync_sleep_works(self):
        """同步上下文中 sleep 等同于 time.sleep。"""
        import time
        from ruyipage._functions.sleep import sleep

        start = time.time()
        sleep(0.05)
        elapsed = time.time() - start
        assert elapsed >= 0.04

    @pytest.mark.asyncio
    async def test_async_sleep_yields(self):
        """greenlet 中 sleep 让出事件循环。"""
        import asyncio
        from ruyipage._async.greenlet_bridge import greenlet_spawn
        from ruyipage._functions.sleep import sleep

        events = []

        async def background():
            events.append("bg_start")
            await asyncio.sleep(0.01)
            events.append("bg_end")

        def sync_with_sleep():
            events.append("sync_start")
            sleep(0.05)
            events.append("sync_end")

        bg_task = asyncio.create_task(background())
        await greenlet_spawn(sync_with_sleep)
        await bg_task

        # bg_start 和 bg_end 应该在 sleep 期间执行
        assert "bg_start" in events
        assert "bg_end" in events
        assert events.index("bg_start") < events.index("sync_end")
