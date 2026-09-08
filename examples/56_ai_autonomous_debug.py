# -*- coding: utf-8 -*-
"""AI 自主调试闭环

只给页面地址。源码在哪、函数叫什么、该在哪一行下断、点击走进了谁、
异常停在哪——全部由调试器自己发现。这是代理排查未知页面时该走的流程。

    python examples/56_ai_autonomous_debug.py

JS 暂停期间任何依赖它的 BiDi 调用都会阻塞，触发断点的调用必须放后台线程。
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


def bg(page, script):
    def run():
        try:
            page.run_js(script, timeout=120)
        except Exception:
            pass

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


def finish(dbg, worker):
    dbg.clear_breakpoints()
    if dbg.paused:
        dbg.resume()
    worker.join(timeout=30)


def discover_source(dbg):
    """1. 有哪些 JS，代码长什么样，入口函数叫什么。"""
    sources = [s for s in dbg.sources() if (s.url or "").endswith(".js")]
    print("1. sources():", [s.url.rsplit("/", 1)[-1] for s in sources])
    target = sources[0]
    code = dbg.source_text(target)
    lines = code.splitlines()
    print("   source_text(): {} 行".format(len(lines)))

    entries = re.findall(r"window\.(\w+)\s*=\s*function", code)
    print("   发现入口:", ["window." + n for n in entries])
    return target, code, lines, entries


def pick_breakpoint(dbg, target, lines):
    """2. 在业务函数体里挑一个真正能停下的行。"""
    breakable = set(dbg.breakable_lines(target.url))
    print("2. breakable_lines() 共 {} 处".format(len(breakable)))

    # 找循环里真正干活的那一行，停在那里作用域更有内容
    body_line = next(
        i + 1 for i, text in enumerate(lines)
        if "const amount" in text and (i + 1) in breakable
    )
    print("   选定第 {} 行: {}".format(body_line, lines[body_line - 1].strip()))
    dbg.set_breakpoint(target.url, body_line)
    return body_line


def inspect_pause(dbg, page, entry):
    """3. 命中后看栈、this、对象内容。"""
    worker = bg(page, "return window.{}();".format(entry))
    state = dbg.wait_paused(timeout=30)
    print("3. wait_paused(): why={} line={}".format(state.why, state.line))
    print("   frames():", " <- ".join(f.display_name for f in dbg.frames()))

    scope = dbg.scope()
    this = scope.get("this")
    print("   scope() 键:", [k for k in scope if k != "arguments"])
    if this is not None:
        print("   constructor_name(this):", dbg.constructor_name(this))
        items = dbg.get_property(this, "items")
        print("   expand(items):", dbg.expand(items, depth=2))
        index = dbg.get_property(this, "index")
        if index is not None:
            print("   entries(index Map):", dbg.entries(index))

    before = dict(scope)
    dbg.step_over()
    dbg.wait_paused(timeout=20)
    after = dbg.scope()
    changed = {k: after[k] for k in after if before.get(k) != after[k]}
    print("   step_over() 后变化:", changed)
    finish(dbg, worker)


def find_throw_site(dbg, page):
    """4. 不知道抛错位置：让异常自己把现场交出来。"""
    dbg.pause_on_exceptions(True, ignore_caught=True)
    worker = bg(page, "return window.crash();")
    state = dbg.wait_paused(timeout=30)
    print("4. pause_on_exceptions(): is_exception={}".format(state.is_exception))
    print("   exception:", state.exception)
    print("   抛出点:", " <- ".join(f.display_name for f in dbg.frames()[:4]))
    snippet = dbg.source_text(state.url).splitlines()[state.line - 1].strip()
    print("   那一行:", snippet)
    dbg.pause_on_exceptions(False)
    finish(dbg, worker)


def find_click_handler(dbg, page):
    """5. 不知道点击走进了谁：按事件下断，而不是按文件行号。"""
    ids = [
        i for group in dbg.available_event_breakpoints().values() for i in group
        if i == "event.mouse.click"
    ]
    print("5. available_event_breakpoints() 含 click:", bool(ids))
    dbg.set_event_breakpoints(["event.mouse.click"])

    worker = bg(page, "return document.getElementById('demo-btn').click();")
    state = dbg.wait_paused(timeout=30)
    print("   is_event_breakpoint={} id={}".format(
        state.is_event_breakpoint, state.event_breakpoint
    ))
    print("   处理器在:", [
        "{}:{}".format(f.display_name, f.line) for f in dbg.frames()[:3]
    ])
    dbg.set_event_breakpoints([])
    finish(dbg, worker)


def main():
    opts = FirefoxOptions()
    opts.headless(True)
    opts.set_browser_path(BROWSER_PATH)
    opts.enable_debugger()
    page = FirefoxPage(opts)
    try:
        page.get(PAGE)
        dbg = page.debugger.start(auto_resume_after=60)

        target, code, lines, entries = discover_source(dbg)
        pick_breakpoint(dbg, target, lines)
        inspect_pause(dbg, page, "buildCart" if "buildCart" in entries else entries[0])
        find_throw_site(dbg, page)
        find_click_handler(dbg, page)
    finally:
        try:
            page.debugger.stop()
        except Exception:
            pass
        page.quit()


if __name__ == "__main__":
    main()
