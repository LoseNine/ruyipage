# -*- coding: utf-8 -*-

import pytest

from ruyipage._bidi.input_ import build_human_click_actions


@pytest.mark.feature
def test_build_human_click_actions_clamps_points_within_bounds():
    actions = build_human_click_actions(
        219, 80,
        sx=260,
        sy=64,
        min_x=1,
        max_x=299,
        min_y=1,
        max_y=64,
    )

    pointer_actions = actions[0]["actions"]
    moves = [a for a in pointer_actions if a.get("type") == "pointerMove"]

    assert moves
    for move in moves:
        assert 1 <= move["x"] <= 299
        assert 1 <= move["y"] <= 64


@pytest.mark.feature
def test_build_human_click_actions_clamps_out_of_range_target():
    actions = build_human_click_actions(
        500,
        200,
        sx=320,
        sy=90,
        min_x=1,
        max_x=299,
        min_y=1,
        max_y=64,
    )

    pointer_actions = actions[0]["actions"]
    final_move = [a for a in pointer_actions if a.get("type") == "pointerMove"][-1]

    assert 1 <= final_move["x"] <= 299
    assert 1 <= final_move["y"] <= 64
