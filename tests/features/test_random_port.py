# -*- coding: utf-8 -*-

import json

import pytest

import ruyipage as ruyipage_module
import ruyipage._base.browser as browser_module

from ruyipage._base.browser import Firefox
from ruyipage._configs.firefox_options import FirefoxOptions


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def bind(self, address):
        self.address = address


def _make_browser(options):
    browser = Firefox.__new__(Firefox)
    browser._options = options
    browser._address = options.address
    browser._reserved_port = None
    return browser


def _fake_socket_factory(*args, **kwargs):
    return _FakeSocket()


def test_default_free_port_selection_uses_random_high_range(monkeypatch):
    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)

    browser = _make_browser(FirefoxOptions())

    port = browser._find_free_port()

    assert 10000 <= port <= 65535
    assert port != 9222


def test_set_port_disables_random_high_port_selection(monkeypatch):
    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)

    opts = FirefoxOptions().set_port(9222)
    browser = _make_browser(opts)

    assert opts.random_port is False
    assert browser._find_free_port() == 9222


def test_set_auto_port_keeps_existing_sequential_behavior(monkeypatch):
    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)

    opts = FirefoxOptions().set_auto_port(True)
    browser = _make_browser(opts)

    assert opts.random_port is False
    assert browser._find_free_port() == 9222


def test_set_random_port_uses_custom_range(monkeypatch):
    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)

    opts = FirefoxOptions().set_port(9222).set_random_port(start=12000, end=12000)
    browser = _make_browser(opts)

    assert opts.random_port is True
    assert opts.random_port_range == (12000, 12000)
    assert browser._find_free_port() == 12000


def test_set_random_port_rejects_invalid_range():
    with pytest.raises(ValueError):
        FirefoxOptions().set_random_port(start=10000, end=9999)


def test_existing_only_disables_random_port_mode():
    opts = FirefoxOptions().existing_only(True)

    assert opts.random_port is False
    assert opts.port == 9222


def test_launch_without_port_keeps_default_random_mode(monkeypatch):
    captured = {}

    class FakeFirefoxPage:
        def __init__(self, opts):
            captured["opts"] = opts

    monkeypatch.setattr(ruyipage_module, "FirefoxPage", FakeFirefoxPage)

    ruyipage_module.launch()

    assert captured["opts"].random_port is True


def test_launch_with_explicit_port_uses_fixed_port(monkeypatch):
    captured = {}

    class FakeFirefoxPage:
        def __init__(self, opts):
            captured["opts"] = opts

    monkeypatch.setattr(ruyipage_module, "FirefoxPage", FakeFirefoxPage)

    ruyipage_module.launch(port=9222)

    assert captured["opts"].random_port is False
    assert captured["opts"].port == 9222


def test_bidi_server_default_options_use_random_high_port(monkeypatch):
    from ruyipage._adapter import context_manager, remote_agent
    from ruyipage._adapter.bidi_server import BiDiServer
    from ruyipage._base import driver as driver_module

    commands = []
    waited = []
    ws_requests = []

    class FakeDriver:
        def __init__(self, address):
            self.address = address

        def start(self, ws_url):
            self.ws_url = ws_url

        def run(self, method, params=None):
            assert method == "browsingContext.getTree"
            return {"contexts": []}

    class FakeContextRegistry:
        def sync_from_tree(self, contexts):
            self.contexts = contexts

    class FakeContextEventAdapter:
        def __init__(self, driver, registry):
            self.driver = driver
            self.registry = registry

        def start(self):
            self.started = True

    monkeypatch.setattr(remote_agent, "launch_firefox", lambda cmd: commands.append(cmd))
    monkeypatch.setattr(
        remote_agent,
        "wait_for_firefox",
        lambda host, port, timeout=30: waited.append((host, port)) or True,
    )
    monkeypatch.setattr(
        remote_agent,
        "get_bidi_ws_url",
        lambda host, port, timeout=10: ws_requests.append((host, port))
        or "ws://{}:{}/session".format(host, port),
    )
    monkeypatch.setattr(driver_module, "BrowserBiDiDriver", FakeDriver)
    monkeypatch.setattr(context_manager, "ContextRegistry", FakeContextRegistry)
    monkeypatch.setattr(context_manager, "ContextEventAdapter", FakeContextEventAdapter)
    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)

    server = BiDiServer(FirefoxOptions())
    server.connect()

    selected_port = server._options.port
    assert 10000 <= selected_port <= 65535
    assert selected_port != 9222
    assert "--remote-debugging-port={}".format(selected_port) in commands[0]
    assert waited == [("127.0.0.1", selected_port)]
    assert ws_requests == [("127.0.0.1", selected_port)]


def test_windows_stuck_session_cleanup_kills_only_current_port(monkeypatch):
    opts = FirefoxOptions().set_address("127.0.0.1:12000").existing_only(True)
    browser = _make_browser(opts)
    browser._driver = None
    browser._process = None

    system_calls = []
    killed_ports = []

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(
        browser_module.os,
        "system",
        lambda command: system_calls.append(command) or 0,
    )
    monkeypatch.setattr(
        browser,
        "_kill_firefox_by_port",
        lambda port: killed_ports.append(port),
    )
    monkeypatch.setattr(browser_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(browser, "_is_port_open", lambda: False)

    browser._restart_firefox_for_stuck_session()

    assert killed_ports == [12000]
    assert not any("/im firefox.exe" in call.lower() for call in system_calls)


def test_windows_kill_firefox_by_port_targets_owning_pid(monkeypatch):
    browser = _make_browser(FirefoxOptions())
    killed = []

    def fake_check_output(command, stderr=None):
        script = command[-1]
        if "Get-CimInstance Win32_Process" in script:
            return json.dumps(
                [
                    {
                        "ProcessId": 4242,
                        "Name": "firefox.exe",
                        "CommandLine": "firefox.exe --remote-debugging-port=12000",
                    },
                    {
                        "ProcessId": 7777,
                        "Name": "not-firefox.exe",
                        "CommandLine": "not-firefox.exe",
                    },
                ]
            ).encode()
        if "Get-NetTCPConnection" in script:
            return json.dumps(
                [
                    {
                        "LocalAddress": "127.0.0.1",
                        "LocalPort": 12000,
                        "OwningProcess": 4242,
                    },
                    {
                        "LocalAddress": "127.0.0.1",
                        "LocalPort": 12001,
                        "OwningProcess": 7777,
                    },
                ]
            ).encode()
        raise AssertionError(script)

    def fake_run(command, **kwargs):
        killed.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(browser_module.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(browser_module.subprocess, "run", fake_run)

    browser._kill_firefox_by_port(12000)

    assert killed == [["taskkill", "/F", "/PID", "4242"]]
