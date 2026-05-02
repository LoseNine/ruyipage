# -*- coding: utf-8 -*-
"""WindowManager - 浏览器窗口管理（JS降级）"""

from .._bidi import browser_module as bidi_browser
import logging

logger = logging.getLogger("ruyipage")


class WindowManager(object):
    """窗口状态管理。

    说明：
        - 优先走 BiDi browser.setClientWindowState。
        - 当目标平台/版本不支持时，会回退到页面级 JS 操作。
    """

    def __init__(self, owner):
        self._owner = owner

    def _bidi_state(self, wid, **kwargs):
        """尝试BiDi命令，失败则返回False"""
        try:
            bidi_browser.set_client_window_state(
                self._owner._driver._browser_driver, wid, **kwargs
            )
            return True
        except Exception:
            return False

    def _get_window_id(self):
        try:
            r = bidi_browser.get_client_windows(self._owner._driver._browser_driver)
            wins = r.get("clientWindows", [])
            return wins[0].get("clientWindow") if wins else None
        except Exception:
            return None

    def maximize(self):
        wid = self._get_window_id()
        if not (wid and self._bidi_state(wid, state="maximized")):
            self._owner.run_js(
                "window.moveTo(0,0);window.resizeTo(screen.width,screen.height)"
            )
        return self._owner

    def minimize(self):
        wid = self._get_window_id()
        if not (wid and self._bidi_state(wid, state="minimized")):
            logger.debug("minimize: BiDi不支持，跳过")
        return self._owner

    def fullscreen(self):
        wid = self._get_window_id()
        if not (wid and self._bidi_state(wid, state="fullscreen")):
            self._owner.run_js(
                "document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen()"
            )
        return self._owner

    def normal(self):
        wid = self._get_window_id()
        if not (wid and self._bidi_state(wid, state="normal")):
            self._owner.run_js("window.resizeTo(1280,800)")
        return self._owner

    def set_size(self, width, height):
        wid = self._get_window_id()
        if not (
            wid and self._bidi_state(wid, state="normal", width=width, height=height)
        ):
            self._owner.run_js("window.resizeTo({},{})".format(width, height))
        return self._owner

    def set_position(self, x, y):
        wid = self._get_window_id()
        if not (wid and self._bidi_state(wid, state="normal", x=x, y=y)):
            self._owner.run_js("window.moveTo({},{})".format(x, y))
        return self._owner

    def center(self, width=None, height=None):
        """将窗口居中（可选同时设置窗口尺寸）。

        Args:
            width: 可选，窗口宽度
            height: 可选，窗口高度

        Returns:
            owner
        """
        if width and height:
            self.set_size(width, height)

        # 读取当前窗口大小后计算目标位置
        try:
            sw, sh = self._owner.run_js(
                "return [window.screen.availWidth, window.screen.availHeight]"
            )
            ww, wh = self._owner.rect.window_size
            x = int(max(0, (sw - ww) / 2))
            y = int(max(0, (sh - wh) / 2))
            self.set_position(x, y)
        except Exception as e:
            logger.debug("窗口居中失败: %s", e)
        return self._owner

    @property
    def info(self):
        try:
            r = bidi_browser.get_client_windows(self._owner._driver._browser_driver)
            wins = r.get("clientWindows", [])
            return wins[0] if wins else {}
        except Exception:
            return {}
