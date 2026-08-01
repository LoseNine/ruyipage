# Action Visual Preload Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a harmless BiDi preload active for every browser session while `action_visual` controls only the visual preload, globals, and DOM.

**Architecture:** `Firefox` owns an idempotent, session-keyed baseline preload protected by a lock. Session activation and context initialization both call the same helper, so reconnects create a fresh preload and transient failures get another retry point. `FirefoxBase` retains the existing visual option guard and a separate visual script ID.

**Tech Stack:** Python 3, WebDriver BiDi, pytest, `unittest.mock`, `concurrent.futures`

---

### Task 1: Add the browser-owned baseline preload registry

**Files:**
- Create: `tests/test_action_visual_preload.py`
- Modify: `ruyipage/_base/browser.py:14-28`
- Modify: `ruyipage/_base/browser.py:639-650`

- [ ] **Step 1: Write the failing registry tests**

Create `tests/test_action_visual_preload.py` with the following baseline tests and helpers:

```python
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from ruyipage._base.browser import Firefox


class RecordingDriver:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [{"script": "baseline-1"}])
        self.lock = threading.Lock()

    def run(self, method, params=None, **kwargs):
        with self.lock:
            self.calls.append((method, params, kwargs))
            response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_browser(session_id="session-1", responses=None):
    browser = object.__new__(Firefox)
    browser._driver = RecordingDriver(responses)
    browser._session_id = session_id
    browser._baseline_preload_script_id = None
    browser._baseline_preload_session_id = None
    browser._baseline_preload_lock = threading.Lock()
    return browser


def test_baseline_preload_registers_once_for_one_session():
    browser = make_browser()

    assert browser._ensure_baseline_preload() is True
    assert browser._ensure_baseline_preload() is True

    assert browser._baseline_preload_script_id == "baseline-1"
    assert browser._baseline_preload_session_id == "session-1"
    assert len(browser._driver.calls) == 1
    method, params, _kwargs = browser._driver.calls[0]
    assert method == "script.addPreloadScript"
    assert params == {"functionDeclaration": "() => {}"}


def test_baseline_preload_registers_again_for_a_new_session():
    browser = make_browser(
        responses=[{"script": "baseline-1"}, {"script": "baseline-2"}]
    )
    assert browser._ensure_baseline_preload() is True

    browser._session_id = "session-2"

    assert browser._ensure_baseline_preload() is True
    assert browser._baseline_preload_script_id == "baseline-2"
    assert browser._baseline_preload_session_id == "session-2"
    assert len(browser._driver.calls) == 2


def test_baseline_preload_is_idempotent_under_concurrent_calls():
    browser = make_browser()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _item: browser._ensure_baseline_preload(), range(16)))

    assert results == [True] * 16
    assert len(browser._driver.calls) == 1


def test_baseline_preload_retries_an_empty_script_id():
    browser = make_browser(responses=[{}, {"script": "baseline-2"}])

    assert browser._ensure_baseline_preload() is True
    assert browser._baseline_preload_script_id == "baseline-2"
    assert len(browser._driver.calls) == 2


def test_baseline_preload_warns_and_keeps_failure_retryable(caplog):
    browser = make_browser(
        responses=[RuntimeError("first"), RuntimeError("second")]
    )

    with caplog.at_level(logging.WARNING, logger="ruyipage"):
        assert browser._ensure_baseline_preload() is False

    assert browser._baseline_preload_script_id is None
    assert browser._baseline_preload_session_id is None
    assert "baseline preload" in caplog.text
    assert len(browser._driver.calls) == 2
```

- [ ] **Step 2: Run the registry tests and verify RED**

Run:

```powershell
python -m pytest tests/test_action_visual_preload.py -q -p no:cacheprovider
```

Expected: five failures reporting that `Firefox` has no `_ensure_baseline_preload` method.

- [ ] **Step 3: Implement the minimal registry**

In `ruyipage/_base/browser.py`, import the BiDi script module and add the constants:

```python
from .._bidi import script as bidi_script

_BASELINE_PRELOAD_SCRIPT = "() => {}"
_BASELINE_PRELOAD_ATTEMPTS = 2
```

Add this method beside `get_context_nav_lock()`:

```python
def _ensure_baseline_preload(self):
    """Ensure the current BiDi session has the neutral baseline preload."""
    session_id = self._session_id
    if not self._driver or not session_id:
        logger.warning("BiDi baseline preload skipped: active session unavailable")
        return False

    with self._baseline_preload_lock:
        if (
            self._baseline_preload_script_id
            and self._baseline_preload_session_id == session_id
        ):
            return True

        failures = []
        for _attempt in range(_BASELINE_PRELOAD_ATTEMPTS):
            try:
                result = bidi_script.add_preload_script(
                    self._driver,
                    _BASELINE_PRELOAD_SCRIPT,
                )
                script_id = result.get("script", "")
                if script_id:
                    self._baseline_preload_script_id = script_id
                    self._baseline_preload_session_id = session_id
                    return True
                failures.append("empty script id")
            except Exception as exc:
                failures.append(str(exc))

        self._baseline_preload_script_id = None
        self._baseline_preload_session_id = None
        logger.warning(
            "BiDi baseline preload registration failed after %d attempts: %s",
            _BASELINE_PRELOAD_ATTEMPTS,
            "; ".join(failures),
        )
        return False
```

- [ ] **Step 4: Run the registry tests and verify GREEN**

Run the same pytest command. Expected: `5 passed`.

- [ ] **Step 5: Commit the registry**

```powershell
git add -- tests/test_action_visual_preload.py ruyipage/_base/browser.py
git commit -m "fix: add session baseline preload registry"
```

### Task 2: Integrate the baseline with every session lifecycle

**Files:**
- Modify: `tests/test_action_visual_preload.py`
- Modify: `ruyipage/_base/browser.py:205-232`
- Modify: `ruyipage/_base/browser.py:594-638`
- Modify: `ruyipage/_base/browser.py:1607-1710`
- Modify: `ruyipage/_pages/firefox_base.py:156-170`

- [ ] **Step 1: Add failing session and context lifecycle tests**

Add these imports to `tests/test_action_visual_preload.py`:

```python
from types import SimpleNamespace
from unittest import mock

from ruyipage._base.browser import Firefox, create_browser_from_probe_info
from ruyipage._configs.firefox_options import FirefoxOptions
from ruyipage._pages.firefox_base import FirefoxBase
```

Replace the earlier `Firefox` import with the combined browser import above,
then append:

```python
def test_activate_session_records_session_and_ensures_baseline():
    browser = make_browser()
    browser._driver.session_id = ""
    browser._owns_session = False
    browser._ensure_baseline_preload = mock.Mock(return_value=True)

    browser._activate_session({"sessionId": "session-2"})

    assert browser._session_id == "session-2"
    assert browser._driver.session_id == "session-2"
    assert browser._owns_session is True
    browser._ensure_baseline_preload.assert_called_once_with()


def test_firefox_init_creates_baseline_registry_state(monkeypatch):
    browser = object.__new__(Firefox)
    browser._initialized = False
    browser._init_lock = threading.Lock()
    monkeypatch.setattr(Firefox, "_connect_or_launch", lambda self: None)
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)
    options = FirefoxOptions().set_port(65520)

    Firefox.__init__(browser, options)

    try:
        assert browser._baseline_preload_script_id is None
        assert browser._baseline_preload_session_id is None
        assert isinstance(browser._baseline_preload_lock, type(threading.Lock()))
    finally:
        Firefox._BROWSERS.pop(browser._address, None)


def test_retained_probe_browser_registers_baseline(monkeypatch):
    address = "127.0.0.1:65521"
    driver = RecordingDriver([{"script": "probe-baseline"}])
    monkeypatch.setattr(Firefox, "_register_exit_cleanup", lambda self: None)

    browser = create_browser_from_probe_info(
        {
            "address": address,
            "driver": driver,
            "session_id": "probe-session",
            "session_owned": True,
            "contexts": [],
        }
    )

    try:
        assert browser._baseline_preload_script_id == "probe-baseline"
        assert browser._baseline_preload_session_id == "probe-session"
        assert len(driver.calls) == 1
    finally:
        Firefox._BROWSERS.pop(address, None)


def test_context_init_retries_browser_owned_baseline():
    driver = RecordingDriver()
    fake_browser = SimpleNamespace(
        driver=driver,
        options=SimpleNamespace(
            load_mode="normal",
            xpath_picker_enabled=False,
            action_visual_enabled=False,
            trace_enabled=False,
            failure_snapshot_enabled=False,
        ),
        _ensure_baseline_preload=mock.Mock(return_value=True),
    )
    page = FirefoxBase()

    page._init_context(fake_browser, "context-1")

    fake_browser._ensure_baseline_preload.assert_called_once_with()
```

- [ ] **Step 2: Run the lifecycle tests and verify RED**

Run:

```powershell
python -m pytest tests/test_action_visual_preload.py -q -p no:cacheprovider
```

Expected: failures for missing `_activate_session`, missing initialization fields, and missing context retry invocation.

- [ ] **Step 3: Add one common session activation hook**

Initialize these fields on `self` in `Firefox.__init__()`:

```python
self._baseline_preload_script_id = None
self._baseline_preload_session_id = None
self._baseline_preload_lock = threading.Lock()
```

Initialize the equivalent `browser._baseline_preload_*` fields in
`create_browser_from_probe_info()` before calling the registry helper.

Add the common hook:

```python
def _activate_session(self, result):
    self._session_id = result.get("sessionId", "")
    self._driver.session_id = self._session_id
    self._owns_session = True
    self._ensure_baseline_preload()
```

Replace each repeated successful `session.new` assignment block in
`_create_session()` with `self._activate_session(result)`. After constructing a
browser from retained probe information, call
`browser._ensure_baseline_preload()` before returning it.

At the start of `FirefoxBase._init_context()`, after assigning the browser and
context driver, add:

```python
ensure_baseline = getattr(browser, "_ensure_baseline_preload", None)
if callable(ensure_baseline):
    ensure_baseline()
```

This remains compatible with lightweight test doubles while real `Firefox`
instances always use the browser-owned helper.

- [ ] **Step 4: Run lifecycle and protocol tests**

```powershell
python -m pytest tests/test_action_visual_preload.py tests/test_bidi_protocol_conformance.py -q -p no:cacheprovider
```

Expected: all tests pass and the protocol test still observes the standard
`script.addPreloadScript` payload.

- [ ] **Step 5: Commit lifecycle integration**

```powershell
git add -- tests/test_action_visual_preload.py ruyipage/_base/browser.py ruyipage/_pages/firefox_base.py
git commit -m "fix: bind baseline preload to BiDi sessions"
```

### Task 3: Lock the visualization option to visual behavior

**Files:**
- Modify: `tests/test_action_visual_preload.py`
- Modify: `ruyipage/_configs/firefox_options.py:919-935`

- [ ] **Step 1: Add visual separation characterization tests**

Append this helper and the two characterization tests to
`tests/test_action_visual_preload.py`:

```python
def make_visual_page(enabled):
    driver = RecordingDriver([{"script": "visual-preload"}])
    browser = SimpleNamespace(
        options=SimpleNamespace(action_visual_enabled=enabled),
        _baseline_preload_script_id="baseline-preload",
        _baseline_preload_session_id="session-1",
    )
    page = FirefoxBase()
    page._browser = browser
    page._driver = SimpleNamespace(_browser_driver=driver)
    page.run_js = mock.Mock()
    return page, browser, driver


def test_disabled_action_visual_has_no_visual_preload_or_evaluation():
    page, browser, driver = make_visual_page(False)

    page._maybe_enable_action_visual()

    assert driver.calls == []
    assert not hasattr(browser, "_action_visual_global_preload_script_id")
    page.run_js.assert_not_called()


def test_enabled_action_visual_keeps_visual_preload_separate():
    page, browser, driver = make_visual_page(True)
    visual_script = page._get_action_visual_script()

    page._maybe_enable_action_visual()

    assert browser._baseline_preload_script_id == "baseline-preload"
    assert browser._action_visual_global_preload_script_id == "visual-preload"
    assert browser._action_visual_global_preload_script_id != browser._baseline_preload_script_id
    method, params, _kwargs = driver.calls[0]
    assert method == "script.addPreloadScript"
    assert params == {"functionDeclaration": visual_script}
    page.run_js.assert_called_once_with(f"({visual_script})()", as_expr=True)
```

- [ ] **Step 2: Run the focused tests**

```powershell
python -m pytest tests/test_action_visual_preload.py -q -p no:cacheprovider
```

Expected before documentation changes: the behavior tests pass as
characterization coverage. They protect the separation while Task 2 changes
context initialization.

- [ ] **Step 3: Document startup-only option semantics**

Extend `FirefoxOptions.enable_action_visual()` with:

```python
说明：
    - 这是浏览器启动配置，应在创建 ``FirefoxPage`` 前设置。
    - 启用后页面上会显示实时鼠标坐标指示器。
```

Keep the existing visual behavior bullets unchanged after the new startup
contract.

- [ ] **Step 4: Run focused and fast regression suites**

```powershell
python -m pytest tests/test_action_visual_preload.py tests/test_bidi_protocol_conformance.py -q -p no:cacheprovider
python -m pytest -m fast -q -p no:cacheprovider
```

Expected: all selected tests pass with no new warnings.

- [ ] **Step 5: Commit the option contract and tests**

```powershell
git add -- tests/test_action_visual_preload.py ruyipage/_configs/firefox_options.py
git commit -m "test: lock action visual preload separation"
```

### Task 4: Verify real rendering and Fingerprint Pro A/B behavior

**Files:**
- No committed file changes

- [ ] **Step 1: Run existing browser-backed visual tests**

```powershell
$env:RUYIPAGE_TEST_FIREFOX_PATH='C:\Program Files\Mozilla Firefox\firefox.exe'
python -m pytest tests/features/test_action_visual_resize.py tests/features/test_human_move_bounds.py -q -p no:cacheprovider
```

Expected: all tests pass and action visual still renders after navigation and
resize.

- [ ] **Step 2: Run live three-way verification**

Run:

```powershell
$env:PYTHONIOENCODING='utf-8'
@'
import json
import shutil
import tempfile
import time

from ruyipage import launch
from ruyipage._bidi import script as bidi_script


URL = "https://demo.fingerprint.com/playground?_gl=1*1a66mte*_gcl_au*MjYyMjg1NTU0LjE3NzQ1MjAwNTY."
XPATH = "/html/body/div[2]/div/div[3]/div/div[2]/div/table/tbody/tr[6]/td[2]"
BROWSER = r"C:\Program Files\Mozilla Firefox\firefox.exe"
variants = [
    ("negative_control", False, True),
    ("action_visual_false", False, False),
    ("action_visual_true", True, False),
]
results = []

for name, visual, remove_baseline in variants:
    profile = tempfile.mkdtemp(prefix="ruyi_action_visual_verify_")
    page = None
    try:
        page = launch(
            headless=True,
            action_visual=visual,
            browser_path=BROWSER,
            user_dir=profile,
            window_size=(1280, 800),
            timeout_page_load=45,
        )
        if remove_baseline:
            bidi_script.remove_preload_script(
                page.browser.driver,
                page.browser._baseline_preload_script_id,
            )

        page.get(URL)
        data = {}
        for _attempt in range(45):
            data = page.run_js(
                r'''(xpath) => {
                    const cell = document.evaluate(
                        xpath, document, null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE, null
                    ).singleNodeValue;
                    const row = cell && cell.closest('tr');
                    const rows = Array.from(document.querySelectorAll('table tbody tr'));
                    const bot = rows.find(item => item.cells[0] && item.cells[0].innerText.trim() === 'Bot');
                    return {
                        label: row && row.cells[0] ? row.cells[0].innerText.trim() : null,
                        devtools: cell ? cell.innerText.trim() : null,
                        bot: bot && bot.cells[1] ? bot.cells[1].innerText.trim() : null,
                        visual: !!window.__ruyiAV,
                        visualNodes: document.querySelectorAll('[id^="__ruyi_av_"]').length,
                    };
                }''',
                XPATH,
            )
            if data.get("devtools") and data.get("bot"):
                break
            time.sleep(1)

        assert data["label"] == "Developer Tools", (name, data)
        assert data["bot"] == "Not detected", (name, data)
        if name == "negative_control":
            assert data["devtools"].startswith("Yes"), (name, data)
            assert data["visual"] is False
            assert data["visualNodes"] == 0
        else:
            assert data["devtools"] == "Not detected", (name, data)
            assert data["visual"] is visual
            assert (data["visualNodes"] > 0) is visual
        results.append({"variant": name, **data})
    finally:
        if page:
            try:
                page.quit(force=True)
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)

print(json.dumps(results, ensure_ascii=False, indent=2))
'@ | python -
```

This launches fresh profiles for:

1. `action_visual=False` with the baseline removed before navigation (negative control);
2. `action_visual=False` with the baseline active;
3. `action_visual=True` with both baseline and visual preloads active.

For each run, load the supplied Fingerprint Pro URL and read the smart-signal
table by label. Assert the row containing the supplied XPath is labeled
`Developer Tools`. Expected results:

```text
negative control: Developer Tools = Yes
action_visual=False: Developer Tools = Not detected; Bot = Not detected; __ruyiAV absent
action_visual=True:  Developer Tools = Not detected; Bot = Not detected; __ruyiAV present
```

- [ ] **Step 3: Run final diff and repository checks**

```powershell
git diff --check
git status --short
git log -5 --oneline
```

Confirm only task-owned files and the user's pre-existing changes are present.

- [ ] **Step 4: Request final independent code review**

Review the implementation range against
`docs/superpowers/specs/2026-08-01-action-visual-preload-separation-design.md`,
fix all Critical and Important findings, then repeat the focused and live
verification commands.
