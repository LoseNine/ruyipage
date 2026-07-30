import re

from ruyipage._configs.firefox_options import FirefoxOptions


HIGH_DENSITY_PREFS = {
    "dom.ipc.processPrelaunch.enabled": "false",
    "dom.ipc.keepProcessesAlive.privilegedabout": "0",
    "browser.sessionstore.resume_from_crash": "false",
    "accessibility.force_disabled": "1",
}


def _written_prefs(profile):
    content = (profile / "user.js").read_text(encoding="utf-8")
    return dict(re.findall(r'user_pref\("([^"]+)", (.+)\);', content))


def test_high_density_mode_is_opt_in_and_only_adds_targeted_prefs(tmp_path):
    default_profile = tmp_path / "default"
    dense_profile = tmp_path / "dense"
    default_profile.mkdir()
    dense_profile.mkdir()

    FirefoxOptions().set_profile(str(default_profile)).write_prefs_to_profile()
    options = FirefoxOptions().set_profile(str(dense_profile))
    returned = options.enable_high_density_mode()
    options.write_prefs_to_profile()

    default_prefs = _written_prefs(default_profile)
    dense_prefs = _written_prefs(dense_profile)

    assert returned is options
    assert options.high_density_mode_enabled is True
    assert not HIGH_DENSITY_PREFS.keys() & default_prefs.keys()
    assert {key: dense_prefs[key] for key in HIGH_DENSITY_PREFS} == HIGH_DENSITY_PREFS
    assert set(dense_prefs) - set(default_prefs) == set(HIGH_DENSITY_PREFS)


def test_explicit_prefs_override_high_density_defaults(tmp_path):
    options = FirefoxOptions().set_profile(str(tmp_path)).enable_high_density_mode()
    options.set_pref("dom.ipc.processPrelaunch.enabled", True)
    options.set_pref("dom.ipc.keepProcessesAlive.privilegedabout", 3)
    options.set_pref("browser.sessionstore.resume_from_crash", True)
    options.set_pref("accessibility.force_disabled", 0)

    options.write_prefs_to_profile()

    prefs = _written_prefs(tmp_path)
    assert prefs["dom.ipc.processPrelaunch.enabled"] == "true"
    assert prefs["dom.ipc.keepProcessesAlive.privilegedabout"] == "3"
    assert prefs["browser.sessionstore.resume_from_crash"] == "true"
    assert prefs["accessibility.force_disabled"] == "0"


def test_high_density_mode_does_not_change_command_line_or_marionette():
    default_command = FirefoxOptions().build_command()
    dense_options = FirefoxOptions().enable_high_density_mode()
    dense_without_marionette = (
        FirefoxOptions().enable_high_density_mode().enable_marionette(False)
    )

    assert dense_options.build_command() == default_command
    assert "--marionette" in dense_options.build_command()
    assert "--marionette" not in dense_without_marionette.build_command()
    assert dense_without_marionette.high_density_mode_enabled is True
