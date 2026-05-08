# -*- coding: utf-8 -*-
"""使用 ruyiPage 通过 BiDi 点击百度搜索框、输入关键词并回车。

说明：
- 打开百度首页。
- 点击搜索框。
- 使用 BiDi 键盘动作清空已有内容。
- 使用 BiDi 键盘输入关键词。
- 使用 BiDi actions 发送 Enter。
"""

import io
import sys


if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


from ruyipage import FirefoxOptions, FirefoxPage, Keys


SEARCH_URL = "https://www.baidu.com/"
KEYWORD = "ruyipage"


def main() -> None:
    opts = FirefoxOptions()
    page = FirefoxPage(opts)

    try:
        print("=" * 60)
        print("百度搜索示例（BiDi）")
        print("=" * 60)

        print(f"打开页面: {SEARCH_URL}")
        page.get(SEARCH_URL)
        page.wait(1)

        search_box = page.ele("#kw")
        if not search_box:
            raise RuntimeError("未找到百度搜索框 #kw")

        print("点击搜索框")
        search_box.click()
        page.wait(0.5)

        print("清空已有内容（BiDi Ctrl+A/Delete）")
        page.actions.key_down(Keys.CONTROL).press("a").key_up(Keys.CONTROL).press(Keys.DELETE).perform()
        page.wait(0.5)

        print(f"输入关键词: {KEYWORD}")
        search_box.input(KEYWORD, clear=False)
        page.wait(0.5)

        print("按 Enter 发起搜索")
        page.actions.press(Keys.ENTER).perform()
        page.wait(2)

        print("当前标题:", page.title)
        print("当前 URL:", page.url)

    finally:
        page.wait(1)
        page.quit()


if __name__ == "__main__":
    main()
