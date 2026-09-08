# -*- coding: utf-8 -*-
"""Debugger - JS 断点调试器

提供 WebDriver BiDi 规范之外的断点级调试能力：下断点、暂停、单步、
读调用栈与作用域变量。底层走 Firefox DevTools 的 RDP 通道
（见 :mod:`ruyipage._adapter.rdp`），与 BiDi 连接并行存在。

启用方式::

    opts = FirefoxOptions()
    opts.enable_debugger()          # 写入所需 pref 并启动 devtools server
    page = FirefoxPage(opts)

    page.debugger.start()
    page.get('https://example.com')

    page.debugger.set_breakpoint('https://example.com/app.js', 42)
    state = page.debugger.wait_paused(timeout=30)
    print(state.why, state.line)
    print(page.debugger.scope())
    page.debugger.step_over()
    page.debugger.resume()

重要限制
--------
JS 线程暂停期间，任何依赖 JS 执行的操作（``run_js``、点击、取元素文本等）
都会阻塞到超时。触发断点的调用应放在后台线程，或在暂停期间只使用
``page.debugger`` 自身的接口。
"""

import logging
import threading
import time
from collections import deque
from queue import Empty, Queue

from .._adapter.rdp import DEFAULT_RDP_PORT, RdpConnection
from ..errors import DebuggerError

logger = logging.getLogger("ruyipage")

# 日志断点的输出缓冲上限，避免高频命中时无限增长
_MAX_LOG_ENTRIES = 1000

# 沿原型链查属性的跳数上限，防止环形原型导致死循环
_MAX_PROTOTYPE_HOPS = 20

_STEP_LIMITS = {
    "over": "next",
    "into": "step",
    "out": "finish",
    "restart": "restart",
}


class Source(object):
    """一个已加载的 JS 源。"""

    __slots__ = ("actor", "url", "introduction_type", "is_black_boxed")

    def __init__(self, form):
        self.actor = form.get("actor", "")
        self.url = form.get("url") or ""
        self.introduction_type = form.get("introductionType")
        self.is_black_boxed = bool(form.get("isBlackBoxed"))

    def __repr__(self):
        return "<Source {}>".format(self.url or self.actor)


class RemoteString(str):
    """一个被截断的超长字符串。

    RDP 对超过一万字符的字符串只随包返回开头一段。本类继承 ``str``，直接当
    字符串用得到的是截断版（末尾带省略号），需要完整内容时用
    :meth:`Debugger.read_string`。
    """

    __slots__ = ("actor", "length")

    def __new__(cls, initial, actor, length):
        obj = super().__new__(cls, initial + "…")
        obj.actor = actor
        obj.length = length
        return obj

    @property
    def truncated(self):
        return True


class RemoteObject(object):
    """暂停现场里的一个 JS 对象。

    RDP 把对象作为「grip」返回：一个带 actor id 的引用，外加一份 ``preview``
    浅层快照。本类把 preview 解码成可读的 Python 值，同时保留 actor 以便
    :meth:`Debugger.expand` 取回完整内容。

    相等性只看类名和已解码的内容，**不看 actor id**。对象 actor 存在暂停池里、
    每次 resume 都会重建，若按 actor 比较，跨单步对比作用域时每个对象都会被
    误判为「变化了」。
    """

    __slots__ = ("class_name", "actor", "value", "truncated", "raw")

    def __init__(self, grip):
        self.raw = grip
        self.actor = grip.get("actor")
        self.class_name = grip.get("class") or grip.get("type") or "Object"
        self.value, self.truncated = _decode_preview(grip)

    def __eq__(self, other):
        if not isinstance(other, RemoteObject):
            return NotImplemented
        return (
            self.class_name == other.class_name
            and self.value == other.value
            and self.truncated == other.truncated
        )

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        return hash((self.class_name, repr(self.value), self.truncated))

    def __repr__(self):
        suffix = " ...(截断)" if self.truncated else ""
        if self.value is None:
            return "<{}>".format(self.class_name)
        return "<{} {!r}{}>".format(self.class_name, self.value, suffix)


def _descriptor_value(descriptor):
    if not isinstance(descriptor, dict):
        return descriptor
    if "value" in descriptor:
        return _grip_to_python(descriptor["value"])
    if "get" in descriptor or "set" in descriptor:
        return "<accessor>"
    return _grip_to_python(descriptor)


def _decode_preview(grip):
    """把 grip 的 preview 解码成 Python 值。

    Returns:
        (value, truncated)。preview 只带前若干项，装不下时 truncated 为 True。
    """
    preview = grip.get("preview")
    if not isinstance(preview, dict):
        return None, bool(grip.get("actor"))

    kind = preview.get("kind")

    if kind == "ArrayLike":
        items = preview.get("items") or []
        length = preview.get("length")
        decoded = [_grip_to_python(item) for item in items]
        return decoded, bool(length is not None and length > len(items))

    if kind == "Object":
        own = preview.get("ownProperties") or {}
        total = preview.get("ownPropertiesLength")
        decoded = {name: _descriptor_value(d) for name, d in own.items()}
        return decoded, bool(total is not None and total > len(own))

    if kind == "MapLike":
        entries = preview.get("entries") or []
        decoded = {}
        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                decoded[repr(_grip_to_python(entry[0]))] = _grip_to_python(entry[1])
        size = preview.get("size")
        return decoded, bool(size is not None and size > len(entries))

    if kind == "DOMNode":
        node = {
            key: preview.get(key)
            for key in ("nodeName", "nodeType", "attributes", "isConnected")
            if preview.get(key) is not None
        }
        return (node or None), False

    # 其余 kind（Error、RegExp、DOMEvent 等）保留 preview 原样，信息量已足够
    return preview, False


def _grip_to_python(grip):
    """把 RDP grip 转成 Python 值；对象包装成 :class:`RemoteObject`。"""
    if not isinstance(grip, dict):
        return grip

    grip_type = grip.get("type")

    if grip_type in ("null", "undefined"):
        return None
    if grip_type in ("Infinity", "-Infinity", "NaN"):
        return float(grip_type.replace("Infinity", "inf"))
    if grip_type == "longString":
        initial = grip.get("initial") or ""
        length = grip.get("length") or 0
        if len(initial) >= length or not grip.get("actor"):
            return initial
        # 保留 actor，调用方可以用 read_string() 取回完整内容
        return RemoteString(initial, grip["actor"], length)
    if grip_type == "symbol":
        return grip.get("name") or "Symbol()"
    if grip_type == "BigInt":
        return grip.get("text")
    if "value" in grip and grip_type != "object":
        return grip["value"]
    if grip.get("actor"):
        return RemoteObject(grip)
    if "value" in grip:
        return grip["value"]
    return grip


def _to_grip_argument(value):
    """把 Python 值转成 RDP 能接受的实参形式。

    远端对象要写成 ``{"type": "object", "actor": ...}``；基本类型直接传字面量。
    服务端把任何「是对象但没有 actor 字段」的实参视为非法 grip，所以 ``None``
    必须原样传 JSON null，不能包成 ``{"type": "null"}``。
    """
    if isinstance(value, RemoteObject):
        return {"type": "object", "actor": value.actor}
    return value


class Breakpoint(object):
    """一个已设置的断点。"""

    __slots__ = ("url", "line", "column", "source_actor", "condition", "log_value")

    def __init__(self, url, line, column, source_actor, condition=None, log_value=None):
        self.url = url
        self.line = line
        self.column = column
        self.source_actor = source_actor
        self.condition = condition
        self.log_value = log_value

    @property
    def is_log_point(self):
        """日志断点命中时只输出 console 消息，不暂停执行。"""
        return bool(self.log_value)

    def __repr__(self):
        kind = "LogPoint" if self.log_value else "Breakpoint"
        suffix = " if {}".format(self.condition) if self.condition else ""
        if self.log_value:
            suffix += " log {}".format(self.log_value)
        return "<{} {}:{}:{}{}>".format(
            kind, self.url, self.line, self.column, suffix
        )


class Frame(object):
    """调用栈中的一帧。"""

    __slots__ = (
        "actor",
        "display_name",
        "type",
        "source_actor",
        "url",
        "line",
        "column",
        "arguments",
        "this_object",
        "oldest",
    )

    def __init__(self, form, source_urls=None):
        self.actor = form.get("actor", "")
        self.display_name = form.get("displayName") or "(anonymous)"
        self.type = form.get("type", "")
        self.arguments = form.get("arguments") or []
        self.this_object = _grip_to_python(form.get("this"))
        self.oldest = bool(form.get("oldest"))

        where = form.get("where") or {}
        self.line = where.get("line")
        self.column = where.get("column")
        # where 只带 source actor，URL 需要用 sources() 的结果换算。
        self.source_actor = where.get("actor") or ""
        self.url = (source_urls or {}).get(self.source_actor) or where.get("url") or ""

    def __repr__(self):
        return "<Frame {} @{}:{}>".format(
            self.display_name, self.url or self.source_actor, self.line
        )


class PausedState(object):
    """一次暂停的快照。"""

    __slots__ = (
        "why",
        "message",
        "exception",
        "event_breakpoint",
        "frame",
        "pause_actor",
        "_raw",
    )

    def __init__(self, packet, source_urls=None):
        self._raw = packet
        why = packet.get("why") or {}
        self.why = why.get("type", "")
        self.message = why.get("message")
        # 异常暂停时带上抛出的值
        self.exception = (
            _grip_to_python(why["exception"]) if "exception" in why else None
        )
        # 事件断点暂停时带上触发的事件 id
        self.event_breakpoint = why.get("breakpoint")
        frame_form = packet.get("frame") or {}
        self.frame = Frame(frame_form, source_urls) if frame_form else None
        self.pause_actor = packet.get("actor")

    @property
    def line(self):
        return self.frame.line if self.frame else None

    @property
    def url(self):
        return self.frame.url if self.frame else ""

    @property
    def condition_failed(self):
        """条件表达式求值抛异常导致的暂停。

        Firefox 在条件出错时选择暂停而不是静默跳过，避免调试者误以为断点没命中。
        具体错误在 :attr:`message`。
        """
        return self.why == "breakpointConditionThrown"

    @property
    def is_exception(self):
        """是否因为 JS 抛异常而暂停；抛出的值在 :attr:`exception`。"""
        return self.why == "exception"

    @property
    def is_event_breakpoint(self):
        """是否因为事件断点而暂停；事件 id 在 :attr:`event_breakpoint`。"""
        return self.why == "eventBreakpoint"

    @property
    def is_xhr(self):
        """是否因为 XHR/fetch 断点而暂停。

        内核用的常量是大写的 ``"XHR"``（``PAUSE_REASONS.XHR``），
        与其他小写驼峰的原因不同。
        """
        return self.why == "XHR"

    @property
    def raw(self):
        """原始 paused 包，便于访问本类未建模的字段。"""
        return self._raw

    def __repr__(self):
        return "<PausedState {} @{}:{}>".format(self.why, self.url, self.line)


class Debugger(object):
    """JS 断点调试器（``page.debugger``）。"""

    def __init__(self, owner):
        self._owner = owner
        self._rdp = None
        self._thread_actor = None
        self._target_actor = None
        self._breakpoints = []
        self._paused = None
        self._paused_queue = Queue()
        self._state_lock = threading.Lock()
        self._on_paused_cb = None
        self._sources_by_url = {}  # {url: [Source, ...]}
        self._source_urls = {}  # {source actor: url}
        self._auto_resume_after = None
        self._watchdog = None
        self._console_actor = None
        self._log_entries = deque(maxlen=_MAX_LOG_ENTRIES)

    # ── 状态 ──

    @property
    def started(self):
        """调试通道是否已连接。"""
        return bool(self._rdp and self._rdp.connected)

    @property
    def paused(self):
        """当前是否处于暂停状态。"""
        with self._state_lock:
            return self._paused is not None

    @property
    def paused_state(self):
        """当前暂停快照；未暂停时为 None。"""
        with self._state_lock:
            return self._paused

    @property
    def breakpoints(self):
        return list(self._breakpoints)

    @property
    def thread_actor(self):
        """底层 thread actor id，便于发送本类未封装的 RDP 请求。"""
        return self._thread_actor

    @property
    def connection(self):
        """底层 :class:`RdpConnection`，便于扩展。"""
        return self._rdp

    # ── 生命周期 ──

    def start(self, port=None, host="127.0.0.1", timeout=20, auto_resume_after=None):
        """连接 devtools server 并 attach 到当前标签页的 thread actor。

        Args:
            port: RDP 端口。默认取 ``opts.enable_debugger()`` 配置的端口。
            host: RDP 主机。
            timeout: 单次 RDP 请求超时（秒）。
            auto_resume_after: 暂停看门狗，单位秒。暂停超过该时长仍未恢复时
                自动 resume。JS 被断住期间所有依赖它的 BiDi 调用都会阻塞，
                无人值守的脚本一旦漏掉 ``resume()`` 就会让页面永久卡死，
                设置本参数可以兜底。``None`` 表示不启用。

        Raises:
            DebuggerError: 无法连接（通常是没有调用 ``opts.enable_debugger()``）
        """
        if self.started:
            return self

        self._auto_resume_after = auto_resume_after
        port = self._resolve_port(port)
        connection = RdpConnection(host=host, port=port, timeout=timeout)
        try:
            connection.connect()
        except OSError as exc:
            raise DebuggerError(
                "无法连接 devtools server {}:{}（{}）。"
                "请在创建页面前调用 opts.enable_debugger()，"
                "它会写入所需 pref 并以 --start-debugger-server 启动 Firefox。".format(
                    host, port, exc
                )
            )

        self._rdp = connection
        try:
            self._attach_current_tab()
        except Exception:
            connection.close()
            self._rdp = None
            raise

        connection.on_event("paused", self._handle_paused, actor=self._thread_actor)
        connection.on_event("resumed", self._handle_resumed, actor=self._thread_actor)

        # 预热源表：paused 事件在 RDP 读线程上处理，不能在那里再发请求，
        # 因此帧的 URL 只能从缓存换算。
        try:
            self.sources()
        except Exception as exc:
            logger.debug("预加载 JS 源列表失败: %s", exc)
        return self

    def stop(self):
        """断开调试通道。若当前处于暂停状态，先恢复执行避免页面卡死。"""
        if not self._rdp:
            return self

        if self.paused:
            try:
                self.resume()
            except Exception:
                pass

        self._rdp.close()
        self._rdp = None
        self._thread_actor = None
        self._target_actor = None
        self._breakpoints = []
        self._sources_by_url = {}
        self._source_urls = {}
        self._console_actor = None
        self._disarm_watchdog()
        with self._state_lock:
            self._paused = None
        return self

    def _resolve_port(self, port):
        if port is not None:
            return int(port)
        options = getattr(getattr(self._owner, "_browser", None), "options", None)
        configured = getattr(options, "debugger_port", None)
        return int(configured or DEFAULT_RDP_PORT)

    def _attach_current_tab(self):
        tabs = self._rdp.request("root", "listTabs").get("tabs") or []
        if not tabs:
            raise DebuggerError("devtools server 未报告任何标签页")

        # BiDi context id 与 RDP actor 无法直接互换，用 URL 对齐当前页面，
        # 匹配不到时回退到 selected 标签页。
        current_url = ""
        try:
            current_url = self._owner.url or ""
        except Exception:
            pass

        tab = None
        if current_url:
            tab = next((t for t in tabs if (t.get("url") or "") == current_url), None)
        if tab is None:
            tab = next((t for t in tabs if t.get("selected")), tabs[0])

        target = self._rdp.request(tab["actor"], "getTarget").get("frame") or {}
        thread_actor = target.get("threadActor")
        if not thread_actor:
            raise DebuggerError("目标未提供 threadActor，无法进行断点调试")

        self._target_actor = target.get("actor")
        self._thread_actor = thread_actor
        self._console_actor = target.get("consoleActor")
        self._rdp.request(thread_actor, "attach", options={})
        if self._console_actor:
            self._rdp.on_event(
                "consoleAPICall", self._handle_console_message, self._console_actor
            )

    def _require_started(self):
        if not self.started:
            raise DebuggerError("调试通道未启动，请先调用 page.debugger.start()")

    # ── 源与断点 ──

    def sources(self, url_contains=None):
        """列出已加载的 JS 源。

        Args:
            url_contains: 只返回 URL 含该子串的源。

        Returns:
            list[Source]
        """
        self._require_started()
        forms = self._rdp.request(self._thread_actor, "sources").get("sources") or []
        result = []
        by_url = {}
        for form in forms:
            source = Source(form)
            if source.url:
                # 一个 URL 可能对应多个源：HTML 页面里的每段内联 <script>
                # 都是独立的源，但共用文档地址。
                by_url.setdefault(source.url, []).append(source)
                self._source_urls[source.actor] = source.url
            if url_contains and url_contains not in source.url:
                continue
            result.append(source)
        self._sources_by_url = by_url
        return result

    def source_text(self, url_or_source):
        """读取源码文本。

        返回的是 JS 引擎正在执行的那一份，因此行号与 ``breakable_lines()``、
        断点位置、调用栈里的 ``line`` 完全对齐。这一点是自行下载 URL 做不到的：
        ``eval`` / ``new Function`` / blob 脚本根本没有可下载的地址，而内联
        ``<script>`` 的行号是相对整个 HTML 文档的。

        Args:
            url_or_source: 源 URL（支持后缀匹配）或 :class:`Source` 对象。

        Returns:
            str: 完整源码。内联脚本返回的是整个 HTML 文档。

        Raises:
            DebuggerError: 源不存在，或返回了无法解码的内容（如 WASM）
        """
        self._require_started()

        if isinstance(url_or_source, Source):
            source = url_or_source
        else:
            # 内联脚本共用文档地址且都返回同一份 HTML，取第一个即可
            source = self._resolve_sources(url_or_source)[0]

        body = self._rdp.request(source.actor, "source").get("source")

        if isinstance(body, str):
            return body

        if isinstance(body, dict) and body.get("type") == "longString":
            # 超过一万字符的源只随包返回开头一段，其余要按需取回
            length = int(body.get("length") or 0)
            initial = body.get("initial") or ""
            if len(initial) >= length:
                return initial
            rest = self._rdp.request(
                body["actor"], "substring", start=len(initial), end=length
            ).get("substring", "")
            return initial + rest

        raise DebuggerError(
            "无法读取 {} 的源码文本，服务端返回: {!r}".format(source.url, body)
        )

    def breakable_lines(self, url):
        """返回某个源可下断点的行号列表。

        URL 对应多段内联脚本时，合并所有脚本的可断点行。
        """
        self._require_started()
        merged = set()
        for source in self._resolve_sources(url):
            merged.update(int(line) for line in self._breakpoint_positions(source))
        return sorted(merged)

    def set_breakpoint(
        self,
        url,
        line,
        column=None,
        condition=None,
        log_value=None,
        log_stacktrace=False,
    ):
        """在指定源的指定行下断点。

        Firefox 只接受落在真实断点位置上的断点，行列不匹配时会被静默忽略。
        因此 ``column`` 省略时会自动查询该行的有效列。

        Args:
            url: 源 URL，需与 ``sources()`` 中的 URL 一致。
            line: 行号（从 1 开始）。
            column: 列号。省略时自动取该行第一个有效列。
            condition: 条件表达式，在断点所在帧中求值，结果为真才暂停。
                表达式抛异常时**仍会暂停**，此时
                ``PausedState.why`` 为 ``'breakpointConditionThrown'``，
                ``PausedState.message`` 是抛出的信息。
            log_value: 日志断点。给了它就**不再暂停**，而是在每次命中时把该
                表达式的求值结果作为 console 消息输出。由于暂停期间无法求
                任意表达式，这是在运行中观察变量的主要手段：配合
                ``page.console`` 即可拿到输出。
            log_stacktrace: 日志断点是否附带调用栈。

        Returns:
            Breakpoint

        Raises:
            DebuggerError: 源不存在或该行不可下断点
        """
        self._require_started()
        sources = self._resolve_sources(url)

        if column is None:
            source, columns_or_lines = self._locate_breakable_column(sources, line)
            if source is None:
                raise DebuggerError(
                    "{} 第 {} 行不可下断点，可用行: {}".format(
                        sources[0].url, line, columns_or_lines
                    )
                )
            column = columns_or_lines
        else:
            source = sources[0]

        options = {}
        if condition:
            options["condition"] = condition
        if log_value:
            options["logValue"] = log_value
            if log_stacktrace:
                options["showStacktrace"] = True

        # 同时传 sourceUrl 与 sourceId：服务端在有 sourceUrl 时会把断点应用到
        # 所有同 URL 的源（正是内联脚本需要的），sourceId 只参与定位键。
        self._rdp.request(
            self._thread_actor,
            "setBreakpoint",
            location={
                "sourceUrl": source.url,
                "sourceId": source.actor,
                "line": int(line),
                "column": int(column),
            },
            options=options,
        )

        breakpoint_ = Breakpoint(
            source.url, int(line), int(column), source.actor, condition, log_value
        )
        self._breakpoints.append(breakpoint_)
        return breakpoint_

    def remove_breakpoint(self, breakpoint_):
        """移除一个断点。"""
        self._require_started()
        self._rdp.request(
            self._thread_actor,
            "removeBreakpoint",
            location={
                "sourceUrl": breakpoint_.url,
                "sourceId": breakpoint_.source_actor,
                "line": breakpoint_.line,
                "column": breakpoint_.column,
            },
        )
        self._breakpoints = [b for b in self._breakpoints if b is not breakpoint_]
        return self

    def clear_breakpoints(self):
        """移除所有由本实例设置的断点。"""
        for breakpoint_ in list(self._breakpoints):
            try:
                self.remove_breakpoint(breakpoint_)
            except DebuggerError as exc:
                logger.warning("移除断点失败 %s: %s", breakpoint_, exc)
        return self

    def _resolve_sources(self, url):
        """返回与该 URL 对应的全部源。

        HTML 页面里每段内联 ``<script>`` 都是独立的源，却共用文档地址，
        因此这里返回列表而不是单个源。
        """
        sources = self._sources_by_url.get(url)
        if sources:
            return sources

        self.sources()
        sources = self._sources_by_url.get(url)
        if sources:
            return sources

        # 允许传入后缀，方便 file:// 这类冗长 URL
        matched_urls = [u for u in self._sources_by_url if u.endswith(url)]
        if len(matched_urls) == 1:
            return self._sources_by_url[matched_urls[0]]
        if len(matched_urls) > 1:
            raise DebuggerError(
                "源 URL '{}' 匹配到多个地址: {}".format(url, sorted(matched_urls))
            )
        raise DebuggerError(
            "未找到源 '{}'，已加载: {}".format(url, sorted(self._sources_by_url))
        )

    def _breakpoint_positions(self, source):
        return (
            self._rdp.request(
                source.actor, "getBreakpointPositionsCompressed", query=None
            ).get("positions")
            or {}
        )

    def _locate_breakable_column(self, sources, line):
        """在同 URL 的多个源里找出包含该行的那个，并返回其首个有效列。"""
        key = str(int(line))
        available = set()
        for source in sources:
            positions = self._breakpoint_positions(source)
            available.update(int(item) for item in positions)
            columns = positions.get(key)
            if columns:
                return source, sorted(columns)[0]
        return None, sorted(available)

    # ── 暂停与恢复 ──

    def pause_on_exceptions(self, enabled=True, ignore_caught=True):
        """异常抛出时自动暂停。

        对自主排查很有用：不必先猜出错位置再下断点，直接让页面跑，
        在抛异常的现场停下，此时调用栈和作用域都还在。

        Args:
            enabled: 是否启用。
            ignore_caught: 忽略会被 ``catch`` 接住的异常。默认忽略，否则
                页面里正常的 try/catch 流程会频繁触发暂停。

        Returns:
            self
        """
        self._require_started()
        self._rdp.request(
            self._thread_actor,
            "reconfigure",
            options={
                "pauseOnExceptions": bool(enabled),
                "ignoreCaughtExceptions": bool(ignore_caught),
            },
        )
        return self

    def pause_on_debugger_statement(self, enabled=True):
        """是否在 JS 的 ``debugger`` 语句处暂停（attach 后默认开启）。"""
        self._require_started()
        self._rdp.request(
            self._thread_actor,
            "reconfigure",
            options={"shouldPauseOnDebuggerStatement": bool(enabled)},
        )
        return self

    def include_async_frames(self, enabled=True):
        """调用栈是否包含异步父帧。

        现代页面大量使用 async/await，同步栈往往只剩一层，看不出是谁发起的。
        打开后 :meth:`frames` 会继续向上追溯异步调用链，帧上的
        ``asyncCause`` 说明它是被什么衔接过来的。
        """
        self._require_started()
        self._rdp.request(
            self._thread_actor,
            "reconfigure",
            options={
                "shouldIncludeSavedFrames": bool(enabled),
                "shouldIncludeAsyncLiveFrames": bool(enabled),
            },
        )
        return self

    def skip_breakpoints(self, skip=True):
        """临时忽略全部断点，不必逐个删除再重建。

        走 ``reconfigure``：thread actor 独立的 ``skipBreakpoints`` 请求在
        Firefox 155 上的 spec 把响应写成了 ``Arg`` 而非 ``RetVal``，服务端会
        直接拒绝，因此不能用。

        Returns:
            self
        """
        self._require_started()
        self._rdp.request(
            self._thread_actor,
            "reconfigure",
            options={"skipBreakpoints": bool(skip)},
        )
        return self

    # ── 黑盒化 ──

    def blackbox(self, url, start_line=None, end_line=None):
        """把某个源标记为黑盒，单步时不再进入其中。

        真实页面里不做黑盒化，``step_into`` 会一头扎进 React / jQuery 之类的
        框架内部，很难走回自己的代码。

        Args:
            url: 源 URL（支持后缀匹配）。同 URL 的多段内联脚本会一并标记。
            start_line: 只黑盒某个行区间时的起始行（含）。
            end_line: 行区间的结束行（含）。

        Returns:
            bool: 当前是否正暂停在该源里

        Raises:
            DebuggerError: 源不存在，或只给了区间的一端
        """
        return self._set_blackbox(url, True, start_line, end_line)

    def unblackbox(self, url, start_line=None, end_line=None):
        """取消黑盒标记，参数含义同 :meth:`blackbox`。"""
        return self._set_blackbox(url, False, start_line, end_line)

    def blackboxed(self):
        """返回当前被标记为黑盒的源 URL 列表。"""
        return sorted(
            {source.url for source in self.sources() if source.is_black_boxed}
        )

    def _set_blackbox(self, url, on, start_line, end_line):
        self._require_started()

        if (start_line is None) != (end_line is None):
            raise DebuggerError("行区间需要同时提供 start_line 和 end_line")

        range_ = None
        if start_line is not None:
            range_ = {
                "start": {"line": int(start_line), "column": 0},
                "end": {"line": int(end_line), "column": 0},
            }

        paused_in_source = False
        for source in self._resolve_sources(url):
            reply = self._rdp.request(
                source.actor, "blackbox" if on else "unblackbox", range=range_
            )
            paused_in_source = paused_in_source or bool(reply.get("pausedInSource"))
        return paused_in_source

    # ── 事件与网络断点 ──

    def available_event_breakpoints(self):
        """列出内核支持的事件断点。

        Returns:
            dict: 分组名到该组事件 id 列表的映射，例如
            ``{"Mouse": ["event.mouse.click", ...], ...}``。这些 id 就是
            :meth:`set_event_breakpoints` 的入参。
        """
        self._require_started()
        groups = (
            self._rdp.request(
                self._thread_actor, "getAvailableEventBreakpoints"
            ).get("value")
            or []
        )
        return {
            group.get("name", ""): [
                event.get("id") for event in group.get("events") or []
            ]
            for group in groups
        }

    def set_event_breakpoints(self, ids):
        """在指定的 DOM 事件被派发时暂停。

        不需要预先知道处理器写在哪个文件哪一行，适合「点击后为什么没反应」
        这类排查。暂停时 ``PausedState.why`` 为 ``'eventBreakpoint'``。

        Args:
            ids: 事件 id 列表，如 ``["event.mouse.click"]``。
                传空列表即清除全部事件断点。

        Returns:
            self
        """
        self._require_started()
        self._rdp.request(
            self._thread_actor, "setActiveEventBreakpoints", ids=list(ids or [])
        )
        return self

    def active_event_breakpoints(self):
        """返回当前生效的事件断点 id 列表。"""
        self._require_started()
        return list(
            self._rdp.request(
                self._thread_actor, "getActiveEventBreakpoints"
            ).get("ids")
            or []
        )

    def set_xhr_breakpoint(self, path="", method="ANY"):
        """在请求 URL 含指定片段时暂停。

        Args:
            path: URL 需要包含的子串。空串匹配所有请求。
            method: 请求方法，``"ANY"`` 表示不限。

        Returns:
            bool: 服务端确认结果
        """
        self._require_started()
        reply = self._rdp.request(
            self._thread_actor, "setXHRBreakpoint", path=str(path), method=str(method)
        )
        return bool(reply.get("value", True))

    def remove_xhr_breakpoint(self, path="", method="ANY"):
        """移除一个 XHR 断点，参数需与设置时一致。"""
        self._require_started()
        reply = self._rdp.request(
            self._thread_actor,
            "removeXHRBreakpoint",
            path=str(path),
            method=str(method),
        )
        return bool(reply.get("value", True))

    def wait_paused(self, timeout=30):
        """等待下一次暂停。

        Returns:
            PausedState，超时返回 None
        """
        self._require_started()
        try:
            return self._paused_queue.get(timeout=timeout)
        except Empty:
            return None

    def on_paused(self, callback):
        """注册暂停回调；``None`` 表示移除。

        回调在 RDP 读线程上执行，不要在其中调用会阻塞的页面操作。
        """
        self._on_paused_cb = callback
        return self

    def pause(self):
        """立即暂停 JS 执行。"""
        self._require_started()
        self._rdp.request(self._thread_actor, "interrupt", when="now")
        return self

    def resume(self):
        """恢复执行。"""
        return self._resume()

    def step_over(self):
        """单步跳过（不进入函数调用）。"""
        return self._resume(limit="over")

    def step_into(self):
        """单步进入函数调用。"""
        return self._resume(limit="into")

    def step_out(self):
        """跳出当前函数。"""
        return self._resume(limit="out")

    def restart_frame(self):
        """回到当前帧的入口重新执行它。

        改完条件想重跑一遍这次调用时很有用，不必刷新页面重来。注意函数此前
        造成的副作用不会被撤销。
        """
        return self._resume(limit="restart")

    def _resume(self, limit=None):
        self._require_started()
        if not self.paused:
            raise DebuggerError("当前未处于暂停状态")

        params = {}
        if limit is not None:
            params["resumeLimit"] = {"type": _STEP_LIMITS[limit]}

        with self._state_lock:
            self._paused = None
        self._disarm_watchdog()
        self._rdp.request(self._thread_actor, "resume", **params)
        return self

    def _handle_paused(self, packet):
        state = PausedState(packet, self._source_urls)
        with self._state_lock:
            self._paused = state
        self._paused_queue.put(state)
        self._arm_watchdog()
        callback = self._on_paused_cb
        if callback:
            try:
                callback(state)
            except Exception as exc:
                logger.warning("暂停回调异常: %s", exc)

    def _handle_resumed(self, _packet):
        with self._state_lock:
            self._paused = None
        self._disarm_watchdog()

    def _arm_watchdog(self):
        """暂停超时未恢复时自动 resume，避免页面被永久断住。"""
        seconds = self._auto_resume_after
        if not seconds:
            return

        self._disarm_watchdog()

        def _fire():
            if not self.paused:
                return
            logger.warning("暂停已超过 %s 秒，自动恢复执行", seconds)
            try:
                self.resume()
            except Exception as exc:
                logger.warning("自动恢复失败: %s", exc)

        timer = threading.Timer(seconds, _fire)
        timer.daemon = True
        self._watchdog = timer
        timer.start()

    def _disarm_watchdog(self):
        timer = self._watchdog
        self._watchdog = None
        if timer is not None:
            timer.cancel()

    # ── 调用栈与作用域 ──

    def frames(self, count=50):
        """读取当前调用栈。

        Returns:
            list[Frame]，最内层在前

        Raises:
            DebuggerError: 当前未暂停
        """
        self._require_started()
        if not self.paused:
            raise DebuggerError("只能在暂停状态下读取调用栈")
        forms = (
            self._rdp.request(
                self._thread_actor, "frames", start=0, count=int(count)
            ).get("frames")
            or []
        )
        return [Frame(form, self._source_urls) for form in forms]

    def scope(self, frame=None, include_parents=False):
        """读取某一帧的作用域变量。

        默认沿作用域链向外读到**函数边界**为止：即当前块作用域加上它所属的
        函数作用域。只读最内层块的话，断点停在 ``const x = ...`` 这类语句上时
        只能看到一个尚未初始化的变量，函数参数和其余局部变量都会缺失。
        全局作用域不包含在内，否则会把整个全局对象倒出来。

        Args:
            frame: :class:`Frame` 或 frame actor id。默认用当前暂停的帧。
            include_parents: 继续向外读闭包与全局作用域。
                内层同名变量优先。

        Returns:
            dict: 变量名到值的映射。无法序列化为原生值的对象保留其 RDP grip。
            当这一帧有 ``this`` 时，它会以 ``'this'`` 为键一并返回。

        Raises:
            DebuggerError: 当前未暂停
        """
        self._require_started()
        state = self.paused_state
        if state is None:
            raise DebuggerError("只能在暂停状态下读取作用域")

        if frame is None:
            if state.frame is None:
                raise DebuggerError("当前暂停状态没有可用的调用帧")
            actor = state.frame.actor
        elif isinstance(frame, Frame):
            actor = frame.actor
        else:
            actor = frame

        environment = self._rdp.request(actor, "getEnvironment")
        chain = []
        node = environment
        while node:
            if not include_parents and node.get("scopeKind") == "global":
                break
            chain.append(node)
            if not include_parents and node.get("type") == "function":
                break
            node = node.get("parent")

        if not chain:
            chain = [environment]

        merged = {}
        # 由外到内合并，内层同名变量覆盖外层
        for node in reversed(chain):
            merged.update(self._bindings_to_dict(node.get("bindings") or {}))

        # this 不在环境绑定里，它挂在帧上。用 'this' 当键不会和局部变量冲突
        # （this 是保留字，不可能被用作变量名）。
        this_object = self._frame_this(frame, state)
        if this_object is not None:
            merged["this"] = this_object
        return merged

    @staticmethod
    def _frame_this(frame, state):
        """取出这一帧的 this；未指定帧时用当前暂停帧。"""
        if isinstance(frame, Frame):
            return frame.this_object
        if frame is None and state.frame is not None:
            return state.frame.this_object
        return None

    @staticmethod
    def _bindings_to_dict(bindings):
        result = {}

        for entry in bindings.get("arguments") or []:
            if isinstance(entry, dict):
                for name, descriptor in entry.items():
                    result[name] = _descriptor_value(descriptor)

        variables = bindings.get("variables") or {}
        for name, descriptor in variables.items():
            result[name] = _descriptor_value(descriptor)
        return result

    def expand(self, obj, depth=1, max_items=100):
        """完整取回一个对象的属性，突破 preview 的浅层上限。

        ``scope()`` 返回的 :class:`RemoteObject` 只带 preview 快照，Firefox 的
        preview 最多给十项。需要看全部内容或更深层级时用本方法。

        Args:
            obj: :class:`RemoteObject`，或直接给对象 actor id。
            depth: 递归展开层数。1 表示只展开这一层，嵌套对象仍为 RemoteObject。
            max_items: 单个对象最多取回多少个属性。

        Returns:
            dict: 属性名到值的映射；数组类对象返回 list。

        Raises:
            DebuggerError: 当前未暂停，或该值不是可展开的对象
        """
        self._require_started()
        if not self.paused:
            raise DebuggerError("只能在暂停状态下展开对象")

        actor = obj.actor if isinstance(obj, RemoteObject) else obj
        if not actor:
            raise DebuggerError("该值不是可展开的对象: {!r}".format(obj))

        reply = self._rdp.request(actor, "prototypeAndProperties")
        own = reply.get("ownProperties") or {}

        if len(own) > max_items:
            logger.warning(
                "对象有 %d 个属性，只展开了前 %d 个；"
                "按名字取用 get_property()，或调大 max_items",
                len(own),
                max_items,
            )

        result = {}
        for index, (name, descriptor) in enumerate(own.items()):
            if index >= max_items:
                break
            value = _descriptor_value(descriptor)
            if depth > 1 and isinstance(value, RemoteObject) and value.actor:
                value = self.expand(value, depth=depth - 1, max_items=max_items)
            result[name] = value

        # 数组类对象的属性名是 "0"/"1"/…，另外还有一个 length，还原成 list 更直观
        numeric = {name for name in result if name.isdigit()}
        if numeric and set(result) <= numeric | {"length"}:
            return [result[name] for name in sorted(numeric, key=int)]
        return result

    def get_property(self, obj, name, own_only=False):
        """按名字取对象的单个属性。

        比 :meth:`expand` 更适合大对象：``window`` 有上千个属性，全量展开既慢
        又会被 ``max_items`` 截断。

        按 JS 的正常语义解析：自有属性找不到时继续沿原型链找，所以类的方法
        （它们挂在原型上，不是实例的自有属性）也能取到。

        Args:
            obj: :class:`RemoteObject` 或对象 actor id。
            name: 属性名。
            own_only: 只查自有属性，不走原型链。

        Returns:
            属性值；不存在时返回 None。访问器属性返回 ``'<accessor>'``，
            要拿真实值用 :meth:`invoke_getter`。

        Raises:
            DebuggerError: 当前未暂停，或该值不是可展开的对象
        """
        self._require_started()
        if not self.paused:
            raise DebuggerError("只能在暂停状态下读取对象属性")

        actor = obj.actor if isinstance(obj, RemoteObject) else obj
        if not actor:
            raise DebuggerError("该值不是可展开的对象: {!r}".format(obj))

        # 原型链一般只有几层，设个上限防止环形原型导致死循环
        for _hop in range(_MAX_PROTOTYPE_HOPS):
            descriptor = self._rdp.request(actor, "property", name=name).get(
                "descriptor"
            )
            if descriptor is not None:
                return _descriptor_value(descriptor)
            if own_only:
                return None

            proto = self._rdp.request(actor, "prototype").get("prototype")
            actor = proto.get("actor") if isinstance(proto, dict) else None
            if not actor:
                return None
        return None

    def global_object(self, obj=None):
        """取全局对象（页面里的 ``window``）。

        全局作用域的绑定来自全局对象本身，不以变量形式出现在环境链里，所以
        ``scope(include_parents=True)`` 里也看不到 ``window``。

        Args:
            obj: 从哪个对象所属的全局取。默认用当前帧的 ``this``，取不到时
                用作用域里任意一个远端对象——同一个页面里它们的全局是同一个。

        Returns:
            RemoteObject: 全局对象；取不到时返回 None。

        Raises:
            DebuggerError: 当前未暂停，或找不到可用来定位全局的对象
        """
        self._require_started()
        if not self.paused:
            raise DebuggerError("只能在暂停状态下读取全局对象")

        if obj is None:
            obj = self._any_object_in_scope()
        actor = obj.actor if isinstance(obj, RemoteObject) else obj
        if not actor:
            raise DebuggerError("找不到可用来定位全局的对象，请显式传入 obj")

        return _grip_to_python(self._rdp.request(actor, "global").get("global"))

    def _any_object_in_scope(self):
        """随便找一个远端对象，用来定位它所属的全局。"""
        state = self.paused_state
        if state is not None and state.frame is not None:
            if isinstance(state.frame.this_object, RemoteObject):
                return state.frame.this_object
        for value in self.scope().values():
            if isinstance(value, RemoteObject):
                return value
        return None

    def constructor_name(self, obj):
        """取对象的构造函数名，例如自定义类实例返回 ``"Cart"``。

        :attr:`RemoteObject.class_name` 反映的是 JS 的 ``[[Class]]`` 内部值，
        对普通类实例统一是 ``"Object"``。真正的类名要从原型链上的
        ``constructor`` 派生，因此单独作为一个按需方法（多花一次请求）。

        Args:
            obj: :class:`RemoteObject` 或对象 actor id。

        Returns:
            构造函数名；取不到时返回 :attr:`RemoteObject.class_name` 兜底。

        Raises:
            DebuggerError: 当前未暂停，或该值不是可展开的对象
        """
        self._require_started()
        if not self.paused:
            raise DebuggerError("只能在暂停状态下读取构造函数名")

        actor = obj.actor if isinstance(obj, RemoteObject) else obj
        if not actor:
            raise DebuggerError("该值不是可展开的对象: {!r}".format(obj))

        fallback = obj.class_name if isinstance(obj, RemoteObject) else None

        proto = self._rdp.request(actor, "prototypeAndProperties").get("prototype")
        if not isinstance(proto, dict) or not proto.get("actor"):
            return fallback

        descriptor = self._rdp.request(
            proto["actor"], "property", name="constructor"
        ).get("descriptor")
        ctor = (descriptor or {}).get("value") or {}
        # 函数 grip 带 name / displayName
        return ctor.get("name") or ctor.get("displayName") or fallback

    def logs(self, clear=True):
        """取回日志断点产生的输出。

        日志断点的消息是由调试器注入 DevTools 控制台管道的，**不经过真实的
        console API**，所以 ``page.console`` 看不到它们，只能从这里读。

        Args:
            clear: 取回后是否清空缓冲。

        Returns:
            list[dict]: 每条含 ``values``（表达式求值结果的列表）、``url``、
            ``line``、``is_error``（表达式求值抛异常时为 True）、
            ``stacktrace``（仅在 ``log_stacktrace=True`` 时非空）。
        """
        with self._state_lock:
            entries = list(self._log_entries)
            if clear:
                self._log_entries.clear()
        return entries

    def wait_logs(self, count=1, timeout=10):
        """等待日志断点产生至少 ``count`` 条输出后取回。

        日志断点不暂停执行，消息是异步到达的，直接调用 :meth:`logs` 可能取到空。

        Returns:
            list[dict]: 超时则返回已收到的部分。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._state_lock:
                enough = len(self._log_entries) >= count
            if enough:
                break
            time.sleep(0.05)
        return self.logs()

    def _handle_console_message(self, packet):
        """收集日志断点的输出，忽略页面自身的 console 调用。"""
        message = packet.get("message") or {}
        level = message.get("level") or ""
        if not level.startswith("logPoint"):
            return

        entry = {
            "values": [_grip_to_python(arg) for arg in (message.get("arguments") or [])],
            "url": message.get("filename") or "",
            "line": message.get("lineNumber"),
            "is_error": level.endswith("Error"),
            "stacktrace": message.get("stacktrace") or [],
        }
        with self._state_lock:
            self._log_entries.append(entry)

    def read_string(self, value):
        """取回被截断的超长字符串的完整内容。

        Args:
            value: :class:`RemoteString`，或普通 ``str``（原样返回）。

        Returns:
            str
        """
        if not isinstance(value, RemoteString):
            return value

        self._require_started()
        rest = self._rdp.request(
            value.actor, "substring", start=len(value) - 1, end=value.length
        ).get("substring", "")
        return str(value)[:-1] + rest

    def entries(self, obj, max_items=1000):
        """读取 ``Map`` / ``Set`` / ``WeakMap`` 等的完整条目。

        这类容器的内容不是普通属性，``expand()`` 看不到，preview 也只带前十项。

        Args:
            obj: :class:`RemoteObject` 或对象 actor id。
            max_items: 最多取回多少条。

        Returns:
            ``Map`` 返回 ``{键: 值}``；``Set`` 返回值的列表。

        Raises:
            DebuggerError: 当前未暂停，或该对象不支持条目枚举
        """
        actor = self._object_actor(obj, "读取条目")

        # 响应把迭代器 actor 的 form 嵌在 iterator 键下：{type, actor, count}
        iterator = self._rdp.request(actor, "enumEntries").get("iterator") or {}
        if not iterator.get("actor"):
            raise DebuggerError("该对象没有可枚举的条目: {!r}".format(obj))

        count = min(int(iterator.get("count") or 0), int(max_items))
        if not count:
            return {}
        own = (
            self._rdp.request(
                iterator["actor"], "slice", start=0, count=count
            ).get("ownProperties")
            or {}
        )

        # Map 的条目是 {type: "mapEntry", preview: {key, value}}，Set 是裸 grip
        pairs = {}
        values = []
        for _index, descriptor in sorted(own.items(), key=lambda kv: int(kv[0])):
            raw = (descriptor or {}).get("value")
            if isinstance(raw, dict) and raw.get("type") == "mapEntry":
                preview = raw.get("preview") or {}
                key = _grip_to_python(preview.get("key"))
                pairs[key if isinstance(key, str) else repr(key)] = _grip_to_python(
                    preview.get("value")
                )
            else:
                values.append(_grip_to_python(raw))
        return pairs if pairs else values

    def call(self, func, args=None, this=None):
        """远程调用一个已存在的函数对象。

        暂停期间无法求任意表达式（协议限制），但可以调用页面里已有的函数。
        例如从作用域取到 ``JSON.stringify`` 再调用它，或直接调业务函数复现问题。

        Args:
            func: 指向函数的 :class:`RemoteObject` 或 actor id。
            args: 参数列表。基本类型直接给值，要传远端对象则给
                :class:`RemoteObject`。
            this: 调用时的 ``this``，同样支持 :class:`RemoteObject`。

        Returns:
            返回值（已解码）。

        Raises:
            DebuggerError: 当前未暂停、目标不是函数，或调用过程中抛异常
        """
        actor = self._object_actor(func, "调用函数")

        reply = self._rdp.request(
            actor,
            "apply",
            context=_to_grip_argument(this),
            arguments=[_to_grip_argument(a) for a in (args or [])],
        )
        return self._unwrap_completion(reply.get("value"), "函数调用")

    def invoke_getter(self, obj, name):
        """调用属性的 getter 并返回其结果。

        ``scope()`` / ``expand()`` 对访问器属性只会显示 ``'<accessor>'``，因为
        读取它意味着执行页面代码，不能默认进行。需要真实值时用本方法。

        Raises:
            DebuggerError: 当前未暂停，或 getter 执行时抛异常
        """
        actor = self._object_actor(obj, "调用 getter")

        reply = self._rdp.request(actor, "propertyValue", name=name, receiverId=None)
        return self._unwrap_completion(reply.get("value"), "getter {}".format(name))

    def promise_state(self, obj):
        """读取 Promise 的状态。

        Returns:
            dict: 含 ``state``（``pending`` / ``fulfilled`` / ``rejected``）、
            ``value`` 或 ``reason``、``creation_timestamp``、``time_to_settle``。

        Raises:
            DebuggerError: 当前未暂停，或该对象不是 Promise
        """
        actor = self._object_actor(obj, "读取 Promise 状态")

        # 结果嵌在同名键下
        reply = self._rdp.request(actor, "promiseState").get("promiseState") or {}
        if not reply.get("state"):
            raise DebuggerError("该对象不是 Promise: {!r}".format(obj))
        return {
            "state": reply.get("state"),
            "value": _grip_to_python(reply.get("value"))
            if reply.get("value") is not None
            else None,
            "reason": _grip_to_python(reply.get("reason"))
            if reply.get("reason") is not None
            else None,
            "creation_timestamp": reply.get("creationTimestamp"),
            "time_to_settle": reply.get("timeToSettle"),
        }

    def watch_property(self, obj, name, on="set", label=None):
        """在属性被读或被写时暂停。

        CDP 没有对应能力。适合排查「这个值是被谁改掉的」。

        目标属性必须**已经存在**、可配置、且是数据属性（不是 getter/setter）；
        不满足时服务端会静默忽略（该请求是 oneway，没有回执可判断）。

        Args:
            obj: :class:`RemoteObject` 或对象 actor id。
            name: 属性名。
            on: ``'set'`` 写入时、``'get'`` 读取时、``'getorset'`` 两者皆可。
            label: 暂停时显示的标签，默认用属性名。

        Returns:
            self
        """
        if on not in ("get", "set", "getorset"):
            raise DebuggerError("on 必须是 'get' / 'set' / 'getorset'")
        actor = self._object_actor(obj, "设置监视点")

        # addWatchpoint 是 oneway，服务端不回包，用 request() 会一直等到超时
        self._rdp.send(
            {
                "to": actor,
                "type": "addWatchpoint",
                "property": name,
                "label": label or name,
                "watchpointType": on,
            }
        )
        return self

    def unwatch_property(self, obj, name=None):
        """移除监视点；``name`` 省略时移除该对象上的全部监视点。"""
        actor = self._object_actor(obj, "移除监视点")

        if name is None:
            self._rdp.send({"to": actor, "type": "removeWatchpoints"})
        else:
            self._rdp.send(
                {"to": actor, "type": "removeWatchpoint", "property": name}
            )
        return self

    def _object_actor(self, obj, what):
        """校验暂停状态并取出对象 actor id。"""
        self._require_started()
        if not self.paused:
            raise DebuggerError("只能在暂停状态下{}".format(what))
        actor = obj.actor if isinstance(obj, RemoteObject) else obj
        if not actor:
            raise DebuggerError("该值不是可操作的对象: {!r}".format(obj))
        return actor

    def _unwrap_completion(self, completion, what):
        """展开 ``{return|throw}`` 形式的执行结果。"""
        if not isinstance(completion, dict):
            return None
        if "throw" in completion and completion["throw"] is not None:
            thrown = _grip_to_python(completion["throw"])
            raise DebuggerError("{}抛出异常: {!r}".format(what, thrown))
        return _grip_to_python(completion.get("return"))
