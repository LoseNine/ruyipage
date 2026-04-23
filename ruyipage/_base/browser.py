# -*- coding: utf-8 -*-
"""Firefox 浏览器管理器

管理浏览器进程的启动/连接、会话创建、标签页生命周期。
单例模式，同一地址只创建一个实例。
"""

import os
import sys
import time
import atexit
import socket
import logging
import tempfile
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from .driver import BrowserBiDiDriver, ContextDriver
from .._adapter.remote_agent import get_bidi_ws_url
from .._functions.sleep import sleep as _sleep
from .._configs.firefox_options import FirefoxOptions
from .._bidi import network as bidi_network
from .._bidi import session as bidi_session
from .._bidi import browsing_context as bidi_context
from ..errors import BrowserConnectError, BrowserLaunchError

logger = logging.getLogger("ruyipage")


DEFAULT_FIREFOX_PROCESS_NAME_PATTERNS = (
    "firefox.exe",
    "flowerbrowser.exe",
    "adsbrowser.exe",
)

DEFAULT_FIREFOX_COMMANDLINE_PATTERNS = (
    "--remote-debugging-port",
    "--marionette",
    "-contentproc",
    "-isforbrowser",
    "adspower",
    "flower",
    "firefox",
)


def _probe_bidi_address(address, timeout=1.0, keep_driver=False):
    """探测 address 是否为可接管的 Firefox BiDi 实例。"""
    host, port = address.rsplit(":", 1)
    try:
        port = int(port)
    except Exception:
        return None

    def _occupied_result(status_message="", error_message="", ws_url=""):
        return {
            "address": address,
            "host": host,
            "port": port,
            "ready": False,
            "message": status_message,
            "status_message": status_message,
            "error_message": error_message,
            "probe_state": "occupied",
            "ws_url": ws_url,
            "driver": None,
            "session_id": "",
            "window_count": 0,
            "tab_count": 0,
            "client_windows": [],
            "contexts": [],
            "session_owned": False,
        }

    def _release_probe_session(driver_obj, owns_session):
        if not driver_obj or not owns_session:
            return
        try:
            bidi_session.end(driver_obj)
        except Exception:
            pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.close()
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        return None

    driver = None
    session_owned = False
    try:
        ws_url = get_bidi_ws_url(host, port, timeout=max(1, timeout))
        driver = BrowserBiDiDriver(address)
        driver.start(ws_url)
        status = bidi_session.status(driver)
        ready = status.get("ready", False)
        message = status.get("message", "")

        # 对可接管实例的定义要更严格：
        # 仅当远端允许我们创建新 session 时，才认为这个端口可以 attach。
        # 像 AdsPower / FlowerBrowser 这类场景，session.status 会返回
        # "Session already started"，但不会把已有 session 交给新连接复用，
        # 后续任何 browsingContext 命令都会报 invalid session id。
        if not ready:
            return _occupied_result(status_message=message, ws_url=ws_url)

        try:
            result = bidi_session.new(driver, {})
            session_owned = True
        except Exception as e:
            err_text = str(e)
            err_lower = err_text.lower()
            if "maximum number of active sessions" in err_lower or (
                "session not created" in err_lower and "maximum" in err_lower
            ):
                return _occupied_result(
                    status_message=message,
                    error_message=err_text,
                    ws_url=ws_url,
                )
            raise
        session_id = result.get("sessionId", "")
        driver.session_id = session_id
        windows = []
        contexts = []
        try:
            windows = driver.run("browser.getClientWindows").get("clientWindows", [])
        except Exception:
            windows = []
        try:
            contexts = bidi_context.get_tree(driver, max_depth=0).get("contexts", [])
        except Exception:
            contexts = []

        return {
            "address": address,
            "host": host,
            "port": port,
            "ready": ready,
            "message": message,
            "ws_url": ws_url,
            "driver": driver if keep_driver else None,
            "session_id": session_id,
            "window_count": len(windows),
            "tab_count": len(contexts),
            "probe_state": "attachable",
            "status_message": message,
            "error_message": "",
            "session_owned": session_owned,
            "client_windows": windows,
            "contexts": [
                {
                    "context": c.get("context", ""),
                    "url": c.get("url", ""),
                    "user_context": c.get("userContext", "default"),
                    "original_opener": c.get("originalOpener", None),
                }
                for c in contexts
            ],
        }
    except Exception:
        return None
    finally:
        if driver and not keep_driver:
            _release_probe_session(driver, session_owned)
            try:
                driver.stop()
            except Exception:
                with BrowserBiDiDriver._lock:
                    BrowserBiDiDriver._BROWSERS.pop(address, None)


def create_browser_from_probe_info(info):
    """根据探测结果复用已建立的 BiDi 连接，直接构造 Firefox 对象。"""
    address = info["address"]
    driver = info.get("driver")
    if driver is None:
        raise BrowserConnectError("探测结果中缺少可复用的 BiDi 连接")

    opts = FirefoxOptions().set_address(address).existing_only(True)
    browser = Firefox.__new__(Firefox, address)
    browser._initialized = False
    browser._options = opts
    browser._address = address
    browser._driver = driver
    browser._process = None
    browser._session_id = info.get("session_id", "")
    browser._owns_session = bool(info.get("session_owned"))
    browser._contexts = {}
    browser._context_ids = [
        c.get("context", "") for c in (info.get("contexts") or []) if c.get("context")
    ]
    browser._context_ids_lock = threading.Lock()
    browser._context_nav_locks = {}
    browser._context_nav_locks_lock = threading.Lock()
    browser._init_lock = threading.Lock()
    browser._auto_profile = None
    browser._quit_lock = threading.Lock()
    browser._proxy_auth_intercept_id = None
    browser._proxy_auth_subscription_id = None
    browser._xpath_picker_last_reinject = {}
    browser._atexit_registered = False
    browser._register_exit_cleanup()
    browser._initialized = True
    return browser


def find_existing_browsers(
    host="127.0.0.1", start_port=9222, end_port=9322, timeout=0.5, max_workers=32
):
    """扫描本机可接管的 Firefox BiDi 浏览器实例。

    使用线程池并发探测端口，显著缩短大范围随机端口扫描时间。
    返回结果保持按端口升序，避免影响既有调用方行为。
    """
    ports = list(range(int(start_port), int(end_port) + 1))
    if not ports:
        return []

    workers = max(1, min(int(max_workers), len(ports)))
    result = []

    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="ruyipage-scan"
    ) as executor:
        future_map = {
            executor.submit(
                _probe_bidi_address, "{}:{}".format(host, port), timeout=timeout
            ): port
            for port in ports
        }

        for future in as_completed(future_map):
            try:
                info = future.result()
            except Exception:
                info = None
            if info:
                if info.get("probe_state", "attachable") == "attachable":
                    result.append(info)

    result.sort(key=lambda item: int(item.get("port", 0)))
    return result


def find_existing_browsers_by_process(
    host="127.0.0.1",
    timeout=0.5,
    max_workers=32,
    keep_driver=False,
    process_name_patterns=None,
    commandline_patterns=None,
):
    """按进程特征发现可接管的 Firefox BiDi 浏览器实例。

    先从系统进程筛出目标浏览器（Firefox / FlowerBrowser 等），
    再读取这些进程监听的本地端口，最后仅对候选端口做 BiDi 探测。
    """
    if process_name_patterns is None:
        process_name_patterns = DEFAULT_FIREFOX_PROCESS_NAME_PATTERNS
    if commandline_patterns is None:
        commandline_patterns = DEFAULT_FIREFOX_COMMANDLINE_PATTERNS

    pids = set()
    ports = set()

    if sys.platform == "win32":
        ps_proc = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId, Name, CommandLine | "
            "ConvertTo-Json -Compress"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_proc],
                stderr=subprocess.DEVNULL,
            )
            import json

            data = json.loads(out.decode(errors="ignore") or "[]")
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = str(item.get("Name") or "").lower()
                cmd = str(item.get("CommandLine") or "").lower()
                pid = int(item.get("ProcessId") or 0)
                if not pid:
                    continue
                name_ok = any(p in name for p in process_name_patterns)
                cmd_ok = any(p in cmd for p in commandline_patterns)
                if name_ok or cmd_ok:
                    pids.add(pid)
        except Exception:
            pass

        if pids:
            ps_net = (
                "Get-NetTCPConnection -State Listen | "
                "Select-Object LocalAddress, LocalPort, OwningProcess | "
                "ConvertTo-Json -Compress"
            )
            try:
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", ps_net],
                    stderr=subprocess.DEVNULL,
                )
                import json

                data = json.loads(out.decode(errors="ignore") or "[]")
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    pid = int(item.get("OwningProcess") or 0)
                    addr = str(item.get("LocalAddress") or "")
                    port = int(item.get("LocalPort") or 0)
                    if (
                        pid in pids
                        and port > 0
                        and addr in ("127.0.0.1", "::1", "0.0.0.0")
                    ):
                        ports.add(port)
            except Exception:
                pass

    if not ports:
        return []

    workers = max(1, min(int(max_workers), len(ports)))
    result = []
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="ruyipage-proc-scan"
    ) as executor:
        future_map = {
            executor.submit(
                _probe_bidi_address,
                "{}:{}".format(host, port),
                timeout,
                keep_driver,
            ): port
            for port in sorted(ports)
        }
        for future in as_completed(future_map):
            try:
                info = future.result()
            except Exception:
                info = None
            if info:
                info["scanned_ports"] = sorted(ports)
                if info.get("probe_state", "attachable") == "attachable":
                    result.append(info)

    result.sort(key=lambda item: int(item.get("port", 0)))
    return result


def find_candidate_ports_by_process(
    process_name_patterns=None,
    commandline_patterns=None,
):
    """按进程特征返回候选监听端口（仅发现，不做 BiDi 探测）。"""
    if process_name_patterns is None:
        process_name_patterns = DEFAULT_FIREFOX_PROCESS_NAME_PATTERNS
    if commandline_patterns is None:
        commandline_patterns = DEFAULT_FIREFOX_COMMANDLINE_PATTERNS

    pids = set()
    ports = set()

    if sys.platform == "win32":
        ps_proc = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId, Name, CommandLine | "
            "ConvertTo-Json -Compress"
        )
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_proc],
                stderr=subprocess.DEVNULL,
            )
            import json

            data = json.loads(out.decode(errors="ignore") or "[]")
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = str(item.get("Name") or "").lower()
                cmd = str(item.get("CommandLine") or "").lower()
                pid = int(item.get("ProcessId") or 0)
                if not pid:
                    continue
                name_ok = any(p in name for p in process_name_patterns)
                cmd_ok = any(p in cmd for p in commandline_patterns)
                if name_ok or cmd_ok:
                    pids.add(pid)
        except Exception:
            pass

        if pids:
            ps_net = (
                "Get-NetTCPConnection -State Listen | "
                "Select-Object LocalAddress, LocalPort, OwningProcess | "
                "ConvertTo-Json -Compress"
            )
            try:
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", ps_net],
                    stderr=subprocess.DEVNULL,
                )
                import json

                data = json.loads(out.decode(errors="ignore") or "[]")
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    pid = int(item.get("OwningProcess") or 0)
                    addr = str(item.get("LocalAddress") or "")
                    port = int(item.get("LocalPort") or 0)
                    if (
                        pid in pids
                        and port > 0
                        and addr in ("127.0.0.1", "::1", "0.0.0.0")
                    ):
                        ports.add(port)
            except Exception:
                pass

    return sorted(ports)


class Firefox(object):
    """Firefox 浏览器管理器

    用法::

        # 连接已启动的浏览器
        browser = Firefox('127.0.0.1:9222')

        # 通过选项启动
        opts = FirefoxOptions()
        browser = Firefox(opts)

        # 获取标签页
        tab = browser.latest_tab
    """

    _BROWSERS = {}  # {address: Firefox}
    _lock = threading.Lock()
    _launch_lock = threading.Lock()
    _RESERVED_PORTS = set()

    @classmethod
    def _cache_key_for(cls, addr_or_opts=None):
        """仅对显式 attach 场景启用地址级单例缓存。"""
        if isinstance(addr_or_opts, FirefoxOptions):
            if not addr_or_opts.is_existing_only:
                return None
            return addr_or_opts.address

        if isinstance(addr_or_opts, str):
            return addr_or_opts

        return None

    def __new__(cls, addr_or_opts=None):
        cache_key = cls._cache_key_for(addr_or_opts)

        with cls._lock:
            if cache_key is not None and cache_key in cls._BROWSERS:
                return cls._BROWSERS[cache_key]
            instance = super(Firefox, cls).__new__(cls)
            instance._initialized = False
            instance._init_lock = threading.Lock()
            if cache_key is not None:
                cls._BROWSERS[cache_key] = instance
            return instance

    def __init__(self, addr_or_opts=None):
        with self._init_lock:
            if self._initialized:
                return

            # 解析参数
            if isinstance(addr_or_opts, FirefoxOptions):
                self._options = addr_or_opts
            elif isinstance(addr_or_opts, str):
                self._options = FirefoxOptions()
                self._options.set_address(addr_or_opts)
            elif addr_or_opts is None:
                self._options = FirefoxOptions()
            else:
                self._options = FirefoxOptions()
                self._options.set_address(str(addr_or_opts))

            self._address = self._options.address
            self._driver = None  # type: BrowserBiDiDriver
            self._process = None  # type: subprocess.Popen
            self._session_id = None
            self._owns_session = False
            self._contexts = {}  # {context_id: FirefoxTab 弱引用信息}
            self._context_ids = []  # 有序的 context ID 列表
            self._context_ids_lock = threading.Lock()
            self._context_nav_locks = {}
            self._context_nav_locks_lock = threading.Lock()
            self._reserved_port = None
            self._auto_profile = None  # 自动创建的临时 profile
            self._quit_lock = threading.Lock()
            self._proxy_auth_intercept_id = None
            self._proxy_auth_subscription_id = None
            self._xpath_picker_last_reinject = {}
            self._atexit_registered = False

            try:
                self._connect_or_launch()
                self._register_exit_cleanup()
                with self._lock:
                    self._BROWSERS[self._address] = self
                self._initialized = True
            except Exception:
                # 初始化失败时移除单例，避免后续复用半初始化对象
                with self._lock:
                    if self._BROWSERS.get(self._address) is self:
                        self._BROWSERS.pop(self._address, None)
                self._initialized = False
                raise

    def get_context_nav_lock(self, context_id):
        """返回某个 browsing context 专属的导航锁。"""
        with self._context_nav_locks_lock:
            lock = self._context_nav_locks.get(context_id)
            if lock is None:
                lock = threading.RLock()
                self._context_nav_locks[context_id] = lock
            return lock

    def _reserve_port(self, port):
        """在进程内保留一个即将用于启动的调试端口。"""
        if port is None:
            return
        self._RESERVED_PORTS.add(port)
        self._reserved_port = port

    def _release_reserved_port(self, port=None):
        """释放进程内保留的调试端口。"""
        port = self._reserved_port if port is None else port
        if port is None:
            return
        self._RESERVED_PORTS.discard(port)
        if self._reserved_port == port:
            self._reserved_port = None

    def _set_launch_port(self, port):
        """更新 options/address，并同步进程内端口保留。"""
        if self._reserved_port is not None and self._reserved_port != port:
            self._release_reserved_port(self._reserved_port)
        self._options.set_port(port)
        self._address = self._options.address
        self._reserve_port(port)

    @property
    def address(self):
        """连接地址 host:port"""
        return self._address

    @property
    def driver(self):
        """BrowserBiDiDriver 实例"""
        return self._driver

    @property
    def session_id(self):
        """会话 ID"""
        return self._session_id

    @property
    def options(self):
        """FirefoxOptions"""
        return self._options

    @property
    def process(self):
        """浏览器子进程"""
        return self._process

    @property
    def tabs_count(self):
        """标签页数量"""
        self._refresh_tabs()
        return len(self._context_ids)

    @property
    def tab_ids(self):
        """所有标签页的 context ID 列表"""
        self._refresh_tabs()
        return self._context_ids[:]

    @property
    def latest_tab(self):
        """最新的标签页"""
        self._refresh_tabs()
        if self._context_ids:
            return self._get_or_create_tab(self._context_ids[-1])
        return None

    @property
    def window_handles(self):
        """获取客户端窗口信息

        通过 browser.getClientWindows 获取所有浏览器窗口的信息。

        Returns:
            客户端窗口列表，每个窗口包含 clientWindow, state, width, height, x, y 等字段
        """
        try:
            result = self._driver.run("browser.getClientWindows")
            return result.get("clientWindows", [])
        except Exception as e:
            logger.warning("获取窗口信息失败: %s", e)
            return []

    def get_tab(self, id_or_num=None, title=None, url=None):
        """获取标签页

        Args:
            id_or_num: context ID 字符串 或 序号（从1开始，负数从后数）
            title: 按标题匹配（部分匹配）
            url: 按 URL 匹配（部分匹配）

        Returns:
            FirefoxTab
        """
        self._refresh_tabs()

        if isinstance(id_or_num, int):
            if id_or_num > 0:
                idx = id_or_num - 1
            else:
                idx = id_or_num
            if 0 <= idx < len(self._context_ids) or idx < 0:
                return self._get_or_create_tab(self._context_ids[idx])
            return None

        if isinstance(id_or_num, str):
            if id_or_num in self._context_ids:
                return self._get_or_create_tab(id_or_num)
            return None

        if title or url:
            for ctx_id in self._context_ids:
                tab = self._get_or_create_tab(ctx_id)
                if title and title in tab.title:
                    return tab
                if url and url in tab.url:
                    return tab
            return None

        # 默认返回第一个
        if self._context_ids:
            return self._get_or_create_tab(self._context_ids[0])
        return None

    def get_tabs(self, title=None, url=None):
        """获取匹配条件的所有标签页

        Args:
            title: 按标题匹配（部分匹配）
            url: 按 URL 匹配（部分匹配）

        Returns:
            列表
        """
        self._refresh_tabs()
        result = []
        for ctx_id in self._context_ids:
            tab = self._get_or_create_tab(ctx_id)
            if title and title not in tab.title:
                continue
            if url and url not in tab.url:
                continue
            result.append(tab)
        return result

    def new_tab(self, url=None, background=False):
        """新建标签页

        Args:
            url: 初始 URL
            background: 是否在后台创建

        Returns:
            FirefoxTab
        """
        ref_ctx = self._context_ids[0] if self._context_ids else None
        params = {"type": "tab", "background": background}
        if ref_ctx:
            params["referenceContext"] = ref_ctx

        result = self._driver.run("browsingContext.create", params)
        ctx_id = result.get("context", "")

        if ctx_id and ctx_id not in self._context_ids:
            self._context_ids.append(ctx_id)

        tab = self._get_or_create_tab(ctx_id)

        if url:
            tab.get(url)

        return tab

    def activate_tab(self, id_ind_tab):
        """激活标签页

        Args:
            id_ind_tab: context ID、序号、或 FirefoxTab 对象
        """
        if isinstance(id_ind_tab, str):
            ctx_id = id_ind_tab
        elif isinstance(id_ind_tab, int):
            ctx_id = self._context_ids[id_ind_tab - 1 if id_ind_tab > 0 else id_ind_tab]
        else:
            ctx_id = getattr(id_ind_tab, "_context_id", str(id_ind_tab))

        bidi_context.activate(self._driver, ctx_id)

    def close_tabs(self, tabs_or_ids=None, others=False):
        """关闭标签页

        Args:
            tabs_or_ids: 要关闭的标签页列表（context ID 或 FirefoxTab）
            others: True 则关闭其他标签页（保留 tabs_or_ids 指定的）
        """
        if tabs_or_ids is None:
            return

        if not isinstance(tabs_or_ids, (list, tuple)):
            tabs_or_ids = [tabs_or_ids]

        target_ids = set()
        for t in tabs_or_ids:
            if isinstance(t, str):
                target_ids.add(t)
            else:
                target_ids.add(getattr(t, "_context_id", str(t)))

        if others:
            to_close = [cid for cid in self._context_ids if cid not in target_ids]
        else:
            to_close = [cid for cid in self._context_ids if cid in target_ids]

        for cid in to_close:
            try:
                bidi_context.close(self._driver, cid)
            except Exception:
                pass

        self._refresh_tabs()

    def cookies(self, all_info=False):
        """获取所有标签页的 Cookie

        Args:
            all_info: True 返回完整 Cookie 信息

        Returns:
            Cookie 列表
        """
        from .._bidi import storage as bidi_storage

        result = bidi_storage.get_cookies(self._driver)
        cookies = result.get("cookies", [])
        if not all_info:
            return [
                {
                    "name": c.get("name", ""),
                    "value": c.get("value", {}).get("value", ""),
                }
                for c in cookies
            ]
        return cookies

    def quit(self, timeout=5, force=False):
        """关闭浏览器

        Args:
            timeout: 等待关闭超时（秒）
            force: 是否强制结束进程
        """
        with self._quit_lock:
            if self._driver:
                self._driver.mark_closing()
            try:
                self._driver.run("browser.close", timeout=3)
            except Exception:
                pass

            if self._driver:
                self._teardown_proxy_auth()
                if self._owns_session:
                    try:
                        bidi_session.end(self._driver)
                    except Exception:
                        pass
                    self._owns_session = False
                self._driver.stop()
                self._driver = None

            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=timeout)
                except Exception:
                    if force:
                        try:
                            self._process.kill()
                        except Exception:
                            pass
                self._process = None

            # 清理临时 profile
            if self._auto_profile:
                import shutil

                try:
                    shutil.rmtree(self._auto_profile, ignore_errors=True)
                except Exception:
                    pass
                self._auto_profile = None

            # 清理单例
            with self._lock:
                self._BROWSERS.pop(self._address, None)
            self._initialized = False

    def _detach_on_exit(self):
        """在解释器退出时仅断开当前连接，不主动关闭浏览器进程。

        该路径适用于：
            - ``close_on_exit(False)``
            - ``existing_only(True)`` 接管的外部浏览器
        """
        if not self._driver:
            return
        self._driver.mark_closing()
        try:
            self._teardown_proxy_auth()
        except Exception:
            pass
        if self._owns_session:
            try:
                bidi_session.end(self._driver)
            except Exception:
                pass
            self._owns_session = False
        try:
            self._driver.stop()
        except Exception:
            pass
        self._driver = None

    def _should_close_browser_on_exit(self):
        """判断解释器退出时是否应执行完整浏览器关闭。

        设计原则：
            - 默认随 Python 程序退出而关闭浏览器，符合脚本型使用场景。
            - 对 ``existing_only(True)`` 接管的外部浏览器，始终只断开连接，
              避免误关用户手动打开的浏览器进程。
        """
        if not self._driver:
            return False
        if not getattr(self._options, "close_on_exit_enabled", True):
            return False
        if getattr(self._options, "is_existing_only", False):
            return False
        return True

    def _register_exit_cleanup(self):
        if self._atexit_registered:
            return
        atexit.register(self._cleanup_on_exit)
        self._atexit_registered = True

    def _cleanup_on_exit(self):
        if not self._driver:
            return

        if self._should_close_browser_on_exit():
            try:
                self.quit(timeout=3, force=True)
            except Exception:
                self._detach_on_exit()
            return

        self._detach_on_exit()

    def reconnect(self):
        """重新连接"""
        if self._driver:
            self._teardown_proxy_auth()
            self._driver.stop()
        self._driver = BrowserBiDiDriver(self._address)
        host, port_str = self._address.rsplit(":", 1)
        ws_url = get_bidi_ws_url(host, int(port_str), timeout=5)
        self._driver.start(ws_url)
        self._create_session()
        if not self._session_id:
            raise BrowserConnectError(
                "无法在 {} 上创建可用的 WebDriver 会话".format(self._address)
            )
        self._subscribe_events()
        self._setup_proxy_auth()
        self._refresh_tabs()

    def _connect_or_launch(self):
        """连接已有浏览器或启动新的"""
        # 尝试自动端口
        if self._options.auto_port:
            with self._launch_lock:
                port = self._find_free_port()
                self._set_launch_port(port)

        if self._options.is_existing_only:
            # attach()/FirefoxPage('host:port') 这类显式接管场景才复用已有浏览器。
            try:
                if self._try_connect():
                    return
            except BrowserConnectError as e:
                if "__stuck_session__" in str(e):
                    # 仅在现有会话确实不可接管时，才回退到重启清理。
                    logger.warning("检测到不可接管的 Firefox 会话，尝试重启浏览器...")
                    self._restart_firefox_for_stuck_session()
                    # 重启后重新连接
                    for i in range(self._options.retry_times + 1):
                        try:
                            if self._try_connect():
                                return
                        except BrowserConnectError:
                            pass
                        time.sleep(self._options.retry_interval)

            raise BrowserConnectError(
                "无法连接到 {}，请先启动 Firefox：\n"
                "  firefox.exe --remote-debugging-port={}".format(
                    self._address, self._options.port
                )
            )

        # launch()/FirefoxPage(opts) 场景应始终按当前 options 启动新实例，
        # 避免复用已存在的普通窗口，导致 private/user_dir/headless 等参数失效。
        # 这里用类级启动锁把“端口探测 -> 真正启动进程”串成原子区间，
        # 避免并发 launch 时多个实例同时抢占同一端口。
        with self._launch_lock:
            self._ensure_launch_port_available()
            self._launch_browser()

        # 等待连接
        for i in range(self._options.retry_times + 1):
            try:
                if self._try_connect():
                    return
            except BrowserConnectError:
                pass
            time.sleep(self._options.retry_interval)

        # 某些环境下首次启动后 remote debugging 端口就绪较慢，
        # 或出现短暂的僵尸会话，导致首轮重试全部失败。
        # 这里做一次“重启并重试”兜底，优先提升稳定性（不影响 existing_only 模式）。
        if not self._options.is_existing_only:
            logger.warning("首次启动连接失败，尝试重启 Firefox 后再重试一次...")
            try:
                if self._process and self._process.poll() is None:
                    self._process.kill()
                    self._process.wait(timeout=5)
            except Exception:
                pass
            self._process = None

            with self._launch_lock:
                self._ensure_launch_port_available()
                self._launch_browser()
            for i in range(self._options.retry_times + 1):
                try:
                    if self._try_connect():
                        return
                except BrowserConnectError:
                    pass
                time.sleep(self._options.retry_interval)

        raise BrowserConnectError(
            "启动后无法连接到 {}，请检查 Firefox 是否正常启动".format(self._address)
        )

    def _restart_firefox_for_stuck_session(self):
        """重启 Firefox 以清理孤儿 BiDi session"""
        host, port_str = self._address.rsplit(":", 1)

        # 断开当前连接
        if self._driver:
            try:
                self._driver.stop()
            except Exception:
                pass
            self._driver = None

        # 尝试优雅关闭 Firefox（仅杀自己启动的进程）
        try:
            if self._process and self._process.poll() is None:
                self._process.kill()
                self._process.wait(timeout=5)
            elif sys.platform == "win32":
                os.system("taskkill /f /im firefox.exe >nul 2>&1")
            else:
                os.system("pkill -f firefox")
        except Exception:
            pass

        time.sleep(2)

        # 如果不是 existing_only 模式，重新启动 Firefox
        if not self._options.is_existing_only:
            self._launch_browser()
            time.sleep(2)
        else:
            # existing_only 模式下，等待用户手动重启
            # 等待端口重新可用
            for _ in range(10):
                if self._is_port_open():
                    return
                time.sleep(1)

    def _is_port_open(self):
        """检查端口是否可达"""
        host, port_str = self._address.rsplit(":", 1)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((host, int(port_str)))
            sock.close()
            return True
        except Exception:
            return False

    def _ensure_launch_port_available(self):
        """启动前确保目标端口未被其他进程占用。"""
        if self._options.auto_port:
            return

        if not self._is_port_open():
            return

        old_port = self._options.port
        new_port = self._find_free_port(start=old_port + 1)
        self._set_launch_port(new_port)

        message = "检测到端口 {} 已被占用，ruyiPage 已自动切换到可用端口 {}".format(
            old_port, new_port
        )
        logger.warning(message)
        print(message)

    def _try_connect(self):
        """尝试连接到已有浏览器

        连接失败时正确清理 BrowserBiDiDriver 单例，避免残留无效实例。

        Returns:
            True 连接成功，False 失败
        """
        # 先检查端口是否可达
        host, port = self._address.rsplit(":", 1)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((host, int(port)))
            sock.close()
        except Exception:
            return False

        try:
            host, port = self._address.rsplit(":", 1)
            ws_url = get_bidi_ws_url(host, int(port), timeout=5)
            self._driver = BrowserBiDiDriver(self._address)
            self._driver.start(ws_url)
            self._create_session()
            if not self._session_id:
                return False
            self._subscribe_events()
            self._setup_proxy_auth()
            self._setup_download_behavior()
            self._refresh_tabs()
            self._release_reserved_port()
            logger.info("已连接到 Firefox: %s", self._address)
            return True
        except BrowserConnectError:
            # stuck session 错误需要向上传播
            if self._driver:
                try:
                    self._driver.stop()
                except Exception:
                    with BrowserBiDiDriver._lock:
                        BrowserBiDiDriver._BROWSERS.pop(self._address, None)
                self._driver = None
            self._release_reserved_port()
            raise
        except Exception as e:
            logger.debug("连接失败: %s", e)
            if self._driver:
                try:
                    self._teardown_proxy_auth()
                    # 使用 stop() 而非 _stop()，确保清理单例注册
                    # 这样下次 _try_connect 会创建全新的 BrowserBiDiDriver
                    self._driver.stop()
                except Exception:
                    # 即使 stop() 异常，也要手动清理单例
                    with BrowserBiDiDriver._lock:
                        BrowserBiDiDriver._BROWSERS.pop(self._address, None)
                self._driver = None
            self._release_reserved_port()
            return False

    def _setup_proxy_auth(self):
        """启用浏览器级代理认证自动应答。"""
        self._teardown_proxy_auth()

        if not self._driver:
            return

        if not self._session_id:
            return

        if not self._options.proxy:
            return

        credentials = self._options._get_proxy_auth_credentials()
        if not credentials:
            return

        result = bidi_network.add_intercept(self._driver, phases=["authRequired"])
        self._proxy_auth_intercept_id = result.get("intercept")

        sub = bidi_session.subscribe(self._driver, ["network.authRequired"])
        self._proxy_auth_subscription_id = sub.get("subscription")
        self._driver.set_callback("network.authRequired", self._on_proxy_auth_required)

    def _teardown_proxy_auth(self):
        """清理浏览器级代理认证拦截。"""
        if self._driver:
            try:
                self._driver.remove_callback("network.authRequired")
            except Exception:
                pass

            if self._proxy_auth_subscription_id:
                try:
                    bidi_session.unsubscribe(
                        self._driver, subscription=self._proxy_auth_subscription_id
                    )
                except Exception:
                    pass

            if self._proxy_auth_intercept_id:
                try:
                    bidi_network.remove_intercept(
                        self._driver, self._proxy_auth_intercept_id
                    )
                except Exception:
                    pass

        self._proxy_auth_subscription_id = None
        self._proxy_auth_intercept_id = None

    def _on_proxy_auth_required(self, params):
        """在代理认证挑战出现时自动提供用户名密码。"""
        request = params.get("request") or {}
        request_id = request.get("request") if isinstance(request, dict) else request
        if not request_id:
            return

        credentials = self._options._get_proxy_auth_credentials() or {}
        if not credentials:
            bidi_network.continue_with_auth(self._driver, request_id, action="default")
            return

        challenge = params.get("authChallenge") or {}
        source = str(challenge.get("source") or params.get("source") or "").lower()
        realm = str(params.get("realm") or challenge.get("realm") or "")
        is_proxy = source == "proxy" or "proxy" in realm.lower()

        if is_proxy:
            bidi_network.continue_with_auth(
                self._driver,
                request_id,
                action="provideCredentials",
                credentials={
                    "type": "password",
                    "username": credentials.get("username", ""),
                    "password": credentials.get("password", ""),
                },
            )
            return

        bidi_network.continue_with_auth(self._driver, request_id, action="default")

    def _launch_browser(self):
        """启动 Firefox 进程

        支持 --fpfile 命令行参数来指定指纹配置文件。
        """
        opts = self._options

        # 如果没有指定 profile，创建临时的
        if not opts.profile_path:
            self._auto_profile = tempfile.mkdtemp(prefix="ruyipage_")
            opts.set_profile(self._auto_profile)

        # 如果设置了 fpfile，将文件路径写入环境变量或通过命令行传递
        # fpfile 会通过 build_command() 自动包含在命令行中

        # 写入首选项
        opts.write_prefs_to_profile()

        cmd = opts.build_command()
        logger.info("启动 Firefox: %s", " ".join(cmd))

        try:
            # 在 Windows 上使用 CREATE_NO_WINDOW 避免弹出控制台
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs
            )
        except FileNotFoundError:
            raise BrowserLaunchError(
                "找不到 Firefox: {}\n"
                "请安装 Firefox 或通过 FirefoxOptions.set_browser_path() 指定路径".format(
                    opts.browser_path
                )
            )
        except Exception as e:
            raise BrowserLaunchError("启动 Firefox 失败: {}".format(e))

        # 等待端口就绪
        time.sleep(1)

    def _create_session(self):
        """创建 BiDi 会话

        处理各种 Firefox BiDi session 状态：
        1. 无活跃 session → session.new 成功
        2. 当前连接已有 session → session.end + session.new
        3. 另一个连接已持有 session → 优先接管当前会话，失败时再回退重启
        """
        # 先检查状态
        try:
            status = bidi_session.status(self._driver)
            ready = status.get("ready", True)
            msg = status.get("message", "")
        except Exception:
            ready = True
            msg = ""

        if ready:
            # 可以直接创建新 session
            try:
                result = bidi_session.new(
                    self._driver,
                    {},
                    user_prompt_handler=self._options.user_prompt_handler,
                )
                self._session_id = result.get("sessionId", "")
                self._driver.session_id = self._session_id
                self._owns_session = True
                logger.info("BiDi 会话已创建: %s", self._session_id)
                return
            except Exception as e:
                logger.debug("创建会话失败: %s", e)
                raise
        else:
            # session 不就绪,可能是已有 session（孤儿或当前连接的）
            logger.debug("会话状态: ready=%s, message=%s", ready, msg)

            # 尝试 session.end + session.new
            try:
                bidi_session.end(self._driver)
                logger.debug("已结果旧会话")
            except Exception:
                pass

            try:
                result = bidi_session.new(
                    self._driver,
                    {},
                    user_prompt_handler=self._options.user_prompt_handler,
                )
                self._session_id = result.get("sessionId", "")
                self._driver.session_id = self._session_id
                self._owns_session = True
                logger.info("BiDi 会话已重新创建: %s", self._session_id)
                return
            except Exception as e:
                err_str = str(e).lower()
                if "maximum" in err_str or "session not created" in err_str:
                    # Firefox 已存在活跃 BiDi 会话时，优先直接接管当前会话，
                    # 避免把可复用的浏览器误判成必须重启的孤儿会话。
                    self._session_id = ""
                    self._driver.session_id = ""
                    self._owns_session = False
                    logger.info("检测到已有 Firefox BiDi 会话，直接接管当前浏览器")
                    return
                raise

    def _subscribe_events(self):
        """订阅关键事件

        包括导航相关的 navigationStarted 和 navigationFailed 事件。
        """
        if not self._driver or not self._session_id:
            return

        try:
            bidi_session.subscribe(
                self._driver,
                [
                    "browsingContext.contextCreated",
                    "browsingContext.contextDestroyed",
                    "browsingContext.load",
                    "browsingContext.domContentLoaded",
                    "browsingContext.userPromptOpened",
                    "browsingContext.userPromptClosed",
                    "browsingContext.navigationStarted",
                    "browsingContext.navigationFailed",
                ],
            )
        except Exception as e:
            logger.warning("订阅事件失败: %s", e)

        # 注册事件处理
        self._driver.set_callback(
            "browsingContext.contextCreated", self._on_context_created
        )
        self._driver.set_callback(
            "browsingContext.contextDestroyed", self._on_context_destroyed
        )
        self._driver.set_callback(
            "browsingContext.load", self._on_navigation_event, immediate=True
        )
        self._driver.set_callback(
            "browsingContext.domContentLoaded",
            self._on_navigation_event,
            immediate=True,
        )

    def _setup_download_behavior(self):
        """配置下载行为

        通过 browser.setDownloadBehavior 设置下载路径和行为。
        需要在会话创建后调用。
        """
        download_path = self._options.download_path
        if not download_path:
            return

        download_path = os.path.abspath(download_path)
        try:
            self._driver.run(
                "browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": download_path,
                },
            )
            logger.debug("下载路径已设置: %s", download_path)
        except Exception as e:
            # browser.setDownloadBehavior 可能在某些 Firefox 版本中不受支持
            logger.debug("设置下载行为失败 (可能不支持): %s", e)

    def _refresh_tabs(self):
        """刷新标签页列表"""
        try:
            result = bidi_context.get_tree(self._driver, max_depth=0)
            contexts = result.get("contexts", [])
            with self._context_ids_lock:
                self._context_ids = [c["context"] for c in contexts]
        except Exception as e:
            logger.warning("刷新标签页列表失败: %s", e)

    def _on_context_created(self, params):
        """处理新 context 创建事件"""
        ctx_id = params.get("context", "")
        with self._context_ids_lock:
            if ctx_id and ctx_id not in self._context_ids:
                self._context_ids.append(ctx_id)
                logger.debug("新标签页: %s", ctx_id)

    def _on_context_destroyed(self, params):
        """处理 context 销毁事件"""
        ctx_id = params.get("context", "")
        with self._context_ids_lock:
            if ctx_id in self._context_ids:
                self._context_ids.remove(ctx_id)
                self._contexts.pop(ctx_id, None)
                self._xpath_picker_last_reinject.pop(ctx_id, None)
                with self._context_nav_locks_lock:
                    self._context_nav_locks.pop(ctx_id, None)
            logger.debug("标签页关闭: %s", ctx_id)

    def _on_navigation_event(self, params):
        """处理导航相关事件，自动收敛 XPath picker 注入。"""
        if not getattr(self._options, "xpath_picker_enabled", False):
            return

        ctx_id = params.get("context", "")
        if not ctx_id:
            return

        now = time.time()
        last = self._xpath_picker_last_reinject.get(ctx_id, 0)
        if now - last < 0.8:
            return
        self._xpath_picker_last_reinject[ctx_id] = now

        try:
            tab = self._get_or_create_tab(ctx_id)
            tab._reinject_xpath_picker_if_needed()
        except Exception as e:
            logger.debug("导航事件 XPath picker reinject 失败: %s", e)

    def _get_or_create_tab(self, context_id):
        """获取或创建 FirefoxTab 实例"""
        from .._pages.firefox_tab import FirefoxTab

        # 单例检查
        if context_id in self._contexts:
            tab = self._contexts[context_id]
            if tab is not None:
                return tab

        tab = FirefoxTab.__new__(FirefoxTab)
        tab._init_from_browser(self, context_id)
        self._contexts[context_id] = tab
        try:
            if getattr(self._options, "xpath_picker_enabled", False):
                tab._reinject_xpath_picker_if_needed()
        except Exception:
            pass
        return tab

    def _find_free_port(self, start=None):
        """查找可用端口"""
        if start is None:
            start = self._options.port
            if (
                isinstance(self._options.auto_port, int)
                and self._options.auto_port > 1024
            ):
                start = self._options.auto_port

        for port in range(start, start + 100):
            try:
                if port in self._RESERVED_PORTS:
                    continue
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", port))
                sock.close()
                return port
            except OSError:
                continue

        raise BrowserLaunchError(
            "在端口范围 {}-{} 中找不到可用端口".format(start, start + 100)
        )

    def __repr__(self):
        return "<Firefox {}>".format(self._address)

    def __del__(self):
        pass  # 不在析构中关闭，避免 atexit 问题
