from types import SimpleNamespace

from ruyipage._pages.firefox_base import FirefoxBase


class _ProtectedContextDriver:
    def __init__(self):
        self.calls = []

    def run(self, method, params=None, **kwargs):
        self.calls.append((method, params or {}))
        if method == "browsingContext.getTree":
            if "root" in (params or {}):
                raise AssertionError("protected contexts reject rooted tree queries")
            return {
                "contexts": [
                    {
                        "context": "private-context",
                        "url": "about:privatebrowsing",
                    }
                ]
            }
        if method == "script.evaluate":
            raise AssertionError("protected contexts must not require script access")
        raise AssertionError("unexpected BiDi command: {}".format(method))


def test_url_uses_context_metadata_for_protected_pages():
    browser_driver = _ProtectedContextDriver()
    page = FirefoxBase.__new__(FirefoxBase)
    page._context_id = "private-context"
    page._driver = SimpleNamespace(_browser_driver=browser_driver)

    assert page.url == "about:privatebrowsing"
    assert browser_driver.calls == [
        (
            "browsingContext.getTree",
            {"maxDepth": 0},
        )
    ]
