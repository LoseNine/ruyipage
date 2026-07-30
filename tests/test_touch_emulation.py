# -*- coding: utf-8 -*-
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

import ruyipage
from ruyipage._configs.firefox_options import FirefoxOptions
from ruyipage._units.emulation import EmulationManager
from ruyipage.errors import BiDiError, PageDisconnectedError


UINT32_MAX = (2**32) - 1


class _BaseBrowserDriver:
    def __init__(self):
        self.calls = []

    def run(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "browsingContext.getTree":
            return {
                "contexts": [
                    {
                        "context": "context-1",
                        "userContext": "uc-touch",
                        "children": [],
                    }
                ]
            }
        return {}


class _MissingUserContextBrowserDriver(_BaseBrowserDriver):
    def run(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "browsingContext.getTree":
            return {
                "contexts": [
                    {
                        "context": "context-1",
                        "children": [],
                    }
                ]
            }
        return {}


class _NativeTouchBrowserDriver(_BaseBrowserDriver):
    pass


class _UnsupportedTouchBrowserDriver(_BaseBrowserDriver):
    def run(self, method, params, **kwargs):
        if method == "emulation.setTouchOverride":
            raise BiDiError("unknown command", "emulation.setTouchOverride")
        return super().run(method, params, **kwargs)


class _UnsupportedTouchBrowserDriverByErrorField(_BaseBrowserDriver):
    def run(self, method, params, **kwargs):
        if method == "emulation.setTouchOverride":
            raise BiDiError("unknown command", "custom unsupported marker")
        return super().run(method, params, **kwargs)


class _InvalidTouchBrowserDriver(_BaseBrowserDriver):
    def run(self, method, params, **kwargs):
        if method == "emulation.setTouchOverride":
            raise BiDiError("invalid argument", "bad maxTouchPoints")
        return super().run(method, params, **kwargs)


class _DisconnectedTouchBrowserDriver(_BaseBrowserDriver):
    def run(self, method, params, **kwargs):
        if method == "emulation.setTouchOverride":
            raise PageDisconnectedError("socket closed")
        return super().run(method, params, **kwargs)


class _FakeDriver:
    def __init__(self, browser_driver):
        self._browser_driver = browser_driver


class _FakeOwner:
    def __init__(self, browser_driver, options=None):
        self._context_id = "context-1"
        self._driver = _FakeDriver(browser_driver)
        self.browser = SimpleNamespace(options=options or FirefoxOptions())


def test_get_touch_capability_uses_current_user_context_and_is_frozen(tmp_path):
    options = FirefoxOptions().set_user_dir(str(tmp_path)).set_touch_fallback()
    owner = _FakeOwner(_BaseBrowserDriver(), options=options)

    capability = EmulationManager(owner).get_touch_capability()

    assert capability.user_context == "uc-touch"
    assert capability.native_supported is None
    assert capability.fallback_configured is True
    assert capability.fallback_installable is True
    assert ruyipage.TouchCapability is type(capability)

    with pytest.raises(FrozenInstanceError):
        capability.user_context = "other"


def test_get_touch_capability_marks_attach_mode_as_not_installable(tmp_path):
    options = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .existing_only(True)
        .set_touch_fallback()
    )
    owner = _FakeOwner(_BaseBrowserDriver(), options=options)

    capability = EmulationManager(owner).get_touch_capability()

    assert capability.fallback_configured is True
    assert capability.fallback_installable is False


def test_set_touch_enabled_result_defaults_to_context_scope_without_user_context_lookup():
    owner = _FakeOwner(_NativeTouchBrowserDriver())

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=5,
    )

    assert ruyipage.TouchOverrideResult is type(result)
    assert result.applied is True
    assert result.supported is True
    assert result.source == "native"
    assert result.runtime_mutable is True
    assert result.reason is None
    assert result.capability.user_context == "uc-touch"

    assert owner._driver._browser_driver.calls == [
        (
            "emulation.setTouchOverride",
            {"maxTouchPoints": 5, "contexts": ["context-1"]},
        ),
        (
            "browsingContext.getTree",
            {"maxDepth": 0, "root": "context-1"},
        ),
    ]


def test_set_touch_enabled_result_uses_current_user_context_only_for_user_context_scope():
    owner = _FakeOwner(_NativeTouchBrowserDriver())

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=5,
        scope="user_context",
    )

    assert result.applied is True
    assert result.source == "native"
    assert result.capability.user_context == "uc-touch"

    assert owner._driver._browser_driver.calls == [
        (
            "browsingContext.getTree",
            {"maxDepth": 0, "root": "context-1"},
        ),
        (
            "emulation.setTouchOverride",
            {"maxTouchPoints": 5, "userContexts": ["uc-touch"]},
        ),
    ]


def test_set_touch_enabled_result_supports_global_scope_and_preserves_capability_user_context():
    owner = _FakeOwner(_NativeTouchBrowserDriver())

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=5,
        scope="global",
    )

    assert result.applied is True
    assert result.source == "native"
    assert result.capability.user_context == "uc-touch"
    assert owner._driver._browser_driver.calls == [
        (
            "emulation.setTouchOverride",
            {"maxTouchPoints": 5},
        ),
        (
            "browsingContext.getTree",
            {"maxDepth": 0, "root": "context-1"},
        ),
    ]


def test_set_touch_enabled_result_rejects_unknown_scope():
    owner = _FakeOwner(_NativeTouchBrowserDriver())

    with pytest.raises(ValueError, match="scope"):
        EmulationManager(owner).set_touch_enabled_result(scope="tab-group")


@pytest.mark.parametrize("value", [True, False, 1.0, "1", -1, UINT32_MAX + 1])
def test_set_touch_enabled_result_rejects_invalid_max_touch_points(value):
    owner = _FakeOwner(_NativeTouchBrowserDriver())

    with pytest.raises((TypeError, ValueError), match="max_touch_points"):
        EmulationManager(owner).set_touch_enabled_result(
            enabled=True,
            max_touch_points=value,
        )


@pytest.mark.parametrize("value", [0, UINT32_MAX])
def test_set_touch_enabled_result_accepts_uint32_range(value):
    owner = _FakeOwner(_NativeTouchBrowserDriver())

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=value,
    )

    assert result.applied is True
    assert owner._driver._browser_driver.calls[0][1]["maxTouchPoints"] == value


def test_set_touch_enabled_returns_applied_instead_of_supported(tmp_path):
    source = tmp_path / "source-fp.txt"
    source.write_text("canvas:123\n", encoding="utf-8")
    options = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .set_fpfile(str(source))
        .set_touch_fallback(max_touch_points=3)
    )
    options.prepare_runtime_files()
    owner = _FakeOwner(_UnsupportedTouchBrowserDriver(), options=options)

    applied = EmulationManager(owner).set_touch_enabled(
        enabled=True,
        max_touch_points=2,
    )

    assert applied is False


def test_set_touch_enabled_result_uses_matching_startup_fallback_when_native_is_unsupported(
    tmp_path,
):
    source = tmp_path / "source-fp.txt"
    source.write_text("canvas:123\n", encoding="utf-8")
    options = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .set_fpfile(str(source))
        .set_touch_fallback(max_touch_points=3)
    )
    options.prepare_runtime_files()
    owner = _FakeOwner(_UnsupportedTouchBrowserDriver(), options=options)

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=3,
    )

    assert result.supported is True
    assert result.applied is True
    assert result.source == "fpfile"
    assert result.runtime_mutable is False
    assert result.reason is None
    assert result.capability.fallback_configured is True
    assert result.capability.fallback_installable is True


def test_set_touch_enabled_result_marks_startup_fallback_as_not_mutable_for_disable_request(
    tmp_path,
):
    source = tmp_path / "source-fp.txt"
    source.write_text("canvas:123\n", encoding="utf-8")
    options = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .set_fpfile(str(source))
        .set_touch_fallback(max_touch_points=3)
    )
    options.prepare_runtime_files()
    owner = _FakeOwner(_UnsupportedTouchBrowserDriver(), options=options)

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=False,
        max_touch_points=3,
    )

    assert result.applied is False
    assert result.source == "fpfile"
    assert result.runtime_mutable is False
    assert "startup" in result.reason


def test_set_touch_enabled_result_marks_startup_fallback_as_not_mutable_for_point_mismatch(
    tmp_path,
):
    source = tmp_path / "source-fp.txt"
    source.write_text("canvas:123\n", encoding="utf-8")
    options = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .set_fpfile(str(source))
        .set_touch_fallback(max_touch_points=3)
    )
    options.prepare_runtime_files()
    owner = _FakeOwner(_UnsupportedTouchBrowserDriver(), options=options)

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=2,
    )

    assert result.applied is False
    assert result.source == "fpfile"
    assert result.runtime_mutable is False
    assert "max_touch_points" in result.reason


def test_set_touch_enabled_result_raises_touch_unsupported_error_in_strict_mode():
    owner = _FakeOwner(_UnsupportedTouchBrowserDriver())

    with pytest.raises(ruyipage.TouchUnsupportedError, match="setTouchOverride"):
        EmulationManager(owner).set_touch_enabled_result(strict=True)


def test_set_touch_enabled_result_user_context_scope_without_user_context_is_not_applied():
    owner = _FakeOwner(_MissingUserContextBrowserDriver())

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=3,
        scope="user_context",
    )

    assert result.supported is False
    assert result.applied is False
    assert result.source == "none"
    assert result.runtime_mutable is False
    assert "userContext" in result.reason
    assert owner._driver._browser_driver.calls == [
        (
            "browsingContext.getTree",
            {"maxDepth": 0, "root": "context-1"},
        )
    ]


def test_set_touch_enabled_result_user_context_scope_without_user_context_raises_in_strict_mode():
    owner = _FakeOwner(_MissingUserContextBrowserDriver())

    with pytest.raises(ruyipage.TouchUnsupportedError, match="userContext"):
        EmulationManager(owner).set_touch_enabled_result(
            enabled=True,
            max_touch_points=3,
            scope="user_context",
            strict=True,
        )


def test_set_touch_enabled_result_treats_unknown_command_error_field_as_unsupported(
    tmp_path,
):
    source = tmp_path / "source-fp.txt"
    source.write_text("canvas:123\n", encoding="utf-8")
    options = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .set_fpfile(str(source))
        .set_touch_fallback(max_touch_points=3)
    )
    options.prepare_runtime_files()
    owner = _FakeOwner(_UnsupportedTouchBrowserDriverByErrorField(), options=options)

    result = EmulationManager(owner).set_touch_enabled_result(
        enabled=True,
        max_touch_points=3,
    )

    assert result.applied is True
    assert result.source == "fpfile"


def test_set_touch_enabled_result_raises_touch_startup_only_error_in_strict_mode(
    tmp_path,
):
    source = tmp_path / "source-fp.txt"
    source.write_text("canvas:123\n", encoding="utf-8")
    options = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .set_fpfile(str(source))
        .set_touch_fallback(max_touch_points=3)
    )
    options.prepare_runtime_files()
    owner = _FakeOwner(_UnsupportedTouchBrowserDriver(), options=options)

    with pytest.raises(ruyipage.TouchStartupOnlyError, match="startup"):
        EmulationManager(owner).set_touch_enabled_result(
            enabled=False,
            max_touch_points=3,
            strict=True,
        )


def test_top_level_exports_include_touch_exceptions():
    assert issubclass(ruyipage.TouchUnsupportedError, Exception)
    assert issubclass(ruyipage.TouchStartupOnlyError, Exception)


def test_set_touch_enabled_result_keeps_invalid_argument_errors():
    owner = _FakeOwner(_InvalidTouchBrowserDriver())

    with pytest.raises(BiDiError, match="invalid argument"):
        EmulationManager(owner).set_touch_enabled_result(max_touch_points=0)


def test_set_touch_enabled_result_keeps_connection_errors():
    owner = _FakeOwner(_DisconnectedTouchBrowserDriver())

    with pytest.raises(PageDisconnectedError, match="socket closed"):
        EmulationManager(owner).set_touch_enabled_result()
