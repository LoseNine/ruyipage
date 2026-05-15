# -*- coding: utf-8 -*-
"""Example 50: click 10 exact viewport coordinates with action_visual."""

from pathlib import Path

from ruyipage import launch


POINTS = [
    (140, 110),
    (360, 160),
    (620, 120),
    (920, 180),
    (1210, 130),
    (260, 410),
    (560, 520),
    (860, 430),
    (1120, 560),
    (1320, 360),
]


def main():
    html_path = (
        Path(__file__).resolve().parent.parent
        / "tests"
        / "fixtures"
        / "pages"
        / "human_move_visual_points.html"
    )

    page = launch(
        action_visual=True,
        headless=False,
        port=9350,
        window_size=(1560, 980),
    )

    try:
        page.get(html_path.resolve().as_uri())
        page.wait(1)
        page.run_js("window.__resetClicks()")

        print("=" * 68)
        print("  ruyiPage - Human click exact points")
        print("=" * 68)

        for index, (x, y) in enumerate(POINTS, 1):
            print("[{}/10] click point ({}, {})".format(index, x, y))
            page.actions.human_move((x, y), style="line").human_click().perform()
            page.wait(0.55)
            click = page.run_js("return window.__lastClick")
            print("       lastClick={}".format(click))

        print("\n命中像素点:", page.run_js("return window.__hitPoints"))
        print("总点击数:", len(page.run_js("return window.__clicks")))

        try:
            input("按 Enter 关闭浏览器...\n")
        except EOFError:
            page.wait(2)
    finally:
        page.quit()


if __name__ == "__main__":
    main()
