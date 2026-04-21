# -*- coding: utf-8 -*-
"""异步 API 测试 conftest —— 提供异步 fixture。"""

import os
import pytest
import pytest_asyncio

from ruyipage import FirefoxOptions
from ruyipage.aio import launch, AsyncFirefoxPage

from tests.support.test_server import TestServer

TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_PAGES_DIR = os.path.join(TESTS_DIR, "fixtures", "pages")
ENV_FIREFOX_PATH = "RUYIPAGE_TEST_FIREFOX_PATH"


@pytest_asyncio.fixture
async def async_page():
    """创建一个异步页面实例，测试结束时清理。"""
    page = await launch(
        headless=False,
        browser_path=os.environ.get(ENV_FIREFOX_PATH) or None,
    )
    await page.get("about:blank")
    yield page
    try:
        await page.quit()
    except Exception:
        pass


@pytest.fixture
def fixture_page_url():
    """返回把 fixtures/pages 下页面转为 file URL 的 helper。"""

    def _get(name):
        from pathlib import Path

        return (Path(FIXTURE_PAGES_DIR) / name).resolve().as_uri()

    return _get
