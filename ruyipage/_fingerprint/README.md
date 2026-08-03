# ruyipage._fingerprint — 智能指纹子系统

构建在 [`firefox-fingerprintBrowser`](https://github.com/LoseNine/firefox-fingerprintBrowser) 内核之上的一站式指纹方案。

## 一句话用法

```python
from ruyipage import FirefoxOptions, FirefoxPage

opts = FirefoxOptions().set_port(9222)
opts.set_browser_path(r"C:/Program Files/Mozilla Firefox/firefox.exe")

ctx = opts.smart_fingerprint(
    proxy_host="proxy.example.com", proxy_port=8080,
    proxy_user="u", proxy_pwd="p",
    require_country="US",
    logger=print,
)

page = FirefoxPage(opts)
ctx.apply_emulation(page)            # 顺序必须是 ctx -> page -> ctx.apply_emulation(page)
page.get("https://browserleaks.com/webgl")
```

`ctx.apply_emulation(page)` 返回的结果包含 `screen`、`geolocation`、`locale`、
`timezone`、`headers`。

异步页面使用 `await ctx.apply_emulation_async(async_page)`；该入口始终返回可等待
对象，即使全部覆盖项都已关闭。

## 约定

- `apply_smart_fingerprint()` 默认不设置外部窗口。
- 默认向 Options 加入 `about:blank` 启动页，使创建页面后可立即应用 BiDi 覆盖，
  无需 `remote-allow-system-access`；已有可执行脚本的自定义启动页时传
  `set_startup_page_on_opts=False`。
- `fpfile` 不再写 `width` / `height`，避免指纹内核把屏幕尺寸拿去启动窗口。
- `set_window_size_on_opts` 仅为兼容保留且已忽略；确需外窗尺寸时由调用方
  在创建 `FirefoxPage` 前显式调用 `opts.set_window_size(width, height)`。
- `ctx.apply_emulation(page)` 默认通过
  `page.emulation.set_screen_size(hw.width, hw.height)` 只覆盖 `screen.width` /
  `screen.height` / `screen.avail*`；`outerWidth` / `innerWidth` / viewport 继续由
  Firefox 原生维护并随窗口变化。
- 不做 15/92、16/93 或其他生产坐标补偿；16/93 仅用于目标指纹浏览器实机验证。

## 文件结构

| 文件 | 说明 |
| ---- | ---- |
| `builder.py` | 全部实现：geo 探测、指纹组合、fpfile 写入、`apply_smart_fingerprint` |
| `data/fingerprints.json` | 22 套 Windows 真机硬件特征（NVIDIA / AMD / Intel） |
| `data/region_locales.json` | 30 国 + `_default` 的语言 / Accept-Language / 微软语音映射 |
| `data/iana_timezones.json` | 与 Firefox 155 ICU 对齐的 IANA 时区及别名集合 |
| `__init__.py` | 子包公开 API |

## 公开 API（`from ruyipage import ...` 即可）

- `apply_smart_fingerprint(opts, ...) -> FingerprintContext` — 一站式入口
- `FingerprintContext` — `summary()` / `apply_emulation(page)` /
  `apply_emulation_async(page)` / `to_dict()`
- 低层组件：`fetch_geo_info` / `fetch_public_ipv6` / `pick_fingerprint`
  / `write_fpfile` / `build_proxies_dict` / `list_hardware_profiles` /
  `get_country_profile`
- 数据契约：`GeoInfo` / `GeolocationProfile` / `WebGLProfile` /
  `HardwareProfile` / `CountryProfile` / `FingerprintProfile`
- 异常体系：
  - `FingerprintError`
    - `FingerprintConfigError` — 内置 JSON 损坏
    - `GeoError` — 10 个 geo 数据源全部失败
      - `CountryMismatchError` — `actual` / `required` 国家码不一致

## 设计要点

- **内核 + BiDi 仿真双层防御**：`fpfile.txt` 控制 navigator / WebGL /
  WebRTC 等核心字段；`ctx.apply_emulation()` 再叠加 geolocation / locale
  / timezone / Accept-Language。
- **10 数据源回退**：任一 Geo 数据源成功即返回，其余数据源仅在失败时继续尝试。
  `require_country` 不匹配立即终止（同一出口 IP 无须再问其他源）。
- **UA 主版本**：优先读取 `opts.browser_path` 对应 Firefox 的实际主版本；只有
  可执行文件无法查询时才回退到内置基准版本，不再随机抖动主版本。
- **Geolocation**：经纬度、精度、海拔、海拔精度、航向和速度在 fpfile 与 BiDi
  之间保持一致。数字 `geolocation_timestamp` 使用 Unix epoch 毫秒，并与
  `prompt/denied` 权限状态一样由启动内核管理；BiDi 不会用不完整字段覆盖它。
- **WebRTC**：自动配置默认省略 `local/public_webrtc_*` 覆盖字段，让 Firefox
  使用原生 ICE；只有显式传入 `webrtc_local_ipv4/ipv6` 或
  `webrtc_public_ipv4/ipv6` 时才写入，并严格校验地址族。代理 geo IP 不会被
  当作 ICE 地址。原生 ICE 仍可能暴露不同于 HTTP 代理出口的直连 srflx 地址；
  `local_webrtc_*` 只控制匹配 host 地址的字面量暴露，不会合成地址，也不会
  筛除其他 host candidate。
- **IPv6 best-effort**：IPv6 出口探测失败时不写入伪造值；显式 WebRTC IPv6 覆盖仍需由调用方提供真实地址。
- **原子写入**：`tmp + os.replace`，UTF-8 + LF，严格 `key:value` 顺序。
- **可注入随机源**：所有抽样接 `rng=random.Random(...)`，便于测试复现。
- **硬件池仅 Windows**：避免与 `font_system:windows` / `navigator.platform`
  冲突。
