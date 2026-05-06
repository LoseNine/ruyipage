# -*- coding: utf-8 -*-
"""pytest 全局 fixture 与 marker 注册。"""

from pathlib import Path
import os
import tempfile
import shutil

import pytest

from ruyipage import FirefoxOptions, FirefoxPage, launch

from tests.support.test_server import TestServer


TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_PAGES_DIR = TESTS_DIR / "fixtures" / "pages"
ENV_FIREFOX_PATH = "RUYIPAGE_TEST_FIREFOX_PATH"


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: 最核心的启动与基础行为回归")
    config.addinivalue_line("markers", "feature: 模块级能力回归")
    config.addinivalue_line("markers", "integration: 多模块串联工作流回归")
    config.addinivalue_line("markers", "release: 发版前最小通过集合")
    config.addinivalue_line("markers", "local_server: 依赖本地测试 server")


@pytest.fixture
def test_browser_path():
    """Return the Firefox executable path configured for browser tests."""
    return os.environ.get(ENV_FIREFOX_PATH) or None


@pytest.fixture
def opts_factory():
    """返回一个可定制的 FirefoxOptions 工厂。"""

    def _make(**kwargs):
        opts = FirefoxOptions()
        opts.headless(kwargs.pop("headless", False))
        firefox_path = kwargs.pop("browser_path", None) or os.environ.get(ENV_FIREFOX_PATH)
        if firefox_path:
            opts.set_browser_path(firefox_path)
        for key, value in kwargs.items():
            if key == "private":
                opts.private_mode(value)
            elif key == "user_dir":
                opts.set_user_dir(value)
            elif key == "action_visual":
                opts.enable_action_visual(value)
            elif key == "human_algorithm":
                opts.set_human_algorithm(value)
            elif key == "window_size":
                opts.set_window_size(value[0], value[1])
            elif key == "close_on_exit":
                opts.close_on_exit(value)
            elif key == "port":
                opts.set_port(value)
            else:
                raise ValueError("未支持的 opts_factory 参数: {}".format(key))
        return opts

    return _make


@pytest.fixture
def page(opts_factory):
    """创建一个默认页面实例，并在测试结束时清理。"""
    page = FirefoxPage(opts_factory())
    page.get("about:blank")
    yield page
    try:
        page.trace.clear()
    except Exception:
        pass
    try:
        page.intercept.stop()
    except Exception:
        pass
    try:
        page.listen.stop()
    except Exception:
        pass
    try:
        page.quit()
    except Exception:
        pass


@pytest.fixture
def launched_page():
    """通过 launch() 创建页面，覆盖小白入口路径。"""
    page = launch(
        headless=False,
        browser_path=os.environ.get(ENV_FIREFOX_PATH) or None,
    )
    page.get("about:blank")
    yield page
    try:
        page.quit()
    except Exception:
        pass


@pytest.fixture
def temp_user_dir():
    """提供临时 user_dir / profile 目录。"""
    path = tempfile.mkdtemp(prefix="ruyipage_test_userdir_")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def server():
    """启动本地测试 server。"""
    srv = TestServer().start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def fixture_page_url():
    """返回把 fixtures/pages 下页面转为 file URL 的 helper。"""

    def _get(name):
        return (FIXTURE_PAGES_DIR / name).resolve().as_uri()

    return _get
