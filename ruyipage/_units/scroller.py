# -*- coding: utf-8 -*-
"""Scroller - 滚动管理"""

import time

from .._functions.sleep import sleep as _sleep


class PageScroller(object):
    """页面级滚动管理器

    用法::

        page.scroll.to_bottom()
        page.scroll.down(300)
        page.scroll.to_see(element)
    """

    def __init__(self, owner):
        self._owner = owner

    def _perform_scroll(self, delta_x=0, delta_y=0, x=None, y=None):
        if x is None or y is None:
            x, y = self._owner.rect.viewport_midpoint

        self._owner._driver._browser_driver.run(
            "input.performActions",
            {
                "context": self._owner._context_id,
                "actions": [
                    {
                        "type": "wheel",
                        "id": "wheel0",
                        "actions": [
                            {
                                "type": "scroll",
                                "x": int(x),
                                "y": int(y),
                                "deltaX": int(delta_x),
                                "deltaY": int(delta_y),
                            }
                        ],
                    }
                ],
            },
        )

    def _scroll_until(self, check, step_x=0, step_y=0, max_steps=20, pause=0.1):
        for _ in range(max_steps):
            if check():
                return True
            self._perform_scroll(step_x, step_y)
            _sleep(pause)
        return check()

    def to_top(self):
        """滚动到顶部"""
        self._scroll_until(
            lambda: self._owner.rect.scroll_position[1] <= 0,
            step_y=-800,
        )
        return self._owner

    def to_bottom(self):
        """滚动到底部"""
        self._scroll_until(
            lambda: self._owner.run_js(
                "window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2"
            ),
            step_y=800,
        )
        return self._owner

    def to_half(self):
        """滚动到页面中部"""
        target_y = int(self._owner.rect.page_size[1] / 2)
        return self.to_location(0, target_y)
        return self._owner

    def to_rightmost(self):
        """滚动到最右侧"""
        self._scroll_until(
            lambda: self._owner.run_js(
                "window.innerWidth + window.scrollX >= document.documentElement.scrollWidth - 2"
            ),
            step_x=800,
        )
        return self._owner

    def to_leftmost(self):
        """滚动到最左侧"""
        self._scroll_until(
            lambda: self._owner.rect.scroll_position[0] <= 0,
            step_x=-800,
        )
        return self._owner

    def down(self, pixel=300):
        """向下滚动

        Args:
            pixel: 滚动像素数
        """
        self._perform_scroll(0, pixel)
        return self._owner

    def up(self, pixel=300):
        """向上滚动"""
        self._perform_scroll(0, -pixel)
        return self._owner

    def right(self, pixel=300):
        """向右滚动"""
        self._perform_scroll(pixel, 0)
        return self._owner

    def left(self, pixel=300):
        """向左滚动"""
        self._perform_scroll(-pixel, 0)
        return self._owner

    def to_see(self, ele_or_loc, center=False):
        """滚动到指定元素可见

        Args:
            ele_or_loc: 元素或定位器
            center: 是否居中显示
        """
        if isinstance(ele_or_loc, str):
            ele = self._owner.ele(ele_or_loc)
        else:
            ele = ele_or_loc

        if ele:
            if ele.states.is_in_viewport:
                return self._owner

            step_y = 500
            ele_mid_y = ele.rect.viewport_midpoint[1]
            viewport_h = self._owner.rect.viewport_size[1]
            if ele_mid_y > viewport_h:
                self._scroll_until(lambda: ele.states.is_in_viewport, step_y=step_y)
            else:
                self._scroll_until(lambda: ele.states.is_in_viewport, step_y=-step_y)

            if center and ele.states.is_in_viewport:
                viewport_mid_y = int(viewport_h / 2)
                ele_mid_y = ele.rect.viewport_midpoint[1]
                delta = ele_mid_y - viewport_mid_y
                if delta:
                    self._perform_scroll(0, delta)
                    _sleep(0.1)
        return self._owner

    def to_location(self, x, y):
        """滚动到指定坐标"""
        curr_x, curr_y = self._owner.rect.scroll_position
        self._perform_scroll(x - curr_x, y - curr_y)
        return self._owner


class ElementScroller(object):
    """元素级滚动管理器"""

    def __init__(self, element):
        self._ele = element

    def _perform_scroll(self, delta_x=0, delta_y=0):
        pos = self._ele._get_center()
        if not pos:
            return

        self._ele._owner._driver._browser_driver.run(
            "input.performActions",
            {
                "context": self._ele._owner._context_id,
                "actions": [
                    {
                        "type": "wheel",
                        "id": "wheel0",
                        "actions": [
                            {
                                "type": "scroll",
                                "x": int(pos["x"]),
                                "y": int(pos["y"]),
                                "deltaX": int(delta_x),
                                "deltaY": int(delta_y),
                            }
                        ],
                    }
                ],
            },
        )

    def _scroll_until(self, check, step_x=0, step_y=0, max_steps=20, pause=0.1):
        for _ in range(max_steps):
            if check():
                return True
            self._perform_scroll(step_x, step_y)
            _sleep(pause)
        return check()

    def to_top(self):
        """滚动元素内容到顶部"""
        self._scroll_until(
            lambda: (self._ele._call_js_on_self("(el) => el.scrollTop") or 0) <= 0,
            step_y=-600,
        )
        return self._ele

    def to_bottom(self):
        """滚动元素内容到底部"""
        self._scroll_until(
            lambda: self._ele._call_js_on_self(
                "(el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 2"
            ),
            step_y=600,
        )
        return self._ele

    def down(self, pixel=300):
        """向下滚动"""
        self._perform_scroll(0, pixel)
        return self._ele

    def up(self, pixel=300):
        """向上滚动"""
        self._perform_scroll(0, -pixel)
        return self._ele

    def right(self, pixel=300):
        """向右滚动"""
        self._perform_scroll(pixel, 0)
        return self._ele

    def left(self, pixel=300):
        """向左滚动"""
        self._perform_scroll(-pixel, 0)
        return self._ele

    def to_see(self, center=False):
        """滚动使元素可见"""
        self._ele._owner.scroll.to_see(self._ele, center=center)
        return self._ele
