# -*- coding: utf-8 -*-

from ruyipage._bidi import browsing_context


class DummyDriver:
    def __init__(self):
        self.calls = []

    def run(self, method, params=None, **kwargs):
        self.calls.append((method, params, kwargs))
        return {"ok": True}


def test_start_screencast_sends_required_params_only():
    driver = DummyDriver()

    result = browsing_context.start_screencast(driver, "ctx-1")

    assert result == {"ok": True}
    assert driver.calls == [
        ("browsingContext.startScreencast", {"context": "ctx-1"}, {})
    ]


def test_start_screencast_sends_optional_params():
    driver = DummyDriver()
    stream_options = {
        "video": {"width": 1280, "height": 720, "frameRate": 30},
        "audio": False,
    }

    browsing_context.start_screencast(
        driver,
        "ctx-1",
        mime_type="video/webm",
        stream_options=stream_options,
    )

    assert driver.calls == [
        (
            "browsingContext.startScreencast",
            {
                "context": "ctx-1",
                "mimeType": "video/webm",
                "video": stream_options["video"],
                "audio": stream_options["audio"],
            },
            {},
        )
    ]


def test_stop_screencast_sends_screencast_id():
    driver = DummyDriver()

    result = browsing_context.stop_screencast(driver, "screencast-1")

    assert result == {"ok": True}
    assert driver.calls == [
        (
            "browsingContext.stopScreencast",
            {"screencast": "screencast-1"},
            {},
        )
    ]


def test_create_sends_user_context_when_provided():
    driver = DummyDriver()

    browsing_context.create(
        driver,
        type_="tab",
        background=True,
        user_context="uc-1",
        reference_context="root-ctx",
    )

    assert driver.calls == [
        (
            "browsingContext.create",
            {
                "type": "tab",
                "referenceContext": "root-ctx",
                "background": True,
                "userContext": "uc-1",
            },
            {},
        )
    ]
