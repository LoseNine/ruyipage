# -*- coding: utf-8 -*-
"""Firefox 浏览器管理器

管理浏览器进程的启动/连接、会话创建、标签页生命周期。
单例模式，同一地址只创建一个实例。
"""

import os
import sys
import time
import atexit
import shutil
import socket
import secrets
import logging
import tempfile
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from .driver import BrowserBiDiDriver, ContextDriver
from .._adapter.remote_agent import get_bidi_ws_url
from .._functions.sleep import sleep as _sleep
from .._configs.firefox_options import FirefoxOptions
from .._bidi import browser_module as bidi_browser
from .._bidi import network as bidi_network
from .._bidi import session as bidi_session
from .._bidi import browsing_context as bidi_context
from .._bidi import script as bidi_script
from ..errors import BrowserConnectError, BrowserLaunchError

logger = logging.getLogger("ruyipage")

_BASELINE_PRELOAD_SCRIPT = "() => {}"
_BASELINE_PRELOAD_ATTEMPTS = 2

# 「连到的 Firefox 不是本实例启动的」的错误标记。
# 并发启动时两个 Firefox 可能落到同一个端口（监听 socket 带 SO_REUSEADDR，
# Windows 允许第二个进程绑定已在监听的端口），此时对它做任何破坏性操作
# （browser.close、按端口杀进程）都会伤及别的实例。
_FOREIGN_BROWSER_MARKER = "__foreign_browser__"


class _ForeignBrowserError(BrowserConnectError):
    """连到的 Firefox 不是本实例启动的，应换端口重启而非重置它。"""


def _is_foreign_browser_error(exc):
    if isinstance(exc, _ForeignBrowserError):
        return True
    return isinstance(exc, BrowserConnectError) and _FOREIGN_BROWSER_MARKER in str(exc)


def _normalize_profile_path(path):
    """把 profile 路径规范到可比较的形式（大小写、分隔符、符号链接）。"""
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(str(path))))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(str(path)))


DEFAULT_FIREFOX_PROCESS_NAME_PATTERNS = (
    "firefox.exe",
    "flowerbrowser.exe",
    "adsbrowser.exe",
    "firefox",
    "firefox-bin",
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


def _is_valid_context_id(context_id):
    return isinstance(context_id, str) and bool(context_id)


def _connect_timeout_for_host(host, default=2.0):
    return 0.1 if host in ("127.0.0.1", "localhost", "::1") else default


def _probe_bidi_address(address, timeout=1.0, keep_driver=False):
    """探测 address 是否为可接管的 Firefox BiDi 实例。"""
    host, port = address.rsplit(":", 1)
    try:
        port = int(port)
    except (TypeError, ValueError):
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
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass

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
        except Exception as e:
            logger.debug("探测窗口信息失败: %s", e)
            windows = []
        try:
            contexts = bidi_context.get_tree(driver, max_depth=0).get("contexts", [])
        except Exception as e:
            logger.debug("探测上下文信息失败: %s", e)
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
    except Exception as e:
        logger.debug("探测 BiDi 地址失败 %s: %s", address, e)
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
    browser._baseline_preload_script_id = None
    browser._baseline_preload_session_id = None
    browser._baseline_preload_lock = threading.Lock()
    browser._atexit_registered = False
    browser._ensure_baseline_preload()
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
            except Exception as e:
                logger.debug("端口扫描线程异常: %s", e)
                info = None
            if info:
                if info.get("probe_state", "attachable") == "attachable":
                    result.append(info)

    result.sort(key=lambda item: int(item.get("port", 0)))
    return result


def _discover_pids_and_ports_linux(process_name_patterns, commandline_patterns):
    """Linux: 通过 /proc 发现匹配的 Firefox 进程及其监听端口。"""
    pids = set()
    ports = set()

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open("/proc/{}/comm".format(pid), "r") as f:
                comm = f.read().strip().lower()
        except (OSError, PermissionError):
            continue
        try:
            with open("/proc/{}/cmdline".format(pid), "rb") as f:
                cmdline = f.read().decode(errors="replace").replace("\x00", " ").lower()
        except (OSError, PermissionError):
            cmdline = ""

        name_ok = any(p in comm for p in process_name_patterns)
        cmd_ok = any(p in cmdline for p in commandline_patterns)
        if name_ok or cmd_ok:
            pids.add(pid)

    if not pids:
        return pids, ports

    LOCAL_ADDRS_V4 = {"0100007F", "00000000"}
    LOCAL_ADDRS_V6 = {
        "00000000000000000000000000000000",
        "00000000000000000000000001000000",
    }
    inode_to_port = {}

    for tcp_file, local_addrs in [
        ("/proc/net/tcp", LOCAL_ADDRS_V4),
        ("/proc/net/tcp6", LOCAL_ADDRS_V6),
    ]:
        try:
            with open(tcp_file, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) < 10 or parts[3] != "0A":
                        continue
                    ip_hex, port_hex = parts[1].rsplit(":", 1)
                    if ip_hex in local_addrs:
                        port = int(port_hex, 16)
                        inode = parts[9]
                        if inode != "0":
                            inode_to_port[inode] = port
        except (OSError, PermissionError):
            continue

    if not inode_to_port:
        return pids, ports

    for pid in pids:
        fd_dir = "/proc/{}/fd".format(pid)
        try:
            for fd_name in os.listdir(fd_dir):
                try:
                    link = os.readlink(os.path.join(fd_dir, fd_name))
                except (OSError, PermissionError):
                    continue
                if link.startswith("socket:[") and link.endswith("]"):
                    inode = link[8:-1]
                    if inode in inode_to_port:
                        ports.add(inode_to_port[inode])
        except (OSError, PermissionError):
            continue

    return pids, ports


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
        except Exception as e:
            logger.debug("进程扫描失败: %s", e)

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
            except Exception as e:
                logger.debug("进程扫描失败: %s", e)
    else:
        try:
            pids, ports = _discover_pids_and_ports_linux(
                process_name_patterns, commandline_patterns
            )
        except Exception as e:
            logger.debug("进程扫描失败: %s", e)

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
            except Exception as e:
                logger.debug("端口扫描线程异常: %s", e)
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
        except Exception as e:
            logger.debug("进程扫描失败: %s", e)

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
            except Exception as e:
                logger.debug("进程扫描失败: %s", e)
    else:
        try:
            _, ports = _discover_pids_and_ports_linux(
                process_name_patterns, commandline_patterns
            )
        except Exception as e:
            logger.debug("进程扫描失败: %s", e)

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

    # 启动宽限期：retry 预算耗尽后，只要 Firefox 进程还活着就再等一会。
    # 并发启动多个实例时 Windows 冷启动明显变慢，不给宽限就会把正常实例
    # 判死重启，白白多起一个进程。默认按 retry 预算的两倍推算，夹在两者之间。
    _LAUNCH_GRACE_TIMEOUT = 30.0
    _LAUNCH_GRACE_MIN = 5.0

    # 实例状态的类级默认值：清理路径（quit / atexit / 强杀）必须在任何构造
    # 阶段都能安全调用，包括 __init__ 中途失败或通过 __new__ 建出的实例。
    _reserved_port = None
    _browser_pid = None
    _session_ownership_verified = False
    _grace_spent = 0.0
    _liveness_cache = None

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
                # 每个实例持有自己的副本：启动会把端口/profile/fpfile 写回选项，
                # 共用同一对象时并发启动会挤在同一个 profile 目录上。
                self._options = addr_or_opts.copy()
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
            # 是否已通过 moz:profile 确认连上的就是自己启动的 Firefox。
            # 未确认前不许对它发 browser.close。
            self._session_ownership_verified = False
            # session.new 返回的 moz:processID：Windows 上 firefox.exe 是个
            # launcher stub，Popen 记录的 PID 启动完就退出了，真正的主进程
            # 要从这里拿。
            self._browser_pid = None
            self._grace_spent = 0.0
            self._quit_lock = threading.Lock()
            self._proxy_auth_intercept_id = None
            self._proxy_auth_subscription_id = None
            self._xpath_picker_last_reinject = {}
            self._baseline_preload_script_id = None
            self._baseline_preload_session_id = None
            self._baseline_preload_lock = threading.Lock()
            self._atexit_registered = False

            try:
                self._connect_or_launch()
                self._register_exit_cleanup()
                with self._lock:
                    self._BROWSERS[self._address] = self
                self._initialized = True
            except Exception:
                self._cleanup_failed_startup()
                raise

    def get_context_nav_lock(self, context_id):
        """返回某个 browsing context 专属的导航锁。"""
        with self._context_nav_locks_lock:
            lock = self._context_nav_locks.get(context_id)
            if lock is None:
                lock = threading.RLock()
                self._context_nav_locks[context_id] = lock
            return lock

    def _ensure_baseline_preload(self):
        """Ensure the current BiDi session has the neutral baseline preload."""
        session_id = self._session_id
        if not self._driver or not session_id:
            logger.warning(
                "BiDi baseline preload skipped: active session unavailable"
            )
            return False

        with self._baseline_preload_lock:
            if (
                self._baseline_preload_script_id
                and self._baseline_preload_session_id == session_id
            ):
                return True

            failures = []
            for _attempt in range(_BASELINE_PRELOAD_ATTEMPTS):
                try:
                    result = bidi_script.add_preload_script(
                        self._driver,
                        _BASELINE_PRELOAD_SCRIPT,
                    )
                    script_id = result.get("script", "")
                    if script_id:
                        self._baseline_preload_script_id = script_id
                        self._baseline_preload_session_id = session_id
                        return True
                    failures.append("empty script id")
                except Exception as exc:
                    failures.append(str(exc))

            self._baseline_preload_script_id = None
            self._baseline_preload_session_id = None
            logger.warning(
                "BiDi baseline preload registration failed after %d attempts: %s",
                _BASELINE_PRELOAD_ATTEMPTS,
                "; ".join(failures),
            )
            return False

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

    def _terminate_owned_process_tree(self, timeout=5):
        """终止本实例自己启动的整棵 Firefox 进程树。

        Windows 上 ``firefox.exe`` 是个 launcher stub：它把自己重新拉起一遍
        然后退出，所以 Popen 记录的 PID 在启动完成时**已经死了**。对着它发
        ``taskkill /T`` 一个进程都杀不到——失败路径没有 BiDi 连接可用，收尸
        只能靠这里，于是每一轮失败都漏一整个 Firefox（约 10 个进程）。
        因此 Windows 上改为按 profile 目录反查真实的主进程。
        """
        process = self._process
        try:
            if sys.platform == "win32":
                self._terminate_windows_process_tree(process, timeout)
                return

            if not process or process.poll() is not None:
                return
            import signal

            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (AttributeError, OSError, ProcessLookupError):
                process.kill()
            try:
                process.wait(timeout=timeout)
            except Exception:
                pass
        finally:
            self._process = None
            self._browser_pid = None

    def _terminate_windows_process_tree(self, process, timeout):
        pids = set()
        if process is not None and process.poll() is None:
            pids.add(int(process.pid))
        if self._browser_pid:
            pids.add(int(self._browser_pid))
        pids.update(self._find_own_firefox_pids_windows())

        for pid in pids:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        if process is not None:
            try:
                process.wait(timeout=timeout)
            except Exception:
                pass

    def _find_own_firefox_pids_windows(self):
        """按 profile 目录找出本实例 Firefox 的主进程 PID。

        profile 目录每个实例独有，比按端口查可靠：端口撞车正是这条路径要
        处理的场景，按端口反查会把别人的浏览器一起杀掉。
        """
        profile = self._options.profile_path
        if not profile:
            return set()

        needles = {
            os.path.normcase(str(profile)),
            os.path.normcase(os.path.normpath(str(profile))),
        }
        exe_names = {"firefox.exe"}
        browser_path = self._options.browser_path
        if browser_path:
            exe_names.add(os.path.basename(str(browser_path)).lower())

        import json

        name_filter = " OR ".join(
            "Name='{}'".format(name.replace("'", "''")) for name in sorted(exe_names)
        )
        script = (
            "Get-CimInstance Win32_Process -Filter \"{}\" | "
            "Select-Object ProcessId, ParentProcessId, CommandLine | "
            "ConvertTo-Json -Compress"
        ).format(name_filter)
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script],
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            data = json.loads(out.decode(errors="ignore") or "[]")
        except Exception as exc:
            logger.debug("按 profile 查询 Firefox 进程失败: %s", exc)
            return set()
        if isinstance(data, dict):
            data = [data]

        roots = set()
        for item in data or []:
            cmdline = os.path.normcase(str(item.get("CommandLine") or ""))
            # 内容进程的命令行里没有 --profile，只有 -contentproc；
            # 它们随主进程的 taskkill /T 一起退出，这里只收主进程。
            if "-contentproc" in cmdline:
                continue
            if not any(needle in cmdline for needle in needles):
                continue
            try:
                roots.add(int(item.get("ProcessId") or 0))
            except (TypeError, ValueError):
                continue
        roots.discard(0)
        return roots

    def _remove_auto_profile(self, attempts=3, delay=0.3):
        """删除自动创建的临时 profile 目录。

        Windows 上 Firefox 退出后句柄释放存在延迟，单次 rmtree 常留下残目录，
        因此这里重试几次再放弃。
        """
        path = self._auto_profile
        if not path:
            return

        for index in range(attempts):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                break
            if index < attempts - 1:
                time.sleep(delay)
        else:
            logger.warning("临时 profile 目录未能删除，请手动清理: %s", path)

        self._auto_profile = None

    def _cleanup_failed_startup(self):
        if self._driver:
            try:
                self._teardown_proxy_auth()
                self._driver.stop()
            except Exception:
                with BrowserBiDiDriver._lock:
                    BrowserBiDiDriver._BROWSERS.pop(self._address, None)
            self._driver = None

        if not self._options.is_existing_only:
            self._terminate_owned_process_tree()
            self._remove_auto_profile()
        else:
            self._process = None

        self._release_reserved_port()
        with self._lock:
            for address, browser in list(self._BROWSERS.items()):
                if browser is self:
                    self._BROWSERS.pop(address, None)
        self._initialized = False

    def _set_launch_port(self, port):
        """更新 options/address，并同步进程内端口保留。"""
        if self._reserved_port is not None and self._reserved_port != port:
            self._release_reserved_port(self._reserved_port)
        self._options._set_port_for_launch(port)
        self._address = self._options.address
        self._reserve_port(port)
        # 换了端口，之前对「连上的是自己的浏览器」的结论就作废了
        self._session_ownership_verified = False
        self._browser_pid = None

    def _rotate_launch_port(self):
        """重启前换一个端口。

        随机 / 自动端口模式下重新挑一个：旧端口上可能还挂着别人的 Firefox
        （端口撞车），或自己那个还没退干净的旧进程。固定端口是用户显式指定
        的，只在确实被占用时才换。
        """
        if self._options.auto_port or self._options.random_port:
            old_port = self._options.port
            # 旧端口仍在 _RESERVED_PORTS 里，_find_free_port 会自动跳过它
            new_port = self._find_free_port()
            self._set_launch_port(new_port)
            logger.warning("重启 Firefox：端口 %s -> %s", old_port, new_port)
            return
        self._ensure_launch_port_available()

    def _can_close_remote_browser(self):
        """是否允许对当前连接的浏览器发 browser.close。

        显式 attach 的地址是用户指定的，照旧允许；自己启动的必须先通过
        moz:profile 确认归属——否则端口撞车时会把别的实例的浏览器关掉。
        """
        if self._options.is_existing_only:
            return True
        return bool(self._session_ownership_verified)

    def _owns_launched_process(self):
        """本实例是否有一个自己拉起来、且仍存活的 Firefox 进程。"""
        if self._options.is_existing_only:
            return False
        process = self._process
        return process is not None and process.poll() is None

    # 宽限期里存活探测的节流间隔：Windows 上要跑一次 PowerShell 查进程表
    _LIVENESS_PROBE_INTERVAL = 2.0

    def _launched_browser_alive(self):
        """自己启动的 Firefox 是否还活着——比 ``_process.poll()`` 可信。

        Windows 上 Popen 记录的是 launcher stub，启动完就退出了；只看它会把
        正在冷启动的正常实例判死。stub 退出后改按 profile 反查真实主进程，
        并做节流避免每次轮询都拉起 PowerShell。
        """
        process = self._process
        if process is None:
            return False
        if process.poll() is None:
            return True
        if sys.platform != "win32":
            return False

        now = time.time()
        cached = self._liveness_cache
        if cached and now - cached[0] < self._LIVENESS_PROBE_INTERVAL:
            return cached[1]
        alive = bool(self._find_own_firefox_pids_windows())
        self._liveness_cache = (now, alive)
        return alive

    def _launch_grace_timeout(self):
        explicit = self._options.retry_grace
        if explicit is not None:
            return float(explicit)
        budget = float(self._options.retry_times + 1) * float(
            self._options.retry_interval
        )
        return max(self._LAUNCH_GRACE_MIN, min(self._LAUNCH_GRACE_TIMEOUT, budget * 2))

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
            if -len(self._context_ids) <= idx < len(self._context_ids):
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

    def new_tab(self, url=None, background=False, user_context=None):
        """新建标签页

        Args:
            url: 初始 URL
            background: 是否在后台创建
            user_context: 可选的 Firefox user context ID

        Returns:
            FirefoxTab
        """
        ref_ctx = self._context_ids[0] if self._context_ids else None
        params = {"type": "tab", "background": background}
        if ref_ctx:
            params["referenceContext"] = ref_ctx
        if user_context:
            params["userContext"] = user_context

        result = self._driver.run("browsingContext.create", params)
        ctx_id = result.get("context", "")

        if ctx_id and ctx_id not in self._context_ids:
            self._context_ids.append(ctx_id)

        tab = self._get_or_create_tab(ctx_id)

        if url:
            tab.get(url)

        return tab

    def new_container_tab(self, url=None, background=False):
        """创建一个新的 Firefox container tab。"""
        result = self._driver.run("browser.createUserContext")
        user_context = result.get("userContext", "")
        return self.new_tab(url=url, background=background, user_context=user_context)

    def new_container_tabs(self, count, url=None, background=False):
        """创建多个 Firefox container tabs。"""
        count = int(count)
        if count <= 0:
            raise ValueError("count 必须大于 0")

        tabs = []
        for _ in range(count):
            tabs.append(self.new_container_tab(url=url, background=background))
        return tabs

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
            if self._options.is_existing_only:
                self._detach_on_exit()
                self._process = None
                self._release_reserved_port()
                with self._lock:
                    self._BROWSERS.pop(self._address, None)
                self._initialized = False
                return

            if self._driver:
                self._driver.mark_closing()
                # 归属未确认时不发 browser.close：端口撞车时连上的可能是别的
                # 实例的浏览器。自己的进程走下面的进程树终止。
                if self._can_close_remote_browser():
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

            if self._process and force:
                self._terminate_owned_process_tree(timeout=timeout)
            elif self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=timeout)
                except Exception:
                    pass
                # terminate() 只作用于启动进程，Firefox 的 content 进程会继续
                # 占用 profile 目录，因此仍需确保整棵进程树退出。
                self._terminate_owned_process_tree(timeout=timeout)
                self._process = None

            self._remove_auto_profile()
            self._release_reserved_port()

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
            # 连接可能已经断开，但自动创建的 profile 目录仍需回收。
            if not self._options.is_existing_only:
                self._terminate_owned_process_tree()
                self._remove_auto_profile()
            self._release_reserved_port()
            return

        if self._should_close_browser_on_exit():
            try:
                self.quit(timeout=3, force=True)
            except Exception:
                self._detach_on_exit()
                self._remove_auto_profile()
            return

        self._detach_on_exit()

    def reconnect(self):
        """重新连接"""
        if self._driver:
            self._teardown_proxy_auth()
            self._driver.stop()
        self._driver = self._new_driver()
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
        # Dynamic ports are resolved before Firefox is launched.
        if self._options.auto_port or self._options.random_port:
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
        self._grace_spent = 0.0
        with self._launch_lock:
            self._ensure_launch_port_available()
            self._launch_browser()

        if self._await_launched_browser():
            return

        # 首轮没连上有两种可能：Firefox 起得慢 / 端口被抢；或连上的根本是别的
        # 实例的浏览器。两种都换一个端口重启，原样复用旧端口好不了。
        if not self._options.is_existing_only:
            self._terminate_owned_process_tree()

            with self._launch_lock:
                self._rotate_launch_port()
                self._launch_browser()
            if self._await_launched_browser():
                return

        raise BrowserConnectError(
            "启动后无法连接到 {}，请检查 Firefox 是否正常启动".format(self._address)
        )

    def _await_launched_browser(self):
        """等刚启动的 Firefox 可连接；连到别人的浏览器算失败而不是异常。"""
        try:
            if self._wait_for_connection():
                return True
            logger.warning("首次启动连接失败，尝试重启 Firefox 后再重试一次...")
        except BrowserConnectError as exc:
            if not _is_foreign_browser_error(exc):
                raise
            logger.warning(
                "%s 上的 Firefox 不是本实例启动的（端口被其他实例占用），换端口重启...",
                self._address,
            )
        return False

    def _wait_for_connection(self, allow_grace=True):
        """等待刚启动的 Firefox 可连接。

        先按 retry 预算轮询；预算耗尽后，只要自己的进程还活着就进入宽限期
        继续等——并发启动时 Windows 冷启动会明显变慢，不给宽限就会把正常实例
        判死重启。宽限期是整次启动的一份总预算，首轮和重启兜底共用，避免
        「Firefox 根本起不来」的失败被拖成两倍时间。

        ``_ForeignBrowserError`` 会原样抛出，调用方据此决定换端口重启。
        """
        deadline = time.time() + max(
            1.0,
            float(self._options.retry_times + 1)
            * float(self._options.retry_interval),
        )
        while time.time() < deadline:
            try:
                if self._try_connect():
                    return True
            except BrowserConnectError as exc:
                if _is_foreign_browser_error(exc):
                    raise
            time.sleep(min(0.2, max(0.01, deadline - time.time())))

        if not allow_grace:
            return False
        remaining = self._launch_grace_timeout() - self._grace_spent
        if remaining <= 0:
            return False

        grace_started = time.time()
        grace_deadline = grace_started + remaining
        try:
            while time.time() < grace_deadline:
                # 宽限期不持锁，其它线程的 quit() 随时可能把 _process 置空
                if not self._launched_browser_alive():
                    return False
                try:
                    if self._try_connect():
                        return True
                except BrowserConnectError as exc:
                    if _is_foreign_browser_error(exc):
                        raise
                time.sleep(min(0.5, max(0.01, grace_deadline - time.time())))
            return False
        finally:
            self._grace_spent += time.time() - grace_started

    def _kill_firefox_by_port(self, port):
        """通过 /proc 找到监听指定端口的进程并终止，避免误杀无关 Firefox。"""
        if sys.platform == "win32":
            import json

            target_pids = set()
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
                data = json.loads(out.decode(errors="ignore") or "[]")
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    try:
                        item_port = int(item.get("LocalPort") or 0)
                        pid = int(item.get("OwningProcess") or 0)
                    except (TypeError, ValueError):
                        continue
                    addr = str(item.get("LocalAddress") or "")
                    if (
                        item_port == int(port)
                        and pid > 0
                        and addr in ("127.0.0.1", "::1", "0.0.0.0", "::")
                    ):
                        target_pids.add(pid)
            except Exception as e:
                logger.debug("按端口查询 Windows 监听进程失败: %s", e)
                return

            if not target_pids:
                return

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
                data = json.loads(out.decode(errors="ignore") or "[]")
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    try:
                        pid = int(item.get("ProcessId") or 0)
                    except (TypeError, ValueError):
                        continue
                    if pid not in target_pids:
                        continue
                    name = str(item.get("Name") or "").lower()
                    cmd = str(item.get("CommandLine") or "").lower()
                    name_ok = any(
                        pattern in name
                        for pattern in DEFAULT_FIREFOX_PROCESS_NAME_PATTERNS
                    )
                    cmd_ok = any(
                        pattern in cmd
                        for pattern in DEFAULT_FIREFOX_COMMANDLINE_PATTERNS
                    )
                    if not (name_ok or cmd_ok):
                        continue
                    logger.debug("找到端口 %d 的 Firefox 进程 PID=%d，正在终止", port, pid)
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    return
            except Exception as e:
                logger.debug("按 PID 查询 Windows Firefox 进程失败: %s", e)
            return

        import signal

        target_port_hex = format(port, "X").upper()
        target_inode = None

        for tcp_file in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(tcp_file, "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) < 10 or parts[3] != "0A":
                            continue
                        _, port_hex = parts[1].rsplit(":", 1)
                        if port_hex.upper() == target_port_hex:
                            target_inode = parts[9]
                            break
            except (OSError, PermissionError):
                continue
            if target_inode:
                break

        if not target_inode:
            return

        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            fd_dir = "/proc/{}/fd".format(entry)
            try:
                for fd_name in os.listdir(fd_dir):
                    try:
                        link = os.readlink(os.path.join(fd_dir, fd_name))
                    except (OSError, PermissionError):
                        continue
                    if link == "socket:[{}]".format(target_inode):
                        pid = int(entry)
                        logger.debug("找到端口 %d 的 Firefox 进程 PID=%d，正在终止", port, pid)
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        return
            except (OSError, PermissionError):
                continue

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
                self._kill_firefox_by_port(int(port_str))
            else:
                self._kill_firefox_by_port(self._options.port)
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
        from .._functions.tools import is_port_open
        host, port_str = self._address.rsplit(":", 1)
        return is_port_open(
            host, int(port_str), timeout=_connect_timeout_for_host(host)
        )

    def _ensure_launch_port_available(self):
        """启动前确保目标端口未被其他进程占用。"""
        if self._options.auto_port or self._options.random_port:
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

    def _try_connect(self):
        """尝试连接到已有浏览器

        连接失败时正确清理 BrowserBiDiDriver 单例，避免残留无效实例。

        Returns:
            True 连接成功，False 失败
        """
        # 先检查端口是否可达
        host, port = self._address.rsplit(":", 1)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(_connect_timeout_for_host(host))
                sock.connect((host, int(port)))
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False

        # 端口预留在这里不释放：握手失败只是中间态，进程和端口仍归我们；
        # 连接成功后 Firefox 还在用这个端口。预留到 quit()/放弃启动时才归还，
        # 否则并发实例会在我们的 Firefox 还没监听前把同一端口挑走。
        try:
            host, port = self._address.rsplit(":", 1)
            ws_url = get_bidi_ws_url(host, int(port), timeout=5)
            self._driver = self._new_driver()
            self._driver.start(ws_url)
            self._create_session()
            self._subscribe_events()
            self._setup_proxy_auth()
            self._setup_download_behavior()
            if not self._wait_for_initial_context():
                raise RuntimeError("Firefox 未返回可用的 browsingContext")
            logger.info("已连接到 Firefox: %s", self._address)
            return True
        except BrowserConnectError:
            # stuck session / foreign browser 错误需要向上传播
            self._discard_connection()
            raise
        except Exception as e:
            logger.debug("连接失败: %s", e)
            self._discard_connection()
            return False

    def _discard_connection(self):
        """撤下一次没成功的连接，把在它上面建出来的会话一并结束。

        ``session.new`` 已成功、之后的步骤（如等首个 browsingContext）才失败时，
        只断 WebSocket 会在**自己的** Firefox 上留下一个孤儿 session：下一次
        ``_try_connect`` 就撞上「maximum number of sessions」，进入 6 秒的
        孤儿回收重试，最后还得靳 browser.close 重启浏览器。高并发冷启动下
        这条路很常见，是 issue #31 日志里「无法回收孤儿 session」的直接来源。
        """
        driver = self._driver
        if driver is None:
            self._owns_session = False
            self._session_id = None
            return

        if self._owns_session:
            try:
                bidi_session.end(driver)
            except Exception:
                pass
        self._owns_session = False
        self._session_id = None

        try:
            self._teardown_proxy_auth()
        except Exception:
            pass
        try:
            # 使用 stop() 而非 _stop()，确保清理单例注册
            driver.stop()
        except Exception:
            with BrowserBiDiDriver._lock:
                BrowserBiDiDriver._BROWSERS.pop(self._address, None)
        self._driver = None

    def _new_driver(self):
        """自己启动的 Firefox 不共用 driver 单例；显式 attach 的照旧复用。"""
        return BrowserBiDiDriver(
            self._address, shared=bool(self._options.is_existing_only)
        )

    # 显式 attach 的浏览器等首个窗口的上限。没有窗口是真问题，快速失败。
    _ATTACHED_INITIAL_CONTEXT_TIMEOUT = 3.0

    def _wait_for_initial_context(self, timeout=None, interval=0.1):
        """等 Firefox 返回首个 browsingContext。

        自己启动的实例：Remote Agent 比第一个窗口早就绪，高并发冷启动时两者
        可能差十几秒。这里**不设短时钟上限**——只要进程还活着就一直等到窗口
        出现。早期版本用 3 秒上限，导致连接刚建好就被判死拆掉、每 3 秒重建
        一次（session.end -> session.new -> addPreloadScript），一次卡顿里空
        转十余轮，甚至把本该成功的慢启动逼成失败。用进程存活而非时钟判定，
        既不空转也不误杀。外层的 retry/宽限预算负责真正起不来的情形。

        显式 attach 的浏览器没有自己的进程可判，保留固定的短超时。
        """
        launched = not self._options.is_existing_only
        if timeout is None:
            # 自启动实例靠进程存活判定；再加一个绝对上限兜底，万一存活探测异常
            # 也不会永久挂死。上限取宽限期，足够覆盖最慢的冷启动。
            timeout = (
                max(self._launch_grace_timeout(), self._LAUNCH_GRACE_MIN)
                if launched
                else self._ATTACHED_INITIAL_CONTEXT_TIMEOUT
            )

        deadline = time.time() + timeout
        while time.time() < deadline:
            self._refresh_tabs()
            if self._context_ids:
                return True
            if launched and not self._launched_browser_alive():
                break
            time.sleep(min(interval, max(0.01, deadline - time.time())))
        self._refresh_tabs()
        return bool(self._context_ids)

    def _setup_proxy_auth(self):
        """启用浏览器级代理认证自动应答。"""
        self._teardown_proxy_auth()

        if not self._driver:
            return

        if not self._session_id:
            return

        if not self._options.proxy:
            return

        if getattr(self._options, "uses_fpfile_http_proxy", False):
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
        response = params.get("response") or {}
        status = response.get("status") if isinstance(response, dict) else None
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = None
        is_proxy = source == "proxy" or "proxy" in realm.lower() or status == 407

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

        # 如果配置了 per-tab 代理池，这里生成 session fpfile，避免修改用户原始 fpfile。
        opts.prepare_runtime_files()

        # 写入首选项
        opts.write_prefs_to_profile()

        cmd = opts.build_command()
        logger.info("启动 Firefox: %s", " ".join(cmd))
        # 新进程，旧的存活探测结果作废
        self._liveness_cache = None

        try:
            # 在 Windows 上使用 CREATE_NO_WINDOW 避免弹出控制台
            # CREATE_BREAKAWAY_FROM_JOB 使 Firefox 脱离 Python 的 Job Object，
            # 避免 IDE 调试器停止时连带杀死浏览器进程
            kwargs = {}
            if sys.platform == "win32":
                CREATE_NO_WINDOW = 0x08000000
                CREATE_BREAKAWAY_FROM_JOB = 0x01000000
                flags = CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
                try:
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=flags,
                    )
                except OSError:
                    # 某些受限环境不允许 BREAKAWAY_FROM_JOB，回退
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=CREATE_NO_WINDOW,
                    )
            else:
                # Unix/macOS: start_new_session 使 Firefox 脱离 Python 的进程组，
                # 避免 IDE 调试器停止时连带杀死浏览器进程
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except FileNotFoundError:
            try:
                from .._runtime.resolver import missing_firefox_message

                message = missing_firefox_message(opts.browser_path)
            except Exception:
                message = (
                    "找不到 Firefox: {}\n"
                    "请先运行 python -m ruyipage install，"
                    "或通过 FirefoxOptions.set_browser_path() 指定路径"
                ).format(opts.browser_path)
            raise BrowserLaunchError(
                message
            )
        except Exception as e:
            raise BrowserLaunchError("启动 Firefox 失败: {}".format(e))

    def _activate_session(self, result):
        """Record a new BiDi session and install its baseline preload."""
        self._verify_session_ownership(result)
        self._session_id = result.get("sessionId", "")
        self._driver.session_id = self._session_id
        self._owns_session = True
        self._ensure_baseline_preload()

    def _verify_session_ownership(self, result):
        """确认连上的 Firefox 就是本实例启动的那个。

        依据 ``session.new`` 返回的 ``moz:profile``：profile 目录每个实例独有，
        对不上就说明端口撞车、连到了别的实例的浏览器。此时结束误建的会话、
        断开连接并抛 :class:`_ForeignBrowserError`，由调用方换端口重启；
        绝不能对它发 browser.close。

        显式 attach 的地址是用户指定的，没有「自己的 profile」可比，跳过。
        旧内核不返回 ``moz:profile`` 时放行但不标记为已确认。
        """
        capabilities = result.get("capabilities") or {}

        pid = capabilities.get("moz:processID")
        if isinstance(pid, int) and pid > 0:
            self._browser_pid = pid

        if self._options.is_existing_only:
            return

        theirs = capabilities.get("moz:profile")
        mine = self._options.profile_path
        if not theirs or not mine:
            return

        if _normalize_profile_path(theirs) == _normalize_profile_path(mine):
            self._session_ownership_verified = True
            return

        logger.warning(
            "%s 上的 Firefox 使用的 profile 是 %s，不是本实例的 %s",
            self._address,
            theirs,
            mine,
        )
        driver = self._driver
        if driver is not None:
            try:
                bidi_session.end(driver)
            except Exception:
                pass
            try:
                driver.stop()
            except Exception:
                with BrowserBiDiDriver._lock:
                    BrowserBiDiDriver._BROWSERS.pop(self._address, None)
        self._driver = None
        self._session_id = None
        self._owns_session = False
        self._session_ownership_verified = False
        raise _ForeignBrowserError(
            "{} {} 上的 Firefox 不是本实例启动的（profile 不一致）".format(
                _FOREIGN_BROWSER_MARKER, self._address
            )
        )

    def _create_session(self):
        """创建 BiDi 会话

        处理各种 Firefox BiDi session 状态：
        1. 无活跃 session → session.new 成功
        2. 当前连接已有 session → session.end + session.new
        3. 另一个连接已持有 session（如调试器强杀后的孤儿 session）
           → 断开重连让 Firefox 回收孤儿 session，再创建新 session
        """
        # 先检查状态
        try:
            status = bidi_session.status(self._driver)
            ready = status.get("ready", True)
            msg = status.get("message", "")
        except Exception as e:
            logger.debug("获取会话状态失败，默认 ready=True: %s", e)
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
                self._activate_session(result)
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
                logger.debug("已结束旧会话")
            except Exception:
                pass

            try:
                result = bidi_session.new(
                    self._driver,
                    {},
                    user_prompt_handler=self._options.user_prompt_handler,
                )
                self._activate_session(result)
                logger.info("BiDi 会话已重新创建: %s", self._session_id)
                return
            except Exception as e:
                err_str = str(e).lower()
                if "maximum" not in err_str and "session not created" not in err_str:
                    raise

            # session.new 失败，可能是调试器强杀后残留的孤儿 session。
            # 断开 WebSocket 让 Firefox Remote Agent 回收孤儿 session，
            # 然后重新连接并创建新 session。
            logger.debug("检测到可能的孤儿 session，断开重连以触发 Firefox 清理...")
            self._driver.stop()
            self._driver = None

            host, port_str = self._address.rsplit(":", 1)
            for retry in range(6):
                time.sleep(1)
                try:
                    ws_url = get_bidi_ws_url(host, int(port_str), timeout=5)
                    self._driver = self._new_driver()
                    self._driver.start(ws_url)

                    # 先尝试 end 再 new
                    try:
                        bidi_session.end(self._driver)
                    except Exception:
                        pass

                    status = bidi_session.status(self._driver)
                    if status.get("ready", False):
                        result = bidi_session.new(
                            self._driver,
                            {},
                            user_prompt_handler=self._options.user_prompt_handler,
                        )
                        self._activate_session(result)
                        logger.info(
                            "孤儿 session 已清理，新会话已创建: %s",
                            self._session_id,
                        )
                        return
                    else:
                        # 仍然 not ready，断开再试
                        logger.debug("重试 %d: session 仍未就绪", retry + 1)
                        self._driver.stop()
                        self._driver = None
                except Exception as ex:
                    logger.debug("重连重试 %d 失败: %s", retry + 1, ex)
                    if self._driver:
                        try:
                            self._driver.stop()
                        except Exception:
                            with BrowserBiDiDriver._lock:
                                BrowserBiDiDriver._BROWSERS.pop(self._address, None)
                        self._driver = None

            # 自己刚启动的 Firefox 不可能已经挂着别人的 session——走到这里
            # 说明端口撞车，连上的是别的实例的浏览器。对它发 browser.close
            # 会把别人的浏览器关掉（issue #31 里 Crash Reporter 的来源）。
            # 归属未确认时：杀掉自己那个进程（如果有），换端口重启。
            if not self._can_close_remote_browser():
                if self._owns_launched_process():
                    logger.warning(
                        "无法回收孤儿 session 且 %s 上的浏览器归属未确认，"
                        "终止本实例的 Firefox 进程后换端口重启...",
                        self._address,
                    )
                    self._terminate_owned_process_tree()
                else:
                    logger.warning(
                        "无法回收孤儿 session 且 %s 上的浏览器归属未确认，换端口重启...",
                        self._address,
                    )
                raise _ForeignBrowserError(
                    "{} {} 上的 Firefox 已被其他会话占用，不是本实例启动的".format(
                        _FOREIGN_BROWSER_MARKER, self._address
                    )
                )

            # 显式 attach 的地址：发送 browser.close 让 Firefox 重启自身，
            # 清理不可回收的孤儿 session
            logger.warning(
                "无法回收孤儿 session，尝试通过 browser.close 重置 Firefox..."
            )
            try:
                ws_url = get_bidi_ws_url(host, int(port_str), timeout=5)
                self._driver = self._new_driver()
                self._driver.start(ws_url)
                self._driver.run("browser.close", timeout=3)
            except Exception:
                pass
            finally:
                if self._driver:
                    try:
                        self._driver.stop()
                    except Exception:
                        with BrowserBiDiDriver._lock:
                            BrowserBiDiDriver._BROWSERS.pop(self._address, None)
                    self._driver = None

            raise BrowserConnectError(
                "检测到 Firefox 残留的孤儿会话（通常由调试器强制停止导致），"
                "已尝试重置浏览器。请稍等几秒后重试连接，"
                "或手动重启浏览器的远程调试端口。\n"
                "提示：正常停止调试（而非强制终止）可避免此问题。"
            )

    def _subscribe_events(self):
        """订阅关键事件

        兼容不同 Firefox / BiDi 版本的事件名差异。
        """
        if not self._driver:
            return

        events = [
            "browsingContext.contextCreated",
            "browsingContext.contextDestroyed",
            "browsingContext.load",
            "browsingContext.domContentLoaded",
            "browsingContext.userPromptOpened",
            "browsingContext.userPromptClosed",
            "browsingContext.navigationStarted",
            "browsingContext.navigationFailed",
        ]

        try:
            result = bidi_session.subscribe_compatible(self._driver, events)
            for event, error in result.get("failed_events", []):
                logger.debug("跳过当前 Firefox 不支持的事件 %s: %s", event, error)
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
            bidi_browser.set_download_behavior(
                self._driver,
                behavior="allow",
                download_path=download_path,
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
                self._context_ids = [
                    c.get("context")
                    for c in contexts
                    if _is_valid_context_id(c.get("context"))
                ]
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
        except Exception as e:
            logger.debug("XPath picker 初始注入失败: %s", e)
        return tab

    def _find_free_port(self, start=None):
        """查找可用端口"""
        if start is None and self._options.random_port:
            range_start, range_end = self._options.random_port_range
            candidates = list(range(range_start, range_end + 1))
            secrets.SystemRandom().shuffle(candidates)
        else:
            if start is None:
                start = self._options.port
                if (
                    isinstance(self._options.auto_port, int)
                    and self._options.auto_port > 1024
                ):
                    start = self._options.auto_port
            range_start, range_end = start, start + 99
            candidates = range(range_start, range_end + 1)

        for port in candidates:
            if port in self._RESERVED_PORTS:
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue

        raise BrowserLaunchError(
            "在端口范围 {}-{} 中找不到可用端口".format(range_start, range_end)
        )

    def __repr__(self):
        return "<Firefox {}>".format(self._address)

    def __del__(self):
        pass  # 不在析构中关闭，避免 atexit 问题
