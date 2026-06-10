# -*- coding: utf-8 -*-

import pytest

from ruyipage._units.selector import SelectElement


class DummyElement:
    def __init__(self):
        self.js_calls = []

    def _call_js_on_self(self, script, *args):
        self.js_calls.append((script, args))
        return True


def _option(index, value=None, disabled=False):
    return {
        "index": index,
        "value": value or "opt{}".format(index),
        "text": "Option {}".format(index),
        "disabled": disabled,
    }


def test_native_select_blurs_after_successful_commit():
    ele = DummyElement()
    selector = SelectElement(ele)
    states = iter(
        [
            {
                "disabled": False,
                "multiple": False,
                "selectedIndex": 0,
                "options": [_option(0), _option(1)],
            },
            {
                "disabled": False,
                "multiple": False,
                "selectedIndex": 0,
                "options": [_option(0), _option(1)],
            },
            {
                "disabled": False,
                "multiple": False,
                "selectedIndex": 1,
                "options": [_option(0), _option(1)],
            },
            {
                "disabled": False,
                "multiple": False,
                "selectedIndex": 1,
                "options": [_option(0), _option(1)],
            },
        ]
    )

    selector._read_state = lambda: next(states)
    selector._focus_select_native = lambda: True
    selector._nudge_with_key = lambda key: None
    selector._commit_with_enter = lambda: None

    assert selector._native_select_stepwise(1) is True
    assert any("blur()" in script for script, _ in ele.js_calls)


def test_native_select_blurs_when_target_is_already_selected():
    ele = DummyElement()
    selector = SelectElement(ele)

    selector._read_state = lambda: {
        "disabled": False,
        "multiple": False,
        "selectedIndex": 1,
        "options": [_option(0), _option(1)],
    }

    assert selector._native_select_stepwise(1) is True
    assert any("blur()" in script for script, _ in ele.js_calls)


@pytest.mark.parametrize(
    ("method_name", "argument"),
    [
        ("_js_select_value", "opt1"),
        ("_js_select_text", "Option 1"),
        ("_js_select_index", 1),
    ],
)
def test_js_select_fallback_blurs_after_change(method_name, argument):
    ele = DummyElement()
    selector = SelectElement(ele)

    assert getattr(selector, method_name)(argument) is True

    script = ele.js_calls[-1][0]
    assert "dispatchEvent(new Event('change'" in script
    assert "blur()" in script
