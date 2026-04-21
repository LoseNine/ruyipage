# -*- coding: utf-8 -*-
"""FirefoxBase - 所有页面/标签页/Frame 的共享基类

提供：导航、元素查找、JS 执行、截图、Cookie、弹窗处理等。
"""

import time
import base64
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .._base.base import BasePage
from .._base.driver import ContextDriver
from .._bidi import browsing_context as bidi_context
from .._bidi import script as bidi_script
from .._functions.bidi_values import parse_value, make_shared_ref
from .._functions.locator import parse_locator
from .._functions.settings import Settings
from .._functions.sleep import sleep as _sleep
from .._bidi.input_ import build_human_click_actions
from ..errors import ElementNotFoundError, JavaScriptError, WaitTimeoutError, BiDiError

logger = logging.getLogger("ruyipage")

if TYPE_CHECKING:
    from .._elements.firefox_element import FirefoxElement
    from .._elements.none_element import NoneElement
    from .._elements.static_element import StaticElement
    from .._units.actions import Actions
    from .._units.browser import BrowserManager
    from .._units.contexts import ContextManager
    from .._units.downloads import DownloadsManager
    from .._units.events import EventTracker
    from .._units.network_tools import NetworkManager
    from .._units.navigation import NavigationTracker
    from .._units.touch_actions import TouchActions
    from .._units.scroller import PageScroller
    from .._units.listener import Listener
    from .._units.interceptor import Interceptor
    from .._units.window import WindowManager
    from .._units.prefs import PrefsManager
    from .._units.realm_tracker import RealmTracker
    from .._units.config_manager import ConfigManager
    from .._units.waiter import PageWaiter
    from .._units.rect import TabRect
    from .._units.states import PageStates
    from .._units.setter import PageSetter
    from .._units.storage import StorageManager
    from .._units.console_listener import ConsoleListener
    from .._units.emulation import EmulationManager
    from .._units.extensions import ExtensionManager
    from .._base.browser import Firefox
    from .._pages.firefox_frame import FirefoxFrame


class FirefoxBase(BasePage):
    """Firefox 页面/标签页/Frame 的共享基类"""

    _type = "FirefoxBase"

    def __init__(self):
        self._browser = None
        self._context_id = None
        self._driver = None  # type: ContextDriver
        self._is_loading = False
        self._ready_state = None
        self._load_mode = "normal"
        self._ua_preload_script_id = None  # 用于 UA override 的 preload script ID

        # 惰性加载的 units
        self._scroll = None
        self._actions = None
        self._touch = None
        self._wait = None
        self._listener = None
        self._rect = None
        self._states = None
        self._setter = None
        self._local_storage = None
        self._session_storage = None
        self._console = None
        self._interceptor = None
        self._network_manager = None
        self._window = None
        self._browser_manager = None
        self._contexts = None
        self._emulation = None
        self._extensions = None
        self._downloads = None
        self._events = None
        self._navigation = None
        self._prefs = None
        self._realms = None
        self._config = None
        self._last_prompt_opened = None
        self._last_prompt_closed = None
        self._prompt_subscription_id = None
        self._prompt_handler_config = None

    def _init_context(self, browser, context_id):
        """初始化上下文连接

        Args:
            browser: Firefox 实例
            context_id: browsingContext ID
        """
        self._browser = browser
        self._context_id = context_id
        self._driver = ContextDriver(browser.driver, context_id)
        self._load_mode = browser.options.load_mode
        self._maybe_enable_xpath_picker()
        self._maybe_enable_action_visual()

    def _maybe_enable_xpath_picker(self):
        """按启动配置自动启用 XPath picker。"""
        options = getattr(self._browser, "options", None)
        if not options or not getattr(options, "xpath_picker_enabled", False):
            return

        try:
            if not getattr(
                self._browser, "_xpath_picker_global_preload_script_id", None
            ):
                result = bidi_script.add_preload_script(
                    self._driver._browser_driver,
                    self._get_xpath_picker_script(),
                )
                self._browser._xpath_picker_global_preload_script_id = result.get(
                    "script", ""
                )
            self.run_js(f"({self._get_xpath_picker_script()})()", as_expr=True)
            self.run_js(
                self._get_xpath_picker_frame_bridge_script(),
                self._get_xpath_picker_script(),
                as_expr=False,
            )
        except Exception as e:
            logger.debug("XPath picker 注入失败: %s", e)

    def _reinject_xpath_picker_if_needed(self):
        """在导航完成后的当前页面重新显式注入 picker。"""
        options = getattr(self._browser, "options", None)
        if not options or not getattr(options, "xpath_picker_enabled", False):
            return

        try:
            script_source = self._get_xpath_picker_script()
            self.run_js(f"({script_source})()", as_expr=True)
            self.run_js(
                self._get_xpath_picker_frame_bridge_script(),
                script_source,
                as_expr=False,
            )
        except Exception as e:
            logger.debug("XPath picker 重新注入失败: %s", e)

    def _maybe_enable_action_visual(self):
        """按启动配置自动启用鼠标行为可视化。"""
        options = getattr(self._browser, "options", None)
        if not options or not getattr(options, "action_visual_enabled", False):
            return

        try:
            if not getattr(
                self._browser, "_action_visual_global_preload_script_id", None
            ):
                result = bidi_script.add_preload_script(
                    self._driver._browser_driver,
                    self._get_action_visual_script(),
                )
                self._browser._action_visual_global_preload_script_id = result.get(
                    "script", ""
                )
            self.run_js(f"({self._get_action_visual_script()})()", as_expr=True)
        except Exception as e:
            logger.debug("鼠标行为可视化注入失败: %s", e)

    def _reinject_action_visual_if_needed(self):
        """在导航完成后重新注入鼠标行为可视化。"""
        options = getattr(self._browser, "options", None)
        if not options or not getattr(options, "action_visual_enabled", False):
            return

        try:
            self.run_js(f"({self._get_action_visual_script()})()", as_expr=True)
        except Exception as e:
            logger.debug("鼠标行为可视化重新注入失败: %s", e)

    @staticmethod
    def _get_action_visual_script():
        """鼠标行为可视化调试脚本 — 数据驱动渲染，不依赖 DOM 事件。

        Python 端通过 run_js 调用以下全局函数来驱动渲染：
        - window.__ruyiAV.moves(points)          渲染鼠标移动轨迹
        - window.__ruyiAV.click(x,y,btn)         渲染点击动画
        - window.__ruyiAV.highlight(rect,label)  高亮点击目标元素
        """
        return r"""(function() {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    if (window.__ruyiAV) return;

    var CANVAS_ID = '__ruyi_av_canvas__';
    var DOT_ID = '__ruyi_av_dot__';
    var COORD_ID = '__ruyi_av_coord__';
    var HIGHLIGHT_ID = '__ruyi_av_highlight__';

    // --- 光标圆点 ---
    var dot = document.getElementById(DOT_ID);
    if (!dot) {
        dot = document.createElement('div');
        dot.id = DOT_ID;
        dot.style.cssText = 'position:fixed;width:14px;height:14px;border-radius:50%;' +
            'background:rgba(255,50,50,0.5);border:2px solid rgba(255,50,50,0.85);' +
            'pointer-events:none;z-index:2147483646;transform:translate(-50%,-50%);display:none;';
        document.documentElement.appendChild(dot);
    }

    // --- 坐标标签 ---
    var coord = document.getElementById(COORD_ID);
    if (!coord) {
        coord = document.createElement('div');
        coord.id = COORD_ID;
        coord.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483646;' +
            'font:11px/1.2 monospace;color:#fff;background:rgba(0,0,0,0.65);' +
            'padding:2px 6px;border-radius:3px;display:none;white-space:nowrap;';
        document.documentElement.appendChild(coord);
    }

    // --- 目标高亮框 ---
    var highlight = document.getElementById(HIGHLIGHT_ID);
    if (!highlight) {
        highlight = document.createElement('div');
        highlight.id = HIGHLIGHT_ID;
        highlight.style.cssText = 'position:fixed;display:none;pointer-events:none;z-index:2147483646;' +
            'border:3px solid rgba(255,205,86,0.98);border-radius:10px;' +
            'background:rgba(255,205,86,0.12);box-shadow:0 0 0 2px rgba(255,255,255,0.20),0 14px 32px rgba(255,205,86,0.28);';
        document.documentElement.appendChild(highlight);
    }
    var highlightLabel = document.createElement('div');
    highlightLabel.style.cssText = 'position:absolute;left:0;top:-28px;padding:3px 8px;border-radius:999px;' +
        'font:11px/1.2 monospace;color:#111827;background:rgba(255,205,86,0.96);white-space:nowrap;';
    highlight.appendChild(highlightLabel);
    var highlightTimer = null;

    // --- Canvas 轨迹层 ---
    var canvas = document.getElementById(CANVAS_ID);
    if (!canvas) {
        canvas = document.createElement('canvas');
        canvas.id = CANVAS_ID;
        canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;' +
            'pointer-events:none;z-index:2147483645;';
        document.documentElement.appendChild(canvas);
    }
    function resizeCanvas() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    var ctx = canvas.getContext('2d');

    // --- 轨迹状态 ---
    var trail = [];
    var MAX_TRAIL = 200;
    var fadeTimer = null;
    var fadeOpacity = 1.0;
    var moveQueue = [];
    var moveRaf = 0;

    function drawTrail() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (trail.length < 2) return;
        var len = trail.length;
        ctx.lineWidth = 5;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        for (var i = 1; i < len; i++) {
            var age = 1 - (i / len);
            var alpha = (0.15 + 0.55 * (i / len)) * fadeOpacity;
            var r = Math.round(80 + 175 * age);
            var g = Math.round(60 + 140 * (1 - age));
            var b = Math.round(200 + 55 * (1 - age));
            ctx.strokeStyle = 'rgba(' + r + ',' + g + ',' + b + ',' + alpha.toFixed(2) + ')';
            ctx.beginPath();
            ctx.moveTo(trail[i-1][0], trail[i-1][1]);
            ctx.lineTo(trail[i][0], trail[i][1]);
            ctx.stroke();
        }
    }

    function startFadeOut() {
        if (fadeTimer) clearInterval(fadeTimer);
        fadeOpacity = 1.0;
        fadeTimer = setInterval(function() {
            fadeOpacity -= 0.025;
            if (fadeOpacity <= 0) {
                fadeOpacity = 0;
                clearInterval(fadeTimer);
                fadeTimer = null;
                trail = [];
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                return;
            }
            drawTrail();
        }, 40);
    }

    function moveDot(x, y) {
        dot.style.left = x + 'px';
        dot.style.top = y + 'px';
        dot.style.display = 'block';
        coord.style.left = (x + 16) + 'px';
        coord.style.top = (y + 16) + 'px';
        coord.textContent = '(' + x + ', ' + y + ')';
        coord.style.display = 'block';
    }

    // === API: 渲染鼠标移动轨迹 ===
    // points: [[x,y], [x,y], ...]
    function pumpMoves() {
        moveRaf = 0;
        if (!moveQueue.length) {
            startFadeOut();
            return;
        }
        if (fadeTimer) { clearInterval(fadeTimer); fadeTimer = null; }
        fadeOpacity = 1.0;

        var batch = Math.min(moveQueue.length, 3);
        for (var i = 0; i < batch; i++) {
            var pt = moveQueue.shift();
            trail.push(pt);
            if (trail.length > MAX_TRAIL) {
                trail.shift();
            }
            moveDot(pt[0], pt[1]);
        }
        drawTrail();
        moveRaf = window.requestAnimationFrame(pumpMoves);
    }

    function renderMoves(points) {
        if (!points || !points.length) return;
        for (var i = 0; i < points.length; i++) {
            moveQueue.push(points[i]);
        }
        if (!moveRaf) {
            moveRaf = window.requestAnimationFrame(pumpMoves);
        }
    }

    // === API: 渲染点击动画 ===
    function renderClick(x, y, button) {
        var color = button === 2 ? '255,60,60' : button === 1 ? '60,60,255' : '60,200,60';
        moveDot(x, y);

        var ring = document.createElement('div');
        ring.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;' +
            'border:3px solid rgba(' + color + ',0.92);border-radius:50%;' +
            'width:14px;height:14px;left:' + x + 'px;top:' + y + 'px;' +
            'transform:translate(-50%,-50%);';
        document.documentElement.appendChild(ring);
        var ch = document.createElement('div');
        ch.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;' +
            'left:' + (x-18) + 'px;top:' + (y-1) + 'px;width:36px;height:2px;' +
            'background:rgba(' + color + ',0.68);';
        var cv = document.createElement('div');
        cv.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;' +
            'left:' + (x-1) + 'px;top:' + (y-18) + 'px;width:2px;height:36px;' +
            'background:rgba(' + color + ',0.68);';
        document.documentElement.appendChild(ch);
        document.documentElement.appendChild(cv);
        var sz = 14, op = 0.92;
        var anim = setInterval(function() {
            sz += 3.2; op -= 0.03;
            if (op <= 0) { clearInterval(anim); ring.remove(); ch.remove(); cv.remove(); return; }
            ring.style.width = sz + 'px';
            ring.style.height = sz + 'px';
            ring.style.borderColor = 'rgba(' + color + ',' + op.toFixed(2) + ')';
        }, 20);
    }

    function renderHighlight(rect, label) {
        if (!rect) return;
        highlight.style.display = 'block';
        highlight.style.left = Math.max(0, rect.x) + 'px';
        highlight.style.top = Math.max(0, rect.y) + 'px';
        highlight.style.width = Math.max(0, rect.width) + 'px';
        highlight.style.height = Math.max(0, rect.height) + 'px';
        highlightLabel.textContent = label || 'target';
        if (highlightTimer) {
            clearTimeout(highlightTimer);
        }
        highlightTimer = setTimeout(function() {
            highlight.style.display = 'none';
        }, 900);
    }

    // 挂载到全局
    window.__ruyiAV = {
        moves: renderMoves,
        click: renderClick,
        highlight: renderHighlight
    };
})"""

    @staticmethod
    def _is_expected_navigation_abort(error):
        """判断是否为 Firefox 导航被页面主动中断的可预期情况。"""
        if not isinstance(error, BiDiError):
            return False

        text = "{} {}".format(error.error or "", error.bidi_message or "").upper()
        return "NS_BINDING_ABORTED" in text

    @staticmethod
    def _get_xpath_picker_frame_bridge_script():
        return r"""
(source) => {
    if (typeof window === 'undefined' || window.top !== window) {
        return false;
    }

    const injectWindow = (targetWindow) => {
        try {
            if (!targetWindow || !targetWindow.eval) {
                return;
            }
            targetWindow.eval(`(${source})()`);
        } catch (e) {
        }
    };

    const bindFrame = (frame) => {
        if (!frame || frame.__ruyiXPathPickerBound) {
            return;
        }
        frame.__ruyiXPathPickerBound = true;

        const inject = () => {
            try {
                const childWindow = frame.contentWindow;
                if (!childWindow || childWindow === window) {
                    return;
                }
                injectWindow(childWindow);
                scanWindow(childWindow);
            } catch (e) {
            }
        };

        const tryInjectReady = () => {
            let attempts = 0;
            const timer = window.setInterval(() => {
                attempts += 1;
                try {
                    const childWindow = frame.contentWindow;
                    const childDocument = childWindow && childWindow.document;
                    if (childWindow && childDocument && childDocument.readyState !== 'loading') {
                        inject();
                        window.clearInterval(timer);
                        return;
                    }
                } catch (e) {
                    window.clearInterval(timer);
                    return;
                }
                if (attempts >= 20) {
                    window.clearInterval(timer);
                }
            }, 150);
        };

        frame.addEventListener('load', inject);
        inject();
        tryInjectReady();
    };

    const observeWindow = (targetWindow) => {
        try {
            const targetDocument = targetWindow.document;
            if (!targetDocument || targetDocument.__ruyiXPathPickerObserverBound) {
                return;
            }
            let pending = false;
            const observer = new MutationObserver(() => {
                if (pending) {
                    return;
                }
                pending = true;
                targetWindow.setTimeout(() => {
                    pending = false;
                    scanWindow(targetWindow);
                }, 50);
            });
            observer.observe(targetDocument.documentElement || targetDocument, {
                childList: true,
                subtree: true,
            });
            targetDocument.__ruyiXPathPickerObserverBound = true;
        } catch (e) {
        }
    };

    const scanWindow = (targetWindow) => {
        try {
            const frames = Array.from(targetWindow.document.querySelectorAll('iframe'));
            frames.forEach(bindFrame);
            observeWindow(targetWindow);
        } catch (e) {
        }
    };

    window.__ruyiXPathPickerInjectIntoFrames = () => scanWindow(window);
    scanWindow(window);
    return true;
}
"""

    @staticmethod
    def _get_xpath_picker_script():
        return r"""
() => {
    if (typeof window === 'undefined' || typeof document === 'undefined') {
        return;
    }

    const PANEL_ID = '__ruyi_xpath_picker_panel__';
    const HIGHLIGHT_ID = '__ruyi_xpath_picker_highlight__';

    let isTopWindow = false;
    let topWindowRef = window;
    try {
        isTopWindow = window.top === window;
        topWindowRef = isTopWindow ? window : window.top;
    } catch (e) {
        isTopWindow = true;
        topWindowRef = window;
    }

    const state = topWindowRef.__ruyiXPathPicker__ || {
        mode: 'idle',
        collapsed: false,
        activeTab: 'info',
        hoverData: null,
        selectedData: null,
        validation: {
            relative: null,
            absolute: null,
        },
        group: {
            sourceKey: null,
            items: [],
            commonXPath: '',
            commonCss: '',
            anchorName: '',
            anchorTag: '',
            diagnostics: [],
            statusText: '',
        },
        panel: null,
        watchdogBound: false,
    };
    topWindowRef.__ruyiXPathPicker__ = state;

    const localState = window.__ruyiXPathPickerLocal__ || {
        hoverElement: null,
        selectedElement: null,
        highlight: null,
        handlersBound: false,
        boundDocument: null,
        moveHandler: null,
        clickHandler: null,
        scrollHandler: null,
        resizeHandler: null,
    };
    window.__ruyiXPathPickerLocal__ = localState;

    function ensureStyles() {
        if (document.getElementById('__ruyi_xpath_picker_style__')) {
            return;
        }
        const style = document.createElement('style');
        style.id = '__ruyi_xpath_picker_style__';
        style.textContent = `
            #${PANEL_ID} {
                position: fixed;
                right: 16px;
                bottom: 16px;
                width: min(320px, calc(100vw - 24px));
                max-height: min(70vh, 560px);
                overflow: auto;
                padding: 14px;
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.16);
                background: rgba(15, 23, 42, 0.62);
                color: #e5eefb;
                box-shadow: 0 18px 42px rgba(2, 6, 23, 0.34);
                backdrop-filter: blur(16px) saturate(140%);
                -webkit-backdrop-filter: blur(16px) saturate(140%);
                font-family: Inter, "Segoe UI", Arial, sans-serif;
                font-size: 12px;
                line-height: 1.5;
                z-index: 2147483647;
                transition: width 0.18s ease, padding 0.18s ease, transform 0.18s ease;
            }
            #${PANEL_ID}[data-collapsed="true"] {
                width: auto;
                max-height: none;
                overflow: visible;
                padding: 10px 12px;
                border-radius: 999px;
            }
            #${PANEL_ID} * {
                box-sizing: border-box;
            }
            #${PANEL_ID} .ruyi-xpath-picker__header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 12px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__title {
                font-size: 13px;
                font-weight: 700;
                letter-spacing: 0.02em;
                color: #f8fafc;
            }
            #${PANEL_ID}[data-collapsed="true"] .ruyi-xpath-picker__title {
                font-size: 12px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__badge {
                display: inline-flex;
                align-items: center;
                padding: 3px 8px;
                border-radius: 999px;
                background: rgba(96, 165, 250, 0.18);
                color: #bfdbfe;
                font-size: 11px;
                font-weight: 600;
                white-space: nowrap;
            }
            #${PANEL_ID}[data-collapsed="true"] .ruyi-xpath-picker__badge {
                display: inline-flex;
            }
            #${PANEL_ID} .ruyi-xpath-picker__intro {
                margin: 0 0 12px;
                color: rgba(226, 232, 240, 0.82);
            }
            #${PANEL_ID}[data-collapsed="true"] .ruyi-xpath-picker__intro,
            #${PANEL_ID}[data-collapsed="true"] .ruyi-xpath-picker__meta,
            #${PANEL_ID}[data-collapsed="true"] .ruyi-xpath-picker__actions {
                display: none;
            }
            #${PANEL_ID} .ruyi-xpath-picker__meta {
                display: grid;
                gap: 10px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__inline-actions {
                display: inline-flex;
                align-items: center;
                gap: 6px;
                flex-wrap: wrap;
                justify-content: flex-end;
            }
            #${PANEL_ID} .ruyi-xpath-picker__tabs {
                display: flex;
                gap: 8px;
                margin-bottom: 12px;
                flex-wrap: wrap;
            }
            #${PANEL_ID} .ruyi-xpath-picker__tab {
                appearance: none;
                border: 1px solid rgba(148, 163, 184, 0.18);
                border-radius: 999px;
                padding: 6px 10px;
                background: rgba(15, 23, 42, 0.24);
                color: #cbd5e1;
                cursor: pointer;
                font-size: 11px;
                font-weight: 700;
            }
            #${PANEL_ID} .ruyi-xpath-picker__tab[data-active="true"] {
                background: rgba(59, 130, 246, 0.22);
                color: #eff6ff;
                border-color: rgba(96, 165, 250, 0.35);
            }
            #${PANEL_ID} .ruyi-xpath-picker__code-block {
                padding: 12px;
                border-radius: 12px;
                background: rgba(2, 6, 23, 0.5);
                border: 1px solid rgba(148, 163, 184, 0.16);
                color: #e2e8f0;
                white-space: pre-wrap;
                word-break: break-word;
                font-family: Consolas, "SFMono-Regular", monospace;
                font-size: 11px;
                line-height: 1.6;
            }
            #${PANEL_ID} .ruyi-xpath-picker__field-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                margin-bottom: 6px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__copy {
                appearance: none;
                border: 1px solid rgba(148, 163, 184, 0.18);
                border-radius: 999px;
                padding: 4px 8px;
                background: rgba(148, 163, 184, 0.12);
                color: #cbd5e1;
                cursor: pointer;
                font-size: 10px;
                font-weight: 700;
                line-height: 1;
                white-space: nowrap;
            }
            #${PANEL_ID} .ruyi-xpath-picker__copy[data-copied="true"] {
                background: rgba(34, 197, 94, 0.18);
                color: #dcfce7;
                border-color: rgba(34, 197, 94, 0.28);
            }
            #${PANEL_ID} .ruyi-xpath-picker__field {
                padding: 10px 11px;
                border-radius: 12px;
                background: rgba(15, 23, 42, 0.34);
                border: 1px solid rgba(148, 163, 184, 0.14);
            }
            #${PANEL_ID} .ruyi-xpath-picker__field[data-kind="validation"] {
                border-color: rgba(96, 165, 250, 0.18);
            }
            #${PANEL_ID} .ruyi-xpath-picker__label {
                display: block;
                margin-bottom: 4px;
                color: #93c5fd;
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.04em;
            }
            #${PANEL_ID} .ruyi-xpath-picker__value {
                color: #f8fafc;
                word-break: break-word;
                white-space: pre-wrap;
            }
            #${PANEL_ID} .ruyi-xpath-picker__value[data-code="true"] {
                font-family: Consolas, "SFMono-Regular", monospace;
                font-size: 11px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__hint {
                margin-top: 6px;
                color: rgba(191, 219, 254, 0.78);
                font-size: 11px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__status-row {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-top: 8px;
                flex-wrap: wrap;
            }
            #${PANEL_ID} .ruyi-xpath-picker__status-chip {
                display: inline-flex;
                align-items: center;
                padding: 3px 8px;
                border-radius: 999px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.02em;
                white-space: nowrap;
            }
            #${PANEL_ID} .ruyi-xpath-picker__status-chip[data-tone="green"] {
                background: rgba(34, 197, 94, 0.18);
                color: #dcfce7;
                border: 1px solid rgba(34, 197, 94, 0.28);
            }
            #${PANEL_ID} .ruyi-xpath-picker__status-chip[data-tone="red"] {
                background: rgba(239, 68, 68, 0.18);
                color: #fee2e2;
                border: 1px solid rgba(248, 113, 113, 0.28);
            }
            #${PANEL_ID} .ruyi-xpath-picker__status-chip[data-tone="yellow"] {
                background: rgba(245, 158, 11, 0.18);
                color: #fef3c7;
                border: 1px solid rgba(251, 191, 36, 0.28);
            }
            #${PANEL_ID} .ruyi-xpath-picker__status-chip[data-tone="blue"] {
                background: rgba(59, 130, 246, 0.18);
                color: #dbeafe;
                border: 1px solid rgba(96, 165, 250, 0.28);
            }
            #${PANEL_ID} .ruyi-xpath-picker__group-list {
                display: grid;
                gap: 8px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__group-item {
                display: grid;
                grid-template-columns: 1fr auto;
                gap: 10px;
                padding: 10px 11px;
                border-radius: 12px;
                background: rgba(2, 6, 23, 0.34);
                border: 1px solid rgba(148, 163, 184, 0.14);
            }
            #${PANEL_ID} .ruyi-xpath-picker__group-item-main {
                min-width: 0;
            }
            #${PANEL_ID} .ruyi-xpath-picker__group-item-title {
                color: #f8fafc;
                font-weight: 700;
                margin-bottom: 4px;
                word-break: break-word;
            }
            #${PANEL_ID} .ruyi-xpath-picker__group-item-sub {
                color: rgba(226, 232, 240, 0.78);
                font-size: 11px;
                word-break: break-word;
            }
            #${PANEL_ID} .ruyi-xpath-picker__compare-list {
                display: grid;
                gap: 10px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__compare-item {
                padding: 10px 11px;
                border-radius: 12px;
                background: rgba(2, 6, 23, 0.34);
                border: 1px solid rgba(148, 163, 184, 0.14);
            }
            #${PANEL_ID} .ruyi-xpath-picker__compare-title {
                color: #f8fafc;
                font-weight: 700;
                margin-bottom: 6px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__remove {
                appearance: none;
                border: 1px solid rgba(248, 113, 113, 0.18);
                border-radius: 999px;
                padding: 4px 8px;
                background: rgba(239, 68, 68, 0.12);
                color: #fecaca;
                cursor: pointer;
                font-size: 10px;
                font-weight: 800;
                align-self: start;
            }
            #${PANEL_ID} .ruyi-xpath-picker__actions {
                display: flex;
                gap: 8px;
                margin-top: 14px;
                flex-wrap: wrap;
            }
            #${PANEL_ID} .ruyi-xpath-picker__header-actions {
                display: inline-flex;
                align-items: center;
                gap: 8px;
            }
            #${PANEL_ID}[data-collapsed="true"] .ruyi-xpath-picker__header {
                margin-bottom: 0;
                gap: 10px;
            }
            #${PANEL_ID} .ruyi-xpath-picker__button {
                appearance: none;
                border: 0;
                border-radius: 10px;
                padding: 9px 12px;
                cursor: pointer;
                font-size: 12px;
                font-weight: 700;
                transition: transform 0.16s ease, background 0.16s ease, opacity 0.16s ease;
            }
            #${PANEL_ID} .ruyi-xpath-picker__button:hover {
                transform: translateY(-1px);
            }
            #${PANEL_ID} .ruyi-xpath-picker__button--primary {
                background: rgba(59, 130, 246, 0.92);
                color: #eff6ff;
            }
            #${PANEL_ID} .ruyi-xpath-picker__button--ghost {
                background: rgba(148, 163, 184, 0.18);
                color: #e2e8f0;
            }
            #${PANEL_ID} .ruyi-xpath-picker__button--icon {
                min-width: 34px;
                padding: 8px 10px;
                border-radius: 999px;
                background: rgba(148, 163, 184, 0.18);
                color: #e2e8f0;
                line-height: 1;
            }
            #${PANEL_ID} .ruyi-xpath-picker__button--secondary {
                background: rgba(15, 23, 42, 0.26);
                color: #e2e8f0;
                border: 1px solid rgba(148, 163, 184, 0.18);
            }
            #${PANEL_ID} .ruyi-xpath-picker__button[disabled] {
                opacity: 0.45;
                cursor: not-allowed;
                transform: none;
            }
            #${HIGHLIGHT_ID} {
                position: absolute;
                display: none;
                border-radius: 12px;
                border: 2px solid rgba(96, 165, 250, 0.95);
                background: rgba(96, 165, 250, 0.12);
                box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.28), 0 10px 30px rgba(37, 99, 235, 0.18);
                pointer-events: none;
                z-index: 2147483646;
            }
            #__ruyi_xpath_picker_aux_overlay__ .ruyi-xpath-picker__overlay-box {
                position: absolute;
                border: 2px dashed rgba(245, 158, 11, 0.96);
                border-radius: 12px;
                pointer-events: none;
            }
            #__ruyi_xpath_picker_aux_overlay__ .ruyi-xpath-picker__overlay-tag {
                position: absolute;
                top: -10px;
                left: 8px;
                padding: 2px 6px;
                border-radius: 999px;
                color: #fff;
                font-size: 10px;
                font-weight: 800;
                line-height: 1.2;
                white-space: nowrap;
                box-shadow: 0 4px 12px rgba(15, 23, 42, 0.20);
            }
            @media (max-width: 640px) {
                #${PANEL_ID} {
                    right: 12px;
                    bottom: 12px;
                    width: calc(100vw - 24px);
                    max-height: 58vh;
                }
                #${PANEL_ID} .ruyi-xpath-picker__actions {
                    flex-direction: column;
                }
            }
        `;
        (document.head || document.documentElement).appendChild(style);
    }

    function ensurePanel() {
        if (!isTopWindow) {
            return null;
        }
        let panel = document.getElementById(PANEL_ID);
        if (!panel) {
            panel = document.createElement('div');
            panel.id = PANEL_ID;
            panel.setAttribute('data-collapsed', 'false');
            panel.innerHTML = `
                <div class="ruyi-xpath-picker__header">
                    <div class="ruyi-xpath-picker__title">XPath Picker</div>
                    <div class="ruyi-xpath-picker__header-actions">
                        <div class="ruyi-xpath-picker__badge" data-role="status">待选择</div>
                        <button type="button" class="ruyi-xpath-picker__button ruyi-xpath-picker__button--icon" data-action="toggle" aria-label="收起 XPath Picker">-</button>
                    </div>
                </div>
                <p class="ruyi-xpath-picker__intro" data-role="intro">移动鼠标可预览目标，点击后锁定当前元素。</p>
                <div class="ruyi-xpath-picker__tabs">
                    <button type="button" class="ruyi-xpath-picker__tab" data-tab="info" data-active="true">Info</button>
                    <button type="button" class="ruyi-xpath-picker__tab" data-tab="group" data-active="false">元素组</button>
                </div>
                <div class="ruyi-xpath-picker__meta" data-role="meta"></div>
                <div class="ruyi-xpath-picker__actions">
                    <button type="button" class="ruyi-xpath-picker__button ruyi-xpath-picker__button--primary" data-action="unlock" disabled>继续选择</button>
                    <button type="button" class="ruyi-xpath-picker__button ruyi-xpath-picker__button--secondary" data-action="pause">暂停选择</button>
                    <button type="button" class="ruyi-xpath-picker__button ruyi-xpath-picker__button--secondary" data-action="capture-group" disabled>捕获相似元素</button>
                    <button type="button" class="ruyi-xpath-picker__button ruyi-xpath-picker__button--ghost" data-action="toggle">收起</button>
                </div>
            `;
            document.documentElement.appendChild(panel);
            const unlockButton = panel.querySelector('[data-action="unlock"]');
            const pauseButton = panel.querySelector('[data-action="pause"]');
            const captureGroupButton = panel.querySelector('[data-action="capture-group"]');
            const toggleButtons = panel.querySelectorAll('[data-action="toggle"]');
            const tabs = panel.querySelectorAll('[data-tab]');
            if (unlockButton) {
                unlockButton.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    unlockSelection();
                });
            }
            if (pauseButton) {
                pauseButton.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    togglePaused();
                });
            }
            if (captureGroupButton) {
                captureGroupButton.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    captureSimilarGroup();
                });
            }
            toggleButtons.forEach((button) => {
                button.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    toggleCollapsed();
                });
            });
            tabs.forEach((button) => {
                button.addEventListener('click', (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    state.activeTab = button.getAttribute('data-tab') || 'info';
                    syncTopUI();
                });
            });
            panel.addEventListener('click', (event) => {
                const copyButton = event.target.closest('[data-copy-value]');
                if (copyButton) {
                    event.preventDefault();
                    event.stopPropagation();
                    copyText(copyButton.getAttribute('data-copy-value') || '', copyButton);
                    return;
                }
                const validateButton = event.target.closest('[data-validate-kind]');
                if (validateButton) {
                    event.preventDefault();
                    event.stopPropagation();
                    runValidation(validateButton.getAttribute('data-validate-kind') || 'relative');
                    return;
                }
                const removeButton = event.target.closest('[data-remove-group-index]');
                if (removeButton) {
                    event.preventDefault();
                    event.stopPropagation();
                    removeGroupItem(parseInt(removeButton.getAttribute('data-remove-group-index') || '-1', 10));
                    return;
                }
                const clearGroupButton = event.target.closest('[data-clear-group]');
                if (clearGroupButton) {
                    event.preventDefault();
                    event.stopPropagation();
                    clearGroupCapture();
                }
            });
        }
        state.panel = panel;
        return panel;
    }

    function ensureHighlight() {
        let highlight = document.getElementById(HIGHLIGHT_ID);
        if (!highlight) {
            highlight = document.createElement('div');
            highlight.id = HIGHLIGHT_ID;
            document.documentElement.appendChild(highlight);
        }
        localState.highlight = highlight;
        return highlight;
    }

    function getElementName(element) {
        if (!element || !element.tagName) {
            return '';
        }
        const tag = element.tagName.toLowerCase();
        const id = element.id ? `#${element.id}` : '';
        const nameAttr = element.getAttribute('name');
        const ariaLabel = element.getAttribute('aria-label');
        const dataTestId = element.getAttribute('data-testid') || element.getAttribute('data-test') || element.getAttribute('data-qa');
        const className = typeof element.className === 'string'
            ? element.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((item) => `.${item}`).join('')
            : '';
        const hints = [nameAttr && `name=${nameAttr}`, ariaLabel && `aria=${ariaLabel}`, dataTestId && `data=${dataTestId}`]
            .filter(Boolean)
            .slice(0, 1);
        return [tag + id + className, ...hints].join(' ');
    }

    function normalizeText(text) {
        return String(text || '').replace(/\s+/g, ' ').trim();
    }

    function getVisibleText(element) {
        if (!element) {
            return '';
        }
        const text = normalizeText(element.innerText || element.textContent || '');
        return text.length > 160 ? `${text.slice(0, 157)}...` : text;
    }

    function getFrameContextPath() {
        const path = [];
        let currentWindow = window;
        while (currentWindow && currentWindow !== currentWindow.top) {
            try {
                const frame = currentWindow.frameElement;
                if (!frame) {
                    break;
                }
                const name = frame.getAttribute('name') || frame.getAttribute('title') || frame.id || frame.tagName.toLowerCase();
                path.unshift(name);
                currentWindow = currentWindow.parent;
            } catch (e) {
                path.unshift('cross-origin-frame');
                break;
            }
        }
        return path;
    }

    function getElementContext(element) {
        const framePath = getFrameContextPath();
        const parts = [];
        if (framePath.length) {
            parts.push(`iframe: ${framePath.join(' > ')}`);
        } else {
            parts.push('main document');
        }

        const root = element && typeof element.getRootNode === 'function' ? element.getRootNode() : null;
        if (root instanceof ShadowRoot) {
            parts.push(`shadow(open): ${root.host && root.host.tagName ? root.host.tagName.toLowerCase() : 'host'}`);
        }

        return parts.join(' | ');
    }

    function isInsideShadow(element) {
        if (!element || typeof element.getRootNode !== 'function') {
            return false;
        }
        return element.getRootNode() instanceof ShadowRoot;
    }

    function getShadowPath(element) {
        const chain = [];
        let current = element;
        while (current && typeof current.getRootNode === 'function') {
            const root = current.getRootNode();
            if (!(root instanceof ShadowRoot)) {
                break;
            }
            const host = root.host;
            chain.unshift({
                mode: 'open',
                selector: getHostSelector(host),
            });
            current = host;
        }
        return chain;
    }

    function getViewportOffsetToTop() {
        let left = 0;
        let top = 0;
        let currentWindow = window;
        while (currentWindow && currentWindow !== currentWindow.top) {
            try {
                const frame = currentWindow.frameElement;
                if (!frame) {
                    break;
                }
                const rect = frame.getBoundingClientRect();
                left += rect.left;
                top += rect.top;
                currentWindow = currentWindow.parent;
            } catch (e) {
                break;
            }
        }
        return { left, top };
    }

    function getElementCenter(element) {
        const rect = element.getBoundingClientRect();
        const topOffset = getViewportOffsetToTop();
        return {
            x: Math.round(rect.left + rect.width / 2 + window.scrollX),
            y: Math.round(rect.top + rect.height / 2 + window.scrollY),
            topViewportLeft: rect.left + topOffset.left,
            topViewportTop: rect.top + topOffset.top,
            rect,
        };
    }

    function escapeXPathLiteral(value) {
        const text = String(value || '');
        if (!text.includes('"')) {
            return `"${text}"`;
        }
        if (!text.includes("'")) {
            return `'${text}'`;
        }
        return 'concat(' + text.split('"').map((part, index, parts) => {
            const pieces = [];
            if (part) {
                pieces.push(`"${part}"`);
            }
            if (index < parts.length - 1) {
                pieces.push(`'"'`);
            }
            return pieces.join(', ');
        }).filter(Boolean).join(', ') + ')';
    }

    function getXPathNodeName(element) {
        if (!element || !element.tagName) {
            return '*';
        }
        const tagName = element.tagName.toLowerCase();
        const namespace = element.namespaceURI || '';
        if (namespace && namespace !== 'http://www.w3.org/1999/xhtml') {
            const localName = typeof element.localName === 'string' ? element.localName : tagName;
            return `*[local-name()=${escapeXPathLiteral(localName)}]`;
        }
        return tagName;
    }

    function getSiblingIndex(element) {
        let index = 1;
        let sibling = element.previousElementSibling;
        while (sibling) {
            if ((sibling.namespaceURI || '') === (element.namespaceURI || '')
                && sibling.tagName === element.tagName) {
                index += 1;
            }
            sibling = sibling.previousElementSibling;
        }
        return index;
    }

    function countMatches(doc, expr) {
        try {
            return doc.evaluate(`count(${expr})`, doc, null, XPathResult.NUMBER_TYPE, null).numberValue;
        } catch (e) {
            return Number.POSITIVE_INFINITY;
        }
    }

    function buildSegmentWithIndex(element) {
        return `${getXPathNodeName(element)}[${getSiblingIndex(element)}]`;
    }

    function buildAncestorRelativeXPath(element, maxDepth) {
        const segments = [];
        let current = element;
        let depth = 0;
        while (current && current.nodeType === Node.ELEMENT_NODE && depth < maxDepth) {
            segments.unshift(buildSegmentWithIndex(current));
            const candidate = '//' + segments.join('/');
            if (countMatches(current.ownerDocument, candidate) === 1) {
                return candidate;
            }
            current = current.parentElement;
            depth += 1;
        }
        return '';
    }

    function getStableAttributeSelector(element) {
        const attrs = ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'placeholder', 'type', 'role', 'title'];
        for (const attr of attrs) {
            const value = normalizeText(element.getAttribute(attr));
            if (!value) {
                continue;
            }
            const expr = `//${getXPathNodeName(element)}[@${attr}=${escapeXPathLiteral(value)}]`;
            if (countMatches(element.ownerDocument, expr) === 1) {
                return expr;
            }
        }
        return '';
    }

    function countCssMatches(root, selector) {
        if (!root || !selector) {
            return Number.POSITIVE_INFINITY;
        }
        try {
            return root.querySelectorAll(selector).length;
        } catch (e) {
            return Number.POSITIVE_INFINITY;
        }
    }

    function buildCssSegment(element) {
        if (!(element instanceof Element)) {
            return '';
        }
        const tagName = element.tagName.toLowerCase();
        if (element.id) {
            return `${tagName}#${cssEscapeValue(element.id)}`;
        }
        const stableAttrs = ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'placeholder', 'type', 'role', 'title'];
        for (const attr of stableAttrs) {
            const rawValue = element.getAttribute(attr);
            const value = normalizeText(rawValue);
            if (value) {
                return `${tagName}[${attr}="${String(rawValue).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`;
            }
        }
        const classNames = getCandidateClassNames(element);
        if (classNames.length) {
            return `${tagName}${classNames.map((item) => '.' + cssEscapeValue(item)).join('')}`;
        }
        if (!element.parentElement) {
            return tagName;
        }
        const siblings = Array.from(element.parentElement.children).filter((child) => child.tagName === element.tagName);
        const index = siblings.indexOf(element);
        return siblings.length > 1 ? `${tagName}:nth-of-type(${index + 1})` : tagName;
    }

    function buildAncestorRelativeCss(element, maxDepth) {
        const segments = [];
        let current = element;
        let depth = 0;
        const root = getContextRoot(element);
        while (current && current.nodeType === Node.ELEMENT_NODE && depth < maxDepth) {
            segments.unshift(buildCssSegment(current));
            const candidate = segments.join(' > ');
            if (countCssMatches(root, candidate) === 1) {
                return candidate;
            }
            current = current.parentElement;
            depth += 1;
        }
        return '';
    }

    function getCssSelector(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) {
            return '';
        }
        if (element.id) {
            return `#${cssEscapeValue(element.id)}`;
        }
        const root = getContextRoot(element);
        const stableAttrs = ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'placeholder', 'type', 'role', 'title'];
        for (const attr of stableAttrs) {
            const rawValue = element.getAttribute(attr);
            const value = normalizeText(rawValue);
            if (!value) {
                continue;
            }
            const candidate = `${element.tagName.toLowerCase()}[${attr}="${String(rawValue).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`;
            if (countCssMatches(root, candidate) === 1) {
                return candidate;
            }
        }
        const byAncestor = buildAncestorRelativeCss(element, 5);
        if (byAncestor) {
            return byAncestor;
        }
        return buildCssSegment(element);
    }

    function getAbsoluteXPath(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) {
            return '';
        }
        const segments = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
            segments.unshift(buildSegmentWithIndex(current));
            current = current.parentElement;
        }
        return '/' + segments.join('/');
    }

    function getRelativeXPath(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) {
            return '';
        }
        if (element.id) {
            return `//*[@id=${escapeXPathLiteral(element.id)}]`;
        }
        const stableAttr = getStableAttributeSelector(element);
        if (stableAttr) {
            return stableAttr;
        }

        const ownText = normalizeText(Array.from(element.childNodes)
            .filter((node) => node.nodeType === Node.TEXT_NODE)
            .map((node) => node.textContent || '')
            .join(' '));
        if (ownText && ownText.length <= 80) {
            const expr = `//${getXPathNodeName(element)}[normalize-space(text())=${escapeXPathLiteral(ownText)}]`;
            if (countMatches(element.ownerDocument, expr) === 1) {
                return expr;
            }
        }

        const ancestorRelative = buildAncestorRelativeXPath(element, 5);
        if (ancestorRelative) {
            return ancestorRelative;
        }

        return getAbsoluteXPath(element);
    }

    function collectElementData(element) {
        const center = getElementCenter(element);
        return {
            tag: element.tagName ? element.tagName.toLowerCase() : '',
            name: getElementName(element),
            text: getVisibleText(element),
            absoluteXPath: getAbsoluteXPath(element),
            relativeXPath: getRelativeXPath(element),
            cssSelector: getCssSelector(element),
            centerX: center.x,
            centerY: center.y,
            context: getElementContext(element),
            framePath: getFrameContextPath(),
            shadowPath: getShadowPath(element),
            topViewportLeft: center.topViewportLeft,
            topViewportTop: center.topViewportTop,
            width: center.rect.width,
            height: center.rect.height,
            rect: center.rect,
        };
    }

    function updateHighlight(element) {
        const highlight = ensureHighlight();
        if (!element || !document.documentElement.contains(element)) {
            highlight.style.display = 'none';
            return;
        }
        const rect = element.getBoundingClientRect();
        highlight.style.display = 'block';
        highlight.style.borderColor = state.mode === 'locked'
            ? 'rgba(59, 130, 246, 0.98)'
            : 'rgba(34, 197, 94, 0.95)';
        highlight.style.background = state.mode === 'locked'
            ? 'rgba(96, 165, 250, 0.12)'
            : 'rgba(34, 197, 94, 0.10)';
        highlight.style.left = `${rect.left + window.scrollX}px`;
        highlight.style.top = `${rect.top + window.scrollY}px`;
        highlight.style.width = `${Math.max(rect.width, 0)}px`;
        highlight.style.height = `${Math.max(rect.height, 0)}px`;
    }

    function getEventElement(event) {
        if (!event) {
            return null;
        }
        const path = typeof event.composedPath === 'function' ? event.composedPath() : null;
        if (Array.isArray(path)) {
            for (const item of path) {
                if (item instanceof Element) {
                    return item;
                }
            }
        }
        return event.target instanceof Element ? event.target : null;
    }

    function updateTopHighlightFromData(data) {
        if (!isTopWindow) {
            return;
        }
        const highlight = ensureHighlight();
        if (!data) {
            highlight.style.display = 'none';
            return;
        }
        highlight.style.display = 'block';
        highlight.style.borderColor = state.mode === 'locked'
            ? 'rgba(59, 130, 246, 0.98)'
            : 'rgba(34, 197, 94, 0.95)';
        highlight.style.background = state.mode === 'locked'
            ? 'rgba(96, 165, 250, 0.12)'
            : 'rgba(34, 197, 94, 0.10)';
        highlight.style.left = `${Math.max(data.topViewportLeft + topWindowRef.scrollX, 0)}px`;
        highlight.style.top = `${Math.max(data.topViewportTop + topWindowRef.scrollY, 0)}px`;
        highlight.style.width = `${Math.max(data.width || 0, 0)}px`;
        highlight.style.height = `${Math.max(data.height || 0, 0)}px`;
    }

    function getDisplayData() {
        if (state.mode === 'locked' || state.mode === 'paused') {
            return state.selectedData;
        }
        return state.hoverData;
    }

    function getElementKey(data) {
        if (!data) {
            return '';
        }
        return [
            data.relativeXPath || '',
            data.absoluteXPath || '',
            data.centerX || 0,
            data.centerY || 0,
            (data.framePath || []).join('>'),
            (data.shadowPath || []).map((item) => item.selector || '').join('>'),
        ].join('|');
    }

    function clearValidationState() {
        state.validation = {
            relative: null,
            absolute: null,
        };
        topWindowRef.__ruyiXPathPickerValidationRects__ = [];
    }

    function clearGroupState() {
        state.group = {
            sourceKey: null,
            items: [],
            commonXPath: '',
            commonCss: '',
            anchorName: '',
            anchorTag: '',
            diagnostics: [],
            statusText: '',
        };
        topWindowRef.__ruyiXPathPickerGroupRects__ = [];
    }

    function setOverlayRects(kind, rects) {
        const targetKey = kind === 'validation'
            ? '__ruyiXPathPickerValidationRects__'
            : '__ruyiXPathPickerGroupRects__';
        topWindowRef[targetKey] = Array.isArray(rects) ? rects : [];
    }

    function getOverlayRects(kind) {
        const targetKey = kind === 'validation'
            ? '__ruyiXPathPickerValidationRects__'
            : '__ruyiXPathPickerGroupRects__';
        const rects = topWindowRef[targetKey];
        return Array.isArray(rects) ? rects : [];
    }

    function buildOverlayHtml(rects, tone, prefix) {
        return rects.map((rect, index) => {
            const label = prefix ? `${prefix} ${index + 1}` : `${index + 1}`;
            return `
                <div class="ruyi-xpath-picker__overlay-box" style="left:${Math.max(rect.left + topWindowRef.scrollX, 0)}px;top:${Math.max(rect.top + topWindowRef.scrollY, 0)}px;width:${Math.max(rect.width || 0, 0)}px;height:${Math.max(rect.height || 0, 0)}px;border-color:${tone.border};background:${tone.fill};box-shadow:0 0 0 1px ${tone.shadow};">
                    <div class="ruyi-xpath-picker__overlay-tag" style="background:${tone.tag};">${label}</div>
                </div>
            `;
        }).join('');
    }

    function ensureAuxOverlay() {
        if (!isTopWindow) {
            return null;
        }
        let overlay = document.getElementById('__ruyi_xpath_picker_aux_overlay__');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = '__ruyi_xpath_picker_aux_overlay__';
            overlay.style.position = 'absolute';
            overlay.style.left = '0';
            overlay.style.top = '0';
            overlay.style.width = '0';
            overlay.style.height = '0';
            overlay.style.pointerEvents = 'none';
            overlay.style.zIndex = '2147483645';
            document.documentElement.appendChild(overlay);
        }
        return overlay;
    }

    function syncAuxOverlay() {
        if (!isTopWindow) {
            return;
        }
        const overlay = ensureAuxOverlay();
        if (!overlay) {
            return;
        }
        const validationRects = getOverlayRects('validation');
        const groupRects = getOverlayRects('group');
        overlay.innerHTML = [
            buildOverlayHtml(validationRects, {
                border: 'rgba(245, 158, 11, 0.96)',
                fill: 'rgba(245, 158, 11, 0.10)',
                shadow: 'rgba(255, 255, 255, 0.20)',
                tag: 'rgba(245, 158, 11, 0.92)',
            }, '命中'),
            buildOverlayHtml(groupRects, {
                border: 'rgba(168, 85, 247, 0.96)',
                fill: 'rgba(168, 85, 247, 0.10)',
                shadow: 'rgba(255, 255, 255, 0.20)',
                tag: 'rgba(168, 85, 247, 0.92)',
            }, '组'),
        ].join('');
    }

    function evaluateXPathNodes(doc, expr) {
        if (!doc || !expr) {
            return [];
        }
        try {
            const result = doc.evaluate(expr, doc, null, XPathResult.ORDERED_NODE_ITERATOR_TYPE, null);
            const nodes = [];
            let node = result.iterateNext();
            while (node) {
                nodes.push(node);
                node = result.iterateNext();
            }
            return nodes;
        } catch (e) {
            return [];
        }
    }

    function buildValidationResult(label, expr) {
        const selected = localState.selectedElement;
        const doc = selected ? selected.ownerDocument : document;
        const nodes = evaluateXPathNodes(doc, expr).filter((node) => node instanceof Element);
        const count = nodes.length;
        const containsSelected = !!(selected && nodes.some((node) => node === selected));
        let status = 'invalid';
        let tone = 'red';
        let message = '未命中任何元素';

        if (count === 1 && containsSelected) {
            status = 'unique';
            tone = 'green';
            message = '唯一命中当前锁定元素';
        } else if (count === 1) {
            status = 'shifted';
            tone = 'blue';
            message = '唯一命中，但目标已偏移';
        } else if (count > 1 && containsSelected) {
            status = 'multiple';
            tone = 'yellow';
            message = `命中 ${count} 个元素，包含当前目标`;
        } else if (count > 1) {
            status = 'multiple';
            tone = 'yellow';
            message = `命中 ${count} 个元素，但未稳定指向当前目标`;
        }

        return {
            label,
            xpath: expr,
            count,
            containsSelected,
            status,
            tone,
            message,
            rects: nodes.map((node) => {
                const center = getElementCenter(node);
                return {
                    left: center.topViewportLeft,
                    top: center.topViewportTop,
                    width: center.rect.width,
                    height: center.rect.height,
                };
            }),
        };
    }

    function runValidation(kind) {
        if (state.mode !== 'locked' || !state.selectedData || !localState.selectedElement) {
            return;
        }
        const expr = kind === 'absolute' ? state.selectedData.absoluteXPath : state.selectedData.relativeXPath;
        if (!expr) {
            return;
        }
        const result = buildValidationResult(kind, expr);
        state.validation[kind] = result;
        const allRects = [];
        ['relative', 'absolute'].forEach((key) => {
            const item = state.validation[key];
            if (item && item.count > 1) {
                allRects.push(...item.rects);
            }
        });
        setOverlayRects('validation', allRects);
        syncTopUI();
    }

    function getContextRoot(element) {
        if (!element) {
            return document;
        }
        const root = typeof element.getRootNode === 'function' ? element.getRootNode() : null;
        return root instanceof ShadowRoot ? root : element.ownerDocument;
    }

    function cssEscapeValue(value) {
        const text = String(value || '');
        if (window.CSS && typeof window.CSS.escape === 'function') {
            return window.CSS.escape(text);
        }
        return text.replace(/([ #;?%&,.+*~\':"!^$\[\]()=>|\/\\@])/g, '\\$1');
    }

    function getCandidateClassNames(element) {
        if (!element || typeof element.className !== 'string') {
            return [];
        }
        return element.className
            .trim()
            .split(/\s+/)
            .filter((item) => item && item.length <= 32 && !/^active|selected|focus|hover|open|close|current|checked|disabled$/.test(item))
            .slice(0, 2);
    }

    function isGenericLayoutClass(className) {
        return /^(grid|row|col|panel|page|layout|container|wrapper|shell|content|main|body|inner|outer)$/i.test(String(className || ''));
    }

    function getMeaningfulClassNames(element) {
        return getCandidateClassNames(element).filter((className) => !isGenericLayoutClass(className));
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function queryAllFromRoot(root, selector) {
        try {
            return Array.from(root.querySelectorAll(selector));
        } catch (e) {
            return [];
        }
    }

    function scoreCandidateGroup(elements, selectedElement) {
        if (!selectedElement || !Array.isArray(elements) || elements.length < 2) {
            return -1;
        }
        const directlyContains = elements.includes(selectedElement);
        const ancestorContains = !directlyContains && elements.some((item) => item.contains && item.contains(selectedElement));
        if (!directlyContains && !ancestorContains) {
            return -1;
        }
        const sameTagCount = elements.filter((item) => item.tagName === elements[0].tagName).length;
        const sameParentCount = elements.filter((item) => item.parentElement === elements[0].parentElement).length;
        const withTextCount = elements.filter((item) => normalizeText(item.textContent || '')).length;
        const penalty = elements.length > 50 ? 100 : 0;
        const sameParentBonus = sameParentCount === elements.length ? 20 : 0;
        return sameTagCount * 4 + sameParentCount * 6 + withTextCount - elements.length - penalty + sameParentBonus;
    }

    function buildGroupXPathFromParent(parent, tagName, classNames) {
        if (!(parent instanceof Element) || !tagName) {
            return '';
        }
        const parentXPath = getRelativeXPath(parent) || getAbsoluteXPath(parent);
        if (!parentXPath) {
            return '';
        }
        const nodeName = tagName.toLowerCase();
        if (!classNames.length) {
            return `${parentXPath}/${nodeName}`;
        }
        const classExpr = classNames
            .map((item) => `contains(concat(' ', normalize-space(@class), ' '), ${escapeXPathLiteral(' ' + item + ' ')})`)
            .join(' and ');
        return `${parentXPath}/${nodeName}[${classExpr}]`;
    }

    function buildGroupCssFromParent(parent, tagName, classNames) {
        if (!(parent instanceof Element) || !tagName) {
            return '';
        }
        const parentCss = getCssSelector(parent);
        if (!parentCss) {
            return '';
        }
        const nodeName = tagName.toLowerCase();
        if (!classNames.length) {
            return `${parentCss} > ${nodeName}`;
        }
        return `${parentCss} > ${nodeName}${classNames.map((item) => '.' + cssEscapeValue(item)).join('')}`;
    }

    function getAncestorChain(element) {
        const chain = [];
        let current = element instanceof Element ? element : null;
        while (current) {
            chain.unshift(current);
            current = current.parentElement;
        }
        return chain;
    }

    function getLowestCommonAncestor(elements) {
        if (!Array.isArray(elements) || !elements.length) {
            return null;
        }
        const chains = elements.map((item) => getAncestorChain(item));
        const minLength = Math.min(...chains.map((chain) => chain.length));
        let lca = null;
        for (let index = 0; index < minLength; index++) {
            const candidate = chains[0][index];
            if (chains.every((chain) => chain[index] === candidate)) {
                lca = candidate;
                continue;
            }
            break;
        }
        return lca;
    }

    function getDepthFromAncestor(ancestor, element) {
        let depth = 0;
        let current = element;
        while (current && current !== ancestor) {
            current = current.parentElement;
            depth += 1;
        }
        return current === ancestor ? depth : -1;
    }

    function getDescendantAtDepth(root, depth, predicate) {
        if (!(root instanceof Element) || depth < 0) {
            return [];
        }
        let currentLevel = [root];
        for (let step = 0; step < depth; step++) {
            currentLevel = currentLevel.flatMap((node) => Array.from(node.children || []));
            if (!currentLevel.length) {
                return [];
            }
        }
        return typeof predicate === 'function' ? currentLevel.filter(predicate) : currentLevel;
    }

    function collectCommonClassNames(elements) {
        if (!Array.isArray(elements) || !elements.length) {
            return [];
        }
        const classLists = elements.map((item) => getCandidateClassNames(item));
        return classLists[0].filter((className) => classLists.every((list) => list.includes(className))).slice(0, 2);
    }

    function buildXPathForSiblingContainers(ancestorXPath, tagName, classNames) {
        if (!ancestorXPath || !tagName) {
            return '';
        }
        const nodeName = String(tagName).toLowerCase();
        if (!classNames.length) {
            return `${ancestorXPath}/${nodeName}`;
        }
        const classExpr = classNames
            .map((item) => `contains(concat(' ', normalize-space(@class), ' '), ${escapeXPathLiteral(' ' + item + ' ')})`)
            .join(' and ');
        return `${ancestorXPath}/${nodeName}[${classExpr}]`;
    }

    function buildCssForSiblingContainers(ancestorCss, tagName, classNames) {
        if (!ancestorCss || !tagName) {
            return '';
        }
        const nodeName = String(tagName).toLowerCase();
        if (!classNames.length) {
            return `${ancestorCss} > ${nodeName}`;
        }
        return `${ancestorCss} > ${nodeName}${classNames.map((item) => '.' + cssEscapeValue(item)).join('')}`;
    }

    function buildStableGroupFromRepeatedAncestors(elements) {
        if (!Array.isArray(elements) || elements.length < 2) {
            return { xpath: '', css: '' };
        }
        const lca = getLowestCommonAncestor(elements);
        if (!(lca instanceof Element)) {
            return { xpath: '', css: '' };
        }

        const depths = elements.map((item) => getDepthFromAncestor(lca, item));
        const uniqueDepths = Array.from(new Set(depths.filter((depth) => depth >= 0)));
        if (uniqueDepths.length !== 1) {
            return { xpath: '', css: '' };
        }

        const targetDepth = uniqueDepths[0];
        for (let containerDepth = Math.max(targetDepth - 1, 0); containerDepth >= 0; containerDepth--) {
            const candidateContainers = elements.map((item) => {
                let current = item;
                let steps = targetDepth - containerDepth;
                while (current && steps > 0) {
                    current = current.parentElement;
                    steps -= 1;
                }
                return current;
            });

            if (candidateContainers.some((item) => !(item instanceof Element))) {
                continue;
            }

            const uniqueContainers = Array.from(new Set(candidateContainers));
            if (uniqueContainers.length !== elements.length) {
                continue;
            }

            const tagName = uniqueContainers[0].tagName;
            if (!uniqueContainers.every((item) => item.tagName === tagName)) {
                continue;
            }

            const classNames = collectCommonClassNames(uniqueContainers);
            const ancestorDepth = containerDepth - 1;
            const siblingContainers = getDescendantAtDepth(lca, ancestorDepth, (node) => {
                if (!(node instanceof Element)) {
                    return false;
                }
                if (node.tagName !== tagName) {
                    return false;
                }
                return classNames.every((className) => node.classList && node.classList.contains(className));
            });

            if (siblingContainers.length !== elements.length || !hasExactElementSet(siblingContainers, uniqueContainers)) {
                continue;
            }

            const lcaXPath = getRelativeXPath(lca) || getAbsoluteXPath(lca);
            const lcaCss = getCssSelector(lca);
            const xpath = buildXPathForSiblingContainers(lcaXPath, tagName, classNames);
            const css = buildCssForSiblingContainers(lcaCss, tagName, classNames);
            return { xpath, css };
        }

        return { xpath: '', css: '' };
    }

    function hasExactElementSet(nodes, elements) {
        if (!Array.isArray(nodes) || !Array.isArray(elements) || nodes.length !== elements.length) {
            return false;
        }
        return elements.every((item) => nodes.includes(item));
    }

    function normalizeXPathSegment(segment) {
        return String(segment || '').replace(/\[\d+\]$/, '');
    }

    function splitXPathSegments(path) {
        return String(path || '').split('/').filter(Boolean);
    }

    function buildXPathCandidateFromSegments(segmentLists) {
        if (!segmentLists.length) {
            return '';
        }
        const minLength = Math.min(...segmentLists.map((segments) => segments.length));
        if (!Number.isFinite(minLength) || minLength <= 0) {
            return '';
        }

        for (let diffIndex = 0; diffIndex < minLength; diffIndex++) {
            const normalized = normalizeXPathSegment(segmentLists[0][diffIndex]);
            if (!normalized) {
                continue;
            }
            const sameNodeKind = segmentLists.every((segments) => normalizeXPathSegment(segments[diffIndex]) === normalized);
            if (!sameNodeKind) {
                continue;
            }
            const prefixSame = segmentLists.every((segments) =>
                segments.slice(0, diffIndex).every((segment, idx) => segment === segmentLists[0][idx])
            );
            const suffixSame = segmentLists.every((segments) =>
                segments.slice(diffIndex + 1).every((segment, idx) => segment === segmentLists[0][diffIndex + 1 + idx])
            );
            const hasVariation = segmentLists.some((segments) => segments[diffIndex] !== segmentLists[0][diffIndex]);
            if (!prefixSame || !suffixSame || !hasVariation) {
                continue;
            }
            const candidate = [''].concat(
                segmentLists[0].slice(0, diffIndex),
                [normalized],
                segmentLists[0].slice(diffIndex + 1)
            ).join('/');
            if (candidate) {
                return candidate;
            }
        }

        const commonPrefix = [];
        for (let index = 0; index < minLength - 1; index++) {
            const segment = segmentLists[0][index];
            if (segmentLists.every((segments) => segments[index] === segment)) {
                commonPrefix.push(segment);
                continue;
            }
            break;
        }
        const nextSegments = segmentLists.map((segments) => normalizeXPathSegment(segments[commonPrefix.length] || ''));
        const uniqueNext = Array.from(new Set(nextSegments.filter(Boolean)));
        if (commonPrefix.length && uniqueNext.length === 1) {
            return [''].concat(commonPrefix, [uniqueNext[0]]).join('/');
        }
        return '';
    }

    function normalizeCssSegment(segment) {
        return String(segment || '').replace(/:nth-of-type\(\d+\)/g, '').trim();
    }

    function splitCssSegments(selector) {
        return String(selector || '').split(/\s*>\s*/).map((item) => item.trim()).filter(Boolean);
    }

    function buildCssCandidateFromSegments(segmentLists) {
        if (!segmentLists.length) {
            return '';
        }
        const minLength = Math.min(...segmentLists.map((segments) => segments.length));
        if (!Number.isFinite(minLength) || minLength <= 0) {
            return '';
        }

        for (let diffIndex = 0; diffIndex < minLength; diffIndex++) {
            const normalized = normalizeCssSegment(segmentLists[0][diffIndex]);
            if (!normalized) {
                continue;
            }
            const sameNodeKind = segmentLists.every((segments) => normalizeCssSegment(segments[diffIndex]) === normalized);
            if (!sameNodeKind) {
                continue;
            }
            const prefixSame = segmentLists.every((segments) =>
                segments.slice(0, diffIndex).every((segment, idx) => segment === segmentLists[0][idx])
            );
            const suffixSame = segmentLists.every((segments) =>
                segments.slice(diffIndex + 1).every((segment, idx) => segment === segmentLists[0][diffIndex + 1 + idx])
            );
            const hasVariation = segmentLists.some((segments) => segments[diffIndex] !== segmentLists[0][diffIndex]);
            if (!prefixSame || !suffixSame || !hasVariation) {
                continue;
            }
            return segmentLists[0].slice(0, diffIndex).concat([normalized], segmentLists[0].slice(diffIndex + 1)).join(' > ');
        }

        const commonPrefix = [];
        for (let index = 0; index < minLength - 1; index++) {
            const segment = segmentLists[0][index];
            if (segmentLists.every((segments) => segments[index] === segment)) {
                commonPrefix.push(segment);
                continue;
            }
            break;
        }
        const nextSegments = segmentLists.map((segments) => normalizeCssSegment(segments[commonPrefix.length] || ''));
        const uniqueNext = Array.from(new Set(nextSegments.filter(Boolean)));
        if (commonPrefix.length && uniqueNext.length === 1) {
            return commonPrefix.concat([uniqueNext[0]]).join(' > ');
        }
        return '';
    }

    function tryXPathCandidate(expr, elements, doc) {
        if (!expr) {
            return false;
        }
        try {
            const nodes = evaluateXPathNodes(doc, expr).filter((node) => node instanceof Element);
            return hasExactElementSet(nodes, elements);
        } catch (e) {
            return false;
        }
    }

    function tryCssCandidate(selector, elements, root) {
        if (!selector) {
            return false;
        }
        try {
            const nodes = queryAllFromRoot(root, selector).filter((node) => node instanceof Element);
            return hasExactElementSet(nodes, elements);
        } catch (e) {
            return false;
        }
    }

    function getSharedGroupAttributes(elements) {
        const sharedAttrs = ['data-group', 'data-list', 'data-type', 'role'];
        return sharedAttrs.map((attr) => {
            const values = elements.map((item) => item.getAttribute(attr)).filter(Boolean);
            const uniqueValues = Array.from(new Set(values));
            if (uniqueValues.length === 1 && values.length === elements.length) {
                return { attr, value: uniqueValues[0] };
            }
            return null;
        }).filter(Boolean);
    }

    function buildXPathCandidateResult(expr, elements, doc, strategy) {
        if (!expr) {
            return null;
        }
        const nodes = evaluateXPathNodes(doc, expr).filter((node) => node instanceof Element);
        return {
            kind: 'xpath',
            strategy,
            selector: expr,
            count: nodes.length,
            exact: hasExactElementSet(nodes, elements),
        };
    }

    function buildCssCandidateResult(selector, elements, root, strategy) {
        if (!selector) {
            return null;
        }
        const nodes = queryAllFromRoot(root, selector).filter((node) => node instanceof Element);
        return {
            kind: 'css',
            strategy,
            selector,
            count: nodes.length,
            exact: hasExactElementSet(nodes, elements),
        };
    }

    function buildCommonGroupXPathResult(elements) {
        if (!Array.isArray(elements) || elements.length < 2) {
            return { value: '', diagnostics: [] };
        }
        const doc = elements[0].ownerDocument;
        const diagnostics = [];

        const allSameParent = elements.every((item) => item.parentElement === elements[0].parentElement);
        if (allSameParent && elements[0].parentElement) {
            const sharedParent = elements[0].parentElement;
            const sharedTag = elements[0].tagName;
            const sharedClassNames = collectCommonClassNames(elements);
            const parentXPath = getRelativeXPath(sharedParent) || getAbsoluteXPath(sharedParent);

            if (parentXPath) {
                const nodeName = sharedTag.toLowerCase();

                for (const item of getSharedGroupAttributes(elements)) {
                    const candidate = `${parentXPath}/${nodeName}[@${item.attr}=${escapeXPathLiteral(item.value)}]`;
                    const result = buildXPathCandidateResult(candidate, elements, doc, `same-parent + @${item.attr}`);
                    if (result) {
                        diagnostics.push(result);
                        if (result.exact) {
                            return { value: candidate, diagnostics };
                        }
                    }
                }

                if (sharedClassNames.length) {
                    const classExpr = sharedClassNames
                        .map((item) => `contains(concat(' ', normalize-space(@class), ' '), ${escapeXPathLiteral(' ' + item + ' ')})`)
                        .join(' and ');
                    const candidate = `${parentXPath}/${nodeName}[${classExpr}]`;
                    const result = buildXPathCandidateResult(candidate, elements, doc, 'same-parent + shared class');
                    if (result) {
                        diagnostics.push(result);
                        if (result.exact) {
                            return { value: candidate, diagnostics };
                        }
                    }
                }

                const candidateTagOnly = `${parentXPath}/${nodeName}`;
                const tagOnlyResult = buildXPathCandidateResult(candidateTagOnly, elements, doc, 'same-parent + tag only');
                if (tagOnlyResult) {
                    diagnostics.push(tagOnlyResult);
                    if (tagOnlyResult.exact) {
                        return { value: candidateTagOnly, diagnostics };
                    }
                }
            }
        }

        const repeatedAncestorResult = buildStableGroupFromRepeatedAncestors(elements);
        if (repeatedAncestorResult.xpath) {
            const repeatedResult = buildXPathCandidateResult(repeatedAncestorResult.xpath, elements, doc, 'repeated ancestor');
            if (repeatedResult) {
                diagnostics.push(repeatedResult);
                if (repeatedResult.exact) {
                    return { value: repeatedAncestorResult.xpath, diagnostics };
                }
            }
        }

        const absoluteSegments = elements
            .map((item) => splitXPathSegments(getAbsoluteXPath(item)))
            .filter((segments) => segments.length);
        if (absoluteSegments.length === elements.length) {
            const aggregated = buildXPathCandidateFromSegments(absoluteSegments);
            const aggregatedResult = buildXPathCandidateResult(aggregated, elements, doc, 'aggregated absolute segments');
            if (aggregatedResult) {
                diagnostics.push(aggregatedResult);
                if (aggregatedResult.exact) {
                    return { value: aggregated, diagnostics };
                }
            }
        }

        return { value: '', diagnostics };
    }

    function buildCommonGroupCssResult(elements) {
        if (!Array.isArray(elements) || elements.length < 2) {
            return { value: '', diagnostics: [] };
        }
        const root = getContextRoot(elements[0]);
        const diagnostics = [];

        const allSameParent = elements.every((item) => item.parentElement === elements[0].parentElement);
        if (allSameParent && elements[0].parentElement) {
            const sharedParent = elements[0].parentElement;
            const sharedTag = elements[0].tagName.toLowerCase();
            const sharedClassNames = collectCommonClassNames(elements);
            const parentCss = getCssSelector(sharedParent);

            if (parentCss) {
                for (const item of getSharedGroupAttributes(elements)) {
                    const candidate = `${parentCss} > ${sharedTag}[${item.attr}="${String(item.value).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`;
                    const result = buildCssCandidateResult(candidate, elements, root, `same-parent + @${item.attr}`);
                    if (result) {
                        diagnostics.push(result);
                        if (result.exact) {
                            return { value: candidate, diagnostics };
                        }
                    }
                }

                if (sharedClassNames.length) {
                    const candidate = `${parentCss} > ${sharedTag}${sharedClassNames.map((item) => '.' + cssEscapeValue(item)).join('')}`;
                    const result = buildCssCandidateResult(candidate, elements, root, 'same-parent + shared class');
                    if (result) {
                        diagnostics.push(result);
                        if (result.exact) {
                            return { value: candidate, diagnostics };
                        }
                    }
                }

                const candidateTagOnly = `${parentCss} > ${sharedTag}`;
                const tagOnlyResult = buildCssCandidateResult(candidateTagOnly, elements, root, 'same-parent + tag only');
                if (tagOnlyResult) {
                    diagnostics.push(tagOnlyResult);
                    if (tagOnlyResult.exact) {
                        return { value: candidateTagOnly, diagnostics };
                    }
                }
            }
        }

        const repeatedAncestorResult = buildStableGroupFromRepeatedAncestors(elements);
        if (repeatedAncestorResult.css) {
            const repeatedResult = buildCssCandidateResult(repeatedAncestorResult.css, elements, root, 'repeated ancestor');
            if (repeatedResult) {
                diagnostics.push(repeatedResult);
                if (repeatedResult.exact) {
                    return { value: repeatedAncestorResult.css, diagnostics };
                }
            }
        }

        const cssSegments = elements
            .map((item) => splitCssSegments(getCssSelector(item)))
            .filter((segments) => segments.length);
        if (cssSegments.length === elements.length) {
            const aggregated = buildCssCandidateFromSegments(cssSegments);
            const aggregatedResult = buildCssCandidateResult(aggregated, elements, root, 'aggregated css segments');
            if (aggregatedResult) {
                diagnostics.push(aggregatedResult);
                if (aggregatedResult.exact) {
                    return { value: aggregated, diagnostics };
                }
            }
        }

        return { value: '', diagnostics };
    }

    function buildCommonGroupXPathFromElements(elements) {
        return buildCommonGroupXPathResult(elements).value;
    }

    function buildCommonGroupCssFromElements(elements) {
        return buildCommonGroupCssResult(elements).value;
    }

    function collectAnchorSiblingGroup(anchorElement) {
        if (!(anchorElement instanceof Element) || !(anchorElement.parentElement instanceof Element)) {
            return { elements: [], strategy: '' };
        }
        const parent = anchorElement.parentElement;
        const tagName = anchorElement.tagName;
        const sharedAttrs = ['data-group', 'data-list', 'data-type', 'role'];

        for (const attr of sharedAttrs) {
            const value = anchorElement.getAttribute(attr);
            if (!value) {
                continue;
            }
            const matches = Array.from(parent.children).filter((child) => child.tagName === tagName && child.getAttribute(attr) === value);
            if (matches.length >= 2) {
                return { elements: matches, strategy: `attr:${attr}` };
            }
        }

        const classNames = getMeaningfulClassNames(anchorElement);
        if (classNames.length) {
            const matches = Array.from(parent.children).filter((child) => {
                if (child.tagName !== tagName) {
                    return false;
                }
                return classNames.every((className) => child.classList && child.classList.contains(className));
            });
            if (matches.length >= 2) {
                return { elements: matches, strategy: 'class' };
            }
        }

        if (getCandidateClassNames(anchorElement).length) {
            return { elements: [], strategy: '' };
        }

        const tagOnlyMatches = Array.from(parent.children).filter((child) => child.tagName === tagName);
        return tagOnlyMatches.length >= 2
            ? { elements: tagOnlyMatches, strategy: 'tag' }
            : { elements: [], strategy: '' };
    }

    function collectAnchorSiblingElements(anchorElement) {
        return collectAnchorSiblingGroup(anchorElement).elements;
    }

    function getAnchorTagBonus(tagName) {
        const tag = String(tagName || '').toLowerCase();
        if (tag === 'tr') {
            return 24;
        }
        if (tag === 'li') {
            return 20;
        }
        if (tag === 'article') {
            return 18;
        }
        if (tag === 'section') {
            return 8;
        }
        if (tag === 'td' || tag === 'th') {
            return -18;
        }
        return 0;
    }

    function resolveGroupAnchorElement(selectedElement) {
        if (!(selectedElement instanceof Element)) {
            return null;
        }
        let bestAnchor = null;
        let bestScore = -1;
        let current = selectedElement;

        for (let depth = 0; current && depth <= 5; depth++, current = current.parentElement) {
            const candidate = collectAnchorSiblingGroup(current);
            const matches = candidate.elements;
            if (matches.length < 2 || !matches.includes(current)) {
                continue;
            }
            const sameParentBonus = matches.every((item) => item.parentElement === current.parentElement) ? 20 : 0;
            const attrBonus = getSharedGroupAttributes(matches).length ? 30 : 0;
            const classBonus = collectCommonClassNames(matches).filter((className) => !isGenericLayoutClass(className)).length ? 20 : 0;
            const strategyBonus = candidate.strategy.startsWith('attr:') ? 16 : candidate.strategy === 'class' ? 10 : 0;
            const tagBonus = getAnchorTagBonus(current.tagName);
            const sizePenalty = matches.length > 8 ? (matches.length - 8) * 4 : 0;
            const score = matches.length * 10 + sameParentBonus + attrBonus + classBonus + strategyBonus + tagBonus - depth * 20 - sizePenalty;
            if (score > bestScore) {
                bestScore = score;
                bestAnchor = current;
            }
        }

        return bestAnchor;
    }

    function collectGroupElementsFromAnchor(anchorElement) {
        if (!(anchorElement instanceof Element)) {
            return [];
        }
        const directMatches = collectAnchorSiblingElements(anchorElement);
        if (directMatches.length >= 2) {
            return directMatches;
        }
        return inferSimilarElements(anchorElement);
    }

    function buildGroupDiagnostics(selectedElement, anchorElement, elements, xpathResult, cssResult) {
        const diagnostics = [];
        diagnostics.push({
            kind: 'meta',
            strategy: 'selected element',
            selector: getElementName(selectedElement),
            count: 1,
            exact: true,
        });
        diagnostics.push({
            kind: 'meta',
            strategy: 'resolved anchor',
            selector: anchorElement ? getElementName(anchorElement) : '未找到组锚点',
            count: Array.isArray(elements) ? elements.length : 0,
            exact: !!anchorElement,
        });
        return diagnostics
            .concat(xpathResult && Array.isArray(xpathResult.diagnostics) ? xpathResult.diagnostics : [])
            .concat(cssResult && Array.isArray(cssResult.diagnostics) ? cssResult.diagnostics : []);
    }

    function hydrateGroupElementsFromItems(items) {
        if (!Array.isArray(items) || !items.length) {
            return [];
        }
        const elements = [];
        items.forEach((item) => {
            const relativeXPath = item && item.relativeXPath;
            const absoluteXPath = item && item.absoluteXPath;
            const expr = relativeXPath || absoluteXPath;
            if (!expr) {
                return;
            }
            const nodes = evaluateXPathNodes(document, expr).filter((node) => node instanceof Element);
            if (nodes.length === 1) {
                elements.push(nodes[0]);
            }
        });
        return Array.from(new Set(elements));
    }

    function inferSimilarElements(selectedElement) {
        if (!(selectedElement instanceof Element)) {
            return [];
        }
        const root = getContextRoot(selectedElement);
        const classNames = getCandidateClassNames(selectedElement);
        const tagName = selectedElement.tagName.toLowerCase();
        const candidates = [];

        let ancestor = selectedElement.parentElement;
        let depth = 0;
        while (ancestor && depth < 3) {
            if (ancestor instanceof Element) {
                const directTagMatches = Array.from(ancestor.children).filter((child) => child.tagName === selectedElement.tagName);
                if (directTagMatches.length >= 2) {
                    candidates.push(directTagMatches);
                }
                if (classNames.length) {
                    const selector = `${tagName}${classNames.map((item) => '.' + cssEscapeValue(item)).join('')}`;
                    const directClassMatches = Array.from(ancestor.children).filter((child) => child.matches && child.matches(selector));
                    if (directClassMatches.length >= 2) {
                        candidates.push(directClassMatches);
                    }
                }
                if (ancestor.parentElement) {
                    const parentTag = ancestor.tagName.toLowerCase();
                    const selector = `${parentTag} > ${tagName}`;
                    const nestedMatches = queryAllFromRoot(ancestor.parentElement, selector);
                    if (nestedMatches.length >= 2) {
                        candidates.push(nestedMatches);
                    }
                }

                const ancestorClassNames = getCandidateClassNames(ancestor);
                if (ancestor.parentElement) {
                    const grandparent = ancestor.parentElement;
                    const ancestorTag = ancestor.tagName;
                    const siblingContainers = Array.from(grandparent.children).filter((child) => {
                        if (child.tagName !== ancestorTag) {
                            return false;
                        }
                        return ancestorClassNames.every((cn) => child.classList && child.classList.contains(cn));
                    });
                    if (siblingContainers.length >= 2 && siblingContainers.includes(ancestor)) {
                        candidates.push(siblingContainers);
                    }
                }
            }
            ancestor = ancestor.parentElement;
            depth += 1;
        }

        if (root && root !== document && classNames.length) {
            const selector = `${tagName}${classNames.map((item) => '.' + cssEscapeValue(item)).join('')}`;
            const shadowMatches = queryAllFromRoot(root, selector);
            if (shadowMatches.length >= 2) {
                candidates.push(shadowMatches);
            }
        }

        let best = [];
        let bestScore = -1;
        candidates.forEach((items) => {
            const deduped = Array.from(new Set(items)).filter((item) => item instanceof Element);
            const score = scoreCandidateGroup(deduped, selectedElement);
            if (score > bestScore) {
                bestScore = score;
                best = deduped;
            }
        });

        if (best.length >= 2) {
            const allSameParent = best.every((item) => item.parentElement === best[0].parentElement);
            if (allSameParent) {
                return best;
            }
        }

        if (best.length >= 2) {
            const lca = getLowestCommonAncestor(best);
            if (lca instanceof Element) {
                const depths = best.map((item) => getDepthFromAncestor(lca, item));
                const uniqueDepths = Array.from(new Set(depths.filter((d) => d >= 0)));
                if (uniqueDepths.length === 1 && uniqueDepths[0] >= 1) {
                    const containerDepthFromLca = uniqueDepths[0] - 1;
                    const containerElements = best.map((item) => {
                        let current = item;
                        for (let step = 0; step < 1; step++) {
                            if (current && current.parentElement && current.parentElement !== lca) {
                                current = current.parentElement;
                            }
                        }
                        return current;
                    });

                    let containers = best;
                    for (let level = 0; level < uniqueDepths[0] - 1; level++) {
                        const parents = containers.map((item) => item.parentElement).filter((item) => item instanceof Element);
                        const uniqueParents = Array.from(new Set(parents));
                        if (uniqueParents.length === containers.length) {
                            const parentsSameParent = uniqueParents.every((item) => item.parentElement === uniqueParents[0].parentElement);
                            if (parentsSameParent) {
                                containers = uniqueParents;
                                continue;
                            }
                        }
                        break;
                    }

                    const containersSameParent = containers.every((item) => item.parentElement === containers[0].parentElement);
                    if (containersSameParent && containers.length === best.length) {
                        const containerTag = containers[0].tagName;
                        const allSameTag = containers.every((item) => item.tagName === containerTag);
                        if (allSameTag) {
                            return containers;
                        }
                    }
                }
            }
        }

        return best;
    }

    function updateGroupStateFromElements(elements, selectedElement, anchorElement, statusText) {
        const safeItems = Array.from(new Set(elements)).filter((item) => item instanceof Element);
        const payloads = safeItems.map((item) => collectElementData(item));
        const xpathResult = buildCommonGroupXPathResult(safeItems);
        const cssResult = buildCommonGroupCssResult(safeItems);
        state.group = {
            sourceKey: getElementKey(state.selectedData),
            items: payloads,
            commonXPath: xpathResult.value,
            commonCss: cssResult.value,
            anchorName: anchorElement ? getElementName(anchorElement) : '',
            anchorTag: anchorElement && anchorElement.tagName ? anchorElement.tagName.toLowerCase() : '',
            diagnostics: buildGroupDiagnostics(selectedElement, anchorElement, safeItems, xpathResult, cssResult),
            statusText: statusText || '',
        };
        setOverlayRects('group', payloads.map((item) => ({
            left: item.topViewportLeft,
            top: item.topViewportTop,
            width: item.width,
            height: item.height,
        })));
    }

    function captureSimilarGroup() {
        if (state.mode !== 'locked' || !localState.selectedElement || !state.selectedData) {
            return;
        }
        const anchorElement = resolveGroupAnchorElement(localState.selectedElement);
        const matches = collectGroupElementsFromAnchor(anchorElement || localState.selectedElement);
        if (matches.length < 2) {
            clearGroupState();
            state.group.sourceKey = getElementKey(state.selectedData);
            state.group.anchorName = anchorElement ? getElementName(anchorElement) : '';
            state.group.anchorTag = anchorElement && anchorElement.tagName ? anchorElement.tagName.toLowerCase() : '';
            state.group.diagnostics = buildGroupDiagnostics(localState.selectedElement, anchorElement, [], { diagnostics: [] }, { diagnostics: [] });
            state.group.statusText = anchorElement ? '已找到组锚点，但未推断到稳定的相似元素组' : '未找到稳定的组锚点，无法生成元素组';
            syncTopUI();
            return;
        }
        updateGroupStateFromElements(matches, localState.selectedElement, anchorElement || matches[0], `已在当前上下文中捕获 ${matches.length} 个相似元素`);
        state.activeTab = 'group';
        syncTopUI();
    }

    function removeGroupItem(index) {
        const current = state.group.items || [];
        if (index < 0 || index >= current.length) {
            return;
        }
        const next = current.filter((_, idx) => idx !== index);
        state.group.items = next;
        const hydrated = hydrateGroupElementsFromItems(next);
        if (hydrated.length >= 2) {
            const xpathResult = buildCommonGroupXPathResult(hydrated);
            const cssResult = buildCommonGroupCssResult(hydrated);
            state.group.commonXPath = xpathResult.value;
            state.group.commonCss = cssResult.value;
            state.group.diagnostics = buildGroupDiagnostics(
                localState.selectedElement,
                hydrated[0],
                hydrated,
                xpathResult,
                cssResult,
            ).concat({
                kind: 'meta',
                strategy: 'manual removal',
                selector: `removed index ${index + 1}`,
                count: next.length,
                exact: true,
            });
        } else {
            state.group.commonXPath = '';
            state.group.commonCss = '';
            state.group.diagnostics = (state.group.diagnostics || []).concat({
                kind: 'meta',
                strategy: 'manual removal',
                selector: `removed index ${index + 1}`,
                count: next.length,
                exact: false,
            });
        }
        state.group.statusText = next.length >= 2
            ? `已移除 1 项，当前组剩余 ${next.length} 个元素`
            : '当前元素组不足 2 个元素，已无法生成稳定组 XPath';
        setOverlayRects('group', next.map((item) => ({
            left: item.topViewportLeft,
            top: item.topViewportTop,
            width: item.width,
            height: item.height,
        })));
        syncTopUI();
    }

    function clearGroupCapture() {
        clearGroupState();
        syncTopUI();
    }

    function buildGroupCode(group) {
        const items = group && Array.isArray(group.items) ? group.items : [];
        if (!items.length) {
            return '# 先锁定一个元素，再点击“捕获相似元素”';
        }
        if (!group.commonXPath) {
            if (group.commonCss) {
                return [
                    '# 已捕获相似元素，当前改用 CSS 组定位',
                    `items = page.eles(${quotePy(group.commonCss)})`,
                    'for item in items:',
                    '    print(item.text)',
                ].join('\n');
            }
            return `# 已捕获 ${items.length} 个相似元素，但未生成稳定组 XPath / CSS`; 
        }
        const framePath = items[0].framePath || [];
        const shadowPath = items[0].shadowPath || [];
        const lines = [];
        let currentVar = 'page';
        lines.push('# ruyiPage generated group snippet');
        framePath.forEach((frameName, index) => {
            const frameVar = `frame${index + 1}`;
            const selector = frameName && frameName !== 'iframe' ? `#${frameName}` : `index=${index}`;
            if (selector.startsWith('#')) {
                lines.push(`${frameVar} = ${currentVar}.get_frame(${quotePy(selector)})`);
            } else {
                lines.push(`${frameVar} = ${currentVar}.get_frame(${selector})`);
            }
            currentVar = frameVar;
        });
        shadowPath.forEach((shadow, index) => {
            const hostVar = `shadow_host${index + 1}`;
            const rootVar = `shadow_root${index + 1}`;
            lines.push(`${hostVar} = ${currentVar}.ele(${quotePy(shadow.selector || '')})`);
            lines.push(`${rootVar} = ${hostVar}.${shadow.mode === 'closed' ? 'closed_shadow_root' : 'shadow_root'}`);
            currentVar = rootVar;
        });
        lines.push(`items = ${currentVar}.eles(${quotePy('xpath:' + group.commonXPath)})`);
        lines.push('for item in items:');
        lines.push('    print(item.text)');
        return lines.join('\n');
    }

    function renderGroupTab() {
        const items = state.group.items || [];
        const diagnostics = state.group.diagnostics || [];
        const code = buildGroupCode(state.group);
        const safeCode = escapeHtml(code);
        const comparisonRows = items.map((item, index) => `
            <div class="ruyi-xpath-picker__compare-item">
                <div class="ruyi-xpath-picker__compare-title">${index + 1}. ${escapeHtml(item.name || item.tag || 'element')}</div>
                <div class="ruyi-xpath-picker__hint">Relative XPath</div>
                <div class="ruyi-xpath-picker__value" data-code="true">${escapeHtml(item.relativeXPath || '-')}</div>
                <div class="ruyi-xpath-picker__hint">Absolute XPath</div>
                <div class="ruyi-xpath-picker__value" data-code="true">${escapeHtml(item.absoluteXPath || '-')}</div>
                <div class="ruyi-xpath-picker__hint">CSS Selector</div>
                <div class="ruyi-xpath-picker__value" data-code="true">${escapeHtml(item.cssSelector || '-')}</div>
            </div>
        `).join('');
        const diagnosticRows = diagnostics.map((item) => `
            <div class="ruyi-xpath-picker__compare-item">
                <div class="ruyi-xpath-picker__compare-title">${escapeHtml(item.kind === 'meta' ? item.strategy : `${item.kind.toUpperCase()} / ${item.strategy}`)}</div>
                <div class="ruyi-xpath-picker__hint">${item.exact ? 'exact match' : 'not exact'}</div>
                <div class="ruyi-xpath-picker__value" data-code="true">${escapeHtml(item.selector || '-')}</div>
                <div class="ruyi-xpath-picker__hint">命中 ${Number.isFinite(item.count) ? item.count : 0} 个元素</div>
            </div>
        `).join('');
        return `
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">Summary</span>
                    <span class="ruyi-xpath-picker__status-chip" data-tone="${items.length >= 2 ? 'green' : 'blue'}">${items.length} items</span>
                </div>
                <div class="ruyi-xpath-picker__value">${escapeHtml(state.group.statusText || (items.length ? '已生成元素组结果' : '先锁定一个元素，再点击“捕获相似元素”'))}</div>
                <div class="ruyi-xpath-picker__hint">当前元素组仅在当前 document / frame / shadow 上下文内推断，并会结合每个元素已有的 XPath/CSS 做归纳收敛。</div>
            </section>
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">Group Anchor</span>
                </div>
                <div class="ruyi-xpath-picker__value">${escapeHtml(state.group.anchorName || '未解析到组锚点')}</div>
                <div class="ruyi-xpath-picker__hint">最终组定位基于锚点收敛到的组元素，而不是原始点击的子节点。</div>
            </section>
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">Common XPath</span>
                    ${state.group.commonXPath ? `<button type="button" class="ruyi-xpath-picker__copy" data-copy-label="复制组XPath" data-copy-value="${escapeHtml(String(state.group.commonXPath)).replace(/"/g, '&quot;')}">复制组XPath</button>` : ''}
                </div>
                <div class="ruyi-xpath-picker__value" data-code="true">${state.group.commonXPath ? escapeHtml(state.group.commonXPath) : '未生成稳定组 XPath'}</div>
            </section>
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">Common CSS</span>
                    ${state.group.commonCss ? `<button type="button" class="ruyi-xpath-picker__copy" data-copy-label="复制组CSS" data-copy-value="${escapeHtml(String(state.group.commonCss)).replace(/"/g, '&quot;')}">复制组CSS</button>` : ''}
                </div>
                <div class="ruyi-xpath-picker__value" data-code="true">${state.group.commonCss ? escapeHtml(state.group.commonCss) : '未生成稳定组 CSS'}</div>
            </section>
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">Batch Code</span>
                    <button type="button" class="ruyi-xpath-picker__copy" data-copy-label="复制代码" data-copy-value="${escapeHtml(code).replace(/"/g, '&quot;')}">复制代码</button>
                </div>
                <div class="ruyi-xpath-picker__code-block">${safeCode}</div>
            </section>
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">Diagnostics</span>
                </div>
                <div class="ruyi-xpath-picker__compare-list">
                    ${diagnosticRows || '<div class="ruyi-xpath-picker__hint">当前没有可展示的诊断信息。</div>'}
                </div>
            </section>
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">逐项定位对照</span>
                </div>
                <div class="ruyi-xpath-picker__compare-list">
                    ${comparisonRows || '<div class="ruyi-xpath-picker__hint">当前没有可对照的组元素。</div>'}
                </div>
            </section>
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">Group Items</span>
                    ${items.length ? `<button type="button" class="ruyi-xpath-picker__copy" data-clear-group="true">清除组</button>` : ''}
                </div>
                <div class="ruyi-xpath-picker__group-list">
                    ${items.length ? items.map((item, index) => `
                        <div class="ruyi-xpath-picker__group-item">
                            <div class="ruyi-xpath-picker__group-item-main">
                                <div class="ruyi-xpath-picker__group-item-title">${index + 1}. ${escapeHtml(item.name || item.tag || 'element')}</div>
                                <div class="ruyi-xpath-picker__group-item-sub">${escapeHtml(item.text || item.relativeXPath || item.absoluteXPath || '-')}</div>
                            </div>
                            <button type="button" class="ruyi-xpath-picker__remove" data-remove-group-index="${index}">移除</button>
                        </div>
                    `).join('') : '<div class="ruyi-xpath-picker__hint">当前没有元素组结果。</div>'}
                </div>
            </section>
        `;
    }

    function getHostSelector(host) {
        if (!host) {
            return '';
        }
        if (host.id) {
            return `#${host.id}`;
        }
        return getRelativeXPath(host);
    }

    function getStatusText() {
        if (state.mode === 'locked') {
            return '已锁定';
        }
        if (state.mode === 'paused') {
            return '已暂停';
        }
        return '待选择';
    }

    function quotePy(value) {
        return JSON.stringify(String(value || ''));
    }

    function copyText(text, button) {
        const value = String(text || '');
        const markCopied = () => {
            if (!button) {
                return;
            }
            button.setAttribute('data-copied', 'true');
            const original = button.getAttribute('data-copy-label') || '复制';
            button.textContent = '已复制';
            topWindowRef.setTimeout(() => {
                button.removeAttribute('data-copied');
                button.textContent = original;
            }, 1200);
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(value).then(markCopied).catch(() => {
                const textarea = document.createElement('textarea');
                textarea.value = value;
                document.body.appendChild(textarea);
                textarea.select();
                try {
                    document.execCommand('copy');
                    markCopied();
                } catch (e) {
                }
                textarea.remove();
            });
            return;
        }

        const textarea = document.createElement('textarea');
        textarea.value = value;
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy');
            markCopied();
        } catch (e) {
        }
        textarea.remove();
    }

    function buildRuyiPageCode(data) {
        if (!data) {
            return '# 先点击一个元素，ruyiPage 代码会显示在这里';
        }

        const lines = [];
        let currentVar = 'page';

        lines.push('# ruyiPage generated snippet');
        lines.push('# page 已经是 FirefoxPage / FirefoxTab 实例');

        (data.framePath || []).forEach((frameName, index) => {
            const frameVar = `frame${index + 1}`;
            const selector = frameName && frameName !== 'iframe' ? `#${frameName}` : `index=${index}`;
            if (selector.startsWith('#')) {
                lines.push(`${frameVar} = ${currentVar}.get_frame(${quotePy(selector)})`);
            } else {
                lines.push(`${frameVar} = ${currentVar}.get_frame(${selector})`);
            }
            currentVar = frameVar;
        });

        const shadowPath = data.shadowPath || [];
        shadowPath.forEach((shadow, index) => {
            const hostVar = `shadow_host${index + 1}`;
            const rootVar = `shadow_root${index + 1}`;
            const hostSelector = shadow.selector || '';
            lines.push(`${hostVar} = ${currentVar}.ele(${quotePy(hostSelector)})`);
            lines.push(`${rootVar} = ${hostVar}.${shadow.mode === 'closed' ? 'closed_shadow_root' : 'shadow_root'}`);
            currentVar = rootVar;
        });

        const primarySelector = data.relativeXPath || data.absoluteXPath || '';
        if (primarySelector) {
            lines.push(`target = ${currentVar}.ele(${quotePy('xpath:' + primarySelector)})`);
            if (data.cssSelector) {
                lines.push(`# CSS 备选: ${data.cssSelector}`);
            }
        } else {
            lines.push(`# 无法生成 XPath，建议手动补充 selector`);
            lines.push(`target = ${currentVar}.ele(${quotePy(data.cssSelector || data.name || data.tag || '')})`);
        }

        if (data.shadowPath && data.shadowPath.length) {
            lines.push('');
            lines.push('# 如果页面暴露了 closed shadow 调试桥，也可以改成 with_shadow() 形式：');
            lines.push('# with shadow_host1.with_shadow("open") as root:');
            lines.push('#     target = root.ele("xpath:...")');
        }

        if (String(data.context || '').includes('shadow(') && (!data.shadowPath || !data.shadowPath.length)) {
            lines.push('');
            lines.push('# 注意：当前命中元素位于 shadow 场景，但未能还原 host 链。');
            lines.push('# closed shadow 需要页面提供 __ruyiGetClosedShadowRoot 调试桥后，才能稳定生成访问代码。');
        }

        return lines.join('\n');
    }

    function syncTopUI() {
        if (isTopWindow) {
            renderFields();
            updateTopHighlightFromData(getDisplayData());
            return;
        }
        try {
            if (topWindowRef && typeof topWindowRef.__ruyiInitXPathPicker === 'function') {
                topWindowRef.__ruyiInitXPathPicker();
            }
        } catch (e) {
        }
    }

    function renderFields() {
        if (!isTopWindow) {
            return;
        }
        const panel = ensurePanel();
        const meta = panel.querySelector('[data-role="meta"]');
        const intro = panel.querySelector('[data-role="intro"]');
        const status = panel.querySelector('[data-role="status"]');
        const unlockButton = panel.querySelector('[data-action="unlock"]');
        const pauseButton = panel.querySelector('[data-action="pause"]');
        const captureGroupButton = panel.querySelector('[data-action="capture-group"]');
        const toggleButtons = panel.querySelectorAll('[data-action="toggle"]');
        const tabs = panel.querySelectorAll('[data-tab]');
        panel.setAttribute('data-collapsed', state.collapsed ? 'true' : 'false');

        toggleButtons.forEach((button) => {
            const isIcon = button.classList.contains('ruyi-xpath-picker__button--icon');
            button.textContent = state.collapsed ? (isIcon ? '+' : '展开') : (isIcon ? '-' : '收起');
            button.setAttribute('aria-label', state.collapsed ? '展开 XPath Picker' : '收起 XPath Picker');
        });
        tabs.forEach((button) => {
            button.setAttribute('data-active', button.getAttribute('data-tab') === state.activeTab ? 'true' : 'false');
        });

        const data = getDisplayData();
        status.textContent = getStatusText();
        unlockButton.disabled = state.mode !== 'locked';
        pauseButton.textContent = state.mode === 'paused' ? '恢复选择' : '暂停选择';
        if (captureGroupButton) {
            captureGroupButton.disabled = state.mode !== 'locked';
        }

        if (!data) {
            intro.textContent = state.mode === 'paused'
                ? '当前已暂停选择，点击“恢复选择”后可继续检查页面元素。'
                : '移动鼠标可预览目标，点击页面元素后会锁定当前结果。';
            meta.innerHTML = '';
            syncAuxOverlay();
            return;
        }

        intro.textContent = state.mode === 'locked'
            ? '当前结果已锁定，点击“继续选择”后可重新选择其他元素。'
            : state.mode === 'paused'
                ? '当前已暂停选择，保留最近一次锁定结果。'
                : '当前为预览态，点击元素后会锁定此结果。';

        if (state.activeTab === 'group') {
            meta.innerHTML = renderGroupTab();
            syncAuxOverlay();
            return;
        }

        const validationMap = {
            'XPath (absolute)': state.validation.absolute,
            'XPath (relative)': state.validation.relative,
        };
        const rawCode = buildRuyiPageCode(data);
        const code = rawCode
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
        const fields = [
            ['Tag', data.tag || '-'],
            ['Name', data.name || '-'],
            ['Text', data.text || '-'],
            ['XPath (absolute)', data.absoluteXPath || '-', true, !!data.absoluteXPath, '复制绝对XPath'],
            ['XPath (relative)', data.relativeXPath || '-', true, !!data.relativeXPath, '复制相对XPath'],
            ['CSS', data.cssSelector || '-', true, !!data.cssSelector, '复制CSS'],
            ['Center', `(${data.centerX}, ${data.centerY})`],
            ['Context', data.context || '-'],
        ];

        meta.innerHTML = fields.map(([label, value, isCode, canCopy, copyLabel]) => `
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">${label}</span>
                    <span class="ruyi-xpath-picker__inline-actions">
                        ${canCopy ? `<button type="button" class="ruyi-xpath-picker__copy" data-copy-label="${copyLabel}" data-copy-value="${String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')}">${copyLabel}</button>` : ''}
                        ${state.mode === 'locked' && (label === 'XPath (absolute)' || label === 'XPath (relative)') && value && value !== '-' ? `<button type="button" class="ruyi-xpath-picker__copy" data-validate-kind="${label === 'XPath (absolute)' ? 'absolute' : 'relative'}">校验</button>` : ''}
                    </span>
                </div>
                <div class="ruyi-xpath-picker__value"${isCode ? ' data-code="true"' : ''}>${String(value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')}</div>
                ${validationMap[label] ? `<div class="ruyi-xpath-picker__status-row"><span class="ruyi-xpath-picker__status-chip" data-tone="${validationMap[label].tone}">${validationMap[label].status}</span><span class="ruyi-xpath-picker__hint">${String(validationMap[label].message || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span></div>` : ''}
            </section>
        `).join('') + `
            <section class="ruyi-xpath-picker__field">
                <div class="ruyi-xpath-picker__field-header">
                    <span class="ruyi-xpath-picker__label">ruyiPage代码生成</span>
                    <button type="button" class="ruyi-xpath-picker__copy" data-copy-label="复制代码" data-copy-value="${rawCode.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')}">复制代码</button>
                </div>
                <div class="ruyi-xpath-picker__code-block">${code}</div>
            </section>
        `;
        syncAuxOverlay();
    }

    function unlockSelection() {
        state.mode = 'idle';
        state.selectedData = null;
        state.hoverData = null;
        clearValidationState();
        clearGroupState();
        localState.selectedElement = null;
        localState.hoverElement = null;
        if (localState.highlight) {
            localState.highlight.style.display = 'none';
        }
        syncTopUI();
    }

    function toggleCollapsed(forceValue) {
        state.collapsed = typeof forceValue === 'boolean' ? forceValue : !state.collapsed;
        syncTopUI();
    }

    function togglePaused() {
        state.mode = state.mode === 'paused' ? 'idle' : 'paused';
        if (state.mode === 'idle') {
            state.hoverData = null;
            localState.hoverElement = null;
            updateHighlight(null);
        } else if (localState.selectedElement && document.documentElement.contains(localState.selectedElement)) {
            updateHighlight(localState.selectedElement);
        } else {
            updateHighlight(null);
        }
        syncTopUI();
    }

    function isPickerNode(node) {
        return !!(node && node.closest && node.closest(`#${PANEL_ID}, #${HIGHLIGHT_ID}`));
    }

    function handleMove(event) {
        const target = getEventElement(event);
        if (state.mode !== 'idle' || isPickerNode(target)) {
            return;
        }
        if (!(target instanceof Element)) {
            return;
        }
        localState.hoverElement = target;
        state.hoverData = collectElementData(target);
        updateHighlight(target);
        syncTopUI();
    }

    function handleClick(event) {
        const target = getEventElement(event);
        if (state.mode !== 'idle' || isPickerNode(target)) {
            return;
        }
        if (!(target instanceof Element)) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        if (typeof event.stopImmediatePropagation === 'function') {
            event.stopImmediatePropagation();
        }
        clearValidationState();
        clearGroupState();
        localState.selectedElement = target;
        state.selectedData = collectElementData(target);
        state.mode = 'locked';
        updateHighlight(target);
        syncTopUI();
    }

    function handleViewportChange() {
        syncAuxOverlay();
        if (state.mode === 'locked' && localState.selectedElement && document.documentElement.contains(localState.selectedElement)) {
            updateHighlight(localState.selectedElement);
            syncTopUI();
            return;
        }
        if (state.mode === 'paused' && localState.selectedElement && document.documentElement.contains(localState.selectedElement)) {
            updateHighlight(localState.selectedElement);
            syncTopUI();
            return;
        }
        if (state.mode === 'idle' && localState.hoverElement && document.documentElement.contains(localState.hoverElement)) {
            updateHighlight(localState.hoverElement);
            syncTopUI();
            return;
        }
        if (state.mode !== 'locked') {
            updateHighlight(null);
            syncTopUI();
        }
    }

    function bindEvents() {
        if (localState.handlersBound && localState.boundDocument === document) {
            return;
        }

        if (localState.handlersBound && localState.boundDocument && localState.moveHandler) {
            try {
                localState.boundDocument.removeEventListener('mousemove', localState.moveHandler, true);
                localState.boundDocument.removeEventListener('click', localState.clickHandler, true);
                window.removeEventListener('scroll', localState.scrollHandler, true);
                window.removeEventListener('resize', localState.resizeHandler, true);
            } catch (e) {
            }
            localState.handlersBound = false;
        }

        localState.moveHandler = handleMove;
        localState.clickHandler = handleClick;
        localState.scrollHandler = handleViewportChange;
        localState.resizeHandler = handleViewportChange;
        document.addEventListener('mousemove', localState.moveHandler, true);
        document.addEventListener('click', localState.clickHandler, true);
        window.addEventListener('scroll', localState.scrollHandler, true);
        window.addEventListener('resize', localState.resizeHandler, true);
        localState.handlersBound = true;
        localState.boundDocument = document;
    }

    function bindWatchdog() {
        if (!isTopWindow || state.watchdogBound) {
            return;
        }

        const restoreUI = () => {
            try {
                ensureStyles();
                ensurePanel();
                ensureHighlight();
                syncTopUI();
                if (typeof topWindowRef.__ruyiXPathPickerInjectIntoFrames === 'function') {
                    topWindowRef.__ruyiXPathPickerInjectIntoFrames();
                }
            } catch (e) {
            }
        };

        window.addEventListener('pageshow', restoreUI, true);
        window.addEventListener('load', restoreUI, true);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                restoreUI();
            }
        }, true);

        const observer = new MutationObserver(() => {
            const panelExists = !!document.getElementById(PANEL_ID);
            const highlightExists = !!document.getElementById(HIGHLIGHT_ID);
            const styleExists = !!document.getElementById('__ruyi_xpath_picker_style__');
            if (!panelExists || !highlightExists || !styleExists) {
                restoreUI();
            }
        });
        observer.observe(document.documentElement || document, {
            childList: true,
            subtree: true,
        });

        window.setInterval(() => {
            const panelExists = !!document.getElementById(PANEL_ID);
            const highlightExists = !!document.getElementById(HIGHLIGHT_ID);
            const styleExists = !!document.getElementById('__ruyi_xpath_picker_style__');
            if (!panelExists || !highlightExists || !styleExists) {
                restoreUI();
            }
        }, 1200);

        state.watchdogBound = true;
    }

    function init() {
        if (!['idle', 'locked', 'paused'].includes(state.mode)) {
            state.mode = 'idle';
        }
        ensureStyles();
        if (isTopWindow) {
            ensurePanel();
        }
        ensureHighlight();
        bindEvents();
        bindWatchdog();
        syncTopUI();
    }

    window.__ruyiInitXPathPicker = init;
    topWindowRef.__ruyiInitXPathPicker = topWindowRef.__ruyiInitXPathPicker || init;
    init();
}
"""

    # ===== __call__ 快捷方式 =====

    def __call__(
        self, locator, index=1, timeout=None
    ) -> "FirefoxElement | NoneElement":
        """快捷查找元素: page('locator') 等价于 page.ele('locator')

        Args:
            locator: 定位器（字符串或元组）
            index: 第几个（从1开始，负数从后数）
            timeout: 超时时间（秒）

        Returns:
            FirefoxElement 或 NoneElement
        """
        return self.ele(locator, index=index, timeout=timeout)

    # ===== 属性 =====

    @property
    def browser(self) -> "Firefox":
        """Firefox 浏览器实例"""
        return self._browser

    @property
    def tab_id(self) -> str:
        """当前 browsingContext ID"""
        return self._context_id

    @property
    def title(self) -> str:
        """页面标题"""
        return self.run_js("document.title") or ""

    @property
    def url(self) -> str:
        """当前 URL"""
        return self.run_js("location.href") or ""

    @property
    def html(self) -> str:
        """页面完整 HTML"""
        return self.run_js("document.documentElement.outerHTML") or ""

    @property
    def user_agent(self) -> str:
        """User-Agent 字符串"""
        return self.run_js("navigator.userAgent") or ""

    @property
    def ready_state(self) -> str:
        """页面加载状态: 'loading' / 'interactive' / 'complete'"""
        return self.run_js("document.readyState") or ""

    @property
    def cookies(self) -> list:
        """当前页面 Cookie（简单格式）"""
        return self.get_cookies()

    @property
    def scroll(self) -> "PageScroller":
        """滚动管理器"""
        if self._scroll is None:
            from .._units.scroller import PageScroller

            self._scroll = PageScroller(self)
        return self._scroll

    @property
    def actions(self) -> "Actions":
        """动作链管理器"""
        if self._actions is None:
            from .._units.actions import Actions

            self._actions = Actions(self)
        return self._actions

    @property
    def touch(self) -> "TouchActions":
        """BiDi 触摸动作链管理器"""
        if self._touch is None:
            from .._units.touch_actions import TouchActions

            self._touch = TouchActions(self)
        return self._touch

    @property
    def prefs(self) -> "PrefsManager":
        """about:config 运行时读写"""
        if self._prefs is None:
            from .._units.prefs import PrefsManager

            self._prefs = PrefsManager(self)
        return self._prefs

    @property
    def realms(self) -> "RealmTracker":
        """realm 生命周期追踪"""
        if self._realms is None:
            from .._units.realm_tracker import RealmTracker

            self._realms = RealmTracker(self)
        return self._realms

    @property
    def config(self) -> "ConfigManager":
        """about:config 完整控制系统（user.js / prefs.js / policies.json）"""
        if self._config is None:
            from .._units.config_manager import ConfigManager

            browser = self._browser
            profile = getattr(browser, "_auto_profile", None) or (
                browser.options.profile_path if hasattr(browser, "options") else None
            )
            self._config = ConfigManager(profile_path=profile)
        return self._config

    @property
    def wait(self) -> "PageWaiter":
        """等待条件管理器"""
        if self._wait is None:
            from .._units.waiter import PageWaiter

            self._wait = PageWaiter(self)
        return self._wait

    @property
    def listen(self) -> "Listener":
        """网络监听管理器"""
        if self._listener is None:
            from .._units.listener import Listener

            self._listener = Listener(self)
        return self._listener

    @property
    def rect(self) -> "TabRect":
        """页面位置/尺寸"""
        if self._rect is None:
            from .._units.rect import TabRect

            self._rect = TabRect(self)
        return self._rect

    @property
    def states(self) -> "PageStates":
        """页面状态查询"""
        if self._states is None:
            from .._units.states import PageStates

            self._states = PageStates(self)
        return self._states

    @property
    def set(self) -> "PageSetter":
        """属性设置器"""
        if self._setter is None:
            from .._units.setter import PageSetter

            self._setter = PageSetter(self)
        return self._setter

    @property
    def local_storage(self) -> "StorageManager":
        """localStorage 管理器"""
        if self._local_storage is None:
            from .._units.storage import StorageManager

            self._local_storage = StorageManager(self, "localStorage")
        return self._local_storage

    @property
    def session_storage(self) -> "StorageManager":
        """sessionStorage 管理器"""
        if self._session_storage is None:
            from .._units.storage import StorageManager

            self._session_storage = StorageManager(self, "sessionStorage")
        return self._session_storage

    @property
    def console(self) -> "ConsoleListener":
        """控制台日志监听器"""
        if self._console is None:
            from .._units.console_listener import ConsoleListener

            self._console = ConsoleListener(self)
        return self._console

    @property
    def intercept(self) -> "Interceptor":
        """网络请求拦截器"""
        if self._interceptor is None:
            from .._units.interceptor import Interceptor

            self._interceptor = Interceptor(self)
        return self._interceptor

    @property
    def network(self) -> "NetworkManager":
        """network 模块高层管理器。

        Returns:
            NetworkManager: 提供额外请求头、缓存行为、data collector 的高层入口。

        适用场景：
            - 设置额外请求头
            - 设置缓存行为
            - 创建和管理 network data collector

        说明：
            - 优先用 ``page.network``，而不是直接写
              ``network.xxx(page._driver, ...)``。
        """
        if self._network_manager is None:
            from .._units.network_tools import NetworkManager

            self._network_manager = NetworkManager(self)
        return self._network_manager

    @property
    def window(self) -> "WindowManager":
        """当前页面对应窗口的常用操作管理器。

        Returns:
            WindowManager: 面向当前页面窗口的高层操作对象。

        适用场景：
            - 想直接最大化、最小化、全屏当前窗口
            - 想设置当前窗口尺寸和位置

        与 ``browser_tools`` 的区别：
            - ``window`` 更偏“当前窗口怎么操作”
            - ``browser_tools`` 更偏“浏览器级有哪些窗口/用户上下文可以管理”
        """
        if self._window is None:
            from .._units.window import WindowManager

            self._window = WindowManager(self)
        return self._window

    @property
    def browser_tools(self) -> "BrowserManager":
        """浏览器级能力管理器。

        Returns:
            BrowserManager: 提供 user context 和 client window 管理能力。

        适用场景：
            - 创建、查询、删除 user context
            - 在指定 user context 中创建 tab
            - 枚举所有 client window 并切换窗口状态

        说明：
            - 这是 browser 模块的高层入口。
            - 当你需要的是“浏览器级资源管理”，优先用它，而不是直接调用
              ``_bidi.browser_module``。
        """
        if self._browser_manager is None:
            from .._units.browser import BrowserManager

            self._browser_manager = BrowserManager(self)
        return self._browser_manager

    @property
    def contexts(self) -> "ContextManager":
        """浏览上下文高层管理器。

        Returns:
            ContextManager: 提供 browsingContext 模块常用能力的简洁入口。

        适用场景：
            - 获取 context 树
            - 创建/关闭 tab 或 window
            - 对当前页面执行 reload、setViewport、setBypassCSP 等操作

        说明：
            - 这是面向使用者的 browsingContext 入口。
            - 优先用 ``page.contexts``，而不是直接写
              ``browsing_context.xxx(page._driver, ...)``。
        """
        if self._contexts is None:
            from .._units.contexts import ContextManager

            self._contexts = ContextManager(self)
        return self._contexts

    @property
    def emulation(self) -> "EmulationManager":
        """设备模拟管理器"""
        if self._emulation is None:
            from .._units.emulation import EmulationManager

            self._emulation = EmulationManager(self)
        return self._emulation

    @property
    def extensions(self) -> "ExtensionManager":
        """WebExtension 管理器。"""
        if self._extensions is None:
            from .._units.extensions import ExtensionManager

            self._extensions = ExtensionManager(self._driver)
        return self._extensions

    @property
    def downloads(self) -> "DownloadsManager":
        """下载管理器。

        Returns:
            DownloadsManager: 下载行为设置、下载事件等待、文件落盘检查的统一入口。

        适用场景：
            - 设置下载目录或 allow / deny 行为
            - 等待 ``downloadWillBegin`` / ``downloadEnd``
            - 验证文件是否真实落盘
        """
        if self._downloads is None:
            from .._units.downloads import DownloadsManager

            self._downloads = DownloadsManager(self)
        return self._downloads

    @property
    def events(self) -> "EventTracker":
        """通用 BiDi 事件跟踪器。

        Returns:
            EventTracker: 用于统一监听和等待各模块事件。

        适用场景：
            - 验证 ``browsingContext.contextCreated`` / ``userPromptOpened`` 等标准事件
            - 不想直接调用底层 ``session.subscribe`` 时
        """
        if self._events is None:
            from .._units.events import EventTracker

            self._events = EventTracker(self)
        return self._events

    @property
    def navigation(self) -> "NavigationTracker":
        """导航事件跟踪器。

        Returns:
            NavigationTracker: 用于订阅、记录、等待导航相关 BiDi 事件。

        适用场景：
            - 验证 ``navigationStarted`` / ``load`` / ``historyUpdated`` 等标准事件
            - 调试某次导航到底触发了哪些 BiDi 事件

        说明：
            - 这是“导航事件监听器”，不是导航命令本身。
            - 真正执行跳转仍然用 ``page.get()``、``page.back()``、``page.forward()``。
            - 当你想知道“跳转过程中浏览器发了什么事件”，再使用 ``page.navigation``。
        """
        if self._navigation is None:
            from .._units.navigation import NavigationTracker

            self._navigation = NavigationTracker(self)
        return self._navigation

    # ===== 导航 =====

    def get(self, url, wait=None, timeout=None) -> "FirefoxBase":
        """导航到指定 URL

        Uses browsingContext.navigate directly through the browser driver
        (not the context driver, since navigate already takes context as a param).

        Args:
            url: 目标 URL
            wait: 等待策略 'none'/'interactive'/'complete'，None 则根据 load_mode 决定
            timeout: 超时时间（秒）

        Returns:
            self
        """
        if wait is None:
            wait_map = {"normal": "complete", "eager": "interactive", "none": "none"}
            wait = wait_map.get(self._load_mode, "complete")

        if timeout:
            old_timeout = Settings.bidi_timeout
            Settings.bidi_timeout = timeout

        try:
            bidi_context.navigate(
                self._driver._browser_driver, self._context_id, url, wait=wait
            )
        except BiDiError as e:
            # navigate 失败不一定是错误（如 none 模式下立即返回）
            if self._is_expected_navigation_abort(e):
                logger.debug("导航被页面主动中断（通常是自动刷新/跳转）: %s", e)
            elif "timeout" not in str(e.error).lower():
                logger.warning("导航错误: %s", e)
        finally:
            if timeout:
                Settings.bidi_timeout = old_timeout

        self._reinject_xpath_picker_if_needed()
        self._reinject_action_visual_if_needed()
        return self

    def back(self) -> "FirefoxBase":
        """后退

        Returns:
            self
        """
        bidi_context.traverse_history(
            self._driver._browser_driver, self._context_id, -1
        )
        self._reinject_xpath_picker_if_needed()
        self._reinject_action_visual_if_needed()
        return self

    def forward(self) -> "FirefoxBase":
        """前进

        Returns:
            self
        """
        bidi_context.traverse_history(self._driver._browser_driver, self._context_id, 1)
        self._reinject_xpath_picker_if_needed()
        self._reinject_action_visual_if_needed()
        return self

    def refresh(self, ignore_cache=False) -> "FirefoxBase":
        """刷新页面

        Args:
            ignore_cache: 是否忽略缓存

        Returns:
            self
        """
        wait_map = {"normal": "complete", "eager": "interactive", "none": "none"}
        wait = wait_map.get(self._load_mode, "complete")
        try:
            bidi_context.reload(
                self._driver._browser_driver,
                self._context_id,
                ignore_cache=ignore_cache,
                wait=wait,
            )
        except BiDiError as e:
            if self._is_expected_navigation_abort(e):
                logger.debug("刷新被页面主动中断（通常是自动刷新/跳转）: %s", e)
            else:
                raise
        self._reinject_xpath_picker_if_needed()
        self._reinject_action_visual_if_needed()
        return self

    def stop_loading(self) -> "FirefoxBase":
        """停止加载

        Returns:
            self
        """
        self.run_js("window.stop()")
        return self

    def wait_loading(self, timeout=None) -> "FirefoxBase":
        """等待页面 DOM 加载完成（domContentLoaded 事件）

        通过订阅 browsingContext.domContentLoaded 事件来等待。
        如果当前 readyState 已经是 interactive 或 complete 则立即返回。

        Args:
            timeout: 超时时间（秒），None 使用默认超时

        Returns:
            self

        Raises:
            WaitTimeoutError: 超时
        """
        if timeout is None:
            timeout = Settings.bidi_timeout

        # 先检查当前状态
        state = self.run_js("document.readyState")
        if state in ("interactive", "complete"):
            self._reinject_xpath_picker_if_needed()
            self._reinject_action_visual_if_needed()
            return self

        # 轮询等待
        end_time = time.time() + timeout
        while time.time() < end_time:
            state = self.run_js("document.readyState")
            if state in ("interactive", "complete"):
                self._reinject_xpath_picker_if_needed()
                self._reinject_action_visual_if_needed()
                return self
            _sleep(0.1)

        raise WaitTimeoutError("等待页面加载超时 ({}s)".format(timeout))

    # ===== 元素查找 =====

    def ele(self, locator, index=1, timeout=None) -> "FirefoxElement | NoneElement":
        """查找单个元素。

        Args:
            locator: 定位器（字符串或元组）。
                最常用写法：
                ``'#kw'`` 按 CSS id；
                ``'.item'`` 按 CSS class；
                ``'css:div.card > a'`` 明确按 CSS；
                ``'xpath://input[@name="q"]'`` 按 XPath；
                ``'tag:input'`` 按标签名；
                ``'text:登录'`` 按文本；
                ``'text=登录'`` 也可作为文本定位简写。

                新手建议优先顺序：
                1. 先用 ``#id``
                2. 再用 ``css:...``
                3. 必要时再用 ``xpath:...``

                例子：
                ``page.ele('#search')``
                ``page.ele('css:.result-item a')``
                ``page.ele('xpath://button[text()="登录"]')``
                ``page.ele('tag:input', index=2)``
            index: 第几个匹配结果。
                单位：序号。
                常见值：``1`` 第一个、``2`` 第二个、``-1`` 最后一个。
            timeout: 查找超时时间。
                单位：秒。
                常见值：``1``、``3``、``5``。

        Returns:
            FirefoxElement 或 NoneElement。

        适用场景：
            - 你只想拿一个元素时
            - 页面上有多个同类元素，但你只关心其中一个
        """
        return self._find_element(locator, index=index, timeout=timeout)

    def eles(self, locator, timeout=None) -> "list[FirefoxElement]":
        """查找所有匹配的元素。

        Args:
            locator: 定位器（字符串或元组）。
                写法与 ``ele()`` 完全相同，例如：
                ``page.eles('css:.card')``
                ``page.eles('tag:a')``
                ``page.eles('xpath://ul/li')``
            timeout: 查找超时时间。
                单位：秒。
                常见值：``1``、``3``、``5``。

        Returns:
            list[FirefoxElement]: 所有匹配到的元素列表。

        适用场景：
            - 需要遍历结果列表时
            - 例如抓取搜索结果、表格行、商品卡片列表
        """
        return self._find_elements(locator, timeout=timeout)

    def s_ele(self, locator=None) -> "StaticElement | NoneElement":
        """获取静态元素（从当前 HTML 解析，不需要浏览器连接）

        Args:
            locator: 定位器，None 返回整个页面的静态元素

        Returns:
            StaticElement 或 NoneElement
        """
        from .._elements.static_element import StaticElement, make_static_ele

        html = self.html
        return make_static_ele(html, locator)

    def s_eles(self, locator) -> "list[StaticElement]":
        """获取所有匹配的静态元素

        Args:
            locator: 定位器

        Returns:
            StaticElement 列表
        """
        from .._elements.static_element import make_static_eles

        html = self.html
        return make_static_eles(html, locator)

    def _find_element(
        self, locator, index=1, timeout=None, raise_err=None, start_node=None
    ):
        """内部查找元素方法

        Args:
            locator: 定位器
            index: 索引
            timeout: 超时
            raise_err: 是否抛出异常
            start_node: 起始节点（用于相对查找）

        Returns:
            FirefoxElement 或 NoneElement
        """
        if timeout is None:
            timeout = Settings.element_find_timeout
        if raise_err is None:
            raise_err = Settings.raise_when_ele_not_found

        end_time = time.time() + timeout

        while True:
            elements = self._do_find(locator, start_node=start_node)
            if elements:
                if index > 0:
                    idx = index - 1
                elif index < 0:
                    idx = index
                else:
                    idx = 0
                try:
                    return elements[idx]
                except IndexError:
                    pass

            if time.time() >= end_time:
                break

            _sleep(0.3)

        if raise_err:
            raise ElementNotFoundError("未找到元素: {}".format(locator))

        from .._elements.none_element import NoneElement

        return NoneElement(self, method="ele", args={"locator": locator})

    def _find_elements(self, locator, timeout=None):
        """内部查找多个元素方法"""
        if timeout is None:
            timeout = Settings.element_find_timeout

        end_time = time.time() + timeout

        while True:
            elements = self._do_find(locator)
            if elements:
                return elements

            if time.time() >= end_time:
                break

            _sleep(0.3)

        return []

    def _do_find(self, locator, start_node=None):
        """执行实际的元素查找

        Args:
            locator: 定位器
            start_node: 起始节点（用于相对查找）

        Returns:
            FirefoxElement 列表
        """
        from .._elements.firefox_element import FirefoxElement

        bidi_locator = parse_locator(locator)

        # innerText 的 matchType 处理
        # BiDi 标准 innerText locator 默认是 full match
        # 我们需要 partial match 作为默认行为
        # 通过 JS 实现 partial text match
        match_type = bidi_locator.pop("matchType", None)

        params = {
            "context": self._context_id,
            "locator": bidi_locator,
        }

        if start_node:
            # 将 FirefoxElement 转换为 SharedReference 格式
            if hasattr(start_node, "_shared_id"):
                params["startNodes"] = [
                    {"type": "sharedReference", "sharedId": start_node._shared_id}
                ]
            else:
                params["startNodes"] = [start_node]

        # 设置序列化选项以获取节点属性
        params["serializationOptions"] = {"maxDomDepth": 0, "includeShadowTree": "open"}

        try:
            result = self._driver._browser_driver.run(
                "browsingContext.locateNodes", params
            )
            nodes = result.get("nodes", [])
        except BiDiError as e:
            # 定位器不支持时回退到 JS 查找
            err_str = str(e.error).lower()
            if (
                "invalid argument" in err_str
                or "invalid selector" in err_str
                or "unsupported" in err_str
            ):
                # innerText 类型回退到 JS 文本查找
                if bidi_locator.get("type") == "innerText":
                    return self._find_by_text_js(
                        bidi_locator.get("value", ""), start_node
                    )
                return self._find_by_js(locator, start_node)
            logger.debug("locateNodes 失败: %s", e)
            return []

        if not nodes:
            # 对于 innerText 类型，始终尝试 JS 回退实现部分匹配
            if bidi_locator.get("type") == "innerText":
                return self._find_by_text_js(bidi_locator.get("value", ""), start_node)
            return []

        elements = []
        for node in nodes:
            ele = FirefoxElement._from_node(self, node)
            if ele:
                elements.append(ele)

        return elements

    def _find_by_text_js(self, text, start_node=None):
        """通过 JS 实现文本部分匹配查找

        使用两轮策略：
        1. 先找所有 textContent 包含目标文本的"最深叶节点"
        2. 如果没找到叶节点，放宽条件找"最小包含节点"（自身文本包含但非所有子节点都包含）
        """
        from .._elements.firefox_element import FirefoxElement
        from .._functions.bidi_values import make_shared_ref

        js = """(text, rootNode) => {
            const root = rootNode || document.body || document.documentElement;
            const results = [];
            const seen = new Set();
            const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'LINK', 'META']);

            // 策略1: 最深叶节点匹配（排除script/style）
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
            let node;
            while (node = walker.nextNode()) {
                if (skip.has(node.tagName)) continue;
                const nt = (node.textContent || '').trim();
                if (!nt.includes(text)) continue;
                let hasChildMatch = false;
                for (let ch of node.children) {
                    if (skip.has(ch.tagName)) continue;
                    if ((ch.textContent || '').trim().includes(text)) {
                        hasChildMatch = true;
                        break;
                    }
                }
                if (!hasChildMatch) {
                    results.push(node);
                    seen.add(node);
                }
            }
            if (results.length > 0) return results;

            // 策略2: 找直接文本包含目标的节点
            const walker2 = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
            while (node = walker2.nextNode()) {
                if (skip.has(node.tagName) || seen.has(node)) continue;
                const nt = (node.textContent || '').trim();
                if (!nt.includes(text)) continue;
                let directText = '';
                for (let cn of node.childNodes) {
                    if (cn.nodeType === 3) directText += cn.textContent;
                }
                if (directText.includes(text)) {
                    results.push(node);
                    continue;
                }
                try {
                    if ((node.innerText || '').includes(text)) {
                        results.push(node);
                    }
                } catch(e) {}
            }
            if (results.length > 0) return results;

            // 策略3: 兜底
            const walker3 = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
            while (node = walker3.nextNode()) {
                if (skip.has(node.tagName)) continue;
                const nt = (node.textContent || '').trim();
                if (nt.includes(text) && nt.length < text.length * 5) {
                    results.push(node);
                }
            }
            return results;
        }"""

        args = [{"type": "string", "value": text}]
        if start_node:
            # 将 start_node 作为第二个参数传递给 JS（使用 BiDi sharedReference 格式）
            args.append({"type": "sharedReference", "sharedId": start_node._shared_id})

        try:
            result = bidi_script.call_function(
                self._driver._browser_driver,
                self._context_id,
                js,
                arguments=args,
                serialization_options={"maxDomDepth": 0, "includeShadowTree": "open"},
            )

            if result.get("type") == "exception":
                return []

            rv = result.get("result", {})
            if rv.get("type") != "array":
                return []

            elements = []
            for node in rv.get("value", []):
                ele = FirefoxElement._from_node(self, node)
                if ele:
                    elements.append(ele)
            return elements

        except Exception as e:
            logger.debug("JS 文本查找失败: %s", e)
            return []

    def _find_by_js(self, locator, start_node=None):
        """通过 JS 回退查找（用于不支持的定位器类型）"""
        from .._elements.firefox_element import FirefoxElement

        bidi_locator = parse_locator(locator)
        loc_type = bidi_locator.get("type", "")
        loc_value = bidi_locator.get("value", "")

        if loc_type == "css":
            js = "(sel) => Array.from(document.querySelectorAll(sel))"
            args = [{"type": "string", "value": loc_value}]
        elif loc_type == "xpath":
            js = """(expr) => {
                const result = [];
                const xr = document.evaluate(expr, document, null,
                    XPathResult.ORDERED_NODE_ITERATOR_TYPE, null);
                let node;
                while (node = xr.iterateNext()) result.push(node);
                return result;
            }"""
            args = [{"type": "string", "value": loc_value}]
        else:
            return []

        try:
            result = bidi_script.call_function(
                self._driver._browser_driver,
                self._context_id,
                js,
                arguments=args,
                serialization_options={"maxDomDepth": 0, "includeShadowTree": "open"},
            )

            if result.get("type") == "exception":
                return []

            rv = result.get("result", {})
            if rv.get("type") != "array":
                return []

            elements = []
            for node in rv.get("value", []):
                ele = FirefoxElement._from_node(self, node)
                if ele:
                    elements.append(ele)
            return elements

        except Exception as e:
            logger.debug("JS 查找失败: %s", e)
            return []

    # ===== JavaScript 执行 =====

    def run_js(self, script, *args, as_expr=None, timeout=None, sandbox=None):
        """执行 JavaScript

        Args:
            script: JS 代码
            *args: 传递给脚本的参数
            as_expr: 是否作为表达式执行（None 自动判断）
            timeout: 超时时间（秒）
            sandbox: BiDi sandbox 名称（隔离执行上下文）

        Returns:
            JS 执行的返回值（自动转换为 Python 对象）
        """
        if timeout:
            old_timeout = Settings.bidi_timeout
            Settings.bidi_timeout = timeout

        try:
            return self._run_js(script, *args, as_expr=as_expr, sandbox=sandbox)
        finally:
            if timeout:
                Settings.bidi_timeout = old_timeout

    def run_js_loaded(self, script, *args, as_expr=None, timeout=None):
        """等待页面加载完成后执行 JavaScript

        Args:
            script: JS 代码
            *args: 参数
            as_expr: 是否作为表达式
            timeout: 超时

        Returns:
            JS 返回值
        """
        self.wait.doc_loaded(timeout=timeout)
        return self.run_js(script, *args, as_expr=as_expr, timeout=timeout)

    def _run_js(self, script, *args, as_expr=None, sandbox=None):
        """内部 JS 执行

        Detection rules (when ``as_expr is None``):
          1. If ``args`` are provided -> callFunction (always)
          2. If script starts with ``return `` -> wrap in function body, callFunction
          3. Otherwise -> evaluate as expression
        """
        script = script.strip()

        # ---------- determine mode ----------
        if as_expr is not None:
            use_expr = as_expr
        elif args:
            # args provided -> must use callFunction
            use_expr = False
        elif script.startswith("return "):
            # has 'return' keyword -> needs function wrapper
            use_expr = False
        else:
            # simple expression (no return, no function keyword, no args)
            use_expr = True

        if use_expr:
            # ---------- expression mode ----------
            result = bidi_script.evaluate(
                self._driver._browser_driver, self._context_id, script, sandbox=sandbox
            )
        else:
            # ---------- function / callFunction mode ----------
            func_body = script

            # Wrap bare statements that start with 'return ' into a function body
            if script.startswith("return "):
                func_body = "function(){" + script + "}"
            elif not script.startswith("function") and not script.startswith("("):
                func_body = "function(){" + script + "}"

            # 序列化参数
            from .._functions.bidi_values import serialize_value

            serialized_args = [serialize_value(a) for a in args] if args else None

            result = bidi_script.call_function(
                self._driver._browser_driver,
                self._context_id,
                func_body,
                sandbox=sandbox,
                arguments=serialized_args,
            )

        # 检查异常
        if result.get("type") == "exception":
            details = result.get("exceptionDetails", {})
            text = details.get("text", str(result))
            raise JavaScriptError(text, details)

        # 解析返回值
        return parse_value(result.get("result", {}))

    # ===== Document Node =====

    def _get_document_node_id(self):
        """获取当前文档的 document 节点 SharedReference

        通过 script.callFunction 获取 document 对象，返回其 sharedId，
        可用于后续元素操作中作为 startNode。

        Returns:
            dict: SharedReference 格式 {'type': 'sharedReference', 'sharedId': '...'}
                  或 None
        """
        try:
            result = bidi_script.call_function(
                self._driver._browser_driver,
                self._context_id,
                "() => document",
                serialization_options={"maxDomDepth": 0, "includeShadowTree": "open"},
            )

            if result.get("type") == "exception":
                return None

            rv = result.get("result", {})
            shared_id = rv.get("sharedId")
            if shared_id:
                return make_shared_ref(shared_id)
            return None
        except Exception as e:
            logger.debug("获取 document 节点失败: %s", e)
            return None

    # ===== Cookie 操作 =====

    def get_cookies(self, all_info=False) -> list:
        """获取当前页面的 Cookie。

        Args:
            all_info: True 返回完整 Cookie 信息

        Returns:
            list[CookieInfo]: Cookie 对象列表。

        适用场景：
            - 读取当前页面上下文下可见的 Cookie
            - 在示例中按属性访问 ``cookie.name`` / ``cookie.value``
        """
        from .._bidi import storage as bidi_storage
        from .._units.cookies import CookieInfo

        try:
            result = bidi_storage.get_cookies(
                self._driver._browser_driver, partition={"context": self._context_id}
            )
        except Exception:
            # 回退到 JS 获取
            cookie_str = self.run_js("document.cookie") or ""
            cookies = []
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    cookies.append(
                        CookieInfo({"name": name.strip(), "value": value.strip()})
                    )
            return cookies

        raw_cookies = result.get("cookies", [])

        if not all_info:
            return [
                CookieInfo(
                    {
                        "name": c.get("name", ""),
                        "value": c.get("value", {}).get("value", "")
                        if isinstance(c.get("value"), dict)
                        else str(c.get("value", "")),
                    }
                )
                for c in raw_cookies
            ]

        return [CookieInfo(c) for c in raw_cookies]

    def get_cookies_filtered(self, name=None, domain=None, all_info=True):
        """按过滤条件读取当前页面 Cookie。

        Args:
            name: Cookie 名称过滤。
                常见值：``'session_id'``、``'user_id'``。传 ``None`` 表示不过滤名称。
            domain: 域名过滤。
                常见值：``'127.0.0.1'``、``'.example.com'``。传 ``None`` 表示不过滤域名。
            all_info: 是否返回完整 Cookie 信息。
                常见值：``True``、``False``。

        Returns:
            list[CookieInfo]: 过滤后的 Cookie 对象列表。

        适用场景：
            - 替代示例层直接调用 ``storage.getCookies(filter_=...)``
            - 新手按名称/域名筛选 Cookie
        """
        cookies = self.get_cookies(all_info=all_info)
        result = cookies
        if name is not None:
            result = [c for c in result if c.name == name]
        if domain is not None:
            result = [c for c in result if c.domain == domain]
        return result

    def set_cookies(self, cookies, domain=None, path=None) -> None:
        """设置 Cookie

        Args:
            cookies: Cookie 字典、字典列表，或浏览器复制出的 Cookie 字符串
                {'name': 'x', 'value': 'y', 'domain': '.example.com'}
                'a=1; b=2'
            domain: 传 Cookie 字符串时可显式指定域名
            path: 传 Cookie 字符串时可显式指定路径
        """
        from .._bidi import storage as bidi_storage
        from .._functions.cookies import cookie_str_to_list

        current_domain = ""
        current_url = self.url
        if current_url.startswith(("http://", "https://")):
            from urllib.parse import urlparse

            current_domain = urlparse(current_url).hostname or ""

        if isinstance(cookies, str):
            cookies = cookie_str_to_list(cookies)
            default_domain = domain or current_domain
            for cookie in cookies:
                if default_domain:
                    cookie.setdefault("domain", default_domain)
                if path:
                    cookie.setdefault("path", path)

        if isinstance(cookies, dict):
            cookies = [cookies]

        for cookie in cookies:
            raw_value = cookie.get("value", "")
            if isinstance(raw_value, dict):
                # 已经是 BiDi 格式 {"type": "string", "value": "..."}，直接使用
                bidi_value = raw_value
            else:
                bidi_value = {"type": "string", "value": str(raw_value)}

            bidi_cookie = {
                "name": cookie.get("name", ""),
                "value": bidi_value,
                "domain": cookie.get("domain", ""),
            }

            cookie_domain = str(cookie.get("domain", "") or "").lstrip(".").lower()
            normalized_current = current_domain.lstrip(".").lower()
            use_context_partition = bool(self._context_id)

            # W3C BiDi 的 context partition 绑定当前浏览上下文的存储分区。
            # 如果当前页面还是 about:blank，或要写入的 cookie 域与当前域不匹配，
            # 把 cookie 绑定到当前 context 分区会导致跨站登录 cookie 无法真正落地。
            if not normalized_current or not cookie_domain:
                use_context_partition = False
            elif not (
                normalized_current == cookie_domain
                or normalized_current.endswith("." + cookie_domain)
            ):
                use_context_partition = False

            # 可选字段
            for key in ("path", "httpOnly", "secure", "sameSite", "expiry"):
                py_key = key
                if py_key in cookie:
                    bidi_cookie[key] = cookie[py_key]

            try:
                if use_context_partition:
                    bidi_storage.set_cookie(
                        self._driver._browser_driver,
                        bidi_cookie,
                        partition={"context": self._context_id},
                    )
                else:
                    bidi_storage.set_cookie(self._driver._browser_driver, bidi_cookie)
            except Exception:
                # 某些 Firefox 版本不支持 partition 参数，或当前 context 分区不适用于跨站 cookie
                bidi_storage.set_cookie(self._driver._browser_driver, bidi_cookie)

    def delete_cookies(self, name=None, domain=None) -> None:
        """删除 Cookie

        Args:
            name: Cookie 名称（None 删除所有）
            domain: 限定域名
        """
        from .._bidi import storage as bidi_storage

        filter_ = {}
        if name:
            filter_["name"] = name
        if domain:
            filter_["domain"] = domain

        try:
            bidi_storage.delete_cookies(
                self._driver._browser_driver,
                filter_=filter_ or None,
                partition={"context": self._context_id},
            )
        except Exception:
            bidi_storage.delete_cookies(
                self._driver._browser_driver, filter_=filter_ or None
            )

    # ===== 截图 / PDF =====

    def screenshot(self, path=None, full_page=False, as_bytes=None, as_base64=None):
        """截图

        Args:
            path: 保存路径（None 不保存文件）
            full_page: True 截取整个页面，False 仅视口
            as_bytes: True 返回 bytes（优先级高于 as_base64）
            as_base64: True 返回 base64 字符串

        Returns:
            根据参数返回文件路径/bytes/base64 字符串
        """
        origin = "document" if full_page else "viewport"
        result = bidi_context.capture_screenshot(
            self._driver._browser_driver, self._context_id, origin=origin
        )

        data_b64 = result.get("data", "")
        data_bytes = base64.b64decode(data_b64)

        if path:
            import os

            os.makedirs(
                os.path.dirname(os.path.abspath(path)), exist_ok=True
            ) if os.path.dirname(path) else None
            with open(path, "wb") as f:
                f.write(data_bytes)

        if as_bytes:
            return data_bytes
        if as_base64:
            return data_b64
        if path:
            return path

        return data_bytes

    def pdf(self, path=None, **kwargs):
        """打印为 PDF。

        Args:
            path: 保存路径。
                - 传字符串路径时：直接写入文件并返回该路径
                - 传 None 时：返回 PDF 的 bytes 数据
            **kwargs: 透传给 `browsingContext.print` 的打印参数，常用项如下：
                - background: 是否打印背景（bool）
                - margin: 页边距，单位 cm，例如
                  {'top': 1.2, 'bottom': 1.2, 'left': 1.0, 'right': 1.0}
                - orientation: 页面方向，'portrait' 或 'landscape'
                - page: 纸张尺寸，单位 cm，例如 A4 为
                  {'width': 21.0, 'height': 29.7}
                - page_ranges: 页码范围列表，例如 ['1-2']
                - scale: 缩放比例，例如 0.9
                - shrink_to_fit: 内容过宽时是否自动缩放（bool）

        Returns:
            path 或 bytes
        """
        result = bidi_context.print_(
            self._driver._browser_driver, self._context_id, **kwargs
        )

        data_b64 = result.get("data", "")
        data_bytes = base64.b64decode(data_b64)

        if path:
            with open(path, "wb") as f:
                f.write(data_bytes)
            return path

        return data_bytes

    def save_pdf(self, path, **kwargs):
        """新手友好别名：直接保存 PDF 到文件。

        Args:
            path: 输出 PDF 文件路径
            **kwargs: 与 `pdf()` 相同的打印参数

        Returns:
            str: 保存后的文件路径
        """
        return self.pdf(path=path, **kwargs)

    # ===== 弹窗处理 =====

    def handle_alert(self, action="accept", text=None, timeout=3) -> "str | None":
        """处理弹窗

        如果弹窗还没出现，会轮询等待直到 timeout。

        Args:
            action: 'accept' 接受 / 'dismiss' 拒绝
            text: 对于 prompt 弹窗填入的文本
            timeout: 等待弹窗出现的超时（秒）

        Returns:
            self
        """
        accept = action != "dismiss"
        end_time = time.time() + timeout
        drv = self._driver._browser_driver

        while time.time() < end_time:
            try:
                # 先等待 userPromptOpened 事件真正到达，避免“页面已调用 confirm，
                # 但浏览器侧 prompt 状态尚未建立”时过早处理。
                if not getattr(drv, "alert_flag", False):
                    _sleep(0.05)
                    continue

                bidi_context.handle_user_prompt(
                    drv,
                    self._context_id,
                    accept=accept,
                    user_text=text,
                )
                return self
            except BiDiError as e:
                if "no such alert" in str(e.error).lower():
                    _sleep(0.05)
                    continue
                raise

        return self

    def accept_alert(self, text=None, timeout=3):
        """接受当前弹窗（prompt 可选输入文本）。

        Args:
            text: prompt 输入文本
            timeout: 等待弹窗出现超时（秒）

        说明：
            - 这是 `handle_alert(action='accept', ...)` 的语义化别名。
            - 建议新手优先使用该方法，可读性更好。
        """
        return self.handle_alert(action="accept", text=text, timeout=timeout)

    def dismiss_alert(self, timeout=3):
        """拒绝/取消当前弹窗。

        说明：
            - 这是 `handle_alert(action='dismiss', ...)` 的语义化别名。
        """
        return self.handle_alert(action="dismiss", timeout=timeout)

    def get_user_prompt(self):
        """获取当前 context 的用户弹窗信息。

        Returns:
            dict 或 None
        """
        try:
            result = self._driver._browser_driver.run(
                "browsingContext.getTree", {"root": self._context_id}
            )
            contexts = result.get("contexts", [])
            if not contexts:
                return None
            prompt = contexts[0].get("userPrompt")
            return prompt if isinstance(prompt, dict) else None
        except Exception:
            return None

    def get_last_prompt_opened(self):
        """获取最近一次 userPromptOpened 事件参数。"""
        return (
            dict(self._last_prompt_opened)
            if isinstance(self._last_prompt_opened, dict)
            else None
        )

    def get_last_prompt_closed(self):
        """获取最近一次 userPromptClosed 事件参数。"""
        return (
            dict(self._last_prompt_closed)
            if isinstance(self._last_prompt_closed, dict)
            else None
        )

    def set_prompt_handler(
        self,
        *,
        alert="accept",
        confirm="accept",
        prompt="ignore",
        default="accept",
        prompt_text=None,
    ):
        """设置用户提示框自动处理策略（新手友好）。

        推荐：
            - alert/confirm 通常直接 accept/dismiss
            - prompt 需要写文本时：prompt='ignore' 且 prompt_text='你的文本'

        Returns:
            self

        说明：
            - alert/confirm 建议直接 accept 或 dismiss。
            - prompt 需要输入文本时，推荐 prompt='ignore' + prompt_text='你的文本'。
            - 该方法内部会订阅 userPromptOpened/Closed 事件。
        """
        from .._bidi import session as bidi_session
        from .._bidi import browsing_context as bidi_context

        self.clear_prompt_handler()
        self._prompt_handler_config = {
            "alert": alert,
            "confirm": confirm,
            "prompt": prompt,
            "default": default,
            "prompt_text": prompt_text,
        }

        def on_opened(params):
            if params.get("context") != self.tab_id:
                return
            self._last_prompt_opened = dict(params)
            if (
                params.get("type") == "prompt"
                and self._prompt_handler_config
                and self._prompt_handler_config.get("prompt") == "ignore"
                and self._prompt_handler_config.get("prompt_text") is not None
            ):
                try:
                    bidi_context.handle_user_prompt(
                        self._driver._browser_driver,
                        self.tab_id,
                        accept=True,
                        user_text=str(self._prompt_handler_config.get("prompt_text")),
                    )
                except Exception:
                    pass

        def on_closed(params):
            if params.get("context") != self.tab_id:
                return
            self._last_prompt_closed = dict(params)

        self._driver._browser_driver.set_callback(
            "browsingContext.userPromptOpened",
            on_opened,
            context=self.tab_id,
            immediate=True,
        )
        self._driver._browser_driver.set_callback(
            "browsingContext.userPromptClosed",
            on_closed,
            context=self.tab_id,
            immediate=True,
        )
        sub = bidi_session.subscribe(
            self._driver._browser_driver,
            ["browsingContext.userPromptOpened", "browsingContext.userPromptClosed"],
            contexts=[self.tab_id],
        )
        self._prompt_subscription_id = sub.get("subscription")
        return self

    def clear_prompt_handler(self):
        """清理用户提示框自动处理策略。"""
        from .._bidi import session as bidi_session

        try:
            if self._prompt_subscription_id:
                bidi_session.unsubscribe(
                    self._driver._browser_driver,
                    subscription=self._prompt_subscription_id,
                )
        except Exception:
            pass
        try:
            self._driver._browser_driver.remove_callback(
                "browsingContext.userPromptOpened",
                context=self.tab_id,
                immediate=True,
            )
            self._driver._browser_driver.remove_callback(
                "browsingContext.userPromptClosed",
                context=self.tab_id,
                immediate=True,
            )
        except Exception:
            pass
        self._prompt_subscription_id = None
        self._prompt_handler_config = None
        return self

    def wait_prompt(self, timeout=3):
        """等待当前 context 出现用户提示框。

        Returns:
            dict 或 None
        """
        end = time.time() + timeout
        initial_opened = self.get_last_prompt_opened()
        initial_closed = self.get_last_prompt_closed()

        while time.time() < end:
            # 优先读取事件缓存。某些实现里 userPromptOpened 事件比 getTree.userPrompt
            # 更早可见，用它能提高步骤式 API 的稳定性。
            opened = self.get_last_prompt_opened()
            closed = self.get_last_prompt_closed()
            if opened and opened != initial_opened and opened != closed:
                return opened

            prompt = self.get_user_prompt()
            if prompt:
                return prompt
            _sleep(0.05)
        return None

    def handle_prompt(self, accept=True, text=None, timeout=3):
        """按步骤处理当前 prompt。

        这是比 handle_alert 更直观的高层封装：
        1) 等待 prompt 出现
        2) 调用 browsingContext.handleUserPrompt

        Args:
            accept: True=确认，False=取消
            text: prompt 输入文本（仅 prompt + accept=True 时有意义）
            timeout: 等待 prompt 出现超时

        Returns:
            self
        """
        from .._bidi import browsing_context as bidi_context

        prompt = self.wait_prompt(timeout=timeout)
        if not prompt:
            return self
        bidi_context.handle_user_prompt(
            self._driver._browser_driver,
            self._context_id,
            accept=accept,
            user_text=("" if text is None else str(text)),
        )
        return self

    def respond_prompt(self, *, accept=True, text=None, timeout=3):
        """步骤式处理 prompt 的统一入口。"""
        return self.handle_prompt(accept=accept, text=text, timeout=timeout)

    def accept_prompt(self, text=None, timeout=3):
        """等待并确认当前 prompt（可选输入文本）。"""
        return self.handle_prompt(accept=True, text=text, timeout=timeout)

    def dismiss_prompt(self, timeout=3):
        """等待并取消当前 prompt。"""
        return self.handle_prompt(accept=False, timeout=timeout)

    def input_prompt(self, text, timeout=3):
        """给 prompt 输入文本并确认。"""
        return self.handle_prompt(accept=True, text=text, timeout=timeout)

    def trigger_prompt_target(self, locator, trigger="mouse"):
        """触发会弹出 prompt 的目标元素。

        Args:
            locator: 元素定位器
            trigger: 'mouse' 或 'keyboard'

        说明：
            - mouse: 原生鼠标点击触发
            - keyboard: 聚焦后发送 Enter 触发
        """
        ele = self.ele(locator)
        if trigger == "keyboard":
            ele.click_self()
            self.actions.key_down("\ue007").key_up("\ue007").perform()
        else:
            ele.click_self()
        return self

    def prompt_login(
        self, trigger_locator, username, password, trigger="mouse", timeout=3
    ):
        """两步 prompt 登录流程：先用户名，再密码。

        这是事件驱动版本：
        - 点击触发目标后
        - 在 userPromptOpened 回调中立即注入用户名/密码
        - 避免 prompt 打开后阻塞后续动作链

        Args:
            trigger_locator: 触发登录 prompt 的元素定位器
            username: 用户名
            password: 密码
            trigger: 'mouse' 或 'keyboard'
            timeout: 总等待时间

        Returns:
            self
        """
        import threading
        from .._bidi import session as bidi_session
        from .._bidi import browsing_context as bidi_context

        # 临时移除已有自动策略，避免与本次登录流冲突
        self.clear_prompt_handler()

        opened_count = {"value": 0}
        done = threading.Event()
        sub_id = None

        def on_opened(params):
            if params.get("context") != self.tab_id:
                return
            if params.get("type") != "prompt":
                return

            opened_count["value"] += 1
            self._last_prompt_opened = dict(params)

            try:
                if opened_count["value"] == 1:
                    bidi_context.handle_user_prompt(
                        self._driver._browser_driver,
                        self.tab_id,
                        accept=True,
                        user_text=str(username),
                    )
                elif opened_count["value"] == 2:
                    bidi_context.handle_user_prompt(
                        self._driver._browser_driver,
                        self.tab_id,
                        accept=True,
                        user_text=str(password),
                    )
                    done.set()
            except Exception:
                pass

        def on_closed(params):
            if params.get("context") != self.tab_id:
                return
            self._last_prompt_closed = dict(params)

        self._driver._browser_driver.set_callback(
            "browsingContext.userPromptOpened",
            on_opened,
            context=self.tab_id,
            immediate=True,
        )
        self._driver._browser_driver.set_callback(
            "browsingContext.userPromptClosed",
            on_closed,
            context=self.tab_id,
            immediate=True,
        )
        sub = bidi_session.subscribe(
            self._driver._browser_driver,
            ["browsingContext.userPromptOpened", "browsingContext.userPromptClosed"],
            contexts=[self.tab_id],
        )
        sub_id = sub.get("subscription")

        try:
            self.trigger_prompt_target(trigger_locator, trigger=trigger)
            done.wait(timeout)
            _sleep(0.2)
        finally:
            try:
                if sub_id:
                    bidi_session.unsubscribe(
                        self._driver._browser_driver,
                        subscription=sub_id,
                    )
            except Exception:
                pass
            try:
                self._driver._browser_driver.remove_callback(
                    "browsingContext.userPromptOpened",
                    context=self.tab_id,
                    immediate=True,
                )
                self._driver._browser_driver.remove_callback(
                    "browsingContext.userPromptClosed",
                    context=self.tab_id,
                    immediate=True,
                )
            except Exception:
                pass
        return self

    # ===== 视口 / 模拟 =====

    def set_viewport(self, width, height, device_pixel_ratio=None) -> "FirefoxBase":
        """设置当前页面视口大小。

        Args:
            width: 视口宽度。
                单位：CSS 像素。
                常见值：``800``、``1280``、``375``。
            height: 视口高度。
                单位：CSS 像素。
                常见值：``600``、``720``、``667``。
            device_pixel_ratio: 设备像素比。
                常见值：``1``、``2``、``3``。传 ``None`` 表示不改动当前 DPR。

        Returns:
            self: 原页面对象，便于链式调用。

        适用场景：
            - 快速调整页面可视区域
            - 与移动端模拟配合设置 viewport + DPR
        """
        bidi_context.set_viewport(
            self._driver._browser_driver,
            self._context_id,
            width=width,
            height=height,
            device_pixel_ratio=device_pixel_ratio,
        )
        return self

    def set_useragent(self, ua) -> "FirefoxBase":
        """设置 User-Agent

        Firefox stable 不支持 emulation.setUserAgentOverride，
        因此通过 script.addPreloadScript 注入 JS 来覆盖 navigator.userAgent。
        同时在当前页面立即执行覆盖脚本。

        Args:
            ua: User-Agent 字符串

        Returns:
            self
        """
        # 移除之前的 UA preload script（如果有）
        if self._ua_preload_script_id:
            try:
                bidi_script.remove_preload_script(
                    self._driver._browser_driver, self._ua_preload_script_id
                )
            except Exception:
                pass
            self._ua_preload_script_id = None

        # 构造注入脚本：覆盖 navigator.userAgent
        escaped_ua = ua.replace("\\", "\\\\").replace("'", "\\'")
        inject_js = (
            "() => {"
            "  Object.defineProperty(navigator, 'userAgent', "
            "{get: () => '" + escaped_ua + "'});"
            "}"
        )

        # 注册 preload script，后续导航也会生效
        result = bidi_script.add_preload_script(
            self._driver._browser_driver, inject_js, contexts=[self._context_id]
        )
        self._ua_preload_script_id = result.get("script", "")

        # 在当前页面立即生效
        try:
            bidi_script.call_function(
                self._driver._browser_driver, self._context_id, inject_js
            )
        except Exception as e:
            logger.debug("当前页面 UA 覆盖执行失败（preload 仍然生效）: %s", e)

        return self

    def set_bypass_csp(self, bypass=True) -> "FirefoxBase":
        """尝试绕过页面 CSP。

        说明：
            - 这是 ruyipage 的高层兼容入口，不等同于标准 ``browsingContext.setBypassCSP``。
            - 当前实现主要通过 preload script 移除页面里的 CSP meta 标签。
            - 它更适合处理“页面内 meta CSP”的场景，不能等价替代浏览器原生 CSP 绕过能力。

        Args:
            bypass: 是否启用兼容式 CSP 绕过。
                常见值：``True`` 启用、``False`` 不处理。

        Returns:
            self: 原页面对象，便于链式调用。

        适用场景：
            - 需要在不支持标准命令的 Firefox 版本上做最小兼容处理
            - 示例或测试中临时移除页面 meta CSP 限制
        """
        if bypass:
            # 注入移除 CSP meta 标签的 preload script
            inject_js = """() => {
                // 移除 CSP meta 标签
                const observer = new MutationObserver((mutations) => {
                    for (const mutation of mutations) {
                        for (const node of mutation.addedNodes) {
                            if (node.tagName === 'META' &&
                                node.httpEquiv &&
                                node.httpEquiv.toLowerCase() === 'content-security-policy') {
                                node.remove();
                            }
                        }
                    }
                });
                observer.observe(document.documentElement, {childList: true, subtree: true});
                // 移除已存在的 CSP meta 标签
                document.querySelectorAll('meta[http-equiv="Content-Security-Policy"]')
                    .forEach(el => el.remove());
            }"""
            self.add_preload_script(inject_js)
        return self

    def add_preload_script(self, script) -> str:
        """添加预加载脚本（每次导航前执行）。

        Args:
            script: JavaScript 函数声明字符串。
                常见值：``"() => { ... }"``、``"(arg) => { ... }"``。

        Returns:
            PreloadScript: 支持 ``id`` 属性访问的预加载脚本对象。

        适用场景：
            - 在每次页面导航前注入一段脚本
            - 配合 ``script.message`` 或页面初始化逻辑做事件测试
        """
        from .._units.script_tools import PreloadScript

        result = bidi_script.add_preload_script(
            self._driver._browser_driver, script, contexts=[self._context_id]
        )
        return PreloadScript(result.get("script", ""))

    def remove_preload_script(self, script_id) -> None:
        """移除预加载脚本。

        Args:
            script_id: 预加载脚本 ID。
                常见值：``preload.id``。也兼容直接传字符串 ID。

        Returns:
            self: 原页面对象，便于链式调用。

        适用场景：
            - 清理测试中临时注册的 preload script
            - 避免后续导航重复执行同一段注入脚本
        """
        script_id = getattr(script_id, "id", script_id)
        bidi_script.remove_preload_script(self._driver._browser_driver, script_id)
        return self

    # ===== Trusted 事件状态 =====

    def is_trusted(self, event_key):
        """读取测试页中记录的 isTrusted 标记。

        适用场景：
            - 示例页会把最近一次事件的 isTrusted 写到 window 变量
            - 例如 window.lastClickTrusted / window.lastMouseEnterTrusted

        Args:
            event_key: 事件键名，支持两种写法：
                1) 完整变量名：'lastClickTrusted'
                2) 简写键名：'click' / 'dblclick' / 'contextmenu' /
                   'keydown' / 'mouseenter' / 'mousedown'

        Returns:
            bool 或 None

        注意：
            - 该方法依赖页面已把 isTrusted 写入 window 变量。
            - 在普通业务页面中，如果未埋点对应变量，返回值通常为 None。
        """
        mapping = {
            "click": "lastClickTrusted",
            "dblclick": "lastDblClickTrusted",
            "contextmenu": "lastContextMenuTrusted",
            "keydown": "lastKeydownTrusted",
            "mouseenter": "lastMouseEnterTrusted",
            "mousedown": "lastMouseDownTrusted",
        }
        key = mapping.get(str(event_key).lower(), event_key)
        return self.run_js("return window[arguments[0]]", key, as_expr=False)

    # ===== Frame 访问 =====

    def get_frame(self, locator=None, index=None, context_id=None) -> "FirefoxFrame":
        """获取 iframe/frame

        Args:
            locator: iframe 元素定位器
            index: iframe 序号（从0开始）
            context_id: 直接指定 context ID

        Returns:
            FirefoxFrame

        匹配优先级：
            1) context_id（最精确）
            2) index
            3) locator（按 iframe src 与 child context URL 尝试匹配）
            4) 兜底返回第一个 child context

        说明：
            - BiDi 下每个 iframe 都有独立 context，可直接操作。
            - 对 srcdoc/动态 iframe，URL 匹配可能不可用，因此保留 index 与兜底策略。
        """
        from .._pages.firefox_frame import FirefoxFrame

        if context_id:
            return FirefoxFrame(self._browser, context_id, self)

        # 通过 browsingContext.getTree 获取子 context
        result = bidi_context.get_tree(
            self._driver._browser_driver, root=self._context_id
        )
        contexts = result.get("contexts", [])
        children = contexts[0].get("children", []) if contexts else []

        if index is not None:
            if 0 <= index < len(children):
                child_ctx = children[index]["context"]
                return FirefoxFrame(self._browser, child_ctx, self)
            return None

        if locator:
            # 查找 iframe 元素，获取其对应的 child context
            ele = self.ele(locator)
            if not ele:
                return None

            # 尝试通过 URL 匹配
            ele_src = ele.attr("src") or ""
            for child in children:
                child_url = child.get("url", "")
                if ele_src and ele_src in child_url:
                    return FirefoxFrame(self._browser, child["context"], self)

            # 如果只有一个 iframe，直接返回第一个 child
            if len(children) == 1:
                return FirefoxFrame(self._browser, children[0]["context"], self)

        # 返回第一个子 context
        if children:
            return FirefoxFrame(self._browser, children[0]["context"], self)

        return None

    def get_frames(self) -> "list[FirefoxFrame]":
        """获取所有 iframe/frame

        Returns:
            FirefoxFrame 列表
        """
        from .._pages.firefox_frame import FirefoxFrame

        result = bidi_context.get_tree(
            self._driver._browser_driver, root=self._context_id
        )
        contexts = result.get("contexts", [])
        children = contexts[0].get("children", []) if contexts else []

        return [FirefoxFrame(self._browser, c["context"], self) for c in children]

    @contextmanager
    def with_frame(self, locator=None, index=None, context_id=None):
        """使用 with 语法访问 iframe（更简洁）。

        用法::

            with page.with_frame('#test-iframe') as frame:
                print(frame.ele('tag:h1').text)

        Args:
            locator: iframe 元素定位器
            index: iframe 序号（从0开始）
            context_id: 直接指定 context ID

        Yields:
            FirefoxFrame

        设计目的：
            - 让新手避免在页面/iframe 间来回切换心智负担。
            - `with` 内专注操作 frame；退出后直接继续操作原 page。
        """
        frame = self.get_frame(locator=locator, index=index, context_id=context_id)
        if frame is None:
            raise RuntimeError("未找到目标 iframe/frame")
        yield frame

    # ===== Cloudflare 验证 =====

    def handle_cloudflare_challenge(self, timeout=30, check_interval=2):
        """自动处理 Cloudflare Turnstile 验证（5s 盾）

        通过 BiDi 查找 CF iframe 并在其内部触发点击，绕过 closed shadow root 限制。

        Args:
            timeout: 最大等待时间（秒）
            check_interval: 检测间隔（秒）

        Returns:
            bool: 是否成功通过验证

        Example:
            page.get('https://example.com')
            if page.handle_cloudflare_challenge():
                print('通过验证')
        """
        import random

        start_time = time.time()
        attempt = 0

        while time.time() - start_time < timeout:
            attempt += 1
            logger.info(f"CF 验证第 {attempt} 次尝试...")

            try:
                # 用 browsingContext.getTree 查找 CF iframe
                tree = self._driver._browser_driver.run("browsingContext.getTree", {})
                all_contexts = tree.get("contexts", [])

                def find_cf_context(ctxs):
                    """递归查找 CF iframe context"""
                    for c in ctxs:
                        url = c.get("url", "")
                        if (
                            "challenges.cloudflare.com" in url
                            or "turnstile" in url
                            or "cf-chl" in url
                        ):
                            return c
                        found = find_cf_context(c.get("children", []))
                        if found:
                            return found
                    return None

                cf_ctx = find_cf_context(all_contexts)

                if not cf_ctx:
                    logger.debug("未找到 CF iframe，继续等待...")
                    _sleep(check_interval)
                    continue

                cf_ctx_id = cf_ctx["context"]
                logger.info(f"找到 CF iframe: {cf_ctx_id[:20]}")

                # 获取 iframe 尺寸
                result = self._driver._browser_driver.run(
                    "script.evaluate",
                    {
                        "expression": """(() => {
                        const rect = document.documentElement.getBoundingClientRect();
                        return {w: Math.round(rect.width), h: Math.round(rect.height)};
                    })()""",
                        "target": {"context": cf_ctx_id},
                        "awaitPromise": False,
                    },
                )

                # 解析 BiDi 嵌套对象格式
                raw_value = result.get("result", {}).get("value", [])
                size = {}
                if isinstance(raw_value, list):
                    for item in raw_value:
                        if isinstance(item, list) and len(item) == 2:
                            key = item[0]
                            val_obj = item[1]
                            if isinstance(val_obj, dict) and "value" in val_obj:
                                size[key] = val_obj["value"]

                if size.get("w", 0) == 0 or size.get("h", 0) == 0:
                    logger.warning("无法获取 iframe 尺寸")
                    _sleep(check_interval)
                    continue

                logger.info(f"iframe 尺寸: {size['w']}×{size['h']}")

                # 在 CF iframe 内部查找 checkbox
                checkbox_result = self._driver._browser_driver.run(
                    "script.evaluate",
                    {
                        "expression": """(() => {
                        const checkbox = document.querySelector('input[type="checkbox"]') ||
                                       document.querySelector('[role="checkbox"]') ||
                                       document.querySelector('label');
                        if (checkbox) {
                            const rect = checkbox.getBoundingClientRect();
                            return {
                                found: true,
                                x: Math.round(rect.left + rect.width / 2),
                                y: Math.round(rect.top + rect.height / 2)
                            };
                        }
                        return {found: false};
                    })()""",
                        "target": {"context": cf_ctx_id},
                        "awaitPromise": False,
                    },
                )

                # 解析 checkbox 位置
                checkbox_raw = checkbox_result.get("result", {}).get("value", [])
                checkbox_data = {}
                if isinstance(checkbox_raw, list):
                    for item in checkbox_raw:
                        if isinstance(item, list) and len(item) == 2:
                            key = item[0]
                            val = item[1]
                            if isinstance(val, dict):
                                checkbox_data[key] = val.get("value", False)

                # 直接在 CF iframe 内部触发点击（绕过 closed shadow root）
                if checkbox_data.get("found"):
                    click_x = int(checkbox_data["x"])
                    click_y = int(checkbox_data["y"])
                    logger.info(f"在 iframe 内部点击 checkbox: ({click_x}, {click_y})")
                else:
                    # fallback: 点击 iframe 左侧（checkbox 通常在左边）
                    click_x = 35
                    click_y = size["h"] // 2
                    logger.info(f"在 iframe 内部点击左侧: ({click_x}, {click_y})")

                # 使用拟人轨迹点击（Bezier/弧线/超出回拉 + 悬停抖动 + 点击后漂移）
                # 起始坐标限制在 iframe 范围内，避免坐标越界
                import random as _rand
                start_x = _rand.randint(max(1, click_x + 40), max(click_x + 60, size["w"] - 10))
                start_y = _rand.randint(max(1, click_y - 15), max(click_y + 15, size["h"] - 5))
                human_actions = build_human_click_actions(click_x, click_y, sx=start_x, sy=start_y)
                self._driver._browser_driver.run(
                    "input.performActions",
                    {
                        "context": cf_ctx_id,
                        "actions": human_actions,
                    },
                )

                # 等待验证结果
                _sleep(3)

                # 检查是否通过
                body_text = self.run_js("document.body.innerText") or ""
                if len(body_text) > 200 and "verify" not in body_text.lower()[:500]:
                    logger.info("成功通过 CF 验证")
                    return True

            except Exception as e:
                logger.warning(f"CF 验证失败: {e}")
                _sleep(check_interval)
                continue

        logger.error(f"CF 验证超时（{timeout}秒）")
        return False

    # ===== Emulation 便捷方法 =====

    def set_geolocation(self, latitude, longitude, accuracy=100):
        """设置地理位置 (FF139+)

        Args:
            latitude: 纬度
            longitude: 经度
            accuracy: 精度（米）

        Returns:
            self
        """
        self.emulation.set_geolocation(latitude, longitude, accuracy)
        return self

    def set_timezone(self, timezone_id):
        """设置时区 (FF144+)，如 'Asia/Shanghai'

        Returns:
            self
        """
        self.emulation.set_timezone(timezone_id)
        return self

    def set_locale(self, locales):
        """设置语言 (FF142+)，如 ['zh-CN', 'zh'] 或 'zh-CN'

        Returns:
            self
        """
        self.emulation.set_locale(locales)
        return self

    def set_screen_orientation(self, orientation_type, angle=0):
        """设置屏幕方向 (FF144+)

        Args:
            orientation_type: 'portrait-primary'/'landscape-primary' 等
            angle: 0/90/180/270

        Returns:
            self
        """
        self.emulation.set_screen_orientation(orientation_type, angle)
        return self

    # ===== Script Realm =====

    def get_realms(self, type_=None):
        """获取当前 context 的所有 Realm（执行上下文）。

        Args:
            type_: 可选的 realm 类型过滤。
                常见值：``'window'``、``'dedicated-worker'``、``'service-worker'``。

        Returns:
            list[RealmInfo]: 支持属性访问的 realm 对象列表。

        适用场景：
            - 查看当前页面有哪些执行上下文
            - 区分 window realm 和 worker realm
            - 结合 script 相关测试验证 realm 数量和类型
        """
        from .._bidi import script as bidi_script
        from .._units.script_tools import RealmInfo

        result = bidi_script.get_realms(
            self._driver._browser_driver, context=self._context_id, type_=type_
        )
        return [RealmInfo(i) for i in result.get("realms", [])]

    def disown_handles(self, handles):
        """释放当前 context 下的远程对象句柄。

        Args:
            handles: 远程对象句柄列表。
                常见值：``[result.result.handle]`` 或多个 handle 组成的列表。

        Returns:
            self: 原页面对象，便于链式调用。

        适用场景：
            - 手动释放 ``script.evaluate`` / ``callFunction`` 返回的远程对象句柄
            - 验证 ``script.disown`` 生命周期行为

        说明：
            - ``handle`` 可以理解为浏览器端某个远程 JS 对象的引用。
            - 当你不再需要这个远程对象时，调用本方法可以通知浏览器释放它。
        """
        from .._bidi import script as bidi_script

        bidi_script.disown(
            self._driver._browser_driver,
            handles=list(handles),
            target={"context": self._context_id},
        )
        return self

    def eval_handle(self, expression, await_promise=True):
        """执行脚本并以高层结果对象形式返回远程值。

        Args:
            expression: JavaScript 表达式字符串。
            await_promise: 是否等待 Promise resolve。

        Returns:
            ScriptResult: 支持 ``success/result.handle/result.value`` 属性访问的结果对象。

        适用场景：
            - 需要拿到 script 远程对象 handle 再做 ``disown``
            - 避免示例继续直接处理底层返回字典

        说明：
            - 如果表达式返回的是对象，通常会得到一个 ``handle``。
            - 如果表达式返回的是普通值，通常看 ``result.value`` 即可。
        """
        from .._bidi import script as bidi_script
        from .._units.script_tools import ScriptResult

        result = bidi_script.evaluate(
            self._driver._browser_driver,
            context=self._context_id,
            expression=expression,
            await_promise=await_promise,
        )
        return ScriptResult(result)

    # ===== 缓存控制 =====

    def set_cache_behavior(self, behavior="bypass"):
        """设置缓存行为

        Args:
            behavior: 'default' 正常缓存 / 'bypass' 绕过缓存

        Returns:
            self
        """
        from .._bidi import network as bidi_network

        try:
            bidi_network.set_cache_behavior(
                self._driver._browser_driver,
                behavior=behavior,
                contexts=[self._context_id],
            )
        except Exception as e:
            logger.debug("set_cache_behavior 失败: %s", e)
        return self

    # ===== 下载控制 =====

    def set_download_path(self, path):
        """设置当前页面下载目录。

        Args:
            path: 下载目录路径。
                单位：文件系统路径字符串。
                常见值：项目内绝对路径，例如 ``'E:/ruyipage/examples/downloads'``。

        Returns:
            self: 原页面对象，便于链式调用。

        适用场景：
            - 希望当前 tab 的下载直接落到指定目录
            - 兼容旧调用方式，但内部统一走 ``page.downloads``
        """
        try:
            return self.downloads.set_path(path)
        except Exception as e:
            logger.debug("set_download_path 失败: %s", e)
        return self
