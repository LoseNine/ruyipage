# -*- coding: utf-8 -*-
"""FirefoxElement - DOM 元素封装

基于 BiDi 的 sharedId 引用 DOM 节点，通过 script.callFunction 访问属性/方法。
"""

import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .._base.base import BaseElement
from .._functions.bidi_values import parse_value, make_shared_ref
from .._functions.keys import Keys
from .._functions.sleep import sleep as _sleep
from .._bidi import script as bidi_script
from ..errors import (
    ElementLostError,
    JavaScriptError,
    CanNotClickError,
    NoRectError,
    ElementNotFoundError,
    BiDiError,
)
from .._functions.settings import Settings

logger = logging.getLogger("ruyipage")


if TYPE_CHECKING:
    from .none_element import NoneElement
    from .static_element import StaticElement
    from .._units.selector import SelectElement
    from .._units.clicker import Clicker
    from .._units.scroller import ElementScroller
    from .._units.rect import ElementRect
    from .._units.setter import ElementSetter
    from .._units.states import ElementStates
    from .._units.waiter import ElementWaiter


def _is_node_lost_error(err_text):
    """Check if an error message indicates the node reference is stale."""
    lower = err_text.lower() if err_text else ""
    return "no such node" in lower or "stale" in lower


class FirefoxElement(BaseElement):
    """Firefox DOM 元素

    通过 page.ele() 或 page.eles() 获取，不要直接实例化。
    """

    _type = "FirefoxElement"

    def __init__(
        self, owner, shared_id, handle=None, node_info=None, locator_info=None
    ):
        """
        Args:
            owner: 所属的 FirefoxBase (page/tab/frame)
            shared_id: BiDi sharedId
            handle: BiDi handle（可选）
            node_info: 节点信息字典（可选）
            locator_info: 用于重新定位的原始定位器信息（可选）
        """
        self._owner = owner
        self._shared_id = shared_id
        self._handle = handle
        self._node_info = node_info or {}
        self._locator_info = locator_info

        # 惰性 units
        self._clicker = None
        self._scroll = None
        self._rect_unit = None
        self._setter = None
        self._states_unit = None
        self._wait_unit = None
        self._select = None

    @classmethod
    def _from_node(cls, owner, node_data, locator_info=None):
        """从 BiDi 节点数据创建元素

        Args:
            owner: 所属页面
            node_data: BiDi 返回的节点 RemoteValue
            locator_info: 原始定位器（用于 _refresh_id）

        Returns:
            FirefoxElement 或 None
        """
        if not isinstance(node_data, dict):
            return None

        node_type = node_data.get("type", "")

        # 处理 'node' 类型
        if node_type == "node":
            shared_id = node_data.get("sharedId", "")
            handle = node_data.get("handle")
            value = node_data.get("value", {})
            if shared_id:
                return cls(owner, shared_id, handle, value, locator_info=locator_info)

        # 直接含有 sharedId 的对象
        shared_id = node_data.get("sharedId", "")
        if shared_id:
            handle = node_data.get("handle")
            value = node_data.get("value", {})
            return cls(owner, shared_id, handle, value, locator_info=locator_info)

        return None

    # ===== __call__ 快捷方式 =====

    def __call__(
        self, locator, index=1, timeout=None
    ) -> "FirefoxElement | NoneElement":
        """快捷查找子元素: ele('locator') 等价于 ele.ele('locator')

        Args:
            locator: 定位器
            index: 索引（从1开始）
            timeout: 超时

        Returns:
            FirefoxElement 或 NoneElement
        """
        return self.ele(locator, index=index, timeout=timeout)

    # ===== 核心属性 =====

    @property
    def tag(self) -> str:
        """标签名（小写）"""
        # 优先从缓存获取
        name = self._node_info.get("localName", "")
        if name:
            return name.lower()
        return (self._run_safe("(el) => el.tagName.toLowerCase()") or "").lower()

    @property
    def text(self) -> str:
        """元素文本内容。

        Returns:
            str: 元素的 ``textContent`` 文本。

        适用场景：
            - 读取标题、按钮文本、搜索结果摘要
            - 例如 ``title_ele.text``、``item.text``
        """
        return self._run_safe("(el) => el.textContent") or ""

    @property
    def inner_html(self) -> str:
        """内部 HTML"""
        return self._run_safe("(el) => el.innerHTML") or ""

    @property
    def html(self) -> str:
        """外部 HTML（outerHTML）"""
        return self._run_safe("(el) => el.outerHTML") or ""

    @property
    def outer_html(self) -> str:
        """外部 HTML（别名）"""
        return self.html

    @property
    def value(self) -> "str | None":
        """输入元素的值"""
        return self._run_safe("(el) => el.value")

    @property
    def attrs(self) -> dict:
        """所有属性字典"""
        # 先从缓存的 node_info 获取
        cached_attrs = self._node_info.get("attributes", {})
        if cached_attrs:
            return dict(cached_attrs) if isinstance(cached_attrs, dict) else {}

        result = self._run_safe("""(el) => {
            const attrs = {};
            for (let i = 0; i < el.attributes.length; i++) {
                const a = el.attributes[i];
                attrs[a.name] = a.value;
            }
            return attrs;
        }""")
        return result if isinstance(result, dict) else {}

    @property
    def link(self) -> str:
        """链接地址（href 属性）"""
        href = self.attr("href")
        if href:
            # 转换为绝对 URL
            return self._run_safe("(el) => el.href") or href
        return ""

    @property
    def src(self) -> str:
        """资源地址（src 属性）"""
        src = self.attr("src")
        if src:
            return self._run_safe("(el) => el.src") or src
        return ""

    @property
    def is_displayed(self) -> bool:
        """元素是否可见"""
        return (
            self._run_safe("""(el) => {
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0'
                && el.offsetWidth > 0 && el.offsetHeight > 0;
        }""")
            or False
        )

    @property
    def is_enabled(self) -> bool:
        """元素是否可用"""
        return not (self._run_safe("(el) => el.disabled") or False)

    @property
    def is_checked(self) -> bool:
        """复选框/单选框是否选中"""
        return self._run_safe("(el) => el.checked") or False

    @property
    def size(self) -> dict:
        """元素尺寸 {'width': int, 'height': int}"""
        return self._run_safe("""(el) => {
            const r = el.getBoundingClientRect();
            return {width: Math.round(r.width), height: Math.round(r.height)};
        }""") or {"width": 0, "height": 0}

    @property
    def location(self) -> dict:
        """元素位置 {'x': int, 'y': int}"""
        return self._run_safe("""(el) => {
            const r = el.getBoundingClientRect();
            return {x: Math.round(r.x), y: Math.round(r.y)};
        }""") or {"x": 0, "y": 0}

    @property
    def pseudo(self) -> dict:
        """伪元素信息"""
        return {
            "before": self.style("content", "::before"),
            "after": self.style("content", "::after"),
        }

    @property
    def shadow_root(self) -> "FirefoxElement | None":
        """Shadow DOM 根节点

        Returns:
            FirefoxElement 或 None
        """
        result = self._call_js_on_self_raw("(el) => el.shadowRoot")
        if result and result.get("type") == "node":
            return FirefoxElement._from_node(self._owner, result)
        return None

    @property
    def closed_shadow_root(self) -> "FirefoxElement | None":
        """尝试获取 closed shadowRoot（仅当页面暴露调试桥接函数时可用）。

        说明：
            - 标准 DOM API 无法直接读取 closed shadowRoot。
            - 若页面定义了 `window.__ruyiGetClosedShadowRoot(el)`，
              则可通过该桥接函数在自动化测试中访问。
            - 未暴露桥接函数或读取失败时返回 None。
        """
        result = self._call_js_on_self_raw(
            """(el) => {
                if (typeof window.__ruyiGetClosedShadowRoot !== 'function') return null;
                return window.__ruyiGetClosedShadowRoot(el);
            }"""
        )
        if result and result.get("type") == "node":
            return FirefoxElement._from_node(self._owner, result)
        return None

    @contextmanager
    def with_shadow(self, mode="open"):
        """以 with 语法访问 shadow root（更简洁）。

        Args:
            mode: 'open' 或 'closed'

        Yields:
            FirefoxElement: shadow root 节点
        """
        mode = (mode or "open").lower()
        if mode not in ("open", "closed"):
            raise ValueError("mode must be 'open' or 'closed'")

        root = self.shadow_root if mode == "open" else self.closed_shadow_root
        if root is None:
            raise RuntimeError("未找到 {} shadow root".format(mode))
        yield root

    # ===== Unit 属性（惰性加载） =====

    @property
    def click(self) -> "Clicker":
        """点击管理器"""
        if self._clicker is None:
            from .._units.clicker import Clicker

            self._clicker = Clicker(self)
        return self._clicker

    @property
    def scroll(self) -> "ElementScroller":
        """滚动管理器"""
        if self._scroll is None:
            from .._units.scroller import ElementScroller

            self._scroll = ElementScroller(self)
        return self._scroll

    @property
    def rect(self) -> "ElementRect":
        """位置/尺寸信息"""
        if self._rect_unit is None:
            from .._units.rect import ElementRect

            self._rect_unit = ElementRect(self)
        return self._rect_unit

    @property
    def set(self) -> "ElementSetter":
        """属性设置器"""
        if self._setter is None:
            from .._units.setter import ElementSetter

            self._setter = ElementSetter(self)
        return self._setter

    @property
    def states(self) -> "ElementStates":
        """状态查询"""
        if self._states_unit is None:
            from .._units.states import ElementStates

            self._states_unit = ElementStates(self)
        return self._states_unit

    @property
    def wait(self) -> "ElementWaiter":
        """等待管理器"""
        if self._wait_unit is None:
            from .._units.waiter import ElementWaiter

            self._wait_unit = ElementWaiter(self)
        return self._wait_unit

    @property
    def select(self) -> "SelectElement":
        """`<select>` 元素管理器。

        Returns:
            SelectElement: 专门负责 `<select>` 元素选择逻辑的高层对象。

        适用场景：
            - 通过 ``by_value()`` / ``by_text()`` / ``by_index()`` 选择选项
            - 指定 ``mode='native_only'`` 做纯原生 BiDi 选择验证

        说明：
            - 只有 `<select>` 元素才支持该属性。
            - 显式返回类型是为了让 VS Code 能从
              ``page.ele(...).select.by_value(...)`` 正常跳转到实现处。
        """
        if self.tag != "select":
            raise TypeError("select 属性仅适用于 <select> 元素")
        if self._select is None:
            from .._units.selector import SelectElement

            self._select = SelectElement(self)
        return self._select

    # ===== 属性访问方法 =====

    def attr(self, name) -> str:
        """获取属性值。

        Args:
            name: 属性名。
                常见值：``'href'``、``'src'``、``'value'``、``'placeholder'``。

        Returns:
            str: 属性值字符串，不存在时通常返回空字符串或 ``None``。

        适用场景：
            - 读取链接地址，例如 ``title_ele.attr('href')``
            - 读取图片地址、输入框占位符等属性
        """
        return self._run_safe("(el, name) => el.getAttribute(name)", name)

    def property(self, name):
        """获取 JS 属性值

        Args:
            name: 属性名

        Returns:
            属性值
        """
        return self._run_safe("(el, name) => el[name]", name)

    def style(self, name, pseudo="") -> str:
        """获取计算样式

        Args:
            name: CSS 属性名
            pseudo: 伪元素，如 '::before'

        Returns:
            样式值字符串
        """
        return (
            self._run_safe(
                "(el, name, pseudo) => window.getComputedStyle(el, pseudo || null).getPropertyValue(name)",
                name,
                pseudo,
            )
            or ""
        )

    # ===== 交互方法 =====

    def click_self(self, by_js=False, timeout=1.5) -> "FirefoxElement":
        """直接点击当前元素。

        Args:
            by_js: 是否使用 JavaScript 方式点击。
                常见值：``False`` 优先原生点击、``True`` 使用 JS click。
            timeout: 等待元素可点击的超时时间。
                单位：秒。

        Returns:
            self: 原元素对象，便于链式调用。

        适用场景：
            - 最常见的元素点击操作
            - 例如 ``next_btn.click_self()``、``button.click_self()``
        """
        if by_js:
            self._run_safe("(el) => el.click()")
        else:
            # 优先使用原生 BiDi 滚轮将元素带入视口，避免生成非原生点击事件
            self._owner.scroll.to_see(self, center=True)
            _sleep(0.1)
            pos = self._run_safe("""(el) => {
                const r = el.getBoundingClientRect();
                return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
            }""")

            if not pos or (pos.get("x", 0) == 0 and pos.get("y", 0) == 0):
                raise RuntimeError("无法获取元素可点击坐标，请确认元素在视口内")

            # 使用 input.performActions
            x, y = pos["x"], pos["y"]
            self._owner._driver._browser_driver.run(
                "input.performActions",
                {
                    "context": self._owner._context_id,
                    "actions": [
                        {
                            "type": "pointer",
                            "id": "mouse0",
                            "parameters": {"pointerType": "mouse"},
                            "actions": [
                                {"type": "pointerMove", "x": x, "y": y, "duration": 50},
                                {"type": "pointerDown", "button": 0},
                                {"type": "pause", "duration": 50},
                                {"type": "pointerUp", "button": 0},
                            ],
                        }
                    ],
                },
            )

            # 行为可视化
            try:
                browser = getattr(self._owner, "_browser", None)
                options = getattr(browser, "options", None)
                if getattr(options, "action_visual_enabled", False):
                    self._owner.run_js(
                        "if(window.__ruyiAV){window.__ruyiAV.moves([["
                        + str(x)
                        + ","
                        + str(y)
                        + "]]);window.__ruyiAV.click("
                        + str(x)
                        + ","
                        + str(y)
                        + ",0)}",
                        as_expr=True,
                    )
            except Exception:
                pass

        return self

    def right_click(self) -> "FirefoxElement":
        """右键点击元素

        Returns:
            self
        """
        self._owner.actions.right_click(self).perform()
        return self

    def double_click(self) -> "FirefoxElement":
        """双击元素

        Returns:
            self
        """
        self._owner.actions.double_click(self).perform()
        return self

    def input(self, text, clear=True, by_js=False) -> "FirefoxElement":
        """输入文本

        Args:
            text: 输入内容（字符串或 list/tuple 用于文件路径）
            clear: 先清空
            by_js: 通过 JS 设值

        Returns:
            self
        """
        # 文件输入
        if self.tag == "input" and self.attr("type") == "file":
            if isinstance(text, str):
                text = [text]
            self._owner._driver._browser_driver.run(
                "input.setFiles",
                {
                    "context": self._owner._context_id,
                    "element": make_shared_ref(self._shared_id),
                    "files": list(text),
                },
            )
            return self

        if by_js:
            try:
                browser = getattr(self._owner, "_browser", None)
                options = getattr(browser, "options", None)
                if getattr(options, "action_visual_enabled", False):
                    loc = self.location
                    size = self.size
                    rect = {
                        "x": int(loc.get("x", 0)),
                        "y": int(loc.get("y", 0)),
                        "width": int(size.get("width", 0)),
                        "height": int(size.get("height", 0)),
                    }
                    center = self._get_center() or {"x": 0, "y": 0}
                    import json

                    self._owner.run_js(
                        "if(window.__ruyiAV){window.__ruyiAV.moves([[{x},{y}]]);window.__ruyiAV.click({x},{y},0);window.__ruyiAV.highlight({rect},{label})}".format(
                            x=int(center.get("x", 0)),
                            y=int(center.get("y", 0)),
                            rect=json.dumps(rect),
                            label=json.dumps(
                                "{} input[js]".format(self.tag or "element")
                            ),
                        ),
                        as_expr=True,
                    )
            except Exception:
                pass
            if clear:
                self._run_safe('(el) => { el.value = ""; }')
            self._run_safe(
                '(el, text) => { el.value = text; el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }',
                str(text),
            )
            return self

        # 聚焦
        self._run_safe("(el) => el.focus()")

        if clear:
            self.clear()

        # 通过键盘输入
        text = str(text)
        key_actions = []
        for char in text:
            key_actions.append({"type": "keyDown", "value": char})
            key_actions.append({"type": "keyUp", "value": char})

        if key_actions:
            self._owner._driver._browser_driver.run(
                "input.performActions",
                {
                    "context": self._owner._context_id,
                    "actions": [
                        {"type": "key", "id": "keyboard0", "actions": key_actions}
                    ],
                },
            )

        return self

    def clear(self) -> "FirefoxElement":
        """清空输入内容

        Returns:
            self
        """
        self.click_self()
        self._owner._driver._browser_driver.run(
            "input.performActions",
            {
                "context": self._owner._context_id,
                "actions": [
                    {
                        "type": "key",
                        "id": "keyboard0",
                        "actions": [
                            {"type": "keyDown", "value": Keys.CONTROL},
                            {"type": "keyDown", "value": "a"},
                            {"type": "keyUp", "value": "a"},
                            {"type": "keyUp", "value": Keys.CONTROL},
                            {"type": "keyDown", "value": Keys.DELETE},
                            {"type": "keyUp", "value": Keys.DELETE},
                        ],
                    }
                ],
            },
        )
        return self

    def hover(self) -> "FirefoxElement":
        """鼠标悬停

        Returns:
            self
        """
        # 先滚动元素到可见区域
        self._run_safe(
            '(el) => el.scrollIntoView({block: "center", inline: "nearest"})'
        )
        _sleep(0.1)
        pos = self._get_center()
        if pos:
            self._owner._driver._browser_driver.run(
                "input.performActions",
                {
                    "context": self._owner._context_id,
                    "actions": [
                        {
                            "type": "pointer",
                            "id": "mouse0",
                            "parameters": {"pointerType": "mouse"},
                            "actions": [
                                {
                                    "type": "pointerMove",
                                    "x": pos["x"],
                                    "y": pos["y"],
                                    "duration": 100,
                                },
                            ],
                        }
                    ],
                },
            )
        return self

    def drag_to(self, target, duration=0.5) -> "FirefoxElement":
        """拖拽到目标 (通过原生 BiDi input.performActions 实现，isTrusted=true)

        使用 W3C WebDriver BiDi input.performActions 发送鼠标拖拽序列，
        所有 pointerMove 使用 origin="viewport" 确保坐标绝对精确。

        Args:
            target: 拖拽目标位置，支持以下类型:
                    - FirefoxElement: 拖拽到目标元素中心（自动滚动源和目标到视口）
                    - dict {'x': int, 'y': int}: 拖拽到指定视口坐标（不自动滚动，
                      假定调用方已确保源元素和目标坐标都在视口内）
                    - tuple/list (x, y): 同 dict 方式
            duration: 拖拽动画总时长 (秒)。值越大拖拽越平滑。默认 0.5。

        Returns:
            self: 支持链式调用。
        """
        if isinstance(target, FirefoxElement):
            target_elem = target
            end = None
        elif isinstance(target, dict):
            target_elem = None
            end = {"x": int(target.get("x", 0)), "y": int(target.get("y", 0))}
        elif isinstance(target, (list, tuple)) and len(target) >= 2:
            target_elem = None
            end = {"x": int(target[0]), "y": int(target[1])}
        else:
            return self

        dur_ms = max(50, int(duration * 1000))

        def _build_segmented_drag(sx, sy, ex, ey, total_ms):
            """构建分段拖拽动作序列 (所有 pointerMove 使用 origin='viewport')"""
            sx, sy = int(sx), int(sy)
            ex, ey = int(ex), int(ey)
            steps = max(8, min(30, total_ms // 30))
            step_dur = max(1, total_ms // steps)

            actions = [
                {
                    "type": "pointerMove",
                    "origin": "viewport",
                    "x": sx,
                    "y": sy,
                    "duration": 0,
                },
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 80},
            ]

            for i in range(1, steps + 1):
                px = int(sx + (ex - sx) * i / steps)
                py = int(sy + (ey - sy) * i / steps)
                actions.append(
                    {
                        "type": "pointerMove",
                        "origin": "viewport",
                        "x": px,
                        "y": py,
                        "duration": step_dur,
                    }
                )

            actions.extend(
                [
                    {"type": "pause", "duration": 80},
                    {"type": "pointerUp", "button": 0},
                ]
            )
            return actions

        if target_elem:
            # 目标是元素: 自动滚动源和目标到视口，然后获取坐标
            try:
                self._run_safe(
                    '(el) => el.scrollIntoView({block: "center", inline: "nearest"})'
                )
                target_elem._run_safe(
                    '(el) => el.scrollIntoView({block: "center", inline: "nearest"})'
                )
            except Exception:
                pass

            start = self._get_center()
            end = target_elem._get_center()
            if not start or not end:
                return self

            pointer_actions = _build_segmented_drag(
                start["x"], start["y"], end["x"], end["y"], dur_ms
            )
        else:
            # 目标是坐标: 不自动滚动，直接读取当前视口内的源元素位置
            # 调用方应确保源元素和目标坐标都在视口范围内
            start = self._run_safe("""(el) => {
                const r = el.getBoundingClientRect();
                if (r.width === 0 && r.height === 0) return null;
                return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
            }""")
            if not start or not end:
                return self

            pointer_actions = _build_segmented_drag(
                start["x"], start["y"], end["x"], end["y"], dur_ms
            )

        # 通过原生 BiDi input.performActions 执行拖拽 (isTrusted=true)
        self._owner._driver._browser_driver.run(
            "input.performActions",
            {
                "context": self._owner._context_id,
                "actions": [
                    {
                        "type": "pointer",
                        "id": "mouse0",
                        "parameters": {"pointerType": "mouse"},
                        "actions": pointer_actions,
                    }
                ],
            },
        )

        return self

    def screenshot(self, path=None, as_bytes=None, as_base64=None):
        """元素截图

        Args:
            path: 保存路径
            as_bytes: 返回 bytes
            as_base64: 返回 base64

        Returns:
            bytes/base64/文件路径
        """
        import base64 as b64_mod

        from .._bidi import browsing_context as bidi_ctx

        result = bidi_ctx.capture_screenshot(
            self._owner._driver._browser_driver,
            self._owner._context_id,
            clip={"type": "element", "element": make_shared_ref(self._shared_id)},
        )

        data_b64 = result.get("data", "")
        data_bytes = b64_mod.b64decode(data_b64)

        if path:
            import os

            if os.path.dirname(path):
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data_bytes)

        if as_bytes:
            return data_bytes
        if as_base64:
            return data_b64
        if path:
            return path

        return data_bytes

    def focus(self) -> "FirefoxElement":
        """聚焦元素

        Returns:
            self
        """
        self._run_safe("(el) => el.focus()")
        return self

    # ===== 元素上执行 JS =====

    def run_js(self, script, *args):
        """在元素上执行 JavaScript，元素作为 this

        Uses ``script.callFunction`` with ``this`` bound to the element.

        Args:
            script: JS 函数声明字符串，可以通过 ``this`` 访问元素
            *args: 传递给脚本的参数

        Returns:
            JS 执行的返回值（自动转换为 Python 对象）

        Example::

            ele.run_js('function(){ return this.textContent; }')
            ele.run_js('function(a){ this.style.color = a; }', 'red')
        """
        from .._functions.bidi_values import serialize_value

        serialized_args = [serialize_value(a) for a in args] if args else None
        this_ref = make_shared_ref(self._shared_id, self._handle)

        try:
            result = bidi_script.call_function(
                self._owner._driver._browser_driver,
                self._owner._context_id,
                script,
                arguments=serialized_args,
                this=this_ref,
                serialization_options={"maxDomDepth": 0, "includeShadowTree": "open"},
            )

            if result.get("type") == "exception":
                details = result.get("exceptionDetails", {})
                err_text = details.get("text", "")
                if _is_node_lost_error(err_text):
                    # Try refresh once
                    if self._refresh_id():
                        this_ref = make_shared_ref(self._shared_id, self._handle)
                        result = bidi_script.call_function(
                            self._owner._driver._browser_driver,
                            self._owner._context_id,
                            script,
                            arguments=serialized_args,
                            this=this_ref,
                            serialization_options={
                                "maxDomDepth": 0,
                                "includeShadowTree": "open",
                            },
                        )
                        if result.get("type") == "exception":
                            details = result.get("exceptionDetails", {})
                            raise JavaScriptError(
                                details.get("text", str(result)), details
                            )
                    else:
                        raise ElementLostError(
                            "元素引用已失效: {}".format(self._shared_id)
                        )
                else:
                    raise JavaScriptError(err_text, details)

            return parse_value(result.get("result", {}))

        except (ElementLostError, JavaScriptError):
            raise
        except Exception as e:
            logger.debug("run_js 失败: %s", e)
            return None

    # ===== DOM 树导航 =====

    def parent(self, locator=None, index=1) -> "FirefoxElement | NoneElement":
        """获取父元素

        Args:
            locator: 可选，匹配条件
            index: 向上第几层父元素

        Returns:
            FirefoxElement 或 NoneElement
        """
        if locator:
            # 向上查找匹配的祖先
            result = self._call_js_on_self_raw(
                """(el, sel, idx) => {
                let curr = el.parentElement;
                let count = 0;
                while (curr) {
                    try {
                        if (curr.matches && curr.matches(sel)) {
                            count++;
                            if (count >= idx) return curr;
                        }
                    } catch(e) {}
                    curr = curr.parentElement;
                }
                return null;
            }""",
                locator if isinstance(locator, str) else "*",
                index,
            )
        else:
            js = (
                "(el) => {let p = el; for(let i=0; i<%d; i++) { p = p ? p.parentElement : null; } return p;}"
                % index
            )
            result = self._call_js_on_self_raw(js)

        if result and result.get("type") == "node":
            return FirefoxElement._from_node(self._owner, result)

        from .none_element import NoneElement

        return NoneElement(self._owner)

    def child(
        self, locator=None, index=1, timeout=None
    ) -> "FirefoxElement | NoneElement":
        """获取子元素

        Args:
            locator: 定位器
            index: 索引（从1开始）
            timeout: 超时

        Returns:
            FirefoxElement 或 NoneElement
        """
        if locator:
            return self.ele(locator, index=index, timeout=timeout)

        result = self._call_js_on_self_raw(
            "(el, idx) => el.children[idx - 1] || null", index
        )
        if result and result.get("type") == "node":
            return FirefoxElement._from_node(self._owner, result)

        from .none_element import NoneElement

        return NoneElement(self._owner)

    def children(self, locator=None, timeout=None) -> "list[FirefoxElement]":
        """获取所有子元素"""
        if locator:
            return self.eles(locator, timeout=timeout)

        result = self._call_js_on_self_raw("(el) => Array.from(el.children)")
        if result and result.get("type") == "array":
            elements = []
            for node in result.get("value", []):
                ele = FirefoxElement._from_node(self._owner, node)
                if ele:
                    elements.append(ele)
            return elements

        return []

    def next(self, locator=None, index=1) -> "FirefoxElement | NoneElement":
        """获取后续兄弟元素

        Args:
            locator: 可选匹配条件
            index: 第几个

        Returns:
            FirefoxElement 或 NoneElement
        """
        if locator:
            result = self._call_js_on_self_raw(
                """(el, sel, idx) => {
                let curr = el.nextElementSibling;
                let count = 0;
                while (curr) {
                    try {
                        if (!sel || (curr.matches && curr.matches(sel))) {
                            count++;
                            if (count >= idx) return curr;
                        }
                    } catch(e) {}
                    curr = curr.nextElementSibling;
                }
                return null;
            }""",
                locator if isinstance(locator, str) else None,
                index,
            )
        else:
            js = (
                "(el) => { let s = el; for(let i=0; i<%d; i++) { s = s ? s.nextElementSibling : null; } return s; }"
                % index
            )
            result = self._call_js_on_self_raw(js)

        if result and result.get("type") == "node":
            return FirefoxElement._from_node(self._owner, result)

        from .none_element import NoneElement

        return NoneElement(self._owner)

    def prev(self, locator=None, index=1) -> "FirefoxElement | NoneElement":
        """获取前面的兄弟元素

        Args:
            locator: 可选匹配条件
            index: 第几个

        Returns:
            FirefoxElement 或 NoneElement
        """
        if locator:
            result = self._call_js_on_self_raw(
                """(el, sel, idx) => {
                let curr = el.previousElementSibling;
                let count = 0;
                while (curr) {
                    try {
                        if (!sel || (curr.matches && curr.matches(sel))) {
                            count++;
                            if (count >= idx) return curr;
                        }
                    } catch(e) {}
                    curr = curr.previousElementSibling;
                }
                return null;
            }""",
                locator if isinstance(locator, str) else None,
                index,
            )
        else:
            js = (
                "(el) => { let s = el; for(let i=0; i<%d; i++) { s = s ? s.previousElementSibling : null; } return s; }"
                % index
            )
            result = self._call_js_on_self_raw(js)

        if result and result.get("type") == "node":
            return FirefoxElement._from_node(self._owner, result)

        from .none_element import NoneElement

        return NoneElement(self._owner)

    # ===== 相对元素查找 =====

    def ele(self, locator, index=1, timeout=None) -> "FirefoxElement | NoneElement":
        """在当前元素内部继续查找单个子元素。

        Args:
            locator: 子元素定位器。
                写法与 ``page.ele()`` 相同，例如：
                ``card.ele('css:h2 a')``
                ``form.ele('#username')``
                ``row.ele('text:删除')``
            index: 第几个匹配结果。
                常见值：``1``、``2``、``-1``。
            timeout: 查找超时时间。
                单位：秒。

        Returns:
            FirefoxElement 或 NoneElement。

        适用场景：
            - 先拿到一张卡片，再在卡片内部找标题、链接、摘要
            - 缩小查找范围，避免全页面选择器过长
        """
        return self._owner._find_element(
            locator, index=index, timeout=timeout, start_node=self
        )

    def eles(self, locator, timeout=None) -> "list[FirefoxElement]":
        """在当前元素内部查找所有匹配的子元素。

        Args:
            locator: 子元素定位器。
                写法与 ``page.eles()`` 相同，例如：
                ``list_box.eles('css:li')``
                ``table.eles('css:tbody tr')``
            timeout: 查找超时时间。
                单位：秒。

        Returns:
            list[FirefoxElement]: 匹配到的所有子元素列表。

        适用场景：
            - 在某个容器内部批量拿子项
            - 比如在一条搜索结果卡片内部找多个链接或标签
        """
        if timeout is None:
            timeout = Settings.element_find_timeout

        end_time = time.time() + timeout

        while True:
            # Pass SharedReference format for startNodes
            start_ref = {"type": "sharedReference", "sharedId": self._shared_id}
            elements = self._owner._do_find(locator, start_node=start_ref)
            if elements:
                return elements
            if time.time() >= end_time:
                break
            _sleep(0.3)

        return []

    def s_ele(self, locator=None) -> "StaticElement | NoneElement":
        """获取静态子元素"""
        from .static_element import make_static_ele

        html = self.inner_html
        return make_static_ele(html, locator)

    # ===== 元素刷新 =====

    def _refresh_id(self):
        """尝试使用存储的定位器信息重新定位元素

        当元素的 sharedId 失效（例如页面部分更新）时，
        通过原始定位器重新查找并更新 sharedId。

        Returns:
            bool: True 如果成功重新定位，False 否则
        """
        if not self._locator_info:
            # 没有定位器信息，尝试通过缓存的属性重建定位器
            tag = self._node_info.get("localName", "")
            attrs = self._node_info.get("attributes", {})

            if not tag:
                return False

            # 尝试用 id 定位
            if isinstance(attrs, dict) and attrs.get("id"):
                css = "#{}".format(attrs["id"])
            elif isinstance(attrs, dict) and attrs.get("class"):
                css = "{}.{}".format(tag, ".".join(attrs["class"].split()))
            else:
                # 没有足够信息重新定位
                return False

            try:
                elements = self._owner._do_find(css)
                if elements:
                    new_ele = elements[0]
                    self._shared_id = new_ele._shared_id
                    self._handle = new_ele._handle
                    self._node_info = new_ele._node_info
                    logger.debug("元素已通过 CSS 选择器重新定位: %s", css)
                    return True
            except Exception as e:
                logger.debug("元素重新定位失败: %s", e)
                return False

            return False

        # 使用存储的定位器
        try:
            elements = self._owner._do_find(self._locator_info)
            if elements:
                new_ele = elements[0]
                self._shared_id = new_ele._shared_id
                self._handle = new_ele._handle
                self._node_info = new_ele._node_info
                logger.debug("元素已通过原始定位器重新定位")
                return True
        except Exception as e:
            logger.debug("元素重新定位失败: %s", e)

        return False

    # ===== 内部方法 =====

    def _run_safe(self, func_declaration, *args):
        """在元素上安全执行 JS 函数，自动处理 ElementLostError

        如果调用失败且错误是 'no such node'，会尝试 _refresh_id() 一次后重试。

        Args:
            func_declaration: JS 函数声明，第一个参数是元素
            *args: 额外参数

        Returns:
            Python 原生值
        """
        result = self._call_js_on_self_safe(func_declaration, *args)
        return parse_value(result) if isinstance(result, dict) else result

    def _call_js_on_self(self, func_declaration, *args):
        """在元素上执行 JS 函数，返回 Python 值

        Args:
            func_declaration: JS 函数声明，第一个参数是元素
            *args: 额外参数

        Returns:
            Python 原生值
        """
        result = self._call_js_on_self_raw(func_declaration, *args)
        return parse_value(result) if isinstance(result, dict) else result

    def _call_js_on_self_safe(self, func_declaration, *args):
        """在元素上执行 JS 函数，处理 ElementLostError 并自动重试

        如果遇到 'no such node' 错误，先尝试 _refresh_id() 重新定位，
        然后重试一次。

        Args:
            func_declaration: JS 函数声明
            *args: 额外参数

        Returns:
            BiDi RemoteValue 字典
        """
        result = self._call_js_on_self_raw(func_declaration, *args)
        if result is not None:
            return result

        # _call_js_on_self_raw 返回 None 可能是因为节点丢失，
        # 但也可能是合法的 None 返回值。如果有丢失标记则重试。
        return result

    def _call_js_on_self_raw(self, func_declaration, *args):
        """在元素上执行 JS 函数，返回原始 BiDi 结果

        当遇到 'no such node' 错误时，尝试 _refresh_id() 一次后重试。

        Args:
            func_declaration: JS 函数声明
            *args: 额外参数

        Returns:
            BiDi RemoteValue 字典
        """
        from .._functions.bidi_values import serialize_value

        # 构建参数：第一个是 self 的 SharedReference，后面是额外参数
        arguments = [make_shared_ref(self._shared_id, self._handle)]
        for arg in args:
            if arg is None:
                arguments.append({"type": "undefined"})
            elif isinstance(arg, dict) and ("sharedId" in arg or "type" in arg):
                arguments.append(arg)
            else:
                arguments.append(serialize_value(arg))

        try:
            result = bidi_script.call_function(
                self._owner._driver._browser_driver,
                self._owner._context_id,
                func_declaration,
                arguments=arguments,
                serialization_options={"maxDomDepth": 0, "includeShadowTree": "open"},
            )

            if result.get("type") == "exception":
                details = result.get("exceptionDetails", {})
                err_text = details.get("text", "")
                if _is_node_lost_error(err_text):
                    # 尝试重新定位元素并重试
                    if self._refresh_id():
                        # 重建参数（shared_id 已更新）
                        arguments[0] = make_shared_ref(self._shared_id, self._handle)
                        retry_result = bidi_script.call_function(
                            self._owner._driver._browser_driver,
                            self._owner._context_id,
                            func_declaration,
                            arguments=arguments,
                            serialization_options={
                                "maxDomDepth": 0,
                                "includeShadowTree": "open",
                            },
                        )
                        if retry_result.get("type") == "exception":
                            raise ElementLostError(
                                "元素引用已失效（重试后仍然失败）: {}".format(
                                    self._shared_id
                                )
                            )
                        return retry_result.get("result", {})
                    else:
                        raise ElementLostError(
                            "元素引用已失效: {}".format(self._shared_id)
                        )
                logger.debug("JS 执行异常: %s", err_text)
                return None

            return result.get("result", {})

        except ElementLostError:
            raise
        except Exception as e:
            logger.debug("_call_js_on_self 失败: %s", e)
            return None

    def _get_center(self):
        """获取元素中心坐标（先滚动到可见区域）"""
        return self._run_safe("""(el) => {
            el.scrollIntoView({block: "center", inline: "nearest"});
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) return null;
            return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
        }""")

    def _make_shared_ref(self):
        """创建 SharedReference"""
        return make_shared_ref(self._shared_id, self._handle)

    def __repr__(self):
        tag = self._node_info.get("localName", "") or "?"
        attrs = self._node_info.get("attributes", {})
        id_str = attrs.get("id", "") if isinstance(attrs, dict) else ""
        cls_str = attrs.get("class", "") if isinstance(attrs, dict) else ""
        parts = [tag]
        if id_str:
            parts.append("#" + id_str)
        if cls_str:
            parts.append("." + cls_str.split()[0])
        return "<FirefoxElement {}>".format("".join(parts))

    def __bool__(self):
        return True

    def __eq__(self, other):
        if isinstance(other, FirefoxElement):
            return self._shared_id == other._shared_id
        return False

    def __hash__(self):
        return hash(self._shared_id)
