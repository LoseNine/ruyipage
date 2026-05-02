# -*- coding: utf-8 -*-
"""
RuyiPage - 基于 WebDriver BiDi 协议的 Firefox 浏览器自动化框架

用法::

    from ruyipage import FirefoxPage, FirefoxOptions

    # 快速开始
    page = FirefoxPage()
    page.get('https://example.com')
    print(page.title)

    # 自定义配置
    opts = FirefoxOptions()
    opts.set_port(9222).headless()
    page = FirefoxPage(opts)
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from .version import __version__
from ._pages.firefox_base import FirefoxBase
from ._pages.firefox_page import FirefoxPage
from ._pages.firefox_tab import FirefoxTab
from ._pages.firefox_frame import FirefoxFrame
from ._base.browser import (
    Firefox,
    find_existing_browsers,
    find_existing_browsers_by_process,
    find_candidate_ports_by_process,
    _probe_bidi_address,
    create_browser_from_probe_info,
)
from ._configs.firefox_options import FirefoxOptions
from ._elements.firefox_element import FirefoxElement
from ._elements.none_element import NoneElement
from ._elements.static_element import StaticElement
from ._functions.settings import Settings
from ._functions.keys import Keys
from ._functions.by import By
from ._units.extensions import ExtensionManager
from ._units.events import BidiEvent
from ._units.interceptor import InterceptedRequest
from ._units.listener import DataPacket
from ._units.network_tools import DataCollector, NetworkData
from ._units.cookies import CookieInfo
from ._units.script_tools import (
    RealmInfo,
    ScriptRemoteValue,
    ScriptResult,
    PreloadScript,
)
from ._units.tracer import FailureSnapshot, TraceEntry
from .errors import (
    RuyiPageError,
    ElementNotFoundError,
    ElementLostError,
    ContextLostError,
    BiDiError,
    PageDisconnectedError,
    JavaScriptError,
    BrowserConnectError,
    BrowserLaunchError,
    AlertExistsError,
    WaitTimeoutError,
    NoRectError,
    CanNotClickError,
    LocatorError,
    IncorrectURLError,
    NetworkInterceptError,
)


def _page_from_existing_browser_info(info, tab_index=1, latest_tab=False):
    """兼容旧内部调用，转发到探测连接复用逻辑。"""
    return _page_from_live_probe_info(
        info,
        tab_index=tab_index,
        latest_tab=latest_tab,
    )


def _page_from_live_probe_info(info, tab_index=1, latest_tab=False):
    """直接基于一次成功的 live probe 结果构造 FirefoxPage。"""
    address = info["address"]
    browser = create_browser_from_probe_info(info)
    page = FirefoxPage.__new__(FirefoxPage)
    FirefoxPage._PAGES[address] = page
    FirefoxBase.__init__(page)
    page._page_initialized = True
    page._firefox = browser

    tab_ids = browser.tab_ids
    if tab_ids:
        if latest_tab:
            ctx_id = tab_ids[-1]
        else:
            idx = tab_index - 1 if isinstance(tab_index, int) and tab_index > 0 else 0
            if isinstance(tab_index, int) and -len(tab_ids) <= idx < len(tab_ids):
                ctx_id = tab_ids[idx]
            else:
                ctx_id = tab_ids[0]
    else:
        ctx_id = None

    if not ctx_id:
        from ._bidi import browsing_context as bidi_context

        result = bidi_context.create(browser.driver, "tab")
        ctx_id = result.get("context", "")

    page._init_context(browser, ctx_id)
    return page


def _page_from_probe(address, timeout=0.2, tab_index=1, latest_tab=False):
    """直接基于探测阶段成功建立的连接构造 FirefoxPage。"""
    info = _probe_bidi_address(address, timeout=timeout, keep_driver=True)
    if not info:
        return None
    return _page_from_live_probe_info(
        info,
        tab_index=tab_index,
        latest_tab=latest_tab,
    )


def _scan_live_probes(host, start_port, end_port, timeout=0.2, max_workers=64):
    """并发扫描端口，并保留成功探测到的 live BiDi 连接。"""
    ports = list(range(int(start_port), int(end_port) + 1))
    if not ports:
        return []

    workers = max(1, min(int(max_workers), len(ports)))
    results = []
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="ruyipage-live-probe"
    ) as executor:
        future_map = {
            executor.submit(
                _probe_bidi_address,
                "{}:{}".format(host, port),
                timeout,
                True,
            ): port
            for port in ports
        }
        for future in as_completed(future_map):
            try:
                info = future.result()
            except Exception:
                info = None
            if info:
                results.append(info)

    results.sort(key=lambda item: int(item.get("port", 0)))
    return results


def _cleanup_live_probe_infos(infos, keep_address=None):
    """关闭未被选中的 live probe 连接。"""
    for item in infos:
        if item.get("address") == keep_address:
            continue
        driver = item.get("driver")
        if driver:
            try:
                driver.stop()
            except Exception:
                pass


def launch(
    *,
    headless=False,
    private=False,
    xpath_picker=False,
    action_visual=False,
    port=9222,
    browser_path=None,
    user_dir=None,
    close_on_exit=True,
    window_size=(1280, 800),
    timeout_base=10,
    timeout_page_load=30,
    timeout_script=30,
    trace=False,
    failure_snapshot=False,
    snapshot_dir=None,
):
    """快速启动 FirefoxPage（小白友好入口）。

    Args:
        headless: 是否无头
        private: 是否启用 Firefox 私密浏览模式
        xpath_picker: 是否启用页面 XPath 选择浮窗
        action_visual: 是否启用鼠标行为可视化调试模式
        port: 远程调试端口
        browser_path: Firefox 可执行文件路径。
            适用于 Firefox 安装在非默认目录时。
        user_dir: 用户目录 / profile 目录。
            适用于希望复用登录态、Cookie、扩展时。
        close_on_exit: Python 程序退出时是否自动关闭浏览器。
            默认 ``True``。仅对 ruyipage 自己启动的浏览器生效；
            attach 已有浏览器时只断开连接，不主动关闭外部进程。
        window_size: 窗口大小 (width, height)
        timeout_base: 基础超时
        timeout_page_load: 页面加载超时
        timeout_script: 脚本执行超时
        trace: 是否启用 debug trace 记录
        failure_snapshot: 是否启用失败自动诊断快照
        snapshot_dir: 诊断快照保存目录

    Returns:
        FirefoxPage

    说明:
        - 推荐新手优先使用 launch()。
        - 内部自动创建 FirefoxOptions 并套用 quick_start 预设。
        - 当你不确定该配置哪些参数时，先从 launch() 开始。
    """
    opts = FirefoxOptions()
    opts.set_port(port).quick_start(
        headless=headless,
        private=private,
        xpath_picker=xpath_picker,
        action_visual=action_visual,
        close_on_exit=close_on_exit,
        window_size=window_size,
        timeout_base=timeout_base,
        timeout_page_load=timeout_page_load,
        timeout_script=timeout_script,
        trace=trace,
        failure_snapshot=failure_snapshot,
        snapshot_dir=snapshot_dir,
    )
    if browser_path:
        opts.set_browser_path(browser_path)
    if user_dir:
        opts.set_user_dir(user_dir)
    return FirefoxPage(opts)


def attach(address="127.0.0.1:9222"):
    """连接到已启动的 Firefox 调试地址（小白友好入口）。

    Args:
        address: 调试地址，例如 127.0.0.1:9222

    Returns:
        FirefoxPage

    说明:
        - 用于连接“已手动启动”的 Firefox 调试端口。
        - 内部启用 existing_only，避免重复启动浏览器进程。
        - 即使设置了 ``close_on_exit(True)``，这里在 Python 退出时也只会
          断开连接，不会主动关闭外部浏览器。
    """
    opts = FirefoxOptions().set_address(address).existing_only(True)
    return FirefoxPage(opts)


def attach_exist_browser(address="127.0.0.1:9222", tab_index=1, latest_tab=False):
    """接管一个已经启动的 Firefox 浏览器。

    Args:
        address: 调试地址，例如 127.0.0.1:9222
        tab_index: 接管后默认切到第几个 tab，按 1 开始计数
        latest_tab: True 时优先切到最新 tab，忽略 tab_index

    Returns:
        FirefoxPage
    """
    page = attach(address)
    target_tab = None

    if latest_tab:
        target_tab = page.latest_tab
    elif tab_index is not None:
        target_tab = page.get_tab(tab_index)

    if target_tab:
        page.browser.activate_tab(target_tab)
        page._context_id = target_tab.tab_id
        page._driver = type(page._driver)(page.browser.driver, target_tab.tab_id)

    return page


def auto_attach_exist_browser(
    address=None,
    host="127.0.0.1",
    start_port=9222,
    end_port=65535,
    timeout=0.2,
    max_workers=64,
    tab_index=1,
    latest_tab=False,
):
    """自动接管一个已经启动的 Firefox 浏览器。

    优先连接显式 address；若未提供或连接失败，则暴力扫描端口范围，
    适合某些 Firefox 指纹浏览器把 ``--remote-debugging-port`` 改成随机端口的场景。

    Args:
        address: 已知调试地址，例如 127.0.0.1:9222。传入后先尝试直连。
        host: 扫描主机，默认 127.0.0.1
        start_port: 扫描起始端口
        end_port: 扫描结束端口
        timeout: 单端口探测超时（秒）
        max_workers: 并发扫描线程数，默认 32
        tab_index: 接管后默认切到第几个 tab，按 1 开始计数
        latest_tab: True 时优先切到最新 tab，忽略 tab_index

    Returns:
        FirefoxPage
    """
    errors = []

    if address:
        try:
            return attach_exist_browser(
                address=address,
                tab_index=tab_index,
                latest_tab=latest_tab,
            )
        except Exception as e:
            errors.append("{} -> {}".format(address, e))

    browsers = _scan_live_probes(
        host=host,
        start_port=start_port,
        end_port=end_port,
        timeout=timeout,
        max_workers=max_workers,
    )
    if not browsers:
        raise RuntimeError(
            "没有发现可接管的 Firefox 浏览器，请检查调试端口是否开启，"
            "或扩大扫描端口范围。"
        )

    for item in browsers:
        try:
            page = _page_from_live_probe_info(
                item,
                tab_index=tab_index,
                latest_tab=latest_tab,
            )
            if page:
                _cleanup_live_probe_infos(browsers, keep_address=item["address"])
                return page
            raise RuntimeError("探测成功但无法复用连接")
        except Exception as e:
            errors.append("{} -> {}".format(item["address"], e))

    _cleanup_live_probe_infos(browsers)

    detail = "；".join(errors[:3]) if errors else ""
    raise RuntimeError(
        "发现了可探测端口，但没有可真正接管的 Firefox 会话。"
        "这通常表示指纹浏览器已被自身或其他客户端占用了唯一 BiDi session。"
        + (" 失败详情: {}".format(detail) if detail else "")
    )


def find_exist_browsers(
    host="127.0.0.1",
    start_port=9222,
    end_port=9322,
    timeout=0.5,
    max_workers=32,
):
    """发现当前机器上可接管的 Firefox 浏览器列表。"""
    return find_existing_browsers(
        host=host,
        start_port=start_port,
        end_port=end_port,
        timeout=timeout,
        max_workers=max_workers,
    )


def find_exist_browsers_by_process(
    host="127.0.0.1",
    timeout=0.5,
    max_workers=32,
):
    """按进程特征发现可接管浏览器（推荐 ADS / FlowerBrowser 场景）。"""
    return find_existing_browsers_by_process(
        host=host,
        timeout=timeout,
        max_workers=max_workers,
        keep_driver=False,
    )


def auto_attach_exist_browser_by_process(
    host="127.0.0.1",
    timeout=0.2,
    max_workers=32,
    tab_index=1,
    latest_tab=False,
):
    """按进程特征自动探测并接管已打开浏览器。"""
    candidate_ports = find_candidate_ports_by_process()
    if not candidate_ports:
        raise RuntimeError(
            "未从进程特征中发现 Firefox 调试端口，请确认浏览器已启动并启用 --remote-debugging-port。"
        )

    browsers = find_existing_browsers_by_process(
        host=host,
        timeout=timeout,
        max_workers=max_workers,
        keep_driver=True,
    )
    if not browsers:
        occupied_infos = []
        for port in candidate_ports:
            info = _probe_bidi_address(
                "{}:{}".format(host, port),
                timeout=timeout,
                keep_driver=False,
            )
            if info and info.get("probe_state") == "occupied":
                occupied_infos.append(info)

        if occupied_infos:
            detail = "；".join(
                "{} -> {}{}".format(
                    item["address"],
                    item.get("status_message") or "Session already started",
                    (
                        " ({})".format(item.get("error_message"))
                        if item.get("error_message")
                        else ""
                    ),
                )
                for item in occupied_infos[:3]
            )
            raise RuntimeError(
                "已发现 Firefox 调试端口，但其唯一 BiDi session 已被占用，当前无法接管。"
                + (" 失败详情: {}".format(detail) if detail else "")
            )

        raise RuntimeError("已发现候选调试端口，但未检测到可接管的 Firefox BiDi 会话。")

    errors = []
    for item in browsers:
        try:
            return _page_from_live_probe_info(
                item,
                tab_index=tab_index,
                latest_tab=latest_tab,
            )
        except Exception as e:
            errors.append("{} -> {}".format(item["address"], e))

    _cleanup_live_probe_infos(browsers)

    detail = "；".join(errors[:3]) if errors else ""
    raise RuntimeError(
        "按进程特征发现了候选端口，但未能完成接管。"
        + (" 失败详情: {}".format(detail) if detail else "")
    )


def find_candidate_ports_from_process():
    """按进程特征返回候选监听端口（不做 BiDi 探测）。"""
    return find_candidate_ports_by_process()


__all__ = [
    # 核心类
    "FirefoxPage",
    "FirefoxTab",
    "FirefoxFrame",
    "Firefox",
    "FirefoxOptions",
    # 元素
    "FirefoxElement",
    "NoneElement",
    "StaticElement",
    # 配置
    "Settings",
    "Keys",
    "By",
    # 单元
    "ExtensionManager",
    "BidiEvent",
    "InterceptedRequest",
    "DataPacket",
    "DataCollector",
    "NetworkData",
    "CookieInfo",
    "RealmInfo",
    "ScriptRemoteValue",
    "ScriptResult",
    "PreloadScript",
    "FailureSnapshot",
    "TraceEntry",
    # 异常
    "RuyiPageError",
    "ElementNotFoundError",
    "ElementLostError",
    "ContextLostError",
    "BiDiError",
    "PageDisconnectedError",
    "JavaScriptError",
    "BrowserConnectError",
    "BrowserLaunchError",
    "AlertExistsError",
    "WaitTimeoutError",
    "NoRectError",
    "CanNotClickError",
    "LocatorError",
    "IncorrectURLError",
    "NetworkInterceptError",
    # 便捷入口
    "launch",
    "attach",
    "attach_exist_browser",
    "auto_attach_exist_browser",
    "find_exist_browsers",
    "find_exist_browsers_by_process",
    "auto_attach_exist_browser_by_process",
    # 版本
    "__version__",
]
