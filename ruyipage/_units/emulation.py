# -*- coding: utf-8 -*-
"""EmulationManager - 设备模拟管理器"""

from dataclasses import dataclass
from typing import Optional

from .._bidi import emulation as bidi_emulation
from .._bidi import browsing_context as bidi_context
from ..errors import RuyiPageError
import logging

logger = logging.getLogger("ruyipage")
UINT32_MAX = (2**32) - 1
_UNSET = object()


class TouchUnsupportedError(RuyiPageError):
    """当前 Firefox 不支持运行时触摸覆盖且没有可用 fallback。"""


class TouchStartupOnlyError(RuyiPageError):
    """触摸 fallback 仅在启动时生效，当前运行时请求无法完成。"""


def _validate_max_touch_points(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            "max_touch_points must be an integer in range 0..{}".format(UINT32_MAX)
        )
    if value < 0 or value > UINT32_MAX:
        raise ValueError(
            "max_touch_points must be in range 0..{}".format(UINT32_MAX)
        )
    return value


@dataclass(frozen=True)
class TouchCapability(object):
    """当前页面可用的触摸模拟能力。

    Attributes:
        native_supported: 是否确认支持原生 BiDi 触摸覆盖；未探测时为 ``None``。
        fallback_configured: 是否配置了启动期 fpfile fallback。
        fallback_installable: 当前启动模式是否允许安装 fpfile fallback。
        user_context: 当前 browsing context 所属的 userContext ID。
    """

    native_supported: Optional[bool] = None
    fallback_configured: bool = False
    fallback_installable: bool = False
    user_context: Optional[str] = None


@dataclass(frozen=True)
class TouchOverrideResult(object):
    """一次触摸覆盖请求的结构化结果。

    ``source`` 为 ``native``、``fpfile`` 或 ``none``；``runtime_mutable``
    表示该结果是否还能在当前浏览器会话中动态修改或重置。
    """

    enabled: bool
    max_touch_points: Optional[int]
    supported: bool
    applied: bool
    native_supported: bool
    fallback_used: bool
    source: str
    runtime_mutable: bool
    reason: Optional[str]
    capability: TouchCapability


class EmulationManager(object):
    """设备模拟管理器

    用法::

        page.emulation.set_geolocation(39.9, 116.4)
        page.emulation.set_timezone('Asia/Shanghai')
        page.emulation.set_locale(['zh-CN', 'zh'])
        page.emulation.set_screen_orientation('landscape-primary')
    """

    def __init__(self, owner):
        self._owner = owner

    def _ctx(self):
        return [self._owner._context_id]

    def _user_context(self):
        try:
            result = bidi_context.get_tree(
                self._owner._driver._browser_driver,
                max_depth=0,
                root=self._owner._context_id,
            )
            contexts = result.get("contexts", [])
            if contexts:
                return contexts[0].get("userContext")
        except Exception as e:
            logger.debug("get current user context failed: %s", e)
        return None

    def _supported(self, result):
        """判断底层命令是否被当前浏览器实现支持。

        Args:
            result: 底层 BiDi 命令返回值。
                常见值：命令成功时为空字典 ``{}``，不支持时为 ``None``。

        Returns:
            bool: ``True`` 表示当前浏览器实现了该命令，``False`` 表示命令未实现。

        适用场景：
            - 示例中区分“成功”和“不支持”
            - 高层 API 向调用方返回统一的布尔支持结果
        """
        return result is not None

    def _options(self):
        browser = getattr(self._owner, "browser", None)
        return getattr(browser, "options", None)

    def _touch_scope_kwargs(self, scope):
        if scope == "global":
            return {}, None
        if scope == "context":
            return {"contexts": self._ctx()}, None
        if scope == "user_context":
            user_context = self._user_context()
            if user_context:
                return {"user_contexts": [user_context]}, user_context
            return None, None
        raise ValueError("scope must be 'context', 'user_context', or 'global'")

    def get_touch_capability(self, native_supported=None, user_context=_UNSET):
        """返回当前页面的触摸模拟能力快照。

        Args:
            native_supported: 已知的原生 BiDi 支持状态；``None`` 表示尚未探测。
            user_context: 显式 userContext ID。省略时自动读取当前页面。

        Returns:
            TouchCapability: 原生支持、fallback 和 userContext 信息。
        """
        options = self._options()
        fallback_configured = bool(
            options and getattr(options, "touch_fallback_enabled", False)
        )
        fallback_installable = bool(
            options
            and hasattr(options, "can_install_touch_fallback")
            and options.can_install_touch_fallback()
        )
        return TouchCapability(
            native_supported=native_supported,
            fallback_configured=fallback_configured,
            fallback_installable=fallback_installable,
            user_context=self._user_context() if user_context is _UNSET else user_context,
        )

    def _touch_startup_fallback_status(self, enabled, max_touch_points):
        options = self._options()
        if not options or not getattr(options, "touch_fallback_active", False):
            return "none", False, None

        configured_points = getattr(options, "touch_fallback_max_touch_points", None)
        if not enabled:
            return (
                "fpfile",
                False,
                "startup touch fallback is active and cannot be disabled at runtime",
            )
        if configured_points != max_touch_points:
            return (
                "fpfile",
                False,
                "startup touch fallback max_touch_points={} does not match request {}".format(
                    configured_points,
                    max_touch_points,
                ),
            )
        return "fpfile", True, None

    def _resolve_capability_user_context(self, scope, scoped_user_context):
        if scope == "user_context":
            return scoped_user_context
        return self._user_context()

    def set_touch_enabled_result(
        self, enabled=True, max_touch_points=1, scope="context", strict=False
    ):
        """设置或清除 ``navigator.maxTouchPoints`` 覆盖并返回详细结果。

        Args:
            enabled: ``True`` 设置覆盖，``False`` 清除覆盖。
            max_touch_points: 最大触点数，允许 ``0..4294967295``。
            scope: ``context``、``user_context`` 或 ``global``。
            strict: 为 ``True`` 时，不支持或只能启动期生效会直接抛出异常。

        Returns:
            TouchOverrideResult: 是否应用、实现来源、运行时可变性及失败原因。

        Raises:
            TypeError: ``max_touch_points`` 不是整数。
            ValueError: 触点数越界或 ``scope`` 无效。
            TouchUnsupportedError: strict 模式下原生命令和 fallback 均不可用。
            TouchStartupOnlyError: strict 模式下请求与启动期 fallback 不匹配。
        """
        requested_max_touch_points = _validate_max_touch_points(max_touch_points)
        scope_kwargs, user_context = self._touch_scope_kwargs(scope)
        if scope == "user_context" and not user_context:
            reason = "current browsing context has no userContext"
            if strict:
                raise TouchUnsupportedError(reason)
            capability = self.get_touch_capability(
                native_supported=None,
                user_context=None,
            )
            return TouchOverrideResult(
                enabled=enabled,
                max_touch_points=requested_max_touch_points,
                supported=False,
                applied=False,
                native_supported=False,
                fallback_used=False,
                source="none",
                runtime_mutable=False,
                reason=reason,
                capability=capability,
            )
        result = bidi_emulation.set_touch_override(
            self._owner._driver._browser_driver,
            max_touch_points=requested_max_touch_points if enabled else None,
            **scope_kwargs,
        )
        if result is not None:
            capability_user_context = self._resolve_capability_user_context(
                scope,
                user_context,
            )
            capability = self.get_touch_capability(
                native_supported=True,
                user_context=capability_user_context,
            )
            return TouchOverrideResult(
                enabled=enabled,
                max_touch_points=requested_max_touch_points,
                supported=True,
                applied=True,
                native_supported=True,
                fallback_used=False,
                source="native",
                runtime_mutable=True,
                reason=None,
                capability=capability,
            )

        capability = self.get_touch_capability(
            native_supported=False,
            user_context=self._resolve_capability_user_context(scope, user_context),
        )
        source, applied, reason = self._touch_startup_fallback_status(
            enabled,
            requested_max_touch_points,
        )
        supported = source == "fpfile"
        fallback_used = applied and source == "fpfile"
        if strict:
            if source == "none":
                raise TouchUnsupportedError(
                    "emulation.setTouchOverride is unsupported and no startup fallback is active"
                )
            if not applied:
                raise TouchStartupOnlyError(reason)
        return TouchOverrideResult(
            enabled=enabled,
            max_touch_points=requested_max_touch_points,
            supported=supported,
            applied=applied,
            native_supported=False,
            fallback_used=fallback_used,
            source=source,
            runtime_mutable=False,
            reason=reason,
            capability=capability,
        )

    def set_geolocation(self, latitude, longitude, accuracy=100):
        """设置地理位置 (FF139+)。

        Args:
            latitude: 纬度
            longitude: 经度
            accuracy: 精度（米），常见值 50~100

        Returns:
            owner
        """
        bidi_emulation.set_geolocation_override(
            self._owner._driver._browser_driver,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            contexts=self._ctx(),
        )
        return self._owner

    def clear_geolocation(self):
        """清除地理位置覆盖"""
        bidi_emulation.set_geolocation_override(
            self._owner._driver._browser_driver, contexts=self._ctx()
        )
        return self._owner

    def set_timezone(self, timezone_id):
        """设置时区 (FF144+)。

        Args:
            timezone_id: 时区标识，如 'Asia/Shanghai' / 'America/New_York'
        """
        bidi_emulation.set_timezone_override(
            self._owner._driver._browser_driver,
            timezone_id=timezone_id,
            contexts=self._ctx(),
        )
        return self._owner

    def set_locale(self, locales):
        """设置语言 (FF142+)。

        Args:
            locales: 语言字符串或列表，如 'ja-JP' 或 ['zh-CN', 'zh']
        """
        bidi_emulation.set_locale_override(
            self._owner._driver._browser_driver, locales=locales, contexts=self._ctx()
        )
        return self._owner

    def set_screen_orientation(self, orientation_type, angle=0):
        """设置屏幕方向 (FF144+)

        Args:
            orientation_type: 'portrait-primary'/'landscape-primary' 等
            angle: 0/90/180/270
        """
        bidi_emulation.set_screen_orientation_override(
            self._owner._driver._browser_driver,
            orientation_type=orientation_type,
            angle=angle,
            contexts=self._ctx(),
        )
        return self._owner

    def set_screen_size(self, width, height, device_pixel_ratio=None):
        """设置屏幕尺寸 (FF147+)。

        Args:
            width: 屏幕宽度（CSS 像素）
            height: 屏幕高度（CSS 像素）
            device_pixel_ratio: 设备像素比，例如 2.0 / 3.0
        """
        user_context = self._user_context()
        scope = (
            {"user_contexts": [user_context]}
            if user_context
            else {"contexts": self._ctx()}
        )
        result = bidi_emulation.set_screen_settings_override(
            self._owner._driver._browser_driver,
            width=width,
            height=height,
            device_pixel_ratio=device_pixel_ratio,
            **scope,
        )
        if result is None:
            bidi_emulation.inject_screen_settings_override(
                self._owner._driver._browser_driver,
                self._owner._context_id,
                width,
                height,
                device_pixel_ratio=device_pixel_ratio,
            )
        return self._owner

    def set_user_agent(self, user_agent, platform=None):
        """设置 UA (FF145+)，旧版自动回退到 preload script。

        Args:
            user_agent: UA 字符串
            platform: 可选平台名，例如 'iPhone'
        """
        result = bidi_emulation.set_user_agent_override(
            self._owner._driver._browser_driver,
            user_agent=user_agent,
            platform=platform,
            contexts=self._ctx(),
        )
        if result is None:
            # 回退到 preload script 方式
            self._owner.set_useragent(user_agent)
        return self._owner

    def set_network_offline(self, enabled=True):
        """模拟离线/在线网络状态。

        Args:
            enabled: True=离线, False=在线

        Returns:
            bool: 当前浏览器是否支持该命令
        """
        result = bidi_emulation.set_network_conditions(
            self._owner._driver._browser_driver,
            offline=enabled,
            contexts=self._ctx(),
        )
        return self._supported(result)

    def set_touch_enabled(self, enabled=True, max_touch_points=1, scope="context"):
        """设置触摸覆盖并返回是否已应用。

        Args:
            enabled: ``True`` 设置覆盖，``False`` 清除覆盖。
            max_touch_points: 最大触点数，允许 ``0..4294967295``。
            scope: ``context``、``user_context`` 或 ``global``。

        Returns:
            bool: 原生覆盖或匹配的启动期 fallback 已生效时为 ``True``。

        Notes:
            需要失败原因和能力信息时使用 :meth:`set_touch_enabled_result`。
        """
        return self.set_touch_enabled_result(
            enabled=enabled,
            max_touch_points=max_touch_points,
            scope=scope,
        ).applied

    def set_javascript_enabled(self, enabled=True):
        """启用/禁用 JavaScript。

        Args:
            enabled: 是否启用 JavaScript。
                常见值：``True`` 启用、``False`` 禁用。

        Returns:
            bool: ``True`` 表示当前浏览器支持该标准命令，``False`` 表示未实现。

        适用场景：
            - 判断当前 Firefox 是否支持 ``emulation.setScriptingEnabled``
            - 在示例里明确标记“成功”或“不支持”
        """
        result = bidi_emulation.set_scripting_enabled(
            self._owner._driver._browser_driver,
            enabled=enabled,
            contexts=self._ctx(),
        )
        return self._supported(result)

    def set_scrollbar_type(self, scrollbar_type="overlay"):
        """设置滚动条类型。

        Args:
            scrollbar_type: 目标滚动条类型。
                常见值：``'none'``、``'standard'``、``'overlay'``。

        Returns:
            bool: ``True`` 表示当前浏览器支持该标准命令，``False`` 表示未实现。

        适用场景：
            - 测试不同滚动条呈现方式
            - 判断当前 Firefox 是否支持 ``emulation.setScrollbarTypeOverride``
        """
        result = bidi_emulation.set_scrollbar_type_override(
            self._owner._driver._browser_driver,
            scrollbar_type=scrollbar_type,
            contexts=self._ctx(),
        )
        return self._supported(result)

    def set_forced_colors_mode(self, mode="dark"):
        """设置强制颜色模式。

        Args:
            mode: 目标模式。
                常见值：``'none'``、``'active'``、``'light'``、``'dark'``。

        Returns:
            bool: ``True`` 表示当前浏览器支持该标准命令，``False`` 表示未实现。

        适用场景：
            - 测试强制颜色主题覆盖
            - 判断当前 Firefox 是否支持 ``emulation.setForcedColorsModeThemeOverride``
        """
        result = bidi_emulation.set_forced_colors_mode_theme_override(
            self._owner._driver._browser_driver,
            mode=mode,
            contexts=self._ctx(),
        )
        return self._supported(result)

    def set_bypass_csp(self, enabled=True):
        """设置是否绕过 CSP。返回当前浏览器是否支持。"""
        result = bidi_context.set_bypass_csp(
            self._owner._driver._browser_driver,
            context=self._owner._context_id,
            enabled=enabled,
        )
        return self._supported(result)

    def apply_mobile_preset(
        self,
        user_agent,
        *,
        width=390,
        height=844,
        device_pixel_ratio=3.0,
        orientation_type="portrait-primary",
        angle=0,
        locale=None,
        timezone_id=None,
        touch=True,
    ):
        """一键应用常见移动端模拟参数（新手友好）。

        Returns:
            dict: 每项能力是否支持
        """
        support = {
            "user_agent": True,
            "screen": True,
            "orientation": True,
            "touch": self.set_touch_enabled(touch) if touch is not None else None,
            "locale": None,
            "timezone": None,
        }

        try:
            self.set_user_agent(user_agent)
        except Exception:
            support["user_agent"] = False

        try:
            self.set_screen_size(width, height, device_pixel_ratio=device_pixel_ratio)
        except Exception:
            support["screen"] = False

        try:
            # 移动端访问不仅要改 screen，还要改当前浏览上下文的 viewport。
            self._owner.set_viewport(
                width, height, device_pixel_ratio=device_pixel_ratio
            )
        except Exception:
            support["screen"] = False

        try:
            self.set_screen_orientation(orientation_type, angle=angle)
        except Exception:
            support["orientation"] = False

        if locale:
            try:
                self.set_locale(locale)
                support["locale"] = True
            except Exception:
                support["locale"] = False

        if timezone_id:
            try:
                self.set_timezone(timezone_id)
                support["timezone"] = True
            except Exception:
                support["timezone"] = False

        return support
