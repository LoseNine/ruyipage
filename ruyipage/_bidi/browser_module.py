# -*- coding: utf-8 -*-
"""BiDi browser 模块命令"""

import os


def close(driver):
    """关闭浏览器"""
    return driver.run("browser.close")


def create_user_context(driver):
    """创建用户上下文（类似容器标签页）。

    Returns:
        dict: 包含 ``userContext`` 字段的结果字典。

    适用场景：
        - 创建隔离的浏览器 user context
        - 在测试中模拟不同容器环境
    """
    return driver.run("browser.createUserContext")


def get_user_contexts(driver):
    """获取所有用户上下文。

    Returns:
        dict: 包含 ``userContexts`` 列表的结果字典。

    适用场景：
        - 查看当前浏览器已有的 user context
        - 对比创建前后的数量变化
    """
    return driver.run("browser.getUserContexts")


def remove_user_context(driver, user_context):
    """移除用户上下文。

    Args:
        user_context: 目标 user context ID。
            常见值：``browser.createUserContext`` 返回的 ``userContext``。

    Returns:
        dict: BiDi 命令返回结果，通常为空字典。

    适用场景：
        - 清理测试中临时创建的 user context
        - 验证 user context 生命周期管理
    """
    return driver.run("browser.removeUserContext", {"userContext": user_context})


def get_client_windows(driver):
    """获取所有浏览器窗口信息。

    Returns:
        dict: 包含 ``clientWindows`` 列表的结果字典。

    适用场景：
        - 查询当前窗口状态、尺寸、位置
        - 多窗口环境下枚举全部 client window
    """
    return driver.run("browser.getClientWindows")


def set_client_window_state(
    driver, client_window, state=None, width=None, height=None, x=None, y=None
):
    """设置窗口状态（Firefox 私有扩展，非 W3C 标准）。

    Args:
        client_window: 窗口 ID。
            常见值：``browser.getClientWindows`` 返回的 ``clientWindow``。
        state: 目标窗口状态。
            常见值：``'normal'``、``'minimized'``、``'maximized'``、``'fullscreen'``。
        width: 目标窗口宽度。
            单位：像素。常见值：``1280``、``1440``。
        height: 目标窗口高度。
            单位：像素。常见值：``720``、``900``。
        x: 目标窗口横坐标。
            单位：屏幕像素。常见值：``0``、``100``。
        y: 目标窗口纵坐标。
            单位：屏幕像素。常见值：``0``、``80``。

    Returns:
        dict: BiDi 命令返回结果，通常为空字典。

    适用场景：
        - 验证窗口状态切换
        - 设置窗口大小与位置
    """
    params = {"clientWindow": client_window}
    if state:
        params["state"] = state
    if width is not None:
        params["width"] = width
    if height is not None:
        params["height"] = height
    if x is not None:
        params["x"] = x
    if y is not None:
        params["y"] = y
    return driver.run("browser.setClientWindowState", params)


def set_download_behavior(
    driver, behavior="allow", download_path=None, contexts=None, user_contexts=None
):
    """设置下载行为。

    Args:
        behavior: 下载策略字符串。
            常见值：``'allow'`` 允许下载、``'deny'`` 拒绝下载、
            ``'allowAndOpen'`` 允许并尝试打开。
        download_path: 下载目录路径。
            单位：文件系统路径字符串。
            常见值：绝对路径，例如 ``'E:/ruyipage/examples/downloads'``。
            当 ``behavior='allow'`` 时通常配合使用。
        contexts: 旧参数。当前 ``browser.setDownloadBehavior`` 不支持按
            browsingContext 设置下载目录，请省略或改用 ``user_contexts``。
        user_contexts: 受影响的 user context ID 列表。
            常见值：Firefox 容器标签页 ID 列表。与 ``contexts`` 互斥。

    Returns:
        dict: BiDi 命令返回结果，通常为空字典。

    适用场景：
        - 示例中切换 allow / deny 下载策略
        - 针对默认浏览器行为或特定 user context 施加下载策略
    """
    if contexts is not None:
        raise ValueError(
            "browser.setDownloadBehavior 不支持 contexts，请省略或使用 user_contexts"
        )

    params = {}

    behavior_text = str(behavior or "").strip()
    if behavior_text in ("allow", "allowAndOpen", "allowed"):
        download_behavior = {"type": "allowed"}
        if download_path:
            download_behavior["destinationFolder"] = os.path.normpath(
                os.path.abspath(os.path.expanduser(os.fspath(download_path)))
            )
    elif behavior_text in ("deny", "denied"):
        download_behavior = {"type": "denied"}
    else:
        raise ValueError("behavior 必须是 'allow'、'allowAndOpen' 或 'deny'")

    params["downloadBehavior"] = download_behavior

    if user_contexts:
        params["userContexts"] = (
            user_contexts if isinstance(user_contexts, list) else [user_contexts]
        )
    return driver.run("browser.setDownloadBehavior", params)
