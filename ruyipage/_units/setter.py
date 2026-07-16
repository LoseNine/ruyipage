# -*- coding: utf-8 -*-
"""Setter - 属性设置器"""


class PageSetter(object):
    """页面级属性设置器

    用法::

        page.set.cookies({'name': 'token', 'value': 'abc', 'domain': '.example.com'})
        page.set.useragent('Mozilla/5.0 ...')
        page.set.viewport(1920, 1080)
    """

    def __init__(self, owner):
        self._owner = owner

    def cookies(self, cookies):
        """设置 Cookie

        Args:
            cookies: Cookie 字典或列表
        """
        self._owner.set_cookies(cookies)
        return self._owner

    def useragent(self, ua):
        """设置 User-Agent"""
        self._owner.set_useragent(ua)
        return self._owner

    def viewport(self, width, height, device_pixel_ratio=None):
        """设置视口大小"""
        self._owner.set_viewport(width, height, device_pixel_ratio)
        return self._owner

    def window_size(self, width, height, device_pixel_ratio=None):
        """设置浏览器窗口外框尺寸。

        ``window.inner*`` / ``page.rect.viewport_size`` 由浏览器自然计算。
        如需模拟视口请使用 ``viewport()``；如需 hook ``screen.*`` 请使用
        ``page.emulation.set_screen_size()``。
        """
        return self._owner.set_window_size(
            width, height, device_pixel_ratio=device_pixel_ratio
        )

    def headers(self, headers):
        """设置额外请求头

        Args:
            headers: 头部字典 {'Header-Name': 'value'}
        """
        bidi_headers = []
        for name, value in headers.items():
            bidi_headers.append(
                {"name": name, "value": {"type": "string", "value": str(value)}}
            )

        self._owner._driver._browser_driver.run(
            "network.setExtraHeaders",
            {"headers": bidi_headers, "contexts": [self._owner._context_id]},
        )
        return self._owner

    def download_path(self, path):
        """设置当前页面下载路径。

        Args:
            path: 下载目录路径。
                单位：文件系统路径字符串。
                常见值：绝对路径，例如 ``'E:/ruyipage/examples/downloads'``。

        Returns:
            owner: 原页面对象，便于链式调用。

        适用场景：
            - 希望通过 ``page.set.download_path(...)`` 快速配置下载目录
            - 内部统一走 ``page.downloads``，避免多套参数结构
        """
        self._owner.downloads.set_path(path)
        return self._owner

    def bypass_csp(self, bypass=True):
        """设置是否绕过 CSP（通过 preload script 注入）"""
        self._owner.set_bypass_csp(bypass)
        return self._owner

    def scroll_bar(self, hide=True):
        """设置是否隐藏滚动条"""
        if hide:
            self._owner.run_js('document.documentElement.style.overflow = "hidden"')
        else:
            self._owner.run_js('document.documentElement.style.overflow = ""')
        return self._owner


class ElementSetter(object):
    """元素级属性设置器

    用法::

        ele.set.attr('data-id', '123')
        ele.set.prop('value', 'hello')
        ele.set.style('color', 'red')
    """

    def __init__(self, element):
        self._ele = element

    def attr(self, name, value):
        """设置属性"""
        self._ele._call_js_on_self(
            "(el, name, value) => el.setAttribute(name, value)", name, str(value)
        )
        return self._ele

    def remove_attr(self, name):
        """移除属性"""
        self._ele._call_js_on_self("(el, name) => el.removeAttribute(name)", name)
        return self._ele

    def prop(self, name, value):
        """设置 JS 属性"""
        self._ele._call_js_on_self(
            "(el, name, value) => { el[name] = value; }", name, value
        )
        return self._ele

    def style(self, name, value):
        """设置样式"""
        self._ele._call_js_on_self(
            "(el, name, value) => el.style.setProperty(name, value)", name, str(value)
        )
        return self._ele

    def inner_html(self, html):
        """设置内部 HTML"""
        self._ele._call_js_on_self("(el, html) => { el.innerHTML = html; }", html)
        return self._ele

    def value(self, val):
        """设置 value"""
        self._ele._call_js_on_self(
            '(el, val) => { el.value = val; el.dispatchEvent(new Event("input", {bubbles:true})); }',
            str(val),
        )
        return self._ele
