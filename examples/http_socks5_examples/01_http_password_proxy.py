# -*- coding: utf-8 -*-
"""HTTP 密码代理示例：fpfile httpauth.*。

用法：
    set RUYIPAGE_HTTP_PROXY_1=REPLACE_WITH_HTTP_USER:REPLACE_WITH_HTTP_PASSWORD@gate.example.com:1288
    python examples/http_socks5_examples/01_http_password_proxy.py --headless

切换代理：
    python examples/http_socks5_examples/01_http_password_proxy.py --proxy-index 2

也可以直接传单条代理：
    python examples/http_socks5_examples/01_http_password_proxy.py --proxy REPLACE_WITH_HTTP_USER:REPLACE_WITH_HTTP_PASSWORD@gate.example.com:1288

说明：
1. 默认使用 ``python -m ruyipage install`` 安装/更新的 runtime 浏览器。
2. 如需手动指定浏览器，传 ``--browser-path`` 或设置 ``RUYIPAGE_FIREFOX_PATH``。
3. HTTP 代理地址和密码认证都写入临时 fpfile，格式如下：
       httpauth.host:<host>
       httpauth.port:<port>
       httpauth.username:<username>
       httpauth.password:<password>
4. 不需要调用 ``opts.set_proxy()``，框架会从 fpfile 读取 HTTP host/port。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from ruyipage import FirefoxOptions, FirefoxPage, resolve_firefox_path  # noqa: E402


TARGET_URL = "http://api.ipify.org"

HTTP_PROXY_ENV_PREFIX = "RUYIPAGE_HTTP_PROXY_"


def parse_http_proxy(value: str) -> dict[str, str | int]:
    auth, server = value.rsplit("@", 1)
    username, password = auth.split(":", 1)
    host, port = server.rsplit(":", 1)
    return {
        "host": host,
        "port": int(port),
        "username": username,
        "password": password,
    }


def write_httpauth_fpfile(
    path: str, host: str, port: int, username: str, password: str
) -> None:
    # ruyipage 读取 host/port 写入 profile；指纹内核读取 username/password。
    with open(path, "w", encoding="utf-8") as f:
        f.write("httpauth.host:{}\n".format(host))
        f.write("httpauth.port:{}\n".format(port))
        f.write("httpauth.username:{}\n".format(username))
        f.write("httpauth.password:{}\n".format(password))


def read_page_text(page: FirefoxPage) -> str:
    try:
        page.wait.doc_loaded(timeout=30)
    except Exception:
        pass
    page.wait(1)
    return (page.run_js("return document.body ? document.body.innerText : ''") or "").strip()


def choose_browser_path(value: str | None) -> str | None:
    # value 为空时 resolve_firefox_path(None) 会优先返回已安装/更新的 ruyipage runtime。
    return resolve_firefox_path(value)


def load_http_proxies() -> list[str]:
    proxies = []
    for index in range(1, 21):
        value = os.getenv("{}{}".format(HTTP_PROXY_ENV_PREFIX, index), "").strip()
        if value:
            proxies.append(value)
    return proxies


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ruyiPage HTTP password proxy example")
    parser.add_argument("--proxy", help="username:password@host:port")
    parser.add_argument("--proxy-index", type=int, default=1, help="select RUYIPAGE_HTTP_PROXY_N")
    parser.add_argument("--target", default=TARGET_URL)
    parser.add_argument("--browser-path", default=os.getenv("RUYIPAGE_FIREFOX_PATH"))
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    proxies = [args.proxy] if args.proxy else load_http_proxies()
    if not proxies:
        raise SystemExit(
            "请通过 --proxy 或 RUYIPAGE_HTTP_PROXY_1 提供 HTTP 代理，格式 username:password@host:port"
        )
    if args.proxy_index < 1 or args.proxy_index > len(proxies):
        raise SystemExit("--proxy-index must be 1..{}".format(len(proxies)))

    proxy = parse_http_proxy(proxies[args.proxy_index - 1])
    fpfile = tempfile.NamedTemporaryFile(
        "w", suffix="-ruyipage-httpauth.txt", delete=False, encoding="utf-8"
    )
    fpfile.close()
    profile_dir = tempfile.mkdtemp(prefix="ruyipage-http-proxy-")
    page = None

    try:
        write_httpauth_fpfile(
            fpfile.name,
            proxy["host"],
            proxy["port"],
            proxy["username"],
            proxy["password"],
        )

        opts = FirefoxOptions()
        browser_path = choose_browser_path(args.browser_path)
        if browser_path:
            opts.set_browser_path(browser_path)
        opts.set_auto_port(True)
        opts.set_user_dir(profile_dir)
        opts.set_fpfile(fpfile.name)
        opts.set_window_size(900, 700)
        opts.headless(args.headless)

        page = FirefoxPage(opts)
        page.get(args.target)
        body_text = read_page_text(page)
        if not body_text:
            raise RuntimeError("empty response; proxy authentication may have failed")

        print("proxy=http://{}:{}".format(proxy["host"], proxy["port"]))
        print("username={}".format(proxy["username"]))
        print("target={}".format(args.target))
        print("exit_ip={}".format(body_text))
        return 0
    finally:
        if page is not None:
            page.quit()
        try:
            os.remove(fpfile.name)
        except OSError:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
