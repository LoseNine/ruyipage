# -*- coding: utf-8 -*-
"""Unit tests for ``ruyipage._fingerprint.builder``.

All tests are fully mocked: no real network calls, no browser launches.
The bundled JSON data files are used as-is to exercise the loader.
"""

from __future__ import annotations

import os
import random
from typing import Any, Dict, List
from unittest import mock

import pytest

from ruyipage._fingerprint import builder
from ruyipage._fingerprint.builder import (
    CountryMismatchError,
    CountryProfile,
    FingerprintConfigError,
    FingerprintContext,
    FingerprintError,
    FingerprintProfile,
    GeoError,
    GeoInfo,
    HardwareProfile,
    apply_smart_fingerprint,
    build_proxies_dict,
    fetch_geo_info,
    fetch_public_ipv6,
    get_country_profile,
    list_hardware_profiles,
    pick_fingerprint,
    write_fpfile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_geo(**overrides: Any) -> GeoInfo:
    base = dict(
        ip="203.0.113.45",
        country_code="US",
        country="United States",
        region="California",
        city="Los Angeles",
        timezone="America/Los_Angeles",
        latitude=34.0522,
        longitude=-118.2437,
        source="geojs",
        ipv6=None,
    )
    base.update(overrides)
    return GeoInfo(**base)


class _StubOptions:
    """Drop-in stand-in for ``FirefoxOptions`` recording every mutation."""

    def __init__(self, fail_on: List[str] = None):
        self.calls: List[tuple] = []
        self.fail_on = set(fail_on or [])

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.fail_on:
            raise RuntimeError("forced failure in " + name)

    def set_proxy(self, url: str) -> None:
        self._record("set_proxy", url)

    def set_user_dir(self, path: str) -> None:
        self._record("set_user_dir", path)

    def set_fpfile(self, path: str) -> None:
        self._record("set_fpfile", path)

    def set_window_size(self, w: int, h: int) -> None:
        self._record("set_window_size", w, h)


# ---------------------------------------------------------------------------
# 1) bundled data validation
# ---------------------------------------------------------------------------

def test_bundled_fingerprints_load_ok():
    profiles = list_hardware_profiles()
    assert len(profiles) == 22
    ids = {p.id for p in profiles}
    assert len(ids) == 22  # no dupes
    for p in profiles:
        assert p.platform == "windows"
        assert p.hardware_concurrency >= 1
        assert p.width >= 800 and p.height >= 600
        assert p.webgl.unmasked_renderer
        assert p.webgl.max_texture_size > 0


# ---------------------------------------------------------------------------
# 2) bundled region locales
# ---------------------------------------------------------------------------

def test_bundled_region_locales_load_ok():
    us = get_country_profile("US")
    assert isinstance(us, CountryProfile)
    assert us.language_primary.startswith("en")
    assert "en-US" in us.accept_language

    # case-insensitive + fallback to _default
    fallback = get_country_profile("zz")
    assert fallback.country_code == "_default"


# ---------------------------------------------------------------------------
# 3) build_proxies_dict
# ---------------------------------------------------------------------------

def test_build_proxies_dict_variants():
    assert build_proxies_dict(None, None) is None
    assert build_proxies_dict("h", None) is None
    pd = build_proxies_dict("proxy.example.com", 8080)
    assert pd == {
        "http": "http://proxy.example.com:8080",
        "https": "http://proxy.example.com:8080",
    }
    pd = build_proxies_dict("proxy.example.com", 8080, "u", "p")
    assert pd["http"].startswith("http://u:p@")


# ---------------------------------------------------------------------------
# 4) fetch_geo_info: source fall-back chain
# ---------------------------------------------------------------------------

def test_fetch_geo_info_fallback_to_second_source():
    payloads = [
        # geojs fails (network)
        IOError("boom"),
        # ipapi succeeds
        {
            "ip": "203.0.113.10",
            "country": "US",
            "country_name": "United States",
            "region": "CA",
            "city": "LA",
            "timezone": "America/Los_Angeles",
            "latitude": "34.0",
            "longitude": "-118.2",
        },
    ]

    def fake_http(url: str, proxies: Any, timeout: float) -> Dict[str, Any]:
        item = payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with mock.patch.object(builder, "_http_get_json", side_effect=fake_http):
        geo = fetch_geo_info(timeout=1.0, retries_per_source=0)
    assert geo.country_code == "US"
    assert geo.source == "ipapi"


# ---------------------------------------------------------------------------
# 5) fetch_geo_info: country gate
# ---------------------------------------------------------------------------

def test_fetch_geo_info_country_mismatch():
    payload = {
        "ip": "203.0.113.10",
        "country_code": "JP",
        "country": "Japan",
        "region": "Tokyo",
        "city": "Tokyo",
        "timezone": "Asia/Tokyo",
        "latitude": "35.0",
        "longitude": "139.7",
    }
    with mock.patch.object(builder, "_http_get_json", return_value=payload):
        with pytest.raises(CountryMismatchError) as ei:
            fetch_geo_info(require_country="US", retries_per_source=0)
    assert ei.value.actual == "JP" and ei.value.required == "US"


# ---------------------------------------------------------------------------
# 6) fetch_geo_info: all sources fail
# ---------------------------------------------------------------------------

def test_fetch_geo_info_all_sources_fail():
    with mock.patch.object(
        builder, "_http_get_json", side_effect=IOError("net down")
    ):
        with pytest.raises(GeoError):
            fetch_geo_info(timeout=0.5, retries_per_source=0)


# ---------------------------------------------------------------------------
# 7) fetch_public_ipv6: returns first valid, never raises
# ---------------------------------------------------------------------------

def test_fetch_public_ipv6_success_and_silent_failure():
    class _Resp:
        def __init__(self, ok: bool, payload: Any, text: str = ""):
            self.ok = ok
            self._payload = payload
            self.text = text

        def json(self) -> Any:
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    def fake_get(url: str, **kw: Any) -> _Resp:
        return _Resp(True, {"ip": "2001:db8::1"})

    with mock.patch("requests.get", side_effect=fake_get):
        assert fetch_public_ipv6() == "2001:db8::1"

    def boom(*a: Any, **kw: Any) -> _Resp:
        raise IOError("offline")

    with mock.patch("requests.get", side_effect=boom):
        assert fetch_public_ipv6() is None


# ---------------------------------------------------------------------------
# 8) pick_fingerprint determinism
# ---------------------------------------------------------------------------

def test_pick_fingerprint_deterministic_with_seed():
    geo = _make_geo()
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    fp_a = pick_fingerprint(geo, rng=rng_a)
    fp_b = pick_fingerprint(geo, rng=rng_b)
    assert fp_a.profile_id == fp_b.profile_id
    assert fp_a.canvas_seed == fp_b.canvas_seed
    assert fp_a.firefox_version >= 149  # 151 ±2
    assert "Firefox/{}.0".format(fp_a.firefox_version) in fp_a.useragent
    assert isinstance(fp_a.hardware, HardwareProfile)


# ---------------------------------------------------------------------------
# 9) write_fpfile schema and atomicity
# ---------------------------------------------------------------------------

def test_write_fpfile_schema(tmp_path):
    geo = _make_geo(ipv6="2001:db8::1")
    fp = pick_fingerprint(geo, rng=random.Random(1))
    out = tmp_path / "fpfile.txt"
    write_fpfile(str(out), geo, fp, proxy_user="u", proxy_pwd="p")

    text = out.read_text(encoding="utf-8")
    lines = text.splitlines()

    # --- basic format: every line is key:value, no '=' in key part ---
    assert all(":" in line and "=" not in line.split(":", 1)[0]
               for line in lines)

    # --- extract actual keys (preserving order) ---
    actual_keys = [line.split(":", 1)[0] for line in lines]

    # The full expected key order when IPv6 is present + httpauth supplied.
    expected_keys = [
        "webdriver",
        "local_webrtc_ipv4",
        "local_webrtc_ipv6",
        "public_webrtc_ipv4",
        "public_webrtc_ipv6",
        "timezone",
        "language",
        "speech.voices.local",
        "speech.voices.remote",
        "speech.voices.local.langs",
        "speech.voices.remote.langs",
        "speech.voices.default.name",
        "speech.voices.default.lang",
        "font_system",
        "useragent",
        "hardwareConcurrency",
        "webgl.vendor",
        "webgl.renderer",
        "webgl.version",
        "webgl.glsl_version",
        "webgl.unmasked_vendor",
        "webgl.unmasked_renderer",
        "webgl.max_texture_size",
        "webgl.max_cube_map_texture_size",
        "webgl.max_texture_image_units",
        "webgl.max_vertex_attribs",
        "webgl.aliased_point_size_max",
        "webgl.max_viewport_dim",
        "width",
        "height",
        "canvas",
        "httpauth.username",
        "httpauth.password",
    ]

    assert actual_keys == expected_keys, (
        "fpfile key mismatch!\nexpected: {}\nactual:   {}".format(
            expected_keys, actual_keys)
    )

    # --- spot-check representative values ---
    assert lines[0] == "webdriver:0"
    assert any(line.startswith("local_webrtc_ipv4:203.0.113.45") for line in lines)
    assert any(line.startswith("local_webrtc_ipv6:2001:db8::1") for line in lines)
    assert any(line.startswith("public_webrtc_ipv4:203.0.113.45") for line in lines)
    assert any(line.startswith("public_webrtc_ipv6:2001:db8::1") for line in lines)
    assert any(line.startswith("timezone:America/Los_Angeles") for line in lines)
    assert any(line.startswith("useragent:Mozilla/5.0") for line in lines)
    assert any(line.startswith("canvas:") for line in lines)
    assert any(line == "httpauth.username:u" for line in lines)
    assert any(line == "httpauth.password:p" for line in lines)


def test_write_fpfile_no_ipv6_no_auth(tmp_path):
    """When IPv6 is absent and no proxy auth, those keys must be omitted."""
    geo = _make_geo(ipv6=None)
    fp = pick_fingerprint(geo, rng=random.Random(1))
    out = tmp_path / "fpfile.txt"
    write_fpfile(str(out), geo, fp)

    text = out.read_text(encoding="utf-8")
    actual_keys = [line.split(":", 1)[0] for line in text.strip().splitlines()]

    # IPv6 keys must not appear
    assert "local_webrtc_ipv6" not in actual_keys
    assert "public_webrtc_ipv6" not in actual_keys
    # httpauth keys must not appear
    assert "httpauth.username" not in actual_keys
    assert "httpauth.password" not in actual_keys

    # Core keys still in correct order (no IPv6 gaps)
    expected_core_keys = [
        "webdriver",
        "local_webrtc_ipv4",
        "public_webrtc_ipv4",
        "timezone",
        "language",
        "speech.voices.local",
        "speech.voices.remote",
        "speech.voices.local.langs",
        "speech.voices.remote.langs",
        "speech.voices.default.name",
        "speech.voices.default.lang",
        "font_system",
        "useragent",
        "hardwareConcurrency",
        "webgl.vendor",
        "webgl.renderer",
        "webgl.version",
        "webgl.glsl_version",
        "webgl.unmasked_vendor",
        "webgl.unmasked_renderer",
        "webgl.max_texture_size",
        "webgl.max_cube_map_texture_size",
        "webgl.max_texture_image_units",
        "webgl.max_vertex_attribs",
        "webgl.aliased_point_size_max",
        "webgl.max_viewport_dim",
        "width",
        "height",
        "canvas",
    ]
    assert actual_keys == expected_core_keys


# ---------------------------------------------------------------------------
# 10) write_fpfile: extra cannot collide with reserved keys
# ---------------------------------------------------------------------------

def test_write_fpfile_extra_collision_rejected(tmp_path):
    geo = _make_geo()
    fp = pick_fingerprint(geo, rng=random.Random(1))
    with pytest.raises(FingerprintError):
        write_fpfile(
            str(tmp_path / "fp.txt"), geo, fp,
            extra={"useragent": "evil"},
        )


# ---------------------------------------------------------------------------
# 11) FingerprintContext.summary / to_dict / apply_emulation
# ---------------------------------------------------------------------------

def test_fingerprint_context_helpers():
    geo = _make_geo(ipv6="2001:db8::1")
    fp = pick_fingerprint(geo, rng=random.Random(7))
    ctx = FingerprintContext(
        geo=geo, fingerprint=fp,
        userdir="/tmp/x", fpfile_path="/tmp/x/fp.txt",
    )

    s = ctx.summary()
    assert "[fp]" in s and "Firefox/" in s and "ipv6=yes" in s

    d = ctx.to_dict()
    assert d["country_code"] == "US"
    assert d["firefox_version"] == fp.firefox_version

    # apply_emulation: stub page with all four hooks
    class _Emu:
        def __init__(self):
            self.calls = []
        def set_geolocation(self, lat, lon, accuracy=100):
            self.calls.append(("geo", lat, lon, accuracy))
        def set_locale(self, langs):
            self.calls.append(("loc", tuple(langs)))
        def set_timezone(self, tz):
            self.calls.append(("tz", tz))

    class _Net:
        def __init__(self):
            self.headers = None
        def set_extra_headers(self, h):
            self.headers = dict(h)

    class _Page:
        def __init__(self):
            self.emulation = _Emu()
            self.network = _Net()

    page = _Page()
    result = ctx.apply_emulation(page)
    assert result == {"geolocation": True, "locale": True,
                      "timezone": True, "headers": True}
    assert page.network.headers["Accept-Language"] == fp.accept_language

    # missing hooks degrade gracefully (e.g. older ruyipage builds)
    class _BarePage:
        pass
    result2 = ctx.apply_emulation(_BarePage())
    assert result2 == {"geolocation": False, "locale": False,
                       "timezone": False, "headers": False}


# ---------------------------------------------------------------------------
# 12) apply_smart_fingerprint: full pipeline with mocks
# ---------------------------------------------------------------------------

def test_apply_smart_fingerprint_full_pipeline(tmp_path):
    geo = _make_geo()

    with mock.patch.object(builder, "fetch_geo_info", return_value=geo) as m_geo, \
            mock.patch.object(builder, "fetch_public_ipv6",
                              return_value="2001:db8::abcd") as m_v6:
        opts = _StubOptions()
        ctx = apply_smart_fingerprint(
            opts,
            proxy_host="proxy.example.com", proxy_port=8080,
            proxy_user="u", proxy_pwd="p",
            base_dir=str(tmp_path),
            require_country="US",
            rng=random.Random(123),
        )

    m_geo.assert_called_once()
    m_v6.assert_called_once()

    # Geo got enriched with ipv6
    assert ctx.geo.ipv6 == "2001:db8::abcd"

    # All four opts mutations recorded in order
    names = [c[0] for c in opts.calls]
    assert names == ["set_proxy", "set_user_dir", "set_fpfile", "set_window_size"]
    assert opts.calls[0][1] == ("http://proxy.example.com:8080",)

    # fpfile actually written and contains httpauth
    assert os.path.isfile(ctx.fpfile_path)
    with open(ctx.fpfile_path, encoding="utf-8") as f:
        text = f.read()
    assert "httpauth.username:u" in text
    assert "httpauth.password:p" in text
    assert "local_webrtc_ipv6:2001:db8::abcd" in text

    # userdir under provided base_dir
    assert os.path.commonpath([ctx.userdir, str(tmp_path)]) == str(tmp_path)


# ---------------------------------------------------------------------------
# 13) apply_smart_fingerprint: opts mutation toggles + tolerated failures
# ---------------------------------------------------------------------------

def test_apply_smart_fingerprint_toggles_and_tolerates_opts_errors(tmp_path):
    geo = _make_geo()

    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        # turn off proxy + window_size mutations; force set_fpfile to raise
        opts = _StubOptions(fail_on=["set_fpfile"])
        ctx = apply_smart_fingerprint(
            opts,
            proxy_host="proxy.example.com", proxy_port=8080,
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            set_proxy_on_opts=False,
            set_window_size_on_opts=False,
            rng=random.Random(7),
        )

    names = [c[0] for c in opts.calls]
    # set_proxy & set_window_size disabled; set_fpfile raised but was caught.
    assert "set_proxy" not in names
    assert "set_window_size" not in names
    assert "set_user_dir" in names
    assert "set_fpfile" in names

    assert ctx.geo.ipv6 is None
    assert isinstance(ctx.fingerprint, FingerprintProfile)
