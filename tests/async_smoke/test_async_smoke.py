# -*- coding: utf-8 -*-
"""异步 smoke 测试 —— 镜像同步 smoke 测试。

每个测试与 tests/smoke/ 下的同步版一一对应，
验证异步 API 在真实浏览器场景下行为与同步版一致。

需要 Firefox 可用。运行方式：
    pytest tests/async_smoke/test_async_smoke.py -v --asyncio-mode=auto
"""

import asyncio

import pytest


pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


# ── 启动测试（对标 test_startup.py）────────────────────────────────────────


async def test_async_launch_and_navigate(async_page):
    """对标 test_launch_entry_works：异步启动 + 导航到 about:blank。"""
    url = await async_page.get_url()
    assert url == "about:blank"


async def test_async_page_has_correct_type(async_page):
    """验证返回的对象类型正确。"""
    from ruyipage.aio import AsyncFirefoxPage
    assert isinstance(async_page, AsyncFirefoxPage)


# ── 导航测试（对标 test_navigation.py）────────────────────────────────────


async def test_async_navigation_to_fixture_page(async_page, fixture_page_url):
    """对标 test_navigation_to_local_fixture_page"""
    await async_page.get(fixture_page_url("basic_form.html"))
    title = await async_page.get_title()
    assert title == "Basic Form"

    el = await async_page.ele("#page-title")
    text = await el.get_text()
    assert text == "Basic Form"


async def test_async_navigation_back_forward(async_page, fixture_page_url):
    """对标 test_navigation_back_and_forward（使用本地 fixture 代替 server）"""
    url1 = fixture_page_url("basic_form.html")
    url2 = fixture_page_url("form_controls.html")

    await async_page.get(url1)
    assert (await async_page.get_title()) == "Basic Form"

    await async_page.get(url2)
    assert (await async_page.get_title()) == "Form Controls"

    await async_page.back()
    await async_page.wait.doc_loaded()
    assert (await async_page.get_title()) == "Basic Form"

    await async_page.forward()
    await async_page.wait.doc_loaded()
    assert (await async_page.get_title()) == "Form Controls"


async def test_async_concurrent_navigation_same_page(async_page, fixture_page_url):
    """同一页面对象上的并发导航应串行执行，不应互相打断。"""
    url1 = fixture_page_url("basic_form.html")
    url2 = fixture_page_url("form_controls.html")

    await asyncio.gather(async_page.get(url1), async_page.get(url2))

    title = await async_page.get_title()
    assert title in {"Basic Form", "Form Controls"}


# ── 元素查找与交互（对标 test_click_input.py）──────────────────────────────


async def test_async_click_and_input(async_page, fixture_page_url):
    """对标 test_click_and_input_on_fixture_page"""
    await async_page.get(fixture_page_url("basic_form.html"))

    btn = await async_page.ele("#click-btn")
    await btn.click_self()
    result = await async_page.ele("#click-result")
    text = await result.get_text()
    assert text == "clicked"

    input_el = await async_page.ele("#text-input")
    await input_el.input("hello async")
    mirror_btn = await async_page.ele("#mirror-btn")
    await mirror_btn.click_self()

    value = await input_el.get_value()
    assert value == "hello async"

    mirror = await async_page.ele("#mirror")
    mirror_text = await mirror.get_text()
    assert mirror_text == "hello async"


# ── 表单控件（对标 test_form_controls.py）──────────────────────────────────


async def test_async_form_controls(async_page, fixture_page_url):
    """对标 test_form_controls_behave_correctly"""
    await async_page.get(fixture_page_url("form_controls.html"))

    # 文本输入 + 清除
    text_input = await async_page.ele("#text-input")
    await text_input.input("before")
    await text_input.clear()
    await text_input.input("after")
    assert (await text_input.get_value()) == "after"

    # 其他输入
    email = await async_page.ele("#email-input")
    await email.input("demo@example.com")

    password = await async_page.ele("#password-input")
    await password.input("secret-123")

    textarea = await async_page.ele("#textarea")
    await textarea.input("line1\nline2")

    # Checkbox
    checkbox = await async_page.ele("#checkbox-a")
    await checkbox.click_self()
    assert (await checkbox.get_is_checked()) is True

    # Radio
    radio_a = await async_page.ele("#radio-a")
    radio_b = await async_page.ele("#radio-b")
    await radio_a.click_self()
    assert (await radio_a.get_is_checked()) is True
    await radio_b.click_self()
    assert (await radio_b.get_is_checked()) is True
    assert (await radio_a.get_is_checked()) is False


# ── JS 执行测试 ──────────────────────────────────────────────────────────


async def test_async_run_js(async_page, fixture_page_url):
    """验证异步 JS 执行。"""
    await async_page.get(fixture_page_url("basic_form.html"))
    result = await async_page.run_js("return document.title")
    assert result == "Basic Form"


async def test_async_run_js_with_args(async_page):
    """验证 JS 执行支持参数传递。"""
    result = await async_page.run_js("return arguments[0] + arguments[1]", 3, 4)
    assert result == 7


# ── 截图测试 ──────────────────────────────────────────────────────────────


async def test_async_screenshot_as_bytes(async_page, fixture_page_url):
    """验证截图功能返回 bytes。"""
    await async_page.get(fixture_page_url("basic_form.html"))
    data = await async_page.screenshot(as_bytes="png")
    assert isinstance(data, bytes)
    assert len(data) > 100
    assert data[:4] == b"\x89PNG"


# ── 元素属性测试 ──────────────────────────────────────────────────────────


async def test_async_element_properties(async_page, fixture_page_url):
    """验证元素属性的异步访问。"""
    await async_page.get(fixture_page_url("basic_form.html"))

    el = await async_page.ele("#page-title")
    tag = await el.get_tag()
    assert tag == "h1"

    text = await el.get_text()
    assert text == "Basic Form"

    html = await el.get_html()
    assert "Basic Form" in html


# ── 多元素查找 ────────────────────────────────────────────────────────────


async def test_async_find_multiple_elements(async_page, fixture_page_url):
    """验证 eles() 返回异步元素列表。"""
    await async_page.get(fixture_page_url("form_controls.html"))
    inputs = await async_page.eles("tag:input")
    assert len(inputs) > 0
    for el in inputs:
        from ruyipage.aio import AsyncFirefoxElement
        assert isinstance(el, AsyncFirefoxElement)


# ── NoneElement 行为 ──────────────────────────────────────────────────────


async def test_async_ele_not_found_returns_none_element(async_page):
    """查找不存在的元素返回 AsyncNoneElement。"""
    el = await async_page.ele("#nonexistent-element-12345", timeout=0.5)
    assert not el  # bool(AsyncNoneElement) == False


# ── Unit 代理测试 ─────────────────────────────────────────────────────────


async def test_async_unit_proxy_accessible(async_page):
    """验证 Unit 属性可以正常访问。"""
    from ruyipage._async._generated import AsyncUnitProxy

    scroll = async_page.scroll
    assert isinstance(scroll, AsyncUnitProxy)

    actions = async_page.actions
    assert isinstance(actions, AsyncUnitProxy)

    wait = async_page.wait
    assert isinstance(wait, AsyncUnitProxy)
