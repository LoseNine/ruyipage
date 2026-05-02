# -*- coding: utf-8 -*-
"""SelectElement - `<select>` 元素操作。

本模块设计重点：
1) 统一三种模式：native_only / native_first / compat
2) 原生路径采用“状态驱动”逐步选择（更稳）
3) JS 仅作为 compat 模式的保底
"""

from .._functions.keys import Keys
from .._functions.bidi_values import make_shared_ref

import logging

logger = logging.getLogger('ruyipage')


class SelectElement(object):
    """<select> 元素管理器。

    用法::

        # 简单调用（默认兼容模式）
        ele.select.by_value('opt2')

        # 严格原生模式（不允许 JS 保底）
        ele.select.by_value('opt2', mode='native_only')

        ele.select.by_text('选项2', mode='native_first')
        ele.select.by_index(2, mode='compat')

    mode 说明::

        native_only  - 只尝试原生 BiDi，失败即 False
        native_first - 先原生，失败即 False（当前与 native_only 一致）
        compat       - 先原生，失败后 JS 保底（默认）
    """

    def __init__(self, element):
        self._ele = element

    # ---------- mode / params ----------
    def _resolve_mode(self, mode):
        """标准化并校验 mode 参数。"""
        mode = (mode or "compat").lower()
        if mode not in ("native_only", "native_first", "compat"):
            raise ValueError("mode must be one of: native_only, native_first, compat")
        return mode

    # ---------- state helpers ----------
    def _read_state(self):
        """读取 select 当前状态（只读，不触发交互）。"""
        return (
            self._ele._call_js_on_self(
                """(el) => {
            const opts = Array.from(el.options).map(o => ({
                text: o.text,
                value: o.value,
                selected: o.selected,
                index: o.index,
                disabled: o.disabled
            }));
            const r = el.getBoundingClientRect();
            return {
                selectedIndex: el.selectedIndex,
                value: el.value,
                multiple: !!el.multiple,
                size: Number(el.size || 0),
                disabled: !!el.disabled,
                focused: document.activeElement === el,
                rect: {x: r.x, y: r.y, width: r.width, height: r.height},
                options: opts,
            };
        }"""
            )
            or {}
        )

    def _find_target_index(self, matcher):
        """根据匹配函数定位目标 option 索引。"""
        for opt in self.options or []:
            if matcher(opt):
                return opt.get("index")
        return None

    def _focus_select_native(self):
        """尝试通过原生点击让 select 获得焦点。"""
        self._ele._owner.scroll.to_see(self._ele, center=True)
        self._native_click_select()
        self._ele._owner.actions.wait(0.06).perform()
        state = self._read_state()
        if state.get("focused"):
            return True

        # 第二次点击重试，处理偶发焦点未命中
        self._native_click_select()
        self._ele._owner.actions.wait(0.06).perform()
        state = self._read_state()
        return bool(state.get("focused"))

    def _native_click_select(self):
        """使用 element-origin pointer click 点击 select。

        说明：
                - 不走 JS click，避免明显的非原生事件。
                - 先尝试激活 context，可提升前台焦点命中率。
        """
        driver = self._ele._owner._driver._browser_driver
        context_id = self._ele._owner._context_id

        # 在派发 pointer 动作前尽量激活当前 context
        try:
            driver.run("browsingContext.activate", {"context": context_id})
        except Exception as e:
            logger.debug("激活上下文失败: %s", e)

        driver.run(
            "input.performActions",
            {
                "context": context_id,
                "actions": [
                    {
                        "type": "pointer",
                        "id": "mouse0",
                        "parameters": {"pointerType": "mouse"},
                        "actions": [
                            {
                                "type": "pointerMove",
                                "x": 0,
                                "y": 0,
                                "duration": 0,
                                "origin": {
                                    "type": "element",
                                    "element": make_shared_ref(
                                        self._ele._shared_id, self._ele._handle
                                    ),
                                },
                            },
                            {"type": "pointerDown", "button": 0},
                            {"type": "pause", "duration": 50},
                            {"type": "pointerUp", "button": 0},
                        ],
                    }
                ],
            },
        )

    def _nudge_with_key(self, key):
        """发送一步键盘动作，并给浏览器短暂处理时间。"""
        self._ele._owner.actions.key_down(key).key_up(key).wait(0.02).perform()

    def _commit_with_enter(self):
        """发送 ENTER 提交当前选择。"""
        self._ele._owner.actions.key_down(Keys.ENTER).key_up(Keys.ENTER).wait(
            0.03
        ).perform()

    def _native_select_stepwise(self, target_index):
        """State-driven native select for single select.

        Strategy:
        1) focus control with native pointer
        2) move selection index step-by-step via UP/DOWN
        3) verify selectedIndex after each key
        4) commit with ENTER and re-verify

        说明：
        - 逐步校验比一次性发整串按键更稳定。
        - 如果某步没有移动索引，会尝试 HOME 做一次对齐唤醒。
        """
        state = self._read_state()
        if not state:
            return False
        if state.get("disabled"):
            return False
        if state.get("multiple"):
            return False

        options = state.get("options") or []
        if target_index < 0 or target_index >= len(options):
            return False
        if options[target_index].get("disabled"):
            return False

        # 已经是目标选项，无需动作
        if state.get("selectedIndex") == target_index:
            return True

        if not self._focus_select_native():
            return False

        state = self._read_state()
        current_index = state.get("selectedIndex", 0)
        max_steps = max(1, len(options) + 3)

        for _ in range(max_steps):
            if current_index == target_index:
                break
            key = Keys.DOWN if current_index < target_index else Keys.UP
            self._nudge_with_key(key)
            state = self._read_state()
            new_index = state.get("selectedIndex", current_index)

            # 索引未变化，尝试 HOME 唤醒一次
            if new_index == current_index:
                self._nudge_with_key(Keys.HOME)
                state = self._read_state()
                new_index = state.get("selectedIndex", current_index)
            current_index = new_index

        if current_index != target_index:
            return False

        self._commit_with_enter()
        final_state = self._read_state()
        return final_state.get("selectedIndex") == target_index

    # ---------- js fallback ----------
    def _js_select_text(self, text):
        """文本选择的 JS 保底（仅 compat 会调用）。"""
        return (
            self._ele._call_js_on_self(
                """(el, text) => {
            for (let opt of el.options) {
                if (opt.text === text || opt.textContent.trim() === text) {
                    opt.selected = true;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
            }
            for (let opt of el.options) {
                if (opt.text.includes(text) || opt.textContent.includes(text)) {
                    opt.selected = true;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
            }
            return false;
        }""",
                text,
            )
            or False
        )

    def _js_select_value(self, value):
        """value 选择的 JS 保底（仅 compat 会调用）。"""
        return (
            self._ele._call_js_on_self(
                """(el, value) => {
            for (let opt of el.options) {
                if (opt.value === value) {
                    opt.selected = true;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
            }
            return false;
        }""",
                str(value),
            )
            or False
        )

    def _js_select_index(self, index):
        """index 选择的 JS 保底（仅 compat 会调用）。"""
        return (
            self._ele._call_js_on_self(
                """(el, idx) => {
            if (idx >= 0 && idx < el.options.length) {
                el.selectedIndex = idx;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            return false;
        }""",
                index,
            )
            or False
        )

    # ---------- public APIs ----------
    def __call__(self, text_or_index, mode="compat"):
        """通过文本或索引选择。

        Args:
            text_or_index: 文本或索引
            mode: native_only / native_first / compat
        """
        if isinstance(text_or_index, int):
            return self.by_index(text_or_index, mode=mode)
        return self.by_text(text_or_index, mode=mode)

    def by_text(self, text, timeout=None, mode="compat"):
        """通过文本选择 option。

        Args:
            text: 目标文本
            timeout: 预留参数（兼容旧接口）
            mode: native_only / native_first / compat
        """
        mode = self._resolve_mode(mode)
        target_index = self._find_target_index(
            lambda opt: (
                opt.get("text") == text
                or str(opt.get("text", "")).strip() == text
                or text in str(opt.get("text", ""))
            )
        )
        if target_index is None:
            return False

        if self._native_select_stepwise(target_index):
            return True
        if mode in ("native_only", "native_first"):
            return False
        return self._js_select_text(text)

    def by_value(self, value, mode="compat"):
        """通过 value 属性选择。

        Args:
            value: option.value
            mode: native_only / native_first / compat
        """
        mode = self._resolve_mode(mode)
        target_index = self._find_target_index(
            lambda opt: str(opt.get("value", "")) == str(value)
        )
        if target_index is None:
            return False

        if self._native_select_stepwise(target_index):
            return True
        if mode in ("native_only", "native_first"):
            return False
        return self._js_select_value(value)

    def by_index(self, index, mode="compat"):
        """通过索引选择（从0开始）。

        Args:
            index: option 索引
            mode: native_only / native_first / compat
        """
        mode = self._resolve_mode(mode)

        if self._native_select_stepwise(index):
            return True
        if mode in ("native_only", "native_first"):
            return False
        return self._js_select_index(index)

    def cancel_by_index(self, index):
        """取消选择指定索引的 option。"""
        result = self._ele._call_js_on_self(
            """(el, idx) => {
            if (idx >= 0 && idx < el.options.length) {
                el.options[idx].selected = false;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            return false;
        }""",
            index,
        )
        return result or False

    def cancel_by_text(self, text):
        """取消选择指定文本的 option。"""
        result = self._ele._call_js_on_self(
            """(el, text) => {
            for (let opt of el.options) {
                if (opt.text === text || opt.textContent.trim() === text) {
                    opt.selected = false;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
            }
            return false;
        }""",
            text,
        )
        return result or False

    def select_all(self):
        """全选（仅 multiple select）。"""
        self._ele._call_js_on_self(
            """(el) => {
            for (let opt of el.options) opt.selected = true;
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
        )
        return self._ele

    def deselect_all(self):
        """取消全选。"""
        self._ele._call_js_on_self(
            """(el) => {
            for (let opt of el.options) opt.selected = false;
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
        )
        return self._ele

    @property
    def options(self):
        """所有选项。"""
        return (
            self._ele._call_js_on_self(
                """(el) => {
            return Array.from(el.options).map(o => ({
                text: o.text,
                value: o.value,
                selected: o.selected,
                index: o.index
            }));
        }"""
            )
            or []
        )

    @property
    def selected_option(self):
        """当前选中的选项。"""
        return self._ele._call_js_on_self(
            """(el) => {
            const o = el.options[el.selectedIndex];
            return o ? {text: o.text, value: o.value, index: o.index} : null;
        }"""
        )

    @property
    def is_multi(self):
        """是否多选。"""
        return self._ele._call_js_on_self("(el) => el.multiple") or False
