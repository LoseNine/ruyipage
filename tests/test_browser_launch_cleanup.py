import threading

import pytest

from ruyipage._base import browser as browser_module
from ruyipage._base.browser import Firefox
from ruyipage._configs.firefox_options import FirefoxOptions
from ruyipage.errors import BrowserConnectError


class _RecordingProcess:
    def __init__(self, pid):
        self.pid = pid
        self.events = []

    def poll(self):
        return None

    def kill(self):
        self.events.append("kill")

    def terminate(self):
        self.events.append("terminate")

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))


@pytest.fixture(autouse=True)
def _clear_browser_state():
    Firefox._BROWSERS.clear()
    Firefox._RESERVED_PORTS.clear()
    yield
    Firefox._BROWSERS.clear()
    Firefox._RESERVED_PORTS.clear()


def test_failed_owned_launch_cleans_every_allocated_resource(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    processes = [_RecordingProcess(1001), _RecordingProcess(1002)]
    instances = []
    taskkill_calls = []

    def launch(self):
        profile.mkdir(exist_ok=True)
        self._auto_profile = str(profile)
        self._reserve_port(9222)
        self._process = processes.pop(0)
        Firefox._BROWSERS["stale"] = self
        instances.append(self)

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(Firefox, "_ensure_launch_port_available", lambda self: None)
    monkeypatch.setattr(Firefox, "_launch_browser", launch)
    monkeypatch.setattr(Firefox, "_wait_for_connection", lambda self: False)
    monkeypatch.setattr(
        browser_module.subprocess,
        "run",
        lambda command, **kwargs: taskkill_calls.append(command),
    )

    options = FirefoxOptions().set_address("127.0.0.1:9222")

    with pytest.raises(BrowserConnectError):
        Firefox(options)

    assert {
        tuple(command)
        for command in taskkill_calls
        if command[0].lower() == "taskkill"
    } == {
        ("taskkill", "/F", "/T", "/PID", "1001"),
        ("taskkill", "/F", "/T", "/PID", "1002"),
    }
    assert instances[-1]._process is None
    assert instances[-1]._auto_profile is None
    assert not profile.exists()
    assert 9222 not in Firefox._RESERVED_PORTS
    assert not Firefox._BROWSERS


def test_failed_existing_only_connection_never_terminates_external_browser(monkeypatch):
    external_process = _RecordingProcess(2001)
    instances = []
    taskkill_calls = []

    def try_connect(self):
        self._process = external_process
        instances.append(self)
        return False

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(Firefox, "_try_connect", try_connect)
    monkeypatch.setattr(
        browser_module.subprocess,
        "run",
        lambda command, **kwargs: taskkill_calls.append(command),
    )

    options = FirefoxOptions().set_address("127.0.0.1:9223").existing_only(True)

    with pytest.raises(BrowserConnectError):
        Firefox(options)

    assert external_process.events == []
    assert taskkill_calls == []
    assert instances[-1]._process is None


def _browser_for_quit(options, process):
    browser = object.__new__(Firefox)
    browser._quit_lock = threading.RLock()
    browser._driver = None
    browser._owns_session = False
    browser._process = process
    browser._options = options
    browser._auto_profile = None
    browser._address = options.address
    browser._initialized = True
    return browser


def test_force_quit_terminates_owned_process_tree(monkeypatch):
    process = _RecordingProcess(3001)
    taskkill_calls = []
    browser = _browser_for_quit(FirefoxOptions(), process)

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(
        browser_module.subprocess,
        "run",
        lambda command, **kwargs: taskkill_calls.append(command),
    )

    browser.quit(force=True)

    assert taskkill_calls == [["taskkill", "/F", "/T", "/PID", "3001"]]
    assert process.events == [("wait", 5)]
    assert browser._process is None


def test_force_quit_never_terminates_existing_only_external_browser(monkeypatch):
    process = _RecordingProcess(4001)
    taskkill_calls = []
    options = FirefoxOptions().existing_only(True)
    browser = _browser_for_quit(options, process)

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(
        browser_module.subprocess,
        "run",
        lambda command, **kwargs: taskkill_calls.append(command),
    )

    browser.quit(force=True)

    assert taskkill_calls == []
    assert process.events == []
    assert browser._process is None
