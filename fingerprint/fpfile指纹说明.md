# fpfile 指纹说明

`--fpfile=<path>` 是这个定制版 Firefox 内核的唯一配置入口。同一个文件同时承载
浏览器指纹、网络代理和认证配置。

本文只描述当前实现。外发时不要填真实代理、账号、密码、出口 IP。

> English version: [`fpfile-fingerprint.md`](fpfile-fingerprint.md)

## 与 ruyiPage 的关系

多数场景**不用手写这个文件**。ruyiPage 的智能指纹 API 会自动生成它：

```python
opts = FirefoxOptions()
opts.set_browser_path(r"C:/path/to/firefox.exe")
ctx = opts.smart_fingerprint(proxy_host="...", proxy_port=8080, require_country="US")
page = FirefoxPage(opts)
ctx.apply_emulation(page)
```

`smart_fingerprint()` 会探测出口 IP、对齐语言/时区/语音、从 22 套真机硬件特征里
抽一套，写出一份自洽的 `fpfile.txt` 并通过 `--fpfile=` 传给内核（用法见主 README 的
「智能指纹一站式 API」一节）。

**什么时候需要读这份文档、手改这个文件**：

- 要固定某台目标机型（自己 dump 的 WebGL / 屏幕 / 字体），而不是用内置那 22 套；
- 智能指纹没覆盖的字段要单独调（WebGPU、语音列表、地理位置细项等）；
- 排查某个检测点为什么没生效——每一项都有对应的 key 和验收方法；
- 只用内核、不经过 ruyiPage 时，这是唯一的配置说明。

手写时把下面 §3 的模板整份复制，按 §6 的必填清单逐项落实，最后按 §7 自检。

## 目录

- [1. 启动方式](#1-启动方式)
- [2. 语法规则](#2-语法规则)
- [3. 指纹模板](#3-指纹模板)
- [4. 逐项说明](#4-逐项说明) — 对应十类常见检测点
  - [4.1 自动化 / WebDriver 检测（无需配置）](#41-自动化--webdriver-检测--无需配置)
  - [4.2 硬件 / 设备](#42-硬件--设备)
  - [4.3 Canvas](#43-canvas)
  - [4.4 WebGL](#44-webgl)
  - [4.5 音频](#45-音频)
  - [4.6 字体](#46-字体)
  - [4.7 Navigator 属性一致性](#47-navigator-属性一致性)
  - [4.8 WebRTC / 媒体 / 权限 / 传感器](#48-webrtc--媒体--权限--传感器)
  - [4.9 时区 / 语言](#49-时区--语言)
  - [4.10 反 Hook / 原型链探测（无需配置）](#410-反-hook--原型链探测--无需配置)
- [5. 网络与认证](#5-网络与认证)
- [6. 必填清单](#6-必填清单)
- [7. 配完自检](#7-配完自检)
- [8. 已知限制](#8-已知限制)

---

## 1. 启动方式

必须用等号形式：

```bat
firefox.exe --new-instance -no-remote -profile C:\path\profile --fpfile=C:\path\fingerprint.fp "https://example.com/"
```

不要写成空格分隔（`--fpfile C:\path\...`）。底层用
`base::CommandLine::GetSwitchValue("fpfile")` 取值，空格形式在部分路径上取不到值，
典型表现是 SOCKS5 认证不带用户名密码。

启动后 console 里会出现一条 `unrecognized command line flag "-fpfile"`，**这是正常的**。

`--fpfile` 会自动透传给所有子进程并加入沙箱读白名单。

---

## 2. 语法规则

- 分隔符 `:` 或 `=` 都认，取**第一个出现的**。所以 UA 里的冒号不会被误当分隔符。
- key 和 value 两侧的空白会去掉。
- 整行以 `#` 或 `//` 开头是注释。空行忽略。
- 同一个 key 出现多次，**取最后一个**。
- key **精确匹配**。`width` 不会命中 `widthFoo`，`speech.voices.local` 不会命中
  `speech.voices.local.langs`。
- 值为空等于没配这个 key。
- 数值解析失败（空、非数字、负数、溢出）一律视为**没配**，回落到真机值，不会崩。

**回落到真机值不是安全的默认，是泄露。** 每次改完配置开日志确认：

```bat
set MOZ_LOG=fpfile:5
```

日志会打印解析了几个 key、哪个 key 缺失、哪个值格式不对。

---

## 3. 指纹模板

下面这份是一台**自洽的目标机型**：Windows 11 / Firefox 155 / 1920×1080 / 无触摸 /
8 逻辑核 / en-US / 纽约时区 / NVIDIA RTX 3060。

直接复制后**必须改三处**：`webgl.*` 换成目标显卡的真机 dump（见 §4.4 的导出方法）、
`timezone` 与 `language` 对齐出口 IP 归属地、代理段填自己的。

```text
# ===== Navigator / HTTP =====
# UA 里的版本号必须和实际构建版本一致（当前 155.0），否则会和 buildID、
# productSub、oscpu 这些改不了的字段互相矛盾。
useragent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0
language: en-US,en

# ===== 硬件 =====
hardwareConcurrency=8
touch.maxTouchPoints=0
width=1920
height=1080
screen.colorDepth=24
screen.devicePixelRatio=1

# ===== 时区 =====
timezone=America/New_York

# ===== Canvas（两个 key 必须同时给，缺一个就不生效）=====
canvas.mode=pixel
canvas.seed=15520260731
canvas.strength=low
# canvas.pngMetadata 保持不配（默认 false），见 §4.3

# ===== 音频 =====
audio.seed=15520260732

# ===== WebGL（必须整套来自同一块卡，下面是形状示例，不要直接用）=====
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

# ===== 字体（必含通用族映射目标和 CJK，见 §4.6）=====
fonts.whitelist=Arial,Arial Black,Calibri,Cambria,Candara,Comic Sans MS,Consolas,Constantia,Corbel,Courier New,Ebrima,Franklin Gothic Medium,Gabriola,Gadugi,Georgia,Impact,Ink Free,Javanese Text,Leelawadee UI,Lucida Console,Lucida Sans Unicode,Malgun Gothic,Marlett,Microsoft Himalaya,Microsoft JhengHei,Microsoft New Tai Lue,Microsoft PhagsPa,Microsoft Sans Serif,Microsoft Tai Le,Microsoft YaHei,Microsoft Yi Baiti,MingLiU-ExtB,Mongolian Baiti,MS Gothic,MV Boli,Myanmar Text,Nirmala UI,Palatino Linotype,Segoe MDL2 Assets,Segoe Print,Segoe Script,Segoe UI,Segoe UI Emoji,Segoe UI Historic,Segoe UI Symbol,SimSun,Sitka,Sylfaen,Symbol,Tahoma,Times New Roman,Trebuchet MS,Verdana,Webdings,Wingdings,Yu Gothic,MS Shell Dlg 2,NSimSun,Arial Unicode MS

# ===== WebRTC / 媒体 =====
webrtc.ice_proxy_only=true
media.devices=audioinput,audiooutput,videoinput

# ===== 代理与认证（按需，填自己的）=====
# socksauth.host=<proxy_host>
# socksauth.port=<proxy_port>
# socksauth.username=<proxy_username>
# socksauth.password=<proxy_password>
# httpauth.username=<http_username>
# httpauth.password=<http_password>
```

---

## 4. 逐项说明

小节编号对应常见反爬指纹的十类检测点。

### 4.1 自动化 / WebDriver 检测 —— 无需配置

**这一层不需要任何 key。**

`navigator.webdriver` 恒为 `false`，不读 fpfile。`cdc_*`、`__fxdriver*`、
`__webdriver*`、`__selenium*`、`_phantom`、`callPhantom`、`__nightmare`、
`awesomium`、`domAutomation*`、`__phantomas` 这些注入变量本来就不存在。

`webdriver-evaluate` / `webdriver-evaluate-response` / `webdriverCommand` /
`selenium-evaluate` / `driver-evaluate` / `__playwright_mark_target__` 六条事件通道，
只要没有驱动在跑就不会有人派发。实测真实采集：2463 次挂监听，0 次派发。

### 4.2 硬件 / 设备

| key | 说明 | 取值 | 影响的 API |
|---|---|---|---|
| `hardwareConcurrency` | 逻辑 CPU 数 | 2 / 4 / 8 / 12 / 16，常见值 | `navigator.hardwareConcurrency`（主线程 + Worker） |
| `touch.maxTouchPoints` | 触摸点数 | 非触摸设备 `0`；触摸设备 `5` 或 `10` | `navigator.maxTouchPoints`、`ontouchstart`、`TouchEvent`、`document.createTouch`、pointer capabilities |
| `width` | 屏幕宽 | `1920` / `1536` / `1366` | `screen.width`、`screen.availWidth`、`window.innerWidth`、`outerWidth`、`visualViewport.width` |
| `height` | 屏幕高 | `1080` / `864` / `768` | `screen.height` 及下面的偏移族 |
| `screen.colorDepth` | 色深 | 只接受 `24` / `30` / `32` | `screen.colorDepth`、`screen.pixelDepth`（两者共用一个值） |
| `screen.devicePixelRatio` | 系统缩放 | 只接受 `1` / `1.25` / `1.5` / `1.75` / `2` / `2.5` / `3` | `window.devicePixelRatio`、CSS `resolution`、`-moz-device-pixel-ratio` |

别名：`hardwareConcurrency` 也认 `HardwareConcurrency`；`touch.maxTouchPoints` 也认
`touch.max_touch_points` 和 `maxTouchPoints`。

**`width` / `height` 的语义很宽**，一个值同时喂多个 API，各带一个历史硬编码偏移：

| API | 返回值 |
|---|---|
| `screen.width` / `screen.availWidth` | `width` |
| `screen.height` | `height` |
| `screen.availHeight` | `height - 48`（任务栏） |
| `window.innerWidth` / `outerWidth` | `width` |
| `window.innerHeight` | `height - 141` |
| `window.outerHeight` | `height - 50` |
| `visualViewport.width` | `width - 15` |
| `visualViewport.height` | `height - 140` |

要精确模拟某组真实尺寸，得按这张表反推 `width` / `height`。小数、负数、0 一律视为
没配。

`navigator.deviceMemory` 不需要配——Firefox 不实现，缺席是对的。

**触摸只配 `touch.maxTouchPoints` 这一个 key。** `touch.enabled` / `touch_enabled` /
`touch.events.enabled` / `touch_events.enabled` 这些别名只被部分模块认，会配出矛盾
状态——实测只写 `touch.events.enabled=true` 时 Touch API 全部存在但
`navigator.maxTouchPoints` 是 0。只写 `touch.maxTouchPoints` 则四项全部正确。

### 4.3 Canvas

| key | 说明 | 取值 | 默认 |
|---|---|---|---|
| `canvas.mode` | 随机化模式 | `pixel` | `none`（不生效） |
| `canvas.seed` | 随机化种子 | 非零十进制 uint64 | 无 |
| `canvas.strength` | 扰动幅度（替换最低几位） | `low` / `medium` / `high` | `low` |
| `canvas.density` | 只扰动约 1/N 的像素 | 1 ~ 4096 | `64` |
| `canvas.preserveAlpha` | 保留 alpha 通道不动 | `true` / `false` | `true` |
| `canvas.preserveWhitePoint` | 保留纯白像素不动 | `true` / `false` | `true` |
| `canvas.pngMetadata` | 往 PNG 写随机化块 | `true` / `false` | `false` |

**`canvas.mode` 和 `canvas.seed` 必须同时给**，缺任一个都不生效。

生效后 `toDataURL`、`toBlob`、`getImageData` 都走扰动后的像素，同 seed 稳定复现。
`fillText` / `fillRect` 不需要单独配，结果通过上面三个出口被扰动。

**`canvas.pngMetadata` 保持关闭。** 实测：配了 `canvas.mode=pixel` 且不开它时，PNG
块序列是干净的 `IHDR,IDAT,IEND`；不带 fpfile 时反而会多一个 `deBG` 私有块。开它等于
主动加回一个标准浏览器不会有的块。

**`canvas.density` 一般不用改。** 改变 canvas hash 只需要改动任意像素，而逐像素加噪
会让 PNG 的行间预测失效，输出体积暴涨——那个体积差不需要任何参照物就能测出来。
实测同一幅画的 `toDataURL()` 长度：

| 画布 | 原生 | density=1 | density=64（默认） |
|---|---|---|---|
| 32×32 纯色 | 570 | 1838（3.2×） | 634（1.1×） |
| 120×40 纯色 | 686 | 6354（9.3×） | 954（1.4×） |
| 280×60 纯色 | 842 | 20334（**24×**） | 1834（2.2×） |
| 240×60 含文字渐变 | 6830 | 11590（1.7×） | 6986（1.02×） |

调大能进一步压体积，但**太稀会让小画布一个像素都不动**，等于原样交出真实指纹——
实测 `density=1024` 时 8×8 和 16×16 的输出与原生逐字节相同。所以代码里按画布面积
收紧密度，保证期望扰动数不低于 8 个像素；`density` 的上限也因此限制在 4096。

`measureText` 的度量值本身不扰动，字体指纹靠 §4.6 的白名单收口。

### 4.4 WebGL

这是整份清单里辨识度最高的单项指纹。**能力齐全，但必须整套配，半套比不配更显眼。**

字符串类：

| key | 说明 | 示例 |
|---|---|---|
| `webgl.vendor` | 掩码后的 VENDOR | `Mozilla` |
| `webgl.renderer` | 掩码后的 RENDERER | `Mozilla` |
| `webgl.unmasked_vendor` | `WEBGL_debug_renderer_info` 的 UNMASKED_VENDOR_WEBGL | `Google Inc. (NVIDIA)` |
| `webgl.unmasked_renderer` | UNMASKED_RENDERER_WEBGL，即显卡型号 | `ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0), or similar` |
| `webgl.version` / `webgl2.version` | VERSION | `WebGL 1.0` / `WebGL 2.0` |
| `webgl.glsl_version` / `webgl2.glsl_version` | SHADING_LANGUAGE_VERSION | `WebGL GLSL ES 1.0` / `WebGL GLSL ES 3.00` |
| `webgl.supported_extensions` | 扩展表，逗号或竖线分隔 | 见模板 |
| `webgl.compressed_texture_formats` | 压缩纹理格式，枚举名或数字 | `COMPRESSED_RGB_S3TC_DXT1_EXT,...` |

整数上限类（全部 `webgl.` 前缀）：

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

浮点类：`webgl.max_anisotropy`、`webgl.aliased_point_size_min` / `_max`、
`webgl.aliased_line_width_min` / `_max`。

shader 精度（值是三个整数 `rangeMin,rangeMax,precision`）：

```text
webgl.shader_precision.vertex.{low,medium,high}_{float,int}
webgl.shader_precision.fragment.{low,medium,high}_{float,int}
```

**怎么导出目标机型的真值**：在目标机器上用真实浏览器打开一个页面跑下面这段，把输出
贴进 fpfile。不要手编——`max_texture_size` 和 renderer 对不上是很容易被查出来的。

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

### 4.5 音频

| key | 说明 | 取值 |
|---|---|---|
| `audio.seed` | 音频扰动种子 | 非零十进制 uint64。不配 = 原生输出 |

没有 `audio.mode` / `audio.enabled`，**seed 存在即启用**。同 seed 稳定复现。

覆盖两个点：

1. `DynamicsCompressorNode` 的输出——经典 audio fingerprint
   （`oscillator → dynamicsCompressor → destination`）走的就是这条；
2. **离线渲染的输出 buffer**——覆盖任何不经过压缩器的图，例如
   `oscillator → destination` 直连。

第 2 点原来是缺口：实测两个不同 seed 与无 fpfile 三者的渲染 hash 逐字节相同。现在
直连路径也随 seed 变化且同 seed 稳定。

图里有压缩器时会被扰动两次，净效果同向 2 ULP，不会互相抵消，仍然确定性。代价是
渲染过程中 `AnalyserNode` 读到的值与最终 buffer 差 1 ULP，要发现这点需要脚本同时用
analyser 和 offline buffer 做逐样本对比。

**仍未覆盖**：实时 `AudioContext` 上的 `AnalyserNode`（非离线渲染）。

### 4.6 字体

| key | 说明 | 取值 |
|---|---|---|
| `fonts.whitelist` | 页面可见的字体 family 白名单 | 逗号或竖线分隔的 family 名 |
| `font_system` | 系统 UI 字体的平台风格 | `windows` / `linux` / `mac`（别名 `Font_System` / `font-system`） |

`fonts.whitelist` 非空即把名单外的**整个 family** 从字体表里滤掉，`measureText` 和
`getClientRects` 的 fallback 同时收口。

**必含通用族映射目标和 CJK 字体，漏了会产生比字体枚举更显眼的新指纹。** 实测
（本机，16px）：

| | 无白名单 | 名单只有拉丁字体 | 名单补全后 |
|---|---|---|---|
| `serif` 拉丁度量 | 32.00 | **29.35** | 32.00 |
| `sans-serif` | 29.83 | **29.35** | 29.83 |
| `monospace` | 32.00 | **29.35** | 32.00 |
| `sans-serif` 中文四字 | 64.00 | **52.00** | 64.00 |
| `<div>` offsetHeight | 21 | **18** | 21 |

三个通用族度量相同在真实 Windows 上不可能，中文掉到 13px 是可见的排版退化。除了要
暴露的字体，名单里必须另外包含：

```text
Times New Roman,Arial,Courier New,Consolas,MS Shell Dlg 2,Lucida Console
SimSun,NSimSun,Microsoft YaHei,Microsoft JhengHei,MS Gothic,Malgun Gothic
Segoe UI,Segoe UI Symbol,Segoe UI Emoji,Arial Unicode MS
```

**验收看这两条，不看名单里写了什么**：

1. `serif` / `sans-serif` / `monospace` 三者的拉丁度量互不相同；
2. 中文 16px 每字前进宽 = 16px（四字 = 64.00）。

注意 `document.fonts.check()` 对任何字体都返回 `true`（会 fallback），**不是**字体
存在性探测。验证白名单生效要看名单外的字体度量是否与不存在的字体一致——实测
`Gabriola` 被滤掉后与 `NoSuchFontXYZ` 同为 72.4833。

### 4.7 Navigator 属性一致性

| key | 别名 | 影响 |
|---|---|---|
| `useragent` | `userAgent`, `user-agent` | `navigator.userAgent`（主线程 + Worker）、HTTP `User-Agent` |
| `language` | 写成 `language:` 或 `language=` 都可以 | `navigator.language` / `languages`（主线程 + Worker）、HTTP `Accept-Language`、`Intl` 默认 locale |

**UA 里的版本号必须和实际构建版本一致。** 下面这些字段**改不了**，全是上游 Firefox
原样输出，会和 UA 互相印证：

| API | 值 | 说明 |
|---|---|---|
| `navigator.platform` | `Win32` | 硬编码 |
| `navigator.vendor` | `""`（空串） | Firefox 恒空 |
| `navigator.vendorSub` | `""` | 恒空 |
| `navigator.product` | `Gecko` | 硬编码 |
| `navigator.productSub` | `20100101` | 硬编码 |
| `navigator.appName` | `Netscape` | 硬编码 |
| `navigator.appCodeName` | `Mozilla` | 硬编码 |
| `navigator.appVersion` | `5.0 (Windows)` | 由平台推导 |
| `navigator.oscpu` | `Windows NT 10.0; Win64; x64` | 由平台推导 |
| `navigator.buildID` | `20181001000000` | 上游为反指纹冻结的固定值 |
| `navigator.doNotTrack` | `unspecified` | 未开启 DNT 时 |
| `navigator.plugins` | 5 个 PDF viewer 别名 | W3C 规定的固定列表，Chrome 也一样 |
| `navigator.mimeTypes` | 2 项 | 同上 |
| `navigator.deviceMemory` | 缺席 | Firefox 不实现 |
| `navigator.userAgentData` | 缺席 | Firefox 不实现 |

`language` 的优先级：fpfile > `BrowsingContext.languageOverride` > `intl.accept_languages`
pref。JS 侧和 HTTP 侧用同一优先级，所以 `navigator.languages` 和 `Accept-Language`
不会给出不同语言。

HTTP `Accept-Language` 的 q 值由上游算法生成，`en-US,en` 会变成 `en-US,en;q=0.9`
（Chrome 风格递减，上游 Bug 2000765 起）。**这是真 Firefox 行为，不是破绽。**

### 4.8 WebRTC / 媒体 / 权限 / 传感器

| key | 说明 | 取值 |
|---|---|---|
| `webrtc.ice_proxy_only` | ICE 只走代理 | `true` / `false`。走代理时**应设 true** |
| `media.devices` | `enumerateDevices()` 返回的设备清单 | `audioinput` / `audiooutput` / `videoinput`，逗号分隔，重复表示多台 |
| `local_webrtc_ipv4` / `local_webrtc_ipv6` | 替换本地 IP | IP 字面量 |
| `public_webrtc_ipv4` / `public_webrtc_ipv6` | 替换公网 IP | IP 字面量 |
| `webrtc_ip_mismatch_policy` | IP 不匹配时的策略 | `passthrough` 或留空 |

**`webrtc.ice_proxy_only=true` 是走代理时的必配项**，否则 STUN 可能直连，srflx
candidate 会独立暴露真实出口 IP。设为 `true` 后 srflx candidate 消失。

本地内网 IP 默认不泄露——mDNS 混淆会把 host candidate 变成 `<uuid>.local`，实测有效。
`local_webrtc_*` 一般不需要。

`media.devices` 的 `deviceId` / `groupId` / `label` 一律留空，与真 Firefox 未授权态
一致。已授权摄像头/麦克风的场景下也返回空 label，需要那种行为就不要配这个 key。

语音列表：

| key | 说明 |
|---|---|
| `speech.voices.local` | 本地语音名列表，**竖线 `|` 分隔** |
| `speech.voices.remote` | 远程语音名列表，同上 |
| `speech.voices.local.langs` | 与 local 一一对应的 BCP47 语言 |
| `speech.voices.remote.langs` | 与 remote 一一对应 |
| `speech.voices.default.name` | 默认语音名 |
| `speech.voices.default.lang` | 默认语音语言 |

**这几个列表只按 `|` 分割**，因为真实语音名里含逗号，例如
`Microsoft Server Speech Text to Speech Voice (en-US, AriaNeural)`。

每个语音**必须有 lang**（从 `.langs` 或 `.default.lang` 得到），否则该语音被跳过并打
警告。`voiceURI` 形如 `urn:moz-tts:sapi:<name>?<lang>`，与真机输出同形。

**不需要配的**（真 Firefox 原生行为已经是对的）：

| API | 现状 | 说明 |
|---|---|---|
| `navigator.getBattery` | 缺席 | Firefox 已移除，Chrome 才有 |
| `navigator.getGamepads` | 存在，返回空数组 | 无手柄时的正常结果 |
| `navigator.requestMIDIAccess` | 存在 | 正常 |
| `navigator.permissions.query` | 全部返回 `prompt` | 干净 profile 的正常状态 |

地理位置（一组独立开关，默认全关）：

| key | 说明 | 取值 |
|---|---|---|
| `geolocation.enabled` | 总开关，**必须为 true 其余才生效** | `true` |
| `geolocation.latitude` | 纬度（必填） | -90 ~ 90 |
| `geolocation.longitude` | 经度（必填） | -180 ~ 180 |
| `geolocation.accuracy` | 精度米（必填） | > 0 |
| `geolocation.altitude` | 海拔（选填） | 任意 |
| `geolocation.altitudeAccuracy` | 海拔精度（选填，需先给 altitude） | ≥ 0 |
| `geolocation.heading` | 朝向（选填，需 speed > 0） | 0 ~ 360 |
| `geolocation.speed` | 速度（选填） | ≥ 0 |
| `geolocation.timestamp` | 时间戳（必填） | `now` 或十进制毫秒 |
| `geolocation.permission` | 权限状态 | `prompt`（默认）/ `granted` / `denied` |

**地理位置必须和出口 IP、时区自洽**，否则比不配更糟。

### 4.9 时区 / 语言

| key | 别名 | 说明 | 取值 |
|---|---|---|---|
| `timezone` | `TimeZone`, `TIMEZONE` | IANA 时区名 | `America/New_York`、`Asia/Singapore`、`Europe/London` |

影响 `Intl.DateTimeFormat().resolvedOptions().timeZone`、`Date.prototype.getTimezoneOffset()`
及所有依赖默认时区的 Intl 构造器，主线程和 Worker 一致。仅 Windows 生效。

语言见 §4.7 的 `language`。

`Date.now` / `performance.now` 的精度**不需要配**，上游已 clamp 到 1ms。

### 4.10 反 Hook / 原型链探测 —— 无需配置

所有指纹值都在 C++ 原生 getter 内部改写，不包装任何 JS 函数。所以 getter 的
`toString()` 返回真实的 `[native code]`，属性描述符的 `enumerable` / `configurable`
形状不变，`Object.getOwnPropertyNames` 的成员和顺序不变。

**这一层不需要也不应该配任何东西。**

---

## 5. 网络与认证

不是指纹，但同一个文件承载。

| key | 说明 |
|---|---|
| `httpauth.username` / `httpauth.password` | HTTP Basic Auth 与 HTTP 代理 407 的凭据 |
| `socksauth.host` / `.port` / `.username` / `.password` | SOCKS5 认证 |
| `<host>:<port>:<user>:<pass>` | SOCKS5 一行式，要求正好 3 个冒号 |
| `proxy.rotate.enabled` | 代理轮换开关，`true` / `false`（别名 `httpproxy.rotate.enabled`） |
| `proxy.rotate.exhausted` | 耗尽策略，`wrap` 循环；`direct` / `none` / `stop` 不循环 |
| `proxy.rotate.proxy` | HTTP 代理轮换项，`http://host:port:user:pass`（别名 `httpproxy.rotate.proxy`、`httpproxy.proxy`） |
| `socks5proxy.rotate.proxy` | SOCKS5 代理轮换项，`socks5://host:port:user:pass` |

SOCKS5 需配合 profile 的 `network.proxy.socks`、`network.proxy.socks_port`、
`network.proxy.socks_version=5`。代理轮换按 userContext 分配。

WebGPU（key 很多，按需查源码 `dom/webgpu/Adapter.cpp`）：

| key | 说明 |
|---|---|
| `webgpu.enabled` | 总开关（别名 `webgpu_enabled`） |
| `webgpu.features` | adapter features 列表 |
| `webgpu.wgslLanguageFeatures` | WGSL language features |
| `webgpu.preferredCanvasFormat` | `rgba8unorm` / `bgra8unorm` |
| `webgpu.vendor` / `.architecture` / `.device` / `.description` | adapter info |
| `webgpu.limits.<name>` | 各项 limit（别名前缀 `webgpu.limit.` / `webgpu.`） |

---

## 6. 必填清单

不配这些等于泄露真机：

| key | 不配的后果 |
|---|---|
| **整套 `webgl.*`** | 真显卡厂商和型号原样输出。这是辨识度最高的单项指纹 |
| `touch.maxTouchPoints` | 真触摸能力泄露；跨进程还可能不一致 |
| `width` / `height` | 真屏幕分辨率 |
| `hardwareConcurrency` | 真 CPU 核数 |
| `timezone` | 真时区，且与出口 IP 可能矛盾 |
| `language` | 真语言 |
| `canvas.mode` + `canvas.seed` | Canvas 不随机化 |
| `audio.seed` | 音频不扰动 |
| `fonts.whitelist` | 真实已装字体可被 `measureText` 枚举 |
| `webrtc.ice_proxy_only`（走代理时） | srflx candidate 暴露真实出口 IP |

按需：`screen.colorDepth`、`screen.devicePixelRatio`（宿主开了系统缩放时**必配**）、
`media.devices`、`speech.voices.*`、`geolocation.*`。

---

## 7. 配完自检

```bat
set MOZ_LOG=fpfile:5
```

1. 日志里**不该有** `missing key` 行；
2. 不该有 `is not an unsigned integer` / `is not a boolean` / `is not a number` 行；
3. `serif` / `sans-serif` / `monospace` 三者拉丁度量互不相同；
4. 中文 16px 四字宽 = 64.00；
5. `navigator.maxTouchPoints` 与 `ontouchstart` 不矛盾；
6. `RTCIceCandidate.candidate` 里没有 `typ srflx`；
7. 主线程与 Worker 的 `navigator.languages` 一致；
8. `window.devicePixelRatio` 与 `matchMedia('(resolution: Xdppx)')` 一致；
9. WebGL 的 `UNMASKED_RENDERER_WEBGL` 是配置值而非真显卡。

---

## 8. 已知限制

**不能伪装成 Chrome。** `platform` / `vendor` / `productSub` / `oscpu` / `buildID` /
`appVersion` 都是上游 Firefox 原样，fpfile 改不了。把 `useragent` 填成 Chrome 会立刻
在五处矛盾：`vendor` 为空串（Chrome 是 `Google Inc.`）、`productSub` 是 `20100101`
（Chrome 是 `20030107`）、`oscpu` 和 `buildID` 在 Chrome 上不存在、
`userAgentData` 和 `deviceMemory` 缺席。

**出口 IP / 时区 / 语言 / 地理位置的自洽性由使用者保证**，代码层只能各自按配置输出。

**实时 `AudioContext` 上的 `AnalyserNode` 未扰动**（离线渲染与压缩器路径已覆盖，
见 §4.5）。

**`measureText` 的度量值本身未扰动**（见 §4.3）。

**Canvas PNG 体积仍略高于原生**（默认密度下最坏约 2.2×，见 §4.3）。这是逐像素扰动
的结构性代价，只能压小、不能消除。
