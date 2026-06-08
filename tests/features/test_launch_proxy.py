# -*- coding: utf-8 -*-

from unittest import mock

from ruyipage import FirefoxOptions, launch
from ruyipage._base.browser import Firefox
from ruyipage._bidi import network as bidi_network


class _ProxyAuthOptions:
    def __init__(self, credentials):
        self._credentials = credentials

    def _get_proxy_auth_credentials(self):
        return self._credentials


def test_quick_start_sets_proxy():
    opts = FirefoxOptions()

    opts.quick_start(proxy="http://127.0.0.1:7890")

    assert opts.proxy == "http://127.0.0.1:7890"


def test_quick_start_sets_fpfile():
    opts = FirefoxOptions()

    opts.quick_start(fpfile=r"C:\firefox\fp.txt")

    assert opts.fpfile == r"C:\firefox\fp.txt"


def test_launch_forwards_proxy_to_options():
    created_opts = {}

    def fake_page(opts):
        created_opts["opts"] = opts
        return object()

    with mock.patch("ruyipage.FirefoxPage", side_effect=fake_page):
        launch(proxy="http://127.0.0.1:7890")

    opts = created_opts["opts"]
    assert opts.proxy == "http://127.0.0.1:7890"


def test_launch_forwards_fpfile_to_options():
    created_opts = {}

    def fake_page(opts):
        created_opts["opts"] = opts
        return object()

    with mock.patch("ruyipage.FirefoxPage", side_effect=fake_page):
        launch(fpfile=r"C:\firefox\fp.txt")

    opts = created_opts["opts"]
    assert opts.fpfile == r"C:\firefox\fp.txt"


def test_launch_forwards_user_dir_to_options(tmp_path):
    created_opts = {}

    def fake_page(opts):
        created_opts["opts"] = opts
        return object()

    with mock.patch("ruyipage.FirefoxPage", side_effect=fake_page):
        launch(user_dir=str(tmp_path))

    opts = created_opts["opts"]
    assert opts.profile_path == str(tmp_path)


def test_write_prefs_uses_socks5_proxy_from_fpfile(tmp_path):
    fpfile = tmp_path / "fp.txt"
    fpfile.write_text(
        "proxy.example.com:1000:username-value:password-value\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.quick_start(user_dir=str(tmp_path), fpfile=str(fpfile))

    opts.write_prefs_to_profile()

    content = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("network.proxy.type", 1);' in content
    assert 'user_pref("network.proxy.socks", "proxy.example.com");' in content
    assert 'user_pref("network.proxy.socks_port", 1000);' in content
    assert 'user_pref("network.proxy.socks_version", 5);' in content
    assert 'user_pref("network.proxy.socks_remote_dns", true);' in content
    assert "username-value" not in content
    assert "password-value" not in content


def test_write_prefs_uses_socksauth_fields_from_fpfile(tmp_path):
    fpfile = tmp_path / "fp.txt"
    fpfile.write_text(
        "\n".join(
            [
                "socksauth.host:proxy.example.com",
                "socksauth.port:1000",
                "socksauth.username:username-value",
                "socksauth.password:password-value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.quick_start(user_dir=str(tmp_path), fpfile=str(fpfile))

    opts.write_prefs_to_profile()

    content = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("network.proxy.type", 1);' in content
    assert 'user_pref("network.proxy.socks", "proxy.example.com");' in content
    assert 'user_pref("network.proxy.socks_port", 1000);' in content
    assert 'user_pref("network.proxy.socks_version", 5);' in content
    assert 'user_pref("network.proxy.socks_remote_dns", true);' in content
    assert "username-value" not in content
    assert "password-value" not in content


def test_write_prefs_uses_httpauth_host_port_from_fpfile(tmp_path):
    fpfile = tmp_path / "fp.txt"
    fpfile.write_text(
        "\n".join(
            [
                "httpauth.host:proxy.example.com",
                "httpauth.port:8080",
                "httpauth.username:username-value",
                "httpauth.password:password-value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.quick_start(user_dir=str(tmp_path), fpfile=str(fpfile))

    opts.write_prefs_to_profile()

    content = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("network.proxy.type", 1);' in content
    assert 'user_pref("network.proxy.http", "proxy.example.com");' in content
    assert 'user_pref("network.proxy.http_port", 8080);' in content
    assert 'user_pref("network.proxy.ssl", "proxy.example.com");' in content
    assert 'user_pref("network.proxy.ssl_port", 8080);' in content
    assert 'user_pref("network.captive-portal-service.enabled", false);' in content
    assert 'user_pref("network.connectivity-service.enabled", false);' in content
    assert 'user_pref("signon.autologin.proxy", true);' in content
    assert 'user_pref("network.auth.subresource-http-auth-allow", 2);' in content
    assert "username-value" not in content
    assert "password-value" not in content


def test_write_prefs_strips_http_proxy_credentials_from_set_proxy(tmp_path):
    opts = FirefoxOptions()
    opts.quick_start(
        user_dir=str(tmp_path),
        proxy="http://username-value:password-value@proxy.example.com:8080",
    )

    opts.write_prefs_to_profile()

    content = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("network.proxy.http", "proxy.example.com");' in content
    assert 'user_pref("network.proxy.http_port", 8080);' in content
    assert 'user_pref("network.proxy.ssl", "proxy.example.com");' in content
    assert 'user_pref("network.proxy.ssl_port", 8080);' in content
    assert "username-value" not in content
    assert "password-value" not in content


def test_prepare_runtime_files_removes_httpauth_host_port_from_browser_fpfile(tmp_path):
    source_fpfile = tmp_path / "source-fp.txt"
    source_fpfile.write_text(
        "\n".join(
            [
                "webdriver:0",
                "httpauth.host:proxy.example.com",
                "httpauth.port:8080",
                "httpauth.username:username-value",
                "httpauth.password:password-value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.quick_start(user_dir=str(tmp_path), fpfile=str(source_fpfile))

    opts.prepare_runtime_files()

    assert opts.fpfile != str(source_fpfile)
    assert opts.uses_fpfile_http_proxy is True
    content = (tmp_path / "ruyipage_runtime_fp.txt").read_text(encoding="utf-8")
    assert "webdriver:0" in content
    assert "httpauth.host" not in content
    assert "httpauth.port" not in content
    assert "httpauth.username:username-value" in content
    assert "httpauth.password:password-value" in content

    opts.write_prefs_to_profile()

    user_js = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("network.proxy.http", "proxy.example.com");' in user_js
    assert 'user_pref("network.proxy.http_port", 8080);' in user_js
    assert 'user_pref("network.captive-portal-service.enabled", false);' in user_js
    assert 'user_pref("network.connectivity-service.enabled", false);' in user_js
    assert "username-value" not in user_js
    assert "password-value" not in user_js


def test_proxy_auth_credentials_ignore_httpauth_host_port(tmp_path):
    fpfile = tmp_path / "fp.txt"
    fpfile.write_text(
        "\n".join(
            [
                "httpauth.host=proxy.example.com",
                "httpauth.port=8080",
                "httpauth.username=username-value",
                "httpauth.password=password-value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.quick_start(user_dir=str(tmp_path), fpfile=str(fpfile))

    assert opts._get_proxy_auth_credentials() == {
        "username": "username-value",
        "password": "password-value",
    }


def test_proxy_auth_credentials_read_socksauth_fields_from_fpfile(tmp_path):
    fpfile = tmp_path / "fp.txt"
    fpfile.write_text(
        "\n".join(
            [
                "socksauth.host:proxy.example.com",
                "socksauth.port:1000",
                "socksauth.username:username-value",
                "socksauth.password:password-value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.quick_start(user_dir=str(tmp_path), fpfile=str(fpfile))

    assert opts._get_proxy_auth_credentials() == {
        "username": "username-value",
        "password": "password-value",
    }


def test_write_prefs_does_not_treat_fpfile_ipv6_as_socks5_proxy(tmp_path):
    fpfile = tmp_path / "fp.txt"
    fpfile.write_text(
        "\n".join(
            [
                "webdriver:0",
                "local_webrtc_ipv4:203.0.113.45",
                "local_webrtc_ipv6:2001:db8::1",
                "public_webrtc_ipv6:2001:db8::1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    opts = FirefoxOptions()
    opts.quick_start(user_dir=str(tmp_path), fpfile=str(fpfile))

    opts.write_prefs_to_profile()

    content = (tmp_path / "user.js").read_text(encoding="utf-8")
    assert "network.proxy.socks" not in content
    assert "network.proxy.socks_port" not in content


def test_build_command_includes_profile_and_fpfile():
    opts = FirefoxOptions()
    opts.set_browser_path(r"C:\firefox\firefox.exe")
    opts.set_user_dir(r"C:\firefox\socks5-profile")
    opts.set_fpfile(r"C:\firefox\fp.txt")

    cmd = opts.build_command()

    assert "--no-remote" in cmd
    assert "--profile" in cmd
    assert r"C:\firefox\socks5-profile" in cmd
    assert r"--fpfile=C:\firefox\fp.txt" in cmd


def test_launch_prefers_resolved_runtime_when_no_browser_path():
    created_opts = {}

    def fake_page(opts):
        created_opts["opts"] = opts
        return object()

    with mock.patch("ruyipage.resolve_firefox_path", return_value="D:/runtime/firefox.exe"):
        with mock.patch("ruyipage.FirefoxPage", side_effect=fake_page):
            launch()

    opts = created_opts["opts"]
    assert opts.browser_path == "D:/runtime/firefox.exe"


def test_proxy_auth_uses_credentials_for_407_without_challenge_source(monkeypatch):
    browser = Firefox.__new__(Firefox)
    browser._options = _ProxyAuthOptions({"username": "user-value", "password": "pass-value"})
    browser._driver = object()
    calls = []

    def fake_continue_with_auth(driver, request_id, action="default", credentials=None):
        calls.append(
            {
                "driver": driver,
                "request_id": request_id,
                "action": action,
                "credentials": credentials,
            }
        )

    monkeypatch.setattr(bidi_network, "continue_with_auth", fake_continue_with_auth)

    browser._on_proxy_auth_required(
        {
            "request": {"request": "request-1", "url": "https://example.com/"},
            "response": {"status": 407},
        }
    )

    assert calls == [
        {
            "driver": browser._driver,
            "request_id": "request-1",
            "action": "provideCredentials",
            "credentials": {
                "type": "password",
                "username": "user-value",
                "password": "pass-value",
            },
        }
    ]
