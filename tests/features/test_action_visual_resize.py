# -*- coding: utf-8 -*-
"""Regression tests for action_visual after browser window resize."""

import pytest


@pytest.mark.feature
def test_action_visual_clears_stale_cursor_after_window_resize(
    opts_factory, fixture_page_url
):
    from ruyipage import FirefoxPage

    opts = opts_factory(headless=False, action_visual=True, window_size=(1560, 900))
    page = FirefoxPage(opts)
    try:
        page.get(fixture_page_url("human_move_visual_grid.html"))
        page.wait(0.5)

        page.actions.human_move(page.ele("#target-4"), style="line").human_click().perform()
        page.wait(0.5)

        before = page.run_js(
            """
            const dot = document.getElementById('__ruyi_av_dot__');
            const coord = document.getElementById('__ruyi_av_coord__');
            return {
              dotDisplay: dot ? dot.style.display : null,
              coordDisplay: coord ? coord.style.display : null
            };
            """,
            as_expr=False,
        )
        assert before == {"dotDisplay": "block", "coordDisplay": "block"}

        page.window.set_size(900, 680)
        page.wait(0.5)

        after = page.run_js(
            """
            const canvas = document.getElementById('__ruyi_av_canvas__');
            const dot = document.getElementById('__ruyi_av_dot__');
            const coord = document.getElementById('__ruyi_av_coord__');
            return {
              innerWidth: window.innerWidth,
              innerHeight: window.innerHeight,
              canvasWidth: canvas ? canvas.width : null,
              canvasHeight: canvas ? canvas.height : null,
              dotDisplay: dot ? dot.style.display : null,
              coordDisplay: coord ? coord.style.display : null
            };
            """,
            as_expr=False,
        )

        assert after["canvasWidth"] == after["innerWidth"]
        assert after["canvasHeight"] == after["innerHeight"]
        assert after["dotDisplay"] == "none"
        assert after["coordDisplay"] == "none"

        page.run_js("window.__resetClicks()")
        page.actions.human_move(page.ele("#target-1"), style="line").human_click().perform()
        page.wait(0.5)

        rendered = page.run_js(
            """
            const dot = document.getElementById('__ruyi_av_dot__');
            const left = dot ? Math.round(parseFloat(dot.style.left)) : null;
            const top = dot ? Math.round(parseFloat(dot.style.top)) : null;
            return {
              dotDisplay: dot ? dot.style.display : null,
              dotLeft: left,
              dotTop: top,
              click: window.__lastClick
            };
            """,
            as_expr=False,
        )

        assert rendered["dotDisplay"] == "block"
        assert rendered["click"]["target"] == "target-1"
        assert rendered["dotLeft"] == rendered["click"]["x"]
        assert rendered["dotTop"] == rendered["click"]["y"]
    finally:
        page.quit()
