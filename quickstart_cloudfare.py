# -*- coding: utf-8 -*-
"""快速开始：访问 Copilot，自动尝试通过 Cloudflare，并打印完整 Cookie。"""

from ruyipage import FirefoxOptions, FirefoxPage, Keys


QUESTION = "你好，今天天气怎么样？"
MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)


def find_input_box(page: FirefoxPage):
    for _ in range(30):
        box = page.ele("css:textarea")
        if not box:
            box = page.ele('css:[contenteditable="true"]')
        if not box:
            box = page.ele("css:.input-area")
        if box:
            return box
        page.wait(1)
    return None


def print_full_cookies(page: FirefoxPage) -> None:
    print("\n" + "=" * 60)
    print("Cloudflare / 页面 Cookie")
    print("=" * 60)

    raw_cookie = page.run_js("return document.cookie") or ""
    print(f"document.cookie: {raw_cookie}")

    cookies = page.get_cookies(all_info=True)
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


opts = FirefoxOptions()
# 某些环境带 --marionette 启动会闪退，示例里显式关闭，
# 仅使用 BiDi Remote Agent 通道。
opts.enable_marionette(False)
# 如果 Firefox 不在默认安装目录，可以取消注释并指定路径。
# opts.set_browser_path(r"D:\Firefox\firefox.exe")

# 如果你想复用登录状态、Cookie、扩展，可以取消注释并指定 userdir。
# opts.set_user_dir(r"D:\ruyipage_userdir")

page = FirefoxPage(opts)

try:
    print("=" * 60)
    print("copilot.microsoft.com Cloudflare 测试")
    print("=" * 60)

    print("\n-> 访问 https://copilot.microsoft.com/ ...")
    page.get("https://copilot.microsoft.com/", wait="none")
    page.wait(5)
    
    print("-> 等待输入框...")
    input_box = find_input_box(page)

    if input_box:
        print("-> 找到输入框，开始输入问题...")
        try:
            input_box.click()
            page.wait(0.8)
            input_box.input(QUESTION, clear=True)
            page.wait(0.8)

            send_btn = page.ele('css:button[aria-label*="Send"]')
            if not send_btn:
                send_btn = page.ele('css:button[type="submit"]')

            if send_btn:
                print("-> 点击发送按钮...")
                send_btn.click()
            else:
                print("-> 按 Enter 发送...")
                page.actions.press(Keys.ENTER).perform()

            print("-> 已发送问题，等待 Cloudflare 触发...")
            page.wait(15)
        except Exception as e:
            print(f"-> 发送失败: {e}")
            page.wait(5)
    else:
        print("-> 未找到输入框，直接等待 Cloudflare...")
        page.wait(5)

    print("\n-> 开始自动处理 Cloudflare...")
    passed = page.handle_cloudflare_challenge(timeout=120, check_interval=2)

    print("\n" + "=" * 60)
    if passed:
        print("SUCCESS: 成功通过 Cloudflare！")
        print_full_cookies(page)
    else:
        print("FAIL: 超时未通过")

    print("\n[+] 保持浏览器打开 500 秒...")
    page.wait(500)

finally:
    page.quit()
