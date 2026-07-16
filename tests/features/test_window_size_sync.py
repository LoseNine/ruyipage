# -*- coding: utf-8 -*-
"""Regression tests for natural runtime window sizing."""

import pytest


@pytest.mark.feature
def test_page_set_window_size_keeps_browser_viewport_natural(page):
    page.get("about:blank")
    before = page.run_js(
        """
        return {
          screen: {w: screen.width, h: screen.height}
        };
        """,
        as_expr=False,
    )

    page.set_window_size(960, 640)
    page.wait(0.3)

    metrics = page.run_js(
        """
        return {
          outer: {w: window.outerWidth, h: window.outerHeight},
          inner: {w: window.innerWidth, h: window.innerHeight},
          screen: {w: screen.width, h: screen.height}
        };
        """,
        as_expr=False,
    )

    assert metrics["outer"] == {"w": 960, "h": 640}
    assert 0 < metrics["inner"]["w"] <= metrics["outer"]["w"]
    assert 0 < metrics["inner"]["h"] <= metrics["outer"]["h"]
    assert metrics["inner"] != metrics["outer"]
    assert metrics["screen"] == before["screen"]
    assert page.rect.window_size == (960, 640)
    assert page.rect.viewport_size == (
        metrics["inner"]["w"],
        metrics["inner"]["h"],
    )
