# -*- coding: utf-8 -*-
"""快速开始（异步版）：访问 Copilot，自动尝试通过 Cloudflare，并打印完整 Cookie。

安装异步依赖：
    pip install ruyiPage[async]

运行：
    python quickstart_cloudflare_async.py
"""

import asyncio
from ruyipage.aio import launch, AsyncFirefoxPage, Keys


QUESTION = "你好，今天天气怎么样？"


async def find_input_box(page: AsyncFirefoxPage):
    for _ in range(30):
        box = await page.ele("css:textarea")
        if not box:
            box = await page.ele('css:[contenteditable="true"]')
        if not box:
            box = await page.ele("css:.input-area")
        if box:
            return box
        await page.wait(1)
    return None


async def print_full_cookies(page: AsyncFirefoxPage):
    print("\n" + "=" * 60)
    print("Cloudflare / 页面 Cookie")
    print("=" * 60)

    raw_cookie = await page.run_js("return document.cookie") or ""
    print(f"document.cookie: {raw_cookie}")

    cookies = await page.get_cookies(all_info=True)
    print(f"Cookie 数量: {len(cookies)}")
    for i, cookie in enumerate(cookies, 1):
        print(f"[{i}] name={cookie.name}")
        print(f"    value={cookie.value}")
        print(f"    domain={cookie.domain}")
        print(f"    path={cookie.path}")
        print(f"    httpOnly={cookie.http_only}")
        print(f"    secure={cookie.secure}")
        print(f"    sameSite={cookie.same_site}")
        print(f"    expiry={cookie.expiry}")


async def main():
    page = await launch()

    try:
        print("=" * 60)
        print("copilot.microsoft.com Cloudflare 测试（异步版）")
        print("=" * 60)

        print("\n-> 访问 https://copilot.microsoft.com/ ...")
        await page.get("https://copilot.microsoft.com/", wait="none")
        await page.wait(5)

        print("-> 等待输入框...")
        input_box = await find_input_box(page)

        if input_box:
            print("-> 找到输入框，开始输入问题...")
            try:
                await input_box.click_self()
                await page.wait(0.8)
                await input_box.input(QUESTION, clear=True)
                await page.wait(0.8)

                send_btn = await page.ele('css:button[aria-label*="Send"]')
                if not send_btn:
                    send_btn = await page.ele('css:button[type="submit"]')

                if send_btn:
                    print("-> 点击发送按钮...")
                    await send_btn.click_self()
                else:
                    print("-> 按 Enter 发送...")
                    await page.actions.press(Keys.ENTER)
                    await page.actions.perform()

                print("-> 已发送问题，等待 Cloudflare 触发...")
                await page.wait(15)
            except Exception as e:
                print(f"-> 发送失败: {e}")
                await page.wait(5)
        else:
            print("-> 未找到输入框，直接等待 Cloudflare...")
            await page.wait(5)

        print("\n-> 开始自动处理 Cloudflare...")
        passed = await page.handle_cloudflare_challenge(timeout=120, check_interval=2)

        print("\n" + "=" * 60)
        if passed:
            print("SUCCESS: 成功通过 Cloudflare！")
            await print_full_cookies(page)
        else:
            print("TIMEOUT: 超时未通过")

        print("\n[+] 保持浏览器打开 30 秒...")
        await page.wait(30)

    finally:
        await page.quit()


if __name__ == "__main__":
    asyncio.run(main())
