# -*- coding: utf-8 -*-
"""Regression tests for screen-size emulation fallback."""

from ruyipage._units.emulation import EmulationManager


class _FakeBrowserDriver:
    def __init__(self):
        self.calls = []

    def run(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "browsingContext.getTree":
            return {
                "contexts": [
                    {
                        "context": "context-1",
                        "userContext": "default",
                        "children": [],
                    }
                ]
            }
        if method == "emulation.setScreenSettingsOverride":
            return None
        if method == "script.addPreloadScript":
            return {"script": "screen-fallback"}
        return {}


class _FakeDriver:
    def __init__(self):
        self._browser_driver = _FakeBrowserDriver()


class _FakeOwner:
    _context_id = "context-1"

    def __init__(self):
        self._driver = _FakeDriver()


def test_set_screen_size_injects_js_fallback_when_bidi_is_unsupported():
    owner = _FakeOwner()

    EmulationManager(owner).set_screen_size(960, 640, device_pixel_ratio=2)

    calls = owner._driver._browser_driver.calls
    methods = [method for method, _params in calls]

    assert methods == [
        "browsingContext.getTree",
        "emulation.setScreenSettingsOverride",
        "browsingContext.setViewport",
        "script.addPreloadScript",
        "script.callFunction",
    ]

    native_override = calls[1][1]
    assert native_override["userContexts"] == ["default"]
    assert "contexts" not in native_override

    viewport_override = calls[2][1]
    assert viewport_override["userContexts"] == ["default"]
    assert viewport_override["viewport"] == {"width": 960, "height": 640}
    assert viewport_override["devicePixelRatio"] == 2

    preload = calls[3][1]
    assert preload["contexts"] == ["context-1"]
    assert "screen.width" in preload["functionDeclaration"]
    assert "screen.height" in preload["functionDeclaration"]
    assert "devicePixelRatio" in preload["functionDeclaration"]
    assert "960" in preload["functionDeclaration"]
    assert "640" in preload["functionDeclaration"]

    current_page_call = calls[4][1]
    assert current_page_call["target"] == {"context": "context-1"}
    assert current_page_call["functionDeclaration"] == preload["functionDeclaration"]


class _SuccessfulRun:
    def __init__(self):
        self.calls = []

    def __call__(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "browsingContext.getTree":
            return {
                "contexts": [
                    {
                        "context": "context-1",
                        "userContext": "default",
                        "children": [],
                    }
                ]
            }
        return {}


def test_set_screen_size_scopes_native_override_to_current_user_context():
    owner = _FakeOwner()
    successful_run = _SuccessfulRun()
    owner._driver._browser_driver.run = successful_run

    EmulationManager(owner).set_screen_size(1366, 768)

    method, params = successful_run.calls[-1]
    assert method == "emulation.setScreenSettingsOverride"
    assert params["userContexts"] == ["default"]
    assert "contexts" not in params
