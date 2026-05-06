# -*- coding: utf-8 -*-
"""Example 40_1: Request Header Capture

Use a real tracking page to inspect request headers and POST payloads from a
scraper/data-capture perspective.

This script is intentionally diagnostic:
- It does not assume the page URL is different from the API URL.
- It does not fail matching requests by default.
- It prints intercepted POST request headers and body previews so the real
  request shape can be identified quickly.
"""

import io
import os
import sys
from typing import List, Tuple


if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import logging

logger = logging.getLogger(__name__)

from ruyipage import FirefoxPage, InterceptedRequest


TARGET_URL = "https://www.posindonesia.co.id/en/tracking/LZ027513746CN"


def main() -> None:
    print("=" * 70)
    print("Example 40_1: Request Header Capture")
    print("=" * 70)

    page = FirefoxPage()
    captured_posts: List[Tuple[str, dict, str]] = []

    try:
        page.get("about:blank")

        def my_request_handler(req: InterceptedRequest) -> None:
            try:
                if req.method == "POST":
                    body = req.body or ""
                    captured_posts.append((req.url, req.headers, body))
                    print("\n[POST]")
                    print(f"url: {req.url}")
                    print(f"headers: {req.headers}")
                    print(f"body: {body[:500]}")
                req.continue_request()
            except Exception as e:
                logger.error(e)
                if not req.handled:
                    req.continue_request()

        page.intercept.start_requests(my_request_handler)
        page.get(TARGET_URL, wait="none")
        page.wait(3)

        try:
            passed = page.handle_cloudflare_challenge(timeout=20, check_interval=2)
            print(f"cloudflare passed: {passed}")
        except Exception as e:
            print(f"cloudflare handler error: {e}")

        page.wait(20)

        print("\n" + "=" * 70)
        print(f"captured POST count: {len(captured_posts)}")
        for index, item in enumerate(captured_posts[:10], start=1):
            url, headers, body = item
            print(f"{index}. {url}")
            print(f"   content-type: {headers.get('Content-Type')}")
            print(f"   origin: {headers.get('Origin')}")
            print(f"   body preview: {body[:200]}")
        print("=" * 70)

    finally:
        try:
            page.intercept.stop()
        except Exception:
            pass
        try:
            page.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
