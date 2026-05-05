# -*- coding: utf-8 -*-
"""W3C WebDriver BiDi input 模块命令封装。

本模块封装了 W3C WebDriver BiDi 规范中 input 模块的全部命令:
  - input.performActions: 执行输入动作序列
  - input.releaseActions: 释放所有按键和鼠标按钮
  - input.setFiles: 设置文件输入元素的文件列表

支持的输入源类型 (source type):
  - "key":     键盘输入源 (keyDown / keyUp / pause)
  - "pointer": 指针输入源 (pointerMove / pointerDown / pointerUp / pause)
               pointerType: "mouse" / "touch" / "pen"
  - "wheel":   滚轮输入源 (scroll / pause)
  - "none":    空输入源 (pause)

支持的 pointer origin (pointerMove 的坐标参考原点):
  - "viewport": 相对视口左上角 (默认)
  - "pointer":  相对当前指针位置
  - 元素引用:   相对元素中心 (input.ElementOrigin)

Pen 触控笔扩展属性 (pointerMove / pointerDown):
  - pressure:            压力 0.0-1.0
  - tiltX / tiltY:       倾斜角度 -90~90
  - twist:               旋转角度 0~359
  - tangentialPressure:  切向压力 -1.0~1.0
  - altitudeAngle:       海拔角 0~π/2
  - azimuthAngle:        方位角 0~2π
  - width / height:      接触面几何尺寸
"""

import math
import random


# ── 轨迹算法 ──────────────────────────────────────────────────────


def _ease_out_cubic(t):
    """三次缓出缓动函数。

    Args:
        t: 归一化时间 [0, 1]。

    Returns:
        float: 缓动后的值 [0, 1]。
    """
    return 1 - (1 - t) ** 3


def _ease_in_out_quad(t):
    """二次缓入缓出缓动函数。

    Args:
        t: 归一化时间 [0, 1]。

    Returns:
        float: 缓动后的值 [0, 1]。
    """
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def _lerp(a, b, t):
    """线性插值。

    Args:
        a: 起始值。
        b: 结束值。
        t: 插值因子 [0, 1]。

    Returns:
        float: 插值结果。
    """
    return a + (b - a) * t


def _lerp_pt(p0, p1, t):
    """二维点的线性插值。

    Args:
        p0: 起始点 (x, y)。
        p1: 结束点 (x, y)。
        t:  插值因子 [0, 1]。

    Returns:
        tuple: 插值点 (x, y)。
    """
    return (_lerp(p0[0], p1[0], t), _lerp(p0[1], p1[1], t))


def _bezier_q(p0, p1, p2, t):
    """二次贝塞尔曲线插值。

    Args:
        p0: 起始点 (x, y)。
        p1: 控制点 (x, y)。
        p2: 结束点 (x, y)。
        t:  插值因子 [0, 1]。

    Returns:
        tuple: 曲线上的点 (x, y)。
    """
    u = 1 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


def _ctrl_arc(start, end, curvature=0.75):
    """生成弧形路径的控制点。

    在起点和终点的中垂线方向上偏移，产生弧形效果。

    Args:
        start:     起始点 (x, y)。
        end:       结束点 (x, y)。
        curvature: 弯曲程度 (0~1)。值越大弧越弯。默认 0.75。

    Returns:
        tuple: 控制点坐标 (x, y)。
    """
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy) or 1.0
    mx, my = (sx + ex) * 0.5, (sy + ey) * 0.5
    nx, ny = -dy / dist, dx / dist
    side = random.choice((-1.0, 1.0))
    offset = max(60.0, min(dist * curvature + random.uniform(-0.12, 0.12) * dist * curvature, 520.0))
    return (mx + nx * offset * side, my + ny * offset * side)


def _arc_path(start, end, steps, curvature=0.75, oversample=4, ctrl=None):
    """生成弧形路径点列表。

    Args:
        start:      起始点 (x, y)。
        end:        结束点 (x, y)。
        steps:      基础步数。
        curvature:  弯曲程度 (0~1)。默认 0.75。
        oversample: 过采样倍率。默认 4。
        ctrl:       自定义控制点。为 None 时自动生成。默认 None。

    Returns:
        list: 路径点列表 [(x, y), ...]。
    """
    c = ctrl or _ctrl_arc(start, end, curvature)
    total = max(steps * oversample, steps)
    return [_bezier_q(start, c, end, _ease_out_cubic(i / total)) for i in range(1, total + 1)]


def _line_path(start, end, steps, oversample=4, ease=None):
    """生成直线路径点列表。

    Args:
        start:      起始点 (x, y)。
        end:        结束点 (x, y)。
        steps:      基础步数。
        oversample: 过采样倍率。默认 4。
        ease:       缓动函数。为 None 时使用 _ease_in_out_quad。默认 None。

    Returns:
        list: 路径点列表 [(x, y), ...]。
    """
    ease = ease or _ease_in_out_quad
    total = max(steps * oversample, steps)
    return [_lerp_pt(start, end, ease(i / total)) for i in range(1, total + 1)]


def _smooth_series(n, sigma, smooth_k):
    """生成平滑噪声序列（用于抖动效果）。

    Args:
        n:        序列长度。
        sigma:    高斯噪声标准差。
        smooth_k: 平滑窗口大小。

    Returns:
        list: 平滑后的噪声序列。
    """
    x = 0.0
    raw = []
    for _ in range(n):
        x += random.gauss(0, sigma)
        raw.append(x)
    if smooth_k <= 1:
        return raw
    win = max(1, int(smooth_k))
    acc = sum(raw[:win])
    out = [acc / win]
    for i in range(win, n):
        acc += raw[i] - raw[i - win]
        out.append(acc / win)
    return [out[0]] * (n - len(out)) + out


def _apply_jitter(path, max_norm=6.0, max_tan=3.0, keep_end=6, keep_start=6):
    """对路径应用自然抖动效果。

    在路径的法线和切线方向添加平滑的随机偏移，
    首尾段逐渐衰减以保证起止点精确。

    Args:
        path:       路径点列表 [(x, y), ...]。
        max_norm:   法线方向最大偏移 (像素)。默认 6.0。
        max_tan:    切线方向最大偏移 (像素)。默认 3.0。
        keep_end:   尾部无抖动的点数。默认 6。
        keep_start: 头部无抖动的点数。默认 6。

    Returns:
        list: 抖动后的路径点列表。
    """
    n = len(path)
    if n < 3:
        return path
    tangents = []
    for i in range(n):
        if i == 0:
            d = (path[1][0] - path[0][0], path[1][1] - path[0][1])
        elif i == n - 1:
            d = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        else:
            d = (path[i + 1][0] - path[i - 1][0], path[i + 1][1] - path[i - 1][1])
        dl = math.hypot(d[0], d[1]) or 1.0
        tx, ty = d[0] / dl, d[1] / dl
        tangents.append((tx, ty, -ty, tx))
    tan_n = _smooth_series(n, 0.55, max(5, n // 30))
    nor_n = _smooth_series(n, 0.9, max(6, n // 28))
    out = []
    for i, (px, py) in enumerate(path):
        t = i / (n - 1)
        edge = (0.5 - abs(t - 0.5)) / 0.5
        if i < keep_start:
            w = i / keep_start
        elif i > n - keep_end - 1:
            w = (n - 1 - i) / keep_end
        else:
            w = 1.0
        w = max(0.0, min(1.0, 0.35 + 0.65 * edge)) * w
        tx, ty, nx, ny = tangents[i]
        out.append((px + tx * tan_n[i] * max_tan * w + nx * nor_n[i] * max_norm * w,
                    py + ty * tan_n[i] * max_tan * w + ny * nor_n[i] * max_norm * w))
    return out


def _overshoot_pt(start, end):
    """计算超越目标后的过冲点。

    Args:
        start: 起始点 (x, y)。
        end:   目标点 (x, y)。

    Returns:
        tuple: 过冲点坐标 (x, y)。
    """
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    px = max(24.0, min(dist * random.uniform(0.10, 0.25), 180.0))
    return (ex + ux * px, ey + uy * px)


def _concat(*segs):
    """连接多段路径，去除重复的衔接点。

    Args:
        *segs: 路径段列表，每段为 [(x, y), ...] 格式。

    Returns:
        list: 连接后的完整路径。
    """
    out = []
    for seg in segs:
        if not seg:
            continue
        if out and seg and out[-1] == seg[0]:
            out.extend(seg[1:])
        else:
            out.extend(seg)
    return out


# ── 公开 API ─────────────────────────────────────────────────────


def build_human_mouse_path(start, end):
    """生成拟人鼠标轨迹点列表。

    自动随机选择路径风格（直线/弧线/直线+弧线/超出回拉），
    并添加自然的抖动效果，模拟真人鼠标操作。

    Args:
        start: 起始坐标 (x, y)。
        end:   目标坐标 (x, y)。

    Returns:
        list: 轨迹点列表 [(float, float), ...]。
    """
    sx, sy = start
    ex, ey = end
    dist = math.hypot(ex - sx, ey - sy) or 1.0
    steps = int(max(12, min(52, round(dist / random.uniform(10, 22)))))
    oversample = random.randint(3, 4)
    curvature = random.uniform(0.55, 0.82)

    styles = ("line_then_arc", "line", "arc", "line_overshoot_arc_back")
    weights = (0.40, 0.22, 0.28, 0.10)
    r = random.random()
    acc = 0.0
    style = styles[-1]
    for name, w in zip(styles, weights):
        acc += w
        if r <= acc:
            style = name
            break

    if style == "line_then_arc":
        ratio = random.uniform(0.45, 0.75)
        mid = _lerp_pt(start, end, ratio)
        raw = _concat(_line_path(start, mid, max(2, int(steps * ratio)), oversample),
                      _arc_path(mid, end, max(2, steps - int(steps * ratio)), curvature, oversample))
    elif style == "line":
        raw = _line_path(start, end, steps, oversample)
    elif style == "arc":
        raw = _arc_path(start, end, steps, curvature, oversample)
    else:
        ovp = _overshoot_pt(start, end)
        ctrl = _ctrl_arc(ovp, end, 0.9)
        raw = _concat(_line_path(start, ovp, max(2, int(steps * 0.55)), oversample),
                      _arc_path(ovp, end, max(2, steps - int(steps * 0.55)), curvature, oversample, ctrl))

    max_norm = min(7.5, max(2.5, dist * random.uniform(0.006, 0.011)))
    max_tan = min(4.0, max(1.2, dist * random.uniform(0.003, 0.008)))
    return _apply_jitter(raw, max_norm, max_tan) if random.random() < 0.75 else raw


def _clamp_point(x, y, min_x=None, max_x=None, min_y=None, max_y=None):
    """将坐标限制在给定边界内。"""
    if min_x is not None:
        x = max(min_x, x)
    if max_x is not None:
        x = min(max_x, x)
    if min_y is not None:
        y = max(min_y, y)
    if max_y is not None:
        y = min(max_y, y)
    return x, y


def build_human_click_actions(
    tx, ty, sx=None, sy=None,
    min_x=None, max_x=None, min_y=None, max_y=None,
):
    """构建完整的拟人点击 BiDi actions（含轨迹+悬停抖动+点击+点击后移开）。

    Args:
        tx: 目标 X 坐标 (视口像素)。
        ty: 目标 Y 坐标 (视口像素)。
        sx: 起始 X 坐标。为 None 时随机生成。默认 None。
        sy: 起始 Y 坐标。为 None 时随机生成。默认 None。
        min_x: 允许的最小 X。默认 None。
        max_x: 允许的最大 X。默认 None。
        min_y: 允许的最小 Y。默认 None。
        max_y: 允许的最大 Y。默认 None。

    Returns:
        list: BiDi actions 列表，可直接传入 input.performActions。
              格式: [{"type": "pointer", "id": "mouse0", ...}]
    """
    if sx is None:
        sx = random.randint(100, 900)
    if sy is None:
        sy = random.randint(100, 600)

    tx, ty = _clamp_point(tx, ty, min_x, max_x, min_y, max_y)
    sx, sy = _clamp_point(sx, sy, min_x, max_x, min_y, max_y)

    path = build_human_mouse_path((sx, sy), (tx, ty))
    acts = [{'type': 'pointerMove', 'x': int(sx), 'y': int(sy), 'duration': 0}]

    prev_x, prev_y = sx, sy
    for px, py in path:
        bx, by = _clamp_point(int(px), int(py), min_x, max_x, min_y, max_y)
        dist = math.hypot(bx - prev_x, by - prev_y)
        acts.append({'type': 'pointerMove', 'x': bx, 'y': by,
                     'duration': max(8, int(dist * random.uniform(1.5, 3.0)))})
        prev_x, prev_y = bx, by

    # 悬停抖动
    for _ in range(random.randint(2, 4)):
        hover_x, hover_y = _clamp_point(
            tx + random.randint(-2, 2), ty + random.randint(-1, 1),
            min_x, max_x, min_y, max_y,
        )
        acts.append({'type': 'pointerMove',
                     'x': hover_x, 'y': hover_y,
                     'duration': random.randint(20, 50)})
    acts.append({'type': 'pointerMove', 'x': tx, 'y': ty, 'duration': random.randint(15, 30)})
    acts.append({'type': 'pause', 'duration': random.randint(80, 300)})
    acts.append({'type': 'pointerDown', 'button': 0})
    acts.append({'type': 'pause', 'duration': random.randint(80, 180)})
    acts.append({'type': 'pointerUp', 'button': 0})
    # 点击后自然移开
    drift_x, drift_y = _clamp_point(
        tx + random.randint(5, 20), ty + random.randint(-5, 5),
        min_x, max_x, min_y, max_y,
    )
    acts.append({'type': 'pointerMove',
                 'x': drift_x, 'y': drift_y,
                 'duration': random.randint(80, 150)})

    return [{'type': 'pointer', 'id': 'mouse0',
             'parameters': {'pointerType': 'mouse'}, 'actions': acts}]


def perform_actions(driver, context, actions):
    """执行输入动作序列 (input.performActions)。

    向指定浏览上下文派发一组输入动作。每个动作源（pointer/key/wheel）
    的动作序列并行执行，同一源内的动作按序执行。

    Args:
        driver:  BrowserBiDiDriver 实例。
        context: browsingContext ID (字符串)。
        actions: 动作源列表。每个元素是一个字典，包含:
                 - "type": "pointer" / "key" / "wheel" / "none"
                 - "id": 输入源标识符 (字符串)
                 - "parameters": (仅 pointer) {"pointerType": "mouse"/"touch"/"pen"}
                 - "actions": 动作序列列表

    Returns:
        dict: BiDi 响应（通常为空结果）。
    """
    return driver.run('input.performActions', {
        'context': context,
        'actions': actions
    })


def release_actions(driver, context):
    """释放所有按键和鼠标按钮 (input.releaseActions)。

    重置指定浏览上下文的所有输入状态，释放所有按住的
    键盘键和鼠标按钮。

    Args:
        driver:  BrowserBiDiDriver 实例。
        context: browsingContext ID (字符串)。

    Returns:
        dict: BiDi 响应（通常为空结果）。
    """
    return driver.run('input.releaseActions', {'context': context})


def set_files(driver, context, element, files):
    """设置文件输入元素的文件列表 (input.setFiles)。

    替换文件输入 (<input type="file">) 元素上已选择的文件。

    Args:
        driver:  BrowserBiDiDriver 实例。
        context: browsingContext ID (字符串)。
        element: 文件输入元素的 SharedReference (dict)，
                 格式: {"sharedId": "xxx"}。
        files:   文件路径列表 (list[str])。
                 每个路径必须是本地文件系统上的绝对路径。

    Returns:
        dict: BiDi 响应（通常为空结果）。

    Raises:
        BiDiError: 如果元素不是 file input，或文件不存在。
    """
    return driver.run('input.setFiles', {
        'context': context,
        'element': element,
        'files': files
    })


def build_pen_action(x, y, pressure=0.5, tilt_x=0, tilt_y=0,
                     twist=0, tangential_pressure=0.0, button=0, duration=50,
                     altitude_angle=None, azimuth_angle=None,
                     width=None, height=None):
    """构建 pen (触控笔) pointer 动作序列。

    按照 W3C BiDi 规范，pen 类型的 pointer action 支持额外的
    压力和倾斜属性，用于模拟手写笔、数位板等输入设备。

    Args:
        x:                     目标 X 坐标 (视口像素)。
        y:                     目标 Y 坐标 (视口像素)。
        pressure:              笔尖压力，范围 [0.0, 1.0]。默认 0.5。
        tilt_x:                X 轴倾斜角度，范围 [-90, 90]。默认 0。
        tilt_y:                Y 轴倾斜角度，范围 [-90, 90]。默认 0。
        twist:                 旋转角度，范围 [0, 359]。默认 0。
        tangential_pressure:   切向压力，范围 [-1.0, 1.0]。默认 0.0。
        button:                鼠标按钮编号。默认 0。
        duration:              移动时长 (毫秒)。默认 50。
        altitude_angle:        海拔角 (弧度)，范围 [0, π/2]。
                               为 None 时不发送。默认 None。
        azimuth_angle:         方位角 (弧度)，范围 [0, 2π]。
                               为 None 时不发送。默认 None。
        width:                 接触面宽度 (像素)。为 None 时不发送。默认 None。
        height:                接触面高度 (像素)。为 None 时不发送。默认 None。

    Returns:
        list: BiDi actions 列表，可直接传入 perform_actions。
    """
    move_action = {
        'type': 'pointerMove', 'x': x, 'y': y, 'duration': duration,
        'pressure': pressure, 'tiltX': tilt_x, 'tiltY': tilt_y,
        'twist': twist, 'tangentialPressure': tangential_pressure
    }
    down_action = {
        'type': 'pointerDown', 'button': button,
        'pressure': pressure, 'tiltX': tilt_x, 'tiltY': tilt_y
    }

    # W3C BiDi 规范扩展属性
    if altitude_angle is not None:
        move_action['altitudeAngle'] = altitude_angle
        down_action['altitudeAngle'] = altitude_angle
    if azimuth_angle is not None:
        move_action['azimuthAngle'] = azimuth_angle
        down_action['azimuthAngle'] = azimuth_angle
    if width is not None:
        move_action['width'] = width
    if height is not None:
        move_action['height'] = height

    return [{
        'type': 'pointer',
        'id': 'pen0',
        'parameters': {'pointerType': 'pen'},
        'actions': [
            move_action,
            down_action,
            {'type': 'pointerUp', 'button': button},
        ]
    }]


def build_key_action(keys):
    """构建键盘动作序列。

    支持两种输入格式:
      1. 字符串: 逐字符输入，如 'Hello'
      2. 列表:   混合输入，如 [('ctrl', 'a'), 'delete']
         - 字符串元素: 按下并释放该键
         - 元组 (modifier, key): 组合键操作

    Args:
        keys: 输入内容，支持以下格式:
              - str: 逐字符输入，如 'Hello World'
              - list: 动作列表，元素可以是:
                - str: 单个键，如 'Enter', 'a'
                - tuple: 组合键，如 ('ctrl', 'a'), ('shift', 'Enter')
                  支持的修饰键名: 'ctrl'/'shift'/'alt'/'meta'
                  支持的特殊键名: 'Enter'/'Tab'/'Backspace'/'Delete'/'Escape'/
                                 'ArrowUp'/'ArrowDown'/'ArrowLeft'/'ArrowRight'

    Returns:
        list: BiDi actions 列表，可直接传入 perform_actions。
    """
    acts = []
    if isinstance(keys, str):
        for ch in keys:
            acts += [{'type': 'keyDown', 'value': ch},
                     {'type': 'keyUp', 'value': ch}]
    else:
        for item in keys:
            if isinstance(item, tuple):
                mod, key = item
                acts += [{'type': 'keyDown', 'value': mod},
                         {'type': 'keyDown', 'value': key},
                         {'type': 'keyUp', 'value': key},
                         {'type': 'keyUp', 'value': mod}]
            else:
                acts += [{'type': 'keyDown', 'value': item},
                         {'type': 'keyUp', 'value': item}]
    return [{'type': 'key', 'id': 'kbd0', 'actions': acts}]


def build_wheel_action(x, y, delta_x=0, delta_y=120, delta_z=0,
                       delta_mode=0, duration=0, origin="viewport"):
    """构建 wheel (滚轮) 滚动动作。

    按照 W3C BiDi 规范，wheel scroll 动作支持三轴滚动
    以及 deltaMode 控制滚动单位。

    Args:
        x:          滚动位置的 X 坐标 (视口像素)。
        y:          滚动位置的 Y 坐标 (视口像素)。
        delta_x:    水平滚动量。正值向右，负值向左。默认 0。
        delta_y:    垂直滚动量。正值向下，负值向上。默认 120。
        delta_z:    Z 轴滚动量 (用于 3D 滚动设备)。默认 0。
        delta_mode: 滚动单位模式。默认 0。
                    - 0: 像素 (pixel)
                    - 1: 行 (line)
                    - 2: 页 (page)
        duration:   滚动动画时长 (毫秒)。默认 0。
        origin:     坐标参考原点。"viewport"(默认) / "pointer" / 元素引用。

    Returns:
        list: BiDi actions 列表，可直接传入 perform_actions。
    """
    action = {
        'type': 'scroll',
        'x': x, 'y': y,
        'deltaX': delta_x, 'deltaY': delta_y,
    }
    if delta_z != 0:
        action['deltaZ'] = delta_z
    if delta_mode != 0:
        action['deltaMode'] = delta_mode
    if duration != 0:
        action['duration'] = duration
    if origin != "viewport":
        action['origin'] = origin

    return [{
        'type': 'wheel',
        'id': 'wheel0',
        'actions': [action]
    }]
