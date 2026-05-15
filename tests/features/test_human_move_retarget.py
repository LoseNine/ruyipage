# -*- coding: utf-8 -*-

import pytest

from ruyipage._units.actions import Actions


class _DummyStates(object):
    def __init__(self, in_viewport=False):
        self.is_whole_in_viewport = in_viewport


class _DummyElement(object):
    def __init__(self, centers, in_viewport=False):
        self._centers = list(centers)
        self._calls = 0
        self.states = _DummyStates(in_viewport=in_viewport)

    def _get_center(self):
        idx = min(self._calls, len(self._centers) - 1)
        self._calls += 1
        x, y = self._centers[idx]
        return {"x": x, "y": y}


class _DummyScroll(object):
    def __init__(self):
        self.calls = []

    def to_see(self, ele, center=False):
        self.calls.append((ele, center))
        ele.states.is_whole_in_viewport = True


class _DummyRect(object):
    viewport_size = (1536, 723)


class _DummyOwner(object):
    def __init__(self):
        self.scroll = _DummyScroll()
        self.rect = _DummyRect()


@pytest.mark.feature
def test_human_move_re_resolves_element_center_after_scroll(monkeypatch):
    monkeypatch.setattr("ruyipage._units.actions._sleep", lambda _: None)

    owner = _DummyOwner()
    actions = Actions(owner)
    ele = _DummyElement([(1329, 922), (1329, 514)], in_viewport=False)

    actions.human_move(ele, style="line")

    moves = [a for a in actions._pointer_actions if a.get("type") == "pointerMove"]
    assert owner.scroll.calls == [(ele, True)]
    assert moves
    assert moves[-1]["x"] == 1329
    assert moves[-1]["y"] == 514
    assert actions.curr_x == 1329
    assert actions.curr_y == 514


@pytest.mark.feature
def test_human_move_tuple_still_clamps_out_of_viewport_target():
    owner = _DummyOwner()
    actions = Actions(owner)

    actions.human_move((1329, 922), style="line")

    moves = [a for a in actions._pointer_actions if a.get("type") == "pointerMove"]
    assert moves
    assert moves[-1]["x"] == 1329
    assert moves[-1]["y"] == 722
