# -*- coding: utf-8 -*-
"""异步 API 基线测试 —— 导入与类结构验证。

这些测试不需要浏览器，验证异步模块的导入、类型、API 完整性。
"""

import inspect
from pathlib import Path
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

    def test_geolocation_optional_fields_remain_keyword_only(self):
        from ruyipage._async._generated import AsyncFirefoxBase

        signature = inspect.signature(AsyncFirefoxBase.set_geolocation)
        for name in (
            "altitude",
            "altitude_accuracy",
            "heading",
            "speed",
        ):
            assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY

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

        coro = asyncio.sleep(0)
        try:
            with pytest.raises(RuntimeError, match="greenlet_spawn"):
                await_(coro)
        finally:
            coro.close()

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


class TestAsyncTabWrapping:
    """Verify tab-returning page APIs expose async tab wrappers."""

    class _SyncPage:
        def __init__(self):
            self.tab = object()
            self.tabs = [object(), object()]
            self.calls = []

        def new_container_tab(self, url=None, background=False):
            self.calls.append(("new_container_tab", url, background))
            return self.tab

        def new_container_tabs(self, count, url=None, background=False):
            self.calls.append(("new_container_tabs", count, url, background))
            return self.tabs[:count]

    @pytest.mark.asyncio
    async def test_new_container_tab_returns_async_tab(self):
        from ruyipage._async._generated import AsyncFirefoxPage, AsyncFirefoxTab

        sync_page = self._SyncPage()
        page = AsyncFirefoxPage(sync_page)

        tab = await page.new_container_tab("https://example.com", background=True)

        assert isinstance(tab, AsyncFirefoxTab)
        assert tab._sync is sync_page.tab
        assert sync_page.calls == [
            ("new_container_tab", "https://example.com", True)
        ]

    @pytest.mark.asyncio
    async def test_new_container_tabs_returns_async_tabs(self):
        from ruyipage._async._generated import AsyncFirefoxPage, AsyncFirefoxTab

        sync_page = self._SyncPage()
        page = AsyncFirefoxPage(sync_page)

        tabs = await page.new_container_tabs(
            2, "https://example.com", background=False
        )

        assert [type(tab) for tab in tabs] == [AsyncFirefoxTab, AsyncFirefoxTab]
        assert [tab._sync for tab in tabs] == sync_page.tabs
        assert sync_page.calls == [
            ("new_container_tabs", 2, "https://example.com", False)
        ]

    @pytest.mark.asyncio
    async def test_get_tab_returns_none_when_sync_api_returns_none(self):
        from ruyipage._async._generated import AsyncFirefoxPage

        class SyncPage:
            def get_tab(self, id_or_num=None, title=None, url=None):
                return None

        page = AsyncFirefoxPage(SyncPage())

        assert await page.get_tab("missing-tab") is None


class TestAsyncUnitProxyWrapping:
    """Verify async unit proxies do not leak sync owner/element objects."""

    class _OwnerUnit:
        def __init__(self, owner):
            self._owner = owner

        def __call__(self):
            return self._owner

        def maximize(self):
            return self._owner

        def self_unit(self):
            return self

    class _ElementUnit:
        def __init__(self, element):
            self._ele = element

        def __call__(self):
            return self._ele

        def displayed(self):
            return self._ele

        def self_unit(self):
            return self

    @pytest.mark.asyncio
    async def test_page_unit_owner_return_stays_async(self):
        from ruyipage._async._generated import AsyncFirefoxPage

        class SyncPage:
            def __init__(self):
                self.window = TestAsyncUnitProxyWrapping._OwnerUnit(self)

        sync_page = SyncPage()
        page = AsyncFirefoxPage(sync_page)

        assert await page.window.maximize() is page
        assert await page.window.self_unit() is page.window
        assert page.window._owner is page
        assert await page.window() is page

    @pytest.mark.asyncio
    async def test_element_unit_element_return_stays_async(self):
        from ruyipage._async._generated import AsyncFirefoxElement

        class SyncElement:
            def __init__(self):
                self.wait = TestAsyncUnitProxyWrapping._ElementUnit(self)

        sync_element = SyncElement()
        element = AsyncFirefoxElement(sync_element)

        assert await element.wait.displayed() is element
        assert await element.wait.self_unit() is element.wait
        assert element.wait._owner is element
        assert await element.wait() is element


class TestAsyncGeneratedReturnWrapping:
    """Verify generated wrappers preserve async types for object properties."""

    def test_wrap_async_result_maps_sync_objects_to_async_wrappers(self):
        from ruyipage import FirefoxPage
        from ruyipage._async._generated import (
            AsyncFirefoxFrame,
            AsyncFirefoxPage,
            AsyncFirefoxTab,
            AsyncFirefoxElement,
            AsyncNoneElement,
            _wrap_async_result,
        )
        from ruyipage._elements.firefox_element import FirefoxElement
        from ruyipage._elements.none_element import NoneElement
        from ruyipage._pages.firefox_frame import FirefoxFrame
        from ruyipage._pages.firefox_tab import FirefoxTab

        sync_page = FirefoxPage.__new__(FirefoxPage)
        sync_tab = FirefoxTab.__new__(FirefoxTab)
        sync_frame = FirefoxFrame.__new__(FirefoxFrame)
        sync_element = FirefoxElement.__new__(FirefoxElement)
        sync_none_element = NoneElement.__new__(NoneElement)

        wrapped_page = _wrap_async_result(sync_page)
        wrapped_tab = _wrap_async_result(sync_tab)
        wrapped_frame = _wrap_async_result(sync_frame)
        wrapped_element = _wrap_async_result(sync_element)
        wrapped_none_element = _wrap_async_result(sync_none_element)

        assert isinstance(wrapped_page, AsyncFirefoxPage)
        assert wrapped_page._sync is sync_page
        assert isinstance(wrapped_tab, AsyncFirefoxTab)
        assert wrapped_tab._sync is sync_tab
        assert isinstance(wrapped_frame, AsyncFirefoxFrame)
        assert wrapped_frame._sync is sync_frame
        assert isinstance(wrapped_element, AsyncFirefoxElement)
        assert wrapped_element._sync is sync_element
        assert isinstance(wrapped_none_element, AsyncNoneElement)
        assert wrapped_none_element._sync is sync_none_element
        assert _wrap_async_result(None) is None

        scalar = {"plain": "value"}
        assert _wrap_async_result(scalar) is scalar

    @pytest.mark.asyncio
    async def test_frame_parent_wraps_parent_page(self):
        from ruyipage import FirefoxPage
        from ruyipage._async._generated import AsyncFirefoxFrame, AsyncFirefoxPage

        sync_parent = FirefoxPage.__new__(FirefoxPage)

        class SyncFrame:
            @property
            def parent(self):
                return sync_parent

        parent = await AsyncFirefoxFrame(SyncFrame()).get_parent()

        assert isinstance(parent, AsyncFirefoxPage)
        assert parent._sync is sync_parent

    @pytest.mark.asyncio
    async def test_element_shadow_getters_wrap_elements(self):
        from ruyipage._elements.firefox_element import FirefoxElement
        from ruyipage._async._generated import AsyncFirefoxElement

        sync_shadow = FirefoxElement.__new__(FirefoxElement)

        class SyncElement:
            @property
            def shadow_root(self):
                return sync_shadow

            @property
            def closed_shadow_root(self):
                return None

        element = AsyncFirefoxElement(SyncElement())

        shadow = await element.get_shadow_root()
        closed_shadow = await element.get_closed_shadow_root()

        assert isinstance(shadow, AsyncFirefoxElement)
        assert shadow._sync is sync_shadow
        assert closed_shadow is None


class TestPytestMarkerBuckets:
    """Verify fast/browser bucket rules without launching Firefox."""

    def test_browser_bucket_detects_paths_and_fixtures(self):
        from tests import conftest

        assert conftest._is_browser_test("tests/smoke/test_startup.py", ())
        assert conftest._is_browser_test("tests/integration/test_flow.py", ())
        assert conftest._is_browser_test("tests/release/test_gate.py", ())
        assert conftest._is_browser_test(
            "tests/async_smoke/test_async_smoke.py", ()
        )
        assert conftest._is_browser_test(
            "tests/features/test_private_mode.py", ()
        )
        assert conftest._is_browser_test(
            "tests/custom/test_direct_browser.py", ("tmp_path", "page")
        )

    def test_fast_bucket_keeps_pure_unit_tests_short(self):
        from tests import conftest

        assert not conftest._is_browser_test(
            "tests/async_smoke/test_async_baseline.py", ()
        )
        assert not conftest._is_browser_test(
            "tests/features/test_launch_proxy.py", ("tmp_path", "monkeypatch")
        )
        assert not conftest._is_browser_test(
            "tests/test_fingerprint_builder.py", ()
        )

    def test_feature_files_that_construct_pages_are_browser_bucketed(self):
        from pathlib import Path
        from tests import conftest

        feature_dir = conftest.TESTS_DIR / "features"
        page_constructing_files = [
            path for path in feature_dir.glob("test_*.py")
            if "FirefoxPage(" in path.read_text(encoding="utf-8")
        ]

        assert page_constructing_files
        for path in page_constructing_files:
            rel_path = path.relative_to(conftest.PROJECT_ROOT).as_posix()
            assert conftest._is_browser_test(rel_path, ()), rel_path

    def test_browser_file_allowlist_points_to_existing_tests(self):
        from tests import conftest

        for rel_path in conftest.BROWSER_TEST_FILES:
            path = conftest.PROJECT_ROOT / rel_path
            assert path.is_file(), rel_path


class TestSmartFingerprintDocs:
    """Verify the smart-fingerprint docs stay aligned with the contract."""

    def _read(self, relative_path: str) -> str:
        root = Path(__file__).resolve().parents[2]
        return (root / relative_path).read_text(encoding="utf-8")

    def test_smart_fingerprint_docs_describe_native_window_contract(self):
        readme = self._read("README.md")
        readme_en = self._read("README_EN.md")
        fingerprint_readme = self._read("ruyipage/_fingerprint/README.md")
        example = self._read("examples/48_smart_fingerprint.py")
        builder_doc = self._read("ruyipage/_fingerprint/builder.py")
        firefox_options_doc = self._read("ruyipage/_configs/firefox_options.py")
        assert "`apply_smart_fingerprint()` 默认不设置外部窗口" in readme
        assert "ctx = opts.smart_fingerprint(" in readme
        assert "page = FirefoxPage(opts)" in readme
        assert "ctx.apply_emulation(page)" in readme
        assert "set_window_size_on_opts" in readme
        assert "仅为兼容保留且已忽略" in readme
        assert "返回结果包含" in readme
        assert "`screen`" in readme
        assert "By default it does not set an external window" in readme_en
        assert "no longer stores `width` / `height`" in readme_en
        assert "After Firefox starts, `ctx.apply_emulation(page)`" in readme_en
        assert "set_screen_size(hw.width, hw.height)" in readme_en
        assert "`apply_smart_fingerprint()` 默认不设置外部窗口" in fingerprint_readme
        assert "`fpfile` 不再写 `width` / `height`" in fingerprint_readme
        assert "ctx = opts.smart_fingerprint(...)" in example
        assert "page = FirefoxPage(opts)" in example
        assert "ctx.apply_emulation(page)" in example
        assert "without ``width`` / ``height`` entries." in builder_doc
        assert "returns a map containing ``screen``" in builder_doc
        assert "If an outer window is required, call ``opts.set_window_size()`` explicitly" in firefox_options_doc

        forbidden_readme_en = [
            "automatically sets the window size",
            "fpfile keeps width and height",
        ]
        for fragment in forbidden_readme_en:
            assert fragment not in readme_en, fragment

        forbidden_readme = [
            "自动配置 FirefoxOptions：proxy / user_dir / fpfile / window_size",
            "自动设置窗口大小",
            "fpfile 保留原始屏幕尺寸",
            "fpfile 保留 width/height",
        ]
        for fragment in forbidden_readme:
            assert fragment not in readme, fragment
