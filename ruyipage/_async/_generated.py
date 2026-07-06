# -*- coding: utf-8 -*-
# ┌──────────────────────────────────────────────────────────────────┐
# │ WARNING: 此文件由 scripts/generate_async_api.py 自动生成          │
# │ 请勿手动编辑！修改后请重新运行生成器：                               │
# │   python scripts/generate_async_api.py                          │
# │ 生成时间: 2026-07-06 21:06:09                                        │
# └──────────────────────────────────────────────────────────────────┘

from .greenlet_bridge import greenlet_spawn
from ._overrides import AsyncFirefoxBaseMixin, AsyncFirefoxElementMixin



class AsyncUnitProxy:
    """通用异步 Unit 代理

    包装任何 unit 对象（Actions, Interceptor, Listener 等），
    将所有公共方法自动包装为异步版本。
    """

    def __init__(self, sync_unit, owner=None):
        self._sync = sync_unit
        self._owner = owner

    def __getattr__(self, name):
        if name.startswith("_"):
            return getattr(self._sync, name)

        attr = getattr(self._sync, name)

        if callable(attr):
            async def _async_method(*args, **kwargs):
                _r = await greenlet_spawn(attr, *args, **kwargs)
                return _wrap_async_result(_r, owner=self._owner, unit_proxy=self)
            _async_method.__name__ = name
            _async_method.__qualname__ = "AsyncUnitProxy.{}".format(name)
            return _async_method

        # 非 callable 属性（如 .active, .listening）直接返回
        return attr

    async def __call__(self, *args, **kwargs):
        """支持可调用的 unit（如 PageWaiter.__call__、ElementWaiter.__call__）"""
        _r = await greenlet_spawn(self._sync, *args, **kwargs)
        return _wrap_async_result(_r, owner=self._owner, unit_proxy=self)

    def __repr__(self):
        return "<Async{}>".format(repr(self._sync))




class AsyncNoneElement:
    """NoneElement 的异步对应 —— 空元素的 null 对象"""

    def __init__(self, sync_obj=None):
        self._sync = sync_obj

    def __bool__(self):
        return False

    def __repr__(self):
        return "<AsyncNoneElement>"

    def __str__(self):
        return self.__repr__()

    async def __getattr__(self, name):
        return None




def _wrap_async_result(value, owner=None, unit_proxy=None):
    """Wrap sync ruyiPage objects returned through async proxies."""
    if value is None:
        return None
    if unit_proxy is not None and value is getattr(unit_proxy, "_sync", None):
        return unit_proxy
    if owner is not None:
        sync_owner = getattr(owner, "_sync", None)
        if value is sync_owner:
            return owner
    value_type = getattr(value, "_type", None)
    if value_type == "FirefoxPage":
        return AsyncFirefoxPage(value)
    if value_type == "FirefoxTab":
        return AsyncFirefoxTab(value)
    if value_type == "FirefoxFrame":
        return AsyncFirefoxFrame(value)
    if value_type == "FirefoxElement":
        return AsyncFirefoxElement(value)
    if value_type == "NoneElement":
        return AsyncNoneElement(value)
    return value



class AsyncFirefoxBase(AsyncFirefoxBaseMixin):
    """FirefoxBase的异步代理"""

    def __init__(self, sync_obj):
        self._sync = sync_obj
        self._unit_cache = {}

    @property
    def browser(self):
        return self._sync.browser

    @property
    def tab_id(self):
        return self._sync.tab_id

    async def get_cookies(self):
        _r = await greenlet_spawn(lambda: self._sync.cookies)
        return _wrap_async_result(_r, self)

    async def get_html(self):
        _r = await greenlet_spawn(lambda: self._sync.html)
        return _wrap_async_result(_r, self)

    async def get_ready_state(self):
        _r = await greenlet_spawn(lambda: self._sync.ready_state)
        return _wrap_async_result(_r, self)

    async def get_title(self):
        _r = await greenlet_spawn(lambda: self._sync.title)
        return _wrap_async_result(_r, self)

    async def get_url(self):
        _r = await greenlet_spawn(lambda: self._sync.url)
        return _wrap_async_result(_r, self)

    async def get_user_agent(self):
        _r = await greenlet_spawn(lambda: self._sync.user_agent)
        return _wrap_async_result(_r, self)

    @property
    def actions(self):
        if "actions" not in self._unit_cache:
            self._unit_cache["actions"] = AsyncUnitProxy(self._sync.actions, owner=self)
        return self._unit_cache["actions"]

    @property
    def browser_tools(self):
        if "browser_tools" not in self._unit_cache:
            self._unit_cache["browser_tools"] = AsyncUnitProxy(self._sync.browser_tools, owner=self)
        return self._unit_cache["browser_tools"]

    @property
    def capture(self):
        if "capture" not in self._unit_cache:
            self._unit_cache["capture"] = AsyncUnitProxy(self._sync.capture, owner=self)
        return self._unit_cache["capture"]

    @property
    def config(self):
        if "config" not in self._unit_cache:
            self._unit_cache["config"] = AsyncUnitProxy(self._sync.config, owner=self)
        return self._unit_cache["config"]

    @property
    def console(self):
        if "console" not in self._unit_cache:
            self._unit_cache["console"] = AsyncUnitProxy(self._sync.console, owner=self)
        return self._unit_cache["console"]

    @property
    def contexts(self):
        if "contexts" not in self._unit_cache:
            self._unit_cache["contexts"] = AsyncUnitProxy(self._sync.contexts, owner=self)
        return self._unit_cache["contexts"]

    @property
    def downloads(self):
        if "downloads" not in self._unit_cache:
            self._unit_cache["downloads"] = AsyncUnitProxy(self._sync.downloads, owner=self)
        return self._unit_cache["downloads"]

    @property
    def emulation(self):
        if "emulation" not in self._unit_cache:
            self._unit_cache["emulation"] = AsyncUnitProxy(self._sync.emulation, owner=self)
        return self._unit_cache["emulation"]

    @property
    def events(self):
        if "events" not in self._unit_cache:
            self._unit_cache["events"] = AsyncUnitProxy(self._sync.events, owner=self)
        return self._unit_cache["events"]

    @property
    def extensions(self):
        if "extensions" not in self._unit_cache:
            self._unit_cache["extensions"] = AsyncUnitProxy(self._sync.extensions, owner=self)
        return self._unit_cache["extensions"]

    @property
    def intercept(self):
        if "intercept" not in self._unit_cache:
            self._unit_cache["intercept"] = AsyncUnitProxy(self._sync.intercept, owner=self)
        return self._unit_cache["intercept"]

    @property
    def listen(self):
        if "listen" not in self._unit_cache:
            self._unit_cache["listen"] = AsyncUnitProxy(self._sync.listen, owner=self)
        return self._unit_cache["listen"]

    @property
    def local_storage(self):
        if "local_storage" not in self._unit_cache:
            self._unit_cache["local_storage"] = AsyncUnitProxy(self._sync.local_storage, owner=self)
        return self._unit_cache["local_storage"]

    @property
    def navigation(self):
        if "navigation" not in self._unit_cache:
            self._unit_cache["navigation"] = AsyncUnitProxy(self._sync.navigation, owner=self)
        return self._unit_cache["navigation"]

    @property
    def network(self):
        if "network" not in self._unit_cache:
            self._unit_cache["network"] = AsyncUnitProxy(self._sync.network, owner=self)
        return self._unit_cache["network"]

    @property
    def prefs(self):
        if "prefs" not in self._unit_cache:
            self._unit_cache["prefs"] = AsyncUnitProxy(self._sync.prefs, owner=self)
        return self._unit_cache["prefs"]

    @property
    def prompts(self):
        if "prompts" not in self._unit_cache:
            self._unit_cache["prompts"] = AsyncUnitProxy(self._sync.prompts, owner=self)
        return self._unit_cache["prompts"]

    @property
    def realms(self):
        if "realms" not in self._unit_cache:
            self._unit_cache["realms"] = AsyncUnitProxy(self._sync.realms, owner=self)
        return self._unit_cache["realms"]

    @property
    def rect(self):
        if "rect" not in self._unit_cache:
            self._unit_cache["rect"] = AsyncUnitProxy(self._sync.rect, owner=self)
        return self._unit_cache["rect"]

    @property
    def scroll(self):
        if "scroll" not in self._unit_cache:
            self._unit_cache["scroll"] = AsyncUnitProxy(self._sync.scroll, owner=self)
        return self._unit_cache["scroll"]

    @property
    def session_storage(self):
        if "session_storage" not in self._unit_cache:
            self._unit_cache["session_storage"] = AsyncUnitProxy(self._sync.session_storage, owner=self)
        return self._unit_cache["session_storage"]

    @property
    def set(self):
        if "set" not in self._unit_cache:
            self._unit_cache["set"] = AsyncUnitProxy(self._sync.set, owner=self)
        return self._unit_cache["set"]

    @property
    def states(self):
        if "states" not in self._unit_cache:
            self._unit_cache["states"] = AsyncUnitProxy(self._sync.states, owner=self)
        return self._unit_cache["states"]

    @property
    def touch(self):
        if "touch" not in self._unit_cache:
            self._unit_cache["touch"] = AsyncUnitProxy(self._sync.touch, owner=self)
        return self._unit_cache["touch"]

    @property
    def trace(self):
        if "trace" not in self._unit_cache:
            self._unit_cache["trace"] = AsyncUnitProxy(self._sync.trace, owner=self)
        return self._unit_cache["trace"]

    @property
    def wait(self):
        if "wait" not in self._unit_cache:
            self._unit_cache["wait"] = AsyncUnitProxy(self._sync.wait, owner=self)
        return self._unit_cache["wait"]

    @property
    def window(self):
        if "window" not in self._unit_cache:
            self._unit_cache["window"] = AsyncUnitProxy(self._sync.window, owner=self)
        return self._unit_cache["window"]

    async def accept_alert(self, text=None, timeout=3):
        _r = await greenlet_spawn(self._sync.accept_alert, text=text, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def accept_prompt(self, text=None, timeout=3):
        _r = await greenlet_spawn(self._sync.accept_prompt, text=text, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def add_preload_script(self, script):
        _r = await greenlet_spawn(self._sync.add_preload_script, script)
        return _wrap_async_result(_r, self)

    async def back(self):
        return await self._run_serialized_navigation('back', )

    async def clear_prompt_handler(self):
        _r = await greenlet_spawn(self._sync.clear_prompt_handler, )
        return _wrap_async_result(_r, self)

    async def delete_cookies(self, name=None, domain=None):
        _r = await greenlet_spawn(self._sync.delete_cookies, name=name, domain=domain)
        return _wrap_async_result(_r, self)

    async def dismiss_alert(self, timeout=3):
        _r = await greenlet_spawn(self._sync.dismiss_alert, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def dismiss_prompt(self, timeout=3):
        _r = await greenlet_spawn(self._sync.dismiss_prompt, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def disown_handles(self, handles):
        _r = await greenlet_spawn(self._sync.disown_handles, handles)
        return _wrap_async_result(_r, self)

    async def ele(self, locator, index=1, timeout=None):
        _r = await greenlet_spawn(self._sync.ele, locator, index=index, timeout=timeout)
        return AsyncFirefoxElement(_r) if _r else AsyncNoneElement(_r)

    async def eles(self, locator, timeout=None):
        _r = await greenlet_spawn(self._sync.eles, locator, timeout=timeout)
        return [AsyncFirefoxElement(e) for e in _r]

    async def eval_handle(self, expression, await_promise=True):
        _r = await greenlet_spawn(self._sync.eval_handle, expression, await_promise=await_promise)
        return _wrap_async_result(_r, self)

    async def forward(self):
        return await self._run_serialized_navigation('forward', )

    async def get(self, url, wait=None, timeout=None):
        return await self._run_serialized_navigation('get', url, wait=wait, timeout=timeout)

    async def get_all_frames(self):
        _r = await greenlet_spawn(self._sync.get_all_frames, )
        return [AsyncFirefoxFrame(f) for f in _r]

    async def get_cookies(self, all_info=False):
        _r = await greenlet_spawn(self._sync.get_cookies, all_info=all_info)
        return _wrap_async_result(_r, self)

    async def get_cookies_filtered(self, name=None, domain=None, all_info=True):
        _r = await greenlet_spawn(self._sync.get_cookies_filtered, name=name, domain=domain, all_info=all_info)
        return _wrap_async_result(_r, self)

    async def get_frame(self, locator=None, index=None, context_id=None):
        _r = await greenlet_spawn(self._sync.get_frame, locator=locator, index=index, context_id=context_id)
        return AsyncFirefoxFrame(_r) if _r else _r

    async def get_frames(self):
        _r = await greenlet_spawn(self._sync.get_frames, )
        return [AsyncFirefoxFrame(f) for f in _r]

    async def get_last_prompt_closed(self):
        _r = await greenlet_spawn(self._sync.get_last_prompt_closed, )
        return _wrap_async_result(_r, self)

    async def get_last_prompt_opened(self):
        _r = await greenlet_spawn(self._sync.get_last_prompt_opened, )
        return _wrap_async_result(_r, self)

    async def get_realms(self, type_=None):
        _r = await greenlet_spawn(self._sync.get_realms, type_=type_)
        return _wrap_async_result(_r, self)

    async def get_user_prompt(self):
        _r = await greenlet_spawn(self._sync.get_user_prompt, )
        return _wrap_async_result(_r, self)

    async def handle_alert(self, action='accept', text=None, timeout=3):
        _r = await greenlet_spawn(self._sync.handle_alert, action=action, text=text, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def handle_cloudflare_challenge(self, timeout=30, check_interval=2):
        _r = await greenlet_spawn(self._sync.handle_cloudflare_challenge, timeout=timeout, check_interval=check_interval)
        return _wrap_async_result(_r, self)

    async def handle_prompt(self, accept=True, text=None, timeout=3):
        _r = await greenlet_spawn(self._sync.handle_prompt, accept=accept, text=text, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def input_prompt(self, text, timeout=3):
        _r = await greenlet_spawn(self._sync.input_prompt, text, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def is_trusted(self, event_key):
        _r = await greenlet_spawn(self._sync.is_trusted, event_key)
        return _wrap_async_result(_r, self)

    async def pdf(self, path=None, **kwargs):
        _r = await greenlet_spawn(self._sync.pdf, path=path, **kwargs)
        return _wrap_async_result(_r, self)

    async def prompt_login(self, trigger_locator, username, password, trigger='mouse', timeout=3):
        _r = await greenlet_spawn(self._sync.prompt_login, trigger_locator, username, password, trigger=trigger, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def refresh(self, ignore_cache=False):
        return await self._run_serialized_navigation('refresh', ignore_cache=ignore_cache)

    async def remove_preload_script(self, script_id):
        _r = await greenlet_spawn(self._sync.remove_preload_script, script_id)
        return _wrap_async_result(_r, self)

    async def respond_prompt(self, accept=True, text=None, timeout=3):
        _r = await greenlet_spawn(self._sync.respond_prompt, accept=accept, text=text, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def run_js(self, script, *args, as_expr=None, timeout=None, sandbox=None):
        _r = await greenlet_spawn(self._sync.run_js, script, *args, as_expr=as_expr, timeout=timeout, sandbox=sandbox)
        return _wrap_async_result(_r, self)

    async def run_js_loaded(self, script, *args, as_expr=None, timeout=None):
        _r = await greenlet_spawn(self._sync.run_js_loaded, script, *args, as_expr=as_expr, timeout=timeout)
        return _wrap_async_result(_r, self)

    async def s_ele(self, locator=None):
        _r = await greenlet_spawn(self._sync.s_ele, locator=locator)
        return _r  # StaticElement, no async wrapper needed

    async def s_eles(self, locator):
        _r = await greenlet_spawn(self._sync.s_eles, locator)
        return _r  # list[StaticElement]

    async def save_pdf(self, path, **kwargs):
        _r = await greenlet_spawn(self._sync.save_pdf, path, **kwargs)
        return _wrap_async_result(_r, self)

    async def screenshot(self, path=None, full_page=False, as_bytes=None, as_base64=None):
        _r = await greenlet_spawn(self._sync.screenshot, path=path, full_page=full_page, as_bytes=as_bytes, as_base64=as_base64)
        return _wrap_async_result(_r, self)

    async def set_bypass_csp(self, bypass=True):
        await greenlet_spawn(self._sync.set_bypass_csp, bypass=bypass)
        return self

    async def set_cache_behavior(self, behavior='bypass'):
        await greenlet_spawn(self._sync.set_cache_behavior, behavior=behavior)
        return self

    async def set_cookies(self, cookies, domain=None, path=None):
        _r = await greenlet_spawn(self._sync.set_cookies, cookies, domain=domain, path=path)
        return _wrap_async_result(_r, self)

    async def set_download_path(self, path):
        await greenlet_spawn(self._sync.set_download_path, path)
        return self

    async def set_geolocation(self, latitude, longitude, accuracy=100):
        await greenlet_spawn(self._sync.set_geolocation, latitude, longitude, accuracy=accuracy)
        return self

    async def set_locale(self, locales):
        await greenlet_spawn(self._sync.set_locale, locales)
        return self

    async def set_prompt_handler(self, alert='accept', confirm='accept', prompt='ignore', default='accept', prompt_text=None):
        _r = await greenlet_spawn(self._sync.set_prompt_handler, alert=alert, confirm=confirm, prompt=prompt, default=default, prompt_text=prompt_text)
        return _wrap_async_result(_r, self)

    async def set_screen_orientation(self, orientation_type, angle=0):
        await greenlet_spawn(self._sync.set_screen_orientation, orientation_type, angle=angle)
        return self

    async def set_timezone(self, timezone_id):
        await greenlet_spawn(self._sync.set_timezone, timezone_id)
        return self

    async def set_useragent(self, ua):
        await greenlet_spawn(self._sync.set_useragent, ua)
        return self

    async def set_viewport(self, width, height, device_pixel_ratio=None):
        await greenlet_spawn(self._sync.set_viewport, width, height, device_pixel_ratio=device_pixel_ratio)
        return self

    async def shadow_roots(self, mode='all', include_frames=True):
        _r = await greenlet_spawn(self._sync.shadow_roots, mode=mode, include_frames=include_frames)
        return [AsyncFirefoxElement(e) for e in _r]

    async def stop_loading(self):
        await greenlet_spawn(self._sync.stop_loading, )
        return self

    async def trigger_prompt_target(self, locator, trigger='mouse'):
        _r = await greenlet_spawn(self._sync.trigger_prompt_target, locator, trigger=trigger)
        return _wrap_async_result(_r, self)

    async def wait_loading(self, timeout=None):
        await greenlet_spawn(self._sync.wait_loading, timeout=timeout)
        return self

    async def wait_prompt(self, timeout=3):
        _r = await greenlet_spawn(self._sync.wait_prompt, timeout=timeout)
        return _wrap_async_result(_r, self)



class AsyncFirefoxPage(AsyncFirefoxBase):
    """FirefoxPage 的异步代理"""

    async def close(self):
        _r = await greenlet_spawn(self._sync.close, )
        return _wrap_async_result(_r, self)

    async def close_other_tabs(self, tab_or_ids=None):
        _r = await greenlet_spawn(self._sync.close_other_tabs, tab_or_ids=tab_or_ids)
        return _wrap_async_result(_r, self)

    async def get_tab(self, id_or_num=None, title=None, url=None):
        _r = await greenlet_spawn(self._sync.get_tab, id_or_num=id_or_num, title=title, url=url)
        return AsyncFirefoxTab(_r) if _r else _r

    async def get_tabs(self, title=None, url=None):
        _r = await greenlet_spawn(self._sync.get_tabs, title=title, url=url)
        return [AsyncFirefoxTab(t) for t in _r]

    async def get_latest_tab(self):
        _r = await greenlet_spawn(lambda: self._sync.latest_tab)
        return AsyncFirefoxTab(_r) if _r else _r

    async def new_container_tab(self, url=None, background=False):
        _r = await greenlet_spawn(self._sync.new_container_tab, url=url, background=background)
        return AsyncFirefoxTab(_r) if _r else _r

    async def new_container_tabs(self, count, url=None, background=False):
        _r = await greenlet_spawn(self._sync.new_container_tabs, count, url=url, background=background)
        return [AsyncFirefoxTab(t) for t in _r]

    async def new_tab(self, url=None, background=False, user_context=None):
        _r = await greenlet_spawn(self._sync.new_tab, url=url, background=background, user_context=user_context)
        return AsyncFirefoxTab(_r) if _r else _r

    async def quit(self, timeout=5, force=False):
        _r = await greenlet_spawn(self._sync.quit, timeout=timeout, force=force)
        return _wrap_async_result(_r, self)

    async def save(self, path=None, name=None, as_pdf=False):
        _r = await greenlet_spawn(self._sync.save, path=path, name=name, as_pdf=as_pdf)
        return _wrap_async_result(_r, self)

    async def get_tab_ids(self):
        _r = await greenlet_spawn(lambda: self._sync.tab_ids)
        return _wrap_async_result(_r, self)

    async def get_tabs_count(self):
        _r = await greenlet_spawn(lambda: self._sync.tabs_count)
        return _wrap_async_result(_r, self)



class AsyncFirefoxTab(AsyncFirefoxBase):
    """FirefoxTab 的异步代理"""

    async def activate(self):
        await greenlet_spawn(self._sync.activate, )
        return self

    async def close(self, others=False):
        _r = await greenlet_spawn(self._sync.close, others=others)
        return _wrap_async_result(_r, self)

    async def save(self, path=None, name=None, as_pdf=False):
        _r = await greenlet_spawn(self._sync.save, path=path, name=name, as_pdf=as_pdf)
        return _wrap_async_result(_r, self)



class AsyncFirefoxFrame(AsyncFirefoxBase):
    """FirefoxFrame 的异步代理"""

    async def get_is_cross_origin(self):
        _r = await greenlet_spawn(lambda: self._sync.is_cross_origin)
        return _wrap_async_result(_r, self)

    async def get_parent(self):
        _r = await greenlet_spawn(lambda: self._sync.parent)
        return _wrap_async_result(_r, self)



class AsyncFirefoxElement(AsyncFirefoxElementMixin):
    """FirefoxElement的异步代理"""

    def __init__(self, sync_obj):
        self._sync = sync_obj
        self._unit_cache = {}

    async def get_html(self):
        _r = await greenlet_spawn(lambda: self._sync.html)
        return _wrap_async_result(_r, self)

    @property
    def click(self):
        if "click" not in self._unit_cache:
            self._unit_cache["click"] = AsyncUnitProxy(self._sync.click, owner=self)
        return self._unit_cache["click"]

    @property
    def rect(self):
        if "rect" not in self._unit_cache:
            self._unit_cache["rect"] = AsyncUnitProxy(self._sync.rect, owner=self)
        return self._unit_cache["rect"]

    @property
    def scroll(self):
        if "scroll" not in self._unit_cache:
            self._unit_cache["scroll"] = AsyncUnitProxy(self._sync.scroll, owner=self)
        return self._unit_cache["scroll"]

    @property
    def select(self):
        if "select" not in self._unit_cache:
            self._unit_cache["select"] = AsyncUnitProxy(self._sync.select, owner=self)
        return self._unit_cache["select"]

    @property
    def set(self):
        if "set" not in self._unit_cache:
            self._unit_cache["set"] = AsyncUnitProxy(self._sync.set, owner=self)
        return self._unit_cache["set"]

    @property
    def states(self):
        if "states" not in self._unit_cache:
            self._unit_cache["states"] = AsyncUnitProxy(self._sync.states, owner=self)
        return self._unit_cache["states"]

    @property
    def wait(self):
        if "wait" not in self._unit_cache:
            self._unit_cache["wait"] = AsyncUnitProxy(self._sync.wait, owner=self)
        return self._unit_cache["wait"]

    async def get_attrs(self):
        _r = await greenlet_spawn(lambda: self._sync.attrs)
        return _wrap_async_result(_r, self)

    async def get_closed_shadow_root(self):
        _r = await greenlet_spawn(lambda: self._sync.closed_shadow_root)
        return _wrap_async_result(_r, self)

    async def get_inner_html(self):
        _r = await greenlet_spawn(lambda: self._sync.inner_html)
        return _wrap_async_result(_r, self)

    async def get_is_checked(self):
        _r = await greenlet_spawn(lambda: self._sync.is_checked)
        return _wrap_async_result(_r, self)

    async def get_is_displayed(self):
        _r = await greenlet_spawn(lambda: self._sync.is_displayed)
        return _wrap_async_result(_r, self)

    async def get_is_enabled(self):
        _r = await greenlet_spawn(lambda: self._sync.is_enabled)
        return _wrap_async_result(_r, self)

    async def get_link(self):
        _r = await greenlet_spawn(lambda: self._sync.link)
        return _wrap_async_result(_r, self)

    async def get_location(self):
        _r = await greenlet_spawn(lambda: self._sync.location)
        return _wrap_async_result(_r, self)

    async def get_outer_html(self):
        _r = await greenlet_spawn(lambda: self._sync.outer_html)
        return _wrap_async_result(_r, self)

    async def get_pseudo(self):
        _r = await greenlet_spawn(lambda: self._sync.pseudo)
        return _wrap_async_result(_r, self)

    async def get_shadow_root(self):
        _r = await greenlet_spawn(lambda: self._sync.shadow_root)
        return _wrap_async_result(_r, self)

    async def get_size(self):
        _r = await greenlet_spawn(lambda: self._sync.size)
        return _wrap_async_result(_r, self)

    async def get_src(self):
        _r = await greenlet_spawn(lambda: self._sync.src)
        return _wrap_async_result(_r, self)

    async def get_tag(self):
        _r = await greenlet_spawn(lambda: self._sync.tag)
        return _wrap_async_result(_r, self)

    async def get_text(self):
        _r = await greenlet_spawn(lambda: self._sync.text)
        return _wrap_async_result(_r, self)

    async def get_value(self):
        _r = await greenlet_spawn(lambda: self._sync.value)
        return _wrap_async_result(_r, self)

    async def attr(self, name):
        _r = await greenlet_spawn(self._sync.attr, name)
        return _wrap_async_result(_r, self)

    async def child(self, locator=None, index=1, timeout=None):
        _r = await greenlet_spawn(self._sync.child, locator=locator, index=index, timeout=timeout)
        return AsyncFirefoxElement(_r) if _r else AsyncNoneElement(_r)

    async def children(self, locator=None, timeout=None):
        _r = await greenlet_spawn(self._sync.children, locator=locator, timeout=timeout)
        return [AsyncFirefoxElement(e) for e in _r]

    async def clear(self):
        await greenlet_spawn(self._sync.clear, )
        return self

    async def click_self(self, by_js=False, timeout=1.5):
        await greenlet_spawn(self._sync.click_self, by_js=by_js, timeout=timeout)
        return self

    async def double_click(self):
        await greenlet_spawn(self._sync.double_click, )
        return self

    async def drag_to(self, target, duration=0.5):
        await greenlet_spawn(self._sync.drag_to, target, duration=duration)
        return self

    async def ele(self, locator, index=1, timeout=None):
        _r = await greenlet_spawn(self._sync.ele, locator, index=index, timeout=timeout)
        return AsyncFirefoxElement(_r) if _r else AsyncNoneElement(_r)

    async def eles(self, locator, timeout=None):
        _r = await greenlet_spawn(self._sync.eles, locator, timeout=timeout)
        return [AsyncFirefoxElement(e) for e in _r]

    async def focus(self):
        await greenlet_spawn(self._sync.focus, )
        return self

    async def hover(self):
        await greenlet_spawn(self._sync.hover, )
        return self

    async def input(self, text, clear=True, by_js=False):
        await greenlet_spawn(self._sync.input, text, clear=clear, by_js=by_js)
        return self

    async def next(self, locator=None, index=1):
        _r = await greenlet_spawn(self._sync.next, locator=locator, index=index)
        return AsyncFirefoxElement(_r) if _r else AsyncNoneElement(_r)

    async def parent(self, locator=None, index=1):
        _r = await greenlet_spawn(self._sync.parent, locator=locator, index=index)
        return AsyncFirefoxElement(_r) if _r else AsyncNoneElement(_r)

    async def prev(self, locator=None, index=1):
        _r = await greenlet_spawn(self._sync.prev, locator=locator, index=index)
        return AsyncFirefoxElement(_r) if _r else AsyncNoneElement(_r)

    async def property(self, name):
        _r = await greenlet_spawn(self._sync.property, name)
        return _wrap_async_result(_r, self)

    async def right_click(self):
        await greenlet_spawn(self._sync.right_click, )
        return self

    async def run_js(self, script, *args):
        _r = await greenlet_spawn(self._sync.run_js, script, *args)
        return _wrap_async_result(_r, self)

    async def s_ele(self, locator=None):
        _r = await greenlet_spawn(self._sync.s_ele, locator=locator)
        return _r  # StaticElement, no async wrapper needed

    async def screenshot(self, path=None, as_bytes=None, as_base64=None):
        _r = await greenlet_spawn(self._sync.screenshot, path=path, as_bytes=as_bytes, as_base64=as_base64)
        return _wrap_async_result(_r, self)

    async def style(self, name, pseudo=''):
        _r = await greenlet_spawn(self._sync.style, name, pseudo=pseudo)
        return _wrap_async_result(_r, self)
