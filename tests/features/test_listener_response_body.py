# -*- coding: utf-8 -*-

import pytest


@pytest.mark.feature
@pytest.mark.local_server
def test_listener_packet_can_read_response_text(page, server):
    page.get("about:blank")
    page.listen.start("/api/data")

    try:
        result = page.run_js(
            """
            return fetch(arguments[0])
              .then(r => r.text())
              .catch(e => String(e));
            """,
            server.get_url("/api/data"),
            as_expr=False,
        )
        packet = page.listen.wait(timeout=8)
        assert packet is not None
        assert packet.status == 200
        assert '"status": "ok"' in (packet.text or "")
        assert packet.response_body == packet.text
        assert '"status": "ok"' in result
    finally:
        page.listen.stop()


@pytest.mark.feature
@pytest.mark.local_server
def test_listener_packet_keeps_response_text_after_stop(page, server):
    page.get("about:blank")
    page.listen.start("/api/data")

    page.run_js(
        "fetch(arguments[0]).catch(() => null); return true;",
        server.get_url("/api/data"),
        as_expr=False,
    )
    packet = page.listen.wait(timeout=8)
    assert packet is not None

    page.listen.stop()

    assert '"status": "ok"' in (packet.text or "")


@pytest.mark.feature
@pytest.mark.local_server
def test_listener_can_disable_response_collection(page, server):
    page.get("about:blank")
    page.listen.start("/api/data", collect_response=False)

    try:
        page.run_js(
            "fetch(arguments[0]).catch(() => null); return true;",
            server.get_url("/api/data"),
            as_expr=False,
        )
        packet = page.listen.wait(timeout=8)
        assert packet is not None
        assert packet.response_body is None
        assert packet.text is None
    finally:
        page.listen.stop()
