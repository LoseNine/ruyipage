# fpfile Fingerprint Reference

`--fpfile=<path>` is the only configuration entry point of this custom Firefox
kernel. One file carries the browser fingerprint, the network proxy, and the
auth credentials.

This document describes the current implementation only. Never ship real
proxies, accounts, passwords, or egress IPs inside it.

> Chinese version: [`fpfile指纹说明.md`](fpfile%E6%8C%87%E7%BA%B9%E8%AF%B4%E6%98%8E.md)

## Relationship with ruyiPage

Most callers **never write this file by hand**. ruyiPage's smart fingerprint API
generates it:

```python
opts = FirefoxOptions()
opts.set_browser_path(r"C:/path/to/firefox.exe")
ctx = opts.smart_fingerprint(proxy_host="...", proxy_port=8080, require_country="US")
page = FirefoxPage(opts)
ctx.apply_emulation(page)
```

`smart_fingerprint()` probes the egress IP, aligns locale / timezone / voices,
picks one of 22 real-machine hardware profiles, writes a self-consistent
`fpfile.txt`, and passes it to the kernel through `--fpfile=` (see the "Smart
Fingerprint API" section of the main README).

**When you do need this document and hand-editing:**

- pinning one specific target machine (your own WebGL / screen / font dump)
  instead of the 22 bundled profiles;
- tuning fields the smart flow does not cover (WebGPU, voice lists, geolocation
  details, and so on);
- diagnosing why a given detection point is not neutralised — every item below
  has its key and its verification method;
- using the kernel without ruyiPage, where this is the only configuration
  reference.

When hand-writing, copy the whole template in §3, work through the required-key
checklist in §6, then run the self-check in §7.

## Contents

- [1. Launching](#1-launching)
- [2. Syntax rules](#2-syntax-rules)
- [3. Fingerprint template](#3-fingerprint-template)
- [4. Field reference](#4-field-reference) — the ten detection categories
  - [4.1 Automation / WebDriver detection (no configuration)](#41-automation--webdriver-detection--no-configuration)
  - [4.2 Hardware / device](#42-hardware--device)
  - [4.3 Canvas](#43-canvas)
  - [4.4 WebGL](#44-webgl)
  - [4.5 Audio](#45-audio)
  - [4.6 Fonts](#46-fonts)
  - [4.7 Navigator consistency](#47-navigator-consistency)
  - [4.8 WebRTC / media / permissions / sensors](#48-webrtc--media--permissions--sensors)
  - [4.9 Timezone / language](#49-timezone--language)
  - [4.10 Anti-hook / prototype probing (no configuration)](#410-anti-hook--prototype-probing--no-configuration)
- [5. Network and auth](#5-network-and-auth)
- [6. Required keys](#6-required-keys)
- [7. Self-check](#7-self-check)
- [8. Known limits](#8-known-limits)

---

## 1. Launching

The `=` form is mandatory:

```bat
firefox.exe --new-instance -no-remote -profile C:\path\profile --fpfile=C:\path\fingerprint.fp "https://example.com/"
```

Do not use the space-separated form (`--fpfile C:\path\...`). The value is read
through `base::CommandLine::GetSwitchValue("fpfile")`, and on some paths the
space form yields nothing — the usual symptom is SOCKS5 auth going out without
a username or password.

A line reading `unrecognized command line flag "-fpfile"` appears in the console
at startup. **That is expected.**

`--fpfile` is forwarded to every child process automatically and added to the
sandbox read allowlist.

---

## 2. Syntax rules

- Both `:` and `=` separate a key from its value, and the **first occurrence**
  wins. A colon inside a UA string is therefore not mistaken for a separator.
- Whitespace around the key and the value is stripped.
- A line starting with `#` or `//` is a comment. Blank lines are ignored.
- If a key appears more than once, **the last one wins**.
- Keys match **exactly**. `width` does not match `widthFoo`, and
  `speech.voices.local` does not match `speech.voices.local.langs`.
- An empty value is the same as not setting the key at all.
- A numeric value that fails to parse (empty, non-numeric, negative, overflow)
  is treated as **absent** and falls back to the real machine value. It never
  crashes.

**Falling back to the real value is not a safe default — it is a leak.** Turn on
logging after every configuration change:

```bat
set MOZ_LOG=fpfile:5
```

The log reports how many keys parsed, which key is missing, and which value has
the wrong format.

---

## 3. Fingerprint template

The template below is one **self-consistent target machine**: Windows 11 /
Firefox 155 / 1920×1080 / no touch / 8 logical cores / en-US / New York
timezone / NVIDIA RTX 3060.

After copying it you **must change three things**: replace `webgl.*` with a real
dump from the target GPU (see the export script in §4.4), align `timezone` and
`language` with the egress IP's location, and fill in your own proxy section.

```text
# ===== Navigator / HTTP =====
# The version in the UA must match the actual build (155.0 here), otherwise it
# contradicts buildID, productSub, and oscpu, none of which can be changed.
useragent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0
language: en-US,en

# ===== Hardware =====
hardwareConcurrency=8
touch.maxTouchPoints=0
width=1920
height=1080
screen.colorDepth=24
screen.devicePixelRatio=1

# ===== Timezone =====
timezone=America/New_York

# ===== Canvas (both keys are required; either one alone does nothing) =====
canvas.mode=pixel
canvas.seed=15520260731
canvas.strength=low
# Leave canvas.pngMetadata unset (defaults to false), see §4.3

# ===== Audio =====
audio.seed=15520260732

# ===== WebGL (the whole set must come from one GPU; this shows the shape only) =====
webgl.vendor=Mozilla
webgl.renderer=Mozilla
webgl.unmasked_vendor=Google Inc. (NVIDIA)
webgl.unmasked_renderer=ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0), or similar
webgl.version=WebGL 1.0
webgl2.version=WebGL 2.0
webgl.glsl_version=WebGL GLSL ES 1.0
webgl2.glsl_version=WebGL GLSL ES 3.00
webgl.max_texture_size=16384
webgl.max_cube_map_texture_size=16384
webgl.max_renderbuffer_size=16384
webgl.max_viewport_dim=32768
webgl.max_vertex_attribs=16
webgl.max_vertex_uniform_vectors=4096
webgl.max_fragment_uniform_vectors=1024
webgl.max_varying_vectors=30
webgl.max_texture_image_units=16
webgl.max_vertex_texture_image_units=16
webgl.max_combined_texture_image_units=32
webgl.max_draw_buffers=8
webgl.max_color_attachments=8
webgl.max_3d_texture_size=2048
webgl.max_array_texture_layers=2048
webgl.max_samples=8
webgl.max_uniform_buffer_bindings=72
webgl.uniform_buffer_offset_alignment=256
webgl.max_uniform_block_size=65536
webgl.max_anisotropy=16
webgl.aliased_point_size_min=1
webgl.aliased_point_size_max=1024
webgl.aliased_line_width_min=1
webgl.aliased_line_width_max=1
webgl.shader_precision.vertex.high_float=127,127,23
webgl.shader_precision.fragment.high_float=127,127,23
webgl.supported_extensions=ANGLE_instanced_arrays,EXT_blend_minmax,EXT_color_buffer_half_float,EXT_float_blend,EXT_frag_depth,EXT_shader_texture_lod,EXT_sRGB,EXT_texture_compression_bptc,EXT_texture_compression_rgtc,EXT_texture_filter_anisotropic,OES_element_index_uint,OES_fbo_render_mipmap,OES_standard_derivatives,OES_texture_float,OES_texture_float_linear,OES_texture_half_float,OES_texture_half_float_linear,OES_vertex_array_object,WEBGL_color_buffer_float,WEBGL_compressed_texture_s3tc,WEBGL_compressed_texture_s3tc_srgb,WEBGL_debug_renderer_info,WEBGL_debug_shaders,WEBGL_depth_texture,WEBGL_draw_buffers,WEBGL_lose_context,WEBGL_provoking_vertex

# ===== Fonts (must include generic-family targets and CJK, see §4.6) =====
fonts.whitelist=Arial,Arial Black,Calibri,Cambria,Candara,Comic Sans MS,Consolas,Constantia,Corbel,Courier New,Ebrima,Franklin Gothic Medium,Gabriola,Gadugi,Georgia,Impact,Ink Free,Javanese Text,Leelawadee UI,Lucida Console,Lucida Sans Unicode,Malgun Gothic,Marlett,Microsoft Himalaya,Microsoft JhengHei,Microsoft New Tai Lue,Microsoft PhagsPa,Microsoft Sans Serif,Microsoft Tai Le,Microsoft YaHei,Microsoft Yi Baiti,MingLiU-ExtB,Mongolian Baiti,MS Gothic,MV Boli,Myanmar Text,Nirmala UI,Palatino Linotype,Segoe MDL2 Assets,Segoe Print,Segoe Script,Segoe UI,Segoe UI Emoji,Segoe UI Historic,Segoe UI Symbol,SimSun,Sitka,Sylfaen,Symbol,Tahoma,Times New Roman,Trebuchet MS,Verdana,Webdings,Wingdings,Yu Gothic,MS Shell Dlg 2,NSimSun,Arial Unicode MS

# ===== WebRTC / media =====
webrtc.ice_proxy_only=true
media.devices=audioinput,audiooutput,videoinput

# ===== Proxy and auth (optional, use your own) =====
# socksauth.host=<proxy_host>
# socksauth.port=<proxy_port>
# socksauth.username=<proxy_username>
# socksauth.password=<proxy_password>
# httpauth.username=<http_username>
# httpauth.password=<http_password>
```

---

## 4. Field reference

The subsection numbers map to the ten detection categories commonly used by
anti-bot fingerprinting.

### 4.1 Automation / WebDriver detection — no configuration

**This layer needs no keys at all.**

`navigator.webdriver` is permanently `false` and does not read the fpfile. The
injected markers `cdc_*`, `__fxdriver*`, `__webdriver*`, `__selenium*`,
`_phantom`, `callPhantom`, `__nightmare`, `awesomium`, `domAutomation*`, and
`__phantomas` simply do not exist.

The six event channels `webdriver-evaluate`, `webdriver-evaluate-response`,
`webdriverCommand`, `selenium-evaluate`, `driver-evaluate`, and
`__playwright_mark_target__` are never dispatched as long as no driver is
running. Measured on real traffic: 2463 listener registrations, 0 dispatches.

### 4.2 Hardware / device

| key | meaning | values | APIs affected |
|---|---|---|---|
| `hardwareConcurrency` | logical CPU count | 2 / 4 / 8 / 12 / 16 are the common ones | `navigator.hardwareConcurrency` (main thread + Worker) |
| `touch.maxTouchPoints` | touch point count | `0` for non-touch devices; `5` or `10` for touch | `navigator.maxTouchPoints`, `ontouchstart`, `TouchEvent`, `document.createTouch`, pointer capabilities |
| `width` | screen width | `1920` / `1536` / `1366` | `screen.width`, `screen.availWidth`, `window.innerWidth`, `outerWidth`, `visualViewport.width` |
| `height` | screen height | `1080` / `864` / `768` | `screen.height` plus the offset family below |
| `screen.colorDepth` | colour depth | only `24` / `30` / `32` accepted | `screen.colorDepth`, `screen.pixelDepth` (both share one value) |
| `screen.devicePixelRatio` | system scaling | only `1` / `1.25` / `1.5` / `1.75` / `2` / `2.5` / `3` accepted | `window.devicePixelRatio`, CSS `resolution`, `-moz-device-pixel-ratio` |

Aliases: `hardwareConcurrency` also accepts `HardwareConcurrency`;
`touch.maxTouchPoints` also accepts `touch.max_touch_points` and
`maxTouchPoints`.

**`width` / `height` are broad in scope** — one value feeds several APIs, each
carrying a historical hardcoded offset:

| API | returns |
|---|---|
| `screen.width` / `screen.availWidth` | `width` |
| `screen.height` | `height` |
| `screen.availHeight` | `height - 48` (taskbar) |
| `window.innerWidth` / `outerWidth` | `width` |
| `window.innerHeight` | `height - 141` |
| `window.outerHeight` | `height - 50` |
| `visualViewport.width` | `width - 15` |
| `visualViewport.height` | `height - 140` |

To reproduce one specific set of real dimensions, work backwards from this table
to derive `width` / `height`. Fractions, negatives, and `0` are all treated as
absent.

`navigator.deviceMemory` needs no configuration — Firefox does not implement it,
and its absence is correct.

**Configure touch through `touch.maxTouchPoints` only.** The aliases
`touch.enabled`, `touch_enabled`, `touch.events.enabled`, and
`touch_events.enabled` are honoured by some modules but not others, which
produces contradictory state: setting only `touch.events.enabled=true` was
measured to expose the whole Touch API while `navigator.maxTouchPoints` stayed
`0`. Setting only `touch.maxTouchPoints` gets all four right.

### 4.3 Canvas

| key | meaning | values | default |
|---|---|---|---|
| `canvas.mode` | randomisation mode | `pixel` | `none` (inactive) |
| `canvas.seed` | randomisation seed | non-zero decimal uint64 | none |
| `canvas.strength` | perturbation depth (how many low bits are replaced) | `low` / `medium` / `high` | `low` |
| `canvas.density` | perturb roughly 1 pixel in N | 1 – 4096 | `64` |
| `canvas.preserveAlpha` | leave the alpha channel untouched | `true` / `false` | `true` |
| `canvas.preserveWhitePoint` | leave pure white pixels untouched | `true` / `false` | `true` |
| `canvas.pngMetadata` | write a randomisation chunk into the PNG | `true` / `false` | `false` |

**`canvas.mode` and `canvas.seed` must both be present**; either one alone does
nothing.

Once active, `toDataURL`, `toBlob`, and `getImageData` all return perturbed
pixels, reproducibly for a given seed. `fillText` / `fillRect` need no separate
configuration — their results are perturbed through those three exits.

**Leave `canvas.pngMetadata` off.** Measured: with `canvas.mode=pixel` and this
key unset, the PNG chunk sequence is a clean `IHDR,IDAT,IEND`; without any
fpfile there is actually an extra private `deBG` chunk. Enabling it means
deliberately adding back a chunk no standard browser emits.

**`canvas.density` rarely needs changing.** Changing a canvas hash only requires
perturbing some pixels, whereas per-pixel noise defeats PNG row prediction and
inflates the output enormously — and that size difference is measurable without
any reference sample. Measured `toDataURL()` lengths for the same drawing:

| canvas | native | density=1 | density=64 (default) |
|---|---|---|---|
| 32×32 solid | 570 | 1838 (3.2×) | 634 (1.1×) |
| 120×40 solid | 686 | 6354 (9.3×) | 954 (1.4×) |
| 280×60 solid | 842 | 20334 (**24×**) | 1834 (2.2×) |
| 240×60 text + gradient | 6830 | 11590 (1.7×) | 6986 (1.02×) |

Raising it compresses the output further, but **too sparse leaves small canvases
completely untouched**, which hands over the real fingerprint verbatim: at
`density=1024`, 8×8 and 16×16 outputs were byte-identical to native. The code
therefore tightens density by canvas area to keep the expected perturbation at
no fewer than 8 pixels, which is also why `density` is capped at 4096.

`measureText` metrics themselves are not perturbed; font fingerprinting is
contained by the whitelist in §4.6.

### 4.4 WebGL

The single most identifying item on this list. **Coverage is complete, but the
whole set must be configured — half a set is more conspicuous than none.**

String values:

| key | meaning | example |
|---|---|---|
| `webgl.vendor` | masked VENDOR | `Mozilla` |
| `webgl.renderer` | masked RENDERER | `Mozilla` |
| `webgl.unmasked_vendor` | UNMASKED_VENDOR_WEBGL from `WEBGL_debug_renderer_info` | `Google Inc. (NVIDIA)` |
| `webgl.unmasked_renderer` | UNMASKED_RENDERER_WEBGL, i.e. the GPU model | `ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0), or similar` |
| `webgl.version` / `webgl2.version` | VERSION | `WebGL 1.0` / `WebGL 2.0` |
| `webgl.glsl_version` / `webgl2.glsl_version` | SHADING_LANGUAGE_VERSION | `WebGL GLSL ES 1.0` / `WebGL GLSL ES 3.00` |
| `webgl.supported_extensions` | extension list, comma or pipe separated | see template |
| `webgl.compressed_texture_formats` | compressed texture formats, enum names or numbers | `COMPRESSED_RGB_S3TC_DXT1_EXT,...` |

Integer limits (all take the `webgl.` prefix):

```text
max_texture_size            max_cube_map_texture_size    max_renderbuffer_size
max_viewport_dim            max_vertex_attribs           max_texture_image_units
max_vertex_texture_image_units                           max_combined_texture_image_units
max_vertex_uniform_vectors  max_fragment_uniform_vectors max_varying_vectors
max_vertex_uniform_components  max_fragment_uniform_components  max_varying_components
max_draw_buffers            max_color_attachments        max_3d_texture_size
max_array_texture_layers    max_samples                  max_uniform_buffer_bindings
uniform_buffer_offset_alignment  max_uniform_block_size
max_combined_uniform_blocks max_vertex_uniform_blocks    max_fragment_uniform_blocks
max_combined_vertex_uniform_components  max_combined_fragment_uniform_components
```

Floating point: `webgl.max_anisotropy`, `webgl.aliased_point_size_min` / `_max`,
`webgl.aliased_line_width_min` / `_max`.

Shader precision (the value is three integers, `rangeMin,rangeMax,precision`):

```text
webgl.shader_precision.vertex.{low,medium,high}_{float,int}
webgl.shader_precision.fragment.{low,medium,high}_{float,int}
```

**How to dump the real values from a target machine**: open any page in a real
browser on that machine, run the snippet below, and paste the output into the
fpfile. Do not hand-author these — a `max_texture_size` that disagrees with the
renderer is easy to catch.

```js
const gl = document.createElement('canvas').getContext('webgl');
const d = gl.getExtension('WEBGL_debug_renderer_info');
const out = [
  ['webgl.vendor', gl.getParameter(gl.VENDOR)],
  ['webgl.renderer', gl.getParameter(gl.RENDERER)],
  ['webgl.unmasked_vendor', d && gl.getParameter(d.UNMASKED_VENDOR_WEBGL)],
  ['webgl.unmasked_renderer', d && gl.getParameter(d.UNMASKED_RENDERER_WEBGL)],
  ['webgl.version', gl.getParameter(gl.VERSION)],
  ['webgl.glsl_version', gl.getParameter(gl.SHADING_LANGUAGE_VERSION)],
  ['webgl.max_texture_size', gl.getParameter(gl.MAX_TEXTURE_SIZE)],
  ['webgl.max_cube_map_texture_size', gl.getParameter(gl.MAX_CUBE_MAP_TEXTURE_SIZE)],
  ['webgl.max_renderbuffer_size', gl.getParameter(gl.MAX_RENDERBUFFER_SIZE)],
  ['webgl.max_viewport_dim', gl.getParameter(gl.MAX_VIEWPORT_DIMS)[0]],
  ['webgl.max_vertex_attribs', gl.getParameter(gl.MAX_VERTEX_ATTRIBS)],
  ['webgl.max_texture_image_units', gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS)],
  ['webgl.max_vertex_texture_image_units', gl.getParameter(gl.MAX_VERTEX_TEXTURE_IMAGE_UNITS)],
  ['webgl.max_combined_texture_image_units', gl.getParameter(gl.MAX_COMBINED_TEXTURE_IMAGE_UNITS)],
  ['webgl.max_vertex_uniform_vectors', gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS)],
  ['webgl.max_fragment_uniform_vectors', gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS)],
  ['webgl.max_varying_vectors', gl.getParameter(gl.MAX_VARYING_VECTORS)],
  ['webgl.aliased_point_size_min', gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)[0]],
  ['webgl.aliased_point_size_max', gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)[1]],
  ['webgl.aliased_line_width_min', gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE)[0]],
  ['webgl.aliased_line_width_max', gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE)[1]],
  ['webgl.supported_extensions', gl.getSupportedExtensions().join(',')],
];
for (const t of ['VERTEX_SHADER', 'FRAGMENT_SHADER']) {
  for (const p of ['LOW_FLOAT','MEDIUM_FLOAT','HIGH_FLOAT','LOW_INT','MEDIUM_INT','HIGH_INT']) {
    const f = gl.getShaderPrecisionFormat(gl[t], gl[p]);
    out.push([`webgl.shader_precision.${t.split('_')[0].toLowerCase()}.${p.toLowerCase()}`,
              `${f.rangeMin},${f.rangeMax},${f.precision}`]);
  }
}
const gl2 = document.createElement('canvas').getContext('webgl2');
if (gl2) {
  out.push(['webgl2.version', gl2.getParameter(gl2.VERSION)]);
  out.push(['webgl2.glsl_version', gl2.getParameter(gl2.SHADING_LANGUAGE_VERSION)]);
  for (const [k, e] of [['max_3d_texture_size','MAX_3D_TEXTURE_SIZE'],
       ['max_array_texture_layers','MAX_ARRAY_TEXTURE_LAYERS'],
       ['max_samples','MAX_SAMPLES'],
       ['max_draw_buffers','MAX_DRAW_BUFFERS'],
       ['max_color_attachments','MAX_COLOR_ATTACHMENTS'],
       ['max_uniform_buffer_bindings','MAX_UNIFORM_BUFFER_BINDINGS'],
       ['uniform_buffer_offset_alignment','UNIFORM_BUFFER_OFFSET_ALIGNMENT'],
       ['max_uniform_block_size','MAX_UNIFORM_BLOCK_SIZE']]) {
    out.push([`webgl.${k}`, gl2.getParameter(gl2[e])]);
  }
}
console.log(out.map(([k, v]) => `${k}=${v}`).join('\n'));
```

### 4.5 Audio

| key | meaning | values |
|---|---|---|
| `audio.seed` | audio perturbation seed | non-zero decimal uint64. Unset = native output |

There is no `audio.mode` / `audio.enabled` — **the seed's presence enables it**.
Reproducible for a given seed.

Two points are covered:

1. the output of `DynamicsCompressorNode` — the classic audio fingerprint
   (`oscillator → dynamicsCompressor → destination`) goes through this path;
2. **the offline render output buffer** — covering any graph that bypasses the
   compressor, such as a direct `oscillator → destination`.

Point 2 used to be a gap: two different seeds and no fpfile at all were measured
to produce byte-identical render hashes. The direct path now varies with the seed
and is stable for a given seed.

When the graph does contain a compressor, the signal is perturbed twice; the net
effect is 2 ULP in the same direction, so the two do not cancel and the result
stays deterministic. The cost is that an `AnalyserNode` reading mid-render sees
values 1 ULP away from the final buffer — noticing that requires a script that
compares analyser output against the offline buffer sample by sample.

**Still not covered**: `AnalyserNode` on a live `AudioContext` (not offline
rendering).

### 4.6 Fonts

| key | meaning | values |
|---|---|---|
| `fonts.whitelist` | whitelist of font families visible to the page | family names, comma or pipe separated |
| `font_system` | platform style for system UI fonts | `windows` / `linux` / `mac` (aliases `Font_System` / `font-system`) |

A non-empty `fonts.whitelist` filters **entire families** outside the list out of
the font table, which contains both `measureText` and the `getClientRects`
fallback.

**It must include the generic-family mapping targets and CJK fonts; omitting
them creates a new fingerprint more conspicuous than font enumeration itself.**
Measured locally at 16px:

| | no whitelist | Latin-only list | completed list |
|---|---|---|---|
| `serif` Latin metric | 32.00 | **29.35** | 32.00 |
| `sans-serif` | 29.83 | **29.35** | 29.83 |
| `monospace` | 32.00 | **29.35** | 32.00 |
| `sans-serif`, four CJK chars | 64.00 | **52.00** | 64.00 |
| `<div>` offsetHeight | 21 | **18** | 21 |

Three identical generic-family metrics are impossible on real Windows, and CJK
dropping to 13px is visible layout degradation. Besides the fonts you want to
expose, the list must also contain:

```text
Times New Roman,Arial,Courier New,Consolas,MS Shell Dlg 2,Lucida Console
SimSun,NSimSun,Microsoft YaHei,Microsoft JhengHei,MS Gothic,Malgun Gothic
Segoe UI,Segoe UI Symbol,Segoe UI Emoji,Arial Unicode MS
```

**Verify these two properties, not what the list contains:**

1. the Latin metrics of `serif`, `sans-serif`, and `monospace` all differ from
   each other;
2. CJK at 16px advances 16px per character (four characters = 64.00).

Note that `document.fonts.check()` returns `true` for any font because it falls
back, so it is **not** a font-existence probe. To confirm the whitelist works,
check whether a font outside the list measures the same as a font that does not
exist — `Gabriola`, once filtered, measured 72.4833, identical to
`NoSuchFontXYZ`.

### 4.7 Navigator consistency

| key | aliases | affects |
|---|---|---|
| `useragent` | `userAgent`, `user-agent` | `navigator.userAgent` (main thread + Worker), HTTP `User-Agent` |
| `language` | written as either `language:` or `language=` | `navigator.language` / `languages` (main thread + Worker), HTTP `Accept-Language`, default `Intl` locale |

**The version inside the UA must match the actual build.** The fields below
**cannot be changed** — they are emitted verbatim by upstream Firefox and
corroborate the UA:

| API | value | note |
|---|---|---|
| `navigator.platform` | `Win32` | hardcoded |
| `navigator.vendor` | `""` (empty string) | always empty in Firefox |
| `navigator.vendorSub` | `""` | always empty |
| `navigator.product` | `Gecko` | hardcoded |
| `navigator.productSub` | `20100101` | hardcoded |
| `navigator.appName` | `Netscape` | hardcoded |
| `navigator.appCodeName` | `Mozilla` | hardcoded |
| `navigator.appVersion` | `5.0 (Windows)` | derived from the platform |
| `navigator.oscpu` | `Windows NT 10.0; Win64; x64` | derived from the platform |
| `navigator.buildID` | `20181001000000` | frozen upstream for anti-fingerprinting |
| `navigator.doNotTrack` | `unspecified` | when DNT is off |
| `navigator.plugins` | 5 PDF viewer aliases | the fixed W3C-mandated list, same in Chrome |
| `navigator.mimeTypes` | 2 entries | same as above |
| `navigator.deviceMemory` | absent | Firefox does not implement it |
| `navigator.userAgentData` | absent | Firefox does not implement it |

Precedence for `language`: fpfile > `BrowsingContext.languageOverride` >
the `intl.accept_languages` pref. The JS side and the HTTP side use the same
precedence, so `navigator.languages` and `Accept-Language` never disagree.

The q values in HTTP `Accept-Language` are generated by the upstream algorithm,
so `en-US,en` becomes `en-US,en;q=0.9` (Chrome-style decay, as of upstream
Bug 2000765). **That is genuine Firefox behaviour, not a tell.**

### 4.8 WebRTC / media / permissions / sensors

| key | meaning | values |
|---|---|---|
| `webrtc.ice_proxy_only` | route ICE through the proxy only | `true` / `false`. **Set true** when using a proxy |
| `media.devices` | the device list returned by `enumerateDevices()` | `audioinput` / `audiooutput` / `videoinput`, comma separated; repeat for multiple units |
| `local_webrtc_ipv4` / `local_webrtc_ipv6` | replace the local IP | IP literal |
| `public_webrtc_ipv4` / `public_webrtc_ipv6` | replace the public IP | IP literal |
| `webrtc_ip_mismatch_policy` | policy when the IPs disagree | `passthrough` or empty |

**`webrtc.ice_proxy_only=true` is mandatory when using a proxy**, otherwise STUN
may connect directly and the srflx candidate independently exposes the real
egress IP. Setting it to `true` makes the srflx candidate disappear.

The local LAN IP does not leak by default — mDNS obfuscation turns the host
candidate into `<uuid>.local`, verified working. `local_webrtc_*` is generally
unnecessary.

For `media.devices`, `deviceId` / `groupId` / `label` are all left empty, which
matches unauthorised-state Firefox. Note that an empty label is also returned
where the camera or microphone has been granted; if you need that behaviour, do
not set this key.

Voice lists:

| key | meaning |
|---|---|
| `speech.voices.local` | local voice names, **pipe (`\|`) separated** |
| `speech.voices.remote` | remote voice names, same format |
| `speech.voices.local.langs` | BCP47 languages, one per local voice |
| `speech.voices.remote.langs` | one per remote voice |
| `speech.voices.default.name` | default voice name |
| `speech.voices.default.lang` | default voice language |

**These lists split on `|` only**, because real voice names contain commas, for
example `Microsoft Server Speech Text to Speech Voice (en-US, AriaNeural)`.

Every voice **must have a language** (from `.langs` or `.default.lang`),
otherwise that voice is skipped with a warning. The `voiceURI` takes the form
`urn:moz-tts:sapi:<name>?<lang>`, matching real-machine output.

**No configuration needed** (native Firefox behaviour is already correct):

| API | state | note |
|---|---|---|
| `navigator.getBattery` | absent | removed from Firefox; a Chrome-only API |
| `navigator.getGamepads` | present, returns an empty array | the normal result with no gamepad |
| `navigator.requestMIDIAccess` | present | normal |
| `navigator.permissions.query` | everything returns `prompt` | the normal state of a clean profile |

Geolocation (an independent group, all off by default):

| key | meaning | values |
|---|---|---|
| `geolocation.enabled` | master switch; **must be true for the rest to apply** | `true` |
| `geolocation.latitude` | latitude (required) | -90 – 90 |
| `geolocation.longitude` | longitude (required) | -180 – 180 |
| `geolocation.accuracy` | accuracy in metres (required) | > 0 |
| `geolocation.altitude` | altitude (optional) | any |
| `geolocation.altitudeAccuracy` | altitude accuracy (optional, needs altitude first) | ≥ 0 |
| `geolocation.heading` | heading (optional, needs speed > 0) | 0 – 360 |
| `geolocation.speed` | speed (optional) | ≥ 0 |
| `geolocation.timestamp` | timestamp (required) | `now` or decimal milliseconds |
| `geolocation.permission` | permission state | `prompt` (default) / `granted` / `denied` |

**Geolocation must be self-consistent with the egress IP and the timezone**,
otherwise it is worse than leaving it unset.

### 4.9 Timezone / language

| key | aliases | meaning | values |
|---|---|---|---|
| `timezone` | `TimeZone`, `TIMEZONE` | IANA timezone name | `America/New_York`, `Asia/Singapore`, `Europe/London` |

Affects `Intl.DateTimeFormat().resolvedOptions().timeZone`,
`Date.prototype.getTimezoneOffset()`, and every Intl constructor that relies on
the default timezone, consistently across the main thread and Workers. Windows
only.

For language, see `language` in §4.7.

`Date.now` / `performance.now` precision needs **no configuration** — upstream
already clamps it to 1ms.

### 4.10 Anti-hook / prototype probing — no configuration

Every fingerprint value is rewritten inside the native C++ getter; no JS
function is wrapped. As a result the getters' `toString()` returns a genuine
`[native code]`, the `enumerable` / `configurable` shape of the property
descriptors is unchanged, and the membership and ordering of
`Object.getOwnPropertyNames` are unchanged.

**This layer needs — and should have — no configuration at all.**

---

## 5. Network and auth

Not fingerprinting, but carried by the same file.

| key | meaning |
|---|---|
| `httpauth.username` / `httpauth.password` | credentials for HTTP Basic Auth and HTTP proxy 407 |
| `socksauth.host` / `.port` / `.username` / `.password` | SOCKS5 auth |
| `<host>:<port>:<user>:<pass>` | one-line SOCKS5 form, requires exactly 3 colons |
| `proxy.rotate.enabled` | proxy rotation switch, `true` / `false` (alias `httpproxy.rotate.enabled`) |
| `proxy.rotate.exhausted` | exhaustion policy: `wrap` cycles; `direct` / `none` / `stop` do not |
| `proxy.rotate.proxy` | an HTTP rotation entry, `http://host:port:user:pass` (aliases `httpproxy.rotate.proxy`, `httpproxy.proxy`) |
| `socks5proxy.rotate.proxy` | a SOCKS5 rotation entry, `socks5://host:port:user:pass` |

SOCKS5 also needs the profile prefs `network.proxy.socks`,
`network.proxy.socks_port`, and `network.proxy.socks_version=5`. Proxy rotation
is allocated per userContext.

WebGPU (many keys; consult `dom/webgpu/Adapter.cpp` as needed):

| key | meaning |
|---|---|
| `webgpu.enabled` | master switch (alias `webgpu_enabled`) |
| `webgpu.features` | adapter feature list |
| `webgpu.wgslLanguageFeatures` | WGSL language features |
| `webgpu.preferredCanvasFormat` | `rgba8unorm` / `bgra8unorm` |
| `webgpu.vendor` / `.architecture` / `.device` / `.description` | adapter info |
| `webgpu.limits.<name>` | individual limits (alias prefixes `webgpu.limit.` / `webgpu.`) |

---

## 6. Required keys

Leaving these unset means leaking the real machine:

| key | consequence of omitting |
|---|---|
| **the whole `webgl.*` set** | the real GPU vendor and model are emitted verbatim. The single most identifying item |
| `touch.maxTouchPoints` | real touch capability leaks, and may be inconsistent across processes |
| `width` / `height` | real screen resolution |
| `hardwareConcurrency` | real CPU core count |
| `timezone` | real timezone, possibly contradicting the egress IP |
| `language` | real language |
| `canvas.mode` + `canvas.seed` | Canvas is not randomised |
| `audio.seed` | audio is not perturbed |
| `fonts.whitelist` | the real installed fonts can be enumerated through `measureText` |
| `webrtc.ice_proxy_only` (when proxying) | the srflx candidate exposes the real egress IP |

As needed: `screen.colorDepth`, `screen.devicePixelRatio` (**mandatory** when
the host has system scaling enabled), `media.devices`, `speech.voices.*`,
`geolocation.*`.

---

## 7. Self-check

```bat
set MOZ_LOG=fpfile:5
```

1. the log **must not** contain `missing key` lines;
2. no `is not an unsigned integer` / `is not a boolean` / `is not a number`
   lines;
3. the Latin metrics of `serif`, `sans-serif`, and `monospace` all differ;
4. CJK at 16px, four characters wide = 64.00;
5. `navigator.maxTouchPoints` does not contradict `ontouchstart`;
6. no `typ srflx` inside `RTCIceCandidate.candidate`;
7. `navigator.languages` agrees between the main thread and Workers;
8. `window.devicePixelRatio` agrees with
   `matchMedia('(resolution: Xdppx)')`;
9. WebGL's `UNMASKED_RENDERER_WEBGL` is the configured value, not the real GPU.

---

## 8. Known limits

**It cannot impersonate Chrome.** `platform`, `vendor`, `productSub`, `oscpu`,
`buildID`, and `appVersion` are all upstream Firefox values that the fpfile
cannot change. Putting a Chrome UA in `useragent` contradicts itself in five
places immediately: `vendor` is an empty string (Chrome reports
`Google Inc.`), `productSub` is `20100101` (Chrome reports `20030107`), `oscpu`
and `buildID` do not exist in Chrome, and `userAgentData` and `deviceMemory`
are absent.

**Self-consistency between egress IP, timezone, language, and geolocation is
the operator's responsibility** — the code can only emit what each key says.

**`AnalyserNode` on a live `AudioContext` is not perturbed** (offline rendering
and the compressor path are covered, see §4.5).

**`measureText` metrics themselves are not perturbed** (see §4.3).

**Canvas PNG output is still slightly larger than native** (worst case around
2.2× at the default density, see §4.3). That is the structural cost of
per-pixel perturbation; it can be reduced but not eliminated.
