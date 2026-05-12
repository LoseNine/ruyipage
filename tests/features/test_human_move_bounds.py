# -*- coding: utf-8 -*-

import pytest


def _setup_small_viewport(page, fixture_page_url):
    page.set.viewport(160, 120)
    page.get(fixture_page_url("small_viewport_target.html"))
    page.run_js("window.__lastClick = null; window.__hitTarget = false;")


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
