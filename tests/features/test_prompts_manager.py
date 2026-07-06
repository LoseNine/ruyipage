# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest import mock

from ruyipage._pages.firefox_base import FirefoxBase


def _make_page():
    page = FirefoxBase()
    page._context_id = "ctx-1"
    page._driver = SimpleNamespace(_browser_driver=mock.Mock())
    page._driver._browser_driver.run.return_value = {"subscription": "sub-1"}
    return page


def _capture_callbacks(page):
    callbacks = {}

    def set_callback(event, callback, context=None, immediate=False):
        callbacks[event] = callback

    page._driver._browser_driver.set_callback.side_effect = set_callback
    return callbacks


def test_page_prompts_set_auto_accepts_user_facing_api():
    page = _make_page()

    result = page.prompts.set_auto(accept=True)

    assert result is page.prompts
    assert page.prompts is page.prompts
    assert page._prompt_handler_config == {
        "alert": "accept",
        "confirm": "accept",
        "prompt": "accept",
        "default": "accept",
        "prompt_text": None,
    }


def test_prompt_handler_accepts_alert_from_opened_callback(monkeypatch):
    page = _make_page()
    callbacks = _capture_callbacks(page)

    handle_user_prompt = mock.Mock()
    monkeypatch.setattr(
        "ruyipage._bidi.browsing_context.handle_user_prompt",
        handle_user_prompt,
    )

    page.prompts.set_auto(accept=True)
    callbacks["browsingContext.userPromptOpened"](
        {"context": "ctx-1", "type": "alert"}
    )

    handle_user_prompt.assert_called_once_with(
        page._driver._browser_driver,
        "ctx-1",
        accept=True,
        user_text=None,
    )


def test_prompt_handler_dismisses_confirm_from_set_auto(monkeypatch):
    page = _make_page()
    callbacks = _capture_callbacks(page)

    handle_user_prompt = mock.Mock()
    monkeypatch.setattr(
        "ruyipage._bidi.browsing_context.handle_user_prompt",
        handle_user_prompt,
    )

    page.prompts.set_auto(accept=False)
    callbacks["browsingContext.userPromptOpened"](
        {"context": "ctx-1", "type": "confirm"}
    )

    handle_user_prompt.assert_called_once_with(
        page._driver._browser_driver,
        "ctx-1",
        accept=False,
        user_text=None,
    )


def test_prompt_handler_accepts_prompt_with_text(monkeypatch):
    page = _make_page()
    callbacks = _capture_callbacks(page)

    handle_user_prompt = mock.Mock()
    monkeypatch.setattr(
        "ruyipage._bidi.browsing_context.handle_user_prompt",
        handle_user_prompt,
    )

    page.prompts.set_auto(accept=True, text="Alice")
    callbacks["browsingContext.userPromptOpened"](
        {"context": "ctx-1", "type": "prompt"}
    )

    handle_user_prompt.assert_called_once_with(
        page._driver._browser_driver,
        "ctx-1",
        accept=True,
        user_text="Alice",
    )


def test_prompts_clear_and_stop_are_chainable():
    page = _make_page()

    manager = page.prompts.set_auto(accept=True)

    assert manager.clear() is manager
    assert page._prompt_handler_config is None
    assert manager.stop() is manager
    page._driver._browser_driver.run.assert_any_call(
        "session.unsubscribe", {"subscriptions": ["sub-1"]}
    )


def test_set_prompt_handler_rejects_invalid_action():
    page = _make_page()

    try:
        page.set_prompt_handler(alert="close")
    except ValueError as e:
        assert "alert prompt handler action" in str(e)
    else:
        raise AssertionError("expected ValueError for invalid prompt action")
