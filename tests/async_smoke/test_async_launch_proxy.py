# -*- coding: utf-8 -*-

from unittest import mock

import pytest

import ruyipage.aio as aio


@pytest.mark.asyncio
async def test_async_launch_forwards_proxy_kwarg():
    called = {}

    async def fake_greenlet_spawn(func, **kwargs):
        called["func"] = func
        called["kwargs"] = kwargs

        class _Driver:
            async def switch_to_async(self):
                return None

        class _BrowserDriver:
            _browser_driver = _Driver()

        class _SyncPage:
            _driver = _BrowserDriver()

        return _SyncPage()

    class _AsyncPage:
        def __init__(self, sync):
            self.sync = sync

    with mock.patch("ruyipage.aio.greenlet_spawn", side_effect=fake_greenlet_spawn):
        with mock.patch("ruyipage.aio.AsyncFirefoxPage", _AsyncPage):
            await aio.launch(proxy="http://127.0.0.1:7890")

    assert called["kwargs"]["proxy"] == "http://127.0.0.1:7890"
