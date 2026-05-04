# -*- coding: utf-8 -*-
"""
ruyipage._fingerprint.builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Smart fingerprint helpers built on top of the firefox-fingerprintBrowser
kernel (https://github.com/LoseNine/firefox-fingerprintBrowser).

Goal
----
Provide a single one-stop API ``apply_smart_fingerprint(opts, ...)`` that:

1. Probes the egress IP and resolves geo information through 5 fall-back
   data sources (geojs.io, ipapi.co, ipwho.is, ip-api.com, ipinfo.io).
2. Optionally enforces a country-code requirement (``require_country``).
3. Picks one of 22 real Windows hardware profiles (NVIDIA / AMD / Intel),
   composes a Firefox 151 ±2 user-agent and a per-session canvas seed.
4. Maps the country code to language / Accept-Language / speech voices.
5. Writes a ``fpfile.txt`` that strictly follows the firefox-fingerprintBrowser
   field schema (``key:value``) to the chosen userdir.
6. Configures the supplied ``FirefoxOptions`` instance (proxy / userdir /
   fpfile / window size) so the caller only needs ``FirefoxPage(opts)``.

Public API
----------
::

    apply_smart_fingerprint(opts, ...) -> FingerprintContext

    fetch_geo_info(proxies, ...) -> GeoInfo
    fetch_public_ipv6(proxies, ...) -> Optional[str]
    pick_fingerprint(geo, ...) -> FingerprintProfile
    write_fpfile(path, geo, fp, ...) -> None

    build_proxies_dict(host, port, user, pwd) -> Optional[Dict[str, str]]
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
            logger=print,
        )
    except CountryMismatchError as e:
        print(f"country mismatch: {e.actual} != {e.required}")
        raise
    except GeoError as e:
        print(f"geo lookup failed: {e}")
        raise

    page = FirefoxPage(opts)
    ctx.apply_emulation(page)        # one-line BiDi emulation overlay
    page.get("https://browserleaks.com/webgl")
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import random
import re
import string
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FingerprintError(Exception):
    """Base class for all fingerprint-module errors.

    Catch this single class in business code if you only want to handle
    fingerprint preparation failures uniformly.
    """


class FingerprintConfigError(FingerprintError):
    """The ``fingerprints.json`` or ``region_locales.json`` file is invalid.

    Raised by the JSON loader when a schema constraint is violated:
    missing field, wrong type, length mismatch in speech arrays, etc.
    This is a deployment-time error, not a runtime network error.
    """


class GeoError(FingerprintError):
    """Every geo data source failed to return a usable response.

    The exception ``message`` includes a brief failure summary for each
    of the five sources tried, so you can tell whether it was a network
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
) -> Optional[Dict[str, str]]:
    """Build a ``requests``-compatible ``proxies`` dict.

    Parameters
    ----------
    host, port : str / int
        Proxy host / port. If either is falsy, returns ``None`` (direct).
    user, pwd : str, optional
        HTTP basic auth credentials embedded in the URL. Both must be
        provided together; otherwise the proxy is treated as anonymous.

    Returns
    -------
    dict or None
        ``{"http": url, "https": url}``; ``None`` for direct mode.
    """
    if not host or not port:
        return None
    if user and pwd:
        url = "http://{}:{}@{}:{}".format(user, pwd, host, port)
    else:
        url = "http://{}:{}".format(host, port)
    return {"http": url, "https": url}


# ---------------------------------------------------------------------------
# Geo lookup with multi-source fall-back
# ---------------------------------------------------------------------------

# Each entry: (tag, url, parser_callable)
_GEO_SOURCES: List[Tuple[str, str, str]] = [
    ("geojs",  "https://get.geojs.io/v1/ip/geo.json",                "geojs"),
    ("ipapi",  "https://ipapi.co/json/",                              "ipapi"),
    ("ipwho",  "https://ipwho.is/",                                   "ipwho"),
    ("ipapi2", "http://ip-api.com/json?fields=66846719",              "ipapi2"),
    ("ipinfo", "https://ipinfo.io/json",                              "ipinfo"),
]


def _to_float(value: Any) -> float:
    """Coerce arbitrary input to ``float``; raises ``ValueError`` if invalid."""
    return float(str(value).strip())


def _parse_geojs(payload: Dict[str, Any]) -> Optional[GeoInfo]:
    """Parse the JSON returned by ``get.geojs.io/v1/ip/geo.json``."""
    return GeoInfo(
        ip=str(payload["ip"]).strip(),
        country_code=str(payload.get("country_code", "")).strip().upper(),
        country=str(payload.get("country", "")).strip(),
        region=str(payload.get("region", "")).strip(),
        city=str(payload.get("city", "")).strip(),
        timezone=str(payload.get("timezone", "")).strip(),
        latitude=_to_float(payload.get("latitude", 0)),
        longitude=_to_float(payload.get("longitude", 0)),
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
        latitude=_to_float(payload.get("latitude", 0)),
        longitude=_to_float(payload.get("longitude", 0)),
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
        latitude=_to_float(payload.get("latitude", 0)),
        longitude=_to_float(payload.get("longitude", 0)),
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
        latitude=_to_float(payload.get("lat", 0)),
        longitude=_to_float(payload.get("lon", 0)),
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


_PARSERS = {
    "geojs":  _parse_geojs,
    "ipapi":  _parse_ipapi,
    "ipwho":  _parse_ipwho,
    "ipapi2": _parse_ipapi_com,
    "ipinfo": _parse_ipinfo,
}


def _validate_geo(geo: GeoInfo) -> None:
    """Enforce required-field constraints on a parsed :class:`GeoInfo`."""
    if not geo.ip:
        raise ValueError("missing ip")
    if not geo.country_code or len(geo.country_code) != 2:
        raise ValueError("invalid country_code: %r" % geo.country_code)
    if not geo.timezone:
        raise ValueError("missing timezone")


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
    """Resolve the egress IP and its geo info via 5 fall-back data sources.

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
    CountryMismatchError
        ``require_country`` set and the geo source observed a different cc.
    GeoError
        All five sources failed (network, parse, missing fields).
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


# ---------------------------------------------------------------------------
# Public IPv6 lookup (optional, best-effort)
# ---------------------------------------------------------------------------

# RFC 4291-ish loose check; sufficient to filter out IPv4 strings.
_IPV6_RE = re.compile(r"^[0-9A-Fa-f:]+$")

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
    Never raises - if the IPv6 cannot be resolved, the caller should
    simply omit the ``*_webrtc_ipv6`` lines from the fpfile.
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
            if ":" in ip and _IPV6_RE.match(ip):
                log("[fp] ipv6 ok {} via {}".format(ip, tag))
                return ip
        except Exception:  # noqa: BLE001
            continue
    log("[fp] ipv6 unavailable")
    return None


# ---------------------------------------------------------------------------
# Fingerprint composition
# ---------------------------------------------------------------------------

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
    4. Sample a Firefox version from ``firefox_base_version`` plus the
       configured small-version jitter (default ``±2``).
    5. Generate a random canvas seed in ``[1, 999]``.

    Parameters
    ----------
    geo : GeoInfo
        Output of :func:`fetch_geo_info`.
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
    """
    rnd = rng or random
    fp_data = _load_fingerprints(
        fingerprints_path or default_fingerprints_path()
    )
    hw_dict = rnd.choice(fp_data["hardware_profiles"])
    hw = _hardware_from_dict(hw_dict)

    country = get_country_profile(geo.country_code, region_locales_path)

    base = int(fp_data.get("firefox_base_version", 151))
    jitter = fp_data.get("firefox_minor_jitter") or [0]
    version = max(1, base + rnd.choice(jitter))

    return FingerprintProfile(
        profile_id=hw.id,
        firefox_version=version,
        useragent=_build_useragent(hw, version),
        hardware=hw,
        country=country,
        canvas_seed=rnd.randint(1, 999),
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
    "width", "height", "canvas",
    "httpauth.username", "httpauth.password",
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


def write_fpfile(
    fpfile_path: str,
    geo: GeoInfo,
    fp: FingerprintProfile,
    *,
    proxy_user: Optional[str] = None,
    proxy_pwd: Optional[str] = None,
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
        Source of WebRTC IPs and timezone.
    fp : FingerprintProfile
        Source of every other field (hardware + country + UA + canvas).
    proxy_user, proxy_pwd : str, optional
        When both are provided, ``httpauth.*`` lines are appended so the
        kernel can authenticate the HTTP proxy automatically.
    extra : dict, optional
        Additional ``key: value`` pairs appended after the core fields.
        Cannot override any reserved key (see ``_RESERVED_KEYS``).

    Raises
    ------
    FingerprintError
        ``extra`` tries to override a reserved key.
    OSError
        File-system error during write.
    """
    if extra:
        bad = [k for k in extra if k in _RESERVED_KEYS]
        if bad:
            raise FingerprintError(
                "extra keys collide with reserved fields: %r" % bad
            )

    hw = fp.hardware
    country = fp.country
    ipv6 = (geo.ipv6 or "").strip()

    lines: List[str] = []
    a = lines.append

    a("webdriver:0")

    a("local_webrtc_ipv4:" + geo.ip)
    if ipv6:
        a("local_webrtc_ipv6:" + ipv6)
    a("public_webrtc_ipv4:" + geo.ip)
    if ipv6:
        a("public_webrtc_ipv6:" + ipv6)

    a("timezone:" + geo.timezone)
    a("language:" + country.language)

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

    a("width:" + str(hw.width))
    a("height:" + str(hw.height))
    a("canvas:" + str(fp.canvas_seed))

    if proxy_user and proxy_pwd:
        a("httpauth.username:" + proxy_user)
        a("httpauth.password:" + proxy_pwd)

    if extra:
        for k, v in extra.items():
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
    proxy_host / proxy_port / proxy_user / proxy_pwd
        Original proxy parameters, kept for diagnostics.
    """

    geo: GeoInfo
    fingerprint: FingerprintProfile
    userdir: str
    fpfile_path: str
    proxies: Optional[Dict[str, str]] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_user: Optional[str] = None
    proxy_pwd: Optional[str] = None

    # ---- inspection ----

    def summary(self) -> str:
        """Return a single-line human-readable summary for logging."""
        masked = _mask_ip(self.geo.ip)
        ipv6_state = "yes" if self.geo.ipv6 else "no"
        return (
            "[fp] {pid} ua=Firefox/{ver} webgl={vendor} "
            "geo={cc}/{tz} ip={ip} ipv6={v6} canvas={c}"
        ).format(
            pid=self.fingerprint.profile_id,
            ver=self.fingerprint.firefox_version,
            vendor=self.fingerprint.hardware.webgl.vendor,
            cc=self.geo.country_code,
            tz=self.geo.timezone,
            ip=masked,
            v6=ipv6_state,
            c=self.fingerprint.canvas_seed,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable summary, e.g. for an account log."""
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
        }

    # ---- emulation overlay ----

    def apply_emulation(
        self,
        page: Any,
        *,
        set_geolocation: bool = True,
        set_locale: bool = True,
        set_timezone: bool = True,
        set_extra_headers: bool = True,
        logger: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, bool]:
        """Inject a BiDi emulation overlay on top of the kernel fingerprint.

        Acts as a defence-in-depth layer: even if a kernel field doesn't
        cover a particular detection vector, the BiDi emulation API will.
        Every individual call is wrapped in ``try/except`` so missing
        ruyipage versions degrade gracefully.

        Parameters
        ----------
        page : FirefoxPage
            The live page returned by ``FirefoxPage(opts)``.
        set_geolocation / set_locale / set_timezone / set_extra_headers
            Toggle individual overlays.
        logger : callable, optional
            Receives ``[emu] ...`` status messages.

        Returns
        -------
        dict[str, bool]
            ``{"geolocation": bool, "locale": bool, "timezone": bool,
            "headers": bool}`` - whether each overlay was applied.
        """
        log = logger or (lambda _msg: None)
        result = {"geolocation": False, "locale": False,
                  "timezone": False, "headers": False}

        if set_geolocation:
            try:
                page.emulation.set_geolocation(
                    self.geo.latitude, self.geo.longitude, accuracy=100,
                )
                result["geolocation"] = True
                log("[emu] geolocation ({}, {})".format(
                    self.geo.latitude, self.geo.longitude))
            except Exception as e:  # noqa: BLE001
                log("[emu] geolocation skipped: {}".format(e))

        if set_locale:
            try:
                page.emulation.set_locale([
                    self.fingerprint.language_primary, "en",
                ])
                result["locale"] = True
                log("[emu] locale {}".format(self.fingerprint.language_primary))
            except Exception as e:  # noqa: BLE001
                log("[emu] locale skipped: {}".format(e))

        if set_timezone:
            try:
                page.emulation.set_timezone(self.geo.timezone)
                result["timezone"] = True
                log("[emu] timezone {}".format(self.geo.timezone))
            except Exception as e:  # noqa: BLE001
                log("[emu] timezone skipped: {}".format(e))

        if set_extra_headers:
            try:
                page.network.set_extra_headers({
                    "Accept-Language": self.fingerprint.accept_language,
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                })
                result["headers"] = True
                log("[emu] headers Accept-Language={}".format(
                    self.fingerprint.accept_language))
            except Exception as e:  # noqa: BLE001
                log("[emu] headers skipped: {}".format(e))

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
    proxy_host: Optional[str] = None,
    proxy_port: Optional[int] = None,
    proxy_user: Optional[str] = None,
    proxy_pwd: Optional[str] = None,
    userdir: Optional[str] = None,
    base_dir: Optional[str] = None,
    fpfile_name: str = "fpfile.txt",
    require_country: Optional[str] = "US",
    geo_timeout: float = 8.0,
    geo_retries: int = 1,
    fetch_ipv6: bool = True,
    fingerprints_path: Optional[str] = None,
    region_locales_path: Optional[str] = None,
    rng: Optional[random.Random] = None,
    set_proxy_on_opts: bool = True,
    set_userdir_on_opts: bool = True,
    set_fpfile_on_opts: bool = True,
    set_window_size_on_opts: bool = True,
    logger: Optional[Callable[[str], None]] = None,
) -> FingerprintContext:
    """One-stop smart fingerprint configuration.

    Pipeline (executed in order):

    1. ``build_proxies_dict()``  - construct the ``requests`` proxies dict.
    2. ``fetch_geo_info()``      - resolve egress geo; enforce
       ``require_country`` if set.
    3. ``fetch_public_ipv6()``   - optional, best-effort.
    4. ``_generate_userdir()``   - only if ``userdir`` is ``None``.
    5. ``pick_fingerprint()``    - sample one of the 22 hardware profiles.
    6. ``write_fpfile()``        - serialize to ``fpfile.txt``.
    7. Configure the supplied ``FirefoxOptions``: proxy / userdir /
       fpfile / window size (toggleable individually).

    Parameters
    ----------
    opts : FirefoxOptions
        The options instance to configure. Not type-annotated to avoid
        an import cycle with :mod:`ruyipage._configs.firefox_options`.
    proxy_host / proxy_port / proxy_user / proxy_pwd
        HTTP proxy info; omit for direct connection.
    userdir : str, optional
        Pre-existing userdir to reuse; when ``None`` a fresh one is
        created under ``base_dir`` (or the current working directory).
    base_dir : str, optional
        Parent directory used to allocate new userdirs.
    fpfile_name : str
        File name written under the userdir.
    require_country : str, optional
        ISO-2 country code required for the egress IP; ``None`` disables
        the check. Default ``"US"``.
    geo_timeout / geo_retries : float / int
        Forwarded to :func:`fetch_geo_info`.
    fetch_ipv6 : bool
        Whether to attempt IPv6 enrichment.
    fingerprints_path / region_locales_path : str, optional
        Override the bundled JSON files.
    rng : random.Random, optional
        Inject deterministic randomness for tests.
    set_proxy_on_opts / set_userdir_on_opts / set_fpfile_on_opts /
    set_window_size_on_opts : bool
        Individually disable each opts mutation if you want to drive
        them yourself.
    logger : callable, optional
        Receives ``[fp] ...`` status messages.

    Returns
    -------
    FingerprintContext

    Raises
    ------
    CountryMismatchError, GeoError, FingerprintConfigError, OSError
    """
    log = logger or (lambda _msg: None)

    # 1) proxies dict
    proxies = build_proxies_dict(proxy_host, proxy_port, proxy_user, proxy_pwd)

    # 2) geo (with optional country gate)
    geo = fetch_geo_info(
        proxies,
        require_country=require_country,
        timeout=geo_timeout,
        retries_per_source=geo_retries,
        logger=log,
    )

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
    fp = pick_fingerprint(
        geo,
        fingerprints_path=fingerprints_path,
        region_locales_path=region_locales_path,
        rng=rng,
    )
    log("[fp] picked profile " + fp.profile_id)

    # 6) write fpfile
    fpfile_path = os.path.join(userdir_abs, fpfile_name)
    write_fpfile(fpfile_path, geo, fp,
                 proxy_user=proxy_user, proxy_pwd=proxy_pwd)
    log("[fp] fpfile " + fpfile_path)

    # 7) opts side-effects
    if set_proxy_on_opts and proxy_host and proxy_port:
        try:
            opts.set_proxy("http://{}:{}".format(proxy_host, proxy_port))
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

    if set_window_size_on_opts:
        try:
            opts.set_window_size(fp.hardware.width, fp.hardware.height)
        except Exception as e:  # noqa: BLE001
            log("[fp] set_window_size failed: " + str(e))

    return FingerprintContext(
        geo=geo,
        fingerprint=fp,
        userdir=userdir_abs,
        fpfile_path=fpfile_path,
        proxies=proxies,
        proxy_host=proxy_host,
        proxy_port=proxy_port,
        proxy_user=proxy_user,
        proxy_pwd=proxy_pwd,
    )
