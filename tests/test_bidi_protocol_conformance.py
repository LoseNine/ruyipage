# -*- coding: utf-8 -*-

import pytest

from ruyipage._bidi import browser_module, browsing_context, emulation, network, script, session


class DummyDriver:
    def __init__(self):
        self.calls = []

    def run(self, method, params=None, **kwargs):
        self.calls.append((method, params, kwargs))
        return {"ok": True}


def test_create_user_context_sends_latest_optional_parameters():
    driver = DummyDriver()
    browser_module.create_user_context(driver, accept_insecure_certs=True, proxy={"proxyType": "manual", "httpProxy": "proxy.test"}, unhandled_prompt_behavior={"default": "dismiss"})
    assert driver.calls[-1] == ("browser.createUserContext", {"acceptInsecureCerts": True, "proxy": {"proxyType": "manual", "httpProxy": "proxy.test"}, "unhandledPromptBehavior": {"default": "dismiss"}}, {})


def test_subscribe_supports_user_contexts():
    driver = DummyDriver()
    session.subscribe(driver, ["network.beforeRequestSent"], user_contexts=["uc-1"])
    assert driver.calls[-1][1] == {"events": ["network.beforeRequestSent"], "userContexts": ["uc-1"]}


def test_unsubscribe_attributes_do_not_send_nonstandard_contexts():
    driver = DummyDriver()
    session.unsubscribe(driver, events=["network.beforeRequestSent"], contexts=["ctx-1"])
    assert driver.calls[-1][1] == {"events": ["network.beforeRequestSent"]}


def test_set_bypass_csp_uses_standard_parameter_and_scope():
    driver = DummyDriver()
    browsing_context.set_bypass_csp(driver, contexts=["ctx-1"], bypass=True)
    assert driver.calls[-1][1] == {"bypass": True, "contexts": ["ctx-1"]}


def test_start_screencast_flattens_media_options():
    driver = DummyDriver()
    browsing_context.start_screencast(driver, "ctx-1", mime_type="video/webm", video={"width": 1280, "height": 720, "frameRate": 30}, audio=False)
    assert driver.calls[-1][1] == {"context": "ctx-1", "mimeType": "video/webm", "video": {"width": 1280, "height": 720, "frameRate": 30}, "audio": False}


def test_emulation_fields_match_latest_schema():
    driver = DummyDriver()
    emulation.set_user_agent_override(driver, "UA", platform="ignored")
    assert "platform" not in driver.calls[-1][1]
    emulation.set_screen_orientation_override(driver, "landscape-primary", angle=90)
    assert driver.calls[-1][1] == {"screenOrientation": {"type": "landscape-primary", "natural": "landscape"}}
    emulation.set_network_conditions(driver, offline=False)
    assert driver.calls[-1][1] == {"networkConditions": None}
    emulation.set_scripting_enabled(driver, enabled=True)
    assert driver.calls[-1][1] == {"enabled": None}
    emulation.set_scrollbar_type_override(driver, "overlay")
    assert driver.calls[-1][1] == {"scrollbarType": "overlay"}
    emulation.set_forced_colors_mode_theme_override(driver, "none")
    assert driver.calls[-1][1] == {"theme": None}


def test_geolocation_override_forwards_complete_coordinates():
    driver = DummyDriver()

    emulation.set_geolocation_override(
        driver,
        latitude=40.7128,
        longitude=-74.006,
        accuracy=25,
        altitude=12.5,
        altitude_accuracy=3.5,
        heading=45,
        speed=2.25,
        contexts=["ctx-1"],
    )

    assert driver.calls[-1][1] == {
        "coordinates": {
            "latitude": 40.7128,
            "longitude": -74.006,
            "accuracy": 25,
            "altitude": 12.5,
            "altitudeAccuracy": 3.5,
            "heading": 45,
            "speed": 2.25,
        },
        "contexts": ["ctx-1"],
    }


def test_viewport_meta_override_exists_and_supports_scope():
    driver = DummyDriver()
    emulation.set_viewport_meta_override(driver, viewport_meta=True, user_contexts=["uc-1"])
    assert driver.calls[-1] == ("emulation.setViewportMetaOverride", {"viewportMeta": True, "userContexts": ["uc-1"]}, {})


def test_data_collector_uses_latest_parameters():
    driver = DummyDriver()
    network.add_data_collector(driver, events=["responseCompleted"], data_types=["response"], user_contexts=["uc-1"])
    assert driver.calls[-1][1] == {"dataTypes": ["response"], "maxEncodedDataSize": 10485760, "collectorType": "blob", "userContexts": ["uc-1"]}


def test_get_data_allows_omitting_collector_and_disowning():
    driver = DummyDriver()
    network.get_data(driver, None, "req-1", data_type="response", disown=True)
    assert driver.calls[-1][1] == {"request": "req-1", "dataType": "response", "disown": True}


def test_add_preload_script_supports_user_contexts():
    driver = DummyDriver()
    script.add_preload_script(driver, "() => 1", user_contexts=["uc-1"])
    assert driver.calls[-1][1] == {"functionDeclaration": "() => 1", "userContexts": ["uc-1"]}


def test_download_behavior_requires_folder_for_allowed_behavior(tmp_path):
    driver = DummyDriver()
    with pytest.raises(ValueError, match="download_path"):
        browser_module.set_download_behavior(driver, behavior="allow")
    browser_module.set_download_behavior(driver, behavior=None)
    assert driver.calls[-1][1] == {"downloadBehavior": None}
