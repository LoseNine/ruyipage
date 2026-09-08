# -*- coding: utf-8 -*-
"""Debugger 单元：断点列自动解析、暂停状态机、作用域解析。"""

import threading
import time

import pytest

from ruyipage._configs.firefox_options import FirefoxOptions
from ruyipage._units.debugger import (
    _MAX_LOG_ENTRIES,
    Debugger,
    Frame,
    PausedState,
    RemoteObject,
    RemoteString,
    Source,
)
from ruyipage.errors import DebuggerError


class FakeRdp(object):
    """记录请求并按 (actor, type) 返回预设应答。"""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}
        self.connected = True
        self.events = {}

    def request(self, actor, type_, timeout=None, **params):
        self.calls.append((actor, type_, params))
        key = (actor, type_)
        if key in self.replies:
            return self.replies[key]
        if type_ in self.replies:
            return self.replies[type_]
        return {"from": actor}

    def on_event(self, event_type, callback, actor=None):
        self.events[(actor, event_type)] = callback

    def close(self):
        self.connected = False


def _debugger(replies=None, thread_actor="thread1"):
    debugger = Debugger(owner=None)
    debugger._rdp = FakeRdp(replies)
    debugger._thread_actor = thread_actor
    # 真实服务端的 where 只带 source actor，URL 靠这张表换算
    debugger._source_urls = {"src1": "https://x.test/app.js"}
    return debugger


PAUSED_PACKET = {
    "from": "thread1",
    "type": "paused",
    "actor": "pause9",
    "why": {"type": "breakpoint", "actors": ["bp1"]},
    "frame": {
        "actor": "frame7",
        "type": "call",
        "displayName": "computeTotal",
        # where 里是 source actor，不是 url —— 这是真实的包结构
        "where": {"actor": "src1", "line": 6, "column": 16},
        "arguments": [25, 4],
        "oldest": False,
    },
}


# ── options 接线 ──


def test_enable_debugger_sets_prefs_and_bare_flag():
    opts = FirefoxOptions().enable_debugger(port=6001)

    assert opts.debugger_port == 6001
    prefs = opts.preferences
    assert prefs["devtools.debugger.remote-enabled"] is True
    assert prefs["devtools.chrome.enabled"] is True
    assert prefs["devtools.debugger.prompt-connection"] is False
    assert prefs["devtools.debugger.remote-port"] == 6001
    # 必须是裸标志：带值形式要求独立 argv token，本类拼的是 --flag=value
    assert "--start-debugger-server" in opts.arguments
    assert not any(a.startswith("--start-debugger-server=") for a in opts.arguments)


def test_enable_debugger_defaults_to_port_6000():
    assert FirefoxOptions().enable_debugger().debugger_port == 6000


def test_disable_debugger_removes_prefs_and_flag():
    opts = FirefoxOptions().enable_debugger(port=6002)
    opts.enable_debugger(False)

    assert opts.debugger_port is None
    assert "--start-debugger-server" not in opts.arguments
    assert "devtools.debugger.remote-enabled" not in opts.preferences


def test_debugger_port_is_none_until_enabled():
    assert FirefoxOptions().debugger_port is None


# ── 断点列自动解析 ──


def test_set_breakpoint_resolves_column_from_breakable_positions():
    """列号必须取自服务端的有效断点位置，否则断点会被静默忽略。"""
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "getBreakpointPositionsCompressed"): {
                "positions": {"3": [2], "6": [16, 24], "7": [2]}
            },
        }
    )

    breakpoint_ = debugger.set_breakpoint("https://x.test/app.js", 6)

    assert breakpoint_.column == 16
    location = next(
        params["location"]
        for actor, type_, params in debugger._rdp.calls
        if type_ == "setBreakpoint"
    )
    assert location == {
        "sourceUrl": "https://x.test/app.js",
        "sourceId": "src1",
        "line": 6,
        "column": 16,
    }


def test_conditional_breakpoint_sends_condition_in_options():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "getBreakpointPositionsCompressed"): {"positions": {"6": [16]}},
        }
    )

    breakpoint_ = debugger.set_breakpoint(
        "https://x.test/app.js", 6, condition="quantity > 3"
    )

    assert breakpoint_.condition == "quantity > 3"
    options = next(
        params["options"]
        for _actor, type_, params in debugger._rdp.calls
        if type_ == "setBreakpoint"
    )
    assert options == {"condition": "quantity > 3"}


def test_unconditional_breakpoint_sends_empty_options():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "getBreakpointPositionsCompressed"): {"positions": {"6": [16]}},
        }
    )

    breakpoint_ = debugger.set_breakpoint("https://x.test/app.js", 6)

    assert breakpoint_.condition is None
    options = next(
        params["options"]
        for _actor, type_, params in debugger._rdp.calls
        if type_ == "setBreakpoint"
    )
    assert options == {}


def test_condition_that_throws_is_surfaced_on_paused_state():
    """条件表达式出错时 Firefox 仍会暂停，需要能区分这种暂停。"""
    debugger = _debugger()

    debugger._handle_paused(
        {
            "from": "thread1",
            "type": "paused",
            "why": {
                "type": "breakpointConditionThrown",
                "message": "ReferenceError: nope is not defined",
            },
            "frame": {"actor": "frame7", "where": {"actor": "src1", "line": 6}},
        }
    )
    state = debugger.wait_paused(timeout=1)

    assert state.condition_failed is True
    assert "ReferenceError" in state.message


def test_normal_breakpoint_pause_is_not_flagged_as_condition_failure():
    debugger = _debugger()

    debugger._handle_paused(PAUSED_PACKET)
    state = debugger.wait_paused(timeout=1)

    assert state.condition_failed is False
    assert state.message is None


def test_source_text_returns_inline_string_as_is():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "source"): {
                "contentType": "text/javascript",
                "source": "line1\nline2\nline3",
            },
        }
    )

    assert debugger.source_text("https://x.test/app.js") == "line1\nline2\nline3"


def test_source_text_stitches_long_string_from_initial_plus_substring():
    """超过一万字符的源只随包带回开头，其余要按需取回。"""
    initial = "A" * 1000
    tail = "B" * 500
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/big.js"}]
            },
            ("src1", "source"): {
                "source": {
                    "type": "longString",
                    "actor": "lstr1",
                    "length": 1500,
                    "initial": initial,
                }
            },
            ("lstr1", "substring"): {"substring": tail},
        }
    )

    text = debugger.source_text("https://x.test/big.js")

    assert text == initial + tail
    start_end = next(
        (params["start"], params["end"])
        for _actor, type_, params in debugger._rdp.calls
        if type_ == "substring"
    )
    assert start_end == (1000, 1500)


def test_source_text_skips_substring_when_initial_is_complete():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "source"): {
                "source": {
                    "type": "longString",
                    "actor": "lstr1",
                    "length": 5,
                    "initial": "short",
                }
            },
        }
    )

    assert debugger.source_text("https://x.test/app.js") == "short"
    assert not any(t == "substring" for _a, t, _p in debugger._rdp.calls)


def test_source_text_accepts_a_source_object():
    debugger = _debugger({("src9", "source"): {"source": "body"}})

    assert debugger.source_text(Source({"actor": "src9", "url": "u"})) == "body"


def test_source_text_reports_undecodable_payload():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/m.wasm"}]
            },
            ("src1", "source"): {"source": {"actor": "buf1", "length": 8}},
        }
    )

    with pytest.raises(DebuggerError, match="无法读取"):
        debugger.source_text("https://x.test/m.wasm")


def test_multiple_inline_scripts_share_one_url_and_are_all_indexed():
    """HTML 里每段内联 <script> 是独立的源，却共用文档地址。"""
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [
                    {"actor": "inline1", "url": "https://x.test/page.html"},
                    {"actor": "inline2", "url": "https://x.test/page.html"},
                    {"actor": "ext1", "url": "https://x.test/app.js"},
                ]
            },
            ("inline1", "getBreakpointPositionsCompressed"): {
                "positions": {"7": [4], "8": [4]}
            },
            ("inline2", "getBreakpointPositionsCompressed"): {
                "positions": {"16": [4], "17": [8]}
            },
        }
    )

    lines = debugger.breakable_lines("https://x.test/page.html")

    # 两段脚本的可断点行都要出现，而不是只有最后一段
    assert lines == [7, 8, 16, 17]


def test_breakpoint_on_second_inline_script_resolves_its_own_column():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [
                    {"actor": "inline1", "url": "https://x.test/page.html"},
                    {"actor": "inline2", "url": "https://x.test/page.html"},
                ]
            },
            ("inline1", "getBreakpointPositionsCompressed"): {"positions": {"7": [4]}},
            ("inline2", "getBreakpointPositionsCompressed"): {"positions": {"16": [8]}},
        }
    )

    breakpoint_ = debugger.set_breakpoint("https://x.test/page.html", 16)

    assert breakpoint_.column == 8
    location = next(
        params["location"]
        for _actor, type_, params in debugger._rdp.calls
        if type_ == "setBreakpoint"
    )
    # sourceId 必须指向真正包含该行的那段脚本
    assert location["sourceId"] == "inline2"
    assert location["sourceUrl"] == "https://x.test/page.html"


def test_unbreakable_line_error_lists_lines_from_every_inline_script():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [
                    {"actor": "inline1", "url": "https://x.test/page.html"},
                    {"actor": "inline2", "url": "https://x.test/page.html"},
                ]
            },
            ("inline1", "getBreakpointPositionsCompressed"): {"positions": {"7": [4]}},
            ("inline2", "getBreakpointPositionsCompressed"): {"positions": {"16": [8]}},
        }
    )

    with pytest.raises(DebuggerError) as excinfo:
        debugger.set_breakpoint("https://x.test/page.html", 99)

    message = str(excinfo.value)
    assert "7" in message and "16" in message


def test_ambiguous_url_suffix_reports_all_candidates():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [
                    {"actor": "a", "url": "https://x.test/a/app.js"},
                    {"actor": "b", "url": "https://x.test/b/app.js"},
                ]
            }
        }
    )

    with pytest.raises(DebuggerError, match="匹配到多个地址"):
        debugger.set_breakpoint("app.js", 1)


def test_explicit_column_skips_position_lookup():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            }
        }
    )

    debugger.set_breakpoint("https://x.test/app.js", 6, column=99)

    assert not any(
        type_ == "getBreakpointPositionsCompressed"
        for _actor, type_, _params in debugger._rdp.calls
    )


def test_set_breakpoint_on_unbreakable_line_reports_available_lines():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "getBreakpointPositionsCompressed"): {
                "positions": {"3": [2], "7": [2]}
            },
        }
    )

    with pytest.raises(DebuggerError) as excinfo:
        debugger.set_breakpoint("https://x.test/app.js", 6)

    assert "不可下断点" in str(excinfo.value)
    assert "[3, 7]" in str(excinfo.value)


def test_unknown_source_raises_with_loaded_list():
    debugger = _debugger({("thread1", "sources"): {"sources": []}})

    with pytest.raises(DebuggerError, match="未找到源"):
        debugger.set_breakpoint("https://x.test/missing.js", 1)


def test_source_can_be_matched_by_url_suffix():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "file:///tmp/deep/app.js"}]
            },
            ("src1", "getBreakpointPositionsCompressed"): {"positions": {"1": [0]}},
        }
    )

    breakpoint_ = debugger.set_breakpoint("app.js", 1)

    assert breakpoint_.url == "file:///tmp/deep/app.js"


# ── 暂停状态机 ──


def test_paused_event_updates_state_and_queue():
    debugger = _debugger()

    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.paused is True
    state = debugger.wait_paused(timeout=1)
    assert isinstance(state, PausedState)
    assert state.why == "breakpoint"
    assert state.line == 6
    assert state.frame.display_name == "computeTotal"
    assert state.frame.arguments == [25, 4]
    assert state.raw is PAUSED_PACKET
    # source actor 必须被换算成 URL
    assert state.frame.source_actor == "src1"
    assert state.url == "https://x.test/app.js"


def test_frame_url_is_empty_when_source_table_is_cold():
    """源表未预热时不能凭空造出 URL，但行号仍可用。"""
    debugger = _debugger()
    debugger._source_urls = {}

    debugger._handle_paused(PAUSED_PACKET)
    state = debugger.wait_paused(timeout=1)

    assert state.url == ""
    assert state.line == 6


def test_wait_paused_returns_none_on_timeout():
    debugger = _debugger()

    assert debugger.wait_paused(timeout=0.05) is None


def test_resume_requires_paused_state():
    debugger = _debugger()

    with pytest.raises(DebuggerError, match="未处于暂停状态"):
        debugger.resume()


def test_step_variants_send_expected_resume_limits():
    for method, expected in (
        ("step_over", "next"),
        ("step_into", "step"),
        ("step_out", "finish"),
    ):
        debugger = _debugger()
        debugger._handle_paused(PAUSED_PACKET)

        getattr(debugger, method)()

        actor, type_, params = debugger._rdp.calls[-1]
        assert (actor, type_) == ("thread1", "resume")
        assert params["resumeLimit"] == {"type": expected}
        assert debugger.paused is False


def test_plain_resume_sends_no_limit():
    debugger = _debugger()
    debugger._handle_paused(PAUSED_PACKET)

    debugger.resume()

    _actor, _type, params = debugger._rdp.calls[-1]
    assert "resumeLimit" not in params


def test_pause_uses_immediate_interrupt():
    debugger = _debugger()

    debugger.pause()

    assert debugger._rdp.calls[-1] == ("thread1", "interrupt", {"when": "now"})


def test_resumed_event_clears_state():
    debugger = _debugger()
    debugger._handle_paused(PAUSED_PACKET)

    debugger._handle_resumed({"from": "thread1", "type": "resumed"})

    assert debugger.paused is False


def test_on_paused_callback_receives_state():
    debugger = _debugger()
    seen = []
    debugger.on_paused(seen.append)

    debugger._handle_paused(PAUSED_PACKET)

    assert len(seen) == 1
    assert seen[0].why == "breakpoint"


def test_callback_exception_does_not_break_state_tracking():
    debugger = _debugger()

    def boom(_state):
        raise RuntimeError("callback failed")

    debugger.on_paused(boom)
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.paused is True


# ── 调用栈与作用域 ──


def test_frames_requires_paused():
    debugger = _debugger()

    with pytest.raises(DebuggerError, match="暂停状态"):
        debugger.frames()


def test_frames_are_parsed_innermost_first():
    debugger = _debugger(
        {
            ("thread1", "frames"): {
                "frames": [
                    {
                        "actor": "f1",
                        "displayName": "inner",
                        "where": {"actor": "src1", "line": 3},
                    },
                    {
                        "actor": "f2",
                        "where": {"actor": "src1", "line": 9},
                        "oldest": True,
                    },
                ]
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    frames = debugger.frames()

    assert [f.display_name for f in frames] == ["inner", "(anonymous)"]
    assert frames[0].line == 3
    assert frames[0].url == "https://x.test/app.js"
    assert frames[1].oldest is True


def test_scope_walks_out_to_the_function_boundary_by_default():
    """真实结构：const 在内层 block，函数参数在外层 function 环境。

    只读最内层会得到一个尚未初始化的变量，参数全部丢失。
    """
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "type": "block",
                "scopeKind": "function lexical",
                "bindings": {
                    "arguments": [],
                    "variables": {
                        "tripled": {"value": {"type": "null", "uninitialized": True}}
                    },
                },
                "parent": {
                    "type": "function",
                    "scopeKind": "function",
                    "bindings": {
                        "arguments": [{"b": {"value": 5}}],
                        "variables": {},
                    },
                    "parent": {
                        "type": "block",
                        "scopeKind": "global",
                        "bindings": {
                            "variables": {"globalNoise": {"value": {"value": 1}}}
                        },
                    },
                },
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    scope = debugger.scope()

    assert scope["b"] == 5, "函数参数必须可见"
    assert "tripled" in scope
    assert "globalNoise" not in scope, "默认不应越过全局作用域"


def test_scope_include_parents_reaches_global():
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "type": "block",
                "bindings": {"variables": {"x": {"value": {"value": "inner"}}}},
                "parent": {
                    "type": "function",
                    "bindings": {"variables": {"y": {"value": {"value": "fn"}}}},
                    "parent": {
                        "type": "block",
                        "scopeKind": "global",
                        "bindings": {"variables": {"g": {"value": {"value": "global"}}}},
                    },
                },
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.scope() == {"x": "inner", "y": "fn"}
    assert debugger.scope(include_parents=True) == {
        "x": "inner",
        "y": "fn",
        "g": "global",
    }


def test_inner_scope_shadows_outer():
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "type": "block",
                "bindings": {"variables": {"x": {"value": {"value": "inner"}}}},
                "parent": {
                    "type": "function",
                    "bindings": {
                        "variables": {
                            "x": {"value": {"value": "outer"}},
                            "only": {"value": {"value": "outer-only"}},
                        }
                    },
                },
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.scope() == {"x": "inner", "only": "outer-only"}


def test_scope_reads_variables_and_arguments_of_current_frame():
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "type": "function",
                "bindings": {
                    "arguments": [
                        {"price": {"value": {"type": "number", "value": 25}}},
                        {"quantity": {"value": {"type": "number", "value": 4}}},
                    ],
                    "variables": {
                        "subtotal": {"value": {"type": "number", "value": 100}},
                        "label": {"value": {"type": "string", "value": "hi"}},
                        "nothing": {"value": {"type": "undefined"}},
                    },
                },
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    scope = debugger.scope()

    assert scope == {
        "price": 25,
        "quantity": 4,
        "subtotal": 100,
        "label": "hi",
        "nothing": None,
    }


def test_scope_can_walk_parent_chain_with_inner_shadowing_outer():
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "type": "function",
                "bindings": {"variables": {"x": {"value": {"value": "inner"}}}},
                "parent": {
                    "type": "function",
                    "bindings": {
                        "variables": {
                            "x": {"value": {"value": "outer"}},
                            "y": {"value": {"value": "only-outer"}},
                        }
                    },
                },
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    # 默认停在第一个 function 边界
    assert debugger.scope() == {"x": "inner"}
    # 显式要求时继续读闭包
    assert debugger.scope(include_parents=True) == {"x": "inner", "y": "only-outer"}


def test_scope_wraps_objects_so_they_are_readable():
    """对象不再是原始 grip，而是带解码内容的 RemoteObject。"""
    grip = {
        "type": "object",
        "class": "Array",
        "actor": "obj3",
        "preview": {"kind": "ArrayLike", "length": 3, "items": [1, 2, 3]},
    }
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "bindings": {"variables": {"items": {"value": grip}}}
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    items = debugger.scope()["items"]

    assert isinstance(items, RemoteObject)
    assert items.class_name == "Array"
    assert items.value == [1, 2, 3]
    assert items.truncated is False
    assert items.actor == "obj3"
    assert repr(items) == "<Array [1, 2, 3]>"


def test_object_without_preview_still_reports_its_class():
    grip = {"type": "object", "class": "Window", "actor": "obj9"}
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "bindings": {"variables": {"w": {"value": grip}}}
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    w = debugger.scope()["w"]

    assert w.class_name == "Window"
    assert w.value is None
    assert w.truncated is True  # 内容未知，需要 expand()


def test_objects_compare_by_content_not_actor_id():
    """暂停池每次 resume 都重建，actor id 会变；比较必须只看内容。

    否则「单步后哪些变量变了」这类对比会把所有对象都误报成变化。
    """
    first = RemoteObject(
        {
            "class": "Array",
            "actor": "obj10",
            "preview": {"kind": "ArrayLike", "length": 2, "items": [1, 2]},
        }
    )
    same_content_new_actor = RemoteObject(
        {
            "class": "Array",
            "actor": "obj77",
            "preview": {"kind": "ArrayLike", "length": 2, "items": [1, 2]},
        }
    )
    different = RemoteObject(
        {
            "class": "Array",
            "actor": "obj10",
            "preview": {"kind": "ArrayLike", "length": 3, "items": [1, 2, 3]},
        }
    )

    assert first == same_content_new_actor
    assert first != different
    assert {k: v for k, v in {"a": first}.items() if {"a": same_content_new_actor}.get(k) != v} == {}


def test_truncated_preview_is_flagged():
    obj = RemoteObject(
        {
            "class": "Object",
            "actor": "obj5",
            "preview": {
                "kind": "Object",
                "ownPropertiesLength": 42,
                "ownProperties": {"a": {"value": {"value": 1}}},
            },
        }
    )

    assert obj.value == {"a": 1}
    assert obj.truncated is True
    assert "截断" in repr(obj)


def test_map_like_and_dom_node_previews_are_decoded():
    map_obj = RemoteObject(
        {
            "class": "Map",
            "actor": "obj6",
            "preview": {
                "kind": "MapLike",
                "size": 1,
                "entries": [[{"type": "string", "value": "k"}, {"value": 7}]],
            },
        }
    )
    assert map_obj.value == {"'k'": 7}

    node = RemoteObject(
        {
            "class": "HTMLDivElement",
            "actor": "obj7",
            "preview": {"kind": "DOMNode", "nodeName": "div", "nodeType": 1},
        }
    )
    assert node.value == {"nodeName": "div", "nodeType": 1}


def test_expand_fetches_full_properties():
    debugger = _debugger(
        {
            ("obj3", "prototypeAndProperties"): {
                "ownProperties": {
                    "name": {"value": {"type": "string", "value": "x"}},
                    "count": {"value": {"value": 9}},
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    obj = RemoteObject({"class": "Object", "actor": "obj3"})
    assert debugger.expand(obj) == {"name": "x", "count": 9}


def test_expand_restores_array_like_objects_to_a_list():
    debugger = _debugger(
        {
            ("obj4", "prototypeAndProperties"): {
                "ownProperties": {
                    "0": {"value": {"value": "a"}},
                    "1": {"value": {"value": "b"}},
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.expand("obj4") == ["a", "b"]


def test_expand_ignores_the_length_property_when_rebuilding_a_list():
    """真实数组除了下标还带 length，不能因此退化成 dict。"""
    debugger = _debugger(
        {
            ("arr", "prototypeAndProperties"): {
                "ownProperties": {
                    "0": {"value": {"value": 1}},
                    "1": {"value": {"value": 2}},
                    "2": {"value": {"value": 3}},
                    "length": {"value": {"value": 3}},
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.expand("arr") == [1, 2, 3]


def test_expand_keeps_dict_shape_for_mixed_keys():
    debugger = _debugger(
        {
            ("mixed", "prototypeAndProperties"): {
                "ownProperties": {
                    "0": {"value": {"value": "a"}},
                    "name": {"value": {"value": "x"}},
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.expand("mixed") == {"0": "a", "name": "x"}


def test_expand_recurses_to_requested_depth():
    debugger = _debugger(
        {
            ("outer", "prototypeAndProperties"): {
                "ownProperties": {
                    "cfg": {
                        "value": {"type": "object", "class": "Object", "actor": "inner"}
                    }
                }
            },
            ("inner", "prototypeAndProperties"): {
                "ownProperties": {"v": {"value": {"value": 42}}}
            },
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    shallow = debugger.expand("outer", depth=1)
    assert isinstance(shallow["cfg"], RemoteObject)

    deep = debugger.expand("outer", depth=2)
    assert deep == {"cfg": {"v": 42}}


def test_get_property_fetches_a_single_named_property():
    """大对象（如 window）不能全量展开，按名字定向取。"""
    debugger = _debugger(
        {
            ("obj3", "property"): {
                "descriptor": {"value": {"type": "string", "value": "hello"}}
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.get_property("obj3", "title") == "hello"
    _actor, _type, params = debugger._rdp.calls[-1]
    assert params == {"name": "title"}


def test_get_property_returns_none_for_missing_property():
    debugger = _debugger({("obj3", "property"): {"descriptor": None}})
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.get_property("obj3", "nope") is None


def test_constructor_name_derives_from_prototype():
    """[[Class]] 对类实例统一是 Object，真类名要从原型的 constructor 派生。"""
    debugger = _debugger(
        {
            ("obj3", "prototypeAndProperties"): {
                "prototype": {"type": "object", "class": "Object", "actor": "proto3"}
            },
            ("proto3", "property"): {
                "descriptor": {
                    "value": {"type": "object", "class": "Function", "name": "Cart"}
                }
            },
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    obj = RemoteObject({"class": "Object", "actor": "obj3"})
    assert debugger.constructor_name(obj) == "Cart"


def test_constructor_name_falls_back_to_class_when_unavailable():
    debugger = _debugger({("obj3", "prototypeAndProperties"): {"prototype": None}})
    debugger._handle_paused(PAUSED_PACKET)

    obj = RemoteObject({"class": "Object", "actor": "obj3"})
    assert debugger.constructor_name(obj) == "Object"


def test_expand_warns_instead_of_silently_truncating(caplog):
    """静默截断会让调用方以为属性不存在。"""
    debugger = _debugger(
        {
            ("big", "prototypeAndProperties"): {
                "ownProperties": {
                    "p{}".format(i): {"value": {"value": i}} for i in range(20)
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    with caplog.at_level("WARNING", logger="ruyipage"):
        result = debugger.expand("big", max_items=5)

    assert len(result) == 5
    assert "get_property" in caplog.text


def test_expand_requires_paused_state():
    debugger = _debugger()

    with pytest.raises(DebuggerError, match="暂停状态"):
        debugger.expand("obj1")


def test_expand_rejects_non_objects():
    debugger = _debugger()
    debugger._handle_paused(PAUSED_PACKET)

    with pytest.raises(DebuggerError, match="不是可展开的对象"):
        debugger.expand(RemoteObject({"class": "Object"}))


def test_scope_accepts_explicit_frame():
    debugger = _debugger(
        {
            ("frameX", "getEnvironment"): {
                "bindings": {"variables": {"v": {"value": {"value": 1}}}}
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    frame = Frame({"actor": "frameX", "where": {}}, debugger._source_urls)
    assert debugger.scope(frame=frame) == {"v": 1}


def test_scope_requires_paused():
    debugger = _debugger()

    with pytest.raises(DebuggerError, match="暂停状态"):
        debugger.scope()


# ── 启动前置校验 ──


def test_pause_on_exceptions_sends_reconfigure():
    debugger = _debugger()

    debugger.pause_on_exceptions()

    assert debugger._rdp.calls[-1] == (
        "thread1",
        "reconfigure",
        {"options": {"pauseOnExceptions": True, "ignoreCaughtExceptions": True}},
    )


def test_pause_on_exceptions_can_include_caught_ones():
    debugger = _debugger()

    debugger.pause_on_exceptions(True, ignore_caught=False)

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params["options"]["ignoreCaughtExceptions"] is False


def test_pause_on_debugger_statement_toggle():
    debugger = _debugger()

    debugger.pause_on_debugger_statement(False)

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params["options"] == {"shouldPauseOnDebuggerStatement": False}


def test_exception_pause_exposes_the_thrown_value():
    debugger = _debugger()

    debugger._handle_paused(
        {
            "from": "thread1",
            "type": "paused",
            "why": {
                "type": "exception",
                "exception": {
                    "type": "object",
                    "class": "TypeError",
                    "actor": "obj1",
                    "preview": {
                        "kind": "Error",
                        "name": "TypeError",
                        "message": "x is not a function",
                    },
                },
            },
            "frame": {"actor": "frame7", "where": {"actor": "src1", "line": 12}},
        }
    )
    state = debugger.wait_paused(timeout=1)

    assert state.is_exception is True
    assert state.exception.class_name == "TypeError"
    assert state.exception.value["message"] == "x is not a function"


def test_breakpoint_pause_is_not_an_exception_pause():
    debugger = _debugger()

    debugger._handle_paused(PAUSED_PACKET)
    state = debugger.wait_paused(timeout=1)

    assert state.is_exception is False
    assert state.exception is None


def test_watchdog_auto_resumes_a_forgotten_pause():
    """无人值守时漏掉 resume 会让页面永久卡死，看门狗兜底。"""
    debugger = _debugger()
    debugger._auto_resume_after = 0.05

    debugger._handle_paused(PAUSED_PACKET)
    assert debugger.paused is True

    time.sleep(0.4)

    assert debugger.paused is False
    assert any(t == "resume" for _a, t, _p in debugger._rdp.calls)


def test_watchdog_is_disarmed_by_a_normal_resume():
    debugger = _debugger()
    debugger._auto_resume_after = 0.05

    debugger._handle_paused(PAUSED_PACKET)
    debugger.resume()
    resume_calls = sum(1 for _a, t, _p in debugger._rdp.calls if t == "resume")

    time.sleep(0.3)

    # 看门狗不应再补一次 resume
    assert sum(1 for _a, t, _p in debugger._rdp.calls if t == "resume") == resume_calls


def test_watchdog_is_off_by_default():
    debugger = _debugger()

    debugger._handle_paused(PAUSED_PACKET)
    time.sleep(0.2)

    assert debugger.paused is True


def test_restart_frame_uses_restart_resume_limit():
    debugger = _debugger()
    debugger._handle_paused(PAUSED_PACKET)

    debugger.restart_frame()

    _actor, type_, params = debugger._rdp.calls[-1]
    assert type_ == "resume"
    assert params["resumeLimit"] == {"type": "restart"}


def test_skip_breakpoints_goes_through_reconfigure():
    """thread.skipBreakpoints 的 spec 在 Firefox 155 上是坏的，只能用 reconfigure。"""
    debugger = _debugger()

    debugger.skip_breakpoints(True)

    actor, type_, params = debugger._rdp.calls[-1]
    assert (actor, type_) == ("thread1", "reconfigure")
    assert params["options"] == {"skipBreakpoints": True}


def test_include_async_frames_sets_both_reconfigure_flags():
    debugger = _debugger()

    debugger.include_async_frames()

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params["options"] == {
        "shouldIncludeSavedFrames": True,
        "shouldIncludeAsyncLiveFrames": True,
    }


# ── 黑盒化 ──


def _blackbox_debugger(extra=None):
    replies = {
        ("thread1", "sources"): {
            "sources": [
                {"actor": "lib1", "url": "https://cdn.test/react.js"},
                {"actor": "app1", "url": "https://x.test/app.js"},
            ]
        },
        ("lib1", "blackbox"): {"pausedInSource": False},
        ("lib1", "unblackbox"): {},
    }
    replies.update(extra or {})
    return _debugger(replies)


def test_blackbox_marks_whole_source_by_default():
    debugger = _blackbox_debugger()

    assert debugger.blackbox("react.js") is False

    actor, type_, params = debugger._rdp.calls[-1]
    assert (actor, type_) == ("lib1", "blackbox")
    assert params == {"range": None}


def test_blackbox_supports_a_line_range():
    debugger = _blackbox_debugger()

    debugger.blackbox("react.js", start_line=10, end_line=200)

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params["range"] == {
        "start": {"line": 10, "column": 0},
        "end": {"line": 200, "column": 0},
    }


def test_blackbox_rejects_a_half_specified_range():
    debugger = _blackbox_debugger()

    with pytest.raises(DebuggerError, match="start_line 和 end_line"):
        debugger.blackbox("react.js", start_line=10)


def test_blackbox_applies_to_every_inline_script_of_one_url():
    """同一 HTML 的多段内联脚本共用 URL，必须逐个标记。"""
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [
                    {"actor": "inline1", "url": "https://x.test/page.html"},
                    {"actor": "inline2", "url": "https://x.test/page.html"},
                ]
            },
            ("inline1", "blackbox"): {"pausedInSource": False},
            ("inline2", "blackbox"): {"pausedInSource": True},
        }
    )

    assert debugger.blackbox("https://x.test/page.html") is True

    targets = [a for a, t, _p in debugger._rdp.calls if t == "blackbox"]
    assert targets == ["inline1", "inline2"]


def test_unblackbox_uses_the_matching_request():
    debugger = _blackbox_debugger()

    debugger.unblackbox("react.js")

    actor, type_, _params = debugger._rdp.calls[-1]
    assert (actor, type_) == ("lib1", "unblackbox")


def test_blackboxed_lists_flagged_sources():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [
                    {
                        "actor": "lib1",
                        "url": "https://cdn.test/react.js",
                        "isBlackBoxed": True,
                    },
                    {"actor": "app1", "url": "https://x.test/app.js"},
                ]
            }
        }
    )

    assert debugger.blackboxed() == ["https://cdn.test/react.js"]


# ── 事件与 XHR 断点 ──


def test_available_event_breakpoints_groups_ids_by_category():
    debugger = _debugger(
        {
            ("thread1", "getAvailableEventBreakpoints"): {
                "value": [
                    {
                        "name": "Mouse",
                        "events": [
                            {"id": "event.mouse.click", "name": "click"},
                            {"id": "event.mouse.mousedown", "name": "mousedown"},
                        ],
                    },
                    {"name": "Keyboard", "events": [{"id": "event.keyboard.keydown"}]},
                ]
            }
        }
    )

    assert debugger.available_event_breakpoints() == {
        "Mouse": ["event.mouse.click", "event.mouse.mousedown"],
        "Keyboard": ["event.keyboard.keydown"],
    }


def test_set_event_breakpoints_sends_ids():
    debugger = _debugger()

    debugger.set_event_breakpoints(["event.mouse.click"])

    assert debugger._rdp.calls[-1] == (
        "thread1",
        "setActiveEventBreakpoints",
        {"ids": ["event.mouse.click"]},
    )


def test_set_event_breakpoints_clears_with_empty_list():
    debugger = _debugger()

    debugger.set_event_breakpoints([])

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params == {"ids": []}


def test_active_event_breakpoints_returns_ids():
    debugger = _debugger(
        {("thread1", "getActiveEventBreakpoints"): {"ids": ["event.mouse.click"]}}
    )

    assert debugger.active_event_breakpoints() == ["event.mouse.click"]


def test_event_breakpoint_pause_is_identified():
    debugger = _debugger()

    debugger._handle_paused(
        {
            "from": "thread1",
            "type": "paused",
            "why": {"type": "eventBreakpoint", "breakpoint": "event.mouse.click"},
            "frame": {"actor": "frame7", "where": {"actor": "src1", "line": 3}},
        }
    )
    state = debugger.wait_paused(timeout=1)

    assert state.is_event_breakpoint is True
    assert state.event_breakpoint == "event.mouse.click"
    assert state.is_exception is False


def test_xhr_breakpoint_round_trip():
    debugger = _debugger(
        {
            ("thread1", "setXHRBreakpoint"): {"value": True},
            ("thread1", "removeXHRBreakpoint"): {"value": True},
        }
    )

    assert debugger.set_xhr_breakpoint("/api/", "GET") is True
    assert debugger._rdp.calls[-1] == (
        "thread1",
        "setXHRBreakpoint",
        {"path": "/api/", "method": "GET"},
    )

    assert debugger.remove_xhr_breakpoint("/api/", "GET") is True
    assert debugger._rdp.calls[-1][1] == "removeXHRBreakpoint"


def test_xhr_breakpoint_defaults_match_any_request():
    debugger = _debugger({("thread1", "setXHRBreakpoint"): {"value": True}})

    debugger.set_xhr_breakpoint()

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params == {"path": "", "method": "ANY"}


def test_xhr_pause_is_identified():
    debugger = _debugger()

    # 内核用的是大写 XHR，与其他小写驼峰的暂停原因不同
    debugger._handle_paused(
        {
            "from": "thread1",
            "type": "paused",
            "why": {"type": "XHR"},
            "frame": {"actor": "frame7", "where": {"actor": "src1", "line": 3}},
        }
    )
    state = debugger.wait_paused(timeout=1)

    assert state.is_xhr is True
    assert state.is_event_breakpoint is False


def test_log_point_sends_log_value_and_does_not_pause():
    """日志断点命中时只输出 console 消息，不暂停。"""
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "getBreakpointPositionsCompressed"): {"positions": {"6": [16]}},
        }
    )

    bp = debugger.set_breakpoint(
        "https://x.test/app.js", 6, log_value="subtotal", log_stacktrace=True
    )

    assert bp.is_log_point is True
    assert bp.log_value == "subtotal"
    assert "LogPoint" in repr(bp)
    options = next(
        params["options"]
        for _actor, type_, params in debugger._rdp.calls
        if type_ == "setBreakpoint"
    )
    assert options == {"logValue": "subtotal", "showStacktrace": True}


def test_plain_breakpoint_is_not_a_log_point():
    debugger = _debugger(
        {
            ("thread1", "sources"): {
                "sources": [{"actor": "src1", "url": "https://x.test/app.js"}]
            },
            ("src1", "getBreakpointPositionsCompressed"): {"positions": {"6": [16]}},
        }
    )

    bp = debugger.set_breakpoint("https://x.test/app.js", 6)

    assert bp.is_log_point is False
    assert bp.log_value is None


# ── 长字符串 ──


def test_long_string_keeps_its_handle_for_a_full_read():
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "bindings": {
                    "variables": {
                        "text": {
                            "value": {
                                "type": "longString",
                                "actor": "lstr1",
                                "length": 12,
                                "initial": "hello",
                            }
                        }
                    }
                }
            },
            ("lstr1", "substring"): {"substring": " world!"},
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    text = debugger.scope()["text"]

    # 当普通字符串用时是截断版
    assert isinstance(text, str)
    assert text == "hello…"
    assert text.truncated is True
    # 需要完整内容时可以取回
    assert debugger.read_string(text) == "hello world!"


def test_short_string_is_returned_whole():
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "bindings": {
                    "variables": {
                        "text": {
                            "value": {
                                "type": "longString",
                                "actor": "lstr1",
                                "length": 5,
                                "initial": "short",
                            }
                        }
                    }
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.scope()["text"] == "short"


def test_read_string_passes_plain_strings_through():
    debugger = _debugger()

    assert debugger.read_string("plain") == "plain"


# ── Map / Set 条目 ──


def test_entries_decodes_a_map():
    """Map 条目形如 {type: "mapEntry", preview: {key, value}}。"""
    debugger = _debugger(
        {
            ("m1", "enumEntries"): {
                "iterator": {"type": "propertyIterator", "actor": "it1", "count": 2}
            },
            ("it1", "slice"): {
                "ownProperties": {
                    "0": {
                        "value": {
                            "type": "mapEntry",
                            "preview": {"key": "alpha", "value": 1},
                        }
                    },
                    "1": {
                        "value": {
                            "type": "mapEntry",
                            "preview": {"key": "beta", "value": 2},
                        }
                    },
                }
            },
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.entries("m1") == {"alpha": 1, "beta": 2}


def test_entries_decodes_a_set_as_a_list():
    """Set 条目是裸 grip。"""
    debugger = _debugger(
        {
            ("s1", "enumEntries"): {
                "iterator": {"type": "propertyIterator", "actor": "it2", "count": 3}
            },
            ("it2", "slice"): {
                "ownProperties": {
                    "0": {"value": "x"},
                    "1": {"value": "y"},
                    "2": {"value": "z"},
                }
            },
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.entries("s1") == ["x", "y", "z"]


def test_entries_on_an_object_without_entries_raises():
    debugger = _debugger({("o1", "enumEntries"): {"iterator": {"count": 0}}})
    debugger._handle_paused(PAUSED_PACKET)

    with pytest.raises(DebuggerError, match="没有可枚举的条目"):
        debugger.entries("o1")


# ── 远程调用与 getter ──


def test_call_invokes_a_remote_function():
    debugger = _debugger(
        {("fn1", "apply"): {"value": {"return": {"type": "number", "value": 42}}}}
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.call("fn1", args=[1, "two"]) == 42

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params["arguments"] == [1, "two"]
    # 服务端把「是对象但没有 actor」的实参当成非法 grip，null 要原样传
    assert params["context"] is None


def test_call_passes_remote_objects_by_reference():
    target = RemoteObject({"class": "Object", "actor": "obj9"})
    debugger = _debugger({("fn1", "apply"): {"value": {"return": {"value": 1}}}})
    debugger._handle_paused(PAUSED_PACKET)

    debugger.call("fn1", args=[target], this=target)

    _actor, _type, params = debugger._rdp.calls[-1]
    assert params["arguments"] == [{"type": "object", "actor": "obj9"}]
    assert params["context"] == {"type": "object", "actor": "obj9"}


def test_call_surfaces_a_thrown_error():
    debugger = _debugger(
        {
            ("fn1", "apply"): {
                "value": {
                    "throw": {
                        "type": "object",
                        "class": "TypeError",
                        "actor": "e1",
                        "preview": {"kind": "Error", "message": "boom"},
                    }
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    with pytest.raises(DebuggerError, match="函数调用抛出异常"):
        debugger.call("fn1")


def test_invoke_getter_returns_the_computed_value():
    debugger = _debugger(
        {("o1", "propertyValue"): {"value": {"return": {"value": "computed"}}}}
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.invoke_getter("o1", "total") == "computed"
    _actor, _type, params = debugger._rdp.calls[-1]
    assert params == {"name": "total", "receiverId": None}


def test_invoke_getter_surfaces_a_thrown_error():
    debugger = _debugger(
        {("o1", "propertyValue"): {"value": {"throw": {"type": "string", "value": "no"}}}}
    )
    debugger._handle_paused(PAUSED_PACKET)

    with pytest.raises(DebuggerError, match="getter total抛出异常"):
        debugger.invoke_getter("o1", "total")


# ── Promise ──


def test_promise_state_is_decoded():
    """结果嵌在同名的 promiseState 键下。"""
    debugger = _debugger(
        {
            ("p1", "promiseState"): {
                "promiseState": {
                    "state": "fulfilled",
                    "value": 7,
                    "creationTimestamp": 123.0,
                    "timeToSettle": 4.5,
                }
            }
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    state = debugger.promise_state("p1")

    assert state["state"] == "fulfilled"
    assert state["value"] == 7
    assert state["reason"] is None
    assert state["time_to_settle"] == 4.5


def test_promise_state_on_a_non_promise_raises():
    debugger = _debugger({("o1", "promiseState"): {"promiseState": {}}})
    debugger._handle_paused(PAUSED_PACKET)

    with pytest.raises(DebuggerError, match="不是 Promise"):
        debugger.promise_state("o1")


# ── 监视点 ──


class _SendingRdp(FakeRdp):
    def __init__(self, replies=None):
        super().__init__(replies)
        self.sent = []

    def send(self, packet):
        self.sent.append(packet)


def _watch_debugger():
    debugger = Debugger(owner=None)
    debugger._rdp = _SendingRdp()
    debugger._thread_actor = "thread1"
    debugger._source_urls = {"src1": "https://x.test/app.js"}
    debugger._handle_paused(PAUSED_PACKET)
    return debugger


def test_watch_property_uses_a_oneway_send():
    """addWatchpoint 是 oneway，用 request() 会一直等到超时。"""
    debugger = _watch_debugger()

    debugger.watch_property("o1", "total", on="set")

    assert debugger._rdp.sent == [
        {
            "to": "o1",
            "type": "addWatchpoint",
            "property": "total",
            "label": "total",
            "watchpointType": "set",
        }
    ]
    assert debugger._rdp.calls == []


def test_watch_property_rejects_an_unknown_kind():
    debugger = _watch_debugger()

    with pytest.raises(DebuggerError, match="getorset"):
        debugger.watch_property("o1", "total", on="both")


def test_unwatch_property_removes_one_or_all():
    debugger = _watch_debugger()

    debugger.unwatch_property("o1", "total")
    debugger.unwatch_property("o1")

    assert [p["type"] for p in debugger._rdp.sent] == [
        "removeWatchpoint",
        "removeWatchpoints",
    ]


def test_object_operations_require_a_pause():
    debugger = _debugger()

    for call in (
        lambda: debugger.entries("o1"),
        lambda: debugger.call("fn1"),
        lambda: debugger.invoke_getter("o1", "x"),
        lambda: debugger.promise_state("p1"),
        lambda: debugger.watch_property("o1", "x"),
    ):
        with pytest.raises(DebuggerError, match="暂停状态"):
            call()


# ── 日志断点输出 ──


def test_log_entries_are_collected_from_console_messages():
    debugger = _debugger()

    debugger._handle_console_message(
        {
            "message": {
                "level": "logPoint",
                "arguments": [1, "two"],
                "filename": "https://x.test/app.js",
                "lineNumber": 6,
            }
        }
    )

    entries = debugger.logs()
    assert entries == [
        {
            "values": [1, "two"],
            "url": "https://x.test/app.js",
            "line": 6,
            "is_error": False,
            "stacktrace": [],
        }
    ]
    # 默认取回后清空
    assert debugger.logs() == []


def test_log_entries_can_be_kept_after_reading():
    debugger = _debugger()
    debugger._handle_console_message({"message": {"level": "logPoint", "arguments": []}})

    assert len(debugger.logs(clear=False)) == 1
    assert len(debugger.logs()) == 1


def test_page_console_calls_are_ignored():
    """页面自己的 console.log 不应混进日志断点输出。"""
    debugger = _debugger()

    debugger._handle_console_message(
        {"message": {"level": "log", "arguments": ["from the page"]}}
    )

    assert debugger.logs() == []


def test_failing_log_expression_is_marked_as_an_error():
    debugger = _debugger()

    debugger._handle_console_message(
        {"message": {"level": "logPointError", "arguments": ["nope is not defined"]}}
    )

    assert debugger.logs()[0]["is_error"] is True


def test_log_buffer_is_bounded():
    debugger = _debugger()

    for i in range(_MAX_LOG_ENTRIES + 50):
        debugger._handle_console_message(
            {"message": {"level": "logPoint", "arguments": [i]}}
        )

    entries = debugger.logs()
    assert len(entries) == _MAX_LOG_ENTRIES
    # 丢的是最早的
    assert entries[0]["values"] == [50]


def test_wait_logs_returns_early_once_enough_arrived():
    debugger = _debugger()
    debugger._handle_console_message({"message": {"level": "logPoint", "arguments": [1]}})

    started = time.time()
    entries = debugger.wait_logs(count=1, timeout=5)

    assert len(entries) == 1
    assert time.time() - started < 1


def test_wait_logs_gives_back_what_it_has_on_timeout():
    debugger = _debugger()
    debugger._handle_console_message({"message": {"level": "logPoint", "arguments": [1]}})

    entries = debugger.wait_logs(count=5, timeout=0.2)

    assert len(entries) == 1


def test_scope_includes_this_from_the_frame():
    """this 不在环境绑定里，它挂在帧上。"""
    debugger = _debugger(
        {("frame7", "getEnvironment"): {"bindings": {"variables": {"sum": {"value": 3}}}}}
    )
    packet = dict(PAUSED_PACKET)
    packet["frame"] = dict(
        PAUSED_PACKET["frame"],
        **{"this": {"type": "object", "class": "Cart", "actor": "cart1"}}
    )
    debugger._handle_paused(packet)

    scope = debugger.scope()

    assert scope["sum"] == 3
    assert isinstance(scope["this"], RemoteObject)
    assert scope["this"].actor == "cart1"


def test_scope_omits_this_when_the_frame_has_none():
    debugger = _debugger(
        {("frame7", "getEnvironment"): {"bindings": {"variables": {"sum": {"value": 3}}}}}
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert "this" not in debugger.scope()


def test_frame_exposes_this():
    frame = Frame({"actor": "f1", "this": {"type": "object", "actor": "o1"}})

    assert isinstance(frame.this_object, RemoteObject)


def test_get_property_walks_the_prototype_chain_for_methods():
    """类的方法挂在原型上，不是实例的自有属性。"""
    debugger = _debugger(
        {
            ("cart1", "property"): {"descriptor": None},
            ("cart1", "prototype"): {
                "prototype": {"type": "object", "actor": "proto1"}
            },
            ("proto1", "property"): {
                "descriptor": {"value": {"type": "object", "class": "Function",
                                          "actor": "fn1"}}
            },
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    method = debugger.get_property("cart1", "lineTotal")

    assert isinstance(method, RemoteObject)
    assert method.actor == "fn1"


def test_get_property_can_be_limited_to_own_properties():
    debugger = _debugger(
        {
            ("cart1", "property"): {"descriptor": None},
            ("cart1", "prototype"): {
                "prototype": {"type": "object", "actor": "proto1"}
            },
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.get_property("cart1", "lineTotal", own_only=True) is None
    # 没有去问原型
    assert not any(actor == "cart1" and type_ == "prototype"
                   for actor, type_, _ in debugger._rdp.calls)


def test_get_property_stops_at_the_end_of_the_prototype_chain():
    debugger = _debugger(
        {
            ("o1", "property"): {"descriptor": None},
            ("o1", "prototype"): {"prototype": {"type": "null"}},
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.get_property("o1", "nope") is None


def test_get_property_does_not_loop_on_a_cyclic_prototype():
    debugger = _debugger(
        {
            ("o1", "property"): {"descriptor": None},
            ("o1", "prototype"): {"prototype": {"type": "object", "actor": "o1"}},
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.get_property("o1", "nope") is None


def test_global_object_is_resolved_from_the_frame_this():
    debugger = _debugger(
        {("cart1", "global"): {"global": {"type": "object", "class": "Window",
                                           "actor": "win1"}}}
    )
    packet = dict(PAUSED_PACKET)
    packet["frame"] = dict(
        PAUSED_PACKET["frame"],
        **{"this": {"type": "object", "class": "Cart", "actor": "cart1"}}
    )
    debugger._handle_paused(packet)

    window = debugger.global_object()

    assert isinstance(window, RemoteObject)
    assert window.actor == "win1"


def test_global_object_falls_back_to_any_object_in_scope():
    """帧没有 this 时，用作用域里任意远端对象定位全局。"""
    debugger = _debugger(
        {
            ("frame7", "getEnvironment"): {
                "bindings": {
                    "variables": {
                        "count": {"value": 1},
                        "cart": {
                            "value": {"type": "object", "class": "Object",
                                       "actor": "cart1"}
                        },
                    }
                }
            },
            ("cart1", "global"): {"global": {"type": "object", "class": "Window",
                                              "actor": "win1"}},
        }
    )
    debugger._handle_paused(PAUSED_PACKET)

    assert debugger.global_object().actor == "win1"


def test_global_object_without_any_object_to_start_from_raises():
    debugger = _debugger(
        {("frame7", "getEnvironment"): {"bindings": {"variables": {"n": {"value": 1}}}}}
    )
    debugger._handle_paused(PAUSED_PACKET)

    with pytest.raises(DebuggerError, match="显式传入 obj"):
        debugger.global_object()


def test_api_calls_before_start_raise_clear_error():
    debugger = Debugger(owner=None)

    for call in (
        lambda: debugger.sources(),
        lambda: debugger.pause(),
        lambda: debugger.wait_paused(timeout=0.01),
    ):
        with pytest.raises(DebuggerError, match="调试通道未启动"):
            call()
