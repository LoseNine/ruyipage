# -*- coding: utf-8 -*-
"""
ruyipage._fingerprint
~~~~~~~~~~~~~~~~~~~~~

Smart fingerprint subsystem for ruyipage.

This subpackage glues together:

* the firefox-fingerprintBrowser kernel (which reads ``fpfile.txt``);
* a bundled pool of 22 Windows hardware profiles;
* a bundled mapping of 30+ countries to language / Accept-Language /
  Microsoft speech voice configurations;
* a multi-source egress IP geo lookup (geojs / ipapi / ipwho /
  ip-api / ipinfo) plus best-effort IPv6 enrichment.

The single entry point is :func:`apply_smart_fingerprint`. The other
exports allow advanced callers to compose the same pipeline manually.

Public API
----------
* :func:`apply_smart_fingerprint` - one-stop configuration helper.
* :class:`FingerprintContext` - bundle of state returned by the helper,
  with :meth:`apply_emulation` for BiDi emulation overlays.
* :func:`fetch_geo_info` / :func:`fetch_public_ipv6` - low-level lookup.
* :func:`pick_fingerprint` / :func:`write_fpfile` - compose / persist.
* :func:`build_proxies_dict` - tiny utility for ``requests`` proxies.
* :func:`list_hardware_profiles` / :func:`get_country_profile` -
  introspect the bundled data files.
* :class:`GeoInfo` / :class:`HardwareProfile` / :class:`WebGLProfile`
  / :class:`CountryProfile` / :class:`FingerprintProfile` - immutable
  data contracts.
* :class:`FingerprintError` / :class:`FingerprintConfigError` /
  :class:`GeoError` / :class:`CountryMismatchError` - error hierarchy.
"""

from .builder import (
    # one-stop API
    apply_smart_fingerprint,
    FingerprintContext,
    # building blocks
    fetch_geo_info,
    fetch_public_ipv6,
    pick_fingerprint,
    write_fpfile,
    build_proxies_dict,
    list_hardware_profiles,
    get_country_profile,
    default_fingerprints_path,
    default_region_locales_path,
    # data contracts
    GeoInfo,
    WebGLProfile,
    HardwareProfile,
    CountryProfile,
    FingerprintProfile,
    # errors
    FingerprintError,
    FingerprintConfigError,
    GeoError,
    CountryMismatchError,
)

__all__ = [
    "apply_smart_fingerprint",
    "FingerprintContext",
    "fetch_geo_info",
    "fetch_public_ipv6",
    "pick_fingerprint",
    "write_fpfile",
    "build_proxies_dict",
    "list_hardware_profiles",
    "get_country_profile",
    "default_fingerprints_path",
    "default_region_locales_path",
    "GeoInfo",
    "WebGLProfile",
    "HardwareProfile",
    "CountryProfile",
    "FingerprintProfile",
    "FingerprintError",
    "FingerprintConfigError",
    "GeoError",
    "CountryMismatchError",
]
