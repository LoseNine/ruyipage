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
ctx.apply_emulation(page)            # 内核 + BiDi 仿真双层覆盖
page.get("https://browserleaks.com/webgl")
```

## 文件结构

| 文件 | 说明 |
| ---- | ---- |
| `builder.py` | 全部实现：geo 探测、指纹组合、fpfile 写入、`apply_smart_fingerprint` |
| `data/fingerprints.json` | 22 套 Windows 真机硬件特征（NVIDIA / AMD / Intel） |
| `data/region_locales.json` | 30 国 + `_default` 的语言 / Accept-Language / 微软语音映射 |
| `__init__.py` | 子包公开 API |

## 公开 API（`from ruyipage import ...` 即可）

- `apply_smart_fingerprint(opts, ...) -> FingerprintContext` — 一站式入口
- `FingerprintContext` — `summary()` / `apply_emulation(page)` / `to_dict()`
- 低层组件：`fetch_geo_info` / `fetch_public_ipv6` / `pick_fingerprint`
  / `write_fpfile` / `build_proxies_dict` / `list_hardware_profiles` /
  `get_country_profile`
- 数据契约：`GeoInfo` / `WebGLProfile` / `HardwareProfile` /
  `CountryProfile` / `FingerprintProfile`
- 异常体系：
  - `FingerprintError`
    - `FingerprintConfigError` — 内置 JSON 损坏
    - `GeoError` — 5 个 geo 数据源全部失败
      - `CountryMismatchError` — `actual` / `required` 国家码不一致

## 设计要点

- **内核 + BiDi 仿真双层防御**：`fpfile.txt` 控制 navigator / WebGL /
  WebRTC 等核心字段；`ctx.apply_emulation()` 再叠加 geolocation / locale
  / timezone / Accept-Language。
- **5 数据源回退**：geojs → ipapi → ipwho → ip-api → ipinfo；任一成功即返。
  `require_country` 不匹配立即终止（同一出口 IP 无须再问其他源）。
- **IPv6 best-effort**：失败时直接省略 `*_webrtc_ipv6` 行，绝不写入伪造值。
- **原子写入**：`tmp + os.replace`，UTF-8 + LF，严格 `key:value` 顺序。
- **可注入随机源**：所有抽样接 `rng=random.Random(...)`，便于测试复现。
- **硬件池仅 Windows**：避免与 `font_system:windows` / `navigator.platform`
  冲突。
