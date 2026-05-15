# -*- coding: utf-8 -*-
"""Actions - 鼠标/键盘/滚轮动作链

通过 W3C WebDriver BiDi input.performActions 实现。
融合了拟人轨迹算法。

符合 W3C WebDriver BiDi 规范:
  - input.performActions: 执行输入动作序列
  - input.releaseActions: 释放所有按键和按钮
  - 支持 pointer (mouse/touch/pen) / key / wheel 三种输入源
  - pointer action 支持 origin 参数: "viewport" / "pointer" / 元素引用
"""

import random
import math
import time

from .._functions.keys import Keys
from .._functions.sleep import sleep as _sleep

import logging

logger = logging.getLogger('ruyipage')


class Actions(object):
    """动作链管理器（页面级）

    提供鼠标、键盘、滚轮的链式操作 API，一次 perform() 调用将所有
    积累的动作通过 BiDi input.performActions 一起发送给浏览器。

    用法::

        # 基础操作
        page.actions.move_to(ele).click().perform()
        page.actions.key_down(Keys.CTRL).type('a').key_up(Keys.CTRL).perform()
        page.actions.scroll(0, -300).perform()

        # 便捷组合键
        page.actions.combo(Keys.CTRL, 'a').perform()         # Ctrl+A 全选
        page.actions.combo(Keys.CTRL, 'c').perform()         # Ctrl+C 复制

        # 统一命名的双击/右击
        page.actions.double_click(ele).perform()
        page.actions.right_click(ele).perform()

        # 拟人操作
        page.actions.human_move(ele).human_click().perform()
        page.actions.human_type('Hello World').perform()
    """

    def __init__(self, owner):
        """初始化动作链管理器。

        Args:
            owner: 所属的 FirefoxBase 页面/标签页对象，
                   提供 _driver 和 _context_id 用于执行 BiDi 命令。
        """
        self._owner = owner
        self._pointer_actions = []  # pointer (mouse) 动作序列
        self._key_actions = []  # keyboard 动作序列
        self._wheel_actions = []  # wheel (滚轮) 动作序列
        self.curr_x = 0  # 当前鼠标 X 坐标 (视口像素)
        self.curr_y = 0  # 当前鼠标 Y 坐标 (视口像素)

    # ════════════════════════════════════════════════════════════════
    #  鼠标/指针操作
    # ════════════════════════════════════════════════════════════════

    def move_to(
        self, ele_or_loc=None, offset_x=0, offset_y=0, duration=100, origin="viewport"
    ):
        """移动鼠标到指定位置。

        按照 W3C BiDi 规范，pointerMove 的 origin 可以是:
          - "viewport": 坐标相对于视口左上角（默认）
          - "pointer":  坐标相对于当前指针位置
          - 元素引用:   坐标相对于元素中心

        Args:
            ele_or_loc: 目标位置，支持以下类型:
                        - FirefoxElement: 移动到元素中心
                        - dict {'x': int, 'y': int}: 移动到指定坐标
                        - tuple/list (x, y): 移动到指定坐标
                        - None: 仅传 offset 时按视口绝对坐标 (offset_x, offset_y)；
                                offset 也为 0 时保持当前鼠标位置不动。
            offset_x:   在目标位置基础上的 X 偏移量 (像素)。默认 0。
            offset_y:   在目标位置基础上的 Y 偏移量 (像素)。默认 0。
            duration:   移动动画时长 (毫秒)。默认 100。
            origin:     坐标参考原点。默认 "viewport"。
                        传 "pointer" 可实现相对移动。

        Returns:
            self: 支持链式调用。
        """
        # 不传 ele_or_loc 但显式给了 offset 时，按视口绝对坐标处理，
        # 避免 "offset 累加到上一次指针位置" 造成的越界 / 错位 bug。
        if ele_or_loc is None and (offset_x or offset_y):
            x, y = 0, 0
        else:
            x, y = self._resolve_position(ele_or_loc)
        x += offset_x
        y += offset_y
        action = {"type": "pointerMove", "x": int(x), "y": int(y), "duration": duration}
        if origin != "viewport":
            action["origin"] = origin
        self._pointer_actions.append(action)
        self.curr_x = x
        self.curr_y = y
        return self

    def move(self, offset_x=0, offset_y=0, duration=100):
        """相对当前位置移动鼠标。

        Args:
            offset_x: X 方向偏移量 (像素)。正值向右，负值向左。默认 0。
            offset_y: Y 方向偏移量 (像素)。正值向下，负值向上。默认 0。
            duration: 移动动画时长 (毫秒)。默认 100。

        Returns:
            self: 支持链式调用。
        """
        self.curr_x += offset_x
        self.curr_y += offset_y
        self._pointer_actions.append(
            {
                "type": "pointerMove",
                "x": int(self.curr_x),
                "y": int(self.curr_y),
                "duration": duration,
            }
        )
        return self

    def click(self, on_ele=None, times=1):
        """鼠标左键点击。

        Args:
            on_ele: 要点击的元素。传入后会先移动到该元素中心再点击。
                    为 None 时在当前鼠标位置点击。默认 None。
            times:  点击次数。默认 1。传 2 可实现双击效果。

        Returns:
            self: 支持链式调用。
        """
        if on_ele:
            self.move_to(on_ele)

        for _ in range(times):
            self._pointer_actions.append({"type": "pointerDown", "button": 0})
            self._pointer_actions.append({"type": "pause", "duration": 50})
            self._pointer_actions.append({"type": "pointerUp", "button": 0})

        return self

    def double_click(self, on_ele=None):
        """鼠标左键双击。

        Args:
            on_ele: 要双击的元素。为 None 时在当前鼠标位置双击。默认 None。

        Returns:
            self: 支持链式调用。
        """
        return self.click(on_ele, times=2)

    def right_click(self, on_ele=None):
        """鼠标右键点击（上下文菜单）。

        Args:
            on_ele: 要右键点击的元素。为 None 时在当前鼠标位置右击。默认 None。

        Returns:
            self: 支持链式调用。
        """
        if on_ele:
            self.move_to(on_ele)

        self._pointer_actions.append({"type": "pointerDown", "button": 2})
        self._pointer_actions.append({"type": "pause", "duration": 50})
        self._pointer_actions.append({"type": "pointerUp", "button": 2})
        return self

    def middle_click(self, on_ele=None):
        """鼠标中键点击。

        Args:
            on_ele: 要中键点击的元素。为 None 时在当前鼠标位置中击。默认 None。

        Returns:
            self: 支持链式调用。
        """
        if on_ele:
            self.move_to(on_ele)

        self._pointer_actions.append({"type": "pointerDown", "button": 1})
        self._pointer_actions.append({"type": "pause", "duration": 50})
        self._pointer_actions.append({"type": "pointerUp", "button": 1})
        return self

    # 保留旧名称为别名，但标记为已弃用，内部统一调用新方法
    def db_click(self, on_ele=None):
        """双击（已弃用，请使用 double_click）。

        Args:
            on_ele: 要双击的元素。默认 None。

        Returns:
            self: 支持链式调用。
        """
        return self.double_click(on_ele)

    def r_click(self, on_ele=None):
        """右键点击（已弃用，请使用 right_click）。

        Args:
            on_ele: 要右键点击的元素。默认 None。

        Returns:
            self: 支持链式调用。
        """
        return self.right_click(on_ele)

    def hold(self, on_ele=None, button=0):
        """按住鼠标按钮不放。

        Args:
            on_ele: 在指定元素上按住。为 None 时在当前位置按住。默认 None。
            button: 鼠标按钮编号。0=左键, 1=中键, 2=右键。默认 0（左键）。

        Returns:
            self: 支持链式调用。
        """
        if on_ele:
            self.move_to(on_ele)
        self._pointer_actions.append({"type": "pointerDown", "button": button})
        return self

    def release(self, on_ele=None, button=0):
        """释放鼠标按钮。

        Args:
            on_ele: 在指定元素上释放。为 None 时在当前位置释放。默认 None。
            button: 鼠标按钮编号。0=左键, 1=中键, 2=右键。默认 0（左键）。

        Returns:
            self: 支持链式调用。
        """
        if on_ele:
            self.move_to(on_ele)
        self._pointer_actions.append({"type": "pointerUp", "button": button})
        return self

    def drag_to(self, source, target, duration=500, steps=20):
        """从源位置拖拽到目标位置。

        完整的拖拽操作: 移动到源 → 按下 → 分步移动到目标 → 释放。
        所有 pointerMove 使用 origin="viewport" 以确保坐标绝对精确。

        Args:
            source:   拖拽起始位置。支持元素、坐标 dict、坐标 tuple。
            target:   拖拽目标位置。支持元素、坐标 dict、坐标 tuple。
            duration: 拖拽总时长（毫秒）。数值越大，拖拽越慢。默认 500。
            steps:    拖拽过程中的移动步数。步数越多越平滑。默认 20。

        Returns:
            self: 支持链式调用。
        """
        sx, sy = self._resolve_position(source)
        ex, ey = self._resolve_position(target)
        step_dur = max(1, duration // steps)

        self._pointer_actions.append(
            {
                "type": "pointerMove",
                "origin": "viewport",
                "x": int(sx),
                "y": int(sy),
                "duration": 0,
            }
        )
        self._pointer_actions.append({"type": "pointerDown", "button": 0})
        self._pointer_actions.append({"type": "pause", "duration": 120})

        for i in range(1, steps + 1):
            px = int(sx + (ex - sx) * i / steps)
            py = int(sy + (ey - sy) * i / steps)
            self._pointer_actions.append(
                {
                    "type": "pointerMove",
                    "origin": "viewport",
                    "x": px,
                    "y": py,
                    "duration": step_dur,
                }
            )

        self._pointer_actions.append({"type": "pause", "duration": 120})
        self._pointer_actions.append({"type": "pointerUp", "button": 0})

        self.curr_x = int(ex)
        self.curr_y = int(ey)
        return self

    def drag(self, source, target, duration=500, steps=20):
        """新手友好别名：等价于 drag_to(source, target, ...)。"""
        return self.drag_to(source, target, duration=duration, steps=steps)

    # ════════════════════════════════════════════════════════════════
    #  键盘操作
    # ════════════════════════════════════════════════════════════════

    def key_down(self, key):
        """按下键盘按键（不松开）。

        Args:
            key: 键值字符串。普通字符如 'a'、'1'，
                 或使用 Keys 常量如 Keys.CTRL、Keys.SHIFT、Keys.ENTER。

        Returns:
            self: 支持链式调用。
        """
        self._key_actions.append({"type": "keyDown", "value": key})
        return self

    def key_up(self, key):
        """释放键盘按键。

        Args:
            key: 键值字符串，需与之前 key_down 的按键一致。

        Returns:
            self: 支持链式调用。
        """
        self._key_actions.append({"type": "keyUp", "value": key})
        return self

    def combo(self, *keys):
        """执行组合键操作（自动按下后释放）。

        按顺序按下所有键，然后倒序释放。
        例如 combo(Keys.CTRL, 'a') 等价于:
          key_down(Keys.CTRL) → key_down('a') → key_up('a') → key_up(Keys.CTRL)

        Args:
            *keys: 要按下的键值序列。
                   例: combo(Keys.CTRL, 'a') 执行 Ctrl+A
                   例: combo(Keys.CTRL, Keys.SHIFT, 'i') 执行 Ctrl+Shift+I

        Returns:
            self: 支持链式调用。
        """
        for k in keys:
            self._key_actions.append({"type": "keyDown", "value": k})
        for k in reversed(keys):
            self._key_actions.append({"type": "keyUp", "value": k})
        return self

    def type(self, text, interval=0):
        """逐字符输入文本。

        每个字符按下后立即释放，模拟真实键盘输入。

        Args:
            text:     要输入的文本字符串。
            interval: 每次击键之间的间隔 (毫秒)。0 表示无间隔。默认 0。

        Returns:
            self: 支持链式调用。
        """
        for char in str(text):
            self._key_actions.append({"type": "keyDown", "value": char})
            if interval:
                self._key_actions.append({"type": "pause", "duration": interval})
            self._key_actions.append({"type": "keyUp", "value": char})
        return self

    def press(self, key):
        """按下并释放单个按键。

        等价于 key_down(key) + key_up(key)。
        适用于 Enter、Tab、Escape 等功能键。

        Args:
            key: 键值字符串。例: Keys.ENTER, Keys.TAB, Keys.ESCAPE。

        Returns:
            self: 支持链式调用。
        """
        self._key_actions.append({"type": "keyDown", "value": key})
        self._key_actions.append({"type": "keyUp", "value": key})
        return self

    # ════════════════════════════════════════════════════════════════
    #  滚轮操作
    # ════════════════════════════════════════════════════════════════

    def scroll(self, delta_x=0, delta_y=0, on_ele=None, origin="viewport"):
        """滚轮滚动。

        按照 W3C BiDi 规范，wheel scroll 动作支持 deltaX/deltaY
        以及 origin 参考原点。

        Args:
            delta_x: 水平滚动量 (像素)。正值向右，负值向左。默认 0。
            delta_y: 垂直滚动量 (像素)。正值向下，负值向上。默认 0。
            on_ele:  在指定元素上滚动。传入后会使用该元素的中心坐标。
                     默认 None（使用当前鼠标位置）。
            origin:  坐标参考原点。"viewport"(默认) 或 "pointer"。

        Returns:
            self: 支持链式调用。
        """
        x, y = self.curr_x, self.curr_y
        if on_ele:
            x, y = self._resolve_position(on_ele)

        action = {
            "type": "scroll",
            "x": int(x),
            "y": int(y),
            "deltaX": int(delta_x),
            "deltaY": int(delta_y),
        }
        if origin != "viewport":
            action["origin"] = origin
        self._wheel_actions.append(action)
        return self

    # ════════════════════════════════════════════════════════════════
    #  等待 / 暂停
    # ════════════════════════════════════════════════════════════════

    def wait(self, seconds):
        """在动作序列中插入等待。

        在 pointer 和 key 两个通道同时插入 pause，确保同步等待。

        Args:
            seconds: 等待时长 (秒)。支持小数，如 0.5 表示 500ms。

        Returns:
            self: 支持链式调用。
        """
        ms = int(seconds * 1000)
        self._pointer_actions.append({"type": "pause", "duration": ms})
        self._key_actions.append({"type": "pause", "duration": ms})
        return self

    # ════════════════════════════════════════════════════════════════
    #  执行与释放
    # ════════════════════════════════════════════════════════════════

    def perform(self):
        """执行积累的所有动作。

        将 pointer、key、wheel 三个通道的动作序列
        通过 BiDi input.performActions 命令一次性发送给浏览器。
        执行后自动清空动作队列。

        Returns:
            self: 支持链式调用。
        """
        actions = []

        # 保存副本供可视化使用
        pointer_copy = self._pointer_actions[:]
        key_copy = self._key_actions[:]

        if self._pointer_actions:
            actions.append(
                {
                    "type": "pointer",
                    "id": "mouse0",
                    "parameters": {"pointerType": "mouse"},
                    "actions": self._pointer_actions[:],
                }
            )

        if self._key_actions:
            actions.append(
                {"type": "key", "id": "keyboard0", "actions": self._key_actions[:]}
            )

        if self._wheel_actions:
            actions.append(
                {"type": "wheel", "id": "wheel0", "actions": self._wheel_actions[:]}
            )

        try:
            if actions:
                self._owner._driver._browser_driver.run(
                    "input.performActions",
                    {"context": self._owner._context_id, "actions": actions},
                )
        finally:
            # 无论成功或失败都清空队列，避免残留动作污染下一次 perform()
            self._pointer_actions.clear()
            self._key_actions.clear()
            self._wheel_actions.clear()

        # 可视化渲染（执行后注入）
        self._send_visual_data(pointer_copy, key_copy)

        return self

    def release_all(self):
        """释放所有按住的按键和鼠标按钮。

        通过 BiDi input.releaseActions 命令重置所有输入状态，
        包括按住的键盘键和鼠标按钮。

        Returns:
            self: 支持链式调用。
        """
        self._owner._driver._browser_driver.run(
            "input.releaseActions", {"context": self._owner._context_id}
        )
        return self

    # ════════════════════════════════════════════════════════════════
    #  拟人化操作
    # ════════════════════════════════════════════════════════════════

    def human_move(self, ele_or_loc, style=None, algorithm=None):
        """拟人化鼠标移动。

        支持两套拟人轨迹算法：

        - ``bezier``：当前默认算法，路径更平滑，支持 ``style`` 变体
        - ``windmouse``：模拟风力 + 重力拖拽的轨迹，路径更飘逸

        这两个算法最终都会生成一组 ``pointerMove`` 序列，
        因此对现有动作链和可视化调试保持兼容。

        Args:
            ele_or_loc: 目标位置。支持元素、坐标 dict、坐标 tuple。
            style:      路径风格，可选值:
                        - 'line': 直线路径
                        - 'arc': 弧线路径
                        - 'line_then_arc': 先直线后弧线
                        - 'line_overshoot_arc_back': 超越目标后弧线回拉
                        - None: 随机选择（默认）
                        仅 ``algorithm='bezier'`` 时生效。
            algorithm:  轨迹算法名。可选 ``'bezier'`` 或 ``'windmouse'``。
                        传 ``None`` 时优先使用 ``FirefoxOptions`` 上配置的默认值。

        Returns:
            self: 支持链式调用。
        """
        start_x, start_y = self.curr_x, self.curr_y
        is_element = hasattr(ele_or_loc, "states") and hasattr(ele_or_loc, "_get_center")

        target_x, target_y = self._resolve_position(ele_or_loc)

        # 自动滚动到可见
        if hasattr(ele_or_loc, "states") and hasattr(ele_or_loc.states, "is_whole_in_viewport"):
            if not ele_or_loc.states.is_whole_in_viewport:
                try:
                    self._owner.scroll.to_see(ele_or_loc, center=True)
                    _sleep(random.uniform(0.1, 0.2))
                    # 滚动后重新取一次元素中心，避免继续使用滚动前的旧视口坐标。
                    if is_element:
                        target_x, target_y = self._resolve_position(ele_or_loc)
                except Exception as e:
                    logger.debug("预滚动元素到视口失败: %s", e)

        min_x, max_x, min_y, max_y = self._get_viewport_bounds()
        start_x, start_y = self._clamp_point(start_x, start_y, min_x, max_x, min_y, max_y)
        target_x, target_y = self._clamp_point(target_x, target_y, min_x, max_x, min_y, max_y)

        algorithm = self._resolve_human_algorithm(algorithm)
        path = self._build_human_move_path(
            (start_x, start_y),
            (target_x, target_y),
            algorithm=algorithm,
            style=style,
        )

        # 执行移动
        for px, py in path:
            px, py = self._clamp_point(px, py, min_x, max_x, min_y, max_y)
            self._pointer_actions.append(
                {
                    "type": "pointerMove",
                    "x": int(px),
                    "y": int(py),
                    "duration": random.randint(8, 20),
                }
            )

        # 悬停微调
        for _ in range(random.randint(2, 4)):
            hover_x, hover_y = self._clamp_point(
                target_x + random.randint(-2, 2),
                target_y + random.randint(-1, 1),
                min_x,
                max_x,
                min_y,
                max_y,
            )
            self._pointer_actions.append(
                {
                    "type": "pointerMove",
                    "x": int(hover_x),
                    "y": int(hover_y),
                    "duration": random.randint(20, 50),
                }
            )

        # 精确落点
        target_x, target_y = self._clamp_point(target_x, target_y, min_x, max_x, min_y, max_y)
        self._pointer_actions.append(
            {
                "type": "pointerMove",
                "x": int(target_x),
                "y": int(target_y),
                "duration": random.randint(15, 30),
            }
        )

        self.curr_x = target_x
        self.curr_y = target_y
        return self

    def human_click(self, on_ele=None, button="left", algorithm=None, style=None):
        """拟人化点击。

        先用拟人轨迹移动到目标，再加入自然的悬停延迟后点击。

        Args:
            on_ele: 要点击的元素。为 None 时在当前位置点击。默认 None。
            button: 鼠标按钮。'left'=左键, 'middle'=中键, 'right'=右键。
                    默认 'left'。
            algorithm: 轨迹算法名。可选 ``'bezier'`` 或 ``'windmouse'``。
                       传 ``None`` 时使用 ``FirefoxOptions`` 默认值。
            style: 点击前移动时的 Bézier 路径风格。
                   仅 ``algorithm='bezier'`` 时生效。

        Returns:
            self: 支持链式调用。
        """
        if on_ele:
            self.human_move(on_ele, style=style, algorithm=algorithm)

        # 悬停随机延迟
        self.wait(random.uniform(0.05, 0.15))

        # 点击
        button_map = {"left": 0, "middle": 1, "right": 2}
        btn = button_map.get(button, 0)

        self._pointer_actions.append({"type": "pointerDown", "button": btn})
        self._pointer_actions.append(
            {"type": "pause", "duration": random.randint(40, 90)}
        )
        self._pointer_actions.append({"type": "pointerUp", "button": btn})

        return self

    def _build_human_move_path(self, start, end, algorithm="bezier", style=None):
        """按指定算法生成拟人鼠标路径点。"""
        if algorithm == "windmouse":
            return self._build_windmouse_path(start, end)
        return self._build_bezier_path(start, end, style=style)

    def _build_bezier_path(self, start, end, style=None):
        """生成当前默认的 Bézier 拟人鼠标路径。"""
        start_x, start_y = start
        target_x, target_y = end
        dist = math.hypot(target_x - start_x, target_y - start_y) or 1.0

        # 近距离模式（<140px）
        if dist <= 140:
            steps = int(max(6, min(14, round(dist / random.uniform(12.0, 20.0)))))
            oversample = random.randint(2, 3)
            slight_curve = random.random() < 0.35
            curvature = random.uniform(0.20, 0.45)

            if slight_curve:
                raw_path = self._arc_path(
                    (start_x, start_y),
                    (target_x, target_y),
                    steps,
                    curvature,
                    oversample,
                )
            else:
                raw_path = self._line_path(
                    (start_x, start_y), (target_x, target_y), steps, oversample
                )

            path = self._apply_jitter(
                raw_path,
                max_norm=min(2.2, max(0.6, dist * 0.008)),
                max_tan=min(1.2, max(0.3, dist * 0.004)),
            )
        else:
            # 长距离模式
            steps = int(max(12, min(52, round(dist / random.uniform(10.0, 22.0)))))
            oversample = random.randint(3, 4)
            curvature = random.uniform(0.55, 0.82)

            # 选择路径风格
            if style is None:
                styles = ("line_then_arc", "line", "arc", "line_overshoot_arc_back")
                weights = (0.40, 0.22, 0.28, 0.10)
                r = random.random()
                acc = 0.0
                for s, w in zip(styles, weights):
                    acc += w
                    if r <= acc:
                        style = s
                        break

            if style == "line_then_arc":
                ratio = random.uniform(0.45, 0.75)
                mid = self._lerp_pt((start_x, start_y), (target_x, target_y), ratio)
                seg1 = self._line_path(
                    (start_x, start_y), mid, max(2, int(steps * ratio)), oversample
                )
                seg2 = self._arc_path(
                    mid,
                    (target_x, target_y),
                    max(2, steps - int(steps * ratio)),
                    curvature,
                    oversample,
                )
                raw_path = self._concat_paths(seg1, seg2)
            elif style == "line":
                raw_path = self._line_path(
                    (start_x, start_y), (target_x, target_y), steps, oversample
                )
            elif style == "arc":
                raw_path = self._arc_path(
                    (start_x, start_y),
                    (target_x, target_y),
                    steps,
                    curvature,
                    oversample,
                )
            else:  # line_overshoot_arc_back
                ovp = self._overshoot_point((start_x, start_y), (target_x, target_y))
                seg1 = self._line_path(
                    (start_x, start_y), ovp, max(2, int(steps * 0.55)), oversample
                )
                ctrl = self._return_arc_ctrl(ovp, (target_x, target_y))
                seg2 = self._arc_path(
                    ovp,
                    (target_x, target_y),
                    max(2, steps - len(seg1) // max(1, oversample)),
                    curvature,
                    oversample,
                    ctrl,
                )
                raw_path = self._concat_paths(seg1, seg2)

            path = (
                self._apply_jitter(
                    raw_path,
                    max_norm=min(7.5, max(2.5, dist * random.uniform(0.006, 0.011))),
                    max_tan=min(4.0, max(1.2, dist * random.uniform(0.003, 0.008))),
                )
                if random.random() < 0.75
                else raw_path
            )
        return path

    def _build_windmouse_path(self, start, end):
        """生成 WindMouse 风格拟人鼠标路径。

        该算法模拟鼠标在“风力扰动 + 向目标收敛的重力”共同作用下的移动。
        相比平滑 Bézier，它的路径会更像真人手部微抖后的拖拽轨迹。

        这里采用适合产品默认值的轻量实现：
            - 不暴露复杂参数给普通用户
            - 保证最终一定收敛到目标点
            - 保持与现有 action_visual 的点列渲染兼容
        """
        sx, sy = start
        ex, ey = end
        path = []

        x = float(sx)
        y = float(sy)
        velocity_x = 0.0
        velocity_y = 0.0
        wind_x = 0.0
        wind_y = 0.0

        gravity = random.uniform(7.0, 10.0)
        wind = random.uniform(2.5, 4.2)
        max_step = random.uniform(10.0, 18.0)
        target_area = random.uniform(9.0, 14.0)
        damping = random.uniform(0.78, 0.88)
        close_damping = random.uniform(0.55, 0.72)

        max_iters = 600
        for _ in range(max_iters):
            dx = ex - x
            dy = ey - y
            dist = math.hypot(dx, dy)
            if dist <= 1.0:
                break

            wind_mag = min(wind, dist)
            if dist >= target_area:
                wind_x = wind_x / math.sqrt(3) + random.uniform(-wind_mag, wind_mag) / math.sqrt(5)
                wind_y = wind_y / math.sqrt(3) + random.uniform(-wind_mag, wind_mag) / math.sqrt(5)
            else:
                wind_x *= close_damping
                wind_y *= close_damping
                max_step = max(3.0, max_step * 0.93)

            if dist > 0:
                velocity_x += wind_x + gravity * dx / dist
                velocity_y += wind_y + gravity * dy / dist

            velocity_x *= damping
            velocity_y *= damping

            velocity_mag = math.hypot(velocity_x, velocity_y)
            if velocity_mag > max_step and velocity_mag > 0:
                scale = max_step / velocity_mag
                velocity_x *= scale
                velocity_y *= scale

            prev_x = x
            prev_y = y
            x += velocity_x
            y += velocity_y

            if int(prev_x) != int(x) or int(prev_y) != int(y):
                path.append((x, y))

        if not path or int(path[-1][0]) != int(ex) or int(path[-1][1]) != int(ey):
            path.append((float(ex), float(ey)))
        return path

    def _resolve_human_algorithm(self, algorithm=None):
        """解析本次 human 动作应使用的轨迹算法。"""
        if algorithm:
            value = str(algorithm).strip().lower()
        else:
            value = "bezier"
            browser = getattr(self._owner, "_browser", None)
            options = getattr(browser, "options", None) if browser else None
            if options:
                value = str(getattr(options, "human_algorithm", "bezier") or "bezier").strip().lower()

        if value not in ("bezier", "windmouse"):
            raise ValueError('human algorithm 必须是 "bezier" 或 "windmouse"')
        return value

    def _get_viewport_bounds(self):
        """返回当前 context 的有效 viewport 边界。"""
        width, height = self._owner.rect.viewport_size
        width = max(1, int(width or 0))
        height = max(1, int(height or 0))
        return 0, width - 1, 0, height - 1

    def _clamp_point(self, x, y, min_x, max_x, min_y, max_y):
        """将坐标限制在当前 viewport 内。"""
        x = min(max(float(x), min_x), max_x)
        y = min(max(float(y), min_y), max_y)
        return x, y

    def human_type(self, text, min_delay=0.045, max_delay=0.24):
        """拟人化输入文本。

        每个字符的按键间隔随机化，模拟真人打字速度波动。

        Args:
            text:      要输入的文本字符串。
            min_delay: 最小击键间隔 (秒)。默认 0.045。
            max_delay: 最大击键间隔 (秒)。默认 0.24。

        Returns:
            self: 支持链式调用。
        """
        for char in str(text):
            self._key_actions.append({"type": "keyDown", "value": char})

            # 随机击键间隔
            interval = int(random.uniform(min_delay, max_delay) * 1000)
            if interval > 0:
                self._key_actions.append({"type": "pause", "duration": interval})

            self._key_actions.append({"type": "keyUp", "value": char})

        return self

    # ════════════════════════════════════════════════════════════════
    #  内部辅助方法
    # ════════════════════════════════════════════════════════════════

    def _resolve_position(self, ele_or_loc):
        """解析目标位置为 (x, y) 坐标。

        Args:
            ele_or_loc: 元素、坐标 dict、坐标 tuple/list、或 None。

        Returns:
            tuple: (x, y) 坐标元组。
        """
        if ele_or_loc is None:
            return self.curr_x, self.curr_y

        if isinstance(ele_or_loc, dict):
            return ele_or_loc.get("x", 0), ele_or_loc.get("y", 0)

        if isinstance(ele_or_loc, (list, tuple)):
            return ele_or_loc[0], ele_or_loc[1]

        # 假定是元素对象
        pos = getattr(ele_or_loc, "_get_center", lambda: None)()
        if pos:
            return pos.get("x", 0), pos.get("y", 0)

        return self.curr_x, self.curr_y

    # ========== 拟人轨迹算法辅助方法 ==========

    def _ease_out_cubic(self, t):
        """三次缓出函数。"""
        return 1 - (1 - t) ** 3

    def _ease_in_out_quad(self, t):
        """二次缓入缓出函数。"""
        return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2

    def _lerp(self, a, b, t):
        """线性插值。"""
        return a + (b - a) * t

    def _lerp_pt(self, p0, p1, t):
        """点的线性插值。"""
        return (self._lerp(p0[0], p1[0], t), self._lerp(p0[1], p1[1], t))

    def _bezier_q(self, p0, p1, p2, t):
        """二次贝塞尔曲线。"""
        s = 1 - t
        x = s * s * p0[0] + 2 * s * t * p1[0] + t * t * p2[0]
        y = s * s * p0[1] + 2 * s * t * p1[1] + t * t * p2[1]
        return (x, y)

    def _control_point_arc(self, start, end, curvature=0.75):
        """生成弧形控制点。"""
        mx = (start[0] + end[0]) / 2.0
        my = (start[1] + end[1]) / 2.0
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dist = math.hypot(dx, dy) or 1.0
        nx = -dy / dist
        ny = dx / dist
        offset = dist * curvature * random.choice([-1, 1])
        return (mx + nx * offset, my + ny * offset)

    def _arc_path(self, start, end, steps, curvature, oversample, ctrl=None):
        """生成弧形路径。"""
        if ctrl is None:
            ctrl = self._control_point_arc(start, end, curvature)
        path = []
        for i in range(steps * oversample + 1):
            t = i / float(steps * oversample)
            path.append(self._bezier_q(start, ctrl, end, t))
        return path

    def _line_path(self, start, end, steps, oversample):
        """生成直线路径。"""
        path = []
        for i in range(steps * oversample + 1):
            t = i / float(steps * oversample)
            path.append(self._lerp_pt(start, end, t))
        return path

    def _smooth_series(self, n, sigma, smooth_k):
        """生成平滑相关噪声序列。"""
        raw = [random.gauss(0, sigma) for _ in range(n)]
        if smooth_k <= 1:
            return raw
        smoothed = []
        for i in range(n):
            window = []
            for j in range(max(0, i - smooth_k + 1), min(n, i + smooth_k)):
                window.append(raw[j])
            smoothed.append(sum(window) / len(window))
        return smoothed

    def _apply_jitter(self, path, max_norm=5.0, max_tan=2.5):
        """对路径应用抖动。"""
        if len(path) < 2:
            return path
        n = len(path)
        norm_jitter = self._smooth_series(n, max_norm / 2.5, max(1, n // 8))
        tan_jitter = self._smooth_series(n, max_tan / 2.5, max(1, n // 8))

        result = []
        for i, (px, py) in enumerate(path):
            if i == 0 or i == n - 1:
                result.append((px, py))
                continue

            dx = path[i][0] - path[i - 1][0]
            dy = path[i][1] - path[i - 1][1]
            dist = math.hypot(dx, dy) or 1.0
            tx = -dy / dist
            ty = dx / dist
            nx = dx / dist
            ny = dy / dist

            jx = tx * norm_jitter[i] + nx * tan_jitter[i]
            jy = ty * norm_jitter[i] + ny * tan_jitter[i]
            result.append((px + jx, py + jy))

        return result

    def _concat_paths(self, seg1, seg2):
        """连接两段路径。"""
        return seg1[:-1] + seg2 if seg1 and seg2 else seg1 or seg2

    def _overshoot_point(self, start, end):
        """计算超调点。"""
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dist = math.hypot(dx, dy) or 1.0
        overshoot_ratio = random.uniform(0.08, 0.18)
        ox = end[0] + (dx / dist) * dist * overshoot_ratio
        oy = end[1] + (dy / dist) * dist * overshoot_ratio
        return (ox, oy)

    def _return_arc_ctrl(self, overshoot, target):
        """生成回拉弧形控制点。"""
        mx = (overshoot[0] + target[0]) / 2.0
        my = (overshoot[1] + target[1]) / 2.0
        dx = target[0] - overshoot[0]
        dy = target[1] - overshoot[1]
        dist = math.hypot(dx, dy) or 1.0
        nx = -dy / dist
        ny = dx / dist
        offset = dist * random.uniform(0.3, 0.6) * random.choice([-1, 1])
        return (mx + nx * offset, my + ny * offset)

    # ════════════════════════════════════════════════════════════════
    #  可视化数据注入
    # ════════════════════════════════════════════════════════════════

    def _is_visual_enabled(self):
        """检查是否启用了 action_visual。"""
        browser = getattr(self._owner, "_browser", None)
        if not browser:
            return False
        options = getattr(browser, "options", None)
        return bool(options and getattr(options, "action_visual_enabled", False))

    def _send_visual_data(self, pointer_actions, key_actions):
        """将鼠标动作数据注入页面 JS 端进行可视化渲染。"""
        if not self._is_visual_enabled():
            return

        try:
            # 提取移动坐标点
            move_points = []
            for a in pointer_actions:
                if a.get("type") == "pointerMove":
                    move_points.append([int(a.get("x", 0)), int(a.get("y", 0))])

            # 提取点击事件
            clicks = []
            last_x, last_y = self.curr_x, self.curr_y
            for a in pointer_actions:
                if a.get("type") == "pointerMove":
                    last_x = int(a.get("x", last_x))
                    last_y = int(a.get("y", last_y))
                elif a.get("type") == "pointerDown":
                    clicks.append([last_x, last_y, a.get("button", 0)])

            # 构建 JS 调用
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

            if js_parts:
                self._owner.run_js(";".join(js_parts), as_expr=True)
        except Exception:
            pass
