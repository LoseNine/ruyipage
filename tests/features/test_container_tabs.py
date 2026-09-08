# -*- coding: utf-8 -*-

import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from ruyipage import FirefoxOptions, FirefoxPage
from ruyipage.errors import BiDiError, BrowserConnectError


def test_page_new_tab_forwards_user_context():
    page = FirefoxPage.__new__(FirefoxPage)
    fake_browser = mock.Mock()
    page._firefox = fake_browser

    page.new_tab("https://example.com", background=True, user_context="ctx-1")

    fake_browser.new_tab.assert_called_once_with(
        "https://example.com", True, user_context="ctx-1"
    )


def test_page_new_container_tab_forwards_to_browser():
    page = FirefoxPage.__new__(FirefoxPage)
    fake_browser = mock.Mock()
    page._firefox = fake_browser

    page.new_container_tab("https://example.com", background=True)

    fake_browser.new_container_tab.assert_called_once_with(
        url="https://example.com", background=True
    )


def test_page_new_container_tabs_forwards_to_browser():
    page = FirefoxPage.__new__(FirefoxPage)
    fake_browser = mock.Mock()
    page._firefox = fake_browser

    page.new_container_tabs(5, "https://example.com", background=False)

    fake_browser.new_container_tabs.assert_called_once_with(
        count=5, url="https://example.com", background=False
    )


def test_browser_new_tab_includes_user_context():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser._context_ids = ["root-ctx"]
    browser._contexts = {}
    browser._driver = mock.Mock()
    browser._driver.run.return_value = {"context": "ctx-2"}
    browser._get_or_create_tab = mock.Mock(return_value=mock.Mock())

    browser.new_tab(user_context="uc-1")

    browser._driver.run.assert_called_once_with(
        "browsingContext.create",
        {
            "type": "tab",
            "background": False,
            "referenceContext": "root-ctx",
            "userContext": "uc-1",
        },
    )


def test_browser_new_container_tab_creates_user_context_then_tab():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser._driver = mock.Mock()
    browser.new_tab = mock.Mock(return_value="tab-1")
    browser._driver.run.return_value = {"userContext": "uc-2"}

    result = browser.new_container_tab(url="https://example.com", background=True)

    assert result == "tab-1"
    browser._driver.run.assert_called_once_with("browser.createUserContext")
    browser.new_tab.assert_called_once_with(
        url="https://example.com", background=True, user_context="uc-2"
    )


def test_browser_new_container_tabs_with_count_one_uses_bidi_path():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser.new_container_tab = mock.Mock(return_value="tab-1")

    tabs = browser.new_container_tabs(1, url="https://example.com", background=False)

    assert tabs == ["tab-1"]
    browser.new_container_tab.assert_called_once_with(
        url="https://example.com", background=False
    )


def test_page_tab_properties_forward_to_browser():
    page = FirefoxPage.__new__(FirefoxPage)
    fake_browser = mock.Mock()
    fake_browser.tabs_count = 3
    fake_browser.tab_ids = ["ctx-1", "ctx-2", "ctx-3"]
    fake_browser.latest_tab = "tab-3"
    page._firefox = fake_browser

    assert page.tabs_count == 3
    assert page.tab_ids == ["ctx-1", "ctx-2", "ctx-3"]
    assert page.latest_tab == "tab-3"


def test_page_get_tab_and_get_tabs_forward_to_browser():
    page = FirefoxPage.__new__(FirefoxPage)
    fake_browser = mock.Mock()
    fake_browser.get_tab.return_value = "tab-1"
    fake_browser.get_tabs.return_value = ["tab-1", "tab-2"]
    page._firefox = fake_browser

    assert page.get_tab("ctx-1", title="Example", url="example.com") == "tab-1"
    assert page.get_tabs(title="Example", url="example.com") == ["tab-1", "tab-2"]

    fake_browser.get_tab.assert_called_once_with(
        "ctx-1", "Example", "example.com"
    )
    fake_browser.get_tabs.assert_called_once_with("Example", "example.com")


def test_page_close_other_tabs_defaults_to_current_context():
    page = FirefoxPage.__new__(FirefoxPage)
    fake_browser = mock.Mock()
    page._firefox = fake_browser
    page._context_id = "ctx-current"

    page.close_other_tabs()

    fake_browser.close_tabs.assert_called_once_with("ctx-current", others=True)


def test_page_quit_calls_browser_and_removes_page_cache():
    page = FirefoxPage.__new__(FirefoxPage)
    fake_browser = mock.Mock()
    fake_browser.address = "127.0.0.1:9999"
    page._firefox = fake_browser
    FirefoxPage._PAGES[fake_browser.address] = page

    try:
        page.quit(timeout=7, force=True)
    finally:
        FirefoxPage._PAGES.pop(fake_browser.address, None)

    fake_browser.quit.assert_called_once_with(7, True)
    assert fake_browser.address not in FirefoxPage._PAGES


def test_browser_tab_ids_returns_copy_after_refresh():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser._context_ids = ["ctx-1", "ctx-2"]
    browser._refresh_tabs = mock.Mock()

    tab_ids = browser.tab_ids
    tab_ids.append("ctx-3")

    browser._refresh_tabs.assert_called_once_with()
    assert tab_ids == ["ctx-1", "ctx-2", "ctx-3"]
    assert browser._context_ids == ["ctx-1", "ctx-2"]


def test_browser_refresh_tabs_filters_invalid_context_ids(monkeypatch):
    from ruyipage._base.browser import Firefox
    from ruyipage._bidi import browsing_context

    browser = Firefox.__new__(Firefox)
    browser._driver = object()
    browser._context_ids = ["old-ctx"]
    browser._context_ids_lock = threading.Lock()

    monkeypatch.setattr(
        browsing_context,
        "get_tree",
        lambda driver, max_depth=0: {
            "contexts": [
                {"context": None},
                {"context": ""},
                {"context": 123},
                {"context": "ctx-1"},
            ]
        },
    )

    browser._refresh_tabs()

    assert browser._context_ids == ["ctx-1"]


def test_browser_subscribe_events_falls_back_when_one_event_invalid():
    from ruyipage._base.browser import Firefox

    class FakeDriver:
        def __init__(self):
            self.subscribe_calls = []
            self.successful_events = []
            self.callbacks = []

        def run(self, method, params=None):
            assert method == "session.subscribe"
            events = list(params["events"])
            self.subscribe_calls.append(events)
            if "browsingContext.navigationFailed" in events:
                raise BiDiError(
                    "invalid argument",
                    "browsingContext.navigationFailed is not a valid event name",
                )
            self.successful_events.extend(events)
            return {"subscription": "sub-{}".format(len(self.subscribe_calls))}

        def set_callback(self, event, callback, immediate=False):
            self.callbacks.append((event, callback, immediate))

    browser = Firefox.__new__(Firefox)
    browser._driver = FakeDriver()

    browser._subscribe_events()

    assert browser._driver.subscribe_calls[0] == [
        "browsingContext.contextCreated",
        "browsingContext.contextDestroyed",
        "browsingContext.load",
        "browsingContext.domContentLoaded",
        "browsingContext.userPromptOpened",
        "browsingContext.userPromptClosed",
        "browsingContext.navigationStarted",
        "browsingContext.navigationFailed",
    ]
    assert "browsingContext.contextCreated" in browser._driver.successful_events
    assert "browsingContext.contextDestroyed" in browser._driver.successful_events
    assert "browsingContext.load" in browser._driver.successful_events
    assert "browsingContext.domContentLoaded" in browser._driver.successful_events
    assert "browsingContext.navigationStarted" in browser._driver.successful_events
    assert "browsingContext.navigationFailed" not in browser._driver.successful_events

    callback_events = [item[0] for item in browser._driver.callbacks]
    assert "browsingContext.contextCreated" in callback_events
    assert "browsingContext.contextDestroyed" in callback_events
    assert (
        "browsingContext.load",
        browser._on_navigation_event,
        True,
    ) in browser._driver.callbacks
    assert (
        "browsingContext.domContentLoaded",
        browser._on_navigation_event,
        True,
    ) in browser._driver.callbacks


def test_browser_try_connect_rejects_missing_context(monkeypatch):
    from ruyipage._base.browser import Firefox
    from ruyipage._configs.firefox_options import FirefoxOptions
    import ruyipage._base.browser as browser_module

    class FakeSocket:
        timeout = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, address):
            return None

    fake_driver = mock.Mock()
    fake_socket = FakeSocket()
    browser = Firefox.__new__(Firefox)
    browser._address = "127.0.0.1:9222"
    browser._options = FirefoxOptions()
    browser._session_id = None
    browser._owns_session = False
    browser._driver = None
    browser._context_ids = []
    browser._context_ids_lock = threading.Lock()
    browser._reserved_port = None
    browser._teardown_proxy_auth = mock.Mock()
    browser._create_session = mock.Mock()
    browser._subscribe_events = mock.Mock()
    browser._setup_proxy_auth = mock.Mock()
    browser._setup_download_behavior = mock.Mock()
    browser._wait_for_initial_context = mock.Mock(return_value=False)

    monkeypatch.setattr(browser_module.socket, "socket", lambda *args, **kwargs: fake_socket)
    monkeypatch.setattr(browser_module, "get_bidi_ws_url", lambda *args, **kwargs: "ws")
    monkeypatch.setattr(
        browser_module, "BrowserBiDiDriver", lambda address, shared=True: fake_driver
    )

    assert browser._try_connect() is False
    assert fake_socket.timeout == 0.1
    browser._wait_for_initial_context.assert_called_once_with()
    fake_driver.stop.assert_called_once_with()


def test_browser_try_connect_survives_invalid_event_subscription(monkeypatch):
    from ruyipage._base.browser import Firefox
    from ruyipage._configs.firefox_options import FirefoxOptions
    import ruyipage._base.browser as browser_module

    class FakeSocket:
        timeout = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, address):
            return None

    class FakeDriver:
        def __init__(self):
            self.subscribe_calls = []
            self.successful_events = []
            self.callbacks = []
            self.stop = mock.Mock()

        def start(self, ws_url):
            self.ws_url = ws_url

        def run(self, method, params=None):
            assert method == "session.subscribe"
            events = list(params["events"])
            self.subscribe_calls.append(events)
            if "browsingContext.navigationFailed" in events:
                raise BiDiError(
                    "invalid argument",
                    "browsingContext.navigationFailed is not a valid event name",
                )
            self.successful_events.extend(events)
            return {"subscription": "sub-{}".format(len(self.subscribe_calls))}

        def set_callback(self, event, callback, immediate=False):
            self.callbacks.append((event, callback, immediate))

    fake_driver = FakeDriver()
    fake_socket = FakeSocket()
    browser = Firefox.__new__(Firefox)
    browser._address = "127.0.0.1:9222"
    browser._options = FirefoxOptions()
    browser._session_id = None
    browser._owns_session = False
    browser._driver = None
    browser._context_ids = ["ctx-1"]
    browser._context_ids_lock = threading.Lock()
    browser._reserved_port = None
    browser._create_session = mock.Mock(
        side_effect=lambda: setattr(browser, "_session_id", "sid")
    )
    browser._setup_proxy_auth = mock.Mock()
    browser._setup_download_behavior = mock.Mock()
    browser._wait_for_initial_context = mock.Mock(return_value=True)

    monkeypatch.setattr(browser_module.socket, "socket", lambda *args, **kwargs: fake_socket)
    monkeypatch.setattr(browser_module, "get_bidi_ws_url", lambda *args, **kwargs: "ws")
    monkeypatch.setattr(
        browser_module, "BrowserBiDiDriver", lambda address, shared=True: fake_driver
    )

    assert browser._try_connect() is True
    assert browser._driver is fake_driver
    assert fake_socket.timeout == 0.1
    assert "browsingContext.load" in fake_driver.successful_events
    assert "browsingContext.navigationFailed" not in fake_driver.successful_events
    fake_driver.stop.assert_not_called()
    browser._setup_proxy_auth.assert_called_once_with()
    browser._setup_download_behavior.assert_called_once_with()
    browser._wait_for_initial_context.assert_called_once_with()


def test_firefox_page_init_creates_valid_context_when_existing_tabs_invalid(monkeypatch):
    from ruyipage._pages import firefox_page as page_module

    fake_browser = mock.Mock()
    fake_browser.tab_ids = []
    fake_browser.driver = object()
    fake_browser.options = SimpleNamespace(
        load_mode="normal",
        xpath_picker_enabled=False,
        action_visual_enabled=False,
        trace_enabled=False,
        failure_snapshot_enabled=False,
    )
    created_contexts = []

    monkeypatch.setattr(page_module, "Firefox", lambda addr_or_opts=None: fake_browser)
    monkeypatch.setattr(page_module, "_INITIAL_CONTEXT_WAIT_TIMEOUT", 0)
    monkeypatch.setattr(page_module, "_INITIAL_CONTEXT_POLL_INTERVAL", 0)
    monkeypatch.setattr(
        page_module.bidi_context,
        "create",
        lambda driver, type_: {"context": "ctx-new"},
    )
    monkeypatch.setattr(
        page_module.FirefoxPage,
        "_init_context",
        lambda self, browser, context_id: created_contexts.append(context_id),
    )

    page = FirefoxPage.__new__(FirefoxPage)
    FirefoxPage.__init__(page)

    assert created_contexts == ["ctx-new"]


def test_firefox_page_init_rejects_empty_created_context(monkeypatch):
    from ruyipage._pages import firefox_page as page_module

    fake_browser = mock.Mock()
    fake_browser.tab_ids = []
    fake_browser.driver = object()
    fake_browser.options = SimpleNamespace(
        load_mode="normal",
        xpath_picker_enabled=False,
        action_visual_enabled=False,
        trace_enabled=False,
        failure_snapshot_enabled=False,
    )

    monkeypatch.setattr(page_module, "Firefox", lambda addr_or_opts=None: fake_browser)
    monkeypatch.setattr(page_module, "_INITIAL_CONTEXT_WAIT_TIMEOUT", 0)
    monkeypatch.setattr(page_module, "_INITIAL_CONTEXT_POLL_INTERVAL", 0)
    monkeypatch.setattr(
        page_module.bidi_context,
        "create",
        lambda driver, type_: {"context": None},
    )

    page = FirefoxPage.__new__(FirefoxPage)
    with pytest.raises(BrowserConnectError):
        FirefoxPage.__init__(page)


def test_firefox_options_remembers_requested_startup_window_size():
    opts = FirefoxOptions().set_window_size(1286, 688)

    assert opts.startup_window_size == (1286, 688)
    assert "--width=1286" in opts.arguments
    assert "--height=688" in opts.arguments


def test_firefox_page_init_reapplies_requested_startup_window_size(monkeypatch):
    from ruyipage._pages import firefox_page as page_module

    opts = FirefoxOptions().set_window_size(1286, 688)
    fake_browser = mock.Mock()
    fake_browser.tab_ids = ["ctx-1"]
    fake_browser.driver = object()
    fake_browser.options = opts
    calls = []

    class FakeWindow:
        def normal(self):
            calls.append(("normal",))

        def set_size(self, width, height):
            calls.append(("window_set_size", width, height))

    def fake_init_context(self, browser, context_id):
        self._window = FakeWindow()

    monkeypatch.setattr(page_module, "Firefox", lambda addr_or_opts=None: fake_browser)
    monkeypatch.setattr(page_module.FirefoxPage, "_init_context", fake_init_context)
    monkeypatch.setattr(
        page_module.FirefoxPage,
        "set_window_size",
        lambda self, width, height: calls.append(
            ("page_set_window_size", width, height)
        ),
    )

    page = FirefoxPage.__new__(FirefoxPage)
    FirefoxPage.__init__(page, opts)

    assert calls == [("normal",), ("window_set_size", 1286, 688)]


def test_firefox_page_init_uses_low_level_startup_window_resize(monkeypatch):
    from ruyipage._pages import firefox_page as page_module

    opts = FirefoxOptions().set_window_size(1286, 688)
    fake_browser = mock.Mock()
    fake_browser.tab_ids = ["ctx-1"]
    fake_browser.driver = object()
    fake_browser.options = opts
    calls = []

    class FakeWindow:
        def normal(self):
            calls.append(("normal",))

        def _set_size_only(self, width, height):
            calls.append(("window_set_size_only", width, height))

        def set_size(self, width, height):
            calls.append(("window_set_size", width, height))

    def fake_init_context(self, browser, context_id):
        self._window = FakeWindow()

    monkeypatch.setattr(page_module, "Firefox", lambda addr_or_opts=None: fake_browser)
    monkeypatch.setattr(page_module.FirefoxPage, "_init_context", fake_init_context)
    monkeypatch.setattr(
        page_module.FirefoxPage,
        "set_window_size",
        lambda self, width, height: calls.append(
            ("page_set_window_size", width, height)
        ),
    )

    page = FirefoxPage.__new__(FirefoxPage)
    FirefoxPage.__init__(page, opts)

    assert calls == [("normal",), ("window_set_size_only", 1286, 688)]


def test_firefox_page_init_keeps_existing_browser_window_state(monkeypatch):
    from ruyipage._pages import firefox_page as page_module

    opts = FirefoxOptions().set_window_size(1286, 688).existing_only(True)
    fake_browser = mock.Mock()
    fake_browser.tab_ids = ["ctx-1"]
    fake_browser.driver = object()
    fake_browser.options = opts
    calls = []

    class FakeWindow:
        def normal(self):
            calls.append(("normal",))

    def fake_init_context(self, browser, context_id):
        self._window = FakeWindow()

    monkeypatch.setattr(page_module, "Firefox", lambda addr_or_opts=None: fake_browser)
    monkeypatch.setattr(page_module.FirefoxPage, "_init_context", fake_init_context)
    monkeypatch.setattr(
        page_module.FirefoxPage,
        "set_window_size",
        lambda self, width, height: calls.append(("set_window_size", width, height)),
    )

    page = FirefoxPage.__new__(FirefoxPage)
    FirefoxPage.__init__(page, opts)

    assert calls == []


def test_browser_latest_tab_returns_last_context_or_none():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser._context_ids = []
    browser._refresh_tabs = mock.Mock()
    browser._get_or_create_tab = mock.Mock()

    assert browser.latest_tab is None

    browser._context_ids = ["ctx-1", "ctx-2"]
    browser._get_or_create_tab.return_value = "tab-2"

    assert browser.latest_tab == "tab-2"
    browser._get_or_create_tab.assert_called_once_with("ctx-2")


def test_browser_get_tab_supports_default_index_negative_and_string_lookup():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser._context_ids = ["ctx-1", "ctx-2"]
    browser._refresh_tabs = mock.Mock()
    browser._get_or_create_tab = mock.Mock(side_effect=lambda ctx_id: "tab-" + ctx_id)

    assert browser.get_tab() == "tab-ctx-1"
    assert browser.get_tab(1) == "tab-ctx-1"
    assert browser.get_tab(-1) == "tab-ctx-2"
    assert browser.get_tab("ctx-2") == "tab-ctx-2"
    assert browser.get_tab("missing") is None


def test_browser_get_tab_returns_none_for_out_of_range_negative_index():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser._context_ids = ["ctx-1"]
    browser._refresh_tabs = mock.Mock()
    browser._get_or_create_tab = mock.Mock()

    assert browser.get_tab(-999) is None
    browser._get_or_create_tab.assert_not_called()


def test_browser_get_tabs_filters_by_title_and_url():
    from ruyipage._base.browser import Firefox

    matching_tab = mock.Mock(title="Example Home", url="https://example.com/home")
    other_tab = mock.Mock(title="Other", url="https://other.test/")
    tabs_by_context = {"ctx-1": matching_tab, "ctx-2": other_tab}
    browser = Firefox.__new__(Firefox)
    browser._context_ids = ["ctx-1", "ctx-2"]
    browser._refresh_tabs = mock.Mock()
    browser._get_or_create_tab = mock.Mock(side_effect=tabs_by_context.get)

    assert browser.get_tabs(title="Example", url="example.com") == [matching_tab]


def test_browser_new_tab_omits_user_context_for_default_context():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)
    browser._context_ids = ["ctx-root"]
    browser._driver = mock.Mock()
    browser._driver.run.return_value = {"context": "ctx-new"}
    browser._get_or_create_tab = mock.Mock(return_value=mock.Mock())

    browser.new_tab(background=True)

    browser._driver.run.assert_called_once_with(
        "browsingContext.create",
        {"type": "tab", "background": True, "referenceContext": "ctx-root"},
    )


def test_browser_new_container_tabs_rejects_non_positive_count():
    from ruyipage._base.browser import Firefox

    browser = Firefox.__new__(Firefox)

    try:
        browser.new_container_tabs(0)
    except ValueError as e:
        assert "count" in str(e)
    else:
        raise AssertionError("expected ValueError for non-positive count")


def test_set_per_tab_proxies_normalizes_lines_and_generates_runtime_fpfile(tmp_path):
    opts = FirefoxOptions()
    opts.set_user_dir(str(tmp_path))
    opts.set_per_tab_proxies(
        [
            "proxy.example.com:1000:user-a:pass-a",
            "socks5://proxy2.example.com:1001:user-b:pass-b",
        ],
        exhausted="wrap",
    )

    opts.prepare_runtime_files()

    assert opts.fpfile.endswith("ruyipage_per_tab_proxy_fp.txt")
    content = (tmp_path / "ruyipage_per_tab_proxy_fp.txt").read_text(encoding="utf-8")
    assert "proxy.rotate.enabled=true" in content
    assert "proxy.rotate.exhausted=wrap" in content
    assert "proxy.rotate.proxy=socks5://proxy.example.com:1000:user-a:pass-a" in content
    assert "proxy.rotate.proxy=socks5://proxy2.example.com:1001:user-b:pass-b" in content


def test_prepare_runtime_files_merges_source_fpfile_and_omits_old_rotate_lines(tmp_path):
    source = tmp_path / "source_fp.txt"
    source.write_text(
        "\n".join(
            [
                "webdriver:0",
                "proxy.rotate.enabled=false",
                "proxy.rotate.exhausted=stop",
                "proxy.rotate.proxy=socks5://old.example.com:9000:old:secret",
                "canvas:123",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.set_user_dir(str(tmp_path))
    opts.set_fpfile(str(source))
    opts.set_per_tab_proxies(["proxy.example.com:1000:user-a:pass-a"])

    opts.prepare_runtime_files()

    content = (tmp_path / "ruyipage_per_tab_proxy_fp.txt").read_text(encoding="utf-8")
    assert "webdriver:0" in content
    assert "canvas:123" in content
    assert "proxy.rotate.enabled=false" not in content
    assert "old.example.com" not in content
    assert "proxy.rotate.enabled=true" in content
    assert "proxy.rotate.proxy=socks5://proxy.example.com:1000:user-a:pass-a" in content


def test_write_prefs_does_not_write_global_proxy_for_per_tab_rotate(tmp_path):
    opts = FirefoxOptions()
    opts.set_user_dir(str(tmp_path))
    opts.set_per_tab_proxies(["proxy.example.com:1000:user-a:pass-a"])
    opts.prepare_runtime_files()

    opts.write_prefs_to_profile()

    content = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert "network.proxy.socks" not in content
    assert "network.proxy.socks_port" not in content
    assert "user-a" not in content
    assert "pass-a" not in content


def test_set_per_tab_proxies_validates_format():
    opts = FirefoxOptions()

    try:
        opts.set_per_tab_proxies(["bad-format"])
    except ValueError as e:
        assert "host:port:username:password" in str(e)
    else:
        raise AssertionError("expected ValueError for invalid proxy format")
