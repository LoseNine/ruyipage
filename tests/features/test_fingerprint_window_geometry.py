# -*- coding: utf-8 -*-
"""Firefox geometry regressions for screen override and native runtime metrics."""

import os
from unittest import mock

import pytest

from ruyipage import FirefoxOptions, FirefoxPage
from ruyipage._fingerprint import builder
from ruyipage._fingerprint.builder import (
    FingerprintProfile,
    GeoInfo,
    get_country_profile,
    list_hardware_profiles,
)


def _read_geometry(page):
    return page.run_js(
        """
        return {
          outer: {w: window.outerWidth, h: window.outerHeight},
          inner: {w: window.innerWidth, h: window.innerHeight},
          screen: {
            w: screen.width,
            h: screen.height,
            availW: screen.availWidth,
            availH: screen.availHeight
          },
          dpr: window.devicePixelRatio
        };
        """,
        as_expr=False,
    )


def _fixed_1366_fingerprint():
    hardware = next(
        profile for profile in list_hardware_profiles() if profile.id == "win-hd4600"
    )
    country = get_country_profile("US")
    return FingerprintProfile(
        profile_id=hardware.id,
        firefox_version=152,
        useragent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) "
            "Gecko/20100101 Firefox/152.0"
        ),
        hardware=hardware,
        country=country,
        canvas_seed=175,
        audio_seed=176,
        language_primary=country.language_primary,
        accept_language=country.accept_language,
    )


def _fixed_geo():
    return GeoInfo(
        ip="203.0.113.45",
        country_code="US",
        country="United States",
        region="California",
        city="Los Angeles",
        timezone="America/Los_Angeles",
        latitude=34.0522,
        longitude=-118.2437,
        source="test",
        ipv6=None,
    )


@pytest.mark.feature
@pytest.mark.browser
def test_smart_fingerprint_public_flow_preserves_native_window_geometry(tmp_path):
    opts = FirefoxOptions()
    fingerprint = _fixed_1366_fingerprint()

    with mock.patch.object(builder, "fetch_geo_info", return_value=_fixed_geo()), \
            mock.patch.object(builder, "pick_fingerprint", return_value=fingerprint):
        ctx = opts.smart_fingerprint(
            base_dir=str(tmp_path),
            require_country="US",
            fetch_ipv6=False,
        )

    assert opts.startup_window_size is None
    assert "width:" not in open(ctx.fpfile_path, encoding="utf-8").read()
    assert "height:" not in open(ctx.fpfile_path, encoding="utf-8").read()

    page = FirefoxPage(opts)
    try:
        page.get("about:blank")
        before = _read_geometry(page)
        result = ctx.apply_emulation(page)
        after = _read_geometry(page)

        assert result["screen"] is True
        assert after["outer"] == before["outer"]
        assert after["inner"] == before["inner"]
        assert after["screen"] == {
            "w": 1366,
            "h": 768,
            "availW": 1366,
            "availH": 768,
        }
    finally:
        page.quit()


@pytest.mark.feature
def test_screen_override_keeps_window_geometry_stable(page):
    page.get("about:blank")
    before = _read_geometry(page)

    page.emulation.set_screen_size(1366, 768)
    page.wait.js_result(
        "screen.width === 1366 && screen.height === 768 && screen.availWidth === 1366 && screen.availHeight === 768",
        timeout=3,
    )

    after = _read_geometry(page)

    assert after["outer"] == before["outer"]
    assert after["inner"] == before["inner"]
    assert after["screen"] == {"w": 1366, "h": 768, "availW": 1366, "availH": 768}


@pytest.mark.feature
def test_screen_override_is_inherited_by_new_tabs(page):
    page.get("about:blank")
    page.emulation.set_screen_size(1366, 768)

    tab = page.new_tab("about:blank")
    try:
        tab.wait.js_result(
            "screen.width === 1366 && screen.height === 768 && screen.availWidth === 1366 && screen.availHeight === 768",
            timeout=3,
        )
        assert _read_geometry(tab)["screen"] == {
            "w": 1366,
            "h": 768,
            "availW": 1366,
            "availH": 768,
        }
    finally:
        tab.close()


@pytest.mark.feature
@pytest.mark.skipif(
    os.environ.get("RUYIPAGE_VERIFY_NATIVE_GEOMETRY") != "1",
    reason="set RUYIPAGE_VERIFY_NATIVE_GEOMETRY=1 to verify native Firefox geometry",
)
def test_native_geometry_matches_target_fingerprint(page):
    page.get("about:blank")
    metrics = _read_geometry(page)

    width_gap = metrics["outer"]["w"] - metrics["inner"]["w"]
    height_gap = metrics["outer"]["h"] - metrics["inner"]["h"]

    expected_gaps = (16, 93)
    actual_gaps = (width_gap, height_gap)

    assert actual_gaps == expected_gaps, (
        "native geometry mismatch: "
        f"actual_gaps={actual_gaps!r}, expected_gaps={expected_gaps!r}, "
        f"metrics={metrics!r}"
    )
