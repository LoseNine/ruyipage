# -*- coding: utf-8 -*-

from unittest import mock

from ruyipage import FirefoxOptions, launch


def test_quick_start_sets_proxy():
    opts = FirefoxOptions()

    opts.quick_start(proxy="http://127.0.0.1:7890")

    assert opts.proxy == "http://127.0.0.1:7890"


def test_launch_forwards_proxy_to_options():
    created_opts = {}

    def fake_page(opts):
        created_opts["opts"] = opts
        return object()

    with mock.patch("ruyipage.FirefoxPage", side_effect=fake_page):
        launch(proxy="http://127.0.0.1:7890")

    opts = created_opts["opts"]
    assert opts.proxy == "http://127.0.0.1:7890"
