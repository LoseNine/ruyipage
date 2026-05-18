# -*- coding: utf-8 -*-
"""States - 状态查询"""


class PageStates(object):
    """页面状态查询

    用法::

        if page.states.is_loaded:
            ...
        if page.states.is_alive:
            ...
    """

    def __init__(self, owner):
        self._owner = owner

    @property
    def is_loaded(self):
        """页面是否加载完成"""
        try:
            return self._owner.ready_state == 'complete'
        except Exception:
            return False

    @property
    def is_alive(self):
        """连接是否正常"""
        try:
            self._owner.run_js('1')
            return True
        except Exception:
            return False

    @property
    def is_loading(self):
        """页面是否正在加载"""
        try:
            return self._owner.ready_state == 'loading'
        except Exception:
            return False

    @property
    def ready_state(self):
        """页面加载状态: loading / interactive / complete"""
        try:
            return self._owner.ready_state
        except Exception:
            return ''

    @property
    def has_alert(self):
        """是否有弹窗（只检测，不处理）"""
        try:
            # 用 getTree 检查 userPrompt 字段，不触发 handleUserPrompt
            result = self._owner._driver._browser_driver.run(
                'browsingContext.getTree',
                {'root': self._owner._context_id}
            )
            ctx = (result.get('contexts') or [{}])[0]
            return ctx.get('userPrompt') is not None
        except Exception:
            return False


class ElementStates(object):
    """元素状态查询

    用法::

        if ele.states.is_displayed:
            ...
        if ele.states.is_enabled:
            ...
    """

    def __init__(self, element):
        self._ele = element

    @property
    def is_displayed(self):
        """元素是否可见"""
        return self._ele.is_displayed

    @property
    def is_enabled(self):
        """元素是否可用"""
        return self._ele.is_enabled

    @property
    def is_checked(self):
        """是否选中"""
        return self._ele.is_checked

    @property
    def is_selected(self):
        """是否被选择（<option>）"""
        return self._ele._call_js_on_self('(el) => el.selected') or False

    @property
    def is_in_viewport(self):
        """元素是否在视口内"""
        return self._ele._call_js_on_self('''(el) => {
            const r = el.getBoundingClientRect();
            return r.top < window.innerHeight && r.bottom > 0
                && r.left < window.innerWidth && r.right > 0;
        }''') or False

    @property
    def is_whole_in_viewport(self):
        """元素是否完全在视口内"""
        return self._ele._call_js_on_self('''(el) => {
            const r = el.getBoundingClientRect();
            return r.top >= 0 && r.bottom <= window.innerHeight
                && r.left >= 0 && r.right <= window.innerWidth;
        }''') or False

    @property
    def has_rect(self):
        """元素是否有可视区域"""
        size = self._ele.size
        return size.get('width', 0) > 0 and size.get('height', 0) > 0
