# -*- coding: utf-8 -*-
"""监听 Google Search 指定请求并直接读取响应文本。

场景：
1. 打开固定的 Google 图片搜索页面。
2. 监听 URL 以 ``https://www.google.com/search?vet=`` 开头的响应包。
3. 向下滚动触发更多请求。
4. 直接通过 ``packet.text`` / ``packet.response_body`` 获取响应文本。
"""

import io
import os
import sys
import time


if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ruyipage import FirefoxOptions, FirefoxPage


SEARCH_URL = (
    "https://www.google.com/search?q=ruyi&sca_esv=063f69fd4600a8b2"
    "&hl=zh-cn&sxsrf=ANbL-n4K3M8chab-JWQBEmWMKFiKge0_bg:1778043786291"
    "&source=hp&biw=1707&bih=825&ei=isv6aeauD_Sm5NoP_f-R4Ak"
    "&iflsig=AFdpzrgAAAAAafrZmgc5hYFsJIPKlGb8HoF5xTCgCleb"
    "&ved=0ahUKEwjmm5mi8aOUAxV0E1kFHf1_BJwQ4dUDCBc&uact=5"
    "&oq=ruyi&gs_lp=EgNpbWciBHJ1eWlI7QVQAFj3A3AAeACQAQCYAQCgAQCqAQC4AQPIAQD4AQGKAgtnd3Mtd2l6LWltZ5gCAKACAJgDAJIHAKAHALIHALgHAMIHAMgHAIAIAQ&sclient=img&udm=2"
)
TARGET_PREFIX = "https://www.google.com/search?vet="


def print_packet(packet, label):
    print(f"[{label}] status={packet.status} method={packet.method}")
    print(f"[{label}] url={packet.url}")
    text = packet.text
    if text:
        print(f"[{label}] text preview (first 1200 chars):")
        print(text[:1200])
        return True
    print(f"[{label}] no readable text on this packet")
    return False


def main() -> None:
    opts = FirefoxOptions()
    page = FirefoxPage(opts)

    try:
        print("=" * 72)
        print("Google Search response listen")
        print("=" * 72)
        print("TARGET_PREFIX:", TARGET_PREFIX)

        page.listen.start(TARGET_PREFIX)

        print("\n[1] 打开搜索页...")
        page.get(SEARCH_URL, wait="none")
        page.wait(3)

        print("[2] 尝试读取初始命中包...")
        first = page.listen.wait(timeout=8)
        if first:
            print_packet(first, "initial")
        else:
            print("[initial] no packet captured yet")

        print("\n[3] 向下滚动，等待 /search?vet= 请求...")
        hit = None
        for i in range(8):
            page.actions.scroll(0, 1400).perform()
            time.sleep(1.2)

            packet = page.listen.wait(timeout=3)
            if not packet:
                print(f"[scroll-{i + 1}] no packet captured")
                continue

            if print_packet(packet, f"scroll-{i + 1}"):
                hit = packet
                break

        print("\n[4] 结果")
        if hit:
            print("SUCCESS: 已捕获到带文本的 /search?vet= 响应")
        else:
            print("FAIL: 捕获到的包都没有可读文本，或未命中目标包")

        print("\n[5] 当前累计命中包数:", len(page.listen.steps))

    finally:
        try:
            page.listen.stop()
        except Exception:
            pass
        page.quit()


if __name__ == "__main__":
    main()
