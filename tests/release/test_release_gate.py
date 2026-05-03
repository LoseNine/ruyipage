# -*- coding: utf-8 -*-
"""发版前最小通过集合。

这类测试本身不承载复杂逻辑，而是把当前版本最关键、最稳定、
对用户最有感知的能力收束成一组可快速执行的 release gate。
"""

import pytest


@pytest.mark.release
@pytest.mark.smoke
def test_release_gate_startup(page):
    page.get("about:blank")
    assert page.url == "about:blank"


@pytest.mark.release
@pytest.mark.local_server
def test_release_gate_navigation(page, server):
    page.get(server.get_url("/nav/basic"))
    assert page.title == "Nav Basic"


@pytest.mark.release
def test_release_gate_click_and_input(page, fixture_page_url):
    page.get(fixture_page_url("basic_form.html"))
    page.ele("#click-btn").click_self()
    page.ele("#text-input").input("release gate")
    page.ele("#mirror-btn").click_self()
    assert page.ele("#click-result").text == "clicked"
    assert page.ele("#mirror").text == "release gate"


@pytest.mark.release
@pytest.mark.local_server
def test_release_gate_response_body(page, server):
    captured = []

    def handler(req):
        if "/api/data" in req.url:
            req.continue_response()
            captured.append(req.response_body)
        else:
            req.continue_response()

    page.get("about:blank")
    page.intercept.start_responses(handler, collect_response=True)
    try:
        page.run_js(
            "fetch(arguments[0]).catch(()=>null); return true;",
            server.get_url("/api/data"),
            as_expr=False,
        )
        page.wait(0.8)
    finally:
        page.intercept.stop()

    assert captured
    assert '"status": "ok"' in (captured[0] or "")


@pytest.mark.release
def test_release_gate_storage(page, fixture_page_url):
    page.get(fixture_page_url("storage_fixture.html"))
    page.local_storage.clear()
    page.local_storage.set("rg", "pass")
    assert page.local_storage.get("rg") == "pass"


@pytest.mark.release
@pytest.mark.local_server
def test_release_gate_mock_response(page, server):
    def handler(req):
        if "/api/data" in req.url:
            req.mock(
                '{"gate": true}',
                headers={
                    "content-type": "application/json",
                    "access-control-allow-origin": "*",
                },
            )
        else:
            req.continue_request()

    page.get("about:blank")
    page.intercept.start_requests(handler)
    try:
        result = page.run_js(
            "return fetch(arguments[0]).then(r => r.json()).catch(e => ({error:String(e)}));",
            server.get_url("/api/data"),
            as_expr=False,
        )
    finally:
        page.intercept.stop()

    assert result.get("gate") is True


@pytest.mark.release
@pytest.mark.local_server
def test_release_gate_cookies(page, server):
    page.get(server.get_url("/set-cookie"))
    cookies = {c.name: c.value for c in page.get_cookies(all_info=True)}
    assert cookies.get("server_cookie") == "server_value"


@pytest.mark.release
def test_release_gate_private_mode(opts_factory, temp_user_dir):
    from ruyipage import FirefoxPage

    page = FirefoxPage(opts_factory(private=True, user_dir=temp_user_dir))
    try:
        page.get("about:blank")
        assert page.url == "about:blank"
    finally:
        page.quit()


@pytest.mark.release
def test_release_gate_actions_offset_no_accumulation(page, fixture_page_url):
    """1.2.5 修复回归：连续多次 move_to(offset_x=, offset_y=) 不应累加。"""
    page.set.viewport(1280, 900)
    page.get(fixture_page_url("fixed_point_target.html"))
    page.run_js("window.__resetClicks()")

    for _ in range(3):
        page.actions.move_to(offset_x=421, offset_y=637).click().perform()
        page.wait(0.2)

    clicks = page.run_js("return window.__clicks")
    assert len(clicks) == 3
    for rec in clicks:
        assert rec["x"] == 421 and rec["y"] == 637
        assert rec["target"] == "target"
