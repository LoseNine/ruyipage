# -*- coding: utf-8 -*-

import pytest

from ruyipage import FirefoxPage, launch


@pytest.mark.smoke
def test_firefox_page_starts_with_default_options(opts_factory):
    page = FirefoxPage(opts_factory())
    try:
        page.get("about:blank")
        assert page.url == "about:blank"
    finally:
        page.quit()


@pytest.mark.smoke
def test_launch_entry_works(test_browser_path):
    page = launch(
        headless=False,
        close_on_exit=True,
        browser_path=test_browser_path,
    )
    try:
        page.get("about:blank")
        assert page.url == "about:blank"
    finally:
        page.quit()


@pytest.mark.smoke
def test_launched_page_fixture_works(launched_page):
    assert launched_page.url == "about:blank"
