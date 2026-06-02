# -*- coding: utf-8 -*-

import os
import time
import urllib.parse
from types import SimpleNamespace
from unittest import mock

import pytest

from ruyipage import FirefoxOptions
from ruyipage._bidi import browser_module as bidi_browser
from ruyipage._base.browser import Firefox
from ruyipage._units.downloads import DownloadsManager


def _wait_file_text(path, expected, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.stat().st_size >= len(expected):
            try:
                if path.read_text(encoding="utf-8") == expected:
                    return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def test_set_download_behavior_uses_destination_folder(tmp_path):
    driver = mock.Mock()

    bidi_browser.set_download_behavior(
        driver,
        behavior="allow",
        download_path=tmp_path,
    )

    driver.run.assert_called_once_with(
        "browser.setDownloadBehavior",
        {
            "downloadBehavior": {
                "type": "allowed",
                "destinationFolder": os.path.normpath(os.path.abspath(str(tmp_path))),
            }
        },
    )


def test_set_download_behavior_denies_without_destination_folder():
    driver = mock.Mock()

    bidi_browser.set_download_behavior(driver, behavior="deny")

    driver.run.assert_called_once_with(
        "browser.setDownloadBehavior",
        {"downloadBehavior": {"type": "denied"}},
    )


def test_set_download_behavior_supports_user_contexts(tmp_path):
    driver = mock.Mock()

    bidi_browser.set_download_behavior(
        driver,
        behavior="allow",
        download_path=tmp_path,
        user_contexts="default",
    )

    params = driver.run.call_args.args[1]
    assert params["downloadBehavior"]["type"] == "allowed"
    assert params["downloadBehavior"]["destinationFolder"] == os.path.normpath(
        os.path.abspath(str(tmp_path))
    )
    assert params["userContexts"] == ["default"]


def test_set_download_behavior_rejects_contexts(tmp_path):
    with pytest.raises(ValueError, match="contexts"):
        bidi_browser.set_download_behavior(
            mock.Mock(),
            behavior="allow",
            download_path=tmp_path,
            contexts=["ctx-1"],
        )


def test_downloads_manager_set_behavior_is_global_by_default(monkeypatch, tmp_path):
    owner = SimpleNamespace(
        _context_id="ctx-page",
        _driver=SimpleNamespace(_browser_driver=object()),
    )
    manager = DownloadsManager(owner)
    calls = []

    def fake_set_download_behavior(driver, **kwargs):
        kwargs["driver"] = driver
        calls.append(kwargs)

    monkeypatch.setattr(
        "ruyipage._units.downloads.bidi_browser.set_download_behavior",
        fake_set_download_behavior,
    )

    manager.set_behavior("allow", path=tmp_path)

    assert calls == [
        {
            "driver": owner._driver._browser_driver,
            "behavior": "allow",
            "download_path": tmp_path,
            "contexts": None,
            "user_contexts": None,
        }
    ]


def test_downloads_manager_set_path_returns_owner(monkeypatch, tmp_path):
    owner = SimpleNamespace(
        _context_id="ctx-page",
        _driver=SimpleNamespace(_browser_driver=object()),
    )
    manager = DownloadsManager(owner)
    calls = []
    monkeypatch.setattr(
        manager,
        "set_behavior",
        lambda behavior, path=None: calls.append((behavior, path)),
    )

    result = manager.set_path(tmp_path)

    assert result is owner
    assert calls == [("allow", tmp_path)]


def test_firefox_setup_download_behavior_uses_bidi_helper(monkeypatch, tmp_path):
    browser = Firefox.__new__(Firefox)
    browser._driver = object()
    browser._options = FirefoxOptions().set_download_path(tmp_path)
    calls = []

    def fake_set_download_behavior(driver, behavior="allow", download_path=None, **kwargs):
        calls.append((driver, behavior, download_path, kwargs))

    monkeypatch.setattr(
        "ruyipage._base.browser.bidi_browser.set_download_behavior",
        fake_set_download_behavior,
    )

    browser._setup_download_behavior()

    assert calls == [(browser._driver, "allow", browser._options.download_path, {})]


def test_firefox_options_download_path_defaults_to_none():
    assert FirefoxOptions().download_path is None


@pytest.mark.browser
@pytest.mark.local_server
def test_page_downloads_set_behavior_writes_to_custom_directory(page, server, tmp_path):
    download_dir = tmp_path / "custom-downloads"
    download_dir.mkdir()
    filename = "ruyipage-custom-download.txt"
    download_url = server.get_url("/download/text?name={}".format(filename))
    html = """
    <!doctype html>
    <html><body>
      <a id="download" href="{url}">download</a>
    </body></html>
    """.format(url=download_url)

    page.downloads.set_behavior("allow", path=download_dir)
    page.get("data:text/html;charset=utf-8," + urllib.parse.quote(html))
    page.downloads.start()
    try:
        page.ele("#download").click_self()
        deadline = time.time() + 8
        downloaded = None
        while time.time() < deadline:
            matches = sorted(download_dir.glob("ruyipage-custom-download*.txt"))
            for item in matches:
                if item.stat().st_size >= 1:
                    downloaded = item
                    break
            if downloaded:
                break
            time.sleep(0.1)

        assert downloaded is not None
        assert downloaded.read_text(encoding="utf-8") == "hello download"
    finally:
        page.downloads.stop()


@pytest.mark.browser
@pytest.mark.local_server
def test_page_downloads_multiple_links_report_distinct_files(page, server, tmp_path):
    download_dir = tmp_path / "multi-downloads"
    download_dir.mkdir()
    expected = [
        ("#download-1", "ruyipage-multi-1.txt", "multi file content 1"),
        ("#download-2", "ruyipage-multi-2.txt", "multi file content 2"),
        ("#download-3", "ruyipage-multi-3.txt", "multi file content 3"),
    ]

    page.downloads.set_behavior("allow", path=download_dir)
    page.get(server.get_url("/download/multiple"))
    page.downloads.start()
    try:
        seen_filenames = []
        for selector, filename, content in expected:
            page.downloads.clear()
            page.ele(selector).click_self()

            begin = page.downloads.wait(
                method="browsingContext.downloadWillBegin",
                timeout=5,
            )
            assert begin is not None
            assert begin.suggested_filename == filename
            seen_filenames.append(begin.suggested_filename)

            downloaded = download_dir / filename
            assert _wait_file_text(downloaded, content, timeout=5)

        assert seen_filenames == [item[1] for item in expected]
        assert len(set(seen_filenames)) == len(expected)
    finally:
        page.downloads.stop()
