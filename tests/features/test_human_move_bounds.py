# -*- coding: utf-8 -*-

import pytest


def _setup_small_viewport(page, fixture_page_url):
    page.set.viewport(160, 120)
    page.get(fixture_page_url("small_viewport_target.html"))
    page.run_js("window.__lastClick = null; window.__hitTarget = false;")


def _setup_scroll_target(page, fixture_page_url):
    page.set.viewport(1536, 723)
    page.get(fixture_page_url("human_move_scroll_target.html"))
    page.run_js("window.__resetClicks()")


@pytest.mark.feature
def test_human_click_small_viewport_edge_target(page, fixture_page_url):
    _setup_small_viewport(page, fixture_page_url)

    target = page.ele("#target")
    page.actions.human_move(target, style="arc").human_click().perform()
    page.wait(0.3)

    assert page.run_js("return window.__hitTarget") is True
    rec = page.run_js("return window.__lastClick")
    assert rec is not None
    assert rec["trusted"] is True


@pytest.mark.feature
def test_repeated_human_click_small_viewport_no_out_of_bounds(page, fixture_page_url):
    _setup_small_viewport(page, fixture_page_url)

    target = page.ele("#target")
    for _ in range(10):
        page.run_js("window.__lastClick = null; window.__hitTarget = false;")
        page.actions.human_move(target).human_click().perform()
        page.wait(0.1)
        assert page.run_js("return window.__hitTarget") is True


@pytest.mark.feature
def test_human_click_inside_tiny_iframe_no_out_of_bounds(page, fixture_page_url):
    _setup_small_viewport(page, fixture_page_url)

    frm = page.get_frame("#tiny-frame")
    btn = frm.ele("#inner-btn")
    frm.actions.human_move(btn).human_click().perform()
    frm.wait(0.2)

    assert frm.run_js("return window.__innerHit") is True


@pytest.mark.feature
def test_human_move_recomputes_target_after_scroll(page, fixture_page_url):
    _setup_scroll_target(page, fixture_page_url)

    target = page.ele("#target")
    initial_midpoint = target.rect.viewport_midpoint
    viewport_width, viewport_height = page.rect.viewport_size

    assert initial_midpoint[1] > viewport_height

    page.actions.human_move(target).human_click().perform()
    page.wait(0.3)

    click = page.run_js("return window.__lastClick")
    rect = target.rect.viewport_location
    width, height = target.rect.size

    assert page.run_js("return window.__hitTarget") is True
    assert click is not None
    assert click["target"] == "target"
    assert click["trusted"] is True
    assert rect[0] <= click["x"] <= rect[0] + width
    assert rect[1] <= click["y"] <= rect[1] + height
    assert 0 <= click["x"] < viewport_width
    assert 0 <= click["y"] < viewport_height


@pytest.mark.feature
def test_human_click_visual_grid_hits_many_targets(page, fixture_page_url):
    page.set.viewport(1560, 900)
    page.get(fixture_page_url("human_move_visual_grid.html"))
    page.run_js("window.__resetClicks()")

    target_ids = ["target-{}".format(i) for i in range(1, 13)]

    for target_id in target_ids:
        target = page.ele("#" + target_id)
        page.actions.human_move(target).human_click().perform()
        page.wait(0.12)

        click = page.run_js("return window.__lastClick")
        rect = target.rect.viewport_location
        width, height = target.rect.size
        assert click is not None
        assert click["target"] == target_id
        assert click["trusted"] is True
        assert rect[0] <= click["x"] <= rect[0] + width
        assert rect[1] <= click["y"] <= rect[1] + height

    assert page.run_js("return window.__hitTargets") == target_ids
    assert len(page.run_js("return window.__clicks")) == len(target_ids)


@pytest.mark.feature
def test_human_move_absolute_points_land_exactly(page, fixture_page_url):
    page.set.viewport(1560, 900)
    page.get(fixture_page_url("human_move_visual_grid.html"))

    points = [
        (172, 149),
        (502, 209),
        (852, 159),
        (1202, 239),
        (262, 589),
        (692, 719),
        (1122, 639),
        (344, 338),
        (822, 246),
        (1268, 311),
    ]

    for x, y in points:
        page.run_js("window.__resetClicks()")
        page.actions.human_move((x, y), style="line").human_click().perform()
        page.wait(0.12)

        click = page.run_js("return window.__lastClick")
        assert click is not None
        assert click["x"] == x
        assert click["y"] == y
