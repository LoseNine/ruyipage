# -*- coding: utf-8 -*-

import pytest

from ruyipage._configs.firefox_options import FirefoxOptions


UINT32_MAX = (2**32) - 1


def test_set_touch_fallback_is_chainable_and_writes_runtime_copy(tmp_path):
    source_fpfile = tmp_path / "source-fp.txt"
    source_fpfile.write_text("canvas:123\n", encoding="utf-8")

    opts = FirefoxOptions()
    returned = (
        opts.set_user_dir(str(tmp_path))
        .set_fpfile(str(source_fpfile))
        .set_touch_fallback(max_touch_points=5)
    )

    assert returned is opts

    opts.prepare_runtime_files()

    assert opts.fpfile == str(tmp_path / "ruyipage_runtime_fp.txt")
    assert source_fpfile.read_text(encoding="utf-8") == "canvas:123\n"

    content = (tmp_path / "ruyipage_runtime_fp.txt").read_text(encoding="utf-8")
    assert "canvas:123" in content
    assert "touch.enabled=true" in content
    assert "touch.maxTouchPoints=5" in content
    assert "touch.legacyApis=true" in content
    assert "touch.primaryPointer=coarse" in content
    assert "touch.anyPointer=coarse" in content


def test_set_touch_fallback_accepts_zero_max_touch_points(tmp_path):
    opts = FirefoxOptions().set_user_dir(str(tmp_path)).set_touch_fallback(
        max_touch_points=0
    )

    opts.prepare_runtime_files()

    content = (tmp_path / "ruyipage_runtime_fp.txt").read_text(encoding="utf-8")
    assert "touch.maxTouchPoints=0" in content


def test_prepare_runtime_files_overrides_touch_lines_from_source_fpfile(tmp_path):
    source_fpfile = tmp_path / "source-fp.txt"
    source_fpfile.write_text(
        "\n".join(
            [
                "canvas:123",
                "touch.enabled=false",
                "touch.maxTouchPoints=9",
                "touch.legacyApis=false",
                "touch.primaryPointer=fine",
                "touch.anyPointer=fine",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = (
        FirefoxOptions()
        .set_user_dir(str(tmp_path))
        .set_fpfile(str(source_fpfile))
        .set_touch_fallback(max_touch_points=5)
    )

    opts.prepare_runtime_files()

    content = (tmp_path / "ruyipage_runtime_fp.txt").read_text(encoding="utf-8")
    assert content.count("touch.enabled=") == 1
    assert content.count("touch.maxTouchPoints=") == 1
    assert content.count("touch.legacyApis=") == 1
    assert content.count("touch.primaryPointer=") == 1
    assert content.count("touch.anyPointer=") == 1
    assert "touch.enabled=true" in content
    assert "touch.maxTouchPoints=5" in content
    assert "touch.legacyApis=true" in content
    assert "touch.primaryPointer=coarse" in content
    assert "touch.anyPointer=coarse" in content


@pytest.mark.parametrize("value", [True, False, 1.0, "1", -1, UINT32_MAX + 1])
def test_set_touch_fallback_rejects_invalid_max_touch_points(value):
    opts = FirefoxOptions()

    with pytest.raises((TypeError, ValueError), match="max_touch_points"):
        opts.set_touch_fallback(max_touch_points=value)
