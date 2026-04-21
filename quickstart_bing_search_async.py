# -*- coding: utf-8 -*-
"""快速开始（异步版）：Bing 搜索前 3 页。

安装异步依赖：
    pip install ruyiPage[async]

运行：
    python quickstart_bing_search_async.py
"""
import asyncio
from ruyipage.aio import launch, AsyncFirefoxPage, Keys


async def main():
    page = await launch()

    try:
        await page.get("https://cn.bing.com/")
        search_box = await page.ele("#sb_form_q")
        await search_box.input("小肩膀教育")
        await page.actions.press(Keys.ENTER)
        await page.actions.perform()
        await page.wait(3)

        for page_no in range(1, 4):
            print("=" * 80)
            print(f"第 {page_no} 页")
            print("=" * 80)

            items = await page.eles("css:#b_results > li.b_algo")

            for i, item in enumerate(items, 1):
                title_ele = await item.ele("css:h2 a")
                if not title_ele:
                    continue

                title = " ".join((await title_ele.get_text() or "").split())
                url = await title_ele.attr("href") or ""

                desc_ele = await item.ele("css:.b_caption p")
                if desc_ele:
                    content = await desc_ele.get_text()
                else:
                    content = await item.get_text()
                content = " ".join((content or "").split())

                print(f"{i}. {title}")
                print(f"   URL: {url}")
                print(f"   内容: {content}")

            if page_no < 3:
                next_btn = await page.ele("css:a.sb_pagN")
                if not next_btn:
                    break
                await next_btn.click_self()
                await page.wait(2)
    finally:
        await page.quit()


if __name__ == "__main__":
    asyncio.run(main())
