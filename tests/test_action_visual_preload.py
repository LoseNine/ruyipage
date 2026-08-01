import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

import ruyipage._base.browser as browser_module
from ruyipage._base.browser import Firefox, create_browser_from_probe_info
from ruyipage._configs.firefox_options import FirefoxOptions
from ruyipage._pages.firefox_base import FirefoxBase


class RecordingDriver:
    def __init__(self, responses=None, delay=0):
        self.calls = []
        self.responses = list(responses or [{"script": "baseline-1"}])
        self.delay = delay
        self.lock = threading.Lock()

    def run(self, method, params=None, **kwargs):
        with self.lock:
            self.calls.append((method, params, kwargs))
            response = self.responses.pop(0)
        if self.delay:
            time.sleep(self.delay)
        if isinstance(response, Exception):
            raise response
        return response


def make_browser(session_id="session-1", responses=None):
    browser = object.__new__(Firefox)
    browser._driver = RecordingDriver(responses)
    browser._session_id = session_id
    browser._baseline_preload_script_id = None
    browser._baseline_preload_session_id = None
    browser._baseline_preload_lock = threading.Lock()
    return browser


def test_baseline_preload_registers_once_for_one_session():
    browser = make_browser()

    assert browser._ensure_baseline_preload() is True
    assert browser._ensure_baseline_preload() is True

    assert browser._baseline_preload_script_id == "baseline-1"
    assert browser._baseline_preload_session_id == "session-1"
    assert len(browser._driver.calls) == 1
    method, params, _kwargs = browser._driver.calls[0]
    assert method == "script.addPreloadScript"
    assert params == {"functionDeclaration": "() => {}"}


def test_baseline_preload_registers_again_for_a_new_session():
    browser = make_browser(
        responses=[{"script": "baseline-1"}, {"script": "baseline-2"}]
    )
    assert browser._ensure_baseline_preload() is True

    browser._session_id = "session-2"

    assert browser._ensure_baseline_preload() is True
    assert browser._baseline_preload_script_id == "baseline-2"
    assert browser._baseline_preload_session_id == "session-2"
    assert len(browser._driver.calls) == 2


def test_baseline_preload_is_idempotent_under_concurrent_calls():
    browser = make_browser()
    browser._driver.delay = 0.05

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _item: browser._ensure_baseline_preload(),
                range(16),
            )
        )

    assert results == [True] * 16
    assert len(browser._driver.calls) == 1


def test_baseline_preload_retries_an_empty_script_id():
    browser = make_browser(responses=[{}, {"script": "baseline-2"}])

    assert browser._ensure_baseline_preload() is True
    assert browser._baseline_preload_script_id == "baseline-2"
    assert len(browser._driver.calls) == 2


def test_baseline_preload_warns_and_keeps_failure_retryable(caplog):
    browser = make_browser(
        responses=[RuntimeError("first"), RuntimeError("second")]
    )

    with caplog.at_level(logging.WARNING, logger="ruyipage"):
        assert browser._ensure_baseline_preload() is False

    assert browser._baseline_preload_script_id is None
    assert browser._baseline_preload_session_id is None
    assert "baseline preload" in caplog.text
    assert len(browser._driver.calls) == 2


def test_activate_session_records_session_and_ensures_baseline():
    browser = make_browser()
    browser._driver.session_id = ""
    browser._owns_session = False
    browser._ensure_baseline_preload = mock.Mock(return_value=True)

    browser._activate_session({"sessionId": "session-2"})

    assert browser._session_id == "session-2"
    assert browser._driver.session_id == "session-2"
    assert browser._owns_session is True
    browser._ensure_baseline_preload.assert_called_once_with()


def test_firefox_init_creates_baseline_registry_state(monkeypatch):
    browser = object.__new__(Firefox)
    browser._initialized = False
    browser._init_lock = threading.Lock()
    monkeypatch.setattr(Firefox, "_connect_or_launch", lambda self: None)
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)
    options = FirefoxOptions().set_port(65520)

    Firefox.__init__(browser, options)

    try:
        assert browser._baseline_preload_script_id is None
        assert browser._baseline_preload_session_id is None
        assert isinstance(
            browser._baseline_preload_lock,
            type(threading.Lock()),
        )
    finally:
        Firefox._BROWSERS.pop(browser._address, None)


def test_retained_probe_browser_registers_baseline(monkeypatch):
    address = "127.0.0.1:65521"
    driver = RecordingDriver([{"script": "probe-baseline"}])
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)

    browser = create_browser_from_probe_info(
        {
            "address": address,
            "driver": driver,
            "session_id": "probe-session",
            "session_owned": True,
            "contexts": [],
        }
    )

    try:
        assert browser._baseline_preload_script_id == "probe-baseline"
        assert browser._baseline_preload_session_id == "probe-session"
        assert len(driver.calls) == 1
    finally:
        Firefox._BROWSERS.pop(address, None)


def test_context_init_retries_browser_owned_baseline():
    driver = RecordingDriver()
    fake_browser = SimpleNamespace(
        driver=driver,
        options=SimpleNamespace(
            load_mode="normal",
            xpath_picker_enabled=False,
            action_visual_enabled=False,
            trace_enabled=False,
            failure_snapshot_enabled=False,
        ),
        _ensure_baseline_preload=mock.Mock(return_value=True),
    )
    page = FirefoxBase()

    page._init_context(fake_browser, "context-1")

    fake_browser._ensure_baseline_preload.assert_called_once_with()


def test_reconnect_registers_baseline_for_the_new_session(monkeypatch):
    class ReconnectDriver(RecordingDriver):
        def __init__(self, address):
            super().__init__([{"script": "reconnected-baseline"}])
            self.address = address
            self.session_id = ""

        def start(self, _url):
            return None

        def stop(self):
            return None

    browser = make_browser(session_id="old-session")
    browser._baseline_preload_script_id = "old-baseline"
    browser._baseline_preload_session_id = "old-session"
    browser._address = "127.0.0.1:65522"
    browser._driver.stop = mock.Mock()
    browser._teardown_proxy_auth = mock.Mock()
    browser._subscribe_events = mock.Mock()
    browser._setup_proxy_auth = mock.Mock()
    browser._refresh_tabs = mock.Mock()
    monkeypatch.setattr(browser_module, "BrowserBiDiDriver", ReconnectDriver)
    monkeypatch.setattr(
        browser_module,
        "get_bidi_ws_url",
        lambda _host, _port, timeout: "ws://test",
    )
    browser._create_session = lambda: browser._activate_session(
        {"sessionId": "new-session"}
    )

    browser.reconnect()

    assert browser._session_id == "new-session"
    assert browser._baseline_preload_session_id == "new-session"
    assert browser._baseline_preload_script_id == "reconnected-baseline"
    assert len(browser._driver.calls) == 1


def make_visual_page(enabled):
    driver = RecordingDriver([{"script": "visual-preload"}])
    browser = SimpleNamespace(
        options=SimpleNamespace(action_visual_enabled=enabled),
        _baseline_preload_script_id="baseline-preload",
        _baseline_preload_session_id="session-1",
    )
    page = FirefoxBase()
    page._browser = browser
    page._driver = SimpleNamespace(_browser_driver=driver)
    page.run_js = mock.Mock()
    return page, browser, driver


def test_disabled_action_visual_has_no_visual_preload_or_evaluation():
    page, browser, driver = make_visual_page(False)

    page._maybe_enable_action_visual()

    assert driver.calls == []
    assert not hasattr(browser, "_action_visual_global_preload_script_id")
    page.run_js.assert_not_called()


def test_enabled_action_visual_keeps_visual_preload_separate():
    page, browser, driver = make_visual_page(True)
    visual_script = page._get_action_visual_script()

    page._maybe_enable_action_visual()

    assert browser._baseline_preload_script_id == "baseline-preload"
    assert browser._action_visual_global_preload_script_id == "visual-preload"
    assert (
        browser._action_visual_global_preload_script_id
        != browser._baseline_preload_script_id
    )
    method, params, _kwargs = driver.calls[0]
    assert method == "script.addPreloadScript"
    assert params == {"functionDeclaration": visual_script}
    page.run_js.assert_called_once_with(f"({visual_script})()", as_expr=True)
