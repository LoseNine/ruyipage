# -*- coding: utf-8 -*-
"""Clicker - 元素点击变体"""


class Clicker(object):
    """元素点击管理器

    用法::

        ele.click()        # 等价于 ele.click.left()
        ele.click.left()
        ele.click.right()
        ele.click.middle()
        ele.click.by_js()
        ele.click.for_new_tab()
        ele.click.at(offset_x, offset_y)
    """

    def __init__(self, element):
        self._ele = element

    def __call__(self, by_js=False):
        """默认左键点击"""
        if by_js:
            return self.by_js()
        return self.left()

    def left(self, times=1):
        """左键点击

        Args:
            times: 点击次数

        Returns:
            元素
        """
        pos = self._ele._get_center()
        if not pos:
            return self.by_js()

        actions = [
            {"type": "pointerMove", "x": pos["x"], "y": pos["y"], "duration": 50}
        ]
        for _ in range(times):
            actions.append({"type": "pointerDown", "button": 0})
            actions.append({"type": "pause", "duration": 50})
            actions.append({"type": "pointerUp", "button": 0})

        self._perform(actions)
        return self._ele

    def right(self):
        """右键点击

        Returns:
            元素
        """
        pos = self._ele._get_center()
        if not pos:
            return self._ele

        self._perform(
            [
                {"type": "pointerMove", "x": pos["x"], "y": pos["y"], "duration": 50},
                {"type": "pointerDown", "button": 2},
                {"type": "pause", "duration": 50},
                {"type": "pointerUp", "button": 2},
            ]
        )
        return self._ele

    def middle(self):
        """中键点击

        Returns:
            元素
        """
        pos = self._ele._get_center()
        if not pos:
            return self._ele

        self._perform(
            [
                {"type": "pointerMove", "x": pos["x"], "y": pos["y"], "duration": 50},
                {"type": "pointerDown", "button": 1},
                {"type": "pause", "duration": 50},
                {"type": "pointerUp", "button": 1},
            ]
        )
        return self._ele

    def by_js(self):
        """通过 JS 点击（不受遮挡影响）

        Returns:
            元素
        """
        self._send_js_click_visual()
        self._ele._call_js_on_self("(el) => el.click()")
        return self._ele

    def at(self, offset_x=0, offset_y=0):
        """在元素指定偏移位置点击

        Args:
            offset_x: 相对元素左上角的 X 偏移
            offset_y: 相对元素左上角的 Y 偏移

        Returns:
            元素
        """
        loc = self._ele.location
        x = loc.get("x", 0) + offset_x
        y = loc.get("y", 0) + offset_y

        self._perform(
            [
                {"type": "pointerMove", "x": x, "y": y, "duration": 50},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 50},
                {"type": "pointerUp", "button": 0},
            ]
        )
        return self._ele

    def for_new_tab(self):
        """点击后等待新标签页打开

        Returns:
            新打开的 FirefoxTab 或 None
        """
        browser = self._ele._owner._browser
        old_tabs = set(browser.tab_ids)

        self.left()

        import time
        from .._functions.sleep import sleep as _sleep

        for _ in range(20):
            _sleep(0.3)
            new_tabs = set(browser.tab_ids) - old_tabs
            if new_tabs:
                return browser.get_tab(list(new_tabs)[0])

        return None

    def _perform(self, pointer_actions):
        """执行指针动作"""
        self._ele._owner._driver._browser_driver.run(
            "input.performActions",
            {
                "context": self._ele._owner._context_id,
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
        self._send_visual(pointer_actions)

    def _send_visual(self, pointer_actions):
        """将点击数据注入页面 JS 端可视化渲染。"""
        browser = getattr(self._ele._owner, "_browser", None)
        if not browser:
            return
        options = getattr(browser, "options", None)
        if not options or not getattr(options, "action_visual_enabled", False):
            return

        try:
            last_x, last_y = 0, 0
            clicks = []
            move_points = []
            for a in pointer_actions:
                if a.get("type") == "pointerMove":
                    last_x = int(a.get("x", last_x))
                    last_y = int(a.get("y", last_y))
                    move_points.append([last_x, last_y])
                elif a.get("type") == "pointerDown":
                    clicks.append([last_x, last_y, a.get("button", 0)])

            js_parts = []
            if move_points:
                import json

                js_parts.append(
                    "if(window.__ruyiAV)window.__ruyiAV.moves({})".format(
                        json.dumps(move_points)
                    )
                )
            for cx, cy, btn in clicks:
                js_parts.append(
                    "if(window.__ruyiAV)window.__ruyiAV.click({},{},{})".format(
                        cx, cy, btn
                    )
                )

            rect = self._get_ele_rect()
            if rect:
                import json

                js_parts.append(
                    "if(window.__ruyiAV)window.__ruyiAV.highlight({}, {})".format(
                        json.dumps(rect), json.dumps(self._get_ele_label())
                    )
                )

            if js_parts:
                self._ele._owner.run_js(";".join(js_parts), as_expr=True)
        except Exception:
            pass

    def _send_js_click_visual(self):
        """为 JS click 提供点击位置和目标高亮。"""
        browser = getattr(self._ele._owner, "_browser", None)
        if not browser:
            return
        options = getattr(browser, "options", None)
        if not options or not getattr(options, "action_visual_enabled", False):
            return

        try:
            rect = self._get_ele_rect()
            center = self._ele._get_center() or {"x": 0, "y": 0}
            import json

            js_parts = [
                "if(window.__ruyiAV)window.__ruyiAV.moves([[{},{}]])".format(
                    int(center.get("x", 0)), int(center.get("y", 0))
                ),
                "if(window.__ruyiAV)window.__ruyiAV.click({},{},{})".format(
                    int(center.get("x", 0)), int(center.get("y", 0)), 0
                ),
            ]
            if rect:
                js_parts.append(
                    "if(window.__ruyiAV)window.__ruyiAV.highlight({}, {})".format(
                        json.dumps(rect), json.dumps(self._get_ele_label())
                    )
                )
            self._ele._owner.run_js(";".join(js_parts), as_expr=True)
        except Exception:
            pass

    def _get_ele_rect(self):
        try:
            loc = self._ele.location
            size = self._ele.size
            return {
                "x": int(loc.get("x", 0)),
                "y": int(loc.get("y", 0)),
                "width": int(size.get("width", 0)),
                "height": int(size.get("height", 0)),
            }
        except Exception:
            return None

    def _get_ele_label(self):
        try:
            tag = getattr(self._ele, "tag", "element") or "element"
            ele_id = self._ele.attr("id")
            if ele_id:
                return "{}#{}".format(tag, ele_id)
            return tag
        except Exception:
            return "element"
