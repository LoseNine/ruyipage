# -*- coding: utf-8 -*-
"""ruyipage 异步 API 入口

提供与同步 API 完全一致的接口，只需加 async/await。

安装::

    pip install ruyiPage[async]

用法::

    from ruyipage.aio import launch, attach

    async def main():
        page = await launch()
        await page.get("https://example.com")
        el = await page.ele("#search")
        await el.click_self()
        await page.quit()

    import asyncio
    asyncio.run(main())
"""

# ── 依赖检查 ──────────────────────────────────────────────────────────────

try:
    import greenlet  # noqa: F401
except ImportError:
    raise ImportError(
        "ruyipage 异步模式需要 greenlet 库。\n"
        "请安装: pip install ruyiPage[async]"
    )

try:
    import websockets  # noqa: F401
except ImportError:
    raise ImportError(
        "ruyipage 异步模式需要 websockets 库。\n"
        "请安装: pip install ruyiPage[async]"
    )

# ── 导入 ──────────────────────────────────────────────────────────────────

from ._async.greenlet_bridge import greenlet_spawn
from ._async._generated import (  # noqa: F401
    AsyncFirefoxPage,
    AsyncFirefoxTab,
    AsyncFirefoxFrame,
    AsyncFirefoxElement,
    AsyncNoneElement,
)

# 不需要异步包装的纯数据类型，直接重导出
from ._elements.static_element import StaticElement  # noqa: F401
from ._configs.firefox_options import FirefoxOptions  # noqa: F401
from ._functions.settings import Settings  # noqa: F401
from ._functions.keys import Keys  # noqa: F401
from ._functions.by import By  # noqa: F401
from .errors import *  # noqa: F401, F403


# ── 异步入口函数 ──────────────────────────────────────────────────────────


async def launch(**kwargs):
    """异步启动浏览器

    接口与 ``ruyipage.launch()`` 完全一致，返回 AsyncFirefoxPage。

    Args:
        **kwargs: 传递给 FirefoxOptions / ruyipage.launch() 的所有参数

    Returns:
        AsyncFirefoxPage: 异步页面对象
    """
    import ruyipage

    # 在 greenlet 中运行同步启动流程
    sync_page = await greenlet_spawn(ruyipage.launch, **kwargs)

    # 将底层驱动切换到异步模式
    driver = sync_page._driver._browser_driver
    await driver.switch_to_async()

    return AsyncFirefoxPage(sync_page)


async def attach(address, **kwargs):
    """异步连接已有浏览器

    接口与 ``ruyipage.attach()`` 完全一致，返回 AsyncFirefoxPage。

    Args:
        address: 浏览器地址，如 "127.0.0.1:9222"
        **kwargs: 传递给 ruyipage.attach() 的其他参数

    Returns:
        AsyncFirefoxPage: 异步页面对象
    """
    import ruyipage

    sync_page = await greenlet_spawn(ruyipage.attach, address, **kwargs)

    driver = sync_page._driver._browser_driver
    await driver.switch_to_async()

    return AsyncFirefoxPage(sync_page)
