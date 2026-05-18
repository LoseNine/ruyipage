# -*- coding: utf-8 -*-
"""测试滚动后点击精度 — 坐标像素点、元素定位、滚动后再次点击。

开启 action_visual 可视化，验证修复后 human_move 在滚动场景下的准确性。
"""

import pytest


@pytest.fixture
def visual_page(opts_factory, fixture_page_url):
    """创建开启可视化的页面实例。"""
    from ruyipage import FirefoxPage

    opts = opts_factory(headless=False, action_visual=True, window_size=(1560, 900))
    page = FirefoxPage(opts)
    page.get(fixture_page_url("scroll_then_click_target.html"))
    page.wait(0.5)
    yield page
    try:
        page.quit()
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
#  Test 1: 坐标像素点点击测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.feature
def test_pixel_point_click_accuracy(visual_page):
    """验证通过绝对坐标点击，落点精确命中指定像素。"""
    page = visual_page

    # 页面顶部可见区域内的几个精确坐标点
    points = [
        (400, 125),
        (780, 125),
        (200, 315),
        (800, 515),
    ]

    for x, y in points:
        page.run_js("window.__resetClicks()")
        page.actions.human_move((x, y), style="line").human_click().perform()
        page.wait(0.3)

        click = page.run_js("return window.__lastClick")
        assert click is not None, f"坐标 ({x}, {y}) 未产生点击事件"
        assert click["x"] == x, f"X 偏移: 期望 {x}, 实际 {click['x']}"
        assert click["y"] == y, f"Y 偏移: 期望 {y}, 实际 {click['y']}"
        assert click["trusted"] is True, "点击事件 isTrusted 应为 True"


# ════════════════════════════════════════════════════════════════
#  Test 2: 元素定位点击测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.feature
def test_element_locate_and_click(visual_page):
    """验证通过元素定位点击，落点在元素边界内。"""
    page = visual_page

    # 点击页面顶部的两个按钮（无需滚动）
    targets = ["#btn-top-1", "#btn-top-2"]

    for selector in targets:
        page.run_js("window.__resetClicks()")
        btn = page.ele(selector)
        assert btn is not None, f"未找到元素: {selector}"

        page.actions.human_move(btn).human_click().perform()
        page.wait(0.3)

        click = page.run_js("return window.__lastClick")
        expected_id = selector.lstrip("#")
        assert click is not None, f"元素 {selector} 未产生点击事件"
        assert click["target"] == expected_id, f"点击目标错误: 期望 {expected_id}, 实际 {click['target']}"
        assert click["trusted"] is True

        # 验证点击坐标在元素边界内
        loc = btn.rect.viewport_location
        size = btn.rect.size
        assert loc[0] <= click["x"] <= loc[0] + size[0], f"X 超出元素边界"
        assert loc[1] <= click["y"] <= loc[1] + size[1], f"Y 超出元素边界"


# ════════════════════════════════════════════════════════════════
#  Test 3: 滚动后再次点击定位测试（核心回归场景）
# ════════════════════════════════════════════════════════════════


@pytest.mark.feature
def test_scroll_to_bottom_then_click_button(visual_page):
    """验证 scroll.to_bottom() 后，human_move + human_click 仍能精准命中按钮。

    这是本次修复的核心回归测试。页面设置了 scroll-behavior: smooth，
    修复前 100% 复现点击偏移。
    """
    page = visual_page

    # 先滚动到底部
    page.scroll.to_bottom()
    page.wait(0.5)

    # 确认已滚动到底部
    at_bottom = page.run_js(
        "return window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 5"
    )
    assert at_bottom, "未成功滚动到底部"

    # 点击底部按钮
    page.run_js("window.__resetClicks()")
    btn = page.ele("#btn-bot-1")
    assert btn is not None, "未找到底部按钮 #btn-bot-1"

    page.actions.human_move(btn).human_click().perform()
    page.wait(0.3)

    click = page.run_js("return window.__lastClick")
    assert click is not None, "滚动到底部后点击未产生事件"
    assert click["target"] == "btn-bot-1", f"点击目标错误: {click['target']}"
    assert click["trusted"] is True

    # 验证坐标在元素范围内
    loc = btn.rect.viewport_location
    size = btn.rect.size
    assert loc[0] <= click["x"] <= loc[0] + size[0], f"X 超出边界: {click['x']}"
    assert loc[1] <= click["y"] <= loc[1] + size[1], f"Y 超出边界: {click['y']}"


@pytest.mark.feature
def test_scroll_down_then_click_middle_button(visual_page):
    """验证滚动到中间位置后点击中间区域的按钮。"""
    page = visual_page

    # 滚动到页面中部
    page.scroll.to_half()
    page.wait(0.5)

    page.run_js("window.__resetClicks()")
    btn = page.ele("#btn-mid-1")
    assert btn is not None

    page.actions.human_move(btn).human_click().perform()
    page.wait(0.3)

    click = page.run_js("return window.__lastClick")
    assert click is not None, "滚动到中间后点击未产生事件"
    assert click["target"] == "btn-mid-1", f"点击目标错误: {click['target']}"
    assert click["trusted"] is True

    loc = btn.rect.viewport_location
    size = btn.rect.size
    assert loc[0] <= click["x"] <= loc[0] + size[0]
    assert loc[1] <= click["y"] <= loc[1] + size[1]


@pytest.mark.feature
def test_scroll_bottom_then_top_then_click(visual_page):
    """验证先滚到底部再滚回顶部后，点击顶部按钮仍然精准。"""
    page = visual_page

    # 先滚到底部
    page.scroll.to_bottom()
    page.wait(0.3)

    # 再滚回顶部
    page.scroll.to_top()
    page.wait(0.3)

    page.run_js("window.__resetClicks()")
    btn = page.ele("#btn-top-1")
    page.actions.human_move(btn).human_click().perform()
    page.wait(0.3)

    click = page.run_js("return window.__lastClick")
    assert click is not None, "滚动回顶部后点击未产生事件"
    assert click["target"] == "btn-top-1"
    assert click["trusted"] is True

    loc = btn.rect.viewport_location
    size = btn.rect.size
    assert loc[0] <= click["x"] <= loc[0] + size[0]
    assert loc[1] <= click["y"] <= loc[1] + size[1]


@pytest.mark.feature
def test_multiple_scroll_and_click_cycle(visual_page):
    """验证多次滚动+点击循环，每次都精准命中。

    模拟真实使用场景：滚动到不同位置，每次都点击当前区域的按钮。
    """
    page = visual_page

    scenarios = [
        ("#btn-top-1", None),           # 不滚动，点顶部
        ("#btn-bot-1", "bottom"),        # 滚到底部，点底部
        ("#btn-top-2", "top"),           # 滚回顶部，点顶部
        ("#btn-mid-2", "half"),          # 滚到中间，点中间
        ("#btn-bot-2", "bottom"),        # 再滚到底部，点底部
    ]

    for selector, scroll_to in scenarios:
        if scroll_to == "bottom":
            page.scroll.to_bottom()
        elif scroll_to == "top":
            page.scroll.to_top()
        elif scroll_to == "half":
            page.scroll.to_half()
        page.wait(0.4)

        page.run_js("window.__resetClicks()")
        btn = page.ele(selector)
        page.actions.human_move(btn).human_click().perform()
        page.wait(0.3)

        click = page.run_js("return window.__lastClick")
        expected_id = selector.lstrip("#")
        assert click is not None, f"场景 {selector} (scroll={scroll_to}) 未产生点击"
        assert click["target"] == expected_id, (
            f"场景 {selector}: 期望 {expected_id}, 实际 {click['target']}"
        )
        assert click["trusted"] is True

        loc = btn.rect.viewport_location
        size = btn.rect.size
        assert loc[0] <= click["x"] <= loc[0] + size[0], (
            f"场景 {selector}: X={click['x']} 超出 [{loc[0]}, {loc[0]+size[0]}]"
        )
        assert loc[1] <= click["y"] <= loc[1] + size[1], (
            f"场景 {selector}: Y={click['y']} 超出 [{loc[1]}, {loc[1]+size[1]}]"
        )
