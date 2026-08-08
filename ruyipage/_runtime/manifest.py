# -*- coding: utf-8 -*-
"""Static Firefox runtime manifest for ruyiPage managed Firefox."""

RELEASE_TAG = "v1.2.58"
FIREFOX_VERSION = "155.0a1"
RELEASE_BASE_URL = "https://github.com/LoseNine/ruyipage/releases/download/{}".format(RELEASE_TAG)

RUNTIME_NAME = "firefox"

RUNTIMES = {
    "win64": {
        "name": RUNTIME_NAME,
        "version": FIREFOX_VERSION,
        "release": RELEASE_TAG,
        "asset": "firefox-155.0a1.en-US.win64-20260803.zip",
        "archive_type": "zip",
        "executable": "firefox/firefox.exe",
        "install_subdir": "firefox-155.0a1-v1.2.58-win64",
        "max_files": 20000,
        "max_total_size": 900 * 1024 * 1024,
    },
    "linux-x86_64": {
        "name": RUNTIME_NAME,
        "version": FIREFOX_VERSION,
        "release": RELEASE_TAG,
        "asset": "firefox-155.0a1.en-US.linux-x86_64.tar.xz",
        "archive_type": "tar.xz",
        "executable": "firefox/firefox",
        "install_subdir": "firefox-155.0a1-v1.2.58-linux-x86_64",
        "max_files": 20000,
        "max_total_size": 900 * 1024 * 1024,
    },
}


def runtime_url(info, base_url=None):
    """Return the download URL for a runtime info entry."""
    root = (base_url or RELEASE_BASE_URL).rstrip("/")
    return "{}/{}".format(root, info["asset"])
