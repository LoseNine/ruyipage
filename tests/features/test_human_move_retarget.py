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

    def _get_center(self, scroll=True):
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


@pytest.mark.asyncio
@pytest.mark.feature
async def test_async_actions_human_move_uses_wrapped_element_center():
    from ruyipage._async._generated import AsyncFirefoxElement, AsyncUnitProxy

    owner = _DummyOwner()
    actions = Actions(owner)
    sync_element = _DummyElement([(420, 240)], in_viewport=True)
    element = AsyncFirefoxElement(sync_element)
    async_actions = AsyncUnitProxy(actions)

    result = await async_actions.human_move(element, style="line")

    moves = [a for a in actions._pointer_actions if a.get("type") == "pointerMove"]
    assert result is async_actions
    assert moves[-1]["x"] == 420
    assert moves[-1]["y"] == 240
    assert actions.curr_x == 420
    assert actions.curr_y == 240


@pytest.mark.asyncio
@pytest.mark.feature
async def test_async_actions_drag_to_unwraps_source_and_target_elements():
    from ruyipage._async._generated import AsyncFirefoxElement, AsyncUnitProxy

    actions = Actions(_DummyOwner())
    source = AsyncFirefoxElement(
        _DummyElement([(120, 80)], in_viewport=True)
    )
    target = AsyncFirefoxElement(
        _DummyElement([(480, 320)], in_viewport=True)
    )
    async_actions = AsyncUnitProxy(actions)

    result = await async_actions.drag_to(source, target, steps=4)

    moves = [a for a in actions._pointer_actions if a.get("type") == "pointerMove"]
    assert result is async_actions
    assert (moves[0]["x"], moves[0]["y"]) == (120, 80)
    assert (moves[-1]["x"], moves[-1]["y"]) == (480, 320)


@pytest.mark.asyncio
@pytest.mark.feature
async def test_async_touch_move_uses_wrapped_element_center():
    from ruyipage._async._generated import AsyncFirefoxElement, AsyncUnitProxy
    from ruyipage._units.touch_actions import TouchActions

    touch = TouchActions(_DummyOwner())
    sync_element = _DummyElement([(360, 180)], in_viewport=True)
    element = AsyncFirefoxElement(sync_element)
    async_touch = AsyncUnitProxy(touch)

    result = await async_touch.move_to(element)

    assert result is async_touch
    assert (touch._x, touch._y) == (360, 180)
    assert touch._fingers[0][-1]["x"] == 360
    assert touch._fingers[0][-1]["y"] == 180


@pytest.mark.feature
def test_human_move_tuple_still_clamps_out_of_viewport_target():
    owner = _DummyOwner()
    actions = Actions(owner)

    actions.human_move((1329, 922), style="line")

    moves = [a for a in actions._pointer_actions if a.get("type") == "pointerMove"]
    assert moves
    assert moves[-1]["x"] == 1329
    assert moves[-1]["y"] == 722


@pytest.mark.feature
def test_first_human_move_uses_random_viewport_start(monkeypatch):
    owner = _DummyOwner()
    actions = Actions(owner)
    monkeypatch.setattr(actions, "_random_human_start", lambda *args: (321, 234))

    actions.human_move((900, 500), style="line")

    moves = [a for a in actions._pointer_actions if a.get("type") == "pointerMove"]
    assert moves
    assert moves[0]["x"] == 321
    assert moves[0]["y"] == 234
    assert moves[-1]["x"] == 900
    assert moves[-1]["y"] == 500
    assert actions.curr_x == 900
    assert actions.curr_y == 500
    assert actions._pointer_position_known is True


@pytest.mark.feature
def test_human_move_respects_explicit_zero_pointer_position(monkeypatch):
    owner = _DummyOwner()
    actions = Actions(owner)

    def fail_random_start(*args):
        raise AssertionError("explicit pointer position should not be randomized")

    monkeypatch.setattr(actions, "_random_human_start", fail_random_start)
    actions.move_to((0, 0))
    move_count = len(actions._pointer_actions)

    actions.human_move((300, 200), style="line")

    moves = [a for a in actions._pointer_actions[move_count:] if a.get("type") == "pointerMove"]
    assert moves
    assert moves[0]["x"] == 0
    assert moves[0]["y"] == 0
    assert moves[-1]["x"] == 300
    assert moves[-1]["y"] == 200
