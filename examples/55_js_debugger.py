# -*- coding: utf-8 -*-
"""JS 断点调试：page.debugger 常用 API

断点、单步、调用栈、作用域——这些不在 WebDriver BiDi 里，Firefox 也已
去掉 CDP。ruyiPage 走 Firefox 自己的 DevTools 通道，与 BiDi 并行工作。

运行前先改 BROWSER_PATH：

    python examples/55_js_debugger.py

JS 暂停期间，run_js / 点击 / 取文本都会阻塞到超时，所以触发断点的调用
必须放后台线程。日志断点不暂停，不需要。
"""

import pathlib
import re
import threading

from ruyipage import FirefoxOptions, FirefoxPage

BROWSER_PATH = r"D:\firefox\src\firefox\obj-jscall-check\dist\bin\firefox.exe"
PAGE = (
    pathlib.Path(__file__).parent.parent
    / "tests" / "fixtures" / "pages" / "debugger_complex.html"
).resolve().as_uri()
SOURCE = "debugger_complex.js"


def line_of(page, pattern):
    """按源码内容定位行号，避免把数字写死。"""
    text = page.debugger.source_text(SOURCE)
    for number, content in enumerate(text.splitlines(), start=1):
        if re.search(pattern, content):
            return number
    raise RuntimeError("源码里找不到: {}".format(pattern))


def bg(page, script):
    """命中断点后 run_js 会卡住，触发动作必须放后台。

    异常暂停那类脚本恢复后会把错误抛回 Python，这里吞掉以免打乱示例输出。
    """
    def run():
        try:
            page.run_js(script, timeout=120)
        except Exception:
            pass

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


def local_http_url():
    """file:// 页面 fetch 本地文件通常发不出去，起一个最小 HTTP 服务给 XHR 断点用。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:{}/".format(server.server_address[1])


def finish(page, worker):
    page.debugger.clear_breakpoints()
    if page.debugger.paused:
        page.debugger.resume()
    worker.join(timeout=30)


def demo_source(page):
    print("\n=== 源码 ===")
    dbg = page.debugger
    for src in dbg.sources(url_contains=SOURCE):
        print("  sources():", src.url)
    text = dbg.source_text(SOURCE)
    print("  source_text(): {} 字符, 首行 = {!r}".format(
        len(text), text.splitlines()[0]
    ))
    print("  breakable_lines():", dbg.breakable_lines(SOURCE)[:8], "...")


def demo_breakpoint_and_step(page):
    print("\n=== 断点 / 条件断点 / 单步 / 调用栈 / 作用域 ===")
    dbg = page.debugger
    loop = line_of(page, r"const amount = this\.lineTotal")

    # 条件断点：只在第一轮循环停下
    bp = dbg.set_breakpoint(SOURCE, loop, condition="i === 0")
    print("  set_breakpoint():", bp)
    print("  breakpoints():", dbg.breakpoints)

    worker = bg(page, "return window.buildCart();")
    state = dbg.wait_paused(timeout=30)
    print("  wait_paused(): why={} line={}".format(state.why, state.line))

    print("  frames():")
    for frame in dbg.frames():
        print("    {}  this={}  @ {}:{}".format(
            frame.display_name, frame.this_object, frame.url, frame.line
        ))

    scope = dbg.scope()  # 当前块 + 所属函数（含 this、参数）
    print("  scope(): i={} line={!r}".format(
        scope.get("i"), getattr(scope.get("line"), "value", scope.get("line"))
    ))
    print("  constructor_name(this):", dbg.constructor_name(scope["this"]))
    print("  scope(include_parents=True) 键:", list(dbg.scope(include_parents=True)))

    dbg.step_into()
    print("  step_into():", dbg.wait_paused(timeout=20).frame.display_name)

    dbg.step_out()
    print("  step_out(): 回到", dbg.wait_paused(timeout=20).frame.display_name)

    dbg.step_over()
    stepped = dbg.wait_paused(timeout=20)
    print("  step_over(): line={} amount={}".format(
        stepped.line, dbg.scope().get("amount")
    ))

    dbg.restart_frame()
    again = dbg.wait_paused(timeout=20)
    print("  restart_frame(): 重新停在 line={}".format(again.line))

    dbg.remove_breakpoint(bp)
    finish(page, worker)


def demo_log_point(page):
    print("\n=== 日志断点（不暂停） ===")
    dbg = page.debugger
    dbg.set_breakpoint(
        SOURCE, line_of(page, r"const amount = this\.lineTotal"), log_value="i, sum"
    )
    page.run_js("return window.buildCart();")  # 不会卡住
    for entry in dbg.wait_logs(count=3, timeout=10):
        print("  wait_logs(): line={} values={}".format(entry["line"], entry["values"]))
    dbg.clear_breakpoints()


def demo_inspect(page):
    print("\n=== 暂停时看对象 ===")
    dbg = page.debugger
    dbg.set_breakpoint(SOURCE, line_of(page, r'const marker = "inspect-here"'))
    worker = bg(page, "return window.buildProbe();")
    dbg.wait_paused(timeout=30)

    probe = dbg.scope()["probe"]
    expanded = dbg.expand(probe, depth=1, max_items=20)
    print("  constructor_name():", dbg.constructor_name(probe),
          "(普通对象；类实例见上一步的 Cart)")
    print("  expand() 键:", list(expanded))
    print("  expand()['computed']:", expanded.get("computed"),
          "  # 访问器默认不执行")
    print("  get_property('double'):", dbg.get_property(probe, "double"))
    print("  entries(map):", dbg.entries(dbg.get_property(probe, "map")))
    print("  entries(set):", dbg.entries(dbg.get_property(probe, "set")))
    print("  call(double, 21):", dbg.call(dbg.get_property(probe, "double"), args=[21]))
    print("  invoke_getter('computed'):", dbg.invoke_getter(probe, "computed"))
    print("  promise_state():", dbg.promise_state(dbg.get_property(probe, "promise")))

    long_text = dbg.get_property(probe, "longText")
    print("  截断长串: len={} {!r}".format(len(long_text), long_text[:12]))
    print("  read_string() 完整长度:", len(dbg.read_string(long_text)))

    window = dbg.global_object()
    print("  global_object():", window.class_name)

    finish(page, worker)


def demo_watchpoint(page):
    print("\n=== 属性监视点（谁改了这个值） ===")
    dbg = page.debugger
    page.run_js("window.config = { token: 'initial' };")
    dbg.set_breakpoint(SOURCE, line_of(page, r"return sum;"))
    worker = bg(page, "return window.buildCart();")
    dbg.wait_paused(timeout=30)

    config = dbg.get_property(dbg.global_object(), "config")
    dbg.watch_property(config, "token", on="set")
    finish(page, worker)

    worker = bg(page, "window.config.token = 'changed';")
    state = dbg.wait_paused(timeout=20)
    print("  watch_property(): why={}".format(state.why if state else None))
    dbg.unwatch_property(config)
    finish(page, worker)


def demo_exceptions(page):
    print("\n=== 异常时自动暂停 ===")
    dbg = page.debugger
    dbg.pause_on_exceptions(True, ignore_caught=True)

    worker = bg(page, "return window.runParse('not-json');")
    missed = dbg.wait_paused(timeout=6)
    worker.join(timeout=30)
    print("  被捕获的异常: {}".format("不停" if missed is None else "误停了"))

    worker = bg(page, "return window.crash();")
    state = dbg.wait_paused(timeout=30)
    print("  未捕获: is_exception={} exception={}".format(
        state.is_exception, state.exception
    ))
    dbg.pause_on_exceptions(False)
    finish(page, worker)

    dbg.pause_on_debugger_statement(True)
    worker = bg(page, "(function () { debugger; return 1; })()")
    state = dbg.wait_paused(timeout=20)
    print("  pause_on_debugger_statement(): why={}".format(
        None if state is None else state.why
    ))
    dbg.pause_on_debugger_statement(False)
    finish(page, worker)


def demo_event_and_xhr(page):
    print("\n=== 事件断点 / XHR 断点（不用先知道源码位置） ===")
    dbg = page.debugger

    groups = dbg.available_event_breakpoints()
    print("  available_event_breakpoints() 分组:", list(groups)[:4], "...")
    dbg.set_event_breakpoints(["event.mouse.click"])
    print("  active_event_breakpoints():", dbg.active_event_breakpoints())

    worker = bg(page, "return document.getElementById('demo-btn').click();")
    state = dbg.wait_paused(timeout=30)
    print("  点击: is_event_breakpoint={} handler={}".format(
        state.is_event_breakpoint,
        [f.display_name for f in dbg.frames()[:3]],
    ))
    dbg.set_event_breakpoints([])
    finish(page, worker)

    dbg.set_xhr_breakpoint("/", "ANY")
    url = local_http_url()
    worker = bg(page, "return window.fireRequest({!r});".format(url))
    state = dbg.wait_paused(timeout=20)
    print("  set_xhr_breakpoint: is_xhr={}".format(
        None if state is None else state.is_xhr
    ))
    dbg.remove_xhr_breakpoint("/", "ANY")
    finish(page, worker)


def demo_control(page):
    print("\n=== 跳过断点 / 黑盒 / 异步栈 ===")
    dbg = page.debugger
    bp = dbg.set_breakpoint(SOURCE, line_of(page, r"return 1;"))

    dbg.skip_breakpoints(True)
    print("  skip_breakpoints(True): factorial =",
          page.run_js("return window.runFactorial(5);"))
    dbg.skip_breakpoints(False)

    worker = bg(page, "return window.runFactorial(5);")
    print("  skip_breakpoints(False) 后重新命中:",
          dbg.wait_paused(timeout=20) is not None)
    dbg.remove_breakpoint(bp)
    finish(page, worker)

    dbg.blackbox(SOURCE)
    print("  blackbox():", dbg.blackboxed())
    dbg.unblackbox(SOURCE)
    print("  unblackbox():", dbg.blackboxed())

    dbg.include_async_frames(True)
    dbg.set_breakpoint(SOURCE, line_of(page, r"const doubled = value \* 2"))
    worker = bg(page, "return window.runAsync(7);")
    dbg.wait_paused(timeout=30)
    print("  include_async_frames():",
          [(f.display_name, f.type) for f in dbg.frames()])
    finish(page, worker)


def main():
    opts = FirefoxOptions()
    opts.set_browser_path(BROWSER_PATH)
    opts.enable_debugger()
    page = FirefoxPage(opts)
    try:
        page.get(PAGE)
        page.debugger.start(auto_resume_after=90)

        demo_source(page)
        demo_breakpoint_and_step(page)
        demo_log_point(page)
        demo_inspect(page)
        demo_watchpoint(page)
        demo_exceptions(page)
        demo_event_and_xhr(page)
        demo_control(page)
    finally:
        page.debugger.stop()
        page.quit()


if __name__ == "__main__":
    main()
