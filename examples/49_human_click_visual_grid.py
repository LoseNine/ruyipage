# -*- coding: utf-8 -*-
"""Example 49: human_move + human_click visual grid.

打开 action_visual=True，按顺序点击 12 个分散目标，
用于肉眼观察最终停点、轨迹和点击命中是否一致。
"""

from pathlib import Path

from ruyipage import launch


TARGET_IDS = ["target-{}".format(i) for i in range(1, 13)]


def main():
    html_path = (
        Path(__file__).resolve().parent.parent
        / "tests"
        / "fixtures"
        / "pages"
        / "human_move_visual_grid.html"
    )

    page = launch(
        action_visual=True,
        headless=False,
        port=9349,
        window_size=(1560, 980),
    )

    try:
        page.get(html_path.resolve().as_uri())
        page.wait(1)
        page.run_js("window.__resetClicks()")

        print("=" * 68)
        print("  ruyiPage - Human click visual grid")
        print("=" * 68)
        print("观察点:")
        print("1. 红点最终停留是否落在按钮中心")
        print("2. 绿色点击动画是否与按钮命中一致")
        print("3. 滚动后的远端按钮是否仍然准确命中")

        for index, target_id in enumerate(TARGET_IDS, 1):
            ele = page.ele("#" + target_id)
            midpoint_before = ele.rect.viewport_midpoint
            scroll_before = page.rect.scroll_position
            print(
                "[{}/12] {} before midpoint={} scroll={}".format(
                    index, target_id, midpoint_before, scroll_before
                )
            )
            page.actions.human_move(ele).human_click().perform()
            page.wait(0.55)
            click = page.run_js("return window.__lastClick")
            midpoint_after = ele.rect.viewport_midpoint
            scroll_after = page.rect.scroll_position
            print(
                "       click={} after midpoint={} scroll={}".format(
                    click, midpoint_after, scroll_after
                )
            )

        print("\n命中列表:", page.run_js("return window.__hitTargets"))
        print("总点击数:", len(page.run_js("return window.__clicks")))

        try:
            input("按 Enter 关闭浏览器...\n")
        except EOFError:
            page.wait(2)
    finally:
        page.quit()


if __name__ == "__main__":
    main()
