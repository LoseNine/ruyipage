# -*- coding: utf-8 -*-
"""RealmTracker - realm 生命周期追踪

订阅 script.realmCreated / script.realmDestroyed 事件。
"""
from .._bidi import session as bidi_session
from .._bidi import script as bidi_script

import logging

logger = logging.getLogger('ruyipage')


class RealmTracker:
    """realm 生命周期追踪

    用法::

        page.realms.start()
        page.realms.on_created(lambda r: print(r))
        realms = page.realms.list()
    """

    def __init__(self, owner):
        self._owner = owner
        self._realms = {}   # {realm_id: realm_info}
        self._created_cb = None
        self._destroyed_cb = None
        self._sub_id = None

    def start(self):
        drv = self._owner._driver._browser_driver
        try:
            r = bidi_session.subscribe(drv, ['script.realmCreated', 'script.realmDestroyed'],
                                       contexts=[self._owner._context_id])
            self._sub_id = r.get('subscription')
        except Exception as e:
            logger.debug("订阅 realm 事件失败: %s", e)
        self._owner._driver.set_global_callback('script.realmCreated', self._on_created)
        self._owner._driver.set_global_callback('script.realmDestroyed', self._on_destroyed)
        # 初始化当前 realms
        try:
            result = bidi_script.get_realms(drv, context=self._owner._context_id)
            for r in result.get('realms', []):
                self._realms[r['realm']] = r
        except Exception as e:
            logger.debug("初始化 realm 列表失败: %s", e)
        return self

    def stop(self):
        self._owner._driver.set_global_callback('script.realmCreated', None)
        self._owner._driver.set_global_callback('script.realmDestroyed', None)
        if self._sub_id:
            try:
                bidi_session.unsubscribe(self._owner._driver._browser_driver,
                                         subscription=self._sub_id)
            except Exception:
                pass
            self._sub_id = None

    def list(self):
        """返回当前所有已知 realm"""
        return list(self._realms.values())

    def on_created(self, callback):
        """注册 realm 创建回调 fn(realm_info)"""
        self._created_cb = callback
        return self

    def on_destroyed(self, callback):
        """注册 realm 销毁回调 fn(realm_id)"""
        self._destroyed_cb = callback
        return self

    def _on_created(self, params):
        ctx = params.get('context', '')
        if ctx and ctx != self._owner._context_id:
            return
        self._realms[params['realm']] = params
        if self._created_cb:
            try:
                self._created_cb(params)
            except Exception as e:
                logger.warning("realm 创建回调异常: %s", e)

    def _on_destroyed(self, params):
        rid = params.get('realm', '')
        self._realms.pop(rid, None)
        if self._destroyed_cb:
            try:
                self._destroyed_cb(rid)
            except Exception as e:
                logger.warning("realm 销毁回调异常: %s", e)
