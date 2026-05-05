# -*- coding: utf-8 -*-

from pathlib import Path

from ruyipage._configs.firefox_options import FirefoxOptions


def _read_text(path):
    return Path(path).read_text(encoding="utf-8")


class TestFirefoxOptionsMarionette:
    def test_marionette_enabled_by_default(self):
        opts = FirefoxOptions()

        assert opts.marionette_enabled is True
        assert "--marionette" in opts.build_command()

    def test_enable_marionette_can_disable_launch_flag(self):
        opts = FirefoxOptions().enable_marionette(False)

        assert opts.marionette_enabled is False
        assert "--marionette" not in opts.build_command()

    def test_write_prefs_skips_marionette_when_disabled(self, tmp_path):
        opts = FirefoxOptions().set_profile(str(tmp_path)).enable_marionette(False)

        opts.write_prefs_to_profile()

        content = _read_text(tmp_path / "user.js")
        assert "marionette.enabled" not in content

    def test_write_prefs_keeps_marionette_when_enabled(self, tmp_path):
        opts = FirefoxOptions().set_profile(str(tmp_path)).enable_marionette(True)

        opts.write_prefs_to_profile()

        content = _read_text(tmp_path / "user.js")
        assert 'user_pref("marionette.enabled", true);' in content

    def test_quick_start_can_disable_marionette(self):
        opts = FirefoxOptions().quick_start(marionette=False)

        assert opts.marionette_enabled is False
        assert "--marionette" not in opts.build_command()
