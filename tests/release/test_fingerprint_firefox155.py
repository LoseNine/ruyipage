# -*- coding: utf-8 -*-
"""Firefox 155 smart-fingerprint release gate."""

import asyncio
import json
import os
import random
from unittest import mock

import pytest

from ruyipage import FirefoxOptions, FirefoxPage
from ruyipage._fingerprint import builder


FIREFOX_PATH_ENV = "RUYIPAGE_TEST_FIREFOX_PATH"
EXPECTED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) "
    "Gecko/20100101 Firefox/155.0"
)

CUSTOM_GEOLOCATION = {
    "latitude": 35.6762,
    "longitude": 139.6503,
    "accuracy": 7.5,
    "altitude": 44.25,
    "altitudeAccuracy": 1.5,
    "heading": 123.5,
    "speed": 4.25,
}

_FINGERPRINT_HASH_SCRIPT = r"""
(async () => {
  async function sha256(bytes) {
    const digest = await crypto.subtle.digest(
      'SHA-256',
      bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
    );
    return Array.from(new Uint8Array(digest), byte =>
      byte.toString(16).padStart(2, '0')
    ).join('');
  }

  const canvas = document.createElement('canvas');
  canvas.width = 320;
  canvas.height = 96;
  const context = canvas.getContext('2d', {willReadFrequently: true});
  const gradient = context.createLinearGradient(0, 0, 320, 96);
  gradient.addColorStop(0, '#13579b');
  gradient.addColorStop(0.5, '#f2c14e');
  gradient.addColorStop(1, '#2a9d8f');
  context.fillStyle = gradient;
  context.fillRect(0, 0, 320, 96);
  context.globalCompositeOperation = 'multiply';
  context.fillStyle = 'rgba(231, 76, 60, 0.73)';
  context.beginPath();
  context.arc(87.25, 44.75, 31.5, 0.15, 5.83);
  context.fill();
  context.strokeStyle = 'rgba(255, 255, 255, 0.81)';
  context.lineWidth = 3.25;
  context.strokeRect(151.5, 17.5, 131.25, 59.25);
  const canvasBytes = context.getImageData(0, 0, 320, 96).data;

  const offline = new OfflineAudioContext(1, 5000, 44100);
  const oscillator = offline.createOscillator();
  oscillator.type = 'triangle';
  oscillator.frequency.value = 10000;
  const compressor = offline.createDynamicsCompressor();
  compressor.threshold.value = -50;
  compressor.knee.value = 40;
  compressor.ratio.value = 12;
  compressor.attack.value = 0;
  compressor.release.value = 0.25;
  oscillator.connect(compressor);
  compressor.connect(offline.destination);
  oscillator.start(0);
  const rendered = await offline.startRendering();
  const audioSamples = rendered.getChannelData(0);
  const audioBytes = new Uint8Array(
    audioSamples.buffer,
    audioSamples.byteOffset,
    audioSamples.byteLength
  );

  return {
    canvas: await sha256(canvasBytes),
    audio: await sha256(audioBytes)
  };
})()
"""

_GEOLOCATION_SCRIPT = r"""
new Promise((resolve, reject) => {
  navigator.geolocation.getCurrentPosition(
    position => resolve({
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy: position.coords.accuracy,
      altitude: position.coords.altitude,
      altitudeAccuracy: position.coords.altitudeAccuracy,
      heading: position.coords.heading,
      speed: position.coords.speed
    }),
    error => reject(new Error(`${error.code}: ${error.message}`)),
    {enableHighAccuracy: true, maximumAge: 0, timeout: 15000}
  );
})
"""


def _require_firefox_155():
    path = os.environ.get(FIREFOX_PATH_ENV)
    if not path:
        pytest.skip("{} is not set".format(FIREFOX_PATH_ENV))

    major = builder._detect_firefox_major_version(path)
    if major != 155:
        pytest.skip(
            "{} must point to Firefox 155 (detected {!r})".format(
                FIREFOX_PATH_ENV, major
            )
        )
    return path


def _new_options(firefox_path, user_dir=None):
    options = FirefoxOptions()
    options.set_browser_path(firefox_path)
    options.enable_action_visual(False)
    options.set_argument("about:blank")
    if user_dir is not None:
        options.set_user_dir(str(user_dir))
    return options


def _collect_fingerprint_hashes(page):
    hashes = page.run_js(
        _FINGERPRINT_HASH_SCRIPT,
        as_expr=True,
        timeout=30,
    )
    assert set(hashes) == {"canvas", "audio"}
    assert all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in hashes.values()
    )
    return hashes


def _header_value(headers, name):
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    raise AssertionError("response did not contain header {!r}".format(name))


def _unsupported_timezones(page):
    zones = sorted(
        builder._load_iana_timezones(builder._default_iana_timezones_path())
    )
    script = """
    const zones = %s;
    return zones.filter(zone => {
      try {
        new Intl.DateTimeFormat('en-US', {timeZone: zone}).format(0);
        return false;
      } catch (error) {
        return true;
      }
    });
    """ % json.dumps(zones)
    return page.run_js(script, as_expr=False)


def _read_unique_fpfile(path):
    values = {}
    with open(path, encoding="utf-8") as fpfile:
        for line_number, raw_line in enumerate(fpfile, 1):
            line = raw_line.rstrip("\r\n")
            if not line or line.lstrip().startswith(("#", "//")):
                continue
            key, separator, value = line.partition(":")
            assert separator, "invalid fpfile line {}: {!r}".format(
                line_number, line
            )
            assert key not in values, "duplicate fpfile key {!r}".format(key)
            values[key] = value
    return values


async def _apply_emulation_through_real_async_page(context, sync_page):
    from ruyipage.aio import AsyncFirefoxPage

    driver = sync_page._driver._browser_driver
    await driver.switch_to_async()
    async_page = AsyncFirefoxPage(sync_page)
    assert isinstance(async_page, AsyncFirefoxPage)
    return await context.apply_emulation_async(async_page)


@pytest.mark.release
@pytest.mark.browser
@pytest.mark.local_server
def test_firefox155_smart_fingerprint_release_gate(tmp_path, server):
    firefox_path = _require_firefox_155()
    geo = builder.GeoInfo(
        ip="203.0.113.155",
        country_code="JP",
        country="Japan",
        region="Tokyo",
        city="Tokyo",
        timezone="Asia/Tokyo",
        latitude=35.6895,
        longitude=139.6917,
        source="release-fixture",
        ipv6=None,
    )

    options = _new_options(firefox_path)
    with mock.patch.object(
        builder, "fetch_geo_info", return_value=geo
    ), mock.patch.object(builder, "fetch_public_ipv6") as ipv6_lookup:
        context = options.smart_fingerprint(
            base_dir=str(tmp_path / "fingerprint-profiles"),
            require_country="JP",
            fetch_ipv6=False,
            geolocation_latitude=CUSTOM_GEOLOCATION["latitude"],
            geolocation_longitude=CUSTOM_GEOLOCATION["longitude"],
            geolocation_accuracy=CUSTOM_GEOLOCATION["accuracy"],
            geolocation_altitude=CUSTOM_GEOLOCATION["altitude"],
            geolocation_altitude_accuracy=CUSTOM_GEOLOCATION[
                "altitudeAccuracy"
            ],
            geolocation_heading=CUSTOM_GEOLOCATION["heading"],
            geolocation_speed=CUSTOM_GEOLOCATION["speed"],
            geolocation_timestamp="now",
            geolocation_permission="granted",
            rng=random.Random(155),
        )

    ipv6_lookup.assert_not_called()
    assert context.geo == geo
    assert context.fingerprint.firefox_version == 155
    assert context.fingerprint.useragent == EXPECTED_UA

    fpfile_values = _read_unique_fpfile(context.fpfile_path)
    assert fpfile_values.get("useragent") == EXPECTED_UA
    expected_canonical_values = {
        "canvas.mode": "pixel",
        "canvas.seed": str(context.fingerprint.canvas_seed),
        "audio.seed": str(context.fingerprint.audio_seed),
    }
    assert {
        key: fpfile_values.get(key) for key in expected_canonical_values
    } == expected_canonical_values
    expected_geolocation_values = {
        "geolocation.enabled": "true",
        "geolocation.latitude": "35.6762",
        "geolocation.longitude": "139.6503",
        "geolocation.accuracy": "7.5",
        "geolocation.altitude": "44.25",
        "geolocation.altitudeAccuracy": "1.5",
        "geolocation.heading": "123.5",
        "geolocation.speed": "4.25",
        "geolocation.timestamp": "now",
        "geolocation.permission": "granted",
    }
    assert {
        key: fpfile_values.get(key) for key in expected_geolocation_values
    } == expected_geolocation_values
    assert not any(
        key.startswith(("local_webrtc_", "public_webrtc_"))
        for key in fpfile_values
    )

    fingerprint_page = None
    try:
        fingerprint_page = FirefoxPage(options)
        fingerprint_process_args = [
            str(argument) for argument in fingerprint_page.browser.process.args
        ]
        assert "--fpfile={}".format(context.fpfile_path) in (
            fingerprint_process_args
        )
        overlays = context.apply_emulation(fingerprint_page)
        assert overlays == {
            "screen": True,
            "geolocation": True,
            "locale": True,
            "timezone": True,
            "headers": True,
        }

        fingerprint_page.get(server.get_url("/nav/basic"))
        runtime = fingerprint_page.run_js(
            """
            return {
              userAgent: navigator.userAgent,
              languages: Array.from(navigator.languages),
              timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
              actionVisual: typeof window.__ruyiAV,
              visualCanvas: document.getElementById('__ruyi_av_canvas__') !== null
            };
            """,
            as_expr=False,
        )
        expected_languages = [
            tag.strip()
            for tag in context.fingerprint.country.language.split(",")
            if tag.strip()
        ]
        assert runtime == {
            "userAgent": EXPECTED_UA,
            "languages": expected_languages,
            "timezone": "Asia/Tokyo",
            "actionVisual": "undefined",
            "visualCanvas": False,
        }
        assert _unsupported_timezones(fingerprint_page) == []

        coordinates = fingerprint_page.run_js(
            _GEOLOCATION_SCRIPT,
            as_expr=True,
            timeout=20,
        )
        for name, expected in CUSTOM_GEOLOCATION.items():
            assert coordinates[name] == pytest.approx(expected)

        headers = fingerprint_page.run_js(
            "fetch('/api/headers').then(response => response.json())",
            as_expr=True,
            timeout=20,
        )
        assert _header_value(headers, "User-Agent") == EXPECTED_UA
        assert _header_value(headers, "Accept-Language") == (
            context.fingerprint.accept_language
        )

        fingerprint_hashes = _collect_fingerprint_hashes(fingerprint_page)
        assert _collect_fingerprint_hashes(fingerprint_page) == fingerprint_hashes
        async_overlays = asyncio.run(
            _apply_emulation_through_real_async_page(
                context,
                fingerprint_page,
            )
        )
        assert async_overlays == {
            "screen": True,
            "geolocation": True,
            "locale": True,
            "timezone": True,
            "headers": True,
        }
    finally:
        if fingerprint_page is not None:
            fingerprint_page.quit()

    native_options = _new_options(
        firefox_path,
        user_dir=tmp_path / "native-profile",
    )
    assert native_options.fpfile is None

    native_page = None
    try:
        native_page = FirefoxPage(native_options)
        assert native_page.browser.options.fpfile is None
        native_process_args = [
            str(argument) for argument in native_page.browser.process.args
        ]
        assert not any(
            argument.startswith("--fpfile=")
            for argument in native_process_args
        )
        native_page.get(server.get_url("/nav/basic"))
        native_hashes = _collect_fingerprint_hashes(native_page)
        assert _collect_fingerprint_hashes(native_page) == native_hashes
    finally:
        if native_page is not None:
            native_page.quit()

    assert fingerprint_hashes["canvas"] != native_hashes["canvas"]
    assert fingerprint_hashes["audio"] != native_hashes["audio"]
