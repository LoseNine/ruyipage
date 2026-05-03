# -*- coding: utf-8 -*-
"""actions 动作链回归测试。

聚焦 1.2.5 修复的两个 bug，避免回潮：

1. ``move_to(offset_x=, offset_y=)`` 不传 ``ele_or_loc`` 时，
   offset 会累加到上次指针位置 → 第二次起越界 / 错位。
2. ``perform()`` 抛异常后动作队列没清空 → 下一次 perform()
   会带着上次失败的越界动作再次失败。

通过固定 viewport(1280x900) 把 target 放在 (400,600)-(480,680)，
测试点 (421, 637) 永远落在 target 上。
"""

import pytest

from ruyipage.errors import BiDiError


X, Y = 421, 637


def _setup(page, fixture_page_url):
    page.set.viewport(1280, 900)
    page.get(fixture_page_url("fixed_point_target.html"))
    page.run_js("window.__resetClicks()")


def _last_click(page):
    return page.run_js("return window.__lastClick")


def _hit_target(page):
    return page.run_js("return window.__hitTarget")


# ════════════════════════════════════════════════════════════════
#  绝对坐标点击
# ════════════════════════════════════════════════════════════════


@pytest.mark.feature
def test_move_to_absolute_tuple(page, fixture_page_url):
    _setup(page, fixture_page_url)

    page.actions.move_to((X, Y)).click().perform()
    page.wait(0.3)

    rec = _last_click(page)
    assert rec is not None
    assert rec["x"] == X and rec["y"] == Y
    assert rec["target"] == "target"
    assert rec["trusted"] is True
    assert _hit_target(page) is True


@pytest.mark.feature
def test_move_to_absolute_dict(page, fixture_page_url):
    _setup(page, fixture_page_url)

    page.actions.move_to({"x": X, "y": Y}).click().perform()
    page.wait(0.3)

    rec = _last_click(page)
    assert rec is not None
    assert rec["x"] == X and rec["y"] == Y
    assert rec["target"] == "target"


# ════════════════════════════════════════════════════════════════
#  offset 形式 - 核心回归
# ════════════════════════════════════════════════════════════════


@pytest.mark.feature
def test_move_to_offset_first_call_hits(page, fixture_page_url):
    _setup(page, fixture_page_url)

    page.actions.move_to(offset_x=X, offset_y=Y).click().perform()
    page.wait(0.3)

    rec = _last_click(page)
    assert rec is not None
    assert rec["x"] == X and rec["y"] == Y
    assert rec["target"] == "target"


@pytest.mark.feature
def test_move_to_offset_no_accumulation(page, fixture_page_url):
    """核心回归 1：连续多次 move_to(offset_x=, offset_y=) 不能累加。

    1.2.5 之前第 2 次会跑到 (842, 1274) 抛 BiDiError。
    """
    _setup(page, fixture_page_url)

    for _ in range(3):
        page.actions.move_to(offset_x=X, offset_y=Y).click().perform()
        page.wait(0.2)

    clicks = page.run_js("return window.__clicks")
    assert len(clicks) == 3
    for rec in clicks:
        assert rec["x"] == X and rec["y"] == Y
        assert rec["target"] == "target"

    # 内部状态：curr_x/curr_y 应稳定在 (X, Y)，而不是叠加成 3*X / 3*Y
    assert page.actions.curr_x == X
    assert page.actions.curr_y == Y


@pytest.mark.feature
def test_move_to_origin_zero_plus_offset(page, fixture_page_url):
    _setup(page, fixture_page_url)

    page.actions.move_to((0, 0), offset_x=X, offset_y=Y).click().perform()
    page.wait(0.3)

    rec = _last_click(page)
    assert rec is not None
    assert rec["x"] == X and rec["y"] == Y


# ════════════════════════════════════════════════════════════════
#  perform() 队列清理 - 核心回归
# ════════════════════════════════════════════════════════════════


@pytest.mark.feature
def test_perform_clears_queue_on_error(page, fixture_page_url):
    """核心回归 2：一次失败的 perform() 不能污染下一次。

    1.2.5 之前 perform() 抛异常时未清队列，下一次 perform()
    会重发越界 pointerMove，再次抛 BiDiError，永远恢复不了。
    """
    _setup(page, fixture_page_url)

    # 故意制造越界
    with pytest.raises(BiDiError):
        page.actions.move_to((10000, 10000)).click().perform()

    # 失败后队列必须为空
    assert page.actions._pointer_actions == []
    assert page.actions._key_actions == []
    assert page.actions._wheel_actions == []

    # 紧接一次正常调用，应该能成功命中
    page.actions.move_to((X, Y)).click().perform()
    page.wait(0.3)

    rec = _last_click(page)
    assert rec is not None
    assert rec["x"] == X and rec["y"] == Y
    assert rec["target"] == "target"


@pytest.mark.feature
def test_perform_clears_queue_on_success(page, fixture_page_url):
    _setup(page, fixture_page_url)

    page.actions.move_to((X, Y)).click().perform()
    page.wait(0.2)

    assert page.actions._pointer_actions == []
    assert page.actions._key_actions == []
    assert page.actions._wheel_actions == []


@pytest.mark.feature
def test_perform_clears_queue_after_exception_whitebox(page, fixture_page_url):
    """白盒：异常路径下三个动作通道都必须清空。"""
    _setup(page, fixture_page_url)

    try:
        page.actions.move_to((10000, 10000)).click().perform()
    except BiDiError:
        pass

    assert page.actions._pointer_actions == []
    assert page.actions._key_actions == []
    assert page.actions._wheel_actions == []
