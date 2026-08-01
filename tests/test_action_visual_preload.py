import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ruyipage._base.browser import Firefox


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
