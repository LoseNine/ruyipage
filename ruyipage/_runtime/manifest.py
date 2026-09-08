# -*- coding: utf-8 -*-
"""Static Firefox runtime manifest for ruyiPage managed Firefox."""

RELEASE_TAG = "v1.2.69"
FIREFOX_VERSION = "155.0"
RELEASE_URL_TEMPLATE = "https://github.com/LoseNine/ruyipage/releases/download/{}"
RELEASE_BASE_URL = RELEASE_URL_TEMPLATE.format(RELEASE_TAG)

RUNTIME_NAME = "firefox"

# 每个平台各自记录 version / release：两个内核构建不一定同时完成，落后的平台
# 可以继续指向仍然可用的旧 release，而不是指向一个尚未上传资源的 tag 直接 404。
RUNTIMES = {
    "win64": {
        "name": RUNTIME_NAME,
        "version": FIREFOX_VERSION,
        "release": RELEASE_TAG,
        "asset": "firefox-155.0.en-US.win64-20260907.zip",
        "archive_type": "zip",
        "executable": "firefox/firefox.exe",
        "install_subdir": "firefox-155.0-v1.2.69-win64",
        "max_files": 20000,
        "max_total_size": 900 * 1024 * 1024,
    },
    "linux-x86_64": {
        "name": RUNTIME_NAME,
        "version": FIREFOX_VERSION,
        "release": RELEASE_TAG,
        "asset": "firefox-155.0.en-US.linux-x86_64-20260907.tar.xz",
        "archive_type": "tar.xz",
        "executable": "firefox/firefox",
        "install_subdir": "firefox-155.0-v1.2.69-linux-x86_64",
        "max_files": 20000,
        "max_total_size": 900 * 1024 * 1024,
    },
}


def runtime_url(info, base_url=None):
    """Return the download URL for a runtime info entry."""
    if base_url:
        root = base_url.rstrip("/")
    else:
        root = RELEASE_URL_TEMPLATE.format(info.get("release") or RELEASE_TAG)
    return "{}/{}".format(root.rstrip("/"), info["asset"])
