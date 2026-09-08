# -*- coding: utf-8 -*-
"""page.debugger 在真实 Firefox 上的断点调试回归。

这些测试需要内核带 DevTools（--start-debugger-server），因此标记为 browser。
"""

import re
import threading
from pathlib import Path

import pytest

from ruyipage import FirefoxOptions, FirefoxPage
from ruyipage._units.debugger import RemoteObject, _grip_to_python as _grip_to_remote
from ruyipage.errors import DebuggerError

COMPLEX_PAGE = "debugger_complex.html"
COMPLEX_SOURCE = "debugger_complex.js"
_COMPLEX_JS = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "pages" / "debugger_complex.js"
).read_text(encoding="utf-8").split("\n")


def _line_of(pattern):
    """按内容定位复杂 fixture 里的行号，避免硬编码行号随文件改动失效。"""
    rx = re.compile(pattern)
    for index, text in enumerate(_COMPLEX_JS):
        if rx.search(text):
            return index + 1
    raise AssertionError("pattern not found in fixture: " + pattern)


# debugger_target.js 中 `const total = subtotal * (1 + taxRate);` 所在行
TOTAL_LINE = 6
SOURCE_NAME = "debugger_target.js"


# 每段内联脚本的行号是相对整个 HTML 文档的
INLINE_PAGE = "debugger_inline.html"
FIRST_INLINE_LINE = 9    # const doubled = a * 2;
SECOND_INLINE_LINE = 17  # const tripled = b * 3;


def _find_free_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def debug_page(test_browser_path, fixture_page_url):
    opts = FirefoxOptions()
    opts.headless(True)
    if test_browser_path:
        opts.set_browser_path(test_browser_path)
    opts.enable_debugger(port=_find_free_port())

    page = FirefoxPage(opts)
    try:
        page.get(fixture_page_url("debugger_target.html"))
        try:
            page.debugger.start()
        except DebuggerError as exc:
            pytest.skip("devtools server unavailable in this build: {}".format(exc))
        yield page
    finally:
        try:
            page.debugger.stop()
        except Exception:
            pass
        try:
            page.quit()
        except Exception:
            pass


def _trigger_in_background(page, script="return window.runComputation(25, 4);"):
    """在后台线程触发 JS。

    暂停期间 BiDi 调用会一直阻塞，因此不能在主线程触发。
    """
    outcome = {}

    def run():
        try:
            outcome["value"] = page.run_js(script, timeout=120)
        except Exception as exc:
            outcome["error"] = "{}: {}".format(type(exc).__name__, exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return outcome, worker


@pytest.mark.feature
@pytest.mark.browser
def test_sources_include_external_script(debug_page):
    sources = debug_page.debugger.sources(url_contains=SOURCE_NAME)

    assert len(sources) == 1
    assert sources[0].url.endswith(SOURCE_NAME)
    assert sources[0].actor


@pytest.mark.feature
@pytest.mark.browser
def test_breakable_lines_report_real_positions(debug_page):
    lines = debug_page.debugger.breakable_lines(SOURCE_NAME)

    assert TOTAL_LINE in lines
    # 函数声明行本身不是可执行语句
    assert 2 not in lines


@pytest.mark.feature
@pytest.mark.browser
def test_breakpoint_pauses_js_and_exposes_stack_and_scope(debug_page):
    debugger = debug_page.debugger
    breakpoint_ = debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)
    assert breakpoint_.column is not None

    outcome, worker = _trigger_in_background(debug_page)

    state = debugger.wait_paused(timeout=30)
    assert state is not None, "断点未命中"
    assert state.why == "breakpoint"
    assert state.line == TOTAL_LINE
    assert debugger.paused is True

    # JS 必须仍处于挂起状态
    worker.join(timeout=1.0)
    assert "value" not in outcome

    frames = debugger.frames()
    assert [f.display_name for f in frames][:2] == [
        "computeTotal",
        "window.runComputation",
    ]
    # 服务端只给 source actor，URL 必须被换算出来
    assert frames[0].source_actor
    assert frames[0].url.endswith(SOURCE_NAME)
    assert state.url.endswith(SOURCE_NAME)
    assert frames[0].arguments == [25, 4]

    scope = debugger.scope()
    assert scope["subtotal"] == 100
    assert scope["taxRate"] == 0.08
    assert scope["marker"] == "scope-probe"
    # total 尚未赋值，断点停在该语句之前
    assert scope.get("total") is None

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108
    assert debugger.paused is False


@pytest.mark.feature
@pytest.mark.browser
def test_step_over_advances_one_statement(debug_page):
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    assert debugger.wait_paused(timeout=30) is not None

    debugger.step_over()
    stepped = debugger.wait_paused(timeout=20)

    assert stepped is not None
    assert stepped.why == "resumeLimit"
    assert stepped.line == TOTAL_LINE + 1
    # 单步后 total 已完成赋值
    assert debugger.scope()["total"] == 108

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_step_out_returns_to_caller(debug_page):
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    assert debugger.wait_paused(timeout=30) is not None

    debugger.step_out()
    stepped = debugger.wait_paused(timeout=20)

    assert stepped is not None
    assert stepped.frame.display_name == "window.runComputation"

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_false_condition_does_not_pause(debug_page):
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE, condition="quantity > 999")

    # 条件不成立，JS 应正常跑完，不需要后台线程
    assert debug_page.run_js("return window.runComputation(25, 4);") == 108
    assert debugger.paused is False


@pytest.mark.feature
@pytest.mark.browser
def test_true_condition_pauses_with_expected_scope(debug_page):
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE, condition="subtotal === 100")

    outcome, worker = _trigger_in_background(debug_page)

    state = debugger.wait_paused(timeout=30)
    assert state is not None, "条件成立时应当暂停"
    assert state.why == "breakpoint"
    assert state.condition_failed is False
    assert debugger.scope()["subtotal"] == 100

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_broken_condition_pauses_and_reports_the_error(debug_page):
    """条件表达式出错时 Firefox 选择暂停，避免调试者误判断点没命中。"""
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE, condition="undefinedVar.x")

    outcome, worker = _trigger_in_background(debug_page)

    state = debugger.wait_paused(timeout=30)
    assert state is not None
    assert state.condition_failed is True
    assert state.message

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_removed_breakpoint_no_longer_pauses(debug_page):
    debugger = debug_page.debugger
    breakpoint_ = debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)
    debugger.remove_breakpoint(breakpoint_)

    assert debugger.breakpoints == []
    assert debug_page.run_js("return window.runComputation(25, 4);") == 108
    assert debugger.paused is False


@pytest.mark.feature
@pytest.mark.browser
def test_stop_resumes_a_paused_page(debug_page):
    """stop() 必须先恢复执行，否则页面会永久卡住。"""
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    assert debugger.wait_paused(timeout=30) is not None

    debugger.stop()

    worker.join(timeout=30)
    assert outcome.get("value") == 108
    assert debugger.started is False


@pytest.fixture
def inline_debug_page(test_browser_path, fixture_page_url):
    """加载含两段内联脚本的页面，它们共用同一个文档 URL。"""
    opts = FirefoxOptions()
    opts.headless(True)
    if test_browser_path:
        opts.set_browser_path(test_browser_path)
    opts.enable_debugger(port=_find_free_port())

    page = FirefoxPage(opts)
    try:
        page.get(fixture_page_url(INLINE_PAGE))
        try:
            page.debugger.start()
        except DebuggerError as exc:
            pytest.skip("devtools server unavailable in this build: {}".format(exc))
        yield page
    finally:
        try:
            page.debugger.stop()
        except Exception:
            pass
        try:
            page.quit()
        except Exception:
            pass


@pytest.mark.feature
@pytest.mark.browser
def test_breakable_lines_merge_every_inline_script(inline_debug_page):
    """两段内联脚本共用文档 URL，可断点行必须都出现。"""
    lines = inline_debug_page.debugger.breakable_lines(INLINE_PAGE)

    assert FIRST_INLINE_LINE in lines
    assert SECOND_INLINE_LINE in lines


@pytest.mark.feature
@pytest.mark.browser
def test_breakpoint_hits_inside_the_second_inline_script(inline_debug_page):
    """回归：列号解析曾只查同 URL 的最后一个源，导致这里下不了断点。"""
    debugger = inline_debug_page.debugger
    breakpoint_ = debugger.set_breakpoint(INLINE_PAGE, SECOND_INLINE_LINE)
    assert breakpoint_.column is not None

    outcome, worker = _trigger_in_background(
        inline_debug_page, "return window.secondInline(5);"
    )

    state = debugger.wait_paused(timeout=30)
    assert state is not None, "第二段内联脚本的断点未命中"
    assert state.line == SECOND_INLINE_LINE
    assert debugger.scope()["b"] == 5

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 15


@pytest.mark.feature
@pytest.mark.browser
def test_breakpoint_hits_inside_the_first_inline_script(inline_debug_page):
    debugger = inline_debug_page.debugger
    debugger.set_breakpoint(INLINE_PAGE, FIRST_INLINE_LINE)

    outcome, worker = _trigger_in_background(
        inline_debug_page, "return window.firstInline(4);"
    )

    state = debugger.wait_paused(timeout=30)
    assert state is not None, "第一段内联脚本的断点未命中"
    assert state.line == FIRST_INLINE_LINE
    assert debugger.scope()["a"] == 4

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 8


@pytest.mark.feature
@pytest.mark.browser
def test_breakpoint_survives_navigation(debug_page, fixture_page_url):
    """断点按 URL 记录，服务端会在源重新加载时自动重新应用。"""
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    assert debugger.wait_paused(timeout=30) is not None
    debugger.resume()
    worker.join(timeout=30)

    debug_page.get(fixture_page_url("debugger_target.html"))
    assert debugger.started is True

    outcome, worker = _trigger_in_background(debug_page)
    state = debugger.wait_paused(timeout=30)
    assert state is not None, "导航后断点应当仍然生效"
    assert state.line == TOTAL_LINE

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_source_text_matches_the_executing_code(debug_page):
    """读回的必须是引擎执行的那份，行号与断点位置对齐。"""
    debugger = debug_page.debugger
    text = debugger.source_text(SOURCE_NAME)

    lines = text.split("\n")
    # TOTAL_LINE 是 1 起的行号
    assert "subtotal * (1 + taxRate)" in lines[TOTAL_LINE - 1]
    assert TOTAL_LINE in debugger.breakable_lines(SOURCE_NAME)


@pytest.mark.feature
@pytest.mark.browser
def test_source_text_covers_sources_that_cannot_be_downloaded(debug_page):
    """eval / new Function / blob 生成的代码没有可下载的地址。"""
    debug_page.run_js(
        "window.viaEval = eval('(function(x){ return x + 100; })')", as_expr=True
    )
    debug_page.run_js(
        "window.viaFunction = new Function('y', 'return y - 7;')", as_expr=True
    )

    debugger = debug_page.debugger
    texts = []
    for source in debugger.sources():
        if source.url:
            continue  # 有 URL 的走普通路径
        try:
            texts.append(debugger.source_text(source))
        except DebuggerError:
            pass

    joined = "\n".join(texts)
    assert "return x + 100" in joined, "eval 生成的代码应当可读"
    assert "return y - 7" in joined, "new Function 生成的代码应当可读"


@pytest.mark.feature
@pytest.mark.browser
def test_inline_source_text_is_the_whole_document(inline_debug_page):
    """内联脚本返回整个 HTML，因此行号是文档绝对行号。"""
    debugger = inline_debug_page.debugger
    text = debugger.source_text(INLINE_PAGE)

    lines = text.split("\n")
    assert lines[0].startswith("<!DOCTYPE html>")
    assert "const doubled" in lines[FIRST_INLINE_LINE - 1]
    assert "const tripled" in lines[SECOND_INLINE_LINE - 1]


@pytest.mark.feature
@pytest.mark.browser
def test_objects_in_scope_are_readable(debug_page):
    """对象不再是不透明的 grip，preview 已解码成可读内容。"""
    debug_page.run_js(
        "window.probe = {list:[1,2,3], cfg:{deep:{v:42}}, name:'x'}", as_expr=True
    )
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    assert debugger.wait_paused(timeout=30) is not None

    args = debugger.scope()["arguments"]
    assert isinstance(args, RemoteObject)
    assert args.class_name == "Arguments"
    # preview 已经把实参解出来了，无需额外请求
    assert args.value["0"] == 25
    assert args.value["1"] == 4

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_expand_reads_nested_object_contents(debug_page):
    debug_page.run_js(
        "window.probe = {list:[1,2,3], cfg:{deep:{v:42}}, name:'x'}", as_expr=True
    )
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    state = debugger.wait_paused(timeout=30)
    assert state is not None

    window = _grip_to_remote(state.raw["frame"]["this"])
    # window 有上千个属性，按名字定向取而不是全量展开
    probe = debugger.get_property(window, "probe")
    contents = debugger.expand(probe, depth=3)

    assert contents["name"] == "x"
    assert contents["list"] == [1, 2, 3]
    assert contents["cfg"]["deep"]["v"] == 42

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_scope_objects_compare_equal_across_a_step(debug_page):
    """回归：actor id 每次暂停都会变，不能让它污染变量 diff。"""
    debugger = debug_page.debugger
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    assert debugger.wait_paused(timeout=30) is not None

    before = debugger.scope()
    debugger.step_over()
    assert debugger.wait_paused(timeout=20) is not None
    after = debugger.scope()

    changed = {k: v for k, v in after.items() if before.get(k) != v}

    # 单步只让 total 完成赋值，arguments 等对象不应被误判为变化
    assert set(changed) == {"total"}
    assert changed["total"] == 108
    assert before["arguments"].actor != after["arguments"].actor, (
        "actor id 本就会变，测试前提成立"
    )

    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 108


@pytest.mark.feature
@pytest.mark.browser
def test_pause_on_exception_stops_at_the_throw_site(debug_page):
    """不用预先知道出错位置，让页面跑到抛异常处自己停下。"""
    debugger = debug_page.debugger
    debugger.pause_on_exceptions(True, ignore_caught=True)

    outcome, worker = _trigger_in_background(
        debug_page, "return window.boom();"
    )

    state = debugger.wait_paused(timeout=30)
    assert state is not None, "抛异常时应当暂停"
    assert state.is_exception is True
    assert state.exception is not None
    assert state.line is not None

    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_watchdog_recovers_a_page_left_paused(debug_page):
    """漏掉 resume 时页面不应被永久断住。"""
    debugger = debug_page.debugger
    debugger._auto_resume_after = 2
    debugger.set_breakpoint(SOURCE_NAME, TOTAL_LINE)

    outcome, worker = _trigger_in_background(debug_page)
    assert debugger.wait_paused(timeout=30) is not None

    # 故意不调用 resume，交给看门狗
    worker.join(timeout=30)

    assert outcome.get("value") == 108
    assert debugger.paused is False


@pytest.fixture
def complex_debug_page(test_browser_path, fixture_page_url):
    """加载接近真实业务代码的页面：类、递归、闭包、Map/Set、异常。"""
    opts = FirefoxOptions()
    opts.headless(True)
    if test_browser_path:
        opts.set_browser_path(test_browser_path)
    opts.enable_debugger(port=_find_free_port())

    page = FirefoxPage(opts)
    try:
        page.get(fixture_page_url(COMPLEX_PAGE))
        try:
            page.debugger.start(auto_resume_after=25)
        except DebuggerError as exc:
            pytest.skip("devtools server unavailable in this build: {}".format(exc))
        yield page
    finally:
        try:
            page.debugger.stop()
        except Exception:
            pass
        try:
            page.quit()
        except Exception:
            pass


@pytest.mark.feature
@pytest.mark.browser
def test_conditional_breakpoint_stops_at_specific_loop_iteration(complex_debug_page):
    """循环里的条件断点只在指定迭代停下，并能读到该迭代的局部对象。"""
    debugger = complex_debug_page.debugger
    loop_line = _line_of(r"const amount = this\.lineTotal")
    debugger.set_breakpoint(COMPLEX_SOURCE, loop_line, condition="i === 2")

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildCart();"
    )
    state = debugger.wait_paused(timeout=30)
    assert state is not None

    scope = debugger.scope()
    assert scope["i"] == 2
    line = scope["line"]
    assert isinstance(line, RemoteObject)
    assert line.value["name"] == "kiwi"  # 第 3 个商品

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value", {}).get("total") == 32.4


@pytest.mark.feature
@pytest.mark.browser
def test_class_instance_expands_with_nested_array_of_objects(complex_debug_page):
    debugger = complex_debug_page.debugger
    loop_line = _line_of(r"const amount = this\.lineTotal")
    debugger.set_breakpoint(COMPLEX_SOURCE, loop_line, condition="i === 2")

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildCart();"
    )
    state = debugger.wait_paused(timeout=30)
    assert state is not None

    this_obj = RemoteObject(state.raw["frame"]["this"])
    # [[Class]] 对类实例是 Object，构造函数名要单独派生
    assert this_obj.class_name == "Object"
    assert debugger.constructor_name(this_obj) == "Cart"

    cart = debugger.expand(this_obj, depth=1)
    assert cart["taxRate"] == 0.08

    items = debugger.expand(debugger.get_property(this_obj, "items"), depth=2)
    assert isinstance(items, list) and len(items) == 3
    assert items[0]["name"] == "apple" and items[0]["price"] == 3

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_step_into_follows_the_call_chain(complex_debug_page):
    debugger = complex_debug_page.debugger
    loop_line = _line_of(r"const amount = this\.lineTotal")
    debugger.set_breakpoint(COMPLEX_SOURCE, loop_line, condition="i === 0")

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildCart();"
    )
    assert debugger.wait_paused(timeout=30) is not None

    debugger.step_into()
    state = debugger.wait_paused(timeout=20)
    assert state is not None
    frames = debugger.frames()
    assert frames[0].display_name.endswith("lineTotal")
    # lineTotal 能看到自己的参数
    arg = debugger.scope()["line"]
    assert isinstance(arg, RemoteObject)
    assert arg.value["name"] == "apple"

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_recursion_call_stack_depth(complex_debug_page):
    debugger = complex_debug_page.debugger
    debugger.set_breakpoint(COMPLEX_SOURCE, _line_of(r"return 1;"))

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.runFactorial(5);"
    )
    assert debugger.wait_paused(timeout=30) is not None

    frames = debugger.frames(count=50)
    factorial_frames = [f for f in frames if f.display_name == "factorial"]
    assert len(factorial_frames) == 5

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 120


@pytest.mark.feature
@pytest.mark.browser
def test_closure_captured_variable_is_visible(complex_debug_page):
    debugger = complex_debug_page.debugger
    debugger.set_breakpoint(COMPLEX_SOURCE, _line_of(r"count \+= 1;"))

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.runCounter();"
    )
    assert debugger.wait_paused(timeout=30) is not None

    # count 是被捕获的闭包变量，在外层作用域
    scope = debugger.scope(include_parents=True)
    assert scope["count"] == 10

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)
    assert outcome.get("value") == 36


@pytest.mark.feature
@pytest.mark.browser
def test_caught_exception_is_ignored_but_uncaught_pauses(complex_debug_page):
    debugger = complex_debug_page.debugger
    debugger.pause_on_exceptions(True, ignore_caught=True)

    # try/catch 接住的异常不应打断
    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.runParse('not-json');"
    )
    assert debugger.wait_paused(timeout=6) is None, "被捕获的异常不应暂停"
    worker.join(timeout=30)
    assert outcome.get("value") == -1

    # 未捕获的异常应停在抛出点
    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.crash();"
    )
    state = debugger.wait_paused(timeout=30)
    assert state is not None and state.is_exception
    assert state.exception.class_name == "TypeError"

    debugger.pause_on_exceptions(False)
    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_blackboxing_keeps_step_into_out_of_a_source(complex_debug_page):
    """黑盒化后单步不再进入该源，这是真实页面里能正常单步的前提。"""
    debugger = complex_debug_page.debugger
    assert debugger.blackboxed() == []

    debugger.blackbox(COMPLEX_SOURCE)

    assert COMPLEX_SOURCE in debugger.blackboxed()[0]

    debugger.unblackbox(COMPLEX_SOURCE)
    assert debugger.blackboxed() == []


@pytest.mark.feature
@pytest.mark.browser
def test_blackboxed_source_is_skipped_when_stepping_into(complex_debug_page):
    debugger = complex_debug_page.debugger
    loop_line = _line_of(r"const amount = this\.lineTotal")
    debugger.set_breakpoint(COMPLEX_SOURCE, loop_line, condition="i === 0")

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildCart();"
    )
    assert debugger.wait_paused(timeout=30) is not None

    # 把自己这个源整体黑盒后，step_into 不应停在它内部
    debugger.blackbox(COMPLEX_SOURCE)
    debugger.clear_breakpoints()
    debugger.step_into()
    stepped = debugger.wait_paused(timeout=8)

    assert stepped is None or not (stepped.url or "").endswith(COMPLEX_SOURCE)

    debugger.unblackbox(COMPLEX_SOURCE)
    if debugger.paused:
        debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_event_breakpoint_pauses_inside_a_click_handler(complex_debug_page):
    """不需要知道处理器在哪个文件哪一行，事件派发时直接断下。"""
    debugger = complex_debug_page.debugger

    groups = debugger.available_event_breakpoints()
    click_ids = [
        i for ids in groups.values() for i in ids if i == "event.mouse.click"
    ]
    assert click_ids, "内核应当支持 event.mouse.click"

    debugger.set_event_breakpoints(["event.mouse.click"])
    assert debugger.active_event_breakpoints() == ["event.mouse.click"]

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return document.getElementById('demo-btn').click();"
    )

    state = debugger.wait_paused(timeout=30)
    assert state is not None, "点击事件应当触发暂停"
    assert state.is_event_breakpoint is True
    assert state.event_breakpoint == "event.mouse.click"

    debugger.set_event_breakpoints([])
    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_xhr_breakpoint_pauses_on_a_matching_request(complex_debug_page, server):
    debugger = complex_debug_page.debugger
    debugger.set_xhr_breakpoint("/", "ANY")

    outcome, worker = _trigger_in_background(
        complex_debug_page,
        "return window.fireRequest('{}');".format(server.get_url("/")),
    )

    state = debugger.wait_paused(timeout=30)
    assert state is not None, "匹配的请求应当触发暂停"
    assert state.is_xhr is True

    debugger.remove_xhr_breakpoint("/", "ANY")
    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_skip_breakpoints_disables_them_without_removing(complex_debug_page):
    debugger = complex_debug_page.debugger
    debugger.set_breakpoint(COMPLEX_SOURCE, _line_of(r"return 1;"))

    debugger.skip_breakpoints(True)
    assert complex_debug_page.run_js("return window.runFactorial(5);") == 120
    assert debugger.paused is False

    # 恢复后同一个断点应重新生效，无需重建
    debugger.skip_breakpoints(False)
    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.runFactorial(5);"
    )
    assert debugger.wait_paused(timeout=30) is not None

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_restart_frame_reruns_the_current_call(complex_debug_page):
    debugger = complex_debug_page.debugger
    debugger.set_breakpoint(COMPLEX_SOURCE, _line_of(r"let sum = 0;"))

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildCart();"
    )
    first = debugger.wait_paused(timeout=30)
    assert first is not None

    debugger.restart_frame()
    again = debugger.wait_paused(timeout=20)

    assert again is not None, "restart 后应重新停在该帧"

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)


@pytest.fixture
def probe_paused(complex_debug_page):
    """停在 inspectProbe 内部，并把 probe 对象交给测试。"""
    debugger = complex_debug_page.debugger
    debugger.set_breakpoint(COMPLEX_SOURCE, _line_of(r'const marker = "inspect-here"'))

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildProbe();"
    )
    state = debugger.wait_paused(timeout=30)
    assert state is not None

    probe = debugger.scope()["probe"]
    assert isinstance(probe, RemoteObject)

    yield debugger, probe

    debugger.clear_breakpoints()
    if debugger.paused:
        debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_entries_reads_real_map_and_set_contents(probe_paused):
    """Map/Set 的内容不是普通属性，expand() 看不到。"""
    debugger, probe = probe_paused

    map_obj = debugger.get_property(probe, "map")
    assert debugger.entries(map_obj) == {"alpha": 1, "beta": 2}

    set_obj = debugger.get_property(probe, "set")
    assert sorted(debugger.entries(set_obj)) == ["x", "y", "z"]


@pytest.mark.feature
@pytest.mark.browser
def test_invoke_getter_runs_the_accessor(probe_paused):
    debugger, probe = probe_paused

    # 默认不执行访问器，只标注
    assert debugger.expand(probe)["computed"] == "<accessor>"
    # 显式调用才拿到真实值
    assert debugger.invoke_getter(probe, "computed") == "from-getter"


@pytest.mark.feature
@pytest.mark.browser
def test_invoke_getter_surfaces_a_throwing_accessor(probe_paused):
    debugger, probe = probe_paused

    with pytest.raises(DebuggerError, match="抛出异常"):
        debugger.invoke_getter(probe, "boom")


@pytest.mark.feature
@pytest.mark.browser
def test_call_invokes_a_page_function_while_paused(probe_paused):
    """暂停时求不了任意表达式，但可以调用页面里已有的函数。"""
    debugger, probe = probe_paused

    double = debugger.get_property(probe, "double")
    assert debugger.call(double, args=[21]) == 42


@pytest.mark.feature
@pytest.mark.browser
def test_long_string_can_be_read_in_full(probe_paused):
    debugger, probe = probe_paused

    text = debugger.get_property(probe, "longText")
    assert text.endswith("…"), "超长字符串应当先给截断版"
    assert len(text) < 20000

    full = debugger.read_string(text)
    assert len(full) == 20000
    assert set(full) == {"L"}


@pytest.mark.feature
@pytest.mark.browser
def test_promise_state_is_readable(probe_paused):
    debugger, probe = probe_paused

    promise = debugger.get_property(probe, "promise")
    state = debugger.promise_state(promise)

    assert state["state"] == "fulfilled"
    assert state["value"] == 99


@pytest.mark.feature
@pytest.mark.browser
def test_log_point_reports_without_pausing(complex_debug_page):
    """日志断点只输出消息，不打断执行。"""
    debugger = complex_debug_page.debugger

    debugger.set_breakpoint(
        COMPLEX_SOURCE, _line_of(r"const amount = this\.lineTotal"), log_value="i"
    )

    # 不需要后台线程：日志断点不会阻塞
    result = complex_debug_page.run_js("return window.buildCart();")

    assert result["total"] == 32.4
    assert debugger.paused is False

    entries = debugger.wait_logs(count=3, timeout=10)
    debugger.clear_breakpoints()

    # 购物车有三行，每行命中一次，依次记录循环下标
    assert [e["values"][0] for e in entries] == [0, 1, 2]
    assert all(e["is_error"] is False for e in entries)
    assert entries[0]["url"].endswith(COMPLEX_SOURCE)


@pytest.mark.feature
@pytest.mark.browser
def test_log_point_output_is_not_visible_to_page_console(complex_debug_page):
    """日志断点不经过真实 console API，page.console 看不到。"""
    debugger = complex_debug_page.debugger
    complex_debug_page.console.start()

    debugger.set_breakpoint(
        COMPLEX_SOURCE, _line_of(r"const amount = this\.lineTotal"), log_value="i"
    )
    complex_debug_page.run_js("return window.buildCart();")
    assert debugger.wait_logs(count=3, timeout=10)

    from_page_console = complex_debug_page.console.get()
    complex_debug_page.console.stop()
    debugger.clear_breakpoints()

    assert from_page_console == []


@pytest.mark.feature
@pytest.mark.browser
def test_log_point_records_a_failing_expression(complex_debug_page):
    debugger = complex_debug_page.debugger

    debugger.set_breakpoint(
        COMPLEX_SOURCE,
        _line_of(r"const amount = this\.lineTotal"),
        log_value="nosuchvariable",
    )
    complex_debug_page.run_js("return window.buildCart();")

    entries = debugger.wait_logs(count=1, timeout=10)
    debugger.clear_breakpoints()

    assert entries[0]["is_error"] is True


@pytest.mark.feature
@pytest.mark.browser
def test_watchpoint_pauses_when_a_property_is_written(complex_debug_page):
    """CDP 没有的能力：属性被写入时暂停。"""
    debugger = complex_debug_page.debugger
    debugger.set_breakpoint(COMPLEX_SOURCE, _line_of(r'const marker = "inspect-here"'))

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildProbe();"
    )
    assert debugger.wait_paused(timeout=30) is not None

    probe = debugger.scope()["probe"]
    debugger.watch_property(probe, "hits", on="set")
    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)

    # 写入被监视的属性应当断下
    outcome, worker = _trigger_in_background(
        complex_debug_page, "return (window.probe.hits = 1);"
    )
    state = debugger.wait_paused(timeout=20)

    assert state is not None, "写入被监视的属性应当暂停"

    debugger.resume()
    worker.join(timeout=20)


@pytest.mark.feature
@pytest.mark.browser
def test_prototype_methods_are_reachable_and_callable(probe_paused):
    """类的方法挂在原型上，不是实例的自有属性。"""
    debugger, probe = probe_paused

    # double 是 probe 的自有属性
    assert debugger.call(debugger.get_property(probe, "double"), args=[21]) == 42

    # hasOwnProperty 来自 Object.prototype，要走原型链才找得到
    inherited = debugger.get_property(probe, "hasOwnProperty")
    assert isinstance(inherited, RemoteObject)
    assert debugger.get_property(probe, "hasOwnProperty", own_only=True) is None


@pytest.mark.feature
@pytest.mark.browser
def test_global_object_reaches_window(probe_paused):
    """全局绑定不出现在环境链里，scope() 看不到 window。"""
    debugger, probe = probe_paused

    window = debugger.global_object()

    assert window.class_name == "Window"
    # probe 是在 buildProbe 里挂到 window 上的
    assert debugger.get_property(window, "probe") is not None


@pytest.mark.feature
@pytest.mark.browser
def test_scope_exposes_this(complex_debug_page):
    debugger = complex_debug_page.debugger
    debugger.set_breakpoint(COMPLEX_SOURCE, _line_of(r"return sum;"))

    outcome, worker = _trigger_in_background(
        complex_debug_page, "return window.buildCart();"
    )
    assert debugger.wait_paused(timeout=30) is not None

    this_object = debugger.scope()["this"]
    assert debugger.constructor_name(this_object) == "Cart"
    assert debugger.frames()[0].this_object == this_object

    debugger.clear_breakpoints()
    debugger.resume()
    worker.join(timeout=30)


@pytest.mark.feature
@pytest.mark.browser
def test_start_without_enable_debugger_raises_actionable_error(
    test_browser_path, fixture_page_url
):
    opts = FirefoxOptions()
    opts.headless(True)
    if test_browser_path:
        opts.set_browser_path(test_browser_path)

    page = FirefoxPage(opts)
    try:
        page.get(fixture_page_url("debugger_target.html"))
        with pytest.raises(DebuggerError, match="enable_debugger"):
            page.debugger.start(port=_find_free_port())
    finally:
        page.quit()
