# ruyiPage

[简体中文](./README.md) | [English](./README_EN.md)

<p align="center">
  <img src="images/ruyipage.png" width="220" alt="ruyiPage logo" />
</p>

> A next-generation browser automation framework based on **Firefox + WebDriver BiDi**.  
> **No CDP dependency, no CDP exposure surface, and less targeted detection.**  
> **Native actions can preserve a large amount of `isTrusted` behavior, with built-in human-like interaction support for high-risk pages.**

[![PyPI version](https://img.shields.io/pypi/v/ruyiPage.svg)](https://pypi.org/project/ruyiPage/)
[![Python Versions](https://img.shields.io/pypi/pyversions/ruyiPage)](https://pypi.org/project/ruyiPage/)

Docs: <https://0xshoulderlab.site/automation>

---

## Project Positioning

`ruyiPage` is a Python library focused on **Firefox browser automation**. Its underlying protocol comes from:

- WebDriver BiDi: https://w3c.github.io/webdriver-bidi/

If you are not looking for just another CDP-based automation library, but instead care about:

- **A next-generation Firefox automation framework**
- **BiDi rather than CDP**
- **Native actions that preserve a large amount of `isTrusted` behavior**
- **Built-in human-like interaction capability for high-risk pages**
- **Support for interception, mocking, collectors, and network control**
- **A Firefox automation stack that is easier to maintain long-term**

then `ruyiPage` is built for that direction.

You can think of it as:

> A high-level automation framework for **Firefox**, with **WebDriver BiDi as the foundation** and beginner-friendly APIs on top.

It especially emphasizes four things:

- **No CDP dependency**, which naturally avoids the CDP exposure surface
- **Native action chains first**, to keep input, drag, click, and similar behavior closer to real `isTrusted` browser interaction
- **Built-in human-like interaction support**, better suited to high-risk pages
- **High-level APIs that are easy to get started with**, which also helps teams keep a consistent code style

---

## Capability Overview

Before diving into the details, this table gives a quick overview of what `ruyiPage` can already do.

| Category | High-level entry | Typical capabilities |
| --- | --- | --- |
| Page navigation | `page.get()` / `page.back()` / `page.forward()` | Open pages, refresh, navigate backward and forward |
| Element lookup | `page.ele()` / `page.eles()` / `ele.ele()` | CSS/XPath/Text locators, chained lookup inside containers |
| Element interaction | `ele.click_self()` / `ele.input()` / `ele.attr()` / `ele.text` | Click, type, get attributes, read text |
| Action chains | `page.actions` | Keyboard, mouse, drag, wheel, human-like actions |
| Touch input | `page.touch` | Tap, long press, and other touch actions |
| Cookies | `page.get_cookies()` / `page.set_cookies()` / `page.delete_cookies()` | Read, write, and delete cookies |
| Downloads | `page.downloads` | Set download directory, wait for download events, verify saved files |
| PDF / screenshots | `page.save_pdf()` / `page.screenshot()` | Print page to PDF and save screenshots |
| Prompt handling | `page.wait_prompt()` / `page.accept_prompt()` / `page.set_prompt_handler()` | `alert` / `confirm` / `prompt` |
| Navigation events | `page.navigation` | `navigationStarted`, `load`, `historyUpdated`, etc. |
| Generic events | `page.events` | `browsingContext` / `network` / `script` / `input` / `log` events |
| Network control | `page.network` / `page.intercept` | Headers, cache control, interception, mocking, fail, collector |
| Browsing contexts | `page.contexts` | `getTree`, create tab/window, reload, viewport |
| Browser-level tools | `page.browser_tools` | user contexts, client windows |
| Script capabilities | `page.get_realms()` / `page.eval_handle()` / `page.disown_handles()` | realms, remote handles, preload scripts |
| Emulation | `page.emulation` | UA, viewport, screen, orientation, JS toggle |
| WebExtension | `page.extensions` | Install unpacked extensions, install xpi, uninstall |
| Local storage | `page.local_storage` / `page.session_storage` | Read and write local/session storage |

---

## About Firefox Engine and the webdriver Flag

If your target site is highly sensitive to automation, there is one important point:

> **The Firefox browser needs the webdriver flag removed at the engine level.**

This is not something you can solve reliably with a simple page-level JavaScript override.

In other words:

- it is not enough to only change `navigator.webdriver`
- it is not enough to only inject a preload script for superficial masking
- you need a Firefox build where the webdriver exposure has already been handled in the **browser engine itself**

Only then can many site-level detections be bypassed more completely.

### Recommended Approach

You can use any Firefox build that already removes the webdriver exposure.

For example:

- https://github.com/LoseNine/firefox-fingerprintBrowser

The value of this kind of browser is:

- it handles webdriver exposure in Firefox internals
- it is not just front-end level masking
- it works better together with `ruyiPage` on stronger anti-bot sites

### Conclusion

The role of `ruyiPage` is:

- to provide high-level Firefox + BiDi automation capabilities
- to avoid the CDP detection surface

But if you are visiting high-risk sites, it is still recommended to:

1. use a Firefox build with webdriver already removed
2. then use `ruyiPage` to automate it

That combination is generally more stable.

---

## Why Firefox + BiDi

Many automation frameworks rely heavily on CDP (Chrome DevTools Protocol).

`ruyiPage` takes a different path:

- using **Firefox** as the core browser
- using **WebDriver BiDi** as the core protocol
- avoiding CDP entirely

That means:

- no traditional CDP automation exposure surface
- closer alignment with the W3C next-generation browser automation direction
- a better fit for Firefox-focused automation, input simulation, event listening, and network control

---

## How It Compares to Other Frameworks

The table below is not about declaring one framework absolutely better than another. It highlights a few points people usually care about most:

- which browser direction each framework mainly focuses on
- whether it depends on CDP
- how strong the CDP exposure surface is
- how good Firefox / BiDi support is
- how likely it is to be targeted by detection

| Framework | Main browser direction | Underlying protocol | CDP exposure surface | Firefox / BiDi support | Targeted detection |
| --- | --- | --- | --- | --- | --- |
| `ruyiPage` | **Firefox** | **WebDriver BiDi** | **No CDP exposure surface** | **High**, Firefox + BiDi is the main route | **Low**, native BiDi + `isTrusted` behavior + human-like actions fit high-risk scenarios better; even more stable with a Firefox build that removes webdriver at the engine level |
| Playwright | Chromium / Firefox / WebKit | Proprietary protocol, many features still lean toward Chromium | Medium to high | Medium, supports Firefox but is not primarily designed around Firefox BiDi | Medium to high, many sites target mainstream automation fingerprints first |
| Selenium | Multiple browsers | WebDriver Classic + partial BiDi | Low to medium | Medium, broad compatibility but weaker high-level BiDi capabilities | Medium, traditional automation traits are widespread |
| Puppeteer | Chromium | CDP | **High** | Low, not really focused on Firefox | **High**, CDP-based exposure is more obvious and more frequently targeted |
| DrissionPage | Chromium | Hybrid driver approach, still mainly Chromium-oriented | Medium to high | Low, Firefox is not the main focus | Medium to high, still easier to fall into mainstream Chromium detection paths |

### One-Line Recommendation

- If you mainly do **Firefox automation**: prefer `ruyiPage`
- If you need **unified multi-browser automation**: prefer Playwright / Selenium
- If you mainly work with **Chromium/CDP**: prefer Puppeteer / Playwright
- If you want **Firefox + no CDP dependency + high-level BiDi APIs**: `ruyiPage` is the more relevant option

---

## Real-World Showcase

The images below show real scenarios. To keep the GitHub homepage more compact, they are arranged in a two-column table.

<table>
  <tr>
    <td align="center"><b>Passes Cloudflare 5s challenge</b><br><img src="images/cloudfare.jpg" width="320" alt="Cloudflare 5s challenge" /></td>
    <td align="center"><b>Passes hCaptcha</b><br><img src="images/hcapture.jpg" width="320" alt="hCaptcha" /></td>
  </tr>
  <tr>
    <td align="center"><b>Passes DataDome</b><br><img src="images/datadome.jpg" width="320" alt="DataDome" /></td>
    <td align="center"><b>Directly enters Outlook Mail</b><br><img src="images/outlook.jpg" width="320" alt="Outlook Mail" /></td>
  </tr>
  <tr>
    <td align="center"><b>Directly enters Google Mail</b><br><img src="images/google.jpg" width="320" alt="Google Mail" /></td>
    <td align="center"><b>Fingerprint browser scan page</b><br><img src="images/fingerprint.png" width="320" alt="Fingerprint Browser Demo" /></td>
  </tr>
</table>

> These images demonstrate real-world capability along the Firefox route.
> If the target site is more heavily protected, it is still recommended to use a Firefox build that removes webdriver at the engine level.

---

## Installation

```bash
pip install ruyiPage --upgrade
```

If this is your first installation, the command above also works as the default way to install the latest version.

Verify the installed version after installation:

```bash
python -c "import ruyipage; print(ruyipage.__version__)"
```

---

## Basic Configuration

### 1. Simplest launch

```python
from ruyipage import FirefoxPage

page = FirefoxPage()
page.get("https://www.example.com")
print(page.title)
page.quit()
```

### 2. Specify Firefox path and userdir

```python
from ruyipage import FirefoxOptions, FirefoxPage

opts = FirefoxOptions()
opts.set_browser_path(r"D:\Firefox\firefox.exe")
opts.set_user_dir(r"D:\ruyipage_userdir")

page = FirefoxPage(opts)
page.get("https://www.example.com")
print(page.title)
page.quit()
```

### 3. Beginner-friendly `launch`

```python
from ruyipage import launch

page = launch(
    browser_path=r"D:\Firefox\firefox.exe",
    user_dir=r"D:\ruyipage_userdir",
    headless=False,
    port=9222,
)

page.get("https://www.example.com")
print(page.title)
page.quit()
```

### What `browser_path` and `user_dir` Mean

#### `browser_path`

The path to the Firefox executable.

Use it when:

- Firefox is not installed in the default location
- you have multiple Firefox versions
- you are using a portable Firefox build

#### `user_dir`

This is the Firefox profile / user directory.

Use it when:

- you want to reuse login state
- you want to keep cookies / local storage
- you want to reuse extensions, certificates, or preferences

If you do not set it:

- `ruyiPage` will create a temporary profile automatically
- this is suitable for one-off testing
- it is usually cleaned up after closing

---

## Root-Level Quick Start Examples

### 1. Bing Search Example

File: `quickstart_bing_search.py`

It will:

- open Bing
- type a keyword
- press Enter to search
- scrape the first 3 result pages
- print title, URL, and summary

Core pattern:

```python
from ruyipage import FirefoxOptions, FirefoxPage, Keys

opts = FirefoxOptions()
page = FirefoxPage(opts)

page.get("https://cn.bing.com/")
page.ele("#sb_form_q").input("小肩膀教育")
page.actions.press(Keys.ENTER).perform()

for item in page.eles("css:#b_results > li.b_algo"):
    title_ele = item.ele("css:h2 a")
    title = title_ele.text
    url = title_ele.attr("href")
```

### 2. Cloudflare / Copilot Example

File: `quickstart_cloudfare.py`

It will:

- open Copilot
- try to find the input box and send a prompt
- attempt to handle Cloudflare automatically
- print full cookies at the end

This example is especially useful for understanding:

- `page.handle_cloudflare_challenge()`
- `page.get_cookies(all_info=True)`
- how `FirefoxOptions` is written into a beginner-level script

### 3. Fingerprint Browser Example

File: `quickstart_fingerprint_browser.py`

It will:

- launch a Firefox fingerprint browser build with webdriver removed
- load the fingerprint file through `--fpfile=...`
- open `browserscan` to inspect fingerprint results
- combine geolocation, timezone, locale, request headers, and screen size emulation

Core pattern:

```python
from ruyipage import FirefoxOptions, FirefoxPage

opts = FirefoxOptions()
opts.set_browser_path(r"C:\Program Files\Mozilla Firefox\firefox.exe")
opts.set_fpfile(r"C:\fingerprints\profile1.txt")

page = FirefoxPage(opts)
page.get("https://www.browserscan.net/zh")

page.emulation.set_geolocation(39.9042, 116.4074, accuracy=100)
page.emulation.set_timezone("Asia/Tokyo")
page.emulation.set_locale(["ja-JP", "ja"])
page.network.set_extra_headers({
    "Accept-Language": "ja-JP,ja;q=0.9"
})
page.emulation.set_screen_size(1366, 768, device_pixel_ratio=2.0)
page.refresh()
```

Suitable for:

- combining a Firefox fingerprint browser with `ruyiPage`
- passing fingerprint files, locale, headers, and screen parameters together
- directly validating fingerprint output on sites such as `browserscan`

---

## Most Common API Guide

This is not a raw list of low-level BiDi commands. It focuses on the **high-level APIs most commonly used by beginners**.

Suggested reading order:

1. Start with `FirefoxPage`
   This is the core page object and almost all operations begin there.
2. Then read `ele()` / `eles()`
   Element lookup is one of the most fundamental capabilities.
3. Then read `actions / downloads / network / events`
   These are the most common advanced extensions in automation work.

Documentation style notes:

- this section prioritizes the **most useful and practical** high-level interfaces
- it does not dump raw BiDi commands and ask beginners to assemble parameters themselves
- each capability tries to explain:
  what it does,
  when to use it,
  the most common calling pattern,
  and what you can continue doing with the return value

---

## 1. Page Object: `FirefoxPage`

### Create a page

```python
from ruyipage import FirefoxPage, FirefoxOptions

page = FirefoxPage()

opts = FirefoxOptions()
page = FirefoxPage(opts)
```

### Common properties

| API | Description | Return value |
| --- | --- | --- |
| `page.title` | Current page title | `str` |
| `page.url` | Current page URL | `str` |
| `page.html` | Current page HTML | `str` |
| `page.tab_id` | Current tab browsingContext ID | `str` |
| `page.cookies` | Visible cookies on the current page | `list[CookieInfo]` |

### Common navigation

```python
page.get("https://www.example.com")
page.refresh()
page.back()
page.forward()
page.quit()
```

#### `page.get(url, wait='complete')`

Open a page.

```python
page.get("https://www.example.com")
page.get("https://www.example.com", wait="interactive")
```

Parameter notes:

- `url`
  - the target address to visit
  - common values: `https://...`, `file:///...`, `data:text/html,...`
- `wait`
  - page waiting strategy
  - common values:
    - `complete`: wait until the page fully loads
    - `interactive`: wait until `DOMContentLoaded`
    - `none`: return immediately after navigation is issued

Suitable use cases:

- routine navigation: use the default `complete`
- slow page but you only want the DOM first: use `interactive`
- you will listen for events or wait manually later: use `none`

#### `page.back()` / `page.forward()` / `page.refresh()`

These are used for:

- back
- forward
- refresh

```python
page.back()
page.forward()
page.refresh()
```

If you need to verify navigation events, use them together with `page.navigation`.

---

## 2. Element Lookup: `ele()` / `eles()`

### `page.ele(locator)`

Find a single element.

Most common patterns:

```python
page.ele("#kw")
page.ele("css:.item")
page.ele("css:div.card > a")
page.ele("xpath://button[text()='登录']")
page.ele("tag:input")
page.ele("text:登录")
```

Recommended priority for beginners:

1. `#id`
2. `css:...`
3. `xpath:...`

### `page.eles(locator)`

Find all matching elements.

```python
items = page.eles("css:.card")
rows = page.eles("css:table tbody tr")
links = page.eles("tag:a")
```

### Continue searching inside an element

```python
card = page.ele("css:.card")
title = card.ele("css:h2 a")
desc = card.ele("css:.desc")
```

### Common element APIs

| API | Description | Return value |
| --- | --- | --- |
| `ele.text` | Element text | `str` |
| `ele.html` | Element HTML | `str` |
| `ele.attr(name)` | Read an attribute | `str | None` |
| `ele.click_self()` | Click the element itself | `self` |
| `ele.input(text)` | Type into an input | `self` |
| `ele.clear()` | Clear input content | `self` |

#### `ele.click_self()`

Click the element directly.

```python
page.ele("text:登录").click_self()
```

This is the most recommended click method for beginners.

#### `ele.input(text, clear=True)`

Type content into an input.

```python
page.ele("#kw").input("ruyiPage")
page.ele("#kw").input("ruyiPage", clear=True)
```

Suitable for:

- text input
- search boxes
- file upload inputs

If the element is `<input type="file">`, you can pass a file path directly:

```python
page.ele("#upload").input(r"D:\test.txt")
page.ele("#upload").input([r"D:\1.txt", r"D:\2.txt"])
```

---

## 3. Action Chains: `page.actions`

Used for native BiDi input actions.

```python
page.actions.press(Keys.ENTER).perform()
page.actions.move_to(page.ele("#btn")).click().perform()
page.actions.drag(page.ele("#a"), page.ele("#b")).perform()
page.actions.release()
```

Common uses:

- keyboard input
- mouse clicks
- drag and drop
- wheel scrolling
- human-like movement and clicking

### Common patterns

#### Press Enter

```python
from ruyipage import Keys

page.actions.press(Keys.ENTER).perform()
```

#### Click an element

```python
page.actions.move_to(page.ele("#btn")).click().perform()
```

#### Drag and drop

```python
page.actions.drag(page.ele("#source"), page.ele("#target")).perform()
page.actions.release()
```

### Why `page.actions` is recommended

Because this chain is closer to the native BiDi input model, and many resulting events behave more like real browser input.

---

## 4. Cookies

### Get cookies

```python
cookies = page.get_cookies()
for cookie in cookies:
    print(cookie.name, cookie.value)
```

The returned objects are usually `CookieInfo`. Common fields include:

- `cookie.name`
- `cookie.value`
- `cookie.domain`
- `cookie.path`
- `cookie.http_only`
- `cookie.secure`
- `cookie.same_site`
- `cookie.expiry`

### Filter cookies

```python
cookies = page.get_cookies_filtered(name="session_id", all_info=True)
```

### Set cookies

```python
page.set_cookies({
    "name": "token",
    "value": "abc",
    "domain": "127.0.0.1",
    "path": "/",
})
```

You can also pass multiple cookies at once:

```python
page.set_cookies([
    {"name": "a", "value": "1", "domain": "127.0.0.1", "path": "/"},
    {"name": "b", "value": "2", "domain": "127.0.0.1", "path": "/"},
])
```

### Delete cookies

```python
page.delete_cookies(name="token")
page.delete_cookies()
```

---

## 5. Downloads

High-level entry: `page.downloads`

```python
page.downloads.set_behavior("allow", path=r"D:\downloads")
page.downloads.start()

page.ele("#download").click_self()

event = page.downloads.wait("browsingContext.downloadEnd", timeout=5)
print(event.status)
```

Common methods:

- `set_behavior()`
- `set_path()`
- `start()`
- `stop()`
- `wait()`
- `wait_chain()`
- `wait_file()`

### Typical download flow

```python
page.downloads.set_behavior("allow", path=r"D:\downloads")
page.downloads.start()

page.ele("#download").click_self()

begin = page.downloads.wait("browsingContext.downloadWillBegin", timeout=5)
end = page.downloads.wait("browsingContext.downloadEnd", timeout=5)

print(begin.suggested_filename)
print(end.status)
```

---

## 6. Navigation Events

High-level entry: `page.navigation`

```python
page.navigation.start()
page.get("https://www.example.com")

event = page.navigation.wait("browsingContext.load", timeout=5)
print(event.url)

page.navigation.stop()
```

Suitable for verifying:

- `navigationStarted`
- `domContentLoaded`
- `load`
- `historyUpdated`
- `navigationCommitted`

---

## 7. Generic Event Listening

High-level entry: `page.events`

```python
page.events.start(["network.beforeRequestSent"], contexts=[page.tab_id])
event = page.events.wait("network.beforeRequestSent", timeout=5)
page.events.stop()
```

Suitable for handling:

- `browsingContext.*`
- `network.*`
- `script.*`
- `input.*`
- `log.*`

Return type: `BidiEvent`

Common fields:

- `method`
- `context`
- `url`
- `request`
- `response`
- `error_text`
- `channel`
- `data`
- `multiple`
- `message`

### When to use `page.events`

When you want to listen to protocol-level events directly rather than only caring about the final page state, this is the right tool.

For example:

- listen for `network.beforeRequestSent`
- listen for `browsingContext.contextCreated`
- listen for `script.message`
- listen for `input.fileDialogOpened`

---

## 8. Network Capabilities

High-level entry: `page.network`

### Set extra headers

```python
page.network.set_extra_headers({"X-Test": "yes"})
```

This is commonly used to:

- add testing headers to requests
- mark environments
- verify whether headers are actually sent together with interception

### Set cache behavior

```python
page.network.set_cache_behavior("bypass")
```

Where:

- `default`: browser default caching policy; a cache hit may avoid a real request
- `bypass`: try to bypass cache and force a real request

### Data Collector

```python
collector = page.network.add_data_collector(
    ["responseCompleted"],
    data_types=["response"],
)

data = collector.get(request_id, data_type="response")
collector.disown(request_id, data_type="response")
collector.remove()
```

Where:

- `events`
  - `beforeRequestSent`: collect data when the request is sent
  - `responseCompleted`: collect data after the response completes
- `data_types`
  - `request`: collect request bodies
  - `response`: collect response bodies

---

## 9. Browsing Contexts

High-level entry: `page.contexts`

```python
tree = page.contexts.get_tree()
print(len(tree.contexts))

tab_id = page.contexts.create_tab()
page.contexts.close(tab_id)

page.contexts.reload()
page.contexts.set_viewport(800, 600)
```

Common methods:

- `get_tree()`
- `create_tab()`
- `create_window()`
- `close()`
- `reload()`
- `set_viewport()`
- `set_bypass_csp()`

### `tree = page.contexts.get_tree()`

The returned value is not a raw dict but a high-level result object.

```python
tree = page.contexts.get_tree()
print(len(tree.contexts))

first = tree.contexts[0]
print(first.context)
print(first.url)
```

---

## 10. Browser-Level Capabilities

High-level entry: `page.browser_tools`

```python
user_context = page.browser_tools.create_user_context()
contexts = page.browser_tools.get_user_contexts()
page.browser_tools.remove_user_context(user_context)

windows = page.browser_tools.get_client_windows()
page.browser_tools.set_window_state(windows[0]["clientWindow"], state="maximized")
```

Suitable for:

- user context management
- client window management

### Typical usage

```python
ctx = page.browser_tools.create_user_context()
tab_id = page.browser_tools.create_tab(user_context=ctx)
page.contexts.close(tab_id)
page.browser_tools.remove_user_context(ctx)
```

---

## 11. Script Capabilities

### Get realms

```python
realms = page.get_realms()
for realm in realms:
    print(realm.type, realm.context)
```

### Execute script and get a handle

```python
result = page.eval_handle("({a: 1, b: 2})")
print(result.success)
print(result.result.handle)

page.disown_handles([result.result.handle])
```

This flow is suitable when:

- you need a handle to a remote JS object
- you want to release the handle manually after using it

### Preload script

```python
preload = page.add_preload_script("""
() => {
    window.__ready = 'ok';
}
""")

page.get("https://www.example.com")
print(page.run_js("return window.__ready"))

page.remove_preload_script(preload)
```

Suitable for:

- injecting initialization logic before page scripts execute
- attaching hooks, markers, or helper functions before the page starts running

---

## 12. Prompts

High-level entry:

- `page.wait_prompt()`
- `page.accept_prompt()`
- `page.dismiss_prompt()`
- `page.input_prompt(text)`
- `page.set_prompt_handler(...)`
- `page.clear_prompt_handler()`

### Typical patterns

#### Wait first, then handle manually

```python
page.run_js("alert('hello')", as_expr=False)
prompt = page.wait_prompt(timeout=3)
page.accept_prompt()
```

#### Handle a prompt automatically

```python
page.set_prompt_handler(prompt="ignore", prompt_text="张三")
page.run_js("prompt('请输入姓名')", as_expr=False)
page.clear_prompt_handler()
```

```python
page.set_prompt_handler(prompt="ignore", prompt_text="张三")
page.run_js("prompt('请输入姓名')", as_expr=False)
page.clear_prompt_handler()
```

---

## 13. Emulation

High-level entry: `page.emulation`

```python
page.emulation.set_network_offline(True)
page.emulation.set_javascript_enabled(False)
page.emulation.set_scrollbar_type("overlay")
page.emulation.apply_mobile_preset(
    width=390,
    height=844,
    device_pixel_ratio=3,
    user_agent="...",
)
```

Notes:

- some emulation commands may not yet be implemented in the current Firefox version
- examples in this project distinguish between “success” and “unsupported”

### Typical usage

```python
page.emulation.apply_mobile_preset(
    width=390,
    height=844,
    device_pixel_ratio=3,
    user_agent="Mozilla/5.0 ...",
)
```

---

## 14. WebExtension

High-level entry: `page.extensions`

```python
ext_id = page.extensions.install_dir(r"D:\my_extension")
page.extensions.uninstall(ext_id)
```

Suitable for:

- verifying whether a content script is active
- testing unpacked extension and xpi installation flows

---

## 15. Representative Examples

The repository already includes many examples. It is recommended to learn them by number.

Suggested order:

### Beginner

- `01_basic_navigation.py`
- `02_element_finding.py`
- `03_element_interaction.py`
- `05_actions_chain.py`
- `06_screenshot.py`

### Page and script

- `07_javascript.py`
- `08_cookies.py`
- `09_tabs.py`
- `13_iframe.py`
- `14_shadow_dom.py`

### Advanced capabilities

- `17_user_prompts.py`
- `18_advanced_network.py`
- `19_pdf_printing.py`
- `20_advanced_input.py`
- `21_emulation.py`

### Strict-result examples

- `23_download.py`
- `24_navigation_events.py`
- `25_browser_user_context.py`
- `26_browsing_context_advanced.py`
- `27_emulation_advanced.py`
- `28_network_data_collector.py`
- `29_script_input_advanced.py`
- `30_browsing_context_events.py`
- `31_network_events.py`
- `32_script_events.py`
- `33_log_input_events.py`
- `34_remaining_commands.py`
- `35_native_bidi_drag.py`
- `36_native_bidi_select.py`

---

## Protocol Source

The core capabilities of `ruyiPage` are aligned with and based on:

- WebDriver BiDi: https://w3c.github.io/webdriver-bidi/

This is also the design source for many high-level APIs in this project, such as:

- `browsingContext.*`
- `network.*`
- `script.*`
- `input.*`
- `browser.*`
- `emulation.*`

---

## Star History

[![Star History Chart](https://api.star-history.com/chart?repos=LoseNine/ruyipage&type=date&legend=top-left)](https://www.star-history.com/?repos=LoseNine%2Fruyipage&type=date&legend=top-left)

---

## Final Notes

The core direction of `ruyiPage` is not to expose every low-level BiDi command in raw form, but to provide:

- a Firefox automation experience that feels friendly enough
- an interface that is intuitive enough for beginners
- structure that works well with editor navigation
- enough room for advanced users to access BiDi capabilities when needed

If you mainly use **Firefox** for automation, want to avoid the CDP detection surface as much as possible, and also want APIs that are easier to understand than writing raw protocol code directly, `ruyiPage` is built for that path.

---

## Usage Statement and Disclaimer

This project is intended only for:

- exploring next-generation automation frameworks
- learning Firefox automation capabilities
- learning the WebDriver BiDi protocol
- learning high-level browser automation API design
- lawful, compliant, non-commercial personal research and technical exchange

### Scope of Authorization

Anyone may use or distribute the source code of this project in a personal capacity, but only for:

- learning purposes
- technical research purposes
- lawful, compliant, non-commercial purposes

Without authorization from the copyright holder, individuals or organizations may not use this project, in source or binary form, for commercial activities.

### Terms of Use

Use of this project must comply with the following terms. If any term is violated during use, the authorization automatically becomes invalid.

- Do not apply `ruyiPage` to any project that may violate local laws or ethical constraints
- Do not use `ruyiPage` in any project that may harm the interests of others
- Do not use `ruyiPage` for attacks, harassment, bulk abuse, malicious registration, credential stuffing, traffic fraud, or similar behavior
- Do not use `ruyiPage` to bypass platform security mechanisms and then carry out illegal actions
- Users must comply with the target website or system's robots rules, terms of service, and local laws and regulations
- Do not use `ruyiPage` to collect data that is explicitly disallowed by law, terms, or robots policies

### Risk and Responsibility

All actions taken with `ruyiPage` are the sole responsibility of the user.

Any disputes or consequences arising from the use of `ruyiPage` are unrelated to the copyright holder.

The copyright holder is not responsible for any risks, losses, bans, restrictions, data issues, legal consequences, or indirect damages resulting from the use of `ruyiPage`.

The copyright holder is also not responsible for any losses caused by possible defects in `ruyiPage`, compatibility issues, misuse risks, or strategy changes on target websites.

### Special Note

This project emphasizes:

- Firefox automation
- BiDi protocol capability
- `isTrusted` behavior
- human-like interaction capability
- adaptation to high-risk scenarios

But these capabilities are limited to **lawful, compliant, and legitimate** technical research and automation scenarios.

---

## Buy Me a Coffee

If this project helps you, you are welcome to buy me a coffee and support continued work on `ruyiPage`.

<table>
  <tr>
    <td align="center">
      <b>Official Account</b><br>
      <img src="images/gzh.jpg" width="220" alt="Official account QR code" />
    </td>
    <td align="center">
      <b>QQ Group</b><br>
      <img src="images/qq.jpg" width="220" alt="QQ group QR code" />
    </td>
    <td align="center">
      <b>Contact Me / WeChat</b><br>
      <img src="images/weixin.jpg" width="220" alt="Personal WeChat QR code" />
    </td>
    <td align="center">
      <b>Buy Me a Coffee</b><br>
      <img src="images/weixingoot.jpg" width="220" alt="Donation QR code" />
    </td>
  </tr>
</table>
