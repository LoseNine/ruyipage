# -*- coding: utf-8 -*-
"""BiDi browsingContext 模块命令"""


def navigate(driver, context, url, wait="complete", timeout=None):
    """导航到指定 URL

    Args:
        context: browsingContext ID
        url: 目标 URL
        wait: 等待策略 - 'none'/'interactive'/'complete'
        timeout: 超时时间（秒），None 使用默认值

    Returns:
        {'navigation': str|None, 'url': str}
    """
    return driver.run(
        "browsingContext.navigate", {"context": context, "url": url, "wait": wait},
        timeout=timeout,
    )


def get_tree(driver, max_depth=None, root=None):
    """获取浏览上下文树。

    Args:
        max_depth: 返回树的最大深度。
            单位：层级深度整数。
            常见值：``0`` 只看顶层、``1`` 看顶层及一层子节点。
        root: 可选的根 browsingContext ID。
            常见值：某个 tab 或 window 的 context ID。传 ``None`` 表示从顶层开始。

    Returns:
        dict: ``{'contexts': [BrowsingContextInfo, ...]}``。

    适用场景：
        - 查看当前浏览器有哪些 context
        - 检查新建 tab/window 后是否进入上下文树
        - 调试多标签页或嵌套 frame 结构
    """
    params = {}
    if max_depth is not None:
        params["maxDepth"] = max_depth
    if root:
        params["root"] = root
    return driver.run("browsingContext.getTree", params)


def create(
    driver, type_="tab", reference_context=None, background=False, user_context=None
):
    """创建新的浏览上下文。

    Args:
        type_: 要创建的上下文类型。
            常见值：``'tab'``、``'window'``。
        reference_context: 参考 browsingContext ID。
            常见值：当前页面的 ``page.tab_id``。某些浏览器可据此决定新 tab 的关联窗口。
        background: 是否后台创建。
            常见值：``False`` 前台、``True`` 后台。
        user_context: 可选的 user context ID。
            常见值：Firefox 容器标签页 ID。用于在指定 user context 中创建 tab。

    Returns:
        dict: ``{'context': str}``，其中 ``context`` 是新创建的 browsingContext ID。

    适用场景：
        - 新建 tab 或 window
        - 在指定 user context 中创建隔离 tab
        - 测试多窗口/多标签页管理能力
    """
    params = {"type": type_}
    if reference_context:
        params["referenceContext"] = reference_context
    if background:
        params["background"] = True
    if user_context:
        params["userContext"] = user_context
    return driver.run("browsingContext.create", params)


def close(driver, context, prompt_unload=False):
    """关闭浏览上下文。

    Args:
        context: 要关闭的 browsingContext ID。
            常见值：``browsingContext.create`` 返回的 ``context``。
        prompt_unload: 是否允许触发 ``beforeunload`` 提示流程。
            常见值：``False`` 直接关闭、``True`` 按浏览器卸载流程处理。

    Returns:
        dict: BiDi 命令返回结果，通常为空字典。

    适用场景：
        - 关闭测试中临时创建的 tab/window
        - 验证 browsingContext 生命周期管理
    """
    params = {"context": context}
    if prompt_unload:
        params["promptUnload"] = True
    return driver.run("browsingContext.close", params)


def activate(driver, context):
    """激活（聚焦）浏览上下文"""
    return driver.run("browsingContext.activate", {"context": context})


def capture_screenshot(driver, context, origin="viewport", format_=None, clip=None):
    """截图

    Args:
        context: browsingContext ID
        origin: 'viewport' 或 'document'
        format_: None 或 {'type': 'image/png'|'image/jpeg', 'quality': 0-1}
        clip: None 或裁剪区域
              - 视口裁剪: {'type': 'viewport', 'x': num, 'y': num, 'width': num, 'height': num}
              - 元素裁剪: {'type': 'element', 'element': SharedReference}

    Returns:
        {'data': str}  base64 编码的图片数据
    """
    params = {"context": context, "origin": origin}
    if format_:
        params["format"] = format_
    if clip:
        params["clip"] = clip
    return driver.run("browsingContext.captureScreenshot", params)


def print_(
    driver,
    context,
    background=None,
    margin=None,
    orientation=None,
    page=None,
    page_ranges=None,
    scale=None,
    shrink_to_fit=None,
):
    """打印为 PDF。

    Args:
        driver: BiDi 驱动实例。
        context: browsingContext ID，表示要打印的页面上下文。
        background: bool，是否打印背景色和背景图片。
            - True: 打印背景
            - False: 不打印背景
        margin: dict，页边距配置，单位为厘米（cm）。
            - 结构：{'top': num, 'bottom': num, 'left': num, 'right': num}
            - 每个值建议为非负数，例如 1.0 / 1.2
        orientation: str，页面方向。
            - 'portrait': 纵向（默认方向）
            - 'landscape': 横向
        page: dict，页面纸张尺寸，单位为厘米（cm）。
            - 结构：{'width': num, 'height': num}
            - 例如 A4 约为 {'width': 21.0, 'height': 29.7}
        page_ranges: list[str]，要打印的页码范围。
            - 例如 ['1']、['1-2']、['1', '3-4']
            - 传 None 表示打印全部页面
        scale: float，打印缩放比例。
            - 常见值：0.8 ~ 1.0
            - 1.0 表示原始比例
        shrink_to_fit: bool，内容过宽时是否自动缩放到页面宽度内。
            - True: 自动缩放以适应页面
            - False: 不自动缩放

    Returns:
        {'data': str}：base64 编码的 PDF 数据
    """
    params = {"context": context}
    if background is not None:
        params["background"] = background
    if margin:
        params["margin"] = margin
    if orientation:
        params["orientation"] = orientation
    if page:
        params["page"] = page
    if page_ranges:
        params["pageRanges"] = page_ranges
    if scale is not None:
        params["scale"] = scale
    if shrink_to_fit is not None:
        params["shrinkToFit"] = shrink_to_fit
    return driver.run("browsingContext.print", params)


def reload(driver, context, ignore_cache=False, wait="complete"):
    """重新加载页面。

    Args:
        context: 目标 browsingContext ID。
            常见值：当前页面的 ``page.tab_id``。
        ignore_cache: 是否忽略缓存。
            常见值：``False`` 普通重载、``True`` 类似强制刷新。
        wait: 重载后的等待策略。
            常见值：``'none'``、``'interactive'``、``'complete'``。

    Returns:
        dict: 通常包含 ``navigation`` 和 ``url`` 字段。

    适用场景：
        - 验证 reload 命令本身
        - 测试 ignore_cache 在当前浏览器版本是否实现
    """
    params = {"context": context, "wait": wait}
    if ignore_cache:
        params["ignoreCache"] = True
    return driver.run("browsingContext.reload", params)


def traverse_history(driver, context, delta):
    """历史导航

    Args:
        context: browsingContext ID
        delta: 导航步数，正数前进，负数后退
    """
    return driver.run(
        "browsingContext.traverseHistory", {"context": context, "delta": delta}
    )


def handle_user_prompt(driver, context, accept=True, user_text=None):
    """处理用户弹窗（alert/confirm/prompt）

    Args:
        context: browsingContext ID
        accept: True 接受，False 拒绝
        user_text: 对于 prompt 弹窗填入的文本
    """
    params = {"context": context, "accept": accept}
    if user_text is not None:
        params["userText"] = user_text
    return driver.run("browsingContext.handleUserPrompt", params)


def locate_nodes(
    driver,
    context,
    locator,
    max_node_count=None,
    serialization_options=None,
    start_nodes=None,
):
    """查找 DOM 节点

    Args:
        context: browsingContext ID
        locator: 定位器字典
            - {'type': 'css', 'value': 'selector'}
            - {'type': 'xpath', 'value': 'expression'}
            - {'type': 'innerText', 'value': 'text', 'maxDepth': int}
            - {'type': 'accessibility', 'value': {'name': str, 'role': str}}
        max_node_count: 最大返回数量
        serialization_options: 序列化选项
        start_nodes: 起始节点列表（用于相对查找）

    Returns:
        {'nodes': [RemoteValue...]}
    """
    params = {"context": context, "locator": locator}
    if max_node_count is not None:
        params["maxNodeCount"] = max_node_count
    if serialization_options:
        params["serializationOptions"] = serialization_options
    if start_nodes:
        params["startNodes"] = start_nodes
    return driver.run("browsingContext.locateNodes", params)


def set_viewport(driver, context, width=None, height=None, device_pixel_ratio=None):
    """设置视口大小。

    Args:
        context: 目标 browsingContext ID。
            常见值：当前页面的 ``page.tab_id``。
        width: 视口宽度。
            单位：CSS 像素。
            常见值：``800``、``1280``、``375``。
        height: 视口高度。
            单位：CSS 像素。
            常见值：``600``、``720``、``667``。
        device_pixel_ratio: 设备像素比。
            常见值：``1``、``2``、``3``。

    Returns:
        dict: BiDi 命令返回结果，通常为空字典。

    适用场景：
        - 调整桌面视口大小
        - 配合移动端模拟设置 viewport + DPR
    """
    params = {"context": context}
    if width is not None and height is not None:
        params["viewport"] = {"width": width, "height": height}
    if device_pixel_ratio is not None:
        params["devicePixelRatio"] = device_pixel_ratio
    return driver.run("browsingContext.setViewport", params)


def set_bypass_csp(driver, context, enabled=True):
    """设置是否绕过内容安全策略（CSP）。

    Args:
        context: 目标 browsingContext ID。
            常见值：当前页面的 ``page.tab_id``。
        enabled: 是否启用绕过。
            常见值：``True`` 启用、``False`` 禁用。

    Returns:
        dict: BiDi 命令返回结果，通常为空字典。

    适用场景：
        - 需要验证浏览器是否支持标准 ``browsingContext.setBypassCSP``
        - 不希望退回 JS 兼容逻辑，而是直接判断标准命令可用性

    说明：
        - 当前 Firefox 若未实现该命令，调用方应拿到异常并标记为“不支持”。
        - 这里不再把 ``unknown command`` 吞成 ``None``，避免示例误判为成功。
    """
    return driver.run(
        "browsingContext.setBypassCSP", {"context": context, "enabled": enabled}
    )
