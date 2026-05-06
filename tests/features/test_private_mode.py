# -*- coding: utf-8 -*-

import pytest

from ruyipage import FirefoxPage, launch
from ruyipage._base.browser import Firefox
from ruyipage._configs.firefox_options import FirefoxOptions


@pytest.mark.feature
def test_private_mode_with_options(opts_factory, temp_user_dir):
    page = FirefoxPage(opts_factory(private=True, user_dir=temp_user_dir))
    try:
        assert page.url == "about:privatebrowsing"
        page.get("about:blank")
        assert page.url == "about:blank"
    finally:
        page.quit()


@pytest.mark.feature
def test_private_mode_with_launch(temp_user_dir, test_browser_path):
    page = launch(
        headless=False,
        private=True,
        user_dir=temp_user_dir,
        browser_path=test_browser_path,
    )
    try:
        assert page.url == "about:privatebrowsing"
        page.get("about:blank")
        assert page.url == "about:blank"
    finally:
        page.quit()


@pytest.mark.feature
def test_private_mode_starts_in_private_browsing_page(temp_user_dir, test_browser_path):
    page = launch(
        headless=False,
        private=True,
        user_dir=temp_user_dir,
        browser_path=test_browser_path,
    )
    try:
        tree = page.browser.driver.run("browsingContext.getTree", {"maxDepth": 0})
        urls = [item.get("url", "") for item in tree.get("contexts", [])]

        assert page.url == "about:privatebrowsing"
        assert "about:privatebrowsing" in urls
    finally:
        page.quit()


def test_launch_mode_does_not_cache_instances_by_address():
    opts = FirefoxOptions().set_address("127.0.0.1:9222").private_mode(True)

    assert FirefoxPage._cache_key_for(opts) is None
    assert Firefox._cache_key_for(opts) is None
