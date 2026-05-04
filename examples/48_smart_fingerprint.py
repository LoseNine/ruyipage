# -*- coding: utf-8 -*-
"""
示例 48: 一站式智能指纹
========================

使用说明
--------
1. 修改下方 BROWSER_PATH 为你本机 Firefox 路径
2. 修改代理参数（无代理则把 PROXY_HOST 改为 None）
3. 直接运行本文件即可

功能概述
--------
调用 opts.smart_fingerprint(...) 自动完成以下全部步骤：

  1) 通过代理探测出口 IP → 国家 / 时区 / 经纬度（5 数据源自动回退）
  2) 可选 IPv6 探测（失败则省略，绝不伪造）
  3) 自动匹配该国的语言 / Accept-Language / 微软语音配置
  4) 随机抽取 22 套 Windows 真机硬件特征之一
  5) 拼装 Firefox 151 ±2 UA + 随机 canvas 种子
  6) 写出 fpfile.txt（内核 key:value 格式，原子写入）
  7) 自动配置 FirefoxOptions：proxy / user_dir / fpfile / window_size

之后只需 FirefoxPage(opts) 启动浏览器即可；
可选再调用 ctx.apply_emulation(page) 叠加 BiDi 仿真覆盖。

常见用法
--------
  A) 无代理直连：
     PROXY_HOST = None
     REQUIRE_COUNTRY = None

  B) 本地 Clash / V2Ray（无密码）：
     PROXY_HOST = "127.0.0.1"
     PROXY_PORT = 7890

  C) 远端鉴权代理：
     PROXY_HOST = "proxy.example.com"
     PROXY_PORT = 8080
     PROXY_USER = "username"
     PROXY_PWD  = "password"
     REQUIRE_COUNTRY = "US"    # 建议开启国家校验

  D) 复用既有 userdir（保持登录态）：
     ctx = opts.smart_fingerprint(userdir="D:/profiles/acc_1", ...)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ruyipage import (
    FirefoxOptions,
    FirefoxPage,
    CountryMismatchError,
    GeoError,
)


# =============================================================
# ↓↓↓ 修改这里 ↓↓↓
# =============================================================

# Firefox 可执行文件路径
BROWSER_PATH = r"C:\Program Files\Mozilla Firefox\firefox.exe"

# 代理配置（直连就把 PROXY_HOST 改成 None）
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7890
PROXY_USER = None       # 无密码代理留 None
PROXY_PWD = None

# 出口 IP 国家校验（None = 不校验）
REQUIRE_COUNTRY = None

# =============================================================
# ↑↑↑ 修改到这里 ↑↑↑
# =============================================================


def main():
    opts = FirefoxOptions()
    opts.set_browser_path(BROWSER_PATH)
    opts.set_port(9222)

    # ---- 一行搞定所有指纹配置 ----
    try:
        ctx = opts.smart_fingerprint(
            proxy_host=PROXY_HOST,
            proxy_port=PROXY_PORT,
            proxy_user=PROXY_USER,
            proxy_pwd=PROXY_PWD,
            require_country=REQUIRE_COUNTRY,
            logger=print,
        )
    except CountryMismatchError as e:
        # 出口 IP 国家与 require_country 不符
        print("[FAIL] 国家不匹配: 实际={} 期望={}".format(e.actual, e.required))
        return
    except GeoError as e:
        # 5 个 geo 数据源全部失败
        print("[FAIL] geo 探测全部失败: {}".format(e))
        return

    # 打印摘要（单行日志）
    print(ctx.summary())

    # ---- 启动浏览器 ----
    page = FirefoxPage(opts)
    try:
        # 可选：叠加 BiDi 仿真覆盖（geolocation/locale/timezone/headers）
        ctx.apply_emulation(page, logger=print)

        # ---- 你的业务逻辑写在这里 ----
        page.get("https://www.google.com")
        print("title:", page.title)

    finally:
        page.quit()


if __name__ == "__main__":
    main()
