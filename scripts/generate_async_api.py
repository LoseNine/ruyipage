# -*- coding: utf-8 -*-
"""代码生成器：从同步类自动生成异步代理类

用法：
    python scripts/generate_async_api.py

输出：
    ruyipage/_async/_generated.py

生成规则：
    1. 公共方法 → async def method(self, ...):
           return await greenlet_spawn(self._sync.method, ...)
    2. 返回 FirefoxElement / FirefoxTab / FirefoxFrame 的方法 → 包装返回值
    3. 返回 self 的方法（链式调用）→ await 后返回 async self
    4. lazy unit 属性 → 返回 AsyncUnit 代理（缓存）
    5. I/O 属性（title, url 等）→ 生成 async get_xxx() 方法
    6. 本地属性（tab_id, browser）→ 直接转发同步属性
"""

import sys
import os
import inspect
import textwrap
import datetime

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


# ── 分类规则 ──────────────────────────────────────────────────────────────

# 返回值需要包装为异步代理的类型
RETURN_WRAPPER_MAP = {
    "FirefoxElement": "AsyncFirefoxElement",
    "NoneElement": "AsyncNoneElement",
    "FirefoxTab": "AsyncFirefoxTab",
    "FirefoxFrame": "AsyncFirefoxFrame",
}

# Unit 属性 → 需要返回异步代理
UNIT_PROPERTIES = {
    "scroll", "actions", "touch", "wait", "listen", "rect", "states",
    "set", "local_storage", "session_storage", "console", "intercept",
    "network", "window", "browser_tools", "contexts", "emulation",
    "extensions", "downloads", "events", "navigation", "prefs",
    "realms", "config",
}

# 需要 I/O 的属性 → 生成 async get_xxx() 方法
IO_PROPERTIES = {
    "title", "url", "html", "user_agent", "ready_state", "cookies",
}

# 纯本地属性 → 直接转发
LOCAL_PROPERTIES = {
    "browser", "tab_id",
}

# 返回 self 的方法（链式调用）→ 异步版返回 async self
CHAINABLE_METHODS = {
    "get", "back", "forward", "refresh", "stop_loading", "wait_loading",
    "set_viewport", "set_useragent", "set_bypass_csp",
    "set_geolocation", "set_timezone", "set_locale",
    "set_screen_orientation", "set_cache_behavior", "set_download_path",
    # Element chainable methods
    "click_self", "right_click", "double_click", "input", "clear",
    "hover", "drag_to", "focus",
    # Tab
    "activate",
}

# 返回 Element 的方法
ELEMENT_RETURNING = {
    "ele": "single",
    "s_ele": "single_static",
    "parent": "single",
    "child": "single",
    "next": "single",
    "prev": "single",
}

LIST_ELEMENT_RETURNING = {
    "eles": "element",
    "s_eles": "static",
    "children": "element",
}

# 返回 Tab 的方法
TAB_RETURNING = {"new_tab", "get_tab", "latest_tab"}
TAB_LIST_RETURNING = {"get_tabs"}

# 返回 Frame 的方法
FRAME_RETURNING = {"get_frame"}
FRAME_LIST_RETURNING = {"get_frames"}

# 跳过的方法（由 _overrides.py 手写提供）
SKIP_METHODS = {"with_frame", "with_shadow", "__call__", "__repr__", "__str__"}

# 跳过的属性
SKIP_PROPERTIES = set()


def _get_signature_str(method, skip_self=True):
    """从方法获取参数签名字符串"""
    try:
        sig = inspect.signature(method)
    except (ValueError, TypeError):
        return None, None

    params = []
    call_args = []

    for name, param in sig.parameters.items():
        if name == "self" and skip_self:
            params.append("self")
            continue

        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            params.append("*{}".format(name))
            call_args.append("*{}".format(name))
        elif param.kind == inspect.Parameter.VAR_KEYWORD:
            params.append("**{}".format(name))
            call_args.append("**{}".format(name))
        elif param.kind == inspect.Parameter.KEYWORD_ONLY:
            if param.default is inspect.Parameter.empty:
                params.append(name)
            else:
                params.append("{}={}".format(name, repr(param.default)))
            call_args.append("{}={}".format(name, name))
        else:
            if param.default is inspect.Parameter.empty:
                params.append(name)
                call_args.append(name)
            else:
                params.append("{}={}".format(name, repr(param.default)))
                call_args.append("{}={}".format(name, name))

    return ", ".join(params), ", ".join(call_args)


def generate_method(name, method, parent_class_name):
    """生成单个异步代理方法的代码"""
    if name in SKIP_METHODS:
        return None
    if name.startswith("_"):
        return None

    params_str, call_args = _get_signature_str(method)
    if params_str is None:
        return None

    lines = []

    if name in CHAINABLE_METHODS:
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        await greenlet_spawn(self._sync.{}, {})".format(name, call_args)
        )
        lines.append("        return self")
    elif name in ELEMENT_RETURNING:
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        _r = await greenlet_spawn(self._sync.{}, {})".format(
                name, call_args
            )
        )
        if ELEMENT_RETURNING[name] == "single_static":
            lines.append(
                "        return _r  # StaticElement, no async wrapper needed"
            )
        else:
            lines.append(
                "        return AsyncFirefoxElement(_r) if _r else AsyncNoneElement(_r)"
            )
    elif name in LIST_ELEMENT_RETURNING:
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        _r = await greenlet_spawn(self._sync.{}, {})".format(
                name, call_args
            )
        )
        if LIST_ELEMENT_RETURNING[name] == "static":
            lines.append("        return _r  # list[StaticElement]")
        else:
            lines.append(
                "        return [AsyncFirefoxElement(e) for e in _r]"
            )
    elif name in TAB_RETURNING:
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        _r = await greenlet_spawn(self._sync.{}, {})".format(
                name, call_args
            )
        )
        lines.append("        return AsyncFirefoxTab(_r)")
    elif name in TAB_LIST_RETURNING:
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        _r = await greenlet_spawn(self._sync.{}, {})".format(
                name, call_args
            )
        )
        lines.append("        return [AsyncFirefoxTab(t) for t in _r]")
    elif name in FRAME_RETURNING:
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        _r = await greenlet_spawn(self._sync.{}, {})".format(
                name, call_args
            )
        )
        lines.append("        return AsyncFirefoxFrame(_r) if _r else _r")
    elif name in FRAME_LIST_RETURNING:
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        _r = await greenlet_spawn(self._sync.{}, {})".format(
                name, call_args
            )
        )
        lines.append("        return [AsyncFirefoxFrame(f) for f in _r]")
    else:
        # 普通方法
        lines.append("    async def {}({}):".format(name, params_str))
        lines.append(
            "        return await greenlet_spawn(self._sync.{}, {})".format(
                name, call_args
            )
        )

    return "\n".join(lines)


def generate_property_getter(name):
    """为 I/O 属性生成 async get_xxx() 方法"""
    return (
        "    async def get_{name}(self):\n"
        "        return await greenlet_spawn(lambda: self._sync.{name})"
    ).format(name=name)


def generate_local_property(name):
    """为本地属性生成直接转发的 @property"""
    return (
        "    @property\n"
        "    def {name}(self):\n"
        "        return self._sync.{name}"
    ).format(name=name)


def generate_unit_property(name):
    """为 unit 属性生成返回 AsyncUnit 代理的 @property"""
    return (
        "    @property\n"
        "    def {name}(self):\n"
        '        if "{name}" not in self._unit_cache:\n'
        "            self._unit_cache[\"{name}\"] = AsyncUnitProxy(self._sync.{name})\n"
        '        return self._unit_cache["{name}"]'
    ).format(name=name)


def generate_class(cls, class_name, base_class, mixin_class=None,
                   extra_io_props=None, extra_unit_props=None,
                   extra_local_props=None):
    """生成一个完整的异步代理类

    Args:
        cls: 同步源类
        class_name: 生成的异步类名
        base_class: 忽略（保留参数兼容）
        mixin_class: 混入类名（字符串）
        extra_io_props: 此类额外的 I/O 属性集合
        extra_unit_props: 此类额外的 Unit 属性集合
        extra_local_props: 此类额外的本地属性集合
    """
    all_io = IO_PROPERTIES | (extra_io_props or set())
    all_unit = UNIT_PROPERTIES | (extra_unit_props or set())
    all_local = LOCAL_PROPERTIES | (extra_local_props or set())
    all_known = all_io | all_unit | all_local

    lines = []

    # 类声明
    bases = []
    if mixin_class:
        bases.append(mixin_class)
    lines.append(
        "class {}({}):".format(class_name, ", ".join(bases) if bases else "object")
    )
    lines.append('    """{}的异步代理"""'.format(cls.__name__))
    lines.append("")
    lines.append("    def __init__(self, sync_obj):")
    lines.append("        self._sync = sync_obj")
    lines.append("        self._unit_cache = {}")
    lines.append("")

    # 本地属性
    for name in sorted(all_local):
        if hasattr(cls, name):
            lines.append(generate_local_property(name))
            lines.append("")

    # I/O 属性 → async get_xxx()
    for name in sorted(all_io):
        if hasattr(cls, name):
            lines.append(generate_property_getter(name))
            lines.append("")

    # Unit 属性
    for name in sorted(all_unit):
        if hasattr(cls, name):
            lines.append(generate_unit_property(name))
            lines.append("")

    # 自动检测未分类的 property → 当作 I/O 属性处理
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        if name in SKIP_METHODS or name in SKIP_PROPERTIES:
            continue
        if name in all_known:
            continue
        raw = inspect.getattr_static(cls, name, None)
        if isinstance(raw, property):
            lines.append(generate_property_getter(name))
            lines.append("")

    # 公共方法
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        if name in SKIP_METHODS or name in SKIP_PROPERTIES:
            continue
        if name in all_known:
            continue

        attr = getattr(cls, name, None)
        if attr is None:
            continue
        raw = inspect.getattr_static(cls, name, None)
        if isinstance(raw, property):
            continue  # 已在上面处理
        if not callable(attr):
            continue

        method_code = generate_method(name, attr, cls.__name__)
        if method_code:
            lines.append(method_code)
            lines.append("")

    return "\n".join(lines)


def generate_unit_proxy():
    """生成通用的 AsyncUnitProxy 类"""
    return '''
class AsyncUnitProxy:
    """通用异步 Unit 代理

    包装任何 unit 对象（Actions, Interceptor, Listener 等），
    将所有公共方法自动包装为异步版本。
    """

    def __init__(self, sync_unit):
        self._sync = sync_unit

    def __getattr__(self, name):
        if name.startswith("_"):
            return getattr(self._sync, name)

        attr = getattr(self._sync, name)

        if callable(attr):
            async def _async_method(*args, **kwargs):
                return await greenlet_spawn(attr, *args, **kwargs)
            _async_method.__name__ = name
            _async_method.__qualname__ = "AsyncUnitProxy.{}".format(name)
            return _async_method

        # 非 callable 属性（如 .active, .listening）直接返回
        return attr

    async def __call__(self, *args, **kwargs):
        """支持可调用的 unit（如 PageWaiter.__call__、ElementWaiter.__call__）"""
        return await greenlet_spawn(self._sync, *args, **kwargs)

    def __repr__(self):
        return "<Async{}>".format(repr(self._sync))
'''


def generate_none_element():
    """生成 AsyncNoneElement"""
    return '''
class AsyncNoneElement:
    """NoneElement 的异步对应 —— 空元素的 null 对象"""

    def __init__(self, sync_obj=None):
        self._sync = sync_obj

    def __bool__(self):
        return False

    def __repr__(self):
        return "<AsyncNoneElement>"

    def __str__(self):
        return self.__repr__()

    async def __getattr__(self, name):
        return None
'''


def main():
    """主函数：生成 _async/_generated.py"""
    # 导入同步类
    from ruyipage._pages.firefox_base import FirefoxBase
    from ruyipage._pages.firefox_page import FirefoxPage
    from ruyipage._pages.firefox_tab import FirefoxTab
    from ruyipage._pages.firefox_frame import FirefoxFrame
    from ruyipage._elements.firefox_element import FirefoxElement

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    header = '''# -*- coding: utf-8 -*-
# ┌──────────────────────────────────────────────────────────────────┐
# │ WARNING: 此文件由 scripts/generate_async_api.py 自动生成          │
# │ 请勿手动编辑！修改后请重新运行生成器：                               │
# │   python scripts/generate_async_api.py                          │
# │ 生成时间: {now}                                        │
# └──────────────────────────────────────────────────────────────────┘

from .greenlet_bridge import greenlet_spawn
from ._overrides import AsyncFirefoxBaseMixin, AsyncFirefoxElementMixin

'''.format(now=now)

    parts = [header]

    # AsyncUnitProxy（通用 unit 代理）
    parts.append(generate_unit_proxy())
    parts.append("\n")

    # AsyncNoneElement
    parts.append(generate_none_element())
    parts.append("\n")

    # AsyncFirefoxBase（FirefoxBase 代理）
    parts.append(
        generate_class(
            FirefoxBase,
            "AsyncFirefoxBase",
            "AsyncFirefoxBaseMixin",
            mixin_class="AsyncFirefoxBaseMixin",
        )
    )
    parts.append("\n")

    # AsyncFirefoxPage（继承 AsyncFirefoxBase，追加 Page 特有方法）
    page_extra = []
    for name in sorted(dir(FirefoxPage)):
        if name.startswith("_"):
            continue
        if name in SKIP_METHODS:
            continue
        # 只处理 FirefoxPage 自身定义的（不在 FirefoxBase 中的）
        if hasattr(FirefoxBase, name):
            continue
        attr = getattr(FirefoxPage, name, None)
        if attr is None:
            continue
        raw = inspect.getattr_static(FirefoxPage, name, None)
        if isinstance(raw, property):
            # Page 特有属性
            if name in TAB_RETURNING:
                # latest_tab → 返回 AsyncFirefoxTab
                page_extra.append(
                    "    async def get_{name}(self):\n"
                    "        _r = await greenlet_spawn(lambda: self._sync.{name})\n"
                    "        return AsyncFirefoxTab(_r) if _r else _r".format(name=name)
                )
            else:
                # tab_ids, tabs_count 等 I/O 属性
                page_extra.append(generate_property_getter(name))
            continue
        if not callable(attr):
            continue
        method_code = generate_method(name, attr, "FirefoxPage")
        if method_code:
            page_extra.append(method_code)

    parts.append("class AsyncFirefoxPage(AsyncFirefoxBase):")
    parts.append('    """FirefoxPage 的异步代理"""')
    parts.append("")
    if page_extra:
        parts.append("\n\n".join(page_extra))
    else:
        parts.append("    pass")
    parts.append("\n\n")

    # AsyncFirefoxTab
    tab_extra_methods = []
    for name in sorted(dir(FirefoxTab)):
        if name.startswith("_"):
            continue
        if name in SKIP_METHODS:
            continue
        if hasattr(FirefoxBase, name):
            continue
        attr = getattr(FirefoxTab, name, None)
        if attr is None or isinstance(attr, property):
            continue
        if not callable(attr):
            continue
        method_code = generate_method(name, attr, "FirefoxTab")
        if method_code:
            tab_extra_methods.append(method_code)

    parts.append("class AsyncFirefoxTab(AsyncFirefoxBase):")
    parts.append('    """FirefoxTab 的异步代理"""')
    parts.append("")
    if tab_extra_methods:
        parts.append("\n\n".join(tab_extra_methods))
    else:
        parts.append("    pass")
    parts.append("\n\n")

    # AsyncFirefoxFrame
    frame_extra = []
    for name in sorted(dir(FirefoxFrame)):
        if name.startswith("_"):
            continue
        if name in SKIP_METHODS:
            continue
        if hasattr(FirefoxBase, name):
            continue
        attr = getattr(FirefoxFrame, name, None)
        if attr is None:
            continue
        if isinstance(attr, property):
            # Frame 特有属性（parent, is_cross_origin）
            frame_extra.append(generate_property_getter(name))
            continue
        if callable(attr):
            method_code = generate_method(name, attr, "FirefoxFrame")
            if method_code:
                frame_extra.append(method_code)

    parts.append("class AsyncFirefoxFrame(AsyncFirefoxBase):")
    parts.append('    """FirefoxFrame 的异步代理"""')
    parts.append("")
    if frame_extra:
        parts.append("\n\n".join(frame_extra))
    else:
        parts.append("    pass")
    parts.append("\n\n")

    # AsyncFirefoxElement
    # Element 的 click 和 select 是 lazy unit 属性
    element_extra_units = {"click", "select"}
    parts.append(
        generate_class(
            FirefoxElement,
            "AsyncFirefoxElement",
            "AsyncFirefoxElementMixin",
            mixin_class="AsyncFirefoxElementMixin",
            extra_unit_props=element_extra_units,
        )
    )

    # 写入文件
    output = "\n".join(parts)
    output_path = os.path.join(
        PROJECT_ROOT, "ruyipage", "_async", "_generated.py"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)

    print("Generated: {}".format(output_path))
    print("Done.")


if __name__ == "__main__":
    main()
