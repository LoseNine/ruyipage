# -*- coding: utf-8 -*-
"""
ruyipage._fingerprint.builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Smart fingerprint helpers built on top of the firefox-fingerprintBrowser
kernel (https://github.com/LoseNine/firefox-fingerprintBrowser).

Goal
----
Provide a single one-stop API ``apply_smart_fingerprint(opts, ...)`` that:

1. Probes the egress IP and resolves geo information through 10 fall-back
   data sources (geojs.io, ipapi.co, ipwho.is, ip-api.com, ipinfo.io, etc.).
2. Optionally enforces a country-code requirement (``require_country``).
3. Picks one of 22 real Windows hardware profiles (NVIDIA / AMD / Intel),
   composes a UA matching the actual Firefox major version and per-session
   Canvas / Audio seeds.
4. Maps the country code to language / Accept-Language / speech voices.
5. Writes a ``fpfile.txt`` that strictly follows the firefox-fingerprintBrowser
   field schema (``key:value``) to the chosen userdir, without ``width`` / ``height`` entries.
6. Configures the supplied ``FirefoxOptions`` instance (proxy / userdir /
   fpfile / script-accessible ``about:blank`` startup page).
   ``set_window_size_on_opts`` is retained as a deprecated no-op;
   fingerprint screen dimensions are never mapped to the outer window.

Public API
----------
::

    apply_smart_fingerprint(opts, ...) -> FingerprintContext

    fetch_geo_info(proxies, ...) -> GeoInfo
    fetch_public_ipv6(proxies, ...) -> Optional[str]
    coerce_manual_geo(data, ...) -> GeoInfo
    pick_fingerprint(geo, ...) -> FingerprintProfile
    write_fpfile(path, geo, fp, ...) -> None

    build_proxies_dict(host, port, user, pwd, scheme) -> Optional[Dict[str, str]]
    list_hardware_profiles() -> List[HardwareProfile]
    get_country_profile(cc) -> CountryProfile
    default_fingerprints_path() -> str
    default_region_locales_path() -> str

Errors
------
::

    FingerprintError              base class
        FingerprintConfigError    json schema invalid
        GeoError                  every geo source failed
            CountryMismatchError  geo ok but country != required

Typical usage
-------------
::

    from ruyipage import (
        FirefoxOptions, FirefoxPage, apply_smart_fingerprint,
        CountryMismatchError, GeoError,
    )

    opts = FirefoxOptions()
    opts.set_browser_path(r"C:\\Program Files\\Mozilla Firefox\\firefox.exe")
    opts.set_port(9222)

    try:
        ctx = apply_smart_fingerprint(
            opts,
            proxy_host="proxy.example.com", proxy_port=8080,
            proxy_user="u", proxy_pwd="p",
            require_country="US",
            manual_geo={
                "ip": "75.166.187.10",
                "country_code": "US",
                "timezone": "America/Denver",
                "latitude": 39.7392,
                "longitude": -104.9903,
            },
            logger=print,
        )

        # SOCKS5 password proxy:
        ctx = apply_smart_fingerprint(
            opts,
            proxy_scheme="socks5",
            proxy_host="gate.example.com", proxy_port=1000,
            proxy_user="u", proxy_pwd="p",
            require_country="US",
        )
    except CountryMismatchError as e:
        print(f"country mismatch: {e.actual} != {e.required}")
        raise
    except GeoError as e:
        print(f"geo lookup failed: {e}")
        raise

    page = FirefoxPage(opts)
    ctx.apply_emulation(page)        # returns a map containing ``screen``
    page.get("https://browserleaks.com/webgl")

    # AsyncFirefoxPage:
    # result = await ctx.apply_emulation_async(async_page)
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import ipaddress
import json
import math
import os
import random
import re
import string
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Any, Callable, Dict, List, Mapping, Optional, Tuple, Union, cast


DEFAULT_GEOLOCATION_ACCURACY = 15000


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FingerprintError(Exception):
    """Base class for all fingerprint-module errors.

    Catch this single class in business code if you only want to handle
    fingerprint preparation failures uniformly.
    """


class FingerprintConfigError(FingerprintError):
    """A bundled fingerprint, locale, or IANA timezone JSON file is invalid.

    Raised by the JSON loader when a schema constraint is violated:
    missing field, wrong type, length mismatch in speech arrays, etc.
    This is a deployment-time error, not a runtime network error.
    """


class GeoError(FingerprintError):
    """Every geo data source failed to return a usable response.

    The exception ``message`` includes a brief failure summary for each
    of the ten sources tried, so you can tell whether it was a network
    issue, a captcha rate-limit, a parse error, or a missing field.
    """


class CountryMismatchError(GeoError):
    """Geo lookup succeeded but the country does not match ``require_country``.

    Attributes
    ----------
    actual : str
        The country code returned by the data source (e.g. ``"JP"``).
    required : str
        The country code requested by the caller (e.g. ``"US"``).
    """

    def __init__(self, actual: str, required: str):
        super().__init__(
            "country mismatch: got {!r}, want {!r}".format(actual, required)
        )
        self.actual = actual
        self.required = required


# ---------------------------------------------------------------------------
# Data contracts (immutable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeoInfo:
    """Aggregated geo view of the egress IP.

    All required fields (``ip`` / ``country_code`` / ``timezone`` /
    ``latitude`` / ``longitude``) are guaranteed to be non-empty when
    returned by :func:`fetch_geo_info`. Optional fields may be ``""``
    or ``None`` when the underlying source did not provide them.

    Attributes
    ----------
    ip : str
        Public IPv4 address, e.g. ``"45.33.32.156"``.
    ipv6 : Optional[str]
        Public IPv6 if available; ``None`` otherwise.
    country_code : str
        ISO-3166-1 alpha-2 in upper case, e.g. ``"US"``.
    country : str
        Full country name; may be ``""``.
    region : str
        First-level admin region; may be ``""``.
    city : str
        City name; may be ``""``.
    timezone : str
        IANA timezone, e.g. ``"America/New_York"``.
    latitude / longitude : float
        WGS-84 coordinates (degrees).
    source : str
        Tag of the geo source that produced this entry, e.g. ``"geojs"``.
        Useful for diagnostics.
    """

    ip: str
    country_code: str
    country: str
    region: str
    city: str
    timezone: str
    latitude: float
    longitude: float
    source: str
    ipv6: Optional[str] = None


@dataclass(frozen=True)
class GeolocationProfile:
    """Validated geolocation state coordinated across fpfile and BiDi."""

    enabled: bool
    latitude: float
    longitude: float
    accuracy: float
    altitude: Optional[float] = None
    altitude_accuracy: Optional[float] = None
    heading: Optional[float] = None
    speed: Optional[float] = None
    timestamp: Any = "now"
    permission: str = "granted"


@dataclass(frozen=True)
class WebGLProfile:
    """Full set of WebGL fields aligned 1:1 with the kernel schema."""

    vendor: str
    renderer: str
    version: str
    glsl_version: str
    unmasked_vendor: str
    unmasked_renderer: str
    max_texture_size: int
    max_cube_map_texture_size: int
    max_texture_image_units: int
    max_vertex_attribs: int
    aliased_point_size_max: int
    max_viewport_dim: int


@dataclass(frozen=True)
class HardwareProfile:
    """One of the 22 Windows hardware profiles bundled with ruyipage."""

    id: str
    platform: str          # currently always ``"windows"``
    os_token: str          # the OS chunk used in the user-agent string
    font_system: str       # ``"windows"``
    hardware_concurrency: int
    width: int
    height: int
    webgl: WebGLProfile


@dataclass(frozen=True)
class CountryProfile:
    """Locale, Accept-Language and speech voice config for one country."""

    country_code: str
    language: str
    language_primary: str
    accept_language: str

    speech_local: Tuple[str, ...]
    speech_remote: Tuple[str, ...]
    speech_local_langs: Tuple[str, ...]
    speech_remote_langs: Tuple[str, ...]
    speech_default_name: str
    speech_default_lang: str


_AUDIO_SEED_UNSET = object()


@dataclass(frozen=True)
class FingerprintProfile:
    """Composite per-session fingerprint produced by :func:`pick_fingerprint`."""

    profile_id: str
    firefox_version: int
    useragent: str
    hardware: HardwareProfile
    country: CountryProfile
    canvas_seed: int
    language_primary: str
    accept_language: str
    audio_seed: Optional[int] = cast(Optional[int], _AUDIO_SEED_UNSET)

    def __post_init__(self) -> None:
        if self.audio_seed is _AUDIO_SEED_UNSET:
            derived = 1
            if isinstance(self.canvas_seed, int) and not isinstance(
                self.canvas_seed, bool
            ):
                derived = (
                    self.canvas_seed ^ 0x9E3779B97F4A7C15
                ) & ((1 << 64) - 1)
            object.__setattr__(self, "audio_seed", derived or 1)


# ---------------------------------------------------------------------------
# Default data file paths (uses package-relative resources)
# ---------------------------------------------------------------------------

def _module_data_dir() -> str:
    """Return the absolute path of the bundled ``data/`` directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def default_fingerprints_path() -> str:
    """Return the absolute path of the bundled ``fingerprints.json``.

    Resolution order:

    1. ``importlib.resources.files()`` (preferred, works after pip install
       even when packaged as a wheel / zip).
    2. ``__file__``-relative fallback for environments where importlib
       resources is unavailable (e.g. PyInstaller frozen builds).
    """
    try:
        from importlib.resources import files
        return str(files("ruyipage._fingerprint.data") / "fingerprints.json")
    except Exception:
        return os.path.join(_module_data_dir(), "fingerprints.json")


def default_region_locales_path() -> str:
    """Return the absolute path of the bundled ``region_locales.json``."""
    try:
        from importlib.resources import files
        return str(files("ruyipage._fingerprint.data") / "region_locales.json")
    except Exception:
        return os.path.join(_module_data_dir(), "region_locales.json")


def _default_iana_timezones_path() -> str:
    """Return the bundled Firefox ICU/IANA timezone index path."""
    try:
        from importlib.resources import files
        return str(files("ruyipage._fingerprint.data") / "iana_timezones.json")
    except Exception:
        return os.path.join(_module_data_dir(), "iana_timezones.json")


# ---------------------------------------------------------------------------
# JSON loaders with strict validation (cached)
# ---------------------------------------------------------------------------

_REQUIRED_HW_FIELDS = (
    "id", "platform", "os_token", "font_system",
    "hardwareConcurrency", "width", "height", "webgl",
)
_REQUIRED_WEBGL_FIELDS = (
    "vendor", "renderer", "version", "glsl_version",
    "unmasked_vendor", "unmasked_renderer",
    "max_texture_size", "max_cube_map_texture_size",
    "max_texture_image_units", "max_vertex_attribs",
    "aliased_point_size_max", "max_viewport_dim",
)


@functools.lru_cache(maxsize=8)
def _load_fingerprints(path: str) -> Dict[str, Any]:
    """Load and strictly validate ``fingerprints.json``.

    Cached per-path; concurrent calls with the same path share the dict.

    Raises
    ------
    FingerprintConfigError
        If the file is missing, JSON parse fails, or any constraint is
        violated (duplicate id, wrong platform, missing webgl field,
        non-positive numeric, etc.).
    """
    if not os.path.exists(path):
        raise FingerprintConfigError("fingerprints.json not found: " + path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise FingerprintConfigError(
            "fingerprints.json parse failed: {}".format(e)
        ) from e

    profiles = data.get("hardware_profiles") or []
    if not isinstance(profiles, list) or not profiles:
        raise FingerprintConfigError(
            "fingerprints.json: hardware_profiles must be a non-empty list"
        )

    seen_ids: set = set()
    for idx, p in enumerate(profiles):
        if not isinstance(p, dict):
            raise FingerprintConfigError(
                "fingerprints.json: profile #%d is not an object" % idx
            )
        for k in _REQUIRED_HW_FIELDS:
            if k not in p:
                raise FingerprintConfigError(
                    "fingerprints.json: profile #%d missing field %r" % (idx, k)
                )
        if p["id"] in seen_ids:
            raise FingerprintConfigError(
                "fingerprints.json: duplicate id %r" % p["id"]
            )
        seen_ids.add(p["id"])

        if p["platform"] != "windows":
            raise FingerprintConfigError(
                "fingerprints.json: only platform=windows is supported "
                "(profile %r has platform=%r)" % (p["id"], p["platform"])
            )
        for k in ("hardwareConcurrency", "width", "height"):
            v = p.get(k)
            if not isinstance(v, int) or v < 1:
                raise FingerprintConfigError(
                    "fingerprints.json: profile %r field %r must be a "
                    "positive int (got %r)" % (p["id"], k, v)
                )

        webgl = p.get("webgl")
        if not isinstance(webgl, dict):
            raise FingerprintConfigError(
                "fingerprints.json: profile %r webgl must be an object" % p["id"]
            )
        for k in _REQUIRED_WEBGL_FIELDS:
            if k not in webgl:
                raise FingerprintConfigError(
                    "fingerprints.json: profile %r webgl missing field %r"
                    % (p["id"], k)
                )

    return data


@functools.lru_cache(maxsize=8)
def _load_region_locales(path: str) -> Dict[str, Any]:
    """Load and validate ``region_locales.json``.

    Validation rules:

    * Top-level ``countries`` must contain ``_default``.
    * For every country: ``language`` / ``language_primary`` /
      ``accept_language`` are non-empty strings.
    * ``language`` and ``accept_language`` contain the same ordered tags.
    * ``speech.local`` and ``speech.local_langs`` have equal length.
    * ``speech.remote`` and ``speech.remote_langs`` have equal length.
    * ``speech.default_name`` appears in either ``local`` or ``remote``.

    Raises
    ------
    FingerprintConfigError
    """
    if not os.path.exists(path):
        raise FingerprintConfigError("region_locales.json not found: " + path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise FingerprintConfigError(
            "region_locales.json parse failed: {}".format(e)
        ) from e

    countries = data.get("countries") or {}
    if "_default" not in countries:
        raise FingerprintConfigError(
            "region_locales.json: missing _default entry"
        )

    for cc, entry in countries.items():
        if not isinstance(entry, dict):
            raise FingerprintConfigError(
                "region_locales.json: %r is not an object" % cc
            )
        for k in ("language", "language_primary", "accept_language"):
            v = entry.get(k)
            if not isinstance(v, str) or not v:
                raise FingerprintConfigError(
                    "region_locales.json: %r missing/invalid %r" % (cc, k)
                )
        language_tags = [part.strip() for part in entry["language"].split(",")]
        accept_tags = [
            part.split(";", 1)[0].strip()
            for part in entry["accept_language"].split(",")
        ]
        if language_tags != accept_tags:
            raise FingerprintConfigError(
                "region_locales.json: %r language/accept_language tags mismatch"
                % cc
            )
        speech = entry.get("speech") or {}
        local = speech.get("local") or []
        local_langs = speech.get("local_langs") or []
        remote = speech.get("remote") or []
        remote_langs = speech.get("remote_langs") or []
        if len(local) != len(local_langs):
            raise FingerprintConfigError(
                "region_locales.json: %r local/local_langs length mismatch" % cc
            )
        if len(remote) != len(remote_langs):
            raise FingerprintConfigError(
                "region_locales.json: %r remote/remote_langs length mismatch" % cc
            )
        default_name = speech.get("default_name") or ""
        if default_name and default_name not in list(local) + list(remote):
            raise FingerprintConfigError(
                "region_locales.json: %r default_name %r not in local/remote"
                % (cc, default_name)
            )
    return data


@functools.lru_cache(maxsize=2)
def _load_iana_timezones(path: str) -> frozenset:
    """Load the timezone IDs accepted by the bundled Firefox ICU data."""
    if not os.path.exists(path):
        raise FingerprintConfigError("iana_timezones.json not found: " + path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise FingerprintConfigError(
            "iana_timezones.json parse failed: {}".format(e)
        ) from e

    timezones = data.get("timezones") if isinstance(data, dict) else None
    if (
        not isinstance(timezones, list)
        or not timezones
        or any(not isinstance(value, str) or not value for value in timezones)
    ):
        raise FingerprintConfigError(
            "iana_timezones.json: timezones must be a non-empty string list"
        )
    return frozenset(timezones)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _hardware_from_dict(d: Dict[str, Any]) -> HardwareProfile:
    """Convert a JSON profile dict into a typed :class:`HardwareProfile`."""
    w = d["webgl"]
    return HardwareProfile(
        id=d["id"],
        platform=d["platform"],
        os_token=d["os_token"],
        font_system=d["font_system"],
        hardware_concurrency=int(d["hardwareConcurrency"]),
        width=int(d["width"]),
        height=int(d["height"]),
        webgl=WebGLProfile(
            vendor=w["vendor"],
            renderer=w["renderer"],
            version=w["version"],
            glsl_version=w["glsl_version"],
            unmasked_vendor=w["unmasked_vendor"],
            unmasked_renderer=w["unmasked_renderer"],
            max_texture_size=int(w["max_texture_size"]),
            max_cube_map_texture_size=int(w["max_cube_map_texture_size"]),
            max_texture_image_units=int(w["max_texture_image_units"]),
            max_vertex_attribs=int(w["max_vertex_attribs"]),
            aliased_point_size_max=int(w["aliased_point_size_max"]),
            max_viewport_dim=int(w["max_viewport_dim"]),
        ),
    )


def _country_from_dict(cc: str, d: Dict[str, Any]) -> CountryProfile:
    """Convert a JSON country entry into a typed :class:`CountryProfile`."""
    speech = d.get("speech") or {}
    return CountryProfile(
        country_code=cc,
        language=d["language"],
        language_primary=d["language_primary"],
        accept_language=d["accept_language"],
        speech_local=tuple(speech.get("local") or ()),
        speech_remote=tuple(speech.get("remote") or ()),
        speech_local_langs=tuple(speech.get("local_langs") or ()),
        speech_remote_langs=tuple(speech.get("remote_langs") or ()),
        speech_default_name=speech.get("default_name") or "",
        speech_default_lang=speech.get("default_lang") or "",
    )


def list_hardware_profiles(
    fingerprints_path: Optional[str] = None,
) -> List[HardwareProfile]:
    """Return a snapshot of every hardware profile bundled with ruyipage.

    Parameters
    ----------
    fingerprints_path : str, optional
        Override the JSON path; defaults to the bundled file.

    Returns
    -------
    list[HardwareProfile]
        One entry per profile, in JSON order.
    """
    path = fingerprints_path or default_fingerprints_path()
    data = _load_fingerprints(path)
    return [_hardware_from_dict(p) for p in data["hardware_profiles"]]


def get_country_profile(
    country_code: str,
    region_locales_path: Optional[str] = None,
) -> CountryProfile:
    """Return the :class:`CountryProfile` for a country code.

    Falls back to the ``_default`` entry when ``country_code`` is not
    in the bundled mapping. The country code is matched case-insensitively
    and trimmed of whitespace.
    """
    path = region_locales_path or default_region_locales_path()
    data = _load_region_locales(path)
    cc = (country_code or "").strip().upper()
    countries = data["countries"]
    entry = countries.get(cc) or countries["_default"]
    return _country_from_dict(cc if cc in countries else "_default", entry)


# ---------------------------------------------------------------------------
# Proxies helper
# ---------------------------------------------------------------------------

def build_proxies_dict(
    host: Optional[str],
    port: Optional[int],
    user: Optional[str] = None,
    pwd: Optional[str] = None,
    scheme: str = "http",
) -> Optional[Dict[str, str]]:
    """Build a ``requests``-compatible ``proxies`` dict.

    Parameters
    ----------
    host, port : str / int
        Proxy host / port. If either is falsy, returns ``None`` (direct).
    user, pwd : str, optional
        Proxy credentials embedded in the URL. Both must be
        provided together; otherwise the proxy is treated as anonymous.
    scheme : str
        ``"http"`` by default. ``"socks5"`` uses ``socks5h`` for
        requests so DNS resolution is performed through the proxy.

    Returns
    -------
    dict or None
        ``{"http": url, "https": url}``; ``None`` for direct mode.
    """
    if not host or not port:
        return None
    scheme = (scheme or "http").strip().lower()
    # requests uses a different URL scheme for SOCKS5 remote DNS.  Keep
    # FirefoxOptions on socks5:// later, but use socks5h:// here so geo
    # probes resolve the target through the proxy instead of the local host.
    if scheme in ("socks", "socks5"):
        url_scheme = "socks5h"
    elif scheme == "socks4":
        url_scheme = "socks4"
    elif scheme in ("http", "https"):
        url_scheme = "http"
    else:
        raise FingerprintConfigError(
            "proxy_scheme must be one of: http, https, socks5, socks4"
        )
    if user and pwd:
        url = "{}://{}:{}@{}:{}".format(url_scheme, user, pwd, host, port)
    else:
        url = "{}://{}:{}".format(url_scheme, host, port)
    return {"http": url, "https": url}


# ---------------------------------------------------------------------------
# Geo lookup with multi-source fall-back
# ---------------------------------------------------------------------------

# Each entry: (tag, url, parser_callable)
_GEO_SOURCES: List[Tuple[str, str, str]] = [
    ("geojs",         "https://get.geojs.io/v1/ip/geo.json",           "geojs"),
    ("ipapi",         "https://ipapi.co/json/",                         "ipapi"),
    ("ipwho",         "https://ipwho.is/",                              "ipwho"),
    ("ipapi2",        "http://ip-api.com/json?fields=66846719",         "ipapi2"),
    ("ipinfo",        "https://ipinfo.io/json",                         "ipinfo"),
    ("ipapi-is",      "https://api.ipapi.is/",                           "ipapi_is"),
    ("ip-guide",      "https://ip.guide/",                                "ip_guide"),
    ("ipwhois-app",   "https://ipwhois.app/json/",                      "ipwhois_app"),
    ("freeipapi",     "https://freeipapi.com/api/json",                 "freeipapi"),
    ("reallyfreegeoip", "https://reallyfreegeoip.org/json/",             "reallyfreegeoip"),
]


def _to_float(value: Any) -> float:
    """Coerce arbitrary input to ``float``; raises ``ValueError`` if invalid."""
    return float(str(value).strip())


def _required_float(payload: Mapping[str, Any], key: str) -> float:
    """Read a required coordinate without turning a missing value into zero."""
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("missing {}".format(key))
    return _to_float(value)


def _parse_geojs(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``get.geojs.io/v1/ip/geo.json``."""
    return GeoInfo(
        ip=str(payload["ip"]).strip(),
        country_code=str(payload.get("country_code", "")).strip().upper(),
        country=str(payload.get("country", "")).strip(),
        region=str(payload.get("region", "")).strip(),
        city=str(payload.get("city", "")).strip(),
        timezone=str(payload.get("timezone", "")).strip(),
        latitude=_required_float(payload, "latitude"),
        longitude=_required_float(payload, "longitude"),
        source="geojs",
    )


def _parse_ipapi(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``ipapi.co/json/``."""
    if payload.get("error"):
        raise ValueError(str(payload.get("reason") or "ipapi error"))
    return GeoInfo(
        ip=str(payload["ip"]).strip(),
        country_code=str(payload.get("country", "")).strip().upper(),
        country=str(payload.get("country_name", "")).strip(),
        region=str(payload.get("region", "")).strip(),
        city=str(payload.get("city", "")).strip(),
        timezone=str(payload.get("timezone", "")).strip(),
        latitude=_required_float(payload, "latitude"),
        longitude=_required_float(payload, "longitude"),
        source="ipapi",
    )


def _parse_ipwho(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``ipwho.is``."""
    if payload.get("success") is False:
        raise ValueError(str(payload.get("message") or "ipwho error"))
    tz = (payload.get("timezone") or {}).get("id", "")
    return GeoInfo(
        ip=str(payload["ip"]).strip(),
        country_code=str(payload.get("country_code", "")).strip().upper(),
        country=str(payload.get("country", "")).strip(),
        region=str(payload.get("region", "")).strip(),
        city=str(payload.get("city", "")).strip(),
        timezone=str(tz).strip(),
        latitude=_required_float(payload, "latitude"),
        longitude=_required_float(payload, "longitude"),
        source="ipwho",
    )


def _parse_ipapi_com(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``ip-api.com/json``."""
    if str(payload.get("status", "")).lower() != "success":
        raise ValueError(str(payload.get("message") or "ip-api error"))
    return GeoInfo(
        ip=str(payload.get("query", "")).strip(),
        country_code=str(payload.get("countryCode", "")).strip().upper(),
        country=str(payload.get("country", "")).strip(),
        region=str(payload.get("regionName", "")).strip(),
        city=str(payload.get("city", "")).strip(),
        timezone=str(payload.get("timezone", "")).strip(),
        latitude=_required_float(payload, "lat"),
        longitude=_required_float(payload, "lon"),
        source="ipapi2",
    )


def _parse_ipinfo(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``ipinfo.io/json``."""
    loc = str(payload.get("loc", "")).strip()
    if "," not in loc:
        raise ValueError("ipinfo loc missing")
    lat_s, lon_s = loc.split(",", 1)
    return GeoInfo(
        ip=str(payload["ip"]).strip(),
        country_code=str(payload.get("country", "")).strip().upper(),
        country=str(payload.get("country", "")).strip(),
        region=str(payload.get("region", "")).strip(),
        city=str(payload.get("city", "")).strip(),
        timezone=str(payload.get("timezone", "")).strip(),
        latitude=_to_float(lat_s),
        longitude=_to_float(lon_s),
        source="ipinfo",
    )


def _parse_ipapi_is(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``api.ipapi.is``."""
    loc = payload.get("location") or {}
    return GeoInfo(
        ip=str(payload.get("ip") or "").strip(),
        country_code=str(loc.get("country_code") or "").strip().upper(),
        country=str(loc.get("country") or "").strip(),
        region=str(loc.get("state") or "").strip(),
        city=str(loc.get("city") or "").strip(),
        timezone=str(loc.get("timezone") or "").strip(),
        latitude=_required_float(loc, "latitude"),
        longitude=_required_float(loc, "longitude"),
        source="ipapi-is",
    )


def _parse_ip_guide(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``ip.guide``."""
    loc = payload.get("location") or {}
    network = payload.get("network") or {}
    asn = network.get("autonomous_system") or {}
    return GeoInfo(
        ip=str(payload.get("ip") or "").strip(),
        country_code=str(asn.get("country") or "").strip().upper(),
        country=str(loc.get("country") or "").strip(),
        region=str(loc.get("region") or "").strip(),
        city=str(loc.get("city") or "").strip(),
        timezone=str(loc.get("timezone") or "").strip(),
        latitude=_required_float(loc, "latitude"),
        longitude=_required_float(loc, "longitude"),
        source="ip-guide",
    )


def _parse_ipwhois_app(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``ipwhois.app/json``."""
    if str(payload.get("success", "true")).lower() == "false":
        raise ValueError(str(payload.get("message") or "ipwhois.app error"))
    return GeoInfo(
        ip=str(payload.get("ip") or "").strip(),
        country_code=str(payload.get("country_code") or "").strip().upper(),
        country=str(payload.get("country") or "").strip(),
        region=str(payload.get("region") or "").strip(),
        city=str(payload.get("city") or "").strip(),
        timezone=str(payload.get("timezone") or "").strip(),
        latitude=_required_float(payload, "latitude"),
        longitude=_required_float(payload, "longitude"),
        source="ipwhois-app",
    )


def _parse_freeipapi(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``freeipapi.com/api/json``."""
    timezones = payload.get("timeZones") or []
    timezone = timezones[0] if timezones else payload.get("timeZone")
    return GeoInfo(
        ip=str(payload.get("ipAddress") or payload.get("ip") or "").strip(),
        country_code=str(
            payload.get("countryCode") or payload.get("country_code") or ""
        ).strip().upper(),
        country=str(payload.get("countryName") or payload.get("country") or "").strip(),
        region=str(payload.get("regionName") or payload.get("region") or "").strip(),
        city=str(payload.get("cityName") or payload.get("city") or "").strip(),
        timezone=str(timezone or payload.get("timezone") or "").strip(),
        latitude=_required_float(payload, "latitude"),
        longitude=_required_float(payload, "longitude"),
        source="freeipapi",
    )


def _parse_reallyfreegeoip(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``reallyfreegeoip.org/json``."""
    return GeoInfo(
        ip=str(payload.get("ip") or payload.get("IPv4") or "").strip(),
        country_code=str(payload.get("country_code") or "").strip().upper(),
        country=str(payload.get("country_name") or "").strip(),
        region=str(payload.get("region_name") or payload.get("region_code") or "").strip(),
        city=str(payload.get("city") or "").strip(),
        timezone=str(payload.get("time_zone") or payload.get("timezone") or "").strip(),
        latitude=_required_float(payload, "latitude"),
        longitude=_required_float(payload, "longitude"),
        source="reallyfreegeoip",
    )


_PARSERS = {
    "geojs":  _parse_geojs,
    "ipapi":  _parse_ipapi,
    "ipwho":  _parse_ipwho,
    "ipapi2": _parse_ipapi_com,
    "ipinfo": _parse_ipinfo,
    "ipapi_is": _parse_ipapi_is,
    "ip_guide": _parse_ip_guide,
    "ipwhois_app": _parse_ipwhois_app,
    "freeipapi": _parse_freeipapi,
    "reallyfreegeoip": _parse_reallyfreegeoip,
}


def _validate_geo(geo: GeoInfo) -> None:
    """Enforce required-field constraints on a parsed :class:`GeoInfo`."""
    if not isinstance(geo.ip, str) or not geo.ip.strip():
        raise ValueError("missing ip")
    try:
        ip = ipaddress.ip_address(geo.ip.strip())
    except ValueError:
        raise ValueError("invalid ip: %r" % geo.ip)
    if ip.version != 4:
        raise ValueError("ip must be IPv4: %r" % geo.ip)
    if (
        not isinstance(geo.country_code, str)
        or not re.fullmatch(r"[A-Z]{2}", geo.country_code)
    ):
        raise ValueError("invalid country_code: %r" % geo.country_code)
    if not geo.timezone:
        raise ValueError("missing timezone")
    if not isinstance(geo.latitude, (int, float)) or isinstance(geo.latitude, bool):
        raise ValueError("invalid latitude: %r" % geo.latitude)
    if not math.isfinite(geo.latitude) or not -90 <= geo.latitude <= 90:
        raise ValueError("invalid latitude: %r" % geo.latitude)
    if not isinstance(geo.longitude, (int, float)) or isinstance(geo.longitude, bool):
        raise ValueError("invalid longitude: %r" % geo.longitude)
    if not math.isfinite(geo.longitude) or not -180 <= geo.longitude <= 180:
        raise ValueError("invalid longitude: %r" % geo.longitude)

    timezone = str(geo.timezone).strip()
    if timezone != geo.timezone or any(ch.isspace() for ch in timezone):
        raise ValueError("invalid timezone: %r" % geo.timezone)
    if timezone not in _load_iana_timezones(_default_iana_timezones_path()):
        raise ValueError("invalid timezone: %r" % geo.timezone)

    if geo.ipv6 not in (None, ""):
        try:
            ipv6 = ipaddress.ip_address(str(geo.ipv6).strip())
        except ValueError:
            raise ValueError("invalid ipv6: %r" % geo.ipv6)
        if ipv6.version != 6:
            raise ValueError("ipv6 must be IPv6: %r" % geo.ipv6)


def _http_get_json(
    url: str,
    proxies: Optional[Dict[str, str]],
    timeout: float,
) -> Dict[str, Any]:
    """Tiny ``requests.get`` wrapper that returns a parsed JSON dict.

    Imported lazily so callers without ``requests`` can still import the
    module (the dependency is only needed at runtime when geo lookup runs).
    """
    import requests
    resp = requests.get(
        url,
        proxies=proxies,
        timeout=timeout,
        headers={"User-Agent": "ruyipage-fingerprint/1.0"},
    )
    if not resp.ok:
        raise IOError("HTTP {}".format(resp.status_code))
    return resp.json()


def fetch_geo_info(
    proxies: Optional[Dict[str, str]] = None,
    *,
    require_country: Optional[str] = None,
    timeout: float = 8.0,
    retries_per_source: int = 1,
    logger: Optional[Callable[[str], None]] = None,
) -> GeoInfo:
    """Resolve the egress IP and its geo info via 10 fall-back data sources.

    Parameters
    ----------
    proxies : dict, optional
        ``requests``-style proxies dict; ``None`` means direct.
    require_country : str, optional
        ISO-3166-1 alpha-2 code, case-insensitive. If set, a successful
        lookup with a different country immediately raises
        :class:`CountryMismatchError` (no further sources are tried,
        because every source observes the same egress IP).
    timeout : float
        Per-request HTTP timeout (seconds).
    retries_per_source : int
        Extra retries per source (so each source is tried at most
        ``retries_per_source + 1`` times). Default ``1``.
    logger : callable, optional
        Receives one-line status messages (``"[fp] ..."``). When ``None``,
        the function is silent. ``print`` works as a logger.

    Returns
    -------
    GeoInfo
        Always non-``None`` on return.

    Raises
    ------
    FingerprintConfigError
        A required bundled validation asset is missing or invalid.
    CountryMismatchError
        ``require_country`` set and the geo source observed a different cc.
    GeoError
        All ten sources failed (network, parse, missing fields).
    """
    log = logger or (lambda _msg: None)
    require_country = (require_country or "").strip().upper() or None
    errors: List[str] = []

    for tag, url, parser_key in _GEO_SOURCES:
        attempts = retries_per_source + 1
        for attempt in range(attempts):
            try:
                log("[fp] geo source={} url={}".format(tag, url))
                payload = _http_get_json(url, proxies, timeout)
                geo = _PARSERS[parser_key](payload)
                _validate_geo(geo)
                if require_country and geo.country_code != require_country:
                    raise CountryMismatchError(
                        actual=geo.country_code,
                        required=require_country,
                    )
                log("[fp] geo ok ip={} cc={} tz={} src={}".format(
                    geo.ip, geo.country_code, geo.timezone, tag))
                return geo
            except FingerprintConfigError:
                raise
            except CountryMismatchError:
                # CC mismatch is final - other sources observe the same IP.
                raise
            except Exception as e:  # noqa: BLE001
                errors.append("{} attempt={} -> {}".format(
                    tag, attempt + 1, e))
                if attempt + 1 < attempts:
                    time.sleep(0.5)
                continue

    raise GeoError("all geo sources failed: " + " | ".join(errors))


def coerce_manual_geo(
    value: Any,
    *,
    require_country: Optional[str] = None,
) -> GeoInfo:
    """Normalize user-provided geo data into :class:`GeoInfo`.

    This is intended as an explicit fallback for environments where every
    online geo source fails, usually because the proxy cannot reach them.
    """
    if isinstance(value, GeoInfo):
        geo = value
    elif isinstance(value, Mapping):
        required = ("ip", "country_code", "timezone", "latitude", "longitude")
        missing = [key for key in required if value.get(key) in (None, "")]
        if missing:
            raise GeoError(
                "manual_geo missing required fields: " + ", ".join(missing)
            )
        try:
            geo = GeoInfo(
                ip=str(value["ip"]).strip(),
                country_code=str(value["country_code"]).strip().upper(),
                country=str(value.get("country") or "").strip(),
                region=str(value.get("region") or "").strip(),
                city=str(value.get("city") or "").strip(),
                timezone=str(value["timezone"]).strip(),
                latitude=_to_float(value["latitude"]),
                longitude=_to_float(value["longitude"]),
                source=str(value.get("source") or "manual").strip() or "manual",
                ipv6=value.get("ipv6"),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise GeoError("invalid manual_geo: {}".format(e))
    else:
        raise GeoError("manual_geo must be a GeoInfo or mapping")

    try:
        _validate_geo(geo)
    except ValueError as e:
        raise GeoError("invalid manual_geo: {}".format(e)) from e
    require_country = (require_country or "").strip().upper() or None
    if require_country and geo.country_code != require_country:
        raise CountryMismatchError(actual=geo.country_code, required=require_country)
    return geo


# ---------------------------------------------------------------------------
# Public IPv6 lookup (optional, best-effort)
# ---------------------------------------------------------------------------

_IPV6_SOURCES: List[Tuple[str, str]] = [
    ("ipify6",       "https://api6.ipify.org?format=json"),
    ("my-ip-io",     "https://api6.my-ip.io/ip.json"),
    ("ifconfig-co",  "https://ifconfig.co/ip"),
]


def fetch_public_ipv6(
    proxies: Optional[Dict[str, str]] = None,
    *,
    timeout: float = 6.0,
    logger: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Best-effort lookup of the egress IPv6 address.

    Returns the address string on success or ``None`` on any failure.
    Never raises. The address enriches :class:`GeoInfo` diagnostics only;
    WebRTC IPv6 policy fields require explicit ``webrtc_*_ipv6`` inputs.
    """
    import requests
    log = logger or (lambda _msg: None)

    for tag, url in _IPV6_SOURCES:
        try:
            resp = requests.get(
                url, proxies=proxies, timeout=timeout,
                headers={"User-Agent": "ruyipage-fingerprint/1.0"},
            )
            if not resp.ok:
                continue
            text = resp.text.strip()
            try:
                payload = resp.json()
                ip = str(payload.get("ip") or payload.get("address") or "").strip()
            except ValueError:
                ip = text
            try:
                parsed = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if parsed.version == 6:
                normalized = str(parsed)
                log("[fp] ipv6 ok {} via {}".format(normalized, tag))
                return normalized
        except Exception:  # noqa: BLE001
            continue
    log("[fp] ipv6 unavailable")
    return None


# ---------------------------------------------------------------------------
# Fingerprint composition
# ---------------------------------------------------------------------------

def _detect_firefox_major_version(browser_path: Optional[str]) -> Optional[int]:
    """Return the executable's reported Firefox major version, if available."""
    if not isinstance(browser_path, str) or not browser_path.strip():
        return None

    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            [browser_path, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    output = "{}\n{}".format(completed.stdout or "", completed.stderr or "")
    match = re.search(r"\bFirefox\s+(\d+)(?:\.|\b)", output, re.IGNORECASE)
    return int(match.group(1)) if match else None

def _build_useragent(profile: HardwareProfile, version: int) -> str:
    """Compose a Firefox user-agent string for the given profile / version.

    The format follows the canonical Firefox UA template; only the OS
    token and the version number vary across profiles.
    """
    return ("Mozilla/5.0 ({os_token}; rv:{ver}.0) "
            "Gecko/20100101 Firefox/{ver}.0").format(
        os_token=profile.os_token, ver=version,
    )


def pick_fingerprint(
    geo: GeoInfo,
    *,
    firefox_version: Optional[int] = None,
    fingerprints_path: Optional[str] = None,
    region_locales_path: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> FingerprintProfile:
    """Compose a one-shot :class:`FingerprintProfile` from a :class:`GeoInfo`.

    Steps:

    1. Load (and cache) the hardware pool and the region locales.
    2. Pick a hardware profile uniformly at random from the pool.
    3. Resolve the country profile based on ``geo.country_code``,
       falling back to ``_default``.
    4. Use the requested Firefox major version, or the exact bundled
       ``firefox_base_version`` when no override is supplied.
    5. Generate independent Canvas and Audio seeds in ``[1, 2**64 - 1]``.

    Parameters
    ----------
    geo : GeoInfo
        Output of :func:`fetch_geo_info`.
    firefox_version : int, optional
        Exact Firefox major version reported by the running browser.
    fingerprints_path / region_locales_path : str, optional
        Override the bundled JSON files (e.g. for tests).
    rng : random.Random, optional
        Inject a deterministic RNG for reproducible tests; defaults to
        the module-level :mod:`random`.

    Returns
    -------
    FingerprintProfile

    Raises
    ------
    FingerprintConfigError
        Underlying JSON files are missing or invalid.
    FingerprintError
        ``firefox_version`` is not a positive integer.
    """
    rnd = rng or random
    fp_data = _load_fingerprints(
        fingerprints_path or default_fingerprints_path()
    )
    hw_dict = rnd.choice(fp_data["hardware_profiles"])
    hw = _hardware_from_dict(hw_dict)

    country = get_country_profile(geo.country_code, region_locales_path)

    version = (
        int(fp_data.get("firefox_base_version", 155))
        if firefox_version is None
        else firefox_version
    )
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise FingerprintError("firefox_version must be a positive integer")

    return FingerprintProfile(
        profile_id=hw.id,
        firefox_version=version,
        useragent=_build_useragent(hw, version),
        hardware=hw,
        country=country,
        canvas_seed=rnd.randrange(1, 1 << 64),
        audio_seed=rnd.randrange(1, 1 << 64),
        language_primary=country.language_primary,
        accept_language=country.accept_language,
    )


# ---------------------------------------------------------------------------
# fpfile writer (firefox-fingerprintBrowser format)
# ---------------------------------------------------------------------------

# Reserved keys that the writer always populates from (geo, fp); user
# supplied ``extra`` keys cannot collide with these.
_RESERVED_KEYS: Tuple[str, ...] = (
    "webdriver",
    "local_webrtc_ipv4", "local_webrtc_ipv6",
    "public_webrtc_ipv4", "public_webrtc_ipv6",
    "timezone", "language",
    "speech.voices.local", "speech.voices.remote",
    "speech.voices.local.langs", "speech.voices.remote.langs",
    "speech.voices.default.name", "speech.voices.default.lang",
    "font_system", "useragent", "hardwareConcurrency",
    "webgl.vendor", "webgl.renderer", "webgl.version", "webgl.glsl_version",
    "webgl.unmasked_vendor", "webgl.unmasked_renderer",
    "webgl.max_texture_size", "webgl.max_cube_map_texture_size",
    "webgl.max_texture_image_units", "webgl.max_vertex_attribs",
    "webgl.aliased_point_size_max", "webgl.max_viewport_dim",
    "width", "height",
    "canvas", "canvas.enabled", "canvas.scope",
    "canvas.mode", "canvas.seed", "canvas.strength",
    "canvas.preserveAlpha", "canvas.preserveWhitePoint",
    "canvas.pngMetadata",
    "audio", "audio.enabled", "audio.mode", "audio.scope", "audio.seed",
    "geolocation.enabled", "geolocation.latitude", "geolocation.longitude",
    "geolocation.accuracy", "geolocation.altitude",
    "geolocation.altitudeAccuracy", "geolocation.heading",
    "geolocation.speed", "geolocation.timestamp", "geolocation.permission",
    "httpauth.host", "httpauth.port",
    "httpauth.username", "httpauth.password",
    "socksauth.host", "socksauth.port",
    "socksauth.username", "socksauth.password",
)


def _atomic_write_text(path: str, content: str) -> None:
    """Atomically write ``content`` to ``path`` via ``tmp + os.replace``.

    Prevents Firefox from reading a half-written fpfile if the process
    is killed mid-write. Uses ``\n`` line endings regardless of platform.
    """
    parent = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".fpfile.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validate_seed(name: str, value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value < (1 << 64)
    ):
        raise FingerprintError(
            "{} must be an integer in [1, 2**64 - 1]".format(name)
        )
    return value


def _validate_geolocation_number(
    name: str,
    value: Any,
    minimum: float,
    maximum: float,
    *,
    minimum_open: bool = False,
) -> Any:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value <= minimum if minimum_open else value < minimum)
        or value > maximum
    ):
        boundary = "(" if minimum_open else "["
        raise FingerprintError(
            "{} must be a finite number in {}{}, {}]".format(
                name, boundary, minimum, maximum
            )
        )
    return value


def _validate_optional_geolocation_number(
    name: str,
    value: Any,
    minimum: float,
    maximum: float,
    *,
    minimum_open: bool = False,
) -> Any:
    if value is None:
        return None
    return _validate_geolocation_number(
        name,
        value,
        minimum,
        maximum,
        minimum_open=minimum_open,
    )


def _serialize_nullable_number(value: Any) -> str:
    return "null" if value is None else str(value)


def _build_geolocation_profile(
    geo: GeoInfo,
    *,
    enabled: bool = True,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    accuracy: float = DEFAULT_GEOLOCATION_ACCURACY,
    altitude: Optional[float] = None,
    altitude_accuracy: Optional[float] = None,
    heading: Optional[float] = None,
    speed: Optional[float] = None,
    timestamp: Any = "now",
    permission: str = "granted",
) -> GeolocationProfile:
    if not isinstance(enabled, bool):
        raise FingerprintError("geolocation_enabled must be a boolean")

    latitude = _validate_geolocation_number(
        "geolocation_latitude",
        geo.latitude if latitude is None else latitude,
        -90,
        90,
    )
    longitude = _validate_geolocation_number(
        "geolocation_longitude",
        geo.longitude if longitude is None else longitude,
        -180,
        180,
    )
    # The fingerprint kernel requires accuracy > 0 even though BiDi alone
    # accepts zero, so the coordinated profile uses the stricter contract.
    accuracy = _validate_geolocation_number(
        "geolocation_accuracy",
        accuracy,
        0,
        float("1.7976931348623157e308"),
        minimum_open=True,
    )
    altitude = _validate_optional_geolocation_number(
        "geolocation_altitude",
        altitude,
        -float("1.7976931348623157e308"),
        float("1.7976931348623157e308"),
    )
    altitude_accuracy = _validate_optional_geolocation_number(
        "geolocation_altitude_accuracy",
        altitude_accuracy,
        0,
        float("1.7976931348623157e308"),
    )
    heading = _validate_optional_geolocation_number(
        "geolocation_heading", heading, 0, 360
    )
    speed = _validate_optional_geolocation_number(
        "geolocation_speed",
        speed,
        0,
        float("1.7976931348623157e308"),
    )
    if altitude_accuracy is not None and altitude is None:
        raise FingerprintError(
            "geolocation_altitude_accuracy requires geolocation_altitude"
        )
    if heading is not None and (
        heading >= 360 or speed is None or speed <= 0
    ):
        raise FingerprintError(
            "geolocation_heading requires geolocation_speed greater than zero"
        )
    if timestamp != "now" and (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, int)
        or not 0 <= timestamp < (1 << 64)
    ):
        raise FingerprintError(
            "geolocation_timestamp must be 'now' or a uint64 integer"
        )
    if permission not in ("prompt", "granted", "denied"):
        raise FingerprintError(
            "geolocation_permission must be prompt, granted, or denied"
        )

    return GeolocationProfile(
        enabled=enabled,
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
        altitude=altitude,
        altitude_accuracy=altitude_accuracy,
        heading=heading,
        speed=speed,
        timestamp=timestamp,
        permission=permission,
    )


def _validate_webrtc_ip(
    name: str,
    value: Optional[str],
    expected_version: int,
) -> Optional[str]:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "%" in value
    ):
        raise FingerprintError(
            "{} must be a valid IPv{} address".format(name, expected_version)
        )
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        raise FingerprintError(
            "{} must be a valid IPv{} address".format(name, expected_version)
        )
    if address.version != expected_version:
        raise FingerprintError(
            "{} must be an IPv{} address".format(name, expected_version)
        )
    return str(address)


def write_fpfile(
    fpfile_path: str,
    geo: GeoInfo,
    fp: FingerprintProfile,
    *,
    proxy_host: Optional[str] = None,
    proxy_port: Optional[int] = None,
    proxy_user: Optional[str] = None,
    proxy_pwd: Optional[str] = None,
    proxy_scheme: str = "http",
    webrtc_local_ipv4: Optional[str] = None,
    webrtc_local_ipv6: Optional[str] = None,
    webrtc_public_ipv4: Optional[str] = None,
    webrtc_public_ipv6: Optional[str] = None,
    geolocation_enabled: bool = True,
    geolocation_latitude: Optional[float] = None,
    geolocation_longitude: Optional[float] = None,
    geolocation_accuracy: float = DEFAULT_GEOLOCATION_ACCURACY,
    geolocation_altitude: Optional[float] = None,
    geolocation_altitude_accuracy: Optional[float] = None,
    geolocation_heading: Optional[float] = None,
    geolocation_speed: Optional[float] = None,
    geolocation_timestamp: Any = "now",
    geolocation_permission: str = "granted",
    extra: Optional[Dict[str, str]] = None,
) -> None:
    """Serialize ``(geo, fp)`` to a firefox-fingerprintBrowser fpfile.

    The writer follows a strict, deterministic field order so that
    repeated runs produce diff-friendly files. Lines use ``key:value``
    separators (``=`` is **not** used). UTF-8 / ``\\n`` only.

    Parameters
    ----------
    fpfile_path : str
        Target file path (the parent directory must already exist).
    geo : GeoInfo
        Source of timezone and default geolocation coordinates.
    fp : FingerprintProfile
        Source of every other field (hardware + country + UA + canvas).
    proxy_host, proxy_port, proxy_user, proxy_pwd, proxy_scheme : optional
        When HTTP credentials are provided, ``httpauth.*`` lines are
        appended. When ``proxy_scheme`` is SOCKS5, ``socksauth.*`` fields
        are appended so the fingerprint browser can authenticate SOCKS5.
    webrtc_local_ipv4 / webrtc_local_ipv6 : str, optional
        Exact host ICE addresses accepted by the Firefox WebRTC policy.
        Omitted by default because proxy geo data cannot identify them.
    webrtc_public_ipv4 / webrtc_public_ipv6 : str, optional
        Exact server-reflexive or peer-reflexive ICE addresses accepted by
        the Firefox WebRTC policy. Omitted by default because HTTP/SOCKS geo
        lookup does not prove the address used by STUN.
    geolocation_* : optional
        Complete kernel geolocation profile. Latitude and longitude default
        to the proxy-derived ``GeoInfo`` coordinates.
    extra : dict, optional
        Additional ``key: value`` pairs appended after the core fields.
        Cannot override any reserved key (see ``_RESERVED_KEYS``).

    Raises
    ------
    FingerprintError
        A Canvas or Audio seed is invalid, or ``extra`` contains a reserved key,
        delimiter, or line break.
    OSError
        File-system error during write.
    """
    canvas_seed = _validate_seed("canvas_seed", fp.canvas_seed)
    audio_seed = _validate_seed("audio_seed", fp.audio_seed)
    webrtc_local_ipv4 = _validate_webrtc_ip(
        "webrtc_local_ipv4", webrtc_local_ipv4, 4
    )
    webrtc_local_ipv6 = _validate_webrtc_ip(
        "webrtc_local_ipv6", webrtc_local_ipv6, 6
    )
    webrtc_public_ipv4 = _validate_webrtc_ip(
        "webrtc_public_ipv4", webrtc_public_ipv4, 4
    )
    webrtc_public_ipv6 = _validate_webrtc_ip(
        "webrtc_public_ipv6", webrtc_public_ipv6, 6
    )

    geolocation = _build_geolocation_profile(
        geo,
        enabled=geolocation_enabled,
        latitude=geolocation_latitude,
        longitude=geolocation_longitude,
        accuracy=geolocation_accuracy,
        altitude=geolocation_altitude,
        altitude_accuracy=geolocation_altitude_accuracy,
        heading=geolocation_heading,
        speed=geolocation_speed,
        timestamp=geolocation_timestamp,
        permission=geolocation_permission,
    )

    extra_items: List[Tuple[str, str]] = []
    if extra:
        for raw_key, raw_value in extra.items():
            key = str(raw_key)
            value = str(raw_value)
            if key.strip() in _RESERVED_KEYS:
                raise FingerprintError(
                    "extra keys collide with reserved fields: %r" % [raw_key]
                )
            if any(char in key for char in ":=\r\n"):
                raise FingerprintError(
                    "extra keys must not contain delimiters or line breaks"
                )
            if "\r" in value or "\n" in value:
                raise FingerprintError(
                    "extra values must not contain line breaks"
                )
            extra_items.append((key, value))

    hw = fp.hardware
    country = fp.country
    lines: List[str] = []
    a = lines.append

    a("webdriver:0")

    if webrtc_local_ipv4:
        a("local_webrtc_ipv4:" + webrtc_local_ipv4)
    if webrtc_local_ipv6:
        a("local_webrtc_ipv6:" + webrtc_local_ipv6)
    if webrtc_public_ipv4:
        a("public_webrtc_ipv4:" + webrtc_public_ipv4)
    if webrtc_public_ipv6:
        a("public_webrtc_ipv6:" + webrtc_public_ipv6)

    a("timezone:" + geo.timezone)
    a("language:" + country.language)

    a("geolocation.enabled:" +
      ("true" if geolocation.enabled else "false"))
    a("geolocation.latitude:" + str(geolocation.latitude))
    a("geolocation.longitude:" + str(geolocation.longitude))
    a("geolocation.accuracy:" + str(geolocation.accuracy))
    a("geolocation.altitude:" + _serialize_nullable_number(geolocation.altitude))
    a("geolocation.altitudeAccuracy:" +
      _serialize_nullable_number(geolocation.altitude_accuracy))
    a("geolocation.heading:" + _serialize_nullable_number(geolocation.heading))
    a("geolocation.speed:" + _serialize_nullable_number(geolocation.speed))
    a("geolocation.timestamp:" + str(geolocation.timestamp))
    a("geolocation.permission:" + geolocation.permission)

    a("speech.voices.local:" + "|".join(country.speech_local))
    a("speech.voices.remote:" + "|".join(country.speech_remote))
    a("speech.voices.local.langs:" + "|".join(country.speech_local_langs))
    a("speech.voices.remote.langs:" + "|".join(country.speech_remote_langs))
    a("speech.voices.default.name:" + country.speech_default_name)
    a("speech.voices.default.lang:" + country.speech_default_lang)

    a("font_system:" + hw.font_system)
    a("useragent:" + fp.useragent)
    a("hardwareConcurrency:" + str(hw.hardware_concurrency))

    w = hw.webgl
    a("webgl.vendor:" + w.vendor)
    a("webgl.renderer:" + w.renderer)
    a("webgl.version:" + w.version)
    a("webgl.glsl_version:" + w.glsl_version)
    a("webgl.unmasked_vendor:" + w.unmasked_vendor)
    a("webgl.unmasked_renderer:" + w.unmasked_renderer)
    a("webgl.max_texture_size:" + str(w.max_texture_size))
    a("webgl.max_cube_map_texture_size:" + str(w.max_cube_map_texture_size))
    a("webgl.max_texture_image_units:" + str(w.max_texture_image_units))
    a("webgl.max_vertex_attribs:" + str(w.max_vertex_attribs))
    a("webgl.aliased_point_size_max:" + str(w.aliased_point_size_max))
    a("webgl.max_viewport_dim:" + str(w.max_viewport_dim))

    a("canvas.mode:pixel")
    a("canvas.seed:" + str(canvas_seed))
    a("canvas.strength:low")
    a("canvas.preserveAlpha:true")
    a("canvas.preserveWhitePoint:true")
    a("canvas.pngMetadata:false")
    a("audio.seed:" + str(audio_seed))

    proxy_scheme = (proxy_scheme or "http").strip().lower()
    # The fingerprint browser reads HTTP and SOCKS5 proxy credentials from
    # different fpfile namespaces.  Writing HTTP credentials for a SOCKS5
    # proxy leaves Firefox without usable SOCKS credentials and can trigger
    # the native username/password dialog.
    if proxy_scheme in ("socks", "socks5"):
        if proxy_host and proxy_port and proxy_user and proxy_pwd:
            a("socksauth.host:" + str(proxy_host))
            a("socksauth.port:" + str(int(proxy_port)))
            a("socksauth.username:" + proxy_user)
            a("socksauth.password:" + proxy_pwd)
    elif proxy_user and proxy_pwd:
        if proxy_host and proxy_port:
            a("httpauth.host:" + str(proxy_host))
            a("httpauth.port:" + str(int(proxy_port)))
        a("httpauth.username:" + proxy_user)
        a("httpauth.password:" + proxy_pwd)

    if extra_items:
        for k, v in extra_items:
            a("{}:{}".format(k, v))

    _atomic_write_text(fpfile_path, "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# FingerprintContext - one-stop result + emulation overlay
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FingerprintContext:
    """Bundle of state produced by :func:`apply_smart_fingerprint`.

    Returned to the caller so business code can:

    1. Log a single summary line (:meth:`summary`).
    2. Inject the BiDi emulation overlay on the live page
       (:meth:`apply_emulation`).
    3. Persist the fingerprint identity (:meth:`to_dict`).

    All fields are immutable - dataclass is ``frozen=True`` for safety.

    Attributes
    ----------
    geo : GeoInfo
    fingerprint : FingerprintProfile
    userdir : str
        Absolute path of the userdir written / used.
    fpfile_path : str
        Absolute path of the fpfile written.
    proxies : dict or None
        ``requests``-style proxies dict (mirrored from inputs).
    proxy_scheme / proxy_host / proxy_port / proxy_user / proxy_pwd
        Original proxy parameters, kept for diagnostics.
    geolocation
        Validated coordinates shared by the fpfile and BiDi overlay.
    webrtc_local_ipv4 / webrtc_local_ipv6 / webrtc_public_ipv4 /
    webrtc_public_ipv6
        Validated explicit ICE overrides, or ``None`` for native ICE.
    """

    geo: GeoInfo
    fingerprint: FingerprintProfile
    userdir: str
    fpfile_path: str
    proxies: Optional[Dict[str, str]] = None
    proxy_scheme: str = "http"
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_user: Optional[str] = None
    proxy_pwd: Optional[str] = None
    geolocation: Optional[GeolocationProfile] = None
    webrtc_local_ipv4: Optional[str] = None
    webrtc_local_ipv6: Optional[str] = None
    webrtc_public_ipv4: Optional[str] = None
    webrtc_public_ipv6: Optional[str] = None

    # ---- inspection ----

    def summary(self) -> str:
        """Return a single-line human-readable summary for logging."""
        masked = _mask_ip(self.geo.ip)
        ipv6_state = "yes" if self.geo.ipv6 else "no"
        return (
            "[fp] {pid} ua=Firefox/{ver} webgl={vendor} "
            "geo={cc}/{tz} ip={ip} ipv6={v6} canvas={c} audio={a}"
        ).format(
            pid=self.fingerprint.profile_id,
            ver=self.fingerprint.firefox_version,
            vendor=self.fingerprint.hardware.webgl.vendor,
            cc=self.geo.country_code,
            tz=self.geo.timezone,
            ip=masked,
            v6=ipv6_state,
            c=self.fingerprint.canvas_seed,
            a=self.fingerprint.audio_seed,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable summary, e.g. for an account log."""
        geolocation = self.geolocation or _build_geolocation_profile(self.geo)
        webrtc_values = (
            self.webrtc_local_ipv4,
            self.webrtc_local_ipv6,
            self.webrtc_public_ipv4,
            self.webrtc_public_ipv6,
        )
        return {
            "profile_id": self.fingerprint.profile_id,
            "firefox_version": self.fingerprint.firefox_version,
            "useragent": self.fingerprint.useragent,
            "country_code": self.geo.country_code,
            "timezone": self.geo.timezone,
            "language": self.fingerprint.country.language,
            "ip": self.geo.ip,
            "ipv6": self.geo.ipv6,
            "userdir": self.userdir,
            "fpfile_path": self.fpfile_path,
            "canvas_seed": self.fingerprint.canvas_seed,
            "audio_seed": self.fingerprint.audio_seed,
            "geolocation": dataclasses.asdict(geolocation),
            "webrtc": {
                "mode": (
                    "explicit"
                    if any(value is not None for value in webrtc_values)
                    else "native"
                ),
                "local_ipv4": self.webrtc_local_ipv4,
                "local_ipv6": self.webrtc_local_ipv6,
                "public_ipv4": self.webrtc_public_ipv4,
                "public_ipv6": self.webrtc_public_ipv6,
            },
        }

    # ---- emulation overlay ----

    def apply_emulation(
        self,
        page: Any,
        *,
        set_screen_size: bool = True,
        set_geolocation: bool = True,
        set_locale: bool = True,
        set_timezone: bool = True,
        set_extra_headers: bool = True,
        logger: Optional[Callable[[str], None]] = None,
    ) -> Union[Dict[str, bool], Awaitable[Dict[str, bool]]]:
        """Inject a BiDi emulation overlay on top of the kernel fingerprint.

        Acts as a defence-in-depth layer: even if a kernel field doesn't
        cover a particular detection vector, the BiDi emulation API will.
        Every individual call is wrapped in ``try/except`` so missing
        ruyipage versions degrade gracefully.

        Parameters
        ----------
        page : FirefoxPage
            The live page returned by ``FirefoxPage(opts)``.
        set_screen_size / set_geolocation / set_locale / set_timezone /
        set_extra_headers
            Toggle individual overlays.
        logger : callable, optional
            Receives ``[emu] ...`` status messages.

        Returns
        -------
        dict[str, bool] or Awaitable[dict[str, bool]]
            ``{"screen": bool, "geolocation": bool, "locale": bool,
            "timezone": bool, "headers": bool}`` - whether each overlay
            was applied. Synchronous page hooks return the mapping directly;
            when a hook is asynchronous, await the returned object to execute
            the remaining overlays and obtain the mapping.
        """
        log = logger or (lambda _msg: None)
        result = {"screen": False, "geolocation": False, "locale": False,
                  "timezone": False, "headers": False}
        geolocation = self.geolocation or _build_geolocation_profile(self.geo)
        locales = [part.strip() for part in self.fingerprint.country.language.split(",")
                   if part.strip()]
        operations = []

        def add_operation(key, call, message):
            operations.append((key, call, message))

        if set_screen_size:
            add_operation(
                "screen",
                lambda: page.emulation.set_screen_size(
                    self.fingerprint.hardware.width,
                    self.fingerprint.hardware.height,
                ),
                "[emu] screen {}x{}".format(
                    self.fingerprint.hardware.width,
                    self.fingerprint.hardware.height,
                ),
            )
        if set_geolocation:
            if geolocation.enabled:
                if geolocation.timestamp != "now":
                    add_operation(
                        "geolocation_reset",
                        lambda: page.emulation.clear_geolocation(),
                        "[emu] geolocation custom timestamp is kernel-managed; "
                        "cleared BiDi override",
                    )
                else:
                    geolocation_kwargs = {"accuracy": geolocation.accuracy}
                    for key, value in (
                        ("altitude", geolocation.altitude),
                        ("altitude_accuracy", geolocation.altitude_accuracy),
                        ("heading", geolocation.heading),
                        ("speed", geolocation.speed),
                    ):
                        if value is not None:
                            geolocation_kwargs[key] = value
                    add_operation(
                        "geolocation",
                        lambda: page.emulation.set_geolocation(
                            geolocation.latitude,
                            geolocation.longitude,
                            **geolocation_kwargs
                        ),
                        "[emu] geolocation ({}, {})".format(
                            geolocation.latitude, geolocation.longitude
                        ),
                    )
            else:
                add_operation(
                    "geolocation_reset",
                    lambda: page.emulation.clear_geolocation(),
                    "[emu] geolocation disabled; cleared BiDi override",
                )
        if set_locale:
            add_operation(
                "locale",
                lambda: page.emulation.set_locale(locales),
                "[emu] locale {}".format(",".join(locales)),
            )
        if set_timezone:
            add_operation(
                "timezone",
                lambda: page.emulation.set_timezone(self.geo.timezone),
                "[emu] timezone {}".format(self.geo.timezone),
            )
        if set_extra_headers:
            add_operation(
                "headers",
                lambda: page.network.set_extra_headers({
                    "Accept-Language": self.fingerprint.accept_language,
                }),
                "[emu] headers Accept-Language={}".format(
                    self.fingerprint.accept_language
                ),
            )

        def mark_success(key, message):
            if key in result:
                result[key] = True
            log(message)

        def invoke(index):
            key, call, message = operations[index]
            try:
                value = call()
            except Exception as e:  # noqa: BLE001
                log("[emu] {} skipped: {}".format(key, e))
                return None
            if inspect.isawaitable(value):
                return value
            mark_success(key, message)
            return None

        for index in range(len(operations)):
            pending = invoke(index)
            if pending is None:
                continue

            async def finish(start_index=index, first_pending=pending):
                key, _call, message = operations[start_index]
                try:
                    await first_pending
                except Exception as e:  # noqa: BLE001
                    log("[emu] {} skipped: {}".format(key, e))
                else:
                    mark_success(key, message)

                for next_index in range(start_index + 1, len(operations)):
                    next_key, next_call, next_message = operations[next_index]
                    try:
                        value = next_call()
                        if inspect.isawaitable(value):
                            await value
                    except Exception as e:  # noqa: BLE001
                        log("[emu] {} skipped: {}".format(next_key, e))
                    else:
                        mark_success(next_key, next_message)
                return result

            return finish()

        return result

    async def apply_emulation_async(
        self,
        page: Any,
        *,
        set_screen_size: bool = True,
        set_geolocation: bool = True,
        set_locale: bool = True,
        set_timezone: bool = True,
        set_extra_headers: bool = True,
        logger: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, bool]:
        """Async wrapper that always returns an awaitable result mapping."""
        result = self.apply_emulation(
            page,
            set_screen_size=set_screen_size,
            set_geolocation=set_geolocation,
            set_locale=set_locale,
            set_timezone=set_timezone,
            set_extra_headers=set_extra_headers,
            logger=logger,
        )
        if inspect.isawaitable(result):
            return await result
        return result


# ---------------------------------------------------------------------------
# Helpers for apply_smart_fingerprint
# ---------------------------------------------------------------------------

def _mask_ip(ip: str) -> str:
    """Return ``a.b.c.*`` style masked IPv4 (or original IPv6 fragment)."""
    try:
        if ":" in ip:
            segs = ip.split(":")
            return ":".join(segs[:3]) + ":*"
        parts = ip.split(".")
        if len(parts) == 4:
            return "{}.{}.{}.*".format(parts[0], parts[1], parts[2])
    except Exception:
        pass
    return ip


def _generate_userdir(base_dir: Optional[str]) -> str:
    """Create a unique timestamped userdir under ``base_dir`` (or cwd)."""
    base = os.path.abspath(base_dir) if base_dir else os.getcwd()
    os.makedirs(base, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    path = os.path.join(base, "userdir_{}_{}".format(stamp, rand))
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# One-stop API
# ---------------------------------------------------------------------------

def apply_smart_fingerprint(
    opts: Any,
    *,
    proxy_scheme: str = "http",
    proxy_host: Optional[str] = None,
    proxy_port: Optional[int] = None,
    proxy_user: Optional[str] = None,
    proxy_pwd: Optional[str] = None,
    userdir: Optional[str] = None,
    base_dir: Optional[str] = None,
    fpfile_name: str = "fpfile.txt",
    firefox_version: Optional[int] = None,
    webrtc_local_ipv4: Optional[str] = None,
    webrtc_local_ipv6: Optional[str] = None,
    webrtc_public_ipv4: Optional[str] = None,
    webrtc_public_ipv6: Optional[str] = None,
    geolocation_enabled: bool = True,
    geolocation_latitude: Optional[float] = None,
    geolocation_longitude: Optional[float] = None,
    geolocation_accuracy: float = DEFAULT_GEOLOCATION_ACCURACY,
    geolocation_altitude: Optional[float] = None,
    geolocation_altitude_accuracy: Optional[float] = None,
    geolocation_heading: Optional[float] = None,
    geolocation_speed: Optional[float] = None,
    geolocation_timestamp: Any = "now",
    geolocation_permission: str = "granted",
    require_country: Optional[str] = "US",
    geo_timeout: float = 8.0,
    geo_retries: int = 1,
    manual_geo: Optional[Any] = None,
    fetch_ipv6: bool = True,
    fingerprints_path: Optional[str] = None,
    region_locales_path: Optional[str] = None,
    rng: Optional[random.Random] = None,
    set_proxy_on_opts: bool = True,
    set_userdir_on_opts: bool = True,
    set_fpfile_on_opts: bool = True,
    set_startup_page_on_opts: bool = True,
    set_window_size_on_opts: bool = False,
    logger: Optional[Callable[[str], None]] = None,
) -> FingerprintContext:
    """One-stop smart fingerprint configuration.

    Pipeline (executed in order):

    1. ``build_proxies_dict()``  - construct the ``requests`` proxies dict.
    2. ``fetch_geo_info()``      - resolve egress geo; enforce
       ``require_country`` if set. If all online sources fail and
       ``manual_geo`` is provided, continue with that explicit user data.
    3. ``fetch_public_ipv6()``   - optional, best-effort.
    4. ``_generate_userdir()``   - only if ``userdir`` is ``None``.
    5. ``pick_fingerprint()``    - sample one of the 22 hardware profiles.
    6. ``write_fpfile()``        - serialize to ``fpfile.txt``.
    7. Configure the supplied ``FirefoxOptions``: proxy / userdir / fpfile
       and a script-accessible ``about:blank`` startup page (toggleable
       individually).

    Parameters
    ----------
    opts : FirefoxOptions
        The options instance to configure. Not type-annotated to avoid
        an import cycle with :mod:`ruyipage._configs.firefox_options`.
    proxy_scheme / proxy_host / proxy_port / proxy_user / proxy_pwd
        Proxy info; omit host/port for direct connection. ``proxy_scheme``
        defaults to ``"http"`` and also supports ``"socks5"``.
    userdir : str, optional
        Pre-existing userdir to reuse; when ``None`` a fresh one is
        created under ``base_dir`` (or the current working directory).
    base_dir : str, optional
        Parent directory used to allocate new userdirs.
    fpfile_name : str
        File name written under the userdir.
    firefox_version : int, optional
        Exact Firefox major version for the generated UA. When omitted, the
        value is read from ``opts.browser_path`` and falls back to the bundled
        fingerprint data only when the executable cannot be queried.
    webrtc_local_ipv4 / webrtc_local_ipv6 / webrtc_public_ipv4 /
    webrtc_public_ipv6 : str, optional
        Explicit ICE addresses for the Firefox WebRTC policy. If omitted,
        the generated fpfile leaves WebRTC policy in native mode.
    geolocation_* : optional
        Coordinated kernel/BiDi geolocation profile. Coordinates default to
        the proxy-derived ``GeoInfo`` and accuracy defaults to 15000 metres.
        Numeric timestamps are Unix epoch milliseconds and remain kernel-only
        because WebDriver BiDi has no timestamp field. ``prompt`` / ``denied``
        permission states are likewise enforced by the startup kernel; BiDi
        only overlays the coordinate fields.
    require_country : str, optional
        ISO-2 country code required for the egress IP; ``None`` disables
        the check. Default ``"US"``.
    geo_timeout / geo_retries : float / int
        Forwarded to :func:`fetch_geo_info`.
    manual_geo : GeoInfo or dict, optional
        Explicit fallback used only when every online geo source fails.
        Required mapping fields: ``ip``, ``country_code``, ``timezone``,
        ``latitude`` and ``longitude``. Optional fields: ``country``,
        ``region``, ``city`` and ``ipv6``.
    fetch_ipv6 : bool
        Whether to attempt IPv6 enrichment.
    fingerprints_path / region_locales_path : str, optional
        Override the bundled JSON files.
    rng : random.Random, optional
        Inject deterministic randomness for tests.
    set_proxy_on_opts / set_userdir_on_opts / set_fpfile_on_opts : bool
        Individually enable or disable each opts mutation if you want
        to drive them yourself.
    set_startup_page_on_opts : bool
        Add ``about:blank`` as the startup page so BiDi overlays can be
        applied immediately without privileged system access. Disable this
        when the caller already supplies a script-accessible startup URL.
    set_window_size_on_opts : bool
        Deprecated compatibility parameter. Fingerprint screen dimensions
        are never mapped to the Firefox outer window. Call
        ``opts.set_window_size()`` yourself when an explicit outer window is
        required.
    logger : callable, optional
        Receives ``[fp] ...`` status messages.

    Returns
    -------
    FingerprintContext

    Raises
    ------
    FingerprintError, CountryMismatchError, GeoError, FingerprintConfigError,
    OSError
    """
    log = logger or (lambda _msg: None)

    # 1) proxies dict
    proxy_scheme = (proxy_scheme or "http").strip().lower()
    # One user-facing proxy declaration drives two consumers: requests for
    # geo lookup and Firefox for page traffic.  requests needs socks5h:// for
    # remote DNS, while Firefox user.js/fpfile uses socks5:// plus socksauth.*.
    proxies = build_proxies_dict(
        proxy_host, proxy_port, proxy_user, proxy_pwd, scheme=proxy_scheme
    )

    # 2) geo (with optional country gate)
    try:
        geo = fetch_geo_info(
            proxies,
            require_country=require_country,
            timeout=geo_timeout,
            retries_per_source=geo_retries,
            logger=log,
        )
    except CountryMismatchError:
        raise
    except GeoError as e:
        if manual_geo is None:
            raise GeoError(
                str(e)
                + ". Please provide manual_geo with required fields: "
                + "ip, country_code, timezone, latitude, longitude. "
                + "Optional fields: country, region, city, ipv6."
            )
        geo = coerce_manual_geo(manual_geo, require_country=require_country)
        log("[fp] geo lookup failed; using manual_geo cc={} tz={} ip={}".format(
            geo.country_code, geo.timezone, _mask_ip(geo.ip)))

    # 3) optional ipv6
    if fetch_ipv6:
        ipv6 = fetch_public_ipv6(proxies, timeout=max(4.0, geo_timeout - 2),
                                 logger=log)
        if ipv6:
            geo = dataclasses.replace(geo, ipv6=ipv6)

    # 4) userdir
    if userdir:
        userdir_abs = os.path.abspath(userdir)
        os.makedirs(userdir_abs, exist_ok=True)
    else:
        userdir_abs = _generate_userdir(base_dir)
    log("[fp] userdir " + userdir_abs)

    # 5) pick fingerprint
    resolved_firefox_version = firefox_version
    if resolved_firefox_version is None:
        browser_path = getattr(opts, "browser_path", None)
        resolved_firefox_version = _detect_firefox_major_version(browser_path)
        if resolved_firefox_version is not None:
            log("[fp] detected Firefox/{}".format(resolved_firefox_version))

    fp = pick_fingerprint(
        geo,
        firefox_version=resolved_firefox_version,
        fingerprints_path=fingerprints_path,
        region_locales_path=region_locales_path,
        rng=rng,
    )
    log("[fp] picked profile " + fp.profile_id)

    geolocation = _build_geolocation_profile(
        geo,
        enabled=geolocation_enabled,
        latitude=geolocation_latitude,
        longitude=geolocation_longitude,
        accuracy=geolocation_accuracy,
        altitude=geolocation_altitude,
        altitude_accuracy=geolocation_altitude_accuracy,
        heading=geolocation_heading,
        speed=geolocation_speed,
        timestamp=geolocation_timestamp,
        permission=geolocation_permission,
    )
    webrtc_local_ipv4 = _validate_webrtc_ip(
        "webrtc_local_ipv4", webrtc_local_ipv4, 4
    )
    webrtc_local_ipv6 = _validate_webrtc_ip(
        "webrtc_local_ipv6", webrtc_local_ipv6, 6
    )
    webrtc_public_ipv4 = _validate_webrtc_ip(
        "webrtc_public_ipv4", webrtc_public_ipv4, 4
    )
    webrtc_public_ipv6 = _validate_webrtc_ip(
        "webrtc_public_ipv6", webrtc_public_ipv6, 6
    )

    # 6) write fpfile
    fpfile_path = os.path.join(userdir_abs, fpfile_name)
    write_fpfile(
        fpfile_path,
        geo,
        fp,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        proxy_user=proxy_user,
        proxy_pwd=proxy_pwd,
        proxy_scheme=proxy_scheme,
        webrtc_local_ipv4=webrtc_local_ipv4,
        webrtc_local_ipv6=webrtc_local_ipv6,
        webrtc_public_ipv4=webrtc_public_ipv4,
        webrtc_public_ipv6=webrtc_public_ipv6,
        geolocation_enabled=geolocation.enabled,
        geolocation_latitude=geolocation.latitude,
        geolocation_longitude=geolocation.longitude,
        geolocation_accuracy=geolocation.accuracy,
        geolocation_altitude=geolocation.altitude,
        geolocation_altitude_accuracy=geolocation.altitude_accuracy,
        geolocation_heading=geolocation.heading,
        geolocation_speed=geolocation.speed,
        geolocation_timestamp=geolocation.timestamp,
        geolocation_permission=geolocation.permission,
    )
    log("[fp] fpfile " + fpfile_path)

    # 7) opts side-effects
    if set_proxy_on_opts and proxy_host and proxy_port:
        try:
            # Do not embed credentials in Firefox network.proxy prefs.  The
            # fingerprint browser consumes httpauth.* or socksauth.* from the
            # fpfile, and embedding secrets in user.js would leak them.
            opts_scheme = "socks5" if proxy_scheme in ("socks", "socks5") else "http"
            opts.set_proxy("{}://{}:{}".format(opts_scheme, proxy_host, proxy_port))
        except Exception as e:  # noqa: BLE001
            log("[fp] set_proxy failed: " + str(e))

    if set_userdir_on_opts:
        try:
            opts.set_user_dir(userdir_abs)
        except Exception as e:  # noqa: BLE001
            log("[fp] set_user_dir failed: " + str(e))

    if set_fpfile_on_opts:
        try:
            opts.set_fpfile(fpfile_path)
        except Exception as e:  # noqa: BLE001
            log("[fp] set_fpfile failed: " + str(e))

    if set_startup_page_on_opts:
        try:
            opts.set_argument("about:blank")
        except Exception as e:  # noqa: BLE001
            log("[fp] set startup page failed: " + str(e))

    if set_window_size_on_opts:
        log(
            "[fp] set_window_size_on_opts is deprecated and ignored; "
            "call opts.set_window_size() explicitly for an outer window"
        )

    return FingerprintContext(
        geo=geo,
        fingerprint=fp,
        userdir=userdir_abs,
        fpfile_path=fpfile_path,
        proxies=proxies,
        proxy_scheme=proxy_scheme,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        proxy_user=proxy_user,
        proxy_pwd=proxy_pwd,
        geolocation=geolocation,
        webrtc_local_ipv4=webrtc_local_ipv4,
        webrtc_local_ipv6=webrtc_local_ipv6,
        webrtc_public_ipv4=webrtc_public_ipv4,
        webrtc_public_ipv6=webrtc_public_ipv6,
    )
