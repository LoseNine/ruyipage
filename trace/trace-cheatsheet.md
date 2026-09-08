# Ruyitrace DOMTrace 开关速查

本文列 `MOZ_DOM_*` 环境变量：取值、默认值、作用、生效条件；§15 附 JSCall 二进制输出的
解码速查。完整的输出文件布局、记录字段、因果链查法、性能实测和故障排查都在
[README.md](README.md)。

代码里实际读取的开关共 63 个，本文全部覆盖。核对脚本 `audit_switches.py` 需要扫内核 C++ 源码，随内核仓库的 `dom/bindings/domtrace/` 提供，不在本目录。

## 0. 使用规则

**必须在 Firefox 启动前设置。** 配置在进程启动或功能首次使用时缓存，运行中修改不生效。
运行中只有 `MOZ_DOM_TRACE_GATE` 能切换采集。

```powershell
$env:MOZ_DOM_TRACE = "1"
$env:MOZ_DOM_TRACE_FILE = "D:\trace\run.jsonl"
& "D:\path\to\firefox.exe" --new-instance -no-remote -profile "D:\profile"
```

**附属参数不能单独启用功能。** 只设 `MOZ_DOM_JSCALL_OPCODE_LIMIT` 不会打开 Opcode，
必须先满足该功能的基础开关。

**`MOZ_DOM_TRACE=0` 不是总关闭开关。** 它关掉核心 DOMTrace 及跟随核心落点的模块
（Eval、Event、Descriptor、WASM、Exception），但 **JSCall、HTTP packet、WebSocket、
API override 都是独立启用的，不受它影响**。所以 `MOZ_DOM_TRACE=0` +
`MOZ_DOM_JSCALL_TRACE=1` 就是“只抓函数调用”的正确写法。要全部停用见 §14。

URL 与函数名过滤统一为**逗号或分号分隔的子串**，大小写敏感，不是通配符或正则。
唯一例外是 `MOZ_DOM_TRACE_UNFILTER`，它按成员名**精确匹配**。

## 1. 核心 DOMTrace

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_TRACE` | `1` 开；`0` 关；默认不设 | 核心主开关。`0` 同时关闭 Eval、Event、Descriptor、WASM、Exception | 核心功能 |
| `MOZ_DOM_TRACE_FILE` | 路径；默认空 | 总路径锚点，同时启用核心。按父目录、扩展名、模块子目录和 PID 派生输出 | 建议与 `MOZ_DOM_TRACE=1` 同设 |
| `MOZ_DOM_TRACE_ASYNC` | `1` 开；`0` 同步回退；默认开 | 核心与 Eval/Cookie/Storage/Descriptor/Event/Wasm/Exception 使用后台 writer | 核心已启用 |
| `MOZ_DOM_COOKIE_TRACE_FILE` | 路径；默认由总锚点派生 | 覆盖 Cookie 输出位置；单独设非空值也可启用 Cookie 输出 | 无总锚点且无此值时不生成该文件 |
| `MOZ_DOM_STORAGE_TRACE_FILE` | 路径；默认由总锚点派生 | 覆盖 Storage 输出位置；单独设非空值也可启用 | 同上 |
| `MOZ_DOM_WASM_TRACE_FILE` | 路径；默认由总锚点派生 | 覆盖 WASM 输出位置；单独设非空值也可启用 | 同上 |
| `MOZ_DOM_TRACE_LIMIT` | 非负整数；默认 `0` | 核心记录条数上限，`0` 不限。JSCall/Opcode/Exception 各有自己的 limit | 核心已启用 |
| `MOZ_DOM_TRACE_OPERATORS` | `0` 关；默认开 | 仅关闭 `typeof`、`instanceof`、RegExp operator 记录及其 JIT hook | 核心已启用 |
| `MOZ_DOM_TRACE_PTYPE` | ASCII 标签；默认 `parent`；≤63 字节 | 设日志里的 `process_type`，不参与文件名。勿用引号、反斜杠、控制字符 | 核心已启用 |
| `MOZ_DOM_TRACE_RUN_ID` | 字符串；默认 `<pid>-<timestamp_ms>`；≤127 字节 | 同一次运行的关联 ID。非字母数字 `_` `-` 的字符替换为 `_`，不参与文件名 | 核心已启用 |
| `MOZ_DOM_TRACE_INTERNAL` | `1` 开；默认关 | 包含 `chrome://`、`resource://`、`about:`、RemoteAgent 等浏览器内部噪声 | 核心已启用 |
| `MOZ_DOM_TRACE_UNFILTER` | 逗号或分号分隔的成员名；默认空 | 取消默认成员黑名单。黑名单为 `requestAnimationFrame`、`cancelAnimationFrame`、`clearTimeout`、`clearInterval`、`getComputedStyle`，默认它们一条记录都不产生且输出中没有任何提示 | 核心已启用 |

## 2. Gate 运行时开关

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_TRACE_GATE` | 控制文件路径；默认空 | 进入受控模式：控制文件存在时接收记录，删除时停止，每次开关产生递增 session | 已配置至少一个日志 Trace；不控制 API override |
| `MOZ_DOM_TRACE_GATE_POLL_MS` | `20..5000`；默认 `200` | 检查控制文件的间隔（毫秒），非法值保留默认 | 已设 `MOZ_DOM_TRACE_GATE` |

Gate 只控制“当前是否接收记录”，不改变“记录什么”，也不重置任何 limit。

## 3. 事件

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_EVENT_TRACE_FULL` | `1` 开；默认关 | 对 14 类原生事件记录全部 `event_dispatch`（鼠标、指针、键盘、触摸、滚轮） | 核心已启用且 `event` 路径存在 |

## 4. 宿主环境探测

这四个是补环境专用层，都默认关闭。前三个记录**页面要了什么**，按集合去重（记录量取决于页面探测
了多少个不同的名字，不取决于探测多少次）；第四个记录**构建提供了什么**，一次性导出。

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_ABSENT_PROBE` | `1` 开；默认关 | 记录页面读过但**不存在**的宿主属性名（全局、`navigator`/`screen`、`document`） | 核心已启用 |
| `MOZ_DOM_IFACE_PROBE` | `1` 开；默认关 | 记录页面探测过且**存在**的 WebIDL 接口对象，带 `state`：`present` 为本构建提供，`disabled` 为本构建认识但已关闭。与上一条互补——一个记不存在的、一个记存在的 | 核心已启用 |
| `MOZ_DOM_HOST_MUTATION` | `1` 开；默认关 | 记录页面在宿主对象上**装了/删了**什么、改了谁的原型 | 核心已启用 |
| `MOZ_DOM_ENV_INVENTORY` | `1` 开；默认关 | 一次性导出 `env\env_inventory_<pid>_<session>.json`（约 1.2 MB）：本构建对该页面所在 scope 暴露的全部接口。每个接口分 `statics` / `proto` / `instance` 三个 target，各带属性种类、**定义顺序**、`nonEnumerable` / `nonConfigurable` / `readonly` 和 `nargs`；接口级带 present/disabled 状态。只读 codegen 静态表，不创建接口对象也不枚举全局 | 核心已启用；每进程一次，页面跑起来后零成本 |

`ABSENT_PROBE` 与 `HOST_MUTATION` 建议配对使用：单开前者时，一次属性安装只会显示成
“问了个不存在的属性”。

`ENV_INVENTORY` 是前三层的另一个操作数：没有它，「页面没问过 `X`」和「`X` 在这儿不存在」在日志里
无法区分。它按 scope 求值而非按构建——同一次运行里非安全上下文的进程会多出 107 个 `disabled`
接口（`Cache`、`Clipboard`、`CryptoKey`、`GPU`、WebAuthn 一族）。详见 [README.md](README.md) §1.0.4。

## 5. Exception

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_EXCEPTION_TRACE` | `1` 开；默认关 | Exception 主开关 | `MOZ_DOM_TRACE=0` 会压制它 |
| `MOZ_DOM_EXCEPTION_TRACE_FILE` | 路径；默认由总锚点派生，无锚点时用内置备用路径 | 路径锚点；非空时也会启用 Exception | 同上 |
| `MOZ_DOM_EXCEPTION_LIMIT` | 非负整数；默认 `50000` | 记录上限，`0` 不限，命中后写 `limit_reached` | Exception 已启用 |
| `MOZ_DOM_EXCEPTION_FLUSH_INTERVAL` | `0..1000000`；默认 `256` | 每多少条刷新一次，`0` 关闭周期刷新。进程可能被外部强杀时降到 `8` | Exception 已启用 |
| `MOZ_DOM_EXCEPTION_STACK_FRAMES` | `1..64`；默认 `8` | 每条异常最多保存多少调用栈帧 | Exception 已启用 |
| `MOZ_DOM_EXCEPTION_MAX_STRING_CHARS` | `256..131072`；默认 `4096` | message、文件名、字符串预览的字符上限 | Exception 已启用 |

## 6. JSCall：启用与数量控制

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_JSCALL_TRACE` | `1` 开；默认关 | JSCall 主开关 | 独立启用，不受 `MOZ_DOM_TRACE=0` 影响 |
| `MOZ_DOM_JSCALL_TRACE_FILE` | 路径；默认由总锚点或内置路径派生 | 路径锚点；非空时也会启用 JSCall | 同上 |
| `MOZ_DOM_JSCALL_LIMIT` | 非负整数；默认 `0`（不限） | 普通调用的 `call_id` 上限，`0` 不限。detail 命中可绕过它 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_PER_FUNC_LIMIT` | 非负整数；默认 `0`（不限） | 每个函数各记前 N 条，之后只计数不落记录。这是让「全量记录 native」在红线内的关键：JS 层字节码 VM 靠少数 builtin 派发数百万次（实测 `load`/`store`/`add` 各 600 万/230 万/340 万次），封顶后它们塌缩成计数，其余函数记录与调用树完整保留。塌缩函数的 `jscall_func_stats` 里 `calls` > `records`，给出真实调用数 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_DURATION` | `0` 关；默认开 | 记录每次调用耗时 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_FUNC_STATS` | `0` 关；默认开 | 诊断用。关闭每函数汇总计数器（`records`/`tiers`/`warmup`）。不建议常关：关掉后无法区分记录流被 JIT 截断和函数真的只跑了几次 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_BASELINE` | `1` 开；默认关 | 扩展到 Baseline 编译帧。**存在已知配对缺陷**（生成器恢复、OSR、异常展开可能配不上），会污染 `origin_call_id`，非必要不要开 | JSCall 已启用 |

JSCall 固定使用后台批量写入器，没有同步回退开关。

## 7. JSCall：记录范围

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_JSCALL_SCRIPT_URL` | URL 子串；默认空 | 只记录 URL 命中的调用。只决定“记不记”，不抓参数和返回值 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_NATIVE` | `1` 开；默认关 | 设了脚本过滤器时仍记录 native/builtin。**不设过滤器时 native 本来就记录**，此开关只在有过滤器时有意义 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_SOURCE` | `1`=全部；或 URL 子串；默认不设 | 把执行过的每个脚本源码存为 `eval\script_<sha256>_<字节数>.js`。按执行而非编译记录 | JSCall 已启用，且 `MOZ_DOM_TRACE` 不为 `0`（复用 Eval 写入器） |

设了 `MOZ_DOM_JSCALL_DETAIL_NATIVE` 而未设 `MOZ_DOM_JSCALL_NATIVE` 时会自动打开并在 stderr
说明，否则 native 在分配 func id 之前就被丢掉，detail 过滤器一条都匹配不到。

`MOZ_DOM_JSCALL_SOURCE` 建议给 URL 过滤而不是 `1`：不加过滤会连浏览器自身约 879 个
`chrome://` 脚本一起存下来。

## 8. JSCall：参数、返回值与闭包

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL` | URL 子串；默认空 | URL 命中就抓该函数的参数、`this` 和返回值 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_DETAIL_NATIVE` | 函数名子串；默认空 | 按 native 函数名命中。对象参数只记身份不记内容 | JSCall 已启用 |
| `MOZ_DOM_JSCALL_MAX_VALUE_BYTES` | `256..16777216`；默认 `65536` | 单个值的字节上限，超出截断并标记 `truncated` | 已配置 detail |
| `MOZ_DOM_JSCALL_CLOSURE_URL` | URL 子串；默认空 | 命中脚本的函数在入口快照其**闭包捕获的变量名与值**。解密壳读的字符串表从不跨调用边界，只有这一层拿得到 | 核心已启用 |
| `MOZ_DOM_JSCALL_CLOSURE_MAX_BYTES` | `256..8388608`；默认 `262144` | 单个绑定序列化的字节上限。上千项的表要给到 2 MB | 已配置 closure |

native 的函数名过滤不带命名空间，`load` 会连带命中浏览器自身任何叫 `load` 的 native。

## 9. Opcode（高开销）

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_JSCALL_OPCODE_URL` | 脚本 URL 子串；默认空 | Opcode 总开关，对命中脚本逐条字节码记录 | JSCall 已启用；必须尽量锁定单个脚本 |
| `MOZ_DOM_JSCALL_OPCODE_LIMIT` | 非负整数；默认 `0` | Opcode 条数上限，`0` 不限 | Opcode 已启用；强烈建议设非零值 |

记录含 `call_id`、`func_id`、脚本内偏移和 opcode 字节，**不含**立即数解码、PC 窗口筛选和栈值捕获。

## 10. WASM

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_WASM_DUMP` | `1` 开；默认关 | 把原始模块字节存为 `wasm_<sha256>_<length>.wasm`，同内容按文件名去重 | 核心已启用且 WASM 路径存在 |
| `MOZ_DOM_WASM_DUMP_MAX` | 字节数；默认 `8388608` | 单个模块保存上限（8 MiB），超限不保存该模块 | `WASM_DUMP=1` |
| `MOZ_DOM_WASM_DUMP_TOTAL` | 字节数；默认 `134217728` | 本进程累计保存上限（128 MiB），超限后不再保存 | `WASM_DUMP=1` |
| `MOZ_DOM_WASM_INSN` | `1` 开；默认关 | 逐函数静态反汇编，产生 `wasm_insn` 文本记录。**不是逐次执行的指令 trace** | 核心已启用且 WASM 路径存在 |
| `MOZ_DOM_WASM_INSN_FUNC_INDEX` | 有符号整数；默认 `-1` | 只处理指定函数索引，`-1` 为全部 | `WASM_INSN=1` |
| `MOZ_DOM_WASM_INSN_MAX_FUNCS` | 非负整数；默认 `0` | 本进程累计最多反汇编多少个函数，`0` 不限 | `WASM_INSN=1` |
| `MOZ_DOM_WASM_INSN_MAX_BYTES` | 字节数；默认 `1048576` | 单个函数反汇编上限（1 MiB），`0` 不限 | `WASM_INSN=1` |
| `MOZ_DOM_WASM_CALL` | `1` 开；默认关 | 每次跨 JS/wasm 边界记一条 `wasm_call`，含按签名定型的参数与返回值 | 核心已启用且 WASM 路径存在 |
| `MOZ_DOM_WASM_MEM_BYTES` | 字节数；默认 `256`，上限 `65536`，`0`=不抓 | 每个「指向内存」的 i32 参数抓多少字节线性内存，enter 与 leave 各一次 | `WASM_CALL=1` |
| `MOZ_DOM_WASM_CALL_MAX` | 非负整数；默认 `0` | 进程内边界记录条数上限，`0` 不限 | `WASM_CALL=1` |

`WASM_DUMP` 与 `WASM_INSN` 都是编译期一次性动作，运行时零开销。

## 11. HTTP packet

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_HTTP_PACKET_TRACE` | 变量存在且值不是 `0` 即开；默认关 | 记录 HTTP 请求/响应、body、时序和发起栈。推荐明确写 `1` | 独立于核心 DOMTrace |
| `MOZ_DOM_HTTP_PACKET_TRACE_DIR` | 目录路径；默认先由 `MOZ_DOM_TRACE_FILE` 派生，再退到内置目录 | 指定 `*.http_packet.json` 和 `index.jsonl` 的输出目录 | HTTP packet 已启用 |
| `MOZ_DOM_HTTP_PACKET_TRACE_MAX_PENDING` | `1..8192`；默认 `256` | 待写 packet 数量上限，满载时记 `dropped_full` | HTTP packet 已启用 |

## 12. WebSocket

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_WS_TRACE` | `1` 开；默认关 | 在网络层记录收发帧，输出 `websocket_frames.jsonl` | 建议同时设总锚点或 HTTP 输出目录 |
| `MOZ_DOM_WS_ASYNC` | `1` 开；`0` 同步回退；默认开 | 帧与握手使用有界后台 writer；正常退出后检查 `websocket_writer_stats` | WebSocket 或 HTTP packet 已启用 |
| `MOZ_DOM_WS_MAX_BYTES` | `256..16777216`；默认 `65536` | 单帧最多保存多少 payload 字节，超出保留原长度并标记截断 | `MOZ_DOM_WS_TRACE=1` |

## 13. API 返回值覆盖

这组**直接修改页面观察到的返回值**，不是日志，与 `MOZ_DOM_TRACE` 和 Gate 都独立。
只设子项而不设 `MOZ_DOM_API_OVERRIDE=1` 不生效；子项值无法解析时该 API 保持 native。

| 开关 | 值与默认值 | 作用 | 生效条件 |
| --- | --- | --- | --- |
| `MOZ_DOM_API_OVERRIDE` | `1` 开；默认关 | 总开关 | 至少一个子项为有效的非 `native` 模式 |
| `MOZ_DOM_API_DATE_NOW` | `native`、`fixed:<ms>`、`increment:<start>:<step>`、`sequence:<v1>,<v2>`；默认 `native` | 控制 `Date.now()`、`new Date()`、`Date()` | `API_OVERRIDE=1` |
| `MOZ_DOM_API_PERFORMANCE_NOW` | 同 Date；默认 `native` | 控制 `performance.now()`，结果保持非负且不倒退 | `API_OVERRIDE=1` |
| `MOZ_DOM_API_MATH_RANDOM` | `native`、`seeded:<seed>`；默认 `native` | 控制 `Math.random()`。**只支持 `seeded:`**，`fixed:` / `sequence:` 已移除，设了会被拒绝并在 stderr 警告 | `API_OVERRIDE=1` |
| `MOZ_DOM_API_CRYPTO_RANDOM_VALUES` | `native`、`seeded:<seed>`、`increment:<0..255>`、`pattern:<hex>`；默认 `native` | 控制 `crypto.getRandomValues()` 填充的字节 | `API_OVERRIDE=1` |

取值细则：时间单位为毫秒；`seed` 支持十进制和 `0x` 十六进制；`sequence` 最多 32 项，
耗尽后重复最后一项；`pattern` 最多 64 字节，可写 `001122ff` 或 `00,11,22,ff`，循环使用。

## 14. 完全停用

默认不设任何 `MOZ_DOM_*` 时全部功能关闭。仅设 `MOZ_DOM_TRACE=0` **不能**关闭独立的
JSCall、HTTP packet、WebSocket 和 API override。恢复无开关状态：

```powershell
Get-ChildItem Env: |
  Where-Object Name -like "MOZ_DOM_*" |
  ForEach-Object { Remove-Item "Env:$($_.Name)" }
```

然后从同一个 PowerShell 启动 Firefox。若变量配置在 Windows 用户/系统环境或启动脚本里，
也要从对应位置删除。

## 15. JSCall 输出解码速查

JSCall 不再写 JSONL，而是写定长二进制 + 一个函数名字典。**先用官方解码器**，只有要自写
工具或核对某一条记录时才按下面的布局手工拆。逐字节的完整说明（含 detail / opcode 文件、
与旧版 JSONL 的字段对照）见 README.md「二进制格式参考」。

### 15.1 输出文件

| 文件 | 内容 | 何时产生 |
| --- | --- | --- |
| `jscall\trace_jscall_process_<pid>.bin` | 每次调用一条 32 字节记录 | JSCall 已启用 |
| `jscall\trace_jscall_process_<pid>.dict.jsonl` | `func_id` → 名字 / URL / 行列；末行 `jscall_stats` | 同上 |
| `jscall\trace_jscall_process_<pid>.detail.bin` | 入参 / `this` / 返回值的类型与真值 | 设了 §8 的 `DETAIL_*` |
| `jscall\trace_jscall_process_<pid>.opcode.bin` | 逐字节码 | 设了 §9 的 `OPCODE_URL` |

### 15.2 解码器

```powershell
python trace\tools\jscall_decode.py trace_jscall_process_<pid>.bin            # 每条一行 JSON
python trace\tools\jscall_decode.py trace_jscall_process_<pid>.bin --tree     # 按线程的调用树
python trace\tools\jscall_decode.py trace_jscall_process_<pid>.bin --stats    # 汇总 + 完整性检查
python trace\tools\jscall_decode.py trace_jscall_process_<pid>.bin --values   # 只列带真值的调用
python trace\tools\jscall_decode.py trace_jscall_process_<pid>.bin --opcodes  # 字节码流
python trace\tools\jscall_decode.py trace_jscall_process_<pid>.bin -o out.jsonl
```

`.dict.jsonl` / `.detail.bin` / `.opcode.bin` 放在 `.bin` 同目录会被自动读取，
也可用 `--dict` / `--detail` 显式指定。查某一条：

```powershell
python trace\tools\jscall_decode.py trace_jscall_process_<pid>.bin | Select-String '"call_id": 19491755'
```

JSON 输出每行字段：`call_id`、`parent_call_id`、`thread`、`seq`、`depth`、`func_id`、
`name`、`url`、`line`、`column`、`kind`、`argc`、`op`(call/construct)、`ok`、`native`、
`self_hosted`、`suspended`、`resumed`、`parent_exact`、`subtree_truncated`、`entry_path`、
`duration_us`；有 detail 时多一个 `values`（`arg0…`、`this`、`return`，各含 `type` / `value`）。

### 15.3 `.bin` 记录布局（小端）

文件头 16 字节：`"JSCT"`(4) + version `1`(2) + recordSize `32`(2) + pid(4) + 保留(4)。
第 N 条记录在字节 `16 + 32*N`——**偏移减 16 不能被 32 整除就不是记录起点，读出来全是错位值**。

| 偏移 | 长度 | 字段 | 说明 |
| --- | --- | --- | --- |
| +0 | 8 | `callId` | 高 16 位线程索引，低 48 位该线程的进入序号（= `seq`）。只在同一线程内可比较 |
| +8 | 8 | `parentCallId` | 最近一个被追踪到的祖先；`0` = 顶层 |
| +16 | 4 | `funcId` | 查 `.dict.jsonl` |
| +20 | 4 | `durationUs` | `0` = 未测量（`MOZ_DOM_JSCALL_DURATION=0` 时恒为 0） |
| +24 | 2 | `depth` | 调用深度 |
| +26 | 2 | `argc` | 参数个数 |
| +28 | 1 | `flags` | 见下 |
| +29 | 1 | `entryPath` | `0` unknown / `1` stack / `2` inline / `3` jit / `4` baseline |
| +30 | 2 | 保留 | `0` |

`flags`：`0x01` construct、`0x02` **ok（为 0 = 抛异常）**、`0x04` native、`0x08` self-hosted、
`0x10` suspended、`0x20` resumed、`0x40` caller-jit（`parent_call_id` 不是直接调用者）。
普通脚本函数正常返回是 `0x02`，native 正常返回是 `0x06`。

Python 一行拆包：`struct.unpack_from("<QQIIHHBBH", data, offset)` 依次得到
`call_id, parent_call_id, func_id, duration_us, depth, argc, flags, entry_path, reserved`。

### 15.4 读结果时的规则

- 文件顺序 = 离开（leave）顺序；按 `call_id` 排序 = 进入（enter）顺序。没有时间戳。
- `parent_exact` 为 `false` 的边只保证可达，不保证相邻；`subtree_truncated` 为 `true` 的节点子调用在 JIT 里，没记录。
- `arg_types` / `result_type` 这类信息**只在 `.detail.bin` 里**（`values[*].type`），没开 detail 时只有 `argc`。
- `.dict.jsonl` 里的 `column` 是被调函数自身的定义位置，不是调用点。
- `.dict.jsonl` 末行必须是 `{"type":"jscall_stats",...}` 且 `dropped_*` / `io_errors` 为 0，否则按截断处理；`--stats` 会直接报 `unclean shutdown`。
