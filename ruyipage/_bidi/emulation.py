# -*- coding: utf-8 -*-
"""BiDi emulation 模块命令

Firefox 149+ 支持状态：
  ✅ setUserAgentOverride     (FF145+)
  ✅ setGeolocationOverride   (FF139+)
  ✅ setTimezoneOverride      (FF144+)
  ✅ setLocaleOverride        (FF142+)
  ✅ setScreenOrientationOverride (FF144+)
  ✅ setScreenSettingsOverride (FF147+)
  ❌ setNetworkConditions     (未实现)
  ❌ setTouchOverride         (未实现)
  ❌ setScriptingEnabled      (未实现)
  ❌ setScrollbarTypeOverride (未实现)
  ❌ setForcedColorsModeThemeOverride (未实现)

标记为「未实现」的命令使用 _safe_run 封装，不支持时仅打印警告不会崩溃。
inject_ua_override() 作为 preload script 回退方案保留，
适用于 < FF145 的旧版本 Firefox。
"""

import logging

logger = logging.getLogger("ruyipage")


def _safe_run(driver, method, params, description="emulation command"):
    """执行 BiDi emulation 命令，不支持时优雅降级。

    Args:
        driver: BiDi driver
        method: BiDi 方法名
        params: 参数字典
        description: 日志描述

    Returns:
        命令结果字典，不支持时返回 None
    """
    try:
        return driver.run(method, params)
    except Exception as e:
        err_str = str(e).lower()
        if (
            "unknown command" in err_str
            or "not supported" in err_str
            or "unknown method" in err_str
            or "invalid method" in err_str
        ):
            logger.warning("%s 不受当前 Firefox 版本支持: %s", description, e)
            return None
        raise


# ---------------------------------------------------------------------------
# Firefox 149+ Stable 支持的命令
# ---------------------------------------------------------------------------


def set_user_agent_override(driver, user_agent, platform=None, contexts=None):
    """覆盖 User-Agent (FF145+ stable)

    Args:
        user_agent: UA 字符串
        platform: 平台标识
        contexts: 限定 context 列表

    Returns:
        命令结果，或 None（旧版 Firefox 不支持时）
    """
    params = {"userAgent": user_agent}
    if platform:
        params["platform"] = platform
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setUserAgentOverride",
        params,
        "emulation.setUserAgentOverride",
    )


def set_geolocation_override(
    driver, latitude=None, longitude=None, accuracy=None, contexts=None
):
    """覆盖地理位置 (FF139+ stable)

    Args:
        latitude: 纬度
        longitude: 经度
        accuracy: 精度（米）
        contexts: 限定 context 列表
    """
    params = {}
    if latitude is not None and longitude is not None:
        coords = {"latitude": latitude, "longitude": longitude}
        if accuracy is not None:
            coords["accuracy"] = accuracy
        params["coordinates"] = coords
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setGeolocationOverride",
        params,
        "emulation.setGeolocationOverride",
    )


def set_timezone_override(driver, timezone_id, contexts=None):
    """覆盖时区 (FF144+ stable)

    Args:
        timezone_id: 时区标识，如 'America/New_York'；传 None 则跳过
        contexts: 限定 context 列表
    """
    if not timezone_id:
        return
    params = {"timezone": timezone_id}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver, "emulation.setTimezoneOverride", params, "emulation.setTimezoneOverride"
    )


def set_locale_override(driver, locales, contexts=None):
    """覆盖语言设置 (FF142+ stable)

    也会覆盖 navigator.language(s) (FF146+) 和 Accept-Language 头 (FF147+)。

    Args:
        locales: 语言字符串或列表，如 'ja-JP' 或 ['ja-JP', 'ja']
        contexts: 限定 context 列表
    """
    # 规范参数名为 locale（单数字符串），取第一个
    locale = locales[0] if isinstance(locales, list) else locales
    params = {"locale": locale}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver, "emulation.setLocaleOverride", params, "emulation.setLocaleOverride"
    )


def set_screen_orientation_override(driver, orientation_type, angle=0, contexts=None):
    """覆盖屏幕方向 (FF144+ stable)

    Args:
        orientation_type: 'portrait-primary'/'portrait-secondary'/
                         'landscape-primary'/'landscape-secondary'
        angle: 旋转角度 (0/90/180/270)
        contexts: 限定 context 列表
    """
    # 从type中提取natural方向
    natural = "portrait" if "portrait" in orientation_type else "landscape"

    params = {
        "screenOrientation": {
            "type": orientation_type,
            "angle": angle,
            "natural": natural,
        }
    }
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setScreenOrientationOverride",
        params,
        "emulation.setScreenOrientationOverride",
    )


def set_screen_settings_override(
    driver, width=None, height=None, device_pixel_ratio=None, contexts=None
):
    """覆盖屏幕设置 (FF147+ stable)

    Args:
        width: 屏幕宽度
        height: 屏幕高度
        device_pixel_ratio: 设备像素比
        contexts: 限定 context 列表
    """
    params = {}

    # 构建screenArea对象
    if width is not None or height is not None:
        screen_area = {}
        if width is not None:
            screen_area["width"] = width
        if height is not None:
            screen_area["height"] = height
        params["screenArea"] = screen_area

    if device_pixel_ratio is not None:
        params["devicePixelRatio"] = device_pixel_ratio
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setScreenSettingsOverride",
        params,
        "emulation.setScreenSettingsOverride",
    )


def inject_screen_settings_override(driver, context, width, height, device_pixel_ratio=None):
    """通过 preload script 回退覆盖 screen / DPR。

    用于不支持 ``emulation.setScreenSettingsOverride`` 的旧版 Firefox。
    """
    from . import script as bidi_script

    width_value = "null" if width is None else str(int(width))
    height_value = "null" if height is None else str(int(height))
    dpr_value = (
        "null" if device_pixel_ratio is None else str(float(device_pixel_ratio))
    )
    inject_js = """() => {
  const width = %s;
  const height = %s;
  const dpr = %s;
  function define(target, name, value) {
    if (value === null || value === undefined) return;
    try {
      Object.defineProperty(target, name, {
        get: () => value,
        configurable: true
      });
    } catch (e) {}
  }
  if (window.screen) {
    // Overrides screen.width / screen.height / screen.availWidth / screen.availHeight.
    define(screen, 'width', width);
    define(screen, 'height', height);
    define(screen, 'availWidth', width);
    define(screen, 'availHeight', height);
  }
  define(window, 'devicePixelRatio', dpr);
}""" % (width_value, height_value, dpr_value)

    result = bidi_script.add_preload_script(
        driver, inject_js, contexts=[context], timeout=3
    )
    script_id = result.get("script", "")

    try:
        bidi_script.call_function(driver, context, inject_js, timeout=3)
    except Exception as e:
        logger.debug("当前页面 screen 覆盖执行失败（preload 仍然生效）: %s", e)

    return script_id


# ---------------------------------------------------------------------------
# Firefox 未实现的命令（安全降级）
# ---------------------------------------------------------------------------


def set_network_conditions(driver, offline=False, contexts=None):
    """模拟网络条件 (Firefox 未实现)

    Args:
        offline: 是否离线
        contexts: 限定 context 列表
    """
    # networkConditions.type 必须是字符串 "offline"，而不是布尔值
    params = {"networkConditions": {"type": "offline" if offline else "online"}}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setNetworkConditions",
        params,
        "emulation.setNetworkConditions",
    )


def set_touch_override(driver, max_touch_points=1, contexts=None, user_contexts=None):
    """启用/禁用触摸模拟。

    规范参数为 maxTouchPoints：
        - 传 >=1 的整数表示启用触摸并设置最大触点数
        - 传 None 表示清除覆盖/禁用模拟

    Args:
        max_touch_points: 最大触点数（>=1）或 None
        contexts: 限定 browsingContext 列表
        user_contexts: 限定 browser.UserContext 列表
    """
    if contexts and user_contexts:
        raise ValueError("contexts 和 user_contexts 不能同时传入")
    params = {"maxTouchPoints": max_touch_points}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    if user_contexts:
        params["userContexts"] = (
            user_contexts if isinstance(user_contexts, list) else [user_contexts]
        )
    return _safe_run(
        driver, "emulation.setTouchOverride", params, "emulation.setTouchOverride"
    )


# ---------------------------------------------------------------------------
# Fallback: UA override via preload script（兼容旧版 Firefox < 145）
# ---------------------------------------------------------------------------


def inject_ua_override(driver, context, user_agent):
    """通过 script.addPreloadScript 注入 UA 覆盖

    用于 Firefox < 145 版本。FF145+ 请直接使用 set_user_agent_override()。

    Args:
        driver: BiDi driver (browser-level)
        context: browsingContext ID
        user_agent: 目标 UA 字符串

    Returns:
        str: preload script ID
    """
    from . import script as bidi_script

    escaped_ua = user_agent.replace("\\", "\\\\").replace("'", "\\'")
    inject_js = (
        "() => {"
        "  Object.defineProperty(navigator, 'userAgent', "
        "{get: () => '" + escaped_ua + "'});"
        "}"
    )

    result = bidi_script.add_preload_script(driver, inject_js, contexts=[context])
    script_id = result.get("script", "")

    try:
        bidi_script.call_function(driver, context, inject_js)
    except Exception as e:
        logger.debug("当前页面 UA 覆盖执行失败（preload 仍然生效）: %s", e)

    return script_id


# ---------------------------------------------------------------------------
# 补全命令（可能不支持，使用 _safe_run 优雅降级）
# ---------------------------------------------------------------------------


def set_media_features_override(driver, features, contexts=None):
    """覆盖CSS媒体特性 (Firefox可能不支持)

    Args:
        features: 媒体特性列表
            [{'name': 'prefers-color-scheme', 'value': 'dark'},
             {'name': 'prefers-reduced-motion', 'value': 'reduce'}]
        contexts: 限定context列表

    Returns:
        命令结果，或None（不支持时）
    """
    params = {"features": features}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setMediaFeaturesOverride",
        params,
        "emulation.setMediaFeaturesOverride",
    )


def set_document_cookie_disabled(driver, disabled=True, contexts=None):
    """禁用/启用Cookie (Firefox可能不支持)

    Args:
        disabled: True禁用，False启用
        contexts: 限定context列表
    """
    params = {"disabled": disabled}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setDocumentCookieDisabled",
        params,
        "emulation.setDocumentCookieDisabled",
    )


def set_bypass_csp(driver, enabled=True, contexts=None):
    """绕过内容安全策略 (Firefox可能不支持)

    Args:
        enabled: True启用绕过，False禁用
        contexts: 限定context列表
    """
    params = {"enabled": enabled}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(driver, "emulation.setBypassCSP", params, "emulation.setBypassCSP")


def set_focus_emulation(driver, enabled=True, contexts=None):
    """模拟焦点状态 (Firefox可能不支持)

    Args:
        enabled: True启用焦点模拟
        contexts: 限定context列表
    """
    params = {"enabled": enabled}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver, "emulation.setFocusEmulation", params, "emulation.setFocusEmulation"
    )


def set_hardware_concurrency(driver, concurrency, contexts=None):
    """覆盖navigator.hardwareConcurrency (Firefox可能不支持)

    Args:
        concurrency: CPU核心数
        contexts: 限定context列表
    """
    params = {"hardwareConcurrency": concurrency}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setHardwareConcurrency",
        params,
        "emulation.setHardwareConcurrency",
    )


def set_scripting_enabled(driver, enabled=True, contexts=None):
    """启用/禁用JavaScript执行 (Firefox可能不支持)

    Args:
        enabled: True启用JavaScript，False禁用
        contexts: 限定context列表
    """
    params = {"enabled": enabled}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver, "emulation.setScriptingEnabled", params, "emulation.setScriptingEnabled"
    )


def set_scrollbar_type_override(driver, scrollbar_type="default", contexts=None):
    """覆盖滚动条类型 (Firefox可能不支持)

    Args:
        scrollbar_type: 'default' / 'none' / 'overlay'
        contexts: 限定context列表
    """
    params = {"type": scrollbar_type}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setScrollbarTypeOverride",
        params,
        "emulation.setScrollbarTypeOverride",
    )


def set_forced_colors_mode_theme_override(driver, mode="none", contexts=None):
    """强制颜色模式主题覆盖 (Firefox可能不支持)

    Args:
        mode: 'none' / 'active' / 'light' / 'dark'
        contexts: 限定context列表
    """
    params = {"mode": mode}
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    return _safe_run(
        driver,
        "emulation.setForcedColorsModeThemeOverride",
        params,
        "emulation.setForcedColorsModeThemeOverride",
    )
