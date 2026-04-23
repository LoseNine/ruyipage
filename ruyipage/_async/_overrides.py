# -*- coding: utf-8 -*-
"""手写的异步覆盖方法

代码生成器无法自动处理的特殊方法（上下文管理器、__repr__ 等），
通过 Mixin 混入到生成的 Async 类中。
"""

import asyncio
from contextlib import asynccontextmanager
from .greenlet_bridge import greenlet_spawn


class AsyncFirefoxBaseMixin:
    """混入 AsyncFirefoxBase —— 手写的特殊方法"""

    def _get_async_nav_lock(self):
        """返回绑定到底层同步 page/frame 对象的导航锁。"""
        lock = getattr(self._sync, "_async_nav_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._sync._async_nav_lock = lock
        return lock

    async def _run_serialized_navigation(self, method_name, *args, **kwargs):
        """串行化同一页面对象上的异步导航类调用。"""
        async with self._get_async_nav_lock():
            await greenlet_spawn(getattr(self._sync, method_name), *args, **kwargs)
        return self

    @asynccontextmanager
    async def with_frame(self, locator=None, index=None, context_id=None):
        """异步版 with_frame 上下文管理器

        用法::

            async with page.with_frame("#my-iframe") as frame:
                el = await frame.ele("#inner")
        """
        frame = await self.get_frame(
            locator=locator, index=index, context_id=context_id
        )
        if frame is None:
            raise RuntimeError("未找到目标 iframe/frame")
        yield frame

    def __repr__(self):
        return "<Async{} {}>".format(
            self._sync._type, getattr(self._sync, "url", "")
        )

    def __str__(self):
        return self.__repr__()


class AsyncFirefoxElementMixin:
    """混入 AsyncFirefoxElement —— 手写的特殊方法"""

    @asynccontextmanager
    async def with_shadow(self, mode="open"):
        """异步版 with_shadow 上下文管理器

        用法::

            async with element.with_shadow() as root:
                inner = await root.ele(".shadow-child")
        """
        if mode == "open":
            root = await greenlet_spawn(lambda: self._sync.shadow_root)
        else:
            root = await greenlet_spawn(lambda: self._sync.closed_shadow_root)
        if root is None:
            raise RuntimeError("未找到 Shadow Root")
        # 避免循环导入，使用延迟导入
        from ._generated import AsyncFirefoxElement

        yield AsyncFirefoxElement(root)

    def __bool__(self):
        return True

    def __repr__(self):
        return "<Async{}>".format(repr(self._sync))

    def __str__(self):
        return self.__repr__()
