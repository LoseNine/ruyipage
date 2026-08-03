# -*- coding: utf-8 -*-
"""Unit tests for ``ruyipage._fingerprint.builder``.

All tests are fully mocked: no real network calls, no browser launches.
The bundled JSON data files are used as-is to exercise the loader.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import random
from collections.abc import Awaitable as AwaitableABC
from typing import Any, Dict, List, get_args, get_origin, get_type_hints
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
    coerce_manual_geo,
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

    def __init__(self, fail_on: List[str] = None, browser_path: str = None):
        self.calls: List[tuple] = []
        self.fail_on = set(fail_on or [])
        self.browser_path = browser_path

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

    def set_argument(self, argument: str) -> None:
        self._record("set_argument", argument)

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


def test_bundled_language_tags_match_accept_language_tags():
    data = builder._load_region_locales(builder.default_region_locales_path())

    for country_code, entry in data["countries"].items():
        language_tags = [part.strip() for part in entry["language"].split(",")]
        accept_tags = [
            part.split(";", 1)[0].strip()
            for part in entry["accept_language"].split(",")
        ]
        assert language_tags == accept_tags, country_code


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
    pd = build_proxies_dict("proxy.example.com", 1000, "u", "p", scheme="socks5")
    assert pd == {
        "http": "socks5h://u:p@proxy.example.com:1000",
        "https": "socks5h://u:p@proxy.example.com:1000",
    }


def test_geo_sources_have_registered_parsers():
    assert len(builder._GEO_SOURCES) == 10
    tags = [tag for tag, _url, _parser_key in builder._GEO_SOURCES]
    assert len(tags) == len(set(tags))
    for _tag, _url, parser_key in builder._GEO_SOURCES:
        assert parser_key in builder._PARSERS


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


def test_fetch_geo_info_fallback_to_late_source():
    failures = [IOError("boom") for _ in range(8)]
    payloads = failures + [
        {
            "ipAddress": "203.0.113.88",
            "countryCode": "US",
            "countryName": "United States",
            "regionName": "California",
            "cityName": "Los Angeles",
            "timeZone": "America/Los_Angeles",
            "latitude": 34.0522,
            "longitude": -118.2437,
        },
    ]

    def fake_http(url: str, proxies: Any, timeout: float) -> Dict[str, Any]:
        item = payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    with mock.patch.object(builder, "_http_get_json", side_effect=fake_http):
        geo = fetch_geo_info(timeout=1.0, retries_per_source=0)

    assert geo.ip == "203.0.113.88"
    assert geo.country_code == "US"
    assert geo.source == "freeipapi"


def test_new_geo_source_parsers():
    cases = [
        (
            "ipapi_is",
            {
                "ip": "203.0.113.1",
                "location": {
                    "country_code": "US",
                    "country": "United States",
                    "state": "California",
                    "city": "Los Angeles",
                    "timezone": "America/Los_Angeles",
                    "latitude": 34.0522,
                    "longitude": -118.2437,
                },
            },
            "ipapi-is",
        ),
        (
            "ip_guide",
            {
                "ip": "203.0.113.2",
                "network": {"autonomous_system": {"country": "US"}},
                "location": {
                    "country": "United States",
                    "region": "Colorado",
                    "city": "Denver",
                    "timezone": "America/Denver",
                    "latitude": "39.7392",
                    "longitude": "-104.9903",
                },
            },
            "ip-guide",
        ),
        (
            "ipwhois_app",
            {
                "ip": "203.0.113.3",
                "country_code": "US",
                "country": "United States",
                "region": "New York",
                "city": "New York",
                "timezone": "America/New_York",
                "latitude": 40.7128,
                "longitude": -74.006,
            },
            "ipwhois-app",
        ),
        (
            "freeipapi",
            {
                "ipAddress": "203.0.113.4",
                "countryCode": "US",
                "countryName": "United States",
                "regionName": "Washington",
                "cityName": "Seattle",
                "timeZones": ["America/Los_Angeles"],
                "latitude": 47.6062,
                "longitude": -122.3321,
            },
            "freeipapi",
        ),
        (
            "reallyfreegeoip",
            {
                "ip": "203.0.113.5",
                "country_code": "US",
                "country_name": "United States",
                "region_name": "Texas",
                "city": "Dallas",
                "time_zone": "America/Chicago",
                "latitude": 32.7767,
                "longitude": -96.797,
            },
            "reallyfreegeoip",
        ),
    ]

    for parser_key, payload, source in cases:
        geo = builder._PARSERS[parser_key](payload)
        assert geo.ip.startswith("203.0.113.")
        assert geo.country_code == "US"
        assert geo.timezone.startswith("America/")
        assert geo.source == source
        builder._validate_geo(geo)


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


def test_fetch_geo_info_propagates_fingerprint_config_error():
    payload = {
        "ip": "203.0.113.10",
        "country_code": "US",
        "country": "United States",
        "region": "California",
        "city": "Los Angeles",
        "timezone": "America/Los_Angeles",
        "latitude": 34.0522,
        "longitude": -118.2437,
    }
    error = FingerprintConfigError("iana_timezones.json not found")

    with mock.patch.object(builder, "_http_get_json", return_value=payload), \
            mock.patch.object(builder, "_validate_geo", side_effect=error):
        with pytest.raises(FingerprintConfigError, match="iana_timezones"):
            fetch_geo_info(timeout=0.5, retries_per_source=0)


def test_coerce_manual_geo_from_mapping():
    geo = coerce_manual_geo({
        "ip": "75.166.187.10",
        "country_code": "us",
        "timezone": "America/Denver",
        "latitude": "39.7392",
        "longitude": "-104.9903",
        "country": "United States",
        "region": "Colorado",
        "city": "Denver",
    }, require_country="US")

    assert geo.country_code == "US"
    assert geo.source == "manual"
    assert geo.latitude == 39.7392
    assert geo.longitude == -104.9903


def test_coerce_manual_geo_requires_fields():
    with pytest.raises(GeoError, match="manual_geo missing required fields"):
        coerce_manual_geo({"ip": "75.166.187.10", "country_code": "US"})


def test_coerce_manual_geo_enforces_required_country():
    with pytest.raises(CountryMismatchError):
        coerce_manual_geo({
            "ip": "75.166.187.10",
            "country_code": "CA",
            "timezone": "America/Toronto",
            "latitude": 43.6532,
            "longitude": -79.3832,
        }, require_country="US")


@pytest.mark.parametrize(
    "overrides",
    [
        {"ip": "not-an-ip"},
        {"ipv6": "192.0.2.1"},
        {"timezone": "America/Definitely_Fake"},
        {"latitude": 90.1},
        {"longitude": float("inf")},
    ],
)
def test_coerce_manual_geo_wraps_validation_failures_as_geo_error(overrides):
    value = dataclasses.asdict(_make_geo())
    value.update(overrides)

    with pytest.raises(GeoError):
        coerce_manual_geo(value)


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
    assert fp_a.audio_seed == fp_b.audio_seed
    assert 1 <= fp_a.canvas_seed <= (1 << 64) - 1
    assert 1 <= fp_a.audio_seed <= (1 << 64) - 1
    assert fp_a.audio_seed != fp_a.canvas_seed
    assert fp_a.firefox_version == 155
    assert "Firefox/{}.0".format(fp_a.firefox_version) in fp_a.useragent
    assert isinstance(fp_a.hardware, HardwareProfile)


def test_pick_fingerprint_uses_exact_requested_firefox_major():
    fp = pick_fingerprint(
        _make_geo(),
        firefox_version=155,
        rng=random.Random(42),
    )

    assert fp.firefox_version == 155
    assert "rv:155.0" in fp.useragent
    assert "Firefox/155.0" in fp.useragent


def test_pick_fingerprint_does_not_jitter_firefox_major_version():
    versions = {
        pick_fingerprint(_make_geo(), rng=random.Random(seed)).firefox_version
        for seed in range(20)
    }

    assert versions == {155}


def test_pick_fingerprint_missing_base_version_falls_back_to_155(tmp_path):
    with open(builder.default_fingerprints_path(), encoding="utf-8") as source:
        fingerprint_data = json.load(source)
    fingerprint_data.pop("firefox_base_version")
    fingerprint_path = tmp_path / "fingerprints.json"
    fingerprint_path.write_text(json.dumps(fingerprint_data), encoding="utf-8")

    profile = pick_fingerprint(
        _make_geo(),
        fingerprints_path=str(fingerprint_path),
        rng=random.Random(42),
    )

    assert profile.firefox_version == 155


def test_fingerprint_profile_legacy_constructor_derives_audio_seed():
    hardware = list_hardware_profiles()[0]
    country = get_country_profile("US")
    profile = FingerprintProfile(
        "legacy",
        151,
        "Mozilla/5.0 Firefox/151.0",
        hardware,
        country,
        175,
        country.language_primary,
        country.accept_language,
    )

    assert profile.language_primary == country.language_primary
    assert profile.accept_language == country.accept_language
    assert isinstance(profile.audio_seed, int)
    assert 1 <= profile.audio_seed < (1 << 64)
    assert profile.audio_seed != profile.canvas_seed


def test_detect_firefox_major_version_from_binary_output():
    completed = mock.Mock(stdout="Mozilla Firefox 155.0a1\n", stderr="")
    with mock.patch.object(builder.subprocess, "run", return_value=completed) as run:
        assert builder._detect_firefox_major_version("C:/firefox/firefox.exe") == 155

    run.assert_called_once()


def test_detect_firefox_major_version_returns_none_for_unknown_output():
    completed = mock.Mock(stdout="unexpected", stderr="")
    with mock.patch.object(builder.subprocess, "run", return_value=completed):
        assert builder._detect_firefox_major_version("C:/firefox/firefox.exe") is None


# ---------------------------------------------------------------------------
# 9) write_fpfile schema and atomicity
# ---------------------------------------------------------------------------

def test_write_fpfile_schema(tmp_path):
    geo = _make_geo(ipv6="2001:db8::1")
    fp = pick_fingerprint(geo, rng=random.Random(1))
    out = tmp_path / "fpfile.txt"
    write_fpfile(
        str(out),
        geo,
        fp,
        proxy_user="u",
        proxy_pwd="p",
        webrtc_local_ipv4="192.0.2.10",
        webrtc_local_ipv6="2001:db8::10",
        webrtc_public_ipv4="198.51.100.10",
        webrtc_public_ipv6="2001:db8::20",
    )

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
        "geolocation.enabled",
        "geolocation.latitude",
        "geolocation.longitude",
        "geolocation.accuracy",
        "geolocation.altitude",
        "geolocation.altitudeAccuracy",
        "geolocation.heading",
        "geolocation.speed",
        "geolocation.timestamp",
        "geolocation.permission",
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
        "canvas.mode",
        "canvas.seed",
        "canvas.strength",
        "canvas.preserveAlpha",
        "canvas.preserveWhitePoint",
        "canvas.pngMetadata",
        "audio.seed",
        "httpauth.username",
        "httpauth.password",
    ]

    assert actual_keys == expected_keys, (
        "fpfile key mismatch!\nexpected: {}\nactual:   {}".format(
            expected_keys, actual_keys)
    )

    # --- spot-check representative values ---
    assert lines[0] == "webdriver:0"
    assert "local_webrtc_ipv4:192.0.2.10" in lines
    assert "local_webrtc_ipv6:2001:db8::10" in lines
    assert "public_webrtc_ipv4:198.51.100.10" in lines
    assert "public_webrtc_ipv6:2001:db8::20" in lines
    assert any(line.startswith("timezone:America/Los_Angeles") for line in lines)
    assert "geolocation.enabled:true" in lines
    assert "geolocation.latitude:34.0522" in lines
    assert "geolocation.longitude:-118.2437" in lines
    assert "geolocation.accuracy:15000" in lines
    assert "geolocation.altitude:null" in lines
    assert "geolocation.altitudeAccuracy:null" in lines
    assert "geolocation.heading:null" in lines
    assert "geolocation.speed:null" in lines
    assert "geolocation.timestamp:now" in lines
    assert "geolocation.permission:granted" in lines
    assert any(line.startswith("useragent:Mozilla/5.0") for line in lines)
    assert "canvas.mode:pixel" in lines
    assert "canvas.seed:{}".format(fp.canvas_seed) in lines
    assert "canvas.strength:low" in lines
    assert "canvas.preserveAlpha:true" in lines
    assert "canvas.preserveWhitePoint:true" in lines
    assert "canvas.pngMetadata:false" in lines
    assert "audio.seed:{}".format(fp.audio_seed) in lines
    assert any(line == "httpauth.username:u" for line in lines)
    assert any(line == "httpauth.password:p" for line in lines)
    assert not any(line.startswith("width:") for line in lines)
    assert not any(line.startswith("height:") for line in lines)


def test_write_fpfile_default_omits_webrtc_overrides_and_auth(tmp_path):
    """Geo lookup data must not be guessed into ICE policy overrides."""
    geo = _make_geo(ipv6="2001:db8::1")
    fp = pick_fingerprint(geo, rng=random.Random(1))
    out = tmp_path / "fpfile.txt"
    write_fpfile(str(out), geo, fp)

    text = out.read_text(encoding="utf-8")
    actual_keys = [line.split(":", 1)[0] for line in text.strip().splitlines()]

    # Native WebRTC policy remains enabled only when the caller opts in.
    assert "local_webrtc_ipv4" not in actual_keys
    assert "local_webrtc_ipv6" not in actual_keys
    assert "public_webrtc_ipv4" not in actual_keys
    assert "public_webrtc_ipv6" not in actual_keys
    # httpauth keys must not appear
    assert "httpauth.host" not in actual_keys
    assert "httpauth.port" not in actual_keys
    assert "httpauth.username" not in actual_keys
    assert "httpauth.password" not in actual_keys

    # Core keys still preserve their deterministic order.
    expected_core_keys = [
        "webdriver",
        "timezone",
        "language",
        "geolocation.enabled",
        "geolocation.latitude",
        "geolocation.longitude",
        "geolocation.accuracy",
        "geolocation.altitude",
        "geolocation.altitudeAccuracy",
        "geolocation.heading",
        "geolocation.speed",
        "geolocation.timestamp",
        "geolocation.permission",
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
        "canvas.mode",
        "canvas.seed",
        "canvas.strength",
        "canvas.preserveAlpha",
        "canvas.preserveWhitePoint",
        "canvas.pngMetadata",
        "audio.seed",
    ]
    assert actual_keys == expected_core_keys
    assert not any(key in ("width", "height") for key in actual_keys)


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("webrtc_local_ipv4", "2001:db8::10"),
        ("webrtc_public_ipv4", "not-an-ip"),
        ("webrtc_local_ipv6", "192.0.2.10"),
        ("webrtc_public_ipv6", "2001:db8::10%7"),
    ],
)
def test_write_fpfile_rejects_invalid_webrtc_override_family(
    tmp_path, keyword, value
):
    geo = _make_geo()
    fp = pick_fingerprint(geo, rng=random.Random(1))

    with pytest.raises(FingerprintError, match=keyword):
        write_fpfile(
            str(tmp_path / "fp.txt"),
            geo,
            fp,
            **{keyword: value},
        )


# ---------------------------------------------------------------------------
# 10) write_fpfile: extra cannot collide with reserved keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "reserved_key",
    [
        "useragent",
        "canvas",
        "canvas.enabled",
        "canvas.scope",
        "canvas.mode",
        "canvas.seed",
        "canvas.strength",
        "canvas.preserveAlpha",
        "canvas.preserveWhitePoint",
        "canvas.pngMetadata",
        "audio",
        "audio.enabled",
        "audio.mode",
        "audio.scope",
        "audio.seed",
        "geolocation.enabled",
        "geolocation.latitude",
        "geolocation.longitude",
        "geolocation.accuracy",
        "geolocation.altitude",
        "geolocation.altitudeAccuracy",
        "geolocation.heading",
        "geolocation.speed",
        "geolocation.timestamp",
        "geolocation.permission",
        "socksauth.host",
        "socksauth.port",
        "socksauth.username",
        "socksauth.password",
    ],
)
def test_write_fpfile_extra_collision_rejected(tmp_path, reserved_key):
    geo = _make_geo()
    fp = pick_fingerprint(geo, rng=random.Random(1))
    with pytest.raises(FingerprintError):
        write_fpfile(
            str(tmp_path / "fp.txt"), geo, fp,
            extra={reserved_key: "evil"},
        )


@pytest.mark.parametrize(
    ("extra_key", "extra_value"),
    [
        (" canvas.seed", "1"),
        ("canvas.seed ", "1"),
        ("canvas.seed:ignored", "1"),
        ("canvas.seed=ignored", "1"),
        ("note\ncanvas.seed", "1"),
        ("note", "value\ncanvas.seed:1"),
    ],
)
def test_write_fpfile_extra_injection_rejected(
    tmp_path, extra_key, extra_value
):
    geo = _make_geo()
    fp = pick_fingerprint(geo, rng=random.Random(1))
    with pytest.raises(FingerprintError):
        write_fpfile(
            str(tmp_path / "fp.txt"),
            geo,
            fp,
            extra={extra_key: extra_value},
        )


@pytest.mark.parametrize(
    "canvas_seed",
    [0, -1, 1 << 64, True, 1.5, "1", None],
)
def test_write_fpfile_rejects_invalid_canvas_seed(tmp_path, canvas_seed):
    geo = _make_geo()
    fp = dataclasses.replace(
        pick_fingerprint(geo, rng=random.Random(1)), canvas_seed=canvas_seed
    )
    with pytest.raises(FingerprintError, match="canvas_seed"):
        write_fpfile(str(tmp_path / "fp.txt"), geo, fp)


def test_write_fpfile_accepts_max_canvas_seed(tmp_path):
    geo = _make_geo()
    fp = dataclasses.replace(
        pick_fingerprint(geo, rng=random.Random(1)),
        canvas_seed=(1 << 64) - 1,
    )
    path = tmp_path / "fp.txt"
    write_fpfile(str(path), geo, fp)
    assert "canvas.seed:18446744073709551615" in path.read_text(
        encoding="utf-8"
    ).splitlines()


@pytest.mark.parametrize(
    "audio_seed",
    [0, -1, 1 << 64, True, 1.5, "1", None],
)
def test_write_fpfile_rejects_invalid_audio_seed(tmp_path, audio_seed):
    geo = _make_geo()
    fp = dataclasses.replace(
        pick_fingerprint(geo, rng=random.Random(1)), audio_seed=audio_seed
    )
    with pytest.raises(FingerprintError, match="audio_seed"):
        write_fpfile(str(tmp_path / "fp.txt"), geo, fp)


def test_write_fpfile_accepts_max_audio_seed(tmp_path):
    geo = _make_geo()
    fp = dataclasses.replace(
        pick_fingerprint(geo, rng=random.Random(1)),
        audio_seed=(1 << 64) - 1,
    )
    path = tmp_path / "fp.txt"
    write_fpfile(str(path), geo, fp)
    assert "audio.seed:18446744073709551615" in path.read_text(
        encoding="utf-8"
    ).splitlines()


def test_write_fpfile_accepts_explicit_geolocation_fields(tmp_path):
    geo = _make_geo()
    fp = pick_fingerprint(geo, rng=random.Random(1))
    path = tmp_path / "fp.txt"
    write_fpfile(
        str(path),
        geo,
        fp,
        geolocation_enabled=False,
        geolocation_latitude=-90,
        geolocation_longitude=180,
        geolocation_accuracy=0.25,
        geolocation_altitude=-12.5,
        geolocation_altitude_accuracy=0,
        geolocation_heading=359.5,
        geolocation_speed=0.1,
        geolocation_timestamp=0,
        geolocation_permission="denied",
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith("geolocation.")] == [
        "geolocation.enabled:false",
        "geolocation.latitude:-90",
        "geolocation.longitude:180",
        "geolocation.accuracy:0.25",
        "geolocation.altitude:-12.5",
        "geolocation.altitudeAccuracy:0",
        "geolocation.heading:359.5",
        "geolocation.speed:0.1",
        "geolocation.timestamp:0",
        "geolocation.permission:denied",
    ]


@pytest.mark.parametrize(
    "geolocation_kwargs",
    [
        {"geolocation_enabled": 1},
        {"geolocation_latitude": True},
        {"geolocation_latitude": -90.0001},
        {"geolocation_latitude": float("nan")},
        {"geolocation_longitude": -180.0001},
        {"geolocation_longitude": float("inf")},
        {"geolocation_accuracy": 0},
        {"geolocation_accuracy": float("inf")},
        {"geolocation_altitude": True},
        {"geolocation_altitude": float("nan")},
        {"geolocation_altitude_accuracy": -1},
        {"geolocation_altitude_accuracy": 1},
        {"geolocation_heading": -1, "geolocation_speed": 1},
        {"geolocation_heading": 360, "geolocation_speed": 1},
        {"geolocation_heading": 0},
        {"geolocation_heading": 0, "geolocation_speed": 0},
        {"geolocation_speed": -1},
        {"geolocation_timestamp": True},
        {"geolocation_timestamp": -1},
        {"geolocation_timestamp": 1 << 64},
        {"geolocation_timestamp": "later"},
        {"geolocation_permission": "GRANTED"},
        {"geolocation_permission": "allow"},
    ],
)
def test_write_fpfile_rejects_invalid_geolocation_fields(
    tmp_path, geolocation_kwargs
):
    geo = _make_geo()
    fp = pick_fingerprint(geo, rng=random.Random(1))
    with pytest.raises(FingerprintError, match="geolocation"):
        write_fpfile(
            str(tmp_path / "fp.txt"), geo, fp, **geolocation_kwargs
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
    assert "audio={}".format(fp.audio_seed) in s

    d = ctx.to_dict()
    assert d["country_code"] == "US"
    assert d["firefox_version"] == fp.firefox_version
    assert d["audio_seed"] == fp.audio_seed
    assert d["webrtc"] == {
        "mode": "native",
        "local_ipv4": None,
        "local_ipv6": None,
        "public_ipv4": None,
        "public_ipv6": None,
    }

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
        def set_screen_size(self, width, height):
            self.calls.append(("screen", width, height))

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
                      "timezone": True, "headers": True, "screen": True}
    assert page.network.headers == {
        "Accept-Language": fp.accept_language,
    }
    assert page.emulation.calls[0] == ("screen", fp.hardware.width, fp.hardware.height)
    assert ("geo", geo.latitude, geo.longitude, 15000) in page.emulation.calls
    assert ("loc", tuple(fp.country.language.split(","))) in page.emulation.calls

    # missing hooks degrade gracefully (e.g. older ruyipage builds)
    class _BarePage:
        pass
    result2 = ctx.apply_emulation(_BarePage())
    assert result2 == {"geolocation": False, "locale": False,
                       "timezone": False, "headers": False, "screen": False}

    # opt-out skips screen overlay
    page2 = _Page()
    result3 = ctx.apply_emulation(page2, set_screen_size=False)
    assert result3 == {"geolocation": True, "locale": True,
                       "timezone": True, "headers": True, "screen": False}
    assert all(call[0] != "screen" for call in page2.emulation.calls)

    class _FlakyEmu(_Emu):
        def set_screen_size(self, width, height):
            raise RuntimeError("screen unavailable")

    class _FlakyPage:
        def __init__(self):
            self.emulation = _FlakyEmu()
            self.network = _Net()

    logs: List[str] = []
    flaky_page = _FlakyPage()
    result4 = ctx.apply_emulation(flaky_page, logger=logs.append)
    assert result4 == {"geolocation": True, "locale": True,
                       "timezone": True, "headers": True, "screen": False}
    assert any("screen skipped" in line for line in logs)
    assert flaky_page.emulation.calls[0][0] == "geo"


@pytest.mark.asyncio
async def test_fingerprint_context_apply_emulation_awaits_async_page_hooks():
    geo = _make_geo(country_code="MX", timezone="America/Mexico_City")
    fp = pick_fingerprint(geo, rng=random.Random(7))
    ctx = FingerprintContext(
        geo=geo, fingerprint=fp,
        userdir="/tmp/x", fpfile_path="/tmp/x/fp.txt",
    )

    class _AsyncEmu:
        def __init__(self):
            self.calls = []

        async def set_screen_size(self, width, height):
            self.calls.append(("screen", width, height))

        async def set_geolocation(self, lat, lon, accuracy=100):
            self.calls.append(("geo", lat, lon, accuracy))

        async def set_locale(self, langs):
            self.calls.append(("loc", tuple(langs)))

        async def set_timezone(self, tz):
            self.calls.append(("tz", tz))

    class _AsyncNet:
        def __init__(self):
            self.headers = None

        async def set_extra_headers(self, headers):
            self.headers = dict(headers)

    class _AsyncPage:
        def __init__(self):
            self.emulation = _AsyncEmu()
            self.network = _AsyncNet()

    page = _AsyncPage()
    pending = ctx.apply_emulation(page)
    assert inspect.isawaitable(pending)
    result = await pending

    assert result == {"geolocation": True, "locale": True,
                      "timezone": True, "headers": True, "screen": True}
    assert ("geo", geo.latitude, geo.longitude, 15000) in page.emulation.calls
    assert ("loc", tuple(fp.country.language.split(","))) in page.emulation.calls
    assert page.network.headers == {
        "Accept-Language": fp.accept_language,
    }


@pytest.mark.asyncio
async def test_fingerprint_context_apply_emulation_handles_mixed_hooks():
    geo = _make_geo(country_code="DE", timezone="Europe/Berlin")
    fp = pick_fingerprint(geo, rng=random.Random(7))
    ctx = FingerprintContext(
        geo=geo,
        fingerprint=fp,
        userdir="/tmp/x",
        fpfile_path="/tmp/x/fp.txt",
    )
    calls = []

    class _MixedEmu:
        def set_screen_size(self, width, height):
            calls.append(("screen", width, height))

        async def set_geolocation(self, latitude, longitude, accuracy=100):
            calls.append(("geolocation", latitude, longitude, accuracy))

        def set_locale(self, locales):
            calls.append(("locale", tuple(locales)))

        async def set_timezone(self, timezone):
            calls.append(("timezone", timezone))

    class _MixedNetwork:
        def set_extra_headers(self, headers):
            calls.append(("headers", dict(headers)))

    class _MixedPage:
        emulation = _MixedEmu()
        network = _MixedNetwork()

    result = await ctx.apply_emulation_async(_MixedPage())

    assert result == {
        "screen": True,
        "geolocation": True,
        "locale": True,
        "timezone": True,
        "headers": True,
    }
    assert [call[0] for call in calls] == [
        "screen",
        "geolocation",
        "locale",
        "timezone",
        "headers",
    ]


def test_fingerprint_context_apply_emulation_declares_sync_and_async_results():
    return_hint = get_type_hints(FingerprintContext.apply_emulation)["return"]
    return_types = get_args(return_hint)

    assert Dict[str, bool] in return_types
    awaitable_hint = next(
        hint
        for hint in return_types
        if get_origin(hint) is AwaitableABC
    )
    assert get_args(awaitable_hint) == (Dict[str, bool],)


@pytest.mark.asyncio
async def test_fingerprint_context_apply_emulation_async_is_always_awaitable():
    geo = _make_geo()
    ctx = FingerprintContext(
        geo=geo,
        fingerprint=pick_fingerprint(geo, rng=random.Random(7)),
        userdir="/tmp/x",
        fpfile_path="/tmp/x/fp.txt",
    )

    result = await ctx.apply_emulation_async(
        object(),
        set_screen_size=False,
        set_geolocation=False,
        set_locale=False,
        set_timezone=False,
        set_extra_headers=False,
    )

    assert result == {
        "screen": False,
        "geolocation": False,
        "locale": False,
        "timezone": False,
        "headers": False,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": float("nan")},
        {"longitude": float("inf")},
        {"latitude": 90.1},
        {"longitude": -180.1},
        {"timezone": "not-a-timezone"},
        {"timezone": "Etc/Unknown"},
        {"timezone": "Factory"},
    ],
)
def test_validate_geo_rejects_invalid_coordinates_and_timezone(overrides):
    with pytest.raises(ValueError):
        builder._validate_geo(_make_geo(**overrides))


def test_geo_parser_rejects_missing_coordinates_instead_of_defaulting_to_zero():
    with pytest.raises((KeyError, ValueError, TypeError)):
        builder._PARSERS["ipapi"]({
            "ip": "203.0.113.10",
            "country": "US",
            "country_name": "United States",
            "region": "CA",
            "city": "LA",
            "timezone": "America/Los_Angeles",
        })


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
            webrtc_local_ipv6="2001:db8::abcd",
            webrtc_public_ipv6="2001:db8::abcd",
            rng=random.Random(123),
        )

    m_geo.assert_called_once()
    m_v6.assert_called_once()

    # Geo got enriched with ipv6
    assert ctx.geo.ipv6 == "2001:db8::abcd"

    # Default window-size mutation is disabled; startup stays script-accessible.
    names = [c[0] for c in opts.calls]
    assert names == ["set_proxy", "set_user_dir", "set_fpfile", "set_argument"]
    assert opts.calls[0][1] == ("http://proxy.example.com:8080",)

    # fpfile actually written and contains httpauth
    assert os.path.isfile(ctx.fpfile_path)
    with open(ctx.fpfile_path, encoding="utf-8") as f:
        text = f.read()
    assert "httpauth.host:proxy.example.com" in text
    assert "httpauth.port:8080" in text
    assert "httpauth.username:u" in text
    assert "httpauth.password:p" in text
    assert "local_webrtc_ipv6:2001:db8::abcd" in text
    assert "public_webrtc_ipv6:2001:db8::abcd" in text
    assert "width:" not in text
    assert "height:" not in text

    # userdir under provided base_dir
    assert os.path.commonpath([ctx.userdir, str(tmp_path)]) == str(tmp_path)


def test_apply_smart_fingerprint_uses_detected_browser_major(tmp_path):
    geo = _make_geo()
    browser_path = r"C:\firefox\firefox.exe"

    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None), \
            mock.patch.object(
                builder, "_detect_firefox_major_version", return_value=155
            ) as detect:
        ctx = apply_smart_fingerprint(
            _StubOptions(browser_path=browser_path),
            base_dir=str(tmp_path),
            require_country="US",
            fetch_ipv6=False,
            rng=random.Random(123),
        )

    detect.assert_called_once_with(browser_path)
    assert ctx.fingerprint.firefox_version == 155
    assert "Firefox/155.0" in ctx.fingerprint.useragent


def test_apply_smart_fingerprint_sets_script_accessible_start_page(tmp_path):
    geo = _make_geo()
    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        opts = _StubOptions()
        apply_smart_fingerprint(
            opts,
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            rng=random.Random(123),
        )

    assert ("set_argument", ("about:blank",)) in opts.calls


def test_apply_smart_fingerprint_can_preserve_custom_start_page(tmp_path):
    geo = _make_geo()
    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        opts = _StubOptions()
        apply_smart_fingerprint(
            opts,
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            set_startup_page_on_opts=False,
            rng=random.Random(123),
        )

    assert all(call[0] != "set_argument" for call in opts.calls)


def test_apply_smart_fingerprint_passes_explicit_webrtc_overrides(tmp_path):
    geo = _make_geo(ipv6="2001:db8::1")
    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        ctx = apply_smart_fingerprint(
            _StubOptions(),
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            webrtc_local_ipv4="192.0.2.10",
            webrtc_local_ipv6="2001:0db8:0:0::10",
            webrtc_public_ipv4="198.51.100.10",
            webrtc_public_ipv6="2001:0db8:0:0::20",
            rng=random.Random(123),
        )

    lines = open(ctx.fpfile_path, encoding="utf-8").read().splitlines()
    assert "local_webrtc_ipv4:192.0.2.10" in lines
    assert "local_webrtc_ipv6:2001:db8::10" in lines
    assert "public_webrtc_ipv4:198.51.100.10" in lines
    assert "public_webrtc_ipv6:2001:db8::20" in lines
    assert ctx.webrtc_local_ipv4 == "192.0.2.10"
    assert ctx.webrtc_local_ipv6 == "2001:db8::10"
    assert ctx.webrtc_public_ipv4 == "198.51.100.10"
    assert ctx.webrtc_public_ipv6 == "2001:db8::20"
    assert ctx.to_dict()["webrtc"] == {
        "mode": "explicit",
        "local_ipv4": "192.0.2.10",
        "local_ipv6": "2001:db8::10",
        "public_ipv4": "198.51.100.10",
        "public_ipv6": "2001:db8::20",
    }


def test_apply_smart_fingerprint_keeps_custom_geolocation_for_overlay(tmp_path):
    geo = _make_geo()
    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        ctx = apply_smart_fingerprint(
            _StubOptions(),
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            geolocation_latitude=40.7128,
            geolocation_longitude=-74.006,
            geolocation_accuracy=25,
            geolocation_altitude=12.5,
            geolocation_altitude_accuracy=3.5,
            geolocation_heading=45,
            geolocation_speed=2.25,
            geolocation_permission="granted",
            rng=random.Random(123),
        )

    assert ctx.geolocation.latitude == 40.7128
    assert ctx.geolocation.longitude == -74.006
    assert ctx.geolocation.accuracy == 25
    assert ctx.geolocation.altitude == 12.5
    assert ctx.geolocation.altitude_accuracy == 3.5
    assert ctx.geolocation.heading == 45
    assert ctx.geolocation.speed == 2.25

    class _Emulation:
        def __init__(self):
            self.calls = []

        def set_geolocation(self, latitude, longitude, **kwargs):
            self.calls.append((latitude, longitude, kwargs))

    class _Page:
        def __init__(self):
            self.emulation = _Emulation()

    page = _Page()
    result = ctx.apply_emulation(
        page,
        set_screen_size=False,
        set_locale=False,
        set_timezone=False,
        set_extra_headers=False,
    )

    assert result["geolocation"] is True
    assert page.emulation.calls == [
        (
            40.7128,
            -74.006,
            {
                "accuracy": 25,
                "altitude": 12.5,
                "altitude_accuracy": 3.5,
                "heading": 45,
                "speed": 2.25,
            },
        )
    ]


def test_fingerprint_context_preserves_custom_timestamp_in_kernel(tmp_path):
    geo = _make_geo()
    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        ctx = apply_smart_fingerprint(
            _StubOptions(),
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            geolocation_timestamp=123456789,
            rng=random.Random(123),
        )

    calls = []

    class _Emulation:
        def set_geolocation(self, *args, **kwargs):
            raise AssertionError("fixed timestamp must not receive coordinates")

        def clear_geolocation(self):
            calls.append("clear")

    class _Page:
        emulation = _Emulation()

    logs = []
    result = ctx.apply_emulation(
        _Page(),
        set_screen_size=False,
        set_locale=False,
        set_timezone=False,
        set_extra_headers=False,
        logger=logs.append,
    )

    assert result["geolocation"] is False
    assert calls == ["clear"]
    assert any("custom timestamp is kernel-managed" in line for line in logs)


def test_apply_smart_fingerprint_does_not_overlay_disabled_geolocation(tmp_path):
    geo = _make_geo()
    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        ctx = apply_smart_fingerprint(
            _StubOptions(),
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            geolocation_enabled=False,
            rng=random.Random(123),
        )

    class _Emulation:
        def __init__(self):
            self.calls = []

        def set_geolocation(self, latitude, longitude, accuracy=100):
            self.calls.append((latitude, longitude, accuracy))

        def clear_geolocation(self):
            self.calls.append("clear")

    class _Page:
        def __init__(self):
            self.emulation = _Emulation()

    page = _Page()
    result = ctx.apply_emulation(
        page,
        set_screen_size=False,
        set_locale=False,
        set_timezone=False,
        set_extra_headers=False,
    )

    assert ctx.geolocation.enabled is False
    assert result["geolocation"] is False
    assert page.emulation.calls == ["clear"]


def test_apply_smart_fingerprint_default_keeps_fpfile_size_without_window_mutation(tmp_path):
    geo = _make_geo()
    hw = next(p for p in list_hardware_profiles() if p.id == "win-hd4600")
    country = get_country_profile("US")
    fp = FingerprintProfile(
        profile_id=hw.id,
        firefox_version=152,
        useragent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) "
            "Gecko/20100101 Firefox/152.0"
        ),
        hardware=hw,
        country=country,
        canvas_seed=175,
        audio_seed=176,
        language_primary=country.language_primary,
        accept_language=country.accept_language,
    )

    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None), \
            mock.patch.object(builder, "pick_fingerprint", return_value=fp):
        opts = _StubOptions()
        ctx = apply_smart_fingerprint(
            opts,
            base_dir=str(tmp_path),
            require_country="US",
            fetch_ipv6=False,
    )

    text = open(ctx.fpfile_path, encoding="utf-8").read()
    assert "width:" not in text
    assert "height:" not in text
    assert all(c[0] != "set_window_size" for c in opts.calls)


def test_apply_smart_fingerprint_never_maps_screen_size_to_outer_window(tmp_path):
    geo = _make_geo()
    hw = next(p for p in list_hardware_profiles() if p.id == "win-hd4600")
    country = get_country_profile("US")
    fp = FingerprintProfile(
        profile_id=hw.id,
        firefox_version=152,
        useragent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) "
            "Gecko/20100101 Firefox/152.0"
        ),
        hardware=hw,
        country=country,
        canvas_seed=175,
        audio_seed=176,
        language_primary=country.language_primary,
        accept_language=country.accept_language,
    )

    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None), \
            mock.patch.object(builder, "pick_fingerprint", return_value=fp):
        opts = _StubOptions()
        logs: List[str] = []
        ctx = apply_smart_fingerprint(
            opts,
            base_dir=str(tmp_path),
            require_country="US",
            fetch_ipv6=False,
            set_window_size_on_opts=True,
            logger=logs.append,
        )

    text = open(ctx.fpfile_path, encoding="utf-8").read()
    assert "width:" not in text
    assert "height:" not in text

    assert all(c[0] != "set_window_size" for c in opts.calls)
    assert any("set_window_size_on_opts is deprecated" in line for line in logs)


def test_apply_smart_fingerprint_supports_socks5_auth_proxy(tmp_path):
    geo = _make_geo()

    with mock.patch.object(builder, "fetch_geo_info", return_value=geo) as m_geo, \
            mock.patch.object(builder, "fetch_public_ipv6",
                              return_value=None):
        opts = _StubOptions()
        ctx = apply_smart_fingerprint(
            opts,
            proxy_scheme="socks5",
            proxy_host="gate.example.com",
            proxy_port=1000,
            proxy_user="user-value",
            proxy_pwd="pass-value",
            base_dir=str(tmp_path),
            require_country="US",
            fetch_ipv6=False,
            rng=random.Random(123),
        )

    m_geo.assert_called_once()
    assert m_geo.call_args.args[0] == {
        "http": "socks5h://user-value:pass-value@gate.example.com:1000",
        "https": "socks5h://user-value:pass-value@gate.example.com:1000",
    }
    assert opts.calls[0][1] == ("socks5://gate.example.com:1000",)

    text = open(ctx.fpfile_path, encoding="utf-8").read()
    assert "socksauth.host:gate.example.com" in text
    assert "socksauth.port:1000" in text
    assert "socksauth.username:user-value" in text
    assert "socksauth.password:pass-value" in text
    assert "httpauth.username:user-value" not in text
    assert "httpauth.password:pass-value" not in text


def test_apply_smart_fingerprint_uses_online_geo_before_manual_geo(tmp_path):
    online_geo = _make_geo(ip="203.0.113.45", source="geojs")

    with mock.patch.object(builder, "fetch_geo_info", return_value=online_geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        ctx = apply_smart_fingerprint(
            _StubOptions(),
            base_dir=str(tmp_path),
            require_country="US",
            manual_geo={
                "ip": "75.166.187.10",
                "country_code": "US",
                "timezone": "America/Denver",
                "latitude": 39.7392,
                "longitude": -104.9903,
            },
            fetch_ipv6=False,
            rng=random.Random(123),
        )

    assert ctx.geo.ip == "203.0.113.45"
    assert ctx.geo.source == "geojs"


def test_apply_smart_fingerprint_falls_back_to_manual_geo(tmp_path):
    with mock.patch.object(builder, "fetch_geo_info", side_effect=GeoError("boom")), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        ctx = apply_smart_fingerprint(
            _StubOptions(),
            base_dir=str(tmp_path),
            require_country="US",
            manual_geo={
                "ip": "75.166.187.10",
                "country_code": "US",
                "timezone": "America/Denver",
                "latitude": 39.7392,
                "longitude": -104.9903,
                "city": "Denver",
            },
            fetch_ipv6=False,
            rng=random.Random(123),
        )

    assert ctx.geo.ip == "75.166.187.10"
    assert ctx.geo.source == "manual"
    assert ctx.geo.city == "Denver"


def test_apply_smart_fingerprint_prompts_for_manual_geo(tmp_path):
    with mock.patch.object(builder, "fetch_geo_info", side_effect=GeoError("boom")):
        with pytest.raises(GeoError, match="Please provide manual_geo"):
            apply_smart_fingerprint(
                _StubOptions(),
                base_dir=str(tmp_path),
                require_country="US",
                fetch_ipv6=False,
            )


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


def test_apply_smart_fingerprint_deprecated_window_flag_does_not_call_opts(tmp_path):
    geo = _make_geo()
    logs: List[str] = []

    with mock.patch.object(builder, "fetch_geo_info", return_value=geo), \
            mock.patch.object(builder, "fetch_public_ipv6", return_value=None):
        opts = _StubOptions(fail_on=["set_window_size"])
        ctx = apply_smart_fingerprint(
            opts,
            proxy_host="proxy.example.com", proxy_port=8080,
            base_dir=str(tmp_path),
            require_country=None,
            fetch_ipv6=False,
            set_window_size_on_opts=True,
            rng=random.Random(7),
            logger=logs.append,
        )

    assert isinstance(ctx, FingerprintContext)
    assert ctx.userdir
    assert ctx.fpfile_path
    assert all(c[0] != "set_window_size" for c in opts.calls)
    assert any("set_window_size_on_opts is deprecated" in line for line in logs)
