# -*- coding: utf-8 -*-

from types import SimpleNamespace

from ruyipage._pages.firefox_base import FirefoxBase, _frame_url_matches


class TreeDriver:
    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    def run(self, method, params=None, timeout=None):
        self.calls.append((method, params, timeout))
        if method == "browsingContext.getTree":
            assert params == {"root": "page-ctx"}
            return self.tree
        raise AssertionError(f"unexpected method: {method}")


class FrameElement:
    def __init__(self, src):
        self.src = src

    def attr(self, name):
        assert name == "src"
        return self.src


def make_page(tree):
    browser_driver = TreeDriver(tree)
    page = object.__new__(FirefoxBase)
    page._context_id = "page-ctx"
    page._driver = SimpleNamespace(_browser_driver=browser_driver)
    page._browser = SimpleNamespace(
        driver=browser_driver,
        options=SimpleNamespace(load_mode="normal"),
    )
    return page


def test_get_all_frames_returns_nested_frames_depth_first():
    page = make_page(
        {
            "contexts": [
                {
                    "context": "page-ctx",
                    "children": [
                        {
                            "context": "frame-a",
                            "children": [
                                {"context": "frame-a-1"},
                                {"context": "frame-a-2", "children": []},
                            ],
                        },
                        {"context": "frame-b", "children": []},
                    ],
                }
            ]
        }
    )

    frames = page.get_all_frames()

    assert [frame._context_id for frame in frames] == [
        "frame-a",
        "frame-a-1",
        "frame-a-2",
        "frame-b",
    ]
    assert frames[0]._parent is page
    assert frames[1]._parent is frames[0]
    assert frames[2]._parent is frames[0]
    assert frames[3]._parent is page


def test_get_frame_locator_matches_default_https_port_url():
    page = make_page(
        {
            "contexts": [
                {
                    "context": "page-ctx",
                    "children": [
                        {
                            "context": "frame-a",
                            "url": "https://other.test/path?q=1",
                        },
                        {
                            "context": "frame-b",
                            "url": "https://example.test:443/path?q=1",
                        },
                    ],
                }
            ]
        }
    )
    page.ele = lambda locator: FrameElement("https://example.test/path?q=1")

    frame = page.get_frame("#target-frame")

    assert frame is not None
    assert frame._context_id == "frame-b"
    assert frame._parent is page


def test_frame_url_match_only_strips_default_ports():
    assert _frame_url_matches(
        "https://example.test/path?q=1",
        "https://example.test:443/path?q=1",
    )
    assert _frame_url_matches(
        "http://example.test/path",
        "http://example.test:80/path",
    )
    assert not _frame_url_matches(
        "https://example.test/path",
        "https://example.test:444/path",
    )
