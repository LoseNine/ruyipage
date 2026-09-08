# -*- coding: utf-8 -*-
"""并发启动回归测试

覆盖 issue #31：多线程 launch 超过约 10 个实例时 Firefox 随机崩溃。
根因是「端口预留被提前释放」+「重启兜底原样复用旧端口」+「连错浏览器后
发 browser.close 关掉了别人的浏览器」三者叠加，这里逐条钉住。

全部使用假进程 / 假 driver，不依赖真实 Firefox。
"""

import socket
import threading

import pytest

from ruyipage._base import browser as browser_module
from ruyipage._base.browser import Firefox
from ruyipage._configs.firefox_options import FirefoxOptions
from ruyipage.errors import BrowserConnectError, BrowserLaunchError


class _FakeProcess:
    """始终存活的假进程，避免 _wait_for_connection 的宽限等待把它判死。"""

    def __init__(self, pid=1000):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return None


class _FakeSocket:
    """让 _find_free_port() 认为候选端口都可绑定，去掉对本机端口占用的依赖。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def bind(self, address):
        self.address = address


def _fake_socket_factory(*args, **kwargs):
    return _FakeSocket()


class _FakeDriver:
    """记录所有 BiDi 命令的假 driver。"""

    _lock = threading.Lock()
    _BROWSERS = {}

    def __init__(self, address=None, capabilities=None, ready=True):
        self.address = address
        self.session_id = None
        self.calls = []
        self.stopped = 0
        self._capabilities = capabilities or {}
        self._ready = ready

    def start(self, ws_url):
        self.ws_url = ws_url

    def mark_closing(self):
        self.calls.append("mark_closing")

    def stop(self):
        self.stopped += 1

    def run(self, method, params=None, timeout=None):
        self.calls.append(method)
        if method == "session.status":
            return {"ready": self._ready, "message": ""}
        if method == "session.new":
            return {"sessionId": "fake-session", "capabilities": self._capabilities}
        return {}


def _bare_browser(address, options=None):
    """构造一个不走 __init__ 的最小 Firefox 实例。"""
    browser = Firefox.__new__(Firefox)
    browser._options = options or FirefoxOptions()
    browser._options.set_address(address)
    browser._address = browser._options.address
    browser._driver = None
    browser._process = None
    browser._reserved_port = None
    browser._session_id = None
    browser._owns_session = False
    browser._session_ownership_verified = False
    browser._browser_pid = None
    browser._grace_spent = 0.0
    browser._auto_profile = None
    return browser


def _listening_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener, listener.getsockname()[1]


@pytest.fixture(autouse=True)
def _clear_browser_state():
    Firefox._BROWSERS.clear()
    Firefox._RESERVED_PORTS.clear()
    yield
    Firefox._BROWSERS.clear()
    Firefox._RESERVED_PORTS.clear()


# --------------------------------------------------------------------------
# (a) 重启兜底必须换端口
# --------------------------------------------------------------------------


def test_restart_fallback_launches_on_a_different_port(monkeypatch):
    """重启兜底不能原样复用旧端口。

    Firefox 的监听 socket 带 SO_REUSEADDR，Windows 上第二个进程可以绑定一个
    已在监听的端口且不报错，复用旧端口会让两个实例抢同一个端口。
    """
    launched_ports = []

    def launch(self):
        launched_ports.append(self._options.port)
        self._process = _FakeProcess()

    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)
    monkeypatch.setattr(Firefox, "_launch_browser", launch)
    monkeypatch.setattr(
        Firefox, "_wait_for_connection", lambda self, allow_grace=True: False
    )
    monkeypatch.setattr(
        Firefox, "_terminate_owned_process_tree", lambda self, timeout=5: None
    )

    with pytest.raises(BrowserConnectError):
        Firefox(FirefoxOptions())

    assert len(launched_ports) == 2, "应当有一次重启兜底"
    assert launched_ports[0] != launched_ports[1], "重启必须换端口"


def test_restart_fallback_keeps_explicit_fixed_port_when_free(monkeypatch):
    """固定端口是用户显式指定的，没被占用时不该擅自换掉。"""
    launched_ports = []

    def launch(self):
        launched_ports.append(self._options.port)
        self._process = _FakeProcess()

    monkeypatch.setattr(Firefox, "_launch_browser", launch)
    monkeypatch.setattr(
        Firefox, "_wait_for_connection", lambda self, allow_grace=True: False
    )
    monkeypatch.setattr(Firefox, "_is_port_open", lambda self: False)
    monkeypatch.setattr(
        Firefox, "_terminate_owned_process_tree", lambda self, timeout=5: None
    )

    with pytest.raises(BrowserConnectError):
        Firefox(FirefoxOptions().set_address("127.0.0.1:9331"))

    assert launched_ports == [9331, 9331]


# --------------------------------------------------------------------------
# (b) 握手失败后端口预留必须保持
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [RuntimeError("ws 握手失败"), BrowserConnectError("会话不可用")],
    ids=["generic_error", "connect_error"],
)
def test_handshake_failure_keeps_port_reservation(monkeypatch, raised):
    """握手失败只是中间态，进程和端口仍归我们所有，预留不能提前释放。"""
    listener, port = _listening_socket()
    try:
        browser = _bare_browser("127.0.0.1:{}".format(port))
        browser._reserve_port(port)

        def boom(host, port_, timeout=5):
            raise raised

        monkeypatch.setattr(browser_module, "get_bidi_ws_url", boom)

        if isinstance(raised, BrowserConnectError):
            with pytest.raises(BrowserConnectError):
                browser._try_connect()
        else:
            assert browser._try_connect() is False

        assert port in Firefox._RESERVED_PORTS
        assert browser._reserved_port == port
    finally:
        listener.close()


def test_reserved_port_is_not_offered_to_another_instance(monkeypatch):
    """预留还在，别的实例就选不走这个端口。"""
    listener, port = _listening_socket()
    try:
        browser = _bare_browser("127.0.0.1:{}".format(port))
        browser._reserve_port(port)

        def boom(host, port_, timeout=5):
            raise RuntimeError("ws 握手失败")

        monkeypatch.setattr(browser_module, "get_bidi_ws_url", boom)
        assert browser._try_connect() is False

        # 换成假 socket，让 bind 永远成功：这样能否选中该端口就只取决于
        # _RESERVED_PORTS，而不是本机端口是否真的被监听。
        monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)

        # 把候选范围收窄到只剩这一个端口：预留生效时必然找不到可用端口。
        other = _bare_browser("127.0.0.1:1")
        other._options.set_random_port(start=port, end=port)
        with pytest.raises(BrowserLaunchError):
            other._find_free_port()
    finally:
        listener.close()


def test_giving_up_startup_finally_releases_the_reservation(monkeypatch):
    """真正放弃启动时（_cleanup_failed_startup）才释放端口预留。"""
    reserved_during_launch = []

    def launch(self):
        reserved_during_launch.append(set(Firefox._RESERVED_PORTS))
        self._process = _FakeProcess()

    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)
    monkeypatch.setattr(Firefox, "_launch_browser", launch)
    monkeypatch.setattr(
        Firefox, "_wait_for_connection", lambda self, allow_grace=True: False
    )
    monkeypatch.setattr(
        Firefox, "_terminate_owned_process_tree", lambda self, timeout=5: None
    )

    with pytest.raises(BrowserConnectError):
        Firefox(FirefoxOptions())

    assert all(ports for ports in reserved_during_launch), "启动期间端口应处于预留状态"
    assert Firefox._RESERVED_PORTS == set(), "放弃启动后必须释放预留"


# --------------------------------------------------------------------------
# (c) 连到别人的浏览器：不发 browser.close，换端口重试
# --------------------------------------------------------------------------


def test_foreign_browser_session_is_ended_never_closed(tmp_path):
    """moz:profile 对不上说明连到了别人的浏览器：只 session.end，绝不 browser.close。"""
    mine = tmp_path / "mine"
    theirs = tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()

    browser = _bare_browser("127.0.0.1:9341")
    browser._options.set_profile(str(mine))
    driver = _FakeDriver(
        capabilities={"moz:profile": str(theirs), "moz:processID": 4242}
    )
    browser._driver = driver

    with pytest.raises(BrowserConnectError) as excinfo:
        browser._create_session()

    assert browser_module._FOREIGN_BROWSER_MARKER in str(excinfo.value)
    assert "session.end" in driver.calls, "必须结束误建的会话"
    assert "browser.close" not in driver.calls, "绝不能关掉别人的浏览器"
    assert driver.stopped == 1, "必须断开连接"
    assert browser._driver is None
    assert browser._session_id is None
    assert browser._owns_session is False
    assert browser._session_ownership_verified is False


def test_own_browser_session_passes_ownership_check(monkeypatch, tmp_path):
    """profile 一致时正常建会话，并标记归属已确认。"""
    mine = tmp_path / "mine"
    mine.mkdir()

    browser = _bare_browser("127.0.0.1:9342")
    browser._options.set_profile(str(mine))
    driver = _FakeDriver(capabilities={"moz:profile": str(mine), "moz:processID": 7})
    browser._driver = driver

    monkeypatch.setattr(Firefox, "_ensure_baseline_preload", lambda self: True)

    browser._create_session()

    assert browser._session_id == "fake-session"
    assert browser._session_ownership_verified is True
    assert "browser.close" not in driver.calls


def test_missing_moz_profile_does_not_block_startup(monkeypatch, tmp_path):
    """旧内核不返回 moz:profile 时放行，不因校验手段缺失就拒绝一次正常启动。"""
    mine = tmp_path / "mine"
    mine.mkdir()

    browser = _bare_browser("127.0.0.1:9343")
    browser._options.set_profile(str(mine))
    driver = _FakeDriver(capabilities={})
    browser._driver = driver

    monkeypatch.setattr(Firefox, "_ensure_baseline_preload", lambda self: True)

    browser._create_session()

    assert browser._session_id == "fake-session"
    # 没校验成功就不该被当作"确认是自己的浏览器"。
    assert browser._session_ownership_verified is False


def test_existing_only_attach_skips_ownership_check(monkeypatch, tmp_path):
    """用户显式 attach 外部浏览器时没有"自己的 profile"可比，跳过校验。"""
    browser = _bare_browser(
        "127.0.0.1:9344", options=FirefoxOptions().existing_only(True)
    )
    browser._options.set_profile(str(tmp_path / "mine"))
    driver = _FakeDriver(
        capabilities={"moz:profile": str(tmp_path / "theirs"), "moz:processID": 9}
    )
    browser._driver = driver

    monkeypatch.setattr(Firefox, "_ensure_baseline_preload", lambda self: True)

    browser._create_session()

    assert browser._session_id == "fake-session"


def test_orphan_fallback_refuses_browser_close_on_unverified_browser(monkeypatch):
    """归属未确认时，孤儿会话兜底不许发 browser.close——这正是故障被放大的原因。

    注意 browser.close 是发在「重连时新建的 driver」上的，不是发在进入
    _create_session 时那个 driver 上。因此这里必须放行 get_bidi_ws_url 并
    捕获重连用的 driver：若把 get_bidi_ws_url 打成抛异常，代码根本走不到
    browser.close，断言就永远成立而测不出任何东西。
    """
    browser = _bare_browser("127.0.0.1:9345")
    browser._driver = _FakeDriver(ready=False)
    reconnect_drivers = []

    def new_fails(driver_, capabilities=None, user_prompt_handler=None):
        driver_.run("session.new")
        raise RuntimeError("session not created: maximum number of sessions")

    def make_driver(address, shared=True):
        driver = _FakeDriver(address=address, ready=False)
        reconnect_drivers.append(driver)
        return driver

    monkeypatch.setattr(browser_module.bidi_session, "new", new_fails)
    monkeypatch.setattr(browser_module, "BrowserBiDiDriver", make_driver)
    monkeypatch.setattr(
        browser_module, "get_bidi_ws_url", lambda host, port, timeout=5: "ws://fake"
    )
    monkeypatch.setattr(browser_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(BrowserConnectError) as excinfo:
        browser._create_session()

    # 先断言行为，再断言标记：反过来写会让缺少标记的旧版本在触及
    # 真正的行为断言之前就抛 AttributeError，测试就退化成「新 API 存在」检查。
    assert not any(
        "browser.close" in driver.calls for driver in reconnect_drivers
    ), "对归属未确认的浏览器发了 browser.close，会关掉别人的浏览器"
    assert browser_module._FOREIGN_BROWSER_MARKER in str(excinfo.value)


def test_orphan_fallback_still_closes_explicitly_attached_browser(monkeypatch):
    """existing_only 是用户显式指定的地址，孤儿兜底照旧允许 browser.close。"""
    browser = _bare_browser(
        "127.0.0.1:9346", options=FirefoxOptions().existing_only(True)
    )
    browser._driver = _FakeDriver(ready=False)
    reconnect_drivers = []

    def new_fails(driver_, capabilities=None, user_prompt_handler=None):
        driver_.run("session.new")
        raise RuntimeError("session not created: maximum number of sessions")

    def make_driver(address, shared=True):
        driver = _FakeDriver(address=address, ready=False)
        reconnect_drivers.append(driver)
        return driver

    monkeypatch.setattr(browser_module.bidi_session, "new", new_fails)
    monkeypatch.setattr(browser_module, "BrowserBiDiDriver", make_driver)
    monkeypatch.setattr(
        browser_module, "get_bidi_ws_url", lambda host, port, timeout=5: "ws://fake"
    )
    monkeypatch.setattr(browser_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(BrowserConnectError):
        browser._create_session()

    assert any("browser.close" in d.calls for d in reconnect_drivers)


def test_foreign_browser_triggers_relaunch_on_a_new_port(monkeypatch):
    """端到端：连到别人的浏览器 -> 换端口重启 -> 成功。"""
    launched_ports = []

    def launch(self):
        launched_ports.append(self._options.port)
        self._process = _FakeProcess()

    def try_connect(self):
        if len(launched_ports) == 1:
            # 刻意用字面量而不是 browser_module._FOREIGN_BROWSER_MARKER：
            # 引用新常量会让旧版本在测试自身的辅助函数里就抛 AttributeError，
            # 从而掩盖「没有换端口重启」这个真正要钉住的行为。
            raise BrowserConnectError(
                "__foreign_browser__ {} 上的 Firefox 不是本实例启动的".format(
                    self._address
                )
            )
        return True

    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)
    monkeypatch.setattr(Firefox, "_launch_browser", launch)
    monkeypatch.setattr(Firefox, "_try_connect", try_connect)
    monkeypatch.setattr(
        Firefox, "_terminate_owned_process_tree", lambda self, timeout=5: None
    )
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)

    browser = Firefox(FirefoxOptions())

    assert len(launched_ports) == 2
    assert launched_ports[0] != launched_ports[1]
    assert browser._address.endswith(":{}".format(launched_ports[1]))


# --------------------------------------------------------------------------
# 并发：多个实例不能拿到同一个端口
# --------------------------------------------------------------------------


def test_concurrent_launches_never_share_a_port(monkeypatch):
    """把候选端口收窄到与线程数相等：任何一次端口重复都会被这里抓到。"""
    thread_count = 12
    port_start = 21000
    port_end = port_start + thread_count - 1

    launched = []
    launched_lock = threading.Lock()

    def launch(self):
        with launched_lock:
            launched.append(self._options.port)
        self._process = _FakeProcess()

    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)
    monkeypatch.setattr(Firefox, "_launch_browser", launch)
    monkeypatch.setattr(Firefox, "_try_connect", lambda self: True)
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)

    browsers = []
    errors = []
    results_lock = threading.Lock()

    def worker():
        try:
            opts = FirefoxOptions().set_random_port(start=port_start, end=port_end)
            browser = Firefox(opts)
            with results_lock:
                browsers.append(browser)
        except Exception as exc:
            with results_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, daemon=True) for _ in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        # join 的返回值是 None，不检查 is_alive() 的话卡死的线程会被静默忽略，
        # 最后报成一个令人费解的数量断言失败。
        assert not thread.is_alive(), "并发启动线程超时未结束"

    assert not errors, "并发启动不应报错: {}".format(errors)
    assert len(launched) == thread_count
    assert len(set(launched)) == thread_count, "端口被重复分配: {}".format(
        sorted(launched)
    )
    assert len({browser._address for browser in browsers}) == thread_count


# --------------------------------------------------------------------------
# (d) 超时不应触发破坏性重启
# --------------------------------------------------------------------------


def test_slow_startup_waits_in_grace_period_while_process_is_alive(monkeypatch):
    """初始 retry 预算耗尽后，只要进程还活着就继续等，不判死重启。

    这是把正常实例误杀的直接原因：并发高时 Windows 冷启动会明显变慢。
    """
    browser = _bare_browser("127.0.0.1:9347")
    browser._process = _FakeProcess()
    browser._options.set_retry(times=0, interval=0.01)

    started = browser_module.time.time()
    # 只在初始 retry 预算（下限 1s）耗尽之后才就绪，强制走宽限等待分支。
    ready_at = started + 1.3

    monkeypatch.setattr(
        Firefox, "_try_connect", lambda self: browser_module.time.time() >= ready_at
    )

    assert browser._wait_for_connection() is True
    assert browser_module.time.time() - started >= 1.0


def test_grace_period_is_skipped_when_process_already_died(monkeypatch):
    """进程真的退出了就立刻放弃，不白等满宽限期。"""

    class _DeadProcess:
        pid = 1
        returncode = 1

        def poll(self):
            return 1

    browser = _bare_browser("127.0.0.1:9348")
    browser._process = _DeadProcess()
    browser._options.set_retry(times=0, interval=0.01)

    monkeypatch.setattr(Firefox, "_try_connect", lambda self: False)

    started = browser_module.time.time()
    assert browser._wait_for_connection() is False
    # 远小于 _LAUNCH_GRACE_TIMEOUT，说明进程退出后立即返回而非空等。
    assert browser_module.time.time() - started < Firefox._LAUNCH_GRACE_TIMEOUT / 3


# --------------------------------------------------------------------------
# (e) quit() 必须归还端口预留，否则端口范围会被逐轮耗尽
# --------------------------------------------------------------------------


class _QuitableProcess(_FakeProcess):
    """支持 terminate/wait 的假进程，供 quit() 使用。"""

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0


def _patch_successful_launch(monkeypatch):
    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)
    monkeypatch.setattr(
        Firefox, "_launch_browser", lambda self: setattr(self, "_process", _QuitableProcess())
    )
    monkeypatch.setattr(Firefox, "_try_connect", lambda self: True)
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)
    monkeypatch.setattr(
        Firefox,
        "_terminate_owned_process_tree",
        lambda self, timeout=5: setattr(self, "_process", None),
    )


def test_quit_releases_the_port_reservation(monkeypatch):
    """quit() 后端口重新可用，进程内预留必须一并撤销。"""
    _patch_successful_launch(monkeypatch)

    browser = Firefox(FirefoxOptions())
    port = browser._options.port
    assert port in Firefox._RESERVED_PORTS

    browser.quit(force=True)

    assert port not in Firefox._RESERVED_PORTS, "quit() 泄漏了端口预留"
    assert browser._reserved_port is None


def test_repeated_rounds_do_not_exhaust_the_port_range(monkeypatch):
    """反复 launch/quit 不能把端口范围耗尽。

    这是 issue #31 场景里“每轮结束后 quit(force=True) 再开下一轮”的直接后果：
    预留只加不减时，可用端口数会随轮次单调递减，最终所有启动都失败。
    """
    _patch_successful_launch(monkeypatch)

    start, end = 21500, 21504  # 只有 5 个候选端口
    for round_index in range(len(range(start, end + 1)) * 3):
        browser = Firefox(
            FirefoxOptions().set_random_port(start=start, end=end)
        )
        browser.quit(force=True)

    assert Firefox._RESERVED_PORTS == set(), "预留未归还: {}".format(
        sorted(Firefox._RESERVED_PORTS)
    )


def test_quit_releases_reservation_for_attached_browser(monkeypatch):
    """existing_only 的 quit() 分支同样要归还预留。"""
    monkeypatch.setattr(Firefox, "_detach_on_exit", lambda self: None)

    browser = _bare_browser(
        "127.0.0.1:9351", options=FirefoxOptions().existing_only(True)
    )
    browser._quit_lock = threading.Lock()
    browser._reserve_port(9351)

    browser.quit()

    assert 9351 not in Firefox._RESERVED_PORTS
    assert browser._reserved_port is None


def test_exit_cleanup_without_driver_releases_reservation(monkeypatch):
    """解释器退出时若连接已断开，也要归还预留。"""
    browser = _bare_browser("127.0.0.1:9352")
    browser._reserve_port(9352)
    browser._driver = None
    browser._auto_profile = None
    monkeypatch.setattr(
        Firefox, "_terminate_owned_process_tree", lambda self, timeout=5: None
    )

    browser._cleanup_on_exit()

    assert 9352 not in Firefox._RESERVED_PORTS


# --------------------------------------------------------------------------
# (f) 宽限等待不能把「Firefox 根本起不来」的失败拖成两倍时间
# --------------------------------------------------------------------------


def _patch_never_connects(monkeypatch):
    monkeypatch.setattr(browser_module.socket, "socket", _fake_socket_factory)
    monkeypatch.setattr(
        Firefox, "_launch_browser", lambda self: setattr(self, "_process", _FakeProcess())
    )
    monkeypatch.setattr(Firefox, "_try_connect", lambda self: False)
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)
    monkeypatch.setattr(
        Firefox,
        "_terminate_owned_process_tree",
        lambda self, timeout=5: setattr(self, "_process", None),
    )


def test_grace_period_is_not_paid_twice(monkeypatch):
    """宽限期是一份总预算，不能每轮各付一份。

    _connect_or_launch 会「首轮启动 -> 换端口重启」两次调用等待逻辑。
    两轮各给一份宽限期，会让 Firefox 根本起不来的用户白等两倍时间；
    共享一份预算则既能在真正需要的那轮上花掉，又不放大失败耗时。
    """
    _patch_never_connects(monkeypatch)

    grace = 3.0
    opts = FirefoxOptions().set_retry(times=0, interval=0.1, grace=grace)
    started = browser_module.time.time()
    with pytest.raises(BrowserConnectError):
        Firefox(opts)
    elapsed = browser_module.time.time() - started

    # 预算 ~0.1s x2 + 一份宽限期；两份宽限期会超过 2 * grace。
    assert elapsed < 2 * grace, "宽限期被重复计入: {:.1f}s".format(elapsed)
    assert elapsed >= grace, "最后一次尝试没有进入宽限等待: {:.1f}s".format(elapsed)


def test_grace_zero_disables_the_wait_entirely(monkeypatch):
    """set_retry(grace=0) 必须完全关闭宽限等待，恢复快速失败。"""
    _patch_never_connects(monkeypatch)

    opts = FirefoxOptions().set_retry(times=0, interval=0.2, grace=0)
    started = browser_module.time.time()
    with pytest.raises(BrowserConnectError):
        Firefox(opts)
    elapsed = browser_module.time.time() - started

    # 只剩两轮 retry 预算：_wait_for_connection 的下限是 max(1.0, ...)，
    # 首轮 + 重启兜底各 1s，因此约 2s 是「完全没有宽限等待」的正确耗时。
    # 用 _LAUNCH_GRACE_MIN 作上界，确保断言真正排除了宽限期。
    assert elapsed < 2.0 + Firefox._LAUNCH_GRACE_MIN, (
        "grace=0 仍在等待: {:.1f}s".format(elapsed)
    )


def test_grace_scales_with_the_retry_budget():
    """宽限期按 retry 预算缩放，且夹在下限与上限之间。"""
    tight = _bare_browser("127.0.0.1:9361")
    tight._options.set_retry(times=0, interval=0.5)
    assert tight._launch_grace_timeout() == Firefox._LAUNCH_GRACE_MIN

    default = _bare_browser("127.0.0.1:9362")
    assert default._launch_grace_timeout() == Firefox._LAUNCH_GRACE_TIMEOUT

    explicit = _bare_browser("127.0.0.1:9363")
    explicit._options.set_retry(grace=7)
    assert explicit._launch_grace_timeout() == 7.0


# --------------------------------------------------------------------------
# (g) 归属未确认时仍要保留孤儿会话的恢复能力
# --------------------------------------------------------------------------


def test_orphan_fallback_kills_own_process_instead_of_closing_others(monkeypatch):
    """归属未确认但进程是自己拉起来的：杀自己的进程树，不发 browser.close。

    这样既不会关掉别人的浏览器，也不丢掉孤儿会话的恢复能力。
    """
    browser = _bare_browser("127.0.0.1:9355")
    browser._driver = _FakeDriver(ready=False)
    browser._process = _FakeProcess()
    reconnect_drivers = []
    killed = []

    def new_fails(driver_, capabilities=None, user_prompt_handler=None):
        driver_.run("session.new")
        raise RuntimeError("session not created: maximum number of sessions")

    def make_driver(address, shared=True):
        driver = _FakeDriver(address=address, ready=False)
        reconnect_drivers.append(driver)
        return driver

    monkeypatch.setattr(browser_module.bidi_session, "new", new_fails)
    monkeypatch.setattr(browser_module, "BrowserBiDiDriver", make_driver)
    monkeypatch.setattr(
        browser_module, "get_bidi_ws_url", lambda host, port, timeout=5: "ws://fake"
    )
    monkeypatch.setattr(browser_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        Firefox,
        "_terminate_owned_process_tree",
        lambda self, timeout=5: killed.append(True),
    )

    with pytest.raises(BrowserConnectError):
        browser._create_session()

    assert killed, "应当终止本实例自己启动的进程树"
    assert not any(
        "browser.close" in driver.calls for driver in reconnect_drivers
    ), "不能对归属未确认的浏览器发 browser.close"


def test_owns_launched_process_requires_a_live_own_process():
    """_owns_launched_process 的三种边界。"""
    mine = _bare_browser("127.0.0.1:9356")
    mine._process = _FakeProcess()
    assert mine._owns_launched_process() is True

    none = _bare_browser("127.0.0.1:9357")
    none._process = None
    assert none._owns_launched_process() is False

    attached = _bare_browser(
        "127.0.0.1:9358", options=FirefoxOptions().existing_only(True)
    )
    attached._process = _FakeProcess()
    assert attached._owns_launched_process() is False


# --------------------------------------------------------------------------
# (h) 「连到别人的浏览器」用异常类型判定，不依赖消息里的字符串
# --------------------------------------------------------------------------


def test_foreign_browser_error_is_detected_by_type_not_message():
    """消息被改写/翻译后，类型判定仍然成立。"""
    typed = browser_module._ForeignBrowserError("消息里没有任何标记")
    assert browser_module._is_foreign_browser_error(typed) is True
    # 仍兼容旧的字符串标记形式
    legacy = BrowserConnectError(
        "{} 旧格式".format(browser_module._FOREIGN_BROWSER_MARKER)
    )
    assert browser_module._is_foreign_browser_error(legacy) is True
    assert browser_module._is_foreign_browser_error(
        BrowserConnectError("普通连接失败")
    ) is False


def test_foreign_browser_error_is_still_a_browser_connect_error():
    """用户既有的 except BrowserConnectError 不能被破坏。"""
    assert issubclass(browser_module._ForeignBrowserError, BrowserConnectError)


# --------------------------------------------------------------------------
# (i) 用真实 bind 探测钉住「重启不能落到别人正在监听的端口」
# --------------------------------------------------------------------------


def test_so_reuseaddr_lets_a_second_socket_bind_a_listening_port():
    """钉住整个修复赖以成立的前提。

    Firefox 的监听 socket 无条件设置 SO_REUSEADDR。若某个平台上第二个
    进程无法绑定一个已在监听的端口，那「重启复用旧端口」本身就是安全的，
    这个修复的前提也就不成立——那时应当重新审视 _rotate_launch_port()。
    """
    first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    first.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    first.bind(("127.0.0.1", 0))
    first.listen(1)
    port = first.getsockname()[1]

    second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    second.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        second.bind(("127.0.0.1", port))
        second.listen(1)
        double_bound = True
    except OSError:
        double_bound = False
    finally:
        second.close()
        first.close()

    if not double_bound:
        pytest.skip("本平台拒绝 SO_REUSEADDR 双重绑定")
    assert double_bound


def test_restart_never_reuses_a_port_held_by_another_live_instance(monkeypatch):
    """issue #31 的真实形态：重启不能落到另一个活实例持有的端口。

    这里刻意不打假 socket，用真实 bind 探测：只有这样「端口可用」才等于
    「端口真的能绑」，而不是退化成「不在 _RESERVED_PORTS 里」。
    """
    other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    other.bind(("127.0.0.1", 0))
    other.listen(1)
    taken = other.getsockname()[1]

    launched = []

    def launch(self):
        launched.append(self._options.port)
        self._process = _FakeProcess()

    monkeypatch.setattr(Firefox, "_launch_browser", launch)
    monkeypatch.setattr(
        Firefox, "_wait_for_connection", lambda self, allow_grace=True: False
    )
    monkeypatch.setattr(
        Firefox, "_terminate_owned_process_tree", lambda self, timeout=5: None
    )
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)

    try:
        with pytest.raises(BrowserConnectError):
            Firefox(
                FirefoxOptions().set_random_port(start=taken - 2, end=taken + 2)
            )
        assert len(launched) == 2, "应当有一次重启兜底"
        assert taken not in launched, "重启落到了别人正在监听的端口: {}".format(
            launched
        )
        assert launched[0] != launched[1], "重启必须换端口"
    finally:
        other.close()


# --------------------------------------------------------------------------
# (j) 端口撞车时不能共用同一个 driver，否则后来者会掐断先来者的连接
# --------------------------------------------------------------------------


def test_launched_browsers_never_share_a_driver_object():
    """自己启动的实例必须各用各的 driver。

    driver 单例按 address 复用；并发启动时两个实例可能落在同一个端口上，
    共用同一个 driver 会让后来者的 stop() 直接关掉先来者的 WebSocket——
    这正是本次修复要避免的故障，绝不能由修复自身制造出来。
    """
    from ruyipage._base.driver import BrowserBiDiDriver

    BrowserBiDiDriver._BROWSERS.clear()
    try:
        winner = BrowserBiDiDriver("127.0.0.1:48010", shared=False)
        loser = BrowserBiDiDriver("127.0.0.1:48010", shared=False)
        assert winner is not loser, "自启动实例共用了同一个 driver"

        winner._is_running = True
        try:
            loser.stop()
        except Exception:
            pass
        assert winner._is_running is True, "后来者的 stop() 掐断了先来者的连接"
    finally:
        BrowserBiDiDriver._BROWSERS.clear()


def test_attached_browsers_still_share_a_driver_object():
    """显式 attach 同一个地址仍应复用连接（保持既有行为）。"""
    from ruyipage._base.driver import BrowserBiDiDriver

    BrowserBiDiDriver._BROWSERS.clear()
    try:
        first = BrowserBiDiDriver("127.0.0.1:48011")
        second = BrowserBiDiDriver("127.0.0.1:48011")
        assert first is second
    finally:
        BrowserBiDiDriver._BROWSERS.clear()


# --------------------------------------------------------------------------
# (k) 宽限等待期间进程被并发置空，不能抛 AttributeError
# --------------------------------------------------------------------------


def test_grace_loop_survives_process_cleared_by_another_thread(monkeypatch):
    """宽限期最长 30s 且不持锁，其它线程的 quit() 随时可能把 _process 置空。"""
    browser = _bare_browser("127.0.0.1:9370")
    browser._process = _FakeProcess()
    browser._options.set_retry(times=0, interval=0.01, grace=5)

    def clear_then_fail(self):
        # 模拟另一个线程在宽限等待中途完成了 quit()
        self._process = None
        return False

    monkeypatch.setattr(Firefox, "_try_connect", clear_then_fail)

    # 不能抛 AttributeError；应当干净地返回 False
    assert browser._wait_for_connection() is False


# --------------------------------------------------------------------------
# (m) Windows 上 Popen 记录的 PID 是已退出的 launcher stub，强杀必须反查真实主进程
# --------------------------------------------------------------------------


class _ExitedStub:
    """firefox.exe launcher stub：把自己重新拉起后立刻退出。"""

    pid = 4000
    returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def _fake_process_table(rows):
    """伪造 Win32_Process 查询结果，rows 为 (pid, ppid, cmdline)。"""
    import json

    payload = json.dumps(
        [
            {"ProcessId": pid, "ParentProcessId": ppid, "CommandLine": cmdline}
            for pid, ppid, cmdline in rows
        ]
    ).encode("utf-8")

    def check_output(cmd, stderr=None, timeout=None):
        assert "Win32_Process" in cmd[-1]
        return payload

    return check_output


def test_windows_kill_targets_the_real_root_not_the_exited_stub(monkeypatch, tmp_path):
    """记录的 PID 已退出时，taskkill 必须落到按 profile 反查出的真实主进程上。

    这是 issue #31 失败路径每轮漏一整个 Firefox 的直接原因：对着死 PID 发
    taskkill /T 一个进程都杀不到，且旧代码在 poll() 非 None 时连 taskkill
    都不会发。
    """
    profile = tmp_path / "profile_a"
    profile.mkdir()
    other_profile = tmp_path / "profile_b"

    browser = _bare_browser("127.0.0.1:9380")
    browser._options.set_profile(str(profile))
    browser._options.set_browser_path(r"D:\ruyi-firefox\firefox.exe")
    browser._process = _ExitedStub()

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(
        browser_module.subprocess,
        "check_output",
        _fake_process_table(
            [
                # 别人的 Firefox：同名进程、不同 profile，不能碰
                (5000, 1, 'firefox.exe --remote-debugging-port=1 --profile "{}"'.format(other_profile)),
                # 我们的真实主进程（stub 4000 已退出，它是 4000 的孩子）
                (4100, 4000, 'firefox.exe --remote-debugging-port=9380 --profile "{}"'.format(profile)),
                # 我们的内容进程：命令行没有 --profile，跟主进程一起被 /T 带走
                (4101, 4100, "firefox.exe -contentproc -parentPid 4100 -isForBrowser"),
            ]
        ),
    )
    killed = []
    monkeypatch.setattr(
        browser_module.subprocess,
        "run",
        lambda cmd, **kwargs: killed.append(int(cmd[cmd.index("/PID") + 1])),
    )

    browser._terminate_owned_process_tree()

    assert 4100 in killed, "必须杀到按 profile 反查出的真实主进程"
    assert 5000 not in killed, "不能杀别人的 Firefox"
    assert 4101 not in killed, "内容进程由 taskkill /T 连带处理，不单独列出"
    assert 4000 not in killed, "已退出的 stub 不必再发 taskkill"
    assert browser._process is None


def test_grace_period_survives_an_exited_launcher_stub_on_windows(monkeypatch, tmp_path):
    """Windows 上 Popen 记录的 stub 已退出，宽限期不能据此判死。

    否则宽限期在 Windows 上形同虚设：retry 预算一到就换端口重启，把一个
    正在慢慢冷启动的正常 Firefox 杀掉再来一次，只会让并发更拥堵。
    """
    browser = _bare_browser("127.0.0.1:9384")
    browser._options.set_profile(str(tmp_path))
    browser._options.set_retry(times=0, interval=0.01, grace=5)
    browser._process = _ExitedStub()

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    # 进程表里能按 profile 找到真实主进程 -> 视为存活
    monkeypatch.setattr(
        Firefox, "_find_own_firefox_pids_windows", lambda self: {4100}
    )
    started = browser_module.time.time()
    ready_at = started + 1.4  # 超过 1s 的 retry 下限，必须靠宽限期才能等到

    monkeypatch.setattr(
        Firefox, "_try_connect", lambda self: browser_module.time.time() >= ready_at
    )

    assert browser._wait_for_connection() is True


def test_grace_period_gives_up_when_no_real_process_exists_on_windows(monkeypatch, tmp_path):
    """stub 已退出且进程表里也找不到自己的 Firefox：确实死了，立刻放弃。"""
    browser = _bare_browser("127.0.0.1:9385")
    browser._options.set_profile(str(tmp_path))
    browser._options.set_retry(times=0, interval=0.01, grace=5)
    browser._process = _ExitedStub()

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(Firefox, "_find_own_firefox_pids_windows", lambda self: set())
    monkeypatch.setattr(Firefox, "_try_connect", lambda self: False)

    started = browser_module.time.time()
    assert browser._wait_for_connection() is False
    assert browser_module.time.time() - started < 3


def test_liveness_probe_is_throttled(monkeypatch, tmp_path):
    """PowerShell 查进程表不便宜，宽限期轮询里必须节流。"""
    browser = _bare_browser("127.0.0.1:9386")
    browser._options.set_profile(str(tmp_path))
    browser._process = _ExitedStub()
    probes = []

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(
        Firefox,
        "_find_own_firefox_pids_windows",
        lambda self: probes.append(1) or {4100},
    )

    for _ in range(5):
        assert browser._launched_browser_alive() is True

    assert len(probes) == 1


def test_windows_kill_also_uses_moz_process_id_when_known(monkeypatch, tmp_path):
    """连接成功后拿到过 moz:processID，即便进程表查不到也要杀它。"""
    browser = _bare_browser("127.0.0.1:9381")
    browser._options.set_profile(str(tmp_path))
    browser._process = _ExitedStub()
    browser._browser_pid = 4200

    monkeypatch.setattr(browser_module.sys, "platform", "win32")
    monkeypatch.setattr(browser_module.subprocess, "check_output", _fake_process_table([]))
    killed = []
    monkeypatch.setattr(
        browser_module.subprocess,
        "run",
        lambda cmd, **kwargs: killed.append(int(cmd[cmd.index("/PID") + 1])),
    )

    browser._terminate_owned_process_tree()

    assert killed == [4200]
    assert browser._browser_pid is None


def test_windows_kill_survives_a_failed_process_table_query(monkeypatch, tmp_path):
    """PowerShell 查询失败时退回到只杀记录中的 PID，不能抛异常。"""
    browser = _bare_browser("127.0.0.1:9382")
    browser._options.set_profile(str(tmp_path))
    browser._process = _FakeProcess(pid=4300)

    monkeypatch.setattr(browser_module.sys, "platform", "win32")

    def boom(*args, **kwargs):
        raise OSError("powershell missing")

    monkeypatch.setattr(browser_module.subprocess, "check_output", boom)
    killed = []
    monkeypatch.setattr(
        browser_module.subprocess,
        "run",
        lambda cmd, **kwargs: killed.append(int(cmd[cmd.index("/PID") + 1])),
    )

    browser._terminate_owned_process_tree()

    assert killed == [4300]


def test_session_capabilities_record_the_real_browser_pid(monkeypatch, tmp_path):
    """moz:processID 是 Windows 上唯一可靠的主进程 PID 来源，建会话时要收下。"""
    browser = _bare_browser("127.0.0.1:9383")
    browser._options.set_profile(str(tmp_path))
    browser._driver = _FakeDriver(
        capabilities={"moz:profile": str(tmp_path), "moz:processID": 31337}
    )
    monkeypatch.setattr(Firefox, "_ensure_baseline_preload", lambda self: True)

    browser._create_session()

    assert browser._browser_pid == 31337


# --------------------------------------------------------------------------
# (n) 建会话之后才失败的连接，撤下时必须 session.end，否则自己的 Firefox 上留孤儿
# --------------------------------------------------------------------------


def _try_connect_after_session_new(monkeypatch, browser, driver, fail_with):
    """走一遍真实的 _try_connect：session.new 成功，随后一步失败。"""

    class _Sock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            pass

    monkeypatch.setattr(browser_module.socket, "socket", lambda *a, **k: _Sock())
    monkeypatch.setattr(browser_module, "get_bidi_ws_url", lambda h, p, timeout=5: "ws://x")
    monkeypatch.setattr(browser_module, "BrowserBiDiDriver", lambda address, shared=True: driver)
    monkeypatch.setattr(Firefox, "_ensure_baseline_preload", lambda self: True)
    monkeypatch.setattr(Firefox, "_subscribe_events", lambda self: None)
    monkeypatch.setattr(Firefox, "_setup_proxy_auth", lambda self: None)
    monkeypatch.setattr(Firefox, "_setup_download_behavior", lambda self: None)
    monkeypatch.setattr(Firefox, "_wait_for_initial_context", fail_with)
    browser._proxy_auth_intercept_id = None
    browser._proxy_auth_subscription_id = None
    return browser._try_connect()


def test_failed_connect_after_session_new_ends_the_session(monkeypatch, tmp_path):
    """首个 browsingContext 等不到时，撤下连接必须先 session.end。

    高并发冷启动下 Firefox 出首个窗口很慢，这是最常见的失败点。不结束会话
    的话，下一次重连会撞上「maximum number of sessions」，白花 6 秒重试，
    最后还得 browser.close 重启自己的浏览器。
    """
    browser = _bare_browser("127.0.0.1:9390")
    browser._options.set_profile(str(tmp_path))
    driver = _FakeDriver(capabilities={"moz:profile": str(tmp_path)})

    result = _try_connect_after_session_new(
        monkeypatch, browser, driver, lambda self: False
    )

    assert result is False
    assert "session.new" in driver.calls
    assert driver.calls.index("session.end") > driver.calls.index("session.new")
    assert driver.stopped == 1
    assert browser._driver is None
    assert browser._owns_session is False
    assert browser._session_id is None
    # 浏览器本身仍是自己的，归属结论保留，下一次重连成功后 quit() 仍可优雅关闭
    assert browser._session_ownership_verified is True


def test_initial_context_wait_outlasts_the_old_three_second_cap(monkeypatch):
    """自启动实例只要进程还活着就一直等窗口，不再 3 秒就拆连接重建。"""
    browser = _bare_browser("127.0.0.1:9395")
    browser._process = _FakeProcess()
    browser._context_ids = []
    browser._context_ids_lock = threading.Lock()
    monkeypatch.setattr(Firefox, "_launched_browser_alive", lambda self: True)

    started = browser_module.time.time()
    ready_at = started + 4.0  # 超过早期的 3 秒上限

    def refresh(self):
        if browser_module.time.time() >= ready_at:
            self._context_ids = ["ctx-1"]

    monkeypatch.setattr(Firefox, "_refresh_tabs", refresh)

    assert browser._wait_for_initial_context() is True


def test_initial_context_wait_stays_short_for_attached_browsers(monkeypatch):
    """显式 attach 的浏览器没有窗口是真问题，3 秒内就要报出来。"""
    browser = _bare_browser(
        "127.0.0.1:9393", options=FirefoxOptions().existing_only(True)
    )
    browser._context_ids = []
    browser._context_ids_lock = threading.Lock()
    monkeypatch.setattr(Firefox, "_refresh_tabs", lambda self: None)

    started = browser_module.time.time()
    assert browser._wait_for_initial_context() is False
    assert browser_module.time.time() - started < 4.0


def test_initial_context_wait_stops_when_own_process_dies(monkeypatch):
    """自己的进程死了就别等满 15 秒。"""

    class _Dead:
        pid = 1
        returncode = 1

        def poll(self):
            return 1

    browser = _bare_browser("127.0.0.1:9394")
    browser._process = _Dead()
    browser._context_ids = []
    browser._context_ids_lock = threading.Lock()
    monkeypatch.setattr(Firefox, "_refresh_tabs", lambda self: None)
    monkeypatch.setattr(Firefox, "_launched_browser_alive", lambda self: False)

    started = browser_module.time.time()
    assert browser._wait_for_initial_context() is False
    assert browser_module.time.time() - started < 2.0


def test_discard_connection_without_a_session_does_not_send_end(monkeypatch, tmp_path):
    """会话根本没建成时不该发 session.end，那会结束别人的会话。"""
    browser = _bare_browser("127.0.0.1:9391")
    driver = _FakeDriver()
    browser._driver = driver
    browser._owns_session = False

    browser._discard_connection()

    assert "session.end" not in driver.calls
    assert driver.stopped == 1
    assert browser._driver is None


# --------------------------------------------------------------------------
# (l) quit() 不能对归属未确认的浏览器发 browser.close
# --------------------------------------------------------------------------


def test_quit_refuses_browser_close_on_unverified_browser():
    """quit() 是 browser.close 最常走的入口，必须同样受归属校验约束。"""
    browser = _bare_browser("127.0.0.1:9371")
    browser._quit_lock = threading.Lock()
    browser._proxy_auth_intercept_id = None
    browser._proxy_auth_subscription_id = None
    driver = _FakeDriver()
    browser._driver = driver

    assert browser._can_close_remote_browser() is False
    browser.quit()

    assert "browser.close" not in driver.calls, (
        "对归属未确认的浏览器发了 browser.close"
    )


def test_quit_still_closes_a_verified_own_browser():
    """归属已确认时，quit() 必须照旧关闭浏览器。"""
    browser = _bare_browser("127.0.0.1:9372")
    browser._quit_lock = threading.Lock()
    browser._proxy_auth_intercept_id = None
    browser._proxy_auth_subscription_id = None
    browser._session_ownership_verified = True
    driver = _FakeDriver()
    browser._driver = driver

    browser.quit()

    assert "browser.close" in driver.calls


def test_ownership_verification_is_reset_on_port_rotation():
    """换端口后旧的归属结论必须失效，否则会重新打开 browser.close 闸门。"""
    browser = _bare_browser("127.0.0.1:9373")
    browser._session_ownership_verified = True

    browser._set_launch_port(9374)

    assert browser._session_ownership_verified is False
    assert browser._can_close_remote_browser() is False
