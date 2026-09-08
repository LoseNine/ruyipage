# Ruyitrace 开关说明书

> **当前状态：jscall 已用二进制格式重建；opcode 已在其上重建；jsvmp 仍已删除。**
>
> 旧实现为了拿到全 tier 覆盖率，在 `CacheIR.cpp`、`TrialInlining.cpp` 和 `Ion.cpp`
> 里把目标脚本的调用逼回 VM 慢路径并禁用 Ion。这会让时序敏感的反爬脚本直接超时
> （日志里的 `Script terminated by timeout`），实测还会招致站点直接拒绝会话
> （`There are currently too many visitors`）。新实现改用定长二进制记录 + 一次性
> 字符串 intern + 每线程缓冲，且**一行 JIT 代码都不改**——`js/src/jit/` 下现在
> 没有任何一处追踪代码影响 JIT 决策。
>
> **仍然无效的开关**：§2 里旧 jscall 的约 20 个 `MOZ_DOM_JSCALL_*`（`_ASYNC`、
> `_FORCE_INTERPRETER`、`_SHALLOW`、`_TARGET_ONLY`、`_DETAIL_FUNCS`、
> `_SCRIPT_URL_EXCLUDE`、`_SELFHOSTED` 等）、§2.6 的 17 个 `MOZ_DOM_JSVMP_*`、
> 以及 §5 里依赖它们的配方和 §8.4。§2.5 的 opcode 开关**部分恢复**：
> `MOZ_DOM_JSCALL_OPCODE_URL` 和 `_OPCODE_LIMIT` 可用，其余（`_OPCODE_STACK`、
> `_OPCODE_OPERANDS`、`_OPCODE_PC_START`、`_OPCODE_PC_END` 等）仍无效。
>
> **新 jscall 现有 11 个开关**，见下面的「新 jscall（二进制）」。
>
> 仍然有效且未改动：DOM/BOM 核心 trace、HTTP 报文、WebSocket、cookie、storage、
> descriptor、event、eval、exception、message、WASM、API override、TRACE GATE。
>
> 一处行为变化：`async_schedule` / `async_resume` / `script_root` 三类执行上下文
> 记录以前写在 jscall 文件里，现在写进核心 domtrace 文件
> （`domtrace/trace_process_<pid>.jsonl`）。`execution_context_id` 和
> `async_context_id` 语义不变。**`origin_call_id` 已接回并实测可用**：某次真实
> 采集中 descriptor 的 13,642 条记录 100% 带非零值，HTTP 报文有 31%（212 条中的
> 65 条）能反查到发起它的 JS 调用。
>
> **`MOZ_DOM_API_MATH_RANDOM` 只支持 `seeded:<n>`。** `fixed:` / `sequence:` 已被
> 移除：JIT 把 `Math.random` 内联成直接读 realm RNG 的机器码，没有可挂的 VM 出口，
> 要支持定值就只能关掉内联——那会拖慢正被观测的热代码，还留下一个专测
> `Math.random` 相对吞吐就能发现的特征。`seeded:` 改为给 realm 的 RNG 播种，
> 解释器与 JIT 自动一致，零开销。

## 新 jscall（二进制）

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_JSCALL_TRACE` | `1` 开 / 不设 | 不设 | jscall 主开关 |
| `MOZ_DOM_JSCALL_TRACE_FILE` | 文件路径 | 总锚点派生 | 非空时同时启用 jscall 并覆盖派生路径 |
| `MOZ_DOM_JSCALL_SCRIPT_URL` | 逗号/分号分隔 URL 子串 | 空 | **只记录命中的脚本**。大小写敏感子串，不是 regex/glob |
| `MOZ_DOM_JSCALL_LIMIT` | 整数，`0`=无限 | `0` | 每进程记录数上限，防爆盘 |
| `MOZ_DOM_JSCALL_DURATION` | `0` 关 / 其它开 | 开 | `duration_us`。每次调用两次时钟读；重 VM 上可关掉 |
| `MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL` | 逗号/分号分隔 URL 子串 | 空 | **抓入参 / this / 返回值真值**，只对命中的脚本 |
| `MOZ_DOM_JSCALL_DETAIL_NATIVE` | 逗号/分号分隔函数名子串 | 空 | 同上，但按 **native 函数名**命中。对象参数只记身份不记内容（见 §1.1） |
| `MOZ_DOM_JSCALL_MAX_VALUE_BYTES` | `256`..`16777216` | `65536` | 单个值的字节上限，超出截断并标 `truncated` |
| `MOZ_DOM_JSCALL_BASELINE` | `1` 开 / 不设关 | **关** | 给命中过滤的脚本在 Baseline 编译产物的 prologue/epilogue 插桩，补一段 tier 覆盖 |
| `MOZ_DOM_JSCALL_OPCODE_URL` | 逗号/分号分隔 URL 子串 | 空 | **逐字节码 op 记录**，独立于调用过滤器。量极大，务必收窄到单脚本 |
| `MOZ_DOM_JSCALL_OPCODE_LIMIT` | 整数，`0`=无限 | `0` | opcode 记录数上限 |
| `MOZ_DOM_JSCALL_NATIVE` | `1` 开 / 不设关 | **关** | 设了脚本过滤器时**仍然记录 native**。不开则 native 跟随过滤器一起被屏蔽（见下） |

> **`_LIMIT` 一类数值开关解析失败不会静默放行**：`strtoull` 对非数字返回 0，而 0
> 恰好是"无限制"，打错一个字符就等于关掉了防爆盘上限。现在解析失败会保留原值并
> 在 stderr 打警告。

> **注意 `MOZ_DOM_JSCALL_NATIVE` 的必要性**：只要设了 `MOZ_DOM_JSCALL_SCRIPT_URL`，
> 默认行为是连同 native 一起屏蔽，于是 `crypto.getRandomValues`、
> `TextEncoder.encode`、`Math.random` 这些反爬分析最想看的原语会**一条都不出现**。
> 实测中一次崩溃就发生在 `TextEncoder.encode` 里，而它在 jscall 记录里查无此函数。
> 默认仍为关（native 调用量很大，开启前应确认不违反"不得更卡"这条），需要时显式打开。

`MOZ_DOM_TRACE=0` **不再关闭 jscall**，它只关核心 sink（详见 §2.1）。要停 jscall 就用它自己
的开关。`MOZ_DOM_TRACE_GATE`（§8）则仍然管住 jscall：关闸期间不产记录，计入 `dropped_gate`。

### 0.9 `MOZ_DOM_TRACE_UNFILTER`：解除默认成员过滤

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_TRACE_UNFILTER` | 逗号/分号分隔的成员名 | 空 | 让被默认黑名单屏蔽的成员重新出记录 |

默认被屏蔽的五个：`requestAnimationFrame`、`cancelAnimationFrame`、`clearTimeout`、
`clearInterval`、`getComputedStyle`。理由是**量**而不是没价值——rAF 每帧一次、clear 每个
定时器一次。但补环境时要决定是否实现 `getComputedStyle`，定时器驱动的状态机也只有把
clear 和 set 一起看才读得懂。

```powershell
$env:MOZ_DOM_TRACE_UNFILTER = "getComputedStyle,clearTimeout,clearInterval"
```

> **为什么这个开关有必要**：被屏蔽时它们**一条记录都不出**，而输出里没有任何东西说明
> "这是被过滤的"。于是"没记录"会被误读成"页面没调用过"——这两件事在补环境时的处置
> 完全相反。实测对照（`member_coverage_probe.html` + `run_member_coverage_probe.ps1`）：
> 这四个成员在默认下是 0、解除后是 1，而 `Document.URL`、`scrollX`、`pageXOffset`、
> `scrollY`、`pageYOffset`、`Performance.getEntries`、`Navigator.javaEnabled` 两种配置下
> 都是 1——**它们一直是通的**，真实站点抓取里为 0 只是因为页面没调用。

WebIDL 成员的插桩不是逐成员生成的，而是集中在 `BindingUtils.cpp` 的 `GenericGetter` /
`GenericSetter` / `GenericMethod` 三个模板里，所有**非 static** 成员都经过它。所以生成的
`*Binding.cpp` 里搜不到 `TRACE_DOM` 是设计如此，不是缺失。真正没有 trace 路径的只有
static 方法与 static 属性（codegen 不给它们走 Generic\* 包装）。

### 1.0 `MOZ_DOM_ABSENT_PROBE`：记录页面探测过哪些**不存在**的宿主属性

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_ABSENT_PROBE` | `1` / 不设 | 关 | 记录页面读取过、但不存在的宿主属性名 |

**其它任何一层都看不到这类信号**：属性不存在就没有 binding 可挂钩，所以 getter 级别的
trace 一条都记不到。于是一次抓取可以看起来很完整，却完全没说明"决定了页面行为的那些
判断"——而按它重建环境时，你会把一个「本该缺席」的属性补上。

页面就是靠问不存在的东西来区分浏览器和自动化工具的。实测目标站在内容进程里探测了
**519 个**不存在的全局，其中包括：

```
$cdc_asdjflasutopfhvcZLmcfl_   cdc_adoQpoasnfa76pfcZLmcfl_Array   (ChromeDriver)
_Selenium_IDE_Recorder  __selenium_evaluate  _selenium  calledSelenium
webdriver  __webdriver_evaluate  __webdriver_script_fn  _WEBDRIVER_ELEM_CACHE
__fxdriver_evaluate  __fxdriver_unwrapped  fxdriver_id
_phantom  callPhantom  __nightmare  domAutomationController
chrome  $chrome_asyncScriptInfo  webkitNotifications
Buffer  Bun  ActiveXObject
```

**按名字去重**，所以这一层的体积是「探测过多少个不同名字」而不是「探测了多少次」——
实测 1,161 条记录。

#### 覆盖的三种宿主对象

不是只有全局。三类宿主对象各在不同位置得出「找不到」这个结论，所以是三条会各自独立
回归的路径，记录里的 `target` 字段说明是哪一类：

| 宿主 | 得出结论的位置 | 例子 |
|---|---|---|
| 全局，以及 `navigator` / `screen` 这类普通 WebIDL 反射对象 | `NativeGetPropertyInline` 的未命中分支 | `window.chrome`、`navigator.brave` |
| Window 的命名属性 | `WindowNamedPropertiesHandler` | `window.某个不存在的 frame 名` |
| DOM proxy（`document` 与各种集合） | codegen 生成的 get trap 末尾 | `document.$cdc_...` |

**`document` 与 `navigator` 这两类是后补的，补之前是全盲的**，而且盲区不是边角：
ChromeDriver 的标记是在 `document` 上找的，不是在 `window` 上，所以只覆盖全局的版本
恰好看不到它。`navigator` 之所以也漏，是因为它是普通反射对象而不是 proxy，会正常走到
未命中分支，却被「必须是全局」这个条件挡掉。

DOM proxy 的插桩点必须在 get trap **最后**、四项（expando、indexed、named getter、
原型链）全部落空之后。不能放在原型链查找之后就记：没有 `LegacyOverrideBuiltIns` 的接口
其顺序是原型链先、named getter 后，放在那里对每个有 named getter 的 proxy 都是错的。
放在 `getOwnPropDescriptor` 里同样错——普通读根本不经过它，而且它记的是"没有自有属性
描述符"，不是"整条查找失败"。

#### 去重键

去重键是(名字, 目标)。**全局按对象身份**，其它宿主对象**按 JSClass**。分开的原因是成本：
放宽到宿主对象之后这个钩子会看到每一个反射对象（每个 DOM 节点一个），给它们逐个建
GC 稳定 id 会在一条「本来就是因为查找失败才存在」的路径上分配内存。按类去重把这个界定
在每(接口, 名字)一条，而这也是更有用的集合——"页面向 Navigator 问过 brave"不会因为
问了第二个 Navigator 而更真。全局数量是每进程个位数，按对象身份可负担，且帧的身份有意义。

所以一次会话里出现多条 `target` 同为 `Window` 的记录是**正常去重**（不同的 Window，
包括每个 chrome sandbox），不是重复；判重要看 `targetId`。

覆盖到的访问形式（每种都实测过恰好记录一次）：

| 写法 | 覆盖 |
|---|---|
| `window.NAME` | 是 |
| `window["NAME"]` | 是 |
| `window[变量]` | 是 |
| `typeof NAME`（裸标识符） | 是 |
| `"NAME" in window` | 是 |
| `globalThis.NAME` | 是 |

> **为什么插桩点必须在 resolve 之后**：`Promise`、`TextEncoder`、`WebAssembly`、`Atomics`
> 这类标准全局是**惰性定义**的，它们也会经过"属性尚未找到"这条路径，只是随后 resolve
> hook 会把它们装上。如果在 resolve 之前记录，就会断言 `Promise` 不存在——那比不记录
> 更糟。`validate_absent_probe.py` 里的阴性对照专门守这一条。

> **代价与红线**：命中路径完全不受影响（找到了就不会走到这个分支）。未命中路径的代价是
> 「一个 bool + 取全局 + 指针异或 + 查表」，去重键用的是 interned 原子指针，字符串转换
> 只在**首次**见到某个名字时才做。
>
> 这两点都是实测逼出来的，不是设计洁癖：早先的版本每次未命中都做一次 UTF-16→UTF-8
> **堆分配**，直接导致目标站返回"访客过多"；改成对名字算哈希后拒绝消失，但目标站超时
> 仍比对照多 1 次；最后改成用原子指针做键（`aId.toAtom()` 本来就在作用域里），配对 A/B
> 才持平——对照与实验都是访客过多 0、崩溃 0、目标站超时 1，实验臂产出 855 条记录。
>
> 结论是这条路径对每次未命中的开销极其敏感，任何 O(名字长度) 以上的工作都不要放进去。

### 1.0.1 `MOZ_DOM_HOST_MUTATION`：记录页面**改动**了宿主环境的哪些地方

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_HOST_MUTATION` | `1` / 不设 | 关 | 记录页面在宿主对象上装了什么、删了什么、改了谁的原型 |

其它所有层记录的是页面**使用**环境，没有一层能记录页面**改动**环境——页面自己发明的
属性没有 WebIDL binding 可挂钩。`document.__update_img = f` 装上一个图片解密入口，
一条记录都不出；于是抓取里会看到解密后的字节从一个「在日志里凭空出现」的函数里冒出来。

**不存在性探测那一层会让这件事更具误导性**，而不是更清楚：它看得见安装**之前**那次存在性
检查，看不见安装本身。于是日志读起来像是"页面问了一个从来不存在的属性"。这两层必须一起开。

记录三种形态，`kind` 字段区分：

| kind | 含义 | 为什么要 |
|---|---|---|
| `define` | 出现了一个自有属性，数据或访问器 | 装在宿主上的访问器是页面被做成会对自己说谎的手段。`detail` 为 `data` 或 `accessor:get:名字` / `accessor:set:名字`，`func_id` 让它和调用层 join |
| `delete` | 删掉了一个自有属性 | 混淆代码在全局上暂存再擦掉，没有这条那个暂存属性看起来从没存在过 |
| `proto` | 换掉了某个对象的原型 | 改变之后所有查找的解析结果。`detail` 是新原型的类名 |

插桩点与频率：

- `define`：`js::DefineProperty`（`js/src/vm/JSObject.cpp`）。`document.foo = f` 会经过两次
  ——一次是 proxy（`OrdinarySetWithOwnDescriptor` 第 5.f 步），一次是 `DOMProxyHandler`
  把 define 转发到 expando。expando 是普通对象，被过滤挡掉，所以记录里是宿主对象本身而
  不是页面看不见的内部对象。
- `delete`：`DelPropOperation` / `DelElemOperation`（`js/src/vm/Interpreter.cpp`），不是
  `js::DeleteProperty`——后者是个头文件里的 inline，挂在那里等于让每个使用者都编译进
  trace。挂在字节码操作上范围也更准：意思是「脚本写了 `delete`」，而不是某个内置函数在
  内部删了个元素。`delete window.x` 拿到的是 WindowProxy，要先解到 Window。
- `proto`：`js::SetPrototype` 成功之后。**不能**插在 `setProtoUnchecked` 里面——那时
  Watchtower 与 shape 替换还没完成，等于读一个改到一半的对象。

三条路径**都没有 CacheIR stub**（`CacheKind` 枚举里没有 delete，define 与 setPrototypeOf
也从不 attach），所以都不在属性读写的快路径上。

> **过滤器是关键**。`js::DefineProperty` 不是「所有属性写入」的汇聚点——`JSON.parse` 构造
> 对象走 `AddDataPropertyToNativeObjectNoHooks`，对象字面量走 `DefineDataProperty`，数组
> 字面量直写 dense slot，`Object.assign` 与 `push` 走 `SetProperty`，plain object 上首次
> `obj.newKey = v` 走 `AddDataProperty`，**这些全都不经过它**。但它**会**命中
> `SetPropertyByDefining`，也就是脚本覆盖一个继承来的可写属性，频率取决于页面怎么写、
> 无法预估。所以钩子第一件事是一个 class flag 测试，只放行全局与 DOM 类，plain object
> 在任何东西被 root 之前就被挡掉。`validate_host_probe.py` 的阴性对照专门守这一条。

去重与 `MOZ_DOM_ABSENT_PROBE` 同一套：键是(名字, 目标, kind)，全局按对象身份、其它按
JSClass。kind 在键里，所以「装上」和「后来删掉」是两个事实——顺序本身就是"页面清理了
痕迹"这个信息。

已知边界：

- plain object 上脚本显式新增数据属性（走 `DefineDataProperty`，太热，不插）看不到。
  我们只要宿主对象上的，这个缺口无影响。
- dense 数组删元素走 `DeleteArrayElement` 快路径，不经过 `delete` 钩子。整数键也会被
  钩子自己的 atom 检查挡掉——这正是数组元素删除不进这一层的原因。
- `Reflect.deleteProperty(document, 'foo')` 不经过字节码操作，看不到。
- `document`、`navigator` 这类宿主对象多数 `staticPrototypeIsImmutable`，改原型会在
  `js::SetPrototype` 早期就失败，所以 `proto` 实际主要对全局和页面自己的对象有意义。
- 浏览器自身的 chrome JS 也会被记录（它同样在全局上装东西、改原型）。按 `pid` 与
  `process_type` 区分内容进程与父进程。

### 1.0.2 `MOZ_DOM_IFACE_PROBE`：记录页面探测过哪些**存在**的接口

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_IFACE_PROBE` | `1` / 不设 | 关 | 记录页面问过、且这个构建**有**的 WebIDL 接口对象 |

这是 §1.0 那层的**另一半**。页面靠枚举「哪些接口存在」判断自己跑在什么环境里，而两种答案
落在两个完全不同的地方：不存在的名字没有 binding，归不存在性探测；**存在**的名字解析成全局
上的一个普通数据属性，同样没有 getter 可挂钩，而且因为它确实存在，不存在性探测也不会认领它。
结果就是一整块盲区——拿一份线上验证能过的手写补环境做标尺，它提供的 54 个接口对象，trace
的读取记录是 **0 条**。

记录带 `state` 区分两种结果：

| state | 含义 | 补环境要怎么做 |
|---|---|---|
| `present` | 这个构建提供该接口 | 必须让它存在 |
| `disabled` | 构建认识这个名字，但对当前 scope 关掉了，页面读到 `undefined` | 必须让它**缺席**；同时这是很锐的版本/配置指纹，别的层都报不出来 |

插桩点是 `WebIDLGlobalNameHash::DefineIfEnabled`（`dom/bindings/WebIDLGlobalNameHash.cpp`），
`nsGlobalWindowInner::DoResolve` 对任何字符串 id 第一步就走它。这条路径的两个性质决定了这层
既便宜又诚实：

- **解析只发生一次。** 接口对象会被塞进全局的一个 slot（删掉也不会重新出现），所以钩子只看
  得到首次触碰。这层的量因此等于「页面问过多少个不同接口」，而不是「问了多少次」——和不存在
  性探测同形，页面在循环里反复检查也不额外花钱。
- **批量枚举不走这里。** `Object.getOwnPropertyNames(window)` 走 `GetNames`，只追加名字不做
  解析。所以一次枚举不会用上千条记录把真正有信号的定向探测埋掉。

`typeof window.X`、`'X' in window`、`window["X"]`、`globalThis.X` 首次触发的都是这条路径，
本地探针逐项验过六种写法各产生且只产生一条记录。

已知边界：

- **只覆盖 WebIDL 接口，不覆盖 JS 标准内置**（`Promise`、`Proxy`、`Reflect`…）。后者走
  `JS_ResolveStandardClass`，不经过这个钩子。这是**故意的**：每个 JS 引擎（含 Node）都有它们，
  不具区分度，手写补环境也不管它们。
- **给的是「首次触碰/存在」，不是每次读的计数。** 接口对象解析后变成普通数据属性，重复读拿
  不到。补环境只需要知道该不该提供，这已经够。
- `disabled` 分支不定义任何属性，所以每次读都会重新解析、重新进钩子。claim 用原始地址、在任何
  分配之前完成，重复读的代价只是一次哈希探测。

### 1.0.3 `MOZ_DOM_JSCALL_CLOSURE_URL`：抓函数从闭包里读到的状态

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_JSCALL_CLOSURE_URL` | 逗号或分号分隔的 URL 子串 | 空（关） | 命中脚本的函数在入口快照其闭包捕获的变量名与值 |
| `MOZ_DOM_JSCALL_CLOSURE_MAX_BYTES` | `256..8388608` | `262144` | 单个绑定序列化的字节上限 |

其它每一层都在**函数边界**采样，这也是"跨边界可还原、函数内部不可见"这条边界的由来。但对**提取算法**来说那条边界攻错了方向：算术不需要被观测，函数源码本来就已落盘、可以离线重跑。真正卡住重放的是函数从外层作用域**读到的状态**——一个 `return atob(c[i])` 的解密壳，没有 `c` 就是废的，而 `c` 待在 IIFE 的 `CallObject` 里，任何调用边界都不会暴露它。

所以这一层只回答一个问题：**这个函数被调用的那一刻，它闭包捕获的变量叫什么、值是什么。**

**为什么这是安全的**：读一个绑定就是读 slot。名字来自 `Scope` 的 `BindingIter`（编译期就填好了），值来自 `env->getSlot(...)`。**不访问任何属性**，所以 getter 和 Proxy 陷阱都不会跑；不进 realm；不改任何 JS 可见状态。这正是它与 Debugger 的 Environment API 的分界——后者用 `GetPropertyKeys` 枚举，并且直言读变量"can trigger getters"，还要求脚本是 debuggee（那会把 Ion 排除掉、直接撞红线一）。

#### 实测：导师案例的两张解密表

对同一份 DataDome bundle：

| 表 | 表真实大小 | 按调用观测（`D`/`E` 的实参→返回） | 闭包快照 |
|---|---|---|---|
| `c`（atob 壳） | 409 | 274（**67.0%**） | **409（100%）** |
| `M`（自定义 base64 壳） | 2,248 | 620（**27.6%**） | **2,248（100%）** |

按调用观测只能拿到**这次运行真跑到的**索引；闭包快照一次拿到**整张表**，不需要把没走的分支走一遍。这把静态分析的覆盖优势用动态手段拿到了。

三条独立路径互相印证：闭包快照的表 vs 按调用观测，重叠部分 **252 项一致、0 分歧**；按调用观测 vs 独立解码静态输出，**274 项一致、0 分歧**。

顺带确认的：`w` 是 512 项状态表、`o` 就是 `String.fromCharCode` 的别名、`d` 记为 `Proxy`（内容页的 `window` 是 WindowProxy）。

#### 两个被实测逼出来的设计

**一、值还是 undefined 时不占 claim。** bundle 在**一条**从左到右的 `var` 语句里初始化它的表，而更靠前的初始化项本身已经是函数调用——所以第一个走到这里的调用**必然**看到后面的表还没赋值。第一版按"每对只记一次"占了 claim，结果 `c`、`M`、`w` 全部记成 `{"t":"undefined"}`：不是漏了，是快照取早了。改成未初始化就跳过且**不占** claim，后面某次调用才把真表记下来。

**二、遍历本身也要有预算。** 只给"写记录"去重是不够的。命中过滤器的反爬脚本其函数会被调用几十万次，于是**遍历**（走链 + 一遍 `BindingIter` + 每个绑定一次 slot 读）被每次调用都付一遍。实测表现是页面在**验证码出现前**那一段明显发卡——正是指纹计算最密的窗口——而客观对应是「其它脚本超时」从对照的 2 升到 3。

改法是两级门控：**先按 funcId 缓存"这个函数的脚本命中了吗"**（子串扫描只做一次，之后是一次数组读），命中的才去花该函数的遍历预算（`kClosureMaxWalksPerFunc = 64`）。顺序很要紧——每个函数的每次调用都会走到这里，任何放在缓存判定之前的东西都是全进程在付；第一版把自增放在匹配之前，那比它要修的问题更糟。

加上预算后：其它超时回到 **1**（对照 2、无预算 3），目标站超时 0、崩溃 0、访客过多 0，而记录数反而略增（7,086 对 6,909）——预算砍掉的是重复空走，不是覆盖。

#### 原理上拿不到的

- **没被任何内层函数引用的局部变量**：编译器把它留在栈帧 slot，不在任何环境对象里。`validate_closure_probe.py` 的阴性对照专门守这一条——它出现就说明这一层在读它读不到的东西。
- `with` 的动态绑定（在目标对象上，枚举需要属性访问）、global 上的 var（是属性不是环境 slot）、module import 绑定。
- 对象值的 `id` 可能是 0：遍历过程中不做"建 id"（那会分配、从而可能 GC，而正在读的 slot 会被移动），所以用只读的 `TraceStableObjectId`，没有 id 就报 0。
- funcId 的缓存表是 65536 槽取模，超过这么多函数会有两个 id 共用一槽、继承对方的判定。真实反爬抓取在内容进程里约 1.2k 个函数。

### 1.0.4 `MOZ_DOM_ENV_INVENTORY`：这个构建**提供**了什么（全量静态清单）

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_ENV_INVENTORY` | `1` / 不设 | 关 | 一次性导出本构建对**当前页面所在 scope** 暴露的全部接口、成员、属性种类与启停状态 |

上面三层记的都是**页面要了什么**。补环境要的是一个差集，而差集要两个操作数——另一个是**这个构建
有什么**。只有探测侧的话，「页面没问过 `X`」和「`X` 在这儿根本不存在」在日志里长得一模一样，
照着这份日志搭出来的环境会原样继承这个盲区。这一层补的是被减数。

拿来做标尺的那份线上能过的手写环境，它的清单是作者在自己的 Node 假环境里
`Object.getOwnPropertyNames(window)` 一路走出来的——在只有 JS 的壳里那是唯一的办法。在浏览器里
它是**错的**办法：枚举页面全局会把每个惰性接口对象都解析并挂到全局上，页面看得见，而且同一下
就把 §1.0.2 的首次触碰信号全毁了。所以这层一个 JS 对象都不枚举，改读 codegen 出来的静态表。

#### 读哪些表

| 要的数据 | 来源 |
|---|---|
| 全部 Window 暴露名 | `WebIDLGlobalNameHash::sEntries[]`（`Codegen.py` 生成进 `RegisterBindings.cpp`） |
| 该名字对当前 scope 开着没有 | 表项的 `mEnabled` 谓词——就是 `DefineIfEnabled` 自己要问的那个 |
| 每个接口的成员与种类 | 表项新增的 `mNativeHooks->mNativeProperties.regular`，即 `CreateInterfaceObjects` 会去定义的那几张 `Prefable<JSFunctionSpec / JSPropertySpec / ConstantSpec>` |
| 每个接口的**父原型** | `WebIDLGlobalNameHash::sParentPrototypeIds[]`（按 `prototypes::ID` 索引，`Codegen.py` 从 `parentPrototypeName` 生成），走链得到 `inherits` |
| 全部 **Worker** 暴露名 | `WebIDLWorkerNameTable::sEntries[]`（`Codegen.py` 生成进 `RegisterWorkerBindings.cpp`），字段与 Window 表同型 |
| JS 标准内置 | `js::ProtoKeyToClass()` 的静态 `ClassSpec` |

Window 和 Worker 各出一份文件：`env_inventory_window_<pid>_<session>.json` 与
`env_inventory_worker_<pid>_<session>.json`，顶层多一个 `"global":"window"|"worker"` 字段，
`schemaVersion` 升到 2。Worker 那份在 `WorkerPrivate::RegisterBindings` 里、
`RegisterWorkerBindings` 定义完接口对象**之后、任何 worker 脚本跑之前**发出——那是唯一同时有
JSContext、有真实 worker 全局可求 `mEnabled`、且页面还什么都没改的时刻。实测一次采集里
worker 清单的 seq 是 1272，该进程第一条 worker 接口记录是 seq 623556。

Worker 表是新加的：worker 没有惰性 resolve，`RegisterWorkerBindings` 是一串
`CreateAndDefineOnGlobal` 调用，从没需要过名字表。顺带一个 codegen 改动：
`sNativePropertyHooks` 以前只对 `wantsXrays`（= Window 暴露）的接口生成，worker-only 的接口
没有这个结构、清单就拿不到它们的成员表——第一版 worker 清单里 `WorkerGlobalScope` 只有
`length`/`name` 两个成员就是这个原因。现在所有有接口对象的接口都生成它；非 Xray 接口的
Xray 专用字段填 `nullptr`，而它们的 JSClass 仍指向 `sEmptyNativePropertyHooks`，**运行时行为
一个字节没变**，新结构只从 Worker 表可达。

`mEnabled` 和 `Prefable::isEnabled` 都只读 pref / secure context / origin trial 状态，resolve 路径
在定义之前本来就要问一遍。**全程不创建接口对象、不定义属性、不触发 getter、不进 Proxy 陷阱。**
只读 `regular` 那张表，不读 `chromeOnly`：后者在内容全局上永远不会被定义，列出来等于断言页面
够得着一批它够不着的成员。

#### 触发点与代价

插桩点是 `WebIDLGlobalNameHash::DefineIfEnabled`，紧接在 §1.0.2 那个钩子之后。选这里是因为它是
最早**同时**拿得到 `JSContext` 和未包 Xray 的 Window 的地方，而两者都拿到才谈得上对当前 scope 求值
`mEnabled`。Xray 分支在这之前已经返回，所以拿到的一定是页面自己的全局。

一个进程一次，用原子 `exchange` 抢 claim，第二次调用在第一条指令就返回。**页面跑起来之后这层的
成本恒等于零**——它不在任何热路径上，也不受 `MOZ_DOM_TRACE_LIMIT` 之类的计数影响。

#### 输出

`env\env_inventory_<pid>_<session>.json`（sidecar 附件，实测约 1.2 MB），另在核心 trace 里留一行
元数据指向它。这行**故意不带 `origin_call_id`**：没有任何 JS 调用导致它，填 0 会被读成「发生在
被追踪调用之外」，而不是「它压根不是个事件」。

```json
{"schemaVersion":1,"seq":22,"ts":1788491281485,"pid":12952,"session":0,
 "process_type":"tab","type":"env_inventory","attachment":"env/env_inventory_12952_0.json",
 "interfaces":1060,"window_props":786,"disabled":289}
```

附件本体：顶层是 `interfaces` / `windowProps` / `stats`，加上 `buildId`、`version`、`processType`。
每个接口下有**三个 target**，对应页面能摸到的三个不同对象：

| target | 真实对象 | 由谁定义 |
|---|---|---|
| `statics` | 接口对象（构造器本身） | `InitInterfaceOrNamespaceObject` |
| `proto` | 接口原型对象 | `DefineProperties` |
| `instance` | **每个实例自己**（`[LegacyUnforgeable]` 成员） | `DefineLegacyUnforgeable*` |

`instance` 必须单列。`Window.document`、`Location.href`、`Event.isTrusted` 这些成员在真实浏览器里
不在原型上，把它们记进 `proto` 就是在断言那个「照着做就会被检出」的错误形状。

```json
"Notification": {
  "statics": {"methods":["requestPermission"], "accessors":["permission","maxActions"],
              "data":["length","name"],
              "order":["length","name","requestPermission","permission","maxActions"],
              "nonEnumerable":["length","name"], "nonConfigurable":[],
              "readonly":["length","name","permission","maxActions"],
              "nargs":{"requestPermission":0}},
  "proto":    {"methods":["close"],
              "accessors":["onclick","onshow","onerror","onclose","title","dir","lang","body",
                           "tag","icon","requireInteraction","silent","data","actions"],
              "data":[],
              "order":["close","onclick","onshow","onerror","onclose","title","dir","lang",
                       "body","tag","icon","requireInteraction","silent","data","actions"],
              "nonEnumerable":[], "nonConfigurable":[],
              "readonly":["title","dir","lang","body","tag","icon","requireInteraction",
                          "silent","data","actions"],
              "nargs":{"close":0}},
  "instance": {"methods":[],"accessors":[],"data":[],"order":[],
              "nonEnumerable":[],"nonConfigurable":[],"readonly":[],"nargs":{}},
  "state": "present", "source": "webidl", "hasChromeOnly": false,
  "disabledMembers": ["vibrate"], "unforgeable": [],
  "inherits": ["EventTarget"]
}
```

`order` 在这里就体现出桶给不出的东西：`close` 是方法、其余是访问器，真实原型上
`close` 在最前，然后是四个事件处理器，再才是内容属性——这个序列在三个桶里是看不出来的。

`inherits` 是父原型链，最近的在前：`Notification.prototype` 的 `[[Prototype]]` 是
`EventTarget.prototype`，再往上是 `Object.prototype`（不列）。`HTMLDivElement` 那条是
`["HTMLElement","Element","Node","EventTarget"]`。这是 `instanceof` 与 `Object.getPrototypeOf`
实际走的那条链——一次 Turnstile 采集里有 20,858 条 `instanceof`，全靠它。空数组是真实答案，
表示父原型就是 `Object.prototype`；没有这个键表示接口没有原型对象（命名空间之类）。

每个 target 里的键：

| 键 | 含义 |
|---|---|
| `methods` / `accessors` / `data` | 按描述符种类分桶，**桶内是定义顺序，不排序**。一份靠遍历活对象得来的参考清单可以直接和这三个桶做差分 |
| `order` | 该对象上属性的**完整定义顺序**。桶给不出这个：真实对象上 method 和 accessor 是交错的，而这里各进各桶。属性顺序就是 `Object.getOwnPropertyNames` 的返回顺序，本身是指纹 |
| `nonEnumerable` / `nonConfigurable` / `readonly` | 例外名单。WebIDL 成员默认可枚举、可配置、方法可写，所以列「不是的那些」既短又正好是 descriptor 走查会注意到的 |
| `nargs` | 每个方法的 `length`。手写环境最容易在这里露馅 |

**这四项都是按 target 分的，不能提到接口级。** 名字只有连同它所在的对象才能确定一个属性：
`length` 和 `name` 在每个接口对象上都不可枚举，但在 `NodeList`、`Attr`、`HTMLAnchorElement`
等 40 多个接口的**原型**上是完全普通的可枚举属性。接口级一张扁平表会把这些全报成不可枚举——
实测就是这样错了 46 处。

接口级的键（不属于任何单个对象的信息）：

| 键 | 含义 |
|---|---|
| `state` | `present` = 页面能拿到；`disabled` = 构建认识这个名字但对当前 scope 关着，页面读到 `undefined`。补环境必须让它**缺席** |
| `source` | `webidl`（名字表）还是 `standard`（JS 标准内置）。后者每个引擎都有，不具区分度，但**不列出来**差分就会把 `Object`/`Array`/`Promise` 全报成缺口 |
| `aliasOf` | 同一份描述符的另一个名字（`Image`→`HTMLImageElement`、`webkitURL`→`URL`）。不合并，因为合并会让键数对不上 `EntryCount()`，还会丢掉页面确实能探测的名字 |
| `disabledMembers` | 接口在，但这个成员被 pref/secure-context 关掉了。它没有 placement——页面根本看不见它——所以留在接口级 |
| `unforgeable` | `instance` 那个 target 的成员名集合，用 WebIDL 自己的术语再说一遍 |
| `hasChromeOnly` | 该接口另有一张 chrome-only 表（内容页看不到，此处不展开成员） |

`windowProps` 是唯一一个**排序**的名字数组，而且是有意的：名字表是哈希槽序，而这些名字在真实
window 上出现的顺序是**解析顺序**——它们是惰性的，取决于页面摸过谁，压根不是构建的属性。
按哈希序输出等于暗示一个不存在的顺序。这个键是集合，不是序列。

#### 它是**按 scope**的，不是按构建的

同一次运行的四个内容进程，接口总数都是 1060，但 `disabled` 分别是 289 / 390 / 281 / 283。差出来的
107 个正是 secure context 专属的那批——`Cache`、`CacheStorage`、`Clipboard`、`Credential`、
`CryptoKey`、`GPU`、`FileSystem*`、WebAuthn 一族——在非安全上下文的那个进程里全部 `disabled`。

这是这一层比任何「构建期静态 dump」都强的地方，也是 `mEnabled` 必须在**拿到真实 global 之后**才求
值的原因：清单描述的是页面当时实际所处的那个环境，不是一个构建范围的常量。

#### 实测：与一份线上能过的手写环境对账

**先说怎么比的，因为口径决定了数字有没有意义。** 那份手写环境提供了两个文件，只有一个能用：

| 文件 | 内容 | 能不能当标尺 |
|---|---|---|
| `hahavm_inventory.json` | 它自己的 `__traceInventory()` 产出，958 接口 | **基本不能。** 958 个里 908 个的 `proto` 桶是空的——它靠 `getOwnPropertyDescriptor(ctor,'prototype')` 取原型，而它自己的接口对象是 Proxy，探针看不穿。全部成员条目只有 3,258 条 |
| `trace_inventory.jsonl` | 它安装每个属性时记的 `def` 事件流，6,185 条 | **能。** 带 owner、属性名、描述符种类、`enumerable`、`configurable`，是真答案 |

拿第一个文件跑它自带的 `inventory_diff.js` 会得到「774 共有接口、8 个成员缺口、kindMismatch=0」。
**这个结果几乎是空的**：774 里有 752 个的参考侧 `proto` 是空的，循环一次都没进，报不出东西也报
不出错。真正有对照面的只有 22 个接口。这条只能用来验证一件事——schema 兼容，它零修改消费我们的
文件。

有意义的验收要用 `def` 流重建参考侧的成员表再比。当前实测：

| 口径 | 参考接口 | 实际比对 | 成员缺口 | kind | enumerable | configurable |
|---|---|---|---|---|---|---|
| `X.prototype` 的 def → 我们的 `proto` | 273 | 220 | 192 | **0** | **0** | **0** |
| `X<obj>` 的 def → 我们的 `instance` | 3 | 3 | 1 | **0** | **0** | **0** |

约 5,000 个成员，三个描述符维度（种类 / 可枚举 / 可配置）**无一处分歧**。`instance` 那行是
`unforgeable` 单列的直接验证：参考侧把 `Location` 的 15 个成员记在 `Location<obj>` 上，我们的
`instance` 桶一个不差地对上。

其余数字：

| 指标 | 值 |
|---|---|
| 接口数 | 1060 = 1006 WebIDL + 54 标准内置。WebIDL 那 1006 与 `EntryCount()` 逐个对上（`stats.nameTableCount` 就是给这条验收用的） |
| present / disabled | 771 / 289 |
| 成员条目总数 | 15,029（statics + proto + instance）；47 个接口有 `disabledMembers`、117 个有 `instance` 成员共 134 条、8 个是别名 |

那 192 个 prototype 缺口逐条查过，**绝大多数是 Chrome 有而这个 Firefox 构建没有**，报出来正是这层
该干的事：`Navigator` 41 项（`deviceMemory`、`userAgentData`、`bluetooth`、`hid`、`usb`、`xr`、
`adAuction*` 一族）、`Document` 33 项（`browsingTopics`、一整批 `webkit*`）、整个
`NetworkInformation`。另有 2 项是**放置位置**的真实差异：`Event.isTrusted` 和
`Navigator.serviceWorker` 在 Firefox 是 `[LegacyUnforgeable]`、落在实例上，参考环境（照 Chrome 做的）
把它们放在原型上。

两个被实测逼出来的修正，都记在这里以免重蹈：

- **`length` 和 `name` 得补。** 第一版 774 个共有接口全报缺口，逐条查下来 766 个的差异**只有**这
  两个：每个函数都有的固有自有属性，没有任何 WebIDL 表会列，但真实接口对象上确实存在。补进去是
  更准确而不是迁就标尺——这是 `AddIntrinsicFunctionMembers` 的由来。
- **描述符标志不能提到接口级。** 见上一节那 46 处。

#### 原理上拿不到的

- **`FinishClassInitOp` 装的成员。** 实测 19 项：`Array.prototype.length`、`Number.NaN` /
  `parseFloat` / `parseInt`、`Symbol` 的 15 个 well-known symbol。它们不在静态 `ClassSpec` 数组里，
  是标准类的 finishInit 钩子在运行时装上去的。这类钩子是任意 C++ 代码，不是可读的表；要拿到就得
  真去创建那个标准类的构造器再枚举它，那正好是本层为了不可观测性而拒绝的做法。硬编码一张补丁名
  单能把数字做到 0，但那是把标尺的答案抄进被测方，不做。
- **页面运行时实际看到的形状。** 这层给的是「构建**应该**定义成什么样」。页面改过原型、装过访问
  器之后的真实形状是另一回事，那是 §1.0.1 和 descriptor 层的活；两者不一致本身就是信号。
- **Chrome 会返回什么值。** `window.chrome`、`navigator.userAgentData` 这类在 Firefox 上被正确记为
  缺席，这是正确的观测而不是欠缺。目标值只能来自 Chrome 侧参考，不能由这份日志产出。
- **非 WebIDL 的命名空间与扩展对象。** `Intl.Collator` / `Intl.DateTimeFormat` 这类 `Intl.*`
  是 SpiderMonkey 的内置命名空间成员，不在 `ProtoKeyToClass` 的顶层类表里；`WEBGL_lose_context`
  这类 WebGL 扩展对象是 `getExtension()` 返回的、没有全局名字。一次 Turnstile 采集碰了 142 个
  接口，Window + Worker 两份清单覆盖 136 个，剩下 6 个全是这两类。

以前列在这里的两项已经补上：**继承链**现在是每个 WebIDL 接口的 `inherits` 数组（最近的父原型
在前，即 `Object.getPrototypeOf` 走的顺序；空数组是真实答案，表示父原型是 `Object.prototype`）；
**Worker 全局**有了自己的清单文件，见上面「读哪些表」。

### 1.0.5 指纹汇的返回值：哪些调用会深抓真值

没有开关，跟随核心 trace。核心 trace 对每次 DOM 调用都记返回值，但默认走的是浅序列化
（`ValueToJson`）：对象一律记成 `"[Object Int32Array]"`。**对指纹汇来说这等于没记**——反爬要的
就是那个数组里的数。所以有一份名单（`DOMTraceLog.h` 的 `IsDeepTraceApi`）走深序列化
（`ValueToJsonDeep`），把真值展开。

指纹相关的在册项：

| 汇 | 深抓拿到什么 |
|---|---|
| `toDataURL` | canvas 指纹的整串 base64（另有 `dom_image_data_url` 高价值标记） |
| `getImageData` / `readPixels` | 像素字节 |
| `getParameter` | WebGL 指纹主入口。`37445`/`37446` 的 vendor/renderer 字符串，以及 `MAX_VIEWPORT_DIMS`、`ALIASED_LINE_WIDTH_RANGE` 这类 `Int32Array`/`Float32Array` 的实际数值 |
| `getContextAttributes` | 返回的是字典（普通对象），整份展开 |
| `getSupportedExtensions` | 扩展名列表 |
| `getChannelData` / `getFloat*Data` / `getByte*Data` | audio 指纹的直接输入 buffer |
| `getRandomValues` / `digest` / `encrypt` 一族 | 密码学原语的输入输出 |
| `sendBeacon` | 回传的 payload |

实测一条（`getParameter(37446)`）：

```json
{"type":"call","interface":"WebGLRenderingContext","member":"getParameter","args":[37446],
 "return":"ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar"}
```

`MAX_VIEWPORT_DIMS` 这种返回 TypedArray 的则给
`{"type":"Int32Array","length":2,"byteLength":8,"truncated":false,"hex":"ff7f0000ff7f0000"}`
（= 32767, 32767）。浅序列化下这一条只会是 `"[Object Int32Array]"`。

**深抓不会触发任何 getter，这是它敢放在核心路径上的前提。** `ValueToJsonDeep` 只展开三类东西：
TypedArray / ArrayBuffer（直接读字节）、原生 Array、plain object（字典就属于这类）。WebIDL 对象
一律只报 `{"type":"object","class":"..."}`，Proxy 直接跳过不看。

因此**故意不在册**的有 `measureText` 和 `getShaderPrecisionFormat`：它们返回的是 `TextMetrics` /
`WebGLShaderPrecisionFormat`，都是 WebIDL 对象，深抓只会多得到一个类名，一个字段都拿不到；而
代价是这两个调用从此走全量栈捕获而不是浅栈。要拿它们的字段，得靠页面自己去读那些 accessor 时
产生的 `get` 记录——那才是页面真的读到的东西，而不是我们替它读的。

### 1.0.6 对象身份与记录 schema：怎么把两条记录接成一件事

没有开关，跟随核心 trace。这一节是给**消费方**的：一条记录说「读了 `transform`，值是 `none`」，
另一条说「对某个 iframe 调了 `getComputedStyle`」，要回答「那个 iframe 的 transform 是什么」就得
把两条接起来。接法不写清楚，消费方只能自己试——而试错的方式不止一种是错的。

#### 四个身份槽位

| 槽位 | 出现在 | 是谁 |
|---|---|---|
| `thisObj` | `call` / `get` / `set` | 调用或读写的接收者 |
| `returnObj` | `call` | 返回的对象 |
| `valueObj` | `get` / `set` | 读到 / 写入的对象 |
| `argsObj` | `call` | **实参里的对象**，与 `args` 同位，非对象位是 `null` |

四个槽位的值形状**完全一致**：`{"id","nativeId","class"}`，或整体 `null`。
`argsObj` 在整条实参都不是对象时也是 `null`——那是绝大多数调用，省下来的是文件体积。

> `args` 把对象渲染成 `"[Object HTMLDivElement]"`，只说了是什么类。`argsObj` 补的是**是哪一个**。
> 两者并列而不是替换：`args` 的形状没动，已有的离线脚本不受影响。

**`args` 曾经会在嵌套调用时串号，已修。** `args` 必须在 `_BEFORE` 渲染（被调方会改实参
对象，`postArgs` 就是为此存在的），到 `_AFTER` 才读；而 DOM 调用会嵌套（`insertBefore` 触发
事件、处理器里又调 `addEventListener`），内层调用的 `_BEFORE` 曾覆盖同一块线程局部缓冲，
于是外层记录的 `args` 装的是**内层那次调用的实参**。实测两份采集 16,643 条可比记录里 50 条
（0.30%）如此，`Window.getComputedStyle` 也中过。

修法是把 `args` 从一块缓冲改成**按嵌套深度索引的 8 槽栈**（`TraceDOMCallState::args[8]`），
`_BEFORE` 取槽、绑定返回时释放（RAII，覆盖所有退出路径，包括门在两者之间关闭和被调方抛出）。
超过 8 层的递归共用最后一槽——保留旧行为而不是分配，那种深度是页面在胡来，trace 不该把它变成
无界内存。每线程多 7 × 32 KB，一次性；热路径多一次自增和一次比较。

修后同一探测器在 DOM binding 层：**834 条，0 条不一致**（修前同口径 830 条里 3 条）。

两个读法上的注意：`args` 把代理绑定的 DOM 对象渲染成 `Proxy`（解包前的类），`argsObj` 报的是
解包后的类（`HTMLFormElement`）——同一个对象，不是串号。另外 JS 内置层（`Object.keys`、
`Map.get` 这些）的 `args` 是另一个发射点、另一块缓冲，本次没动它。

#### join 的规则：`id` 优先，为空再用 `nativeId`，两个都为空就是接不上

- **`id`** 是 JS 对象的 collection-stable unique id。call / wasm 层也按它命名，所以它是跨层
  join 的那个键。**代理绑定的接口拿不到它**：`TraceStableObjectId` 只读
  `NativeObject::maybeUniqueId()`，而有索引/命名 getter 或是 MaybeCrossOriginObject 的接口
  （`Configuration.py` 的 `proxy` 判定）不是 `NativeObject`。实测一次采集里 `id` 为空的 20 个类
  ——`CSSStyleProperties`、`HTMLDocument`、`NodeList`、`StyleSheetList`、`Location`、
  `DOMTokenList`…——**无一例外**都命中这条。
- **`nativeId`** 是背后 C++ 对象指针的混淆值，用来认出「两个 wrapper 指的是同一个 DOM 节点」。
  非 nsISupports 的 WebIDL 对象（`TextMetrics` 这类）和纯 JS 对象拿不到它。
- **两个哨兵都是带内的**：`id` 缺时是空串，`nativeId` 缺时是 16 个 0。**必须当作「没有」而不是
  值**，否则一次朴素的单字段 join 会把所有缺失项并成同一个对象。

三个指纹汇正好各占一种情况，没有任何单一字段能覆盖全部：

| 汇 | 返回对象带什么 | 只用 `nativeId` 会怎样 |
|---|---|---|
| `getBoundingClientRect` | `id` + `nativeId` | 能接上 |
| `getComputedStyle` | 只有 `nativeId`（返回的是代理绑定的 `CSSStyleProperties`） | 能接上 |
| `measureText` | 只有 `id`（`TextMetrics` 不是 nsISupports） | **接不上**，会误判成「返回值没被读过」 |

**`id` 只在单个进程内唯一**，而且一个进程里有多个 zone，同一个 `id` 值可能属于不同 zone 的两个
对象。join 必须带上 `pid`。实测一次采集里 1,494 个不同 `id` 中有 272 个在多个 pid 里出现过。

按这条规则，一次真实采集里 19 次 `getComputedStyle` 的实参**全部**能命名，其中 18 次能接回同一份
日志里被别处记过的那个元素——包括 Turnstile 那个 iframe，它的 `display` 是 `inline`。

#### 身份是只读取的，`argsObj` 不铸新 id

`thisObj` 和返回值那两个槽位会在必要时创建 id（`TraceEnsureStableObjectId`）；
`argsObj` 和 `Object.prototype.toString` 的 `thisObj` **不会**。理由是红线而不是性能：创建 id 会
分配、因而可能触发 GC，而一次只在开着 trace 时才发生的 GC 是页面原则上能测到的时序差异。
交叉引用位置不值得付这个代价——代价是那些没有被任何其它记录命名过的对象只剩 `nativeId`
或什么都没有。实测 `argsObj` 里 3,646 项有 `id`、15 项只有 `nativeId`、15,326 项两者皆空
（后者几乎全是 `Object.assign`/`keys` 一类内置调用收到的普通 JS 对象，本来也没有原生身份）。

体积代价实测两处，取决于页面的 DOM 调用密度：Turnstile 页 **0.24%**（492 MB 的采集里
1.19 MB），DOM 密集得多的 galaxyticketing 注册页 **0.59%**（206 MB 里 1.22 MB）。
只算核心 domtrace 那一层的话后者是 2.19%——引用这个数字时要说清分母是哪个。

#### 值字段随记录类型换名字

| type | 值 | 值的身份 | 独有 |
|---|---|---|---|
| `call` | `return` | `returnObj` | `args` / `argsObj` / `postArgs` |
| `get` | `value` | `valueObj` | — |
| `set` | `value` | `valueObj` | — |

语义上没错（返回值和被读到的值不是一回事），但它是个**沉默陷阱**：在 `get` 记录上读 `return`
拿到的是 `None` 而不是报错。这份日志的作者自己踩过——一版审计脚本统一读 `return`，于是把 92 条
带真值的 computed-style 读全报成「值没抓到」，差点得出「只记属性名不记值」的错误结论。

#### 跨层的同一个字段拼法不同：`origin_call_id` 与 `originCallId`

同一件事——「发起这条记录的 JS 调用是哪一个」——domtrace / jscall / message 记录拼作
`origin_call_id`，而 `http_packet` 的 `meta` 和 `index.jsonl` 拼作 `originCallId`，且藏在
`meta` 下一层。同样一组：`origin_pid` / `originPid`、`origin_execution_context_id` /
`originExecutionContextId`、`origin_async_context_id` / `originAsyncContextId`。

这不是笔误，驼峰名从 `HttpPacketCorrelation` 一路贯穿 IPC 结构、`nsIDOMTraceGate.idl`
和 27 个 C++/JS 文件，改写入侧不值。**现在两种拼法在 `http_packet` 里都有**：`meta` 和
`index.jsonl` 各多出六个蛇形键，值与驼峰原名逐字节相同（实测 21 个报文 21 个一致）。
按蛇形名写跨层 join 即可，驼峰名留给已有的读者。

一样是被踩出来的：一版链路审计只查 `origin_call_id`，报「网络报文 0% 带 JS 调用 id」，
实际是 41%。

#### `member` 带访问器前缀

getter / setter 的 `member` 是 `"get transform"` / `"set title"`，不是 `"transform"`。这来自
SpiderMonkey 的 `JS_GetFunctionDisplayId`，是继承来的命名不是本层的选择。实测一次采集里
17,518 条记录带前缀、涉及 578 个不同名字。**要和 §1.0.4 的 `env_inventory` 对账就得先剥前缀**
——那边记的是 `transform`。`type` 字段已经说明了是 get 还是 set，前缀不携带额外信息。

没有改掉它：那会改变一个已有字段的语义，是兼容性决定而不是缺陷修复。

### 1.1 `MOZ_DOM_JSCALL_DETAIL_NATIVE`：抓 native 的值

detail 的脚本过滤器以 `script->filename()` 为依据，而 native 没有脚本，所以过去值捕获
只能停在脚本函数上。这是**筛选方式的限制，不是读值能力的限制**：读值只看
`JS::Value` 的内部表示，不关心它被传给了什么。

之所以需要：**用 JS 写的字节码 VM，并不在 JS 里做计算**。它的派发循环把每个操作都交给
内置函数——实测一次验证码流程里 `Atomics.load/store/add/and/or/xor` 被调用
**1,113 万次**，而脚本调用只有 **142 万次**。只有脚本过滤器的话，你看得见派发，看不见
任何一次算术。操作数流才是算法本身。

```powershell
$env:MOZ_DOM_JSCALL_DETAIL_NATIVE = "load,store,add,and,or,xor"
```

逗号或分号分隔、大小写敏感子串，与 URL 过滤器同一套匹配。**函数名不带命名空间**：
`load` 会同时命中 `Atomics.load` 和任何其它叫 `load` 的 native。实测本地空白页会因此
多出几百条浏览器自身的 `Set.prototype.add`，量很小但要知道它在那儿。

设了 `MOZ_DOM_JSCALL_SCRIPT_URL` 时会**自动打开** `MOZ_DOM_JSCALL_NATIVE`并在 stderr
说明：否则 native 在分配 func id 之前就被丢掉，过滤器一条也匹配不到，还不会告诉你为什么。

**对象参数只记身份，不记内容**（`identity_only` 标志，payload 是 8 字节身份）。这是
正确性要求而非偏好：值得抓的 native 恰好是 VM 派发的目标，其第一个参数是 VM 自己的
内存——同一个 typed array，每次操作都重传一遍。抓内容等于每次调用写下整个数组，几百万
次下来比整个 trace 的其余部分还高出几个数量级，页面会直接卡住。重建 VM 需要的是
「身份 + 下标 + 操作数」，数组内容由操作序列推出来，不必存在每一条旁边。

> **身份必须跨 GC 稳定，这一点是实测出来的。** 早先的实现用对象地址的哈希做身份。分代
> GC 晋升 nursery 对象是靠**拷贝**的，所以地址会变——实测同一个 `Int32Array` 在一次
> minor GC 前后报出了**两个不同身份**（`gc_identity_probe.html` + `run_gc_identity_probe.ps1`）。
> 按这种身份做 join，会把一个对象报成两个，对象图就是一个自信的错答案。
>
> 现在用的是引擎自己的 collection-stable unique id。对 `NativeObject` 而言这个 id 存在
> 对象自己的槽里，读它只是几次 load；**建立**它会分配内存、可能触发 GC，所以建立放在
> `JSCallEnter` 侧能 root 的安全点（`JSCallEnsureStableIds`），捕获路径只读。万一拿不到，
> 会打 `unstable_identity` 标志，解码器显示成 `?` 前缀而不是 `#`——绝不让不可 join 的
> 东西看起来可以 join。
>
> 真实站点实测：1,114,204 次带 TypedArray 首参的 Atomics 调用，`unstable_identity` **0 条**，
> 解析出 **33 个不同的内存数组**（id 连续、首见集中在同一段 call_id 区间，说明是 VM 初始化
> 时一次性创建的）。修复前这个数字是不可知的——GC 会把它打乱，且每次运行都不一样。

一条记录长这样（`jscall_decode.py` 输出）：

```
store  arg0=#44d10f41d8abee90(typedarray) [id]  arg1=3  arg2=286331153  return=286331153
load   arg0=#44d10f41d8abee90(typedarray) [id]  arg1=3                  return=286331153
add    arg0=#44d10f41d8abee90(typedarray) [id]  arg1=3  arg2=1          return=286331153
```

同一个 `arg0` 身份贯穿全程，于是这三条就是「同一块内存、下标 3、写入 / 读出 / 加一」。

**务必设 `MOZ_DOM_JSCALL_SCRIPT_URL`。** 不设时会把浏览器自身的 chrome/resource
JS 全部记下来：实测本地空白页 12 秒就能写出 6 MB 以上；收窄到单个脚本后同一页
只剩几百字节。判定按 ScriptSource 首次见到时做一次并缓存，不是每次调用做字符串
扫描——这正是旧实现每调用十几次 `strstr` 的开销来源。

> 注意：设了脚本过滤后**不再记录 native 调用**（如 `Math.sqrt`）。native 没有自己
> 的脚本 URL，过滤器对它无从判断，而在锁定单脚本时混入全部 native 只会淹没目标。

**打真实站点时建议加 `MOZ_DOM_JSCALL_DURATION=0`。** 这是单项收益最大的一处：开着
duration 时每次调用要读两次 `TimeStamp::Now()`（Windows 上是 QPC），这两次时钟读
**压过**记录本身的 32 字节 memcpy 和全部 TLS 写入，是每调用固定开销的主要来源。关掉
等于把固定开销砍掉一个量级，而 `duration_us` 对「反推执行逻辑」本来就不是必需的。
对时延敏感的反爬 VM（Turnstile 一类）尤其该关。

产出两个文件：

```text
jscall\trace_jscall_process_<pid>.bin          定长 32 字节记录
jscall\trace_jscall_process_<pid>.dict.jsonl   func_id -> 名字/URL/行列
jscall\trace_jscall_process_<pid>.detail.bin   入参/this/返回值（设了 DETAIL 才有）
jscall\trace_jscall_process_<pid>.opcode.bin   逐 opcode（设了 OPCODE_URL 才有）
```

### opcode 级 trace（`MOZ_DOM_JSCALL_OPCODE_URL`）

调用级 trace 看不穿自建字节码 VM：整个算法跑在一个宿主函数的派发循环里，调用树上
只有一个帧在做所有事。逐条记录宿主字节码 op 才能还原它内部的控制流。

```bash
jscall_decode.py <bin> --opcodes
```

```text
823 opcode records
  call=1      runMarshal      pc=0    op=190
  call=1      runMarshal      pc=5    op=130
```

记录 16 字节：`call_id`(8) + `func_id`(4) + `pc`(低 24 位) 与 `op`(高 8 位) 打包成
一个 word。**没有时间戳**——逐 op 读时钟是每秒上百万次系统调用，而且对控制流重建
毫无价值；顺序由线程内的文件顺序给出，`call_id` 把一段 op 流绑回执行它的那次调用。

过滤器与调用过滤器**分开**，因为量级差几个数量级，必须能单独收窄到一个脚本。同样
是按 ScriptSource 首见判定一次并缓存。命中的脚本会把解释器的 interrupt 位钉住，
所以这些 op 只在 C++ 解释器层可见——进了 JIT 就没有了。

### 真值抓取（detail）

设 `MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL` 后，命中脚本的每次调用会额外记录
`this`、每个入参、以及返回值。用 `jscall_decode.py <bin> --values` 查看：

```text
marshal(3)  .../page.html:26:17  call_id=2
    arg0     string       'pow-seed-abc'
    arg1     typedarray   '010203faff'
    arg2     object       'Object'
    return   int32        18
```

**读取纪律（这是不被探测的关键）**：只读值已有的原生表示。

| 类型 | 记录内容 |
|---|---|
| number / boolean / string | 原值（字符串转 UTF-8） |
| TypedArray / ArrayBuffer | **原始字节的 hex**，不做有损文本化 |
| 普通对象 / 函数 | **只记类名，绝不遍历属性** |
| rope 字符串 / BigInt | 标 `unreadable`，不强行展平 |

不遍历属性意味着 **getter、Proxy trap、惰性访问器一个都不会被触发**，页面无法通过
自己的对象察觉被读。展平 rope 会分配内存并可能跑 JS，所以宁可标为不可读也不做。
残留信号只剩时间。

**跨 compartment 的 buffer 必须先解包**：`JS::IsArrayBufferObject` /
`JS_IsArrayBufferViewObject` 这类判定走 `canUnwrapAs`，**会穿透包装**回答 true；而
`JS::GetArrayBufferLengthAndData` 用的是不解包的 `as<ArrayBufferObject>()`。两者混用
就是类型混淆：debug 断言崩溃，opt 下把包装对象当 buffer 读出垃圾长度和垃圾指针再
memcpy。所以取字节前一律先 `JS::UnwrapArrayBuffer` / `js::UnwrapArrayBufferView`，
解包失败标 `unreadable`，绝不猜。

能踩到这条路径的对象比想象中窄，`ccw_value_capture_probe.html` 把三种情形都跑一遍：
**同源 iframe 不算**——`SelectZone` 会让它复用祖先的同源 compartment，对象是裸的；
**跨源对象也不算**——`CheckedUnwrapStatic` 直接拒绝，压根进不了 buffer 分支。真正
能把包装对象送进字节读取的是**同源的顶层浏览上下文**（`window.open` 开出来的同源
窗口走 `setNewCompartmentAndZone()`，自成 compartment，又因同源而通过安全检查）。

离线解码：

```bat
python trace\tools\jscall_decode.py trace_jscall_process_1234.bin           :: JSONL
python trace\tools\jscall_decode.py trace_jscall_process_1234.bin --tree    :: 调用树
python trace\tools\jscall_decode.py trace_jscall_process_1234.bin --stats   :: 汇总
```

**覆盖边界（重要，别误判成漏记）**：只覆盖 C++ 解释器可见的调用边界。函数升到
Baseline/Ion 之后，它发出的调用走 inline cache，不产记录。这是刻意的——旧实现
正是靠取消这个优化才拿到全覆盖，代价是把被观测的反爬代码本身拖慢到超时。
对「反推执行逻辑」影响有限：**每个函数、每条路径的第一次执行都在解释器里**，
调用边至少被记一次；丢的是热循环里的重复次数。

**每条记录字段**：`call_id`、`parent_call_id`、`depth`、`func_id`、`argc`、
`op`(call/construct)、`ok`(是否抛异常)、`entry_path`(stack/inline/jit)、
`native`、`self_hosted`、`duration_us`，以及三个语义位：

| 字段 | 含义 |
|---|---|
| `parent_exact` | `false` 时 `parent_call_id` **不是**直接调用者，中间至少跳过了一个 JIT 帧。这条边只保证可达性，不保证相邻 |
| `subtree_truncated` | 被调方在 JIT 里执行，它自己的子调用不在日志里。子树是被截断的，不是真的为空 |
| `suspended` / `resumed` | 帧停在 `await`/`yield` 而不是返回 / 本条是先前挂起帧的续跑 |

只在 leave 写一条：`call_id` 单调递增 = enter 顺序，文件顺序 = leave 顺序，
两者合起来可完整重建交错的进出场序列。

**`parent_exact` 为什么必须看**：`parent_call_id` 记的是「最近的一个**被追踪到**的
祖先」。中间帧如果在 JIT 里跑，这条边就断言了一个源码里不存在的调用 `A→C`。这不是
精度损失，是**伪造的边**，而且从日志本身无法证伪——所以必须靠这个位区分。

**跨模块关联**：cookie / storage / descriptor / event / exception / message /
http_packet 的 `origin_call_id` 等于触发它的那次调用的 `call_id`，可直接 join。
`origin_call_id == 0` 表示当时没有被追踪的 JS 调用在栈上（顶层语句、parent 进程的
后端操作、或被脚本过滤器排除的代码）。

**验收**：`.dict.jsonl` 的最后一行应是 `{"type":"jscall_stats",...}`，且
`dropped_limit` / `dropped_gate` / `dropped_chunks` / `io_errors` 全为 0。
**缺这一行就说明该进程没有正常关闭，日志按截断处理**。`jscall_decode.py --stats`
会直接把这些打出来，并在缺失时提示 `unclean shutdown`。

### 二进制格式参考（手工解码 / 自写工具用）

`jscall_decode.py` 是权威解码器，下面的布局与它的 `struct` 格式串一一对应
（`RECORD_FORMAT = "<QQIIHHBBH"`）。**所有整数都是小端**。源头定义在
`JSCallRecord.h` / `JSCallDetail.h`，改格式必须同时改这三处。

#### `.bin`：文件头 16 字节 + 定长 32 字节记录

文件头（`<4sHHII`）：

| 偏移 | 长度 | 字段 | 值 |
|---|---|---|---|
| 0 | 4 | magic | `JSCT` |
| 4 | 2 | version | `1` |
| 6 | 2 | recordSize | `32`，解码前必须校验 |
| 8 | 4 | pid | 进程 id |
| 12 | 4 | reserved | 0 |

第 N 条记录（N 从 0 起）位于字节 `16 + 32*N`。**一个偏移减 16 之后不能被 32 整除，
就不是记录起点**，读出来的每个字段都是错位的。记录布局：

| 偏移 | 长度 | 字段 | 含义 |
|---|---|---|---|
| +0 | 8 | `callId` | 高 16 位 = 线程索引，低 48 位 = 该线程内的进入序号（即 `seq`）。**只在同一线程内可比较** |
| +8 | 8 | `parentCallId` | 最近一个**被追踪到**的祖先调用；0 = 顶层。是否为直接调用者看 `flags` bit6 |
| +16 | 4 | `funcId` | 查 `.dict.jsonl` 得名字 / URL / 行列 |
| +20 | 4 | `durationUs` | 微秒。`0` = 未测量（`MOZ_DOM_JSCALL_DURATION=0` 时恒为 0） |
| +24 | 2 | `depth` | 调用深度 |
| +26 | 2 | `argc` | 实际传入参数个数 |
| +28 | 1 | `flags` | 位字段，见下表 |
| +29 | 1 | `entryPath` | `0` unknown / `1` stack / `2` inline / `3` jit / `4` baseline |
| +30 | 2 | reserved | 0 |

`flags` 各位：

| 位 | 值 | 名字 | 解码器输出 | 含义 |
|---|---|---|---|---|
| 0 | `0x01` | Construct | `op: "construct"` | 是 `new` 调用，否则 `op: "call"` |
| 1 | `0x02` | Ok | `ok: true` | 正常返回；**为 0 表示抛出异常** |
| 2 | `0x04` | Native | `native` | 被调方是 C++ native / builtin |
| 3 | `0x08` | SelfHosted | `self_hosted` | 被调方是引擎自宿主 JS |
| 4 | `0x10` | Suspended | `suspended` | 帧停在 `await`/`yield` 而不是返回 |
| 5 | `0x20` | Resumed | `resumed` | 本条是先前挂起帧的续跑 |
| 6 | `0x40` | CallerJit | `parent_exact: false` | 直接调用者在 JIT 里，`parentCallId` 至少跳过了一帧 |

典型值：普通脚本函数正常返回 → `flags = 0x02`；native 正常返回 → `0x06`。
一个脚本函数如果解出 `flags = 12 (0x0C)`，含义是「native + self-hosted + 抛了异常」，
基本可以断定偏移读错了。

#### `.dict.jsonl`：`funcId` → 函数身份

每行一个 JSON，按首次见到顺序追加，`func_id` 从 1 起连续（`0` 表示函数表满、没能分配 id，
这类记录会被丢弃并计入 `jscall_stats.dropped_no_func_id`）：

```json
{"func_id":10624,"kind":"js","name":"d9cc/G.methods.searchFlight<","url":"https://www.9air.com/js/chunk-617d45ea.16e90f98.js","line":1,"column":672751,"key":"7f3a..."}
```

`kind` 取值：`js`（页面脚本）、`selfhosted`（引擎自宿主 JS）、`native`、`wasm`；后两者
`url`/`line`/`column` 为空或 0。`column` 是**被调函数自身**的定义位置，不是调用点；
旧版 JSONL 的 `caller_column` 在新格式里没有对应字段。最后一行是 `{"type":"jscall_stats",...}`（见上文「验收」），可能夹有
`jscall_func_stats` 行，读 dict 时按 `type` 字段跳过即可。

#### `.detail.bin`：入参 / `this` / 返回值（只有设了 `MOZ_DOM_JSCALL_DETAIL_*` 才有）

文件头 16 字节，magic `JSCV`，其余同 `.bin`（`headerSize` 字段应为 16）。之后是变长记录，
每条 = 16 字节头（`<QHBBI`）+ `byteLength` 字节 payload：

| 偏移 | 长度 | 字段 | 含义 |
|---|---|---|---|
| +0 | 8 | `callId` | 与 `.bin` 里的 `callId` join |
| +8 | 2 | `slot` | `0..N-1` = 第几个参数；`0xFFFE` = `this`；`0xFFFF` = 返回值 |
| +10 | 1 | `type` | 值类型，见下表 |
| +11 | 1 | `flags` | `0x01` truncated / `0x02` unreadable / `0x04` identity-only / `0x08` unstable-identity |
| +12 | 4 | `byteLength` | payload 长度 |

`type` 编码：`0` unknown、`1` undefined、`2` null、`3` boolean、`4` int32、`5` double、
`6` string、`7` bigint、`8` object、`9` array、`10` typedarray、`11` arraybuffer、
`12` function、`13` symbol、`14` magic。

payload 按类型解读：boolean 1 字节；int32 4 字节；double 8 字节；string / object /
function 为 UTF-8（object、function 只是类名）；typedarray / arraybuffer 为原始字节；
array 为 JSON 文本。`flags` 带 `0x04` 时 payload 固定 8 字节，是对象身份 id，
解码器显示为 `#xxxxxxxxxxxxxxxx`（带 `0x08` 时前缀改为 `?`，表示不可 join）。

**旧版 JSONL 的 `arg_types` / `result_type` 就对应这里**：按 `callId` 收齐所有 slot，
`slot 0..argc-1` 的 `type` 即参数类型，`slot 0xFFFF` 的 `type` 即返回值类型。
没有 detail 文件时，新格式**只知道 argc，不知道参数类型**。

#### `.opcode.bin`：逐字节码（只有设了 `MOZ_DOM_JSCALL_OPCODE_URL` 才有）

文件头 16 字节，magic `JSCO`，`recordSize = 16`。记录（`<QII`）：`callId`(8) +
`funcId`(4) + `pcAndOp`(4)，其中 `pc = pcAndOp & 0xFFFFFF`，`op = pcAndOp >> 24`。

#### 手工解一条记录

已知文件偏移时，先验证对齐，再按布局拆：

```python
import struct
from pathlib import Path

data = Path("trace_jscall_process_1604.bin").read_bytes()
offset = 247415760
assert (offset - 16) % 32 == 0, "not a record boundary"

(call_id, parent_call_id, func_id, duration_us,
 depth, argc, flags, entry_path, _) = struct.unpack_from("<QQIIHHBBH", data, offset)

print("call_id       ", call_id, "thread", call_id >> 48, "seq", call_id & ((1 << 48) - 1))
print("parent_call_id", parent_call_id)
print("func_id       ", func_id)
print("duration_us   ", duration_us)
print("depth         ", depth)
print("argc          ", argc)
print("ok            ", bool(flags & 0x02))
print("construct     ", bool(flags & 0x01))
print("native        ", bool(flags & 0x04))
print("parent_exact  ", not (flags & 0x40))
print("entry_path    ", {0: "unknown", 1: "stack", 2: "inline", 3: "jit", 4: "baseline"}[entry_path])
```

只想看某一条时用解码器更省事：

```powershell
python trace\tools\jscall_decode.py trace_jscall_process_1604.bin | Select-String '"call_id": 19491755'
```

#### 与旧版 JSONL 字段的对照

| 旧版 JSONL | 新版位置 |
|---|---|
| `jscall_frame` (enter) + `jscall` (leave) 两条 | 合并为一条 32 字节记录，只在 leave 写。enter 顺序按 `callId` 恢复 |
| `call_id` / `parent_call_id` / `depth` / `argc` | `.bin` 记录内同名字段 |
| `ok` | `.bin` `flags` bit1 |
| `duration_us` | `.bin` +20 |
| `seq` | `callId` 低 48 位 |
| `ts` | **无**。顺序靠 `callId`（enter）与文件顺序（leave） |
| `callee_name` | `.dict.jsonl[funcId].name` |
| `caller_column` | **无**。只有被调函数自身的 `column` |
| `arg_types` / `result_type` | `.detail.bin` 各 slot 的 `type`；未开 detail 则无 |

### 能力边界（真实站点实测，非估计）

下列数字来自对一个 Alibaba AWSC/baxia 反爬流程（galaxyticketing.com 注册+九宫格
验证码）的完整采集，单进程 830,090 条调用记录、1,761,814 条真值记录。

| 边界 | 实测数字 | 含义 |
|---|---|---|
| **只观测调用/返回接口** | fireyejs 855,105 次调用，平均 **0.07** 个参数，返回值 **93.8% 是 `undefined`**，返回字符串中位长 **2 字节** | 值跨函数边界才抓得到。自建 VM 靠闭包捕获的状态计算，函数体内拼出的值没有采样点——AWSC 的 `ua` 指纹串在 176 万条真值里**从未**作为返回值出现 |
| **调用图多数是推断的** | `parent_exact` 仅 **1.4%**（12,068 / 830,090），`truncated_subtrees` 5,206 | 98.6% 的 `parent_call_id` 是"最近的被追踪祖先"而非真实调用者，中间跳过了 JIT 帧。这条边只保证可达性，不保证相邻 |
| **需要"动手才能读"的值被刻意拒绝** | 不可读 **0.01%**（211 / 176 万），全是 rope 字符串；68,313 个对象只记类名 | 遍历属性会触发 getter 和 Proxy trap，那正是"不可探测"的立身之本，所以宁可标不可读 |
| **native 的值需显式开启；WASM 走另一层** | native 抓到操作数 3,266,178 次（命中过滤器的 100%）；wasm 在 jscall 里恒为 0 值 | 曾经 `JSCallShouldCaptureValues` 要求 `isInterpreted() && hasBytecode()`，native 与 WASM 导出一律无值——那是**筛选方式**的限制而非读值能力的限制。现在 native 由 `MOZ_DOM_JSCALL_DETAIL_NATIVE` 按函数名开启（§1.1；对象参数只记身份）。WASM 导出在 jscall 里仍恒为 0 值，其参数与返回值在独立的边界层（§3.3 的 `wasm_call`，按 wasm 签名精确定型） |

一句话概括：**跨函数边界的数据流可还原，函数内部的计算不可见。**

#### 逐条量化：自建页面的还原实测

上面那些数字来自真实站点，但真实站点没有真值可比对，所以"还原了多少"只能定性。
`reconstruct_page.js` 是为此写的：它把反爬常见的十几种手法各做一遍，**自己把真值 POST
给本地服务器落盘**，再由 `reconstruct_from_trace.py` **只读 trace** 回答同一批问题并机械
对比。跑法见 `run_reconstruct_probe.ps1`。

16 个问题里 13 项完整还原、2 项部分、1 项按文档确认不可还原：

| 问题 | 结果 |
|---|---|
| 字符串解码器的 (密文 → 明文) 对 | **5/5** |
| 宿主安装（`document.__decrypt_entry` 等） | 完整，含目标对象与属性名 |
| 伪装访问器 | 完整，`detail=accessor:get:fakeWebdriverGetter`，`func_id` 可接调用层 |
| 暂存后删除的全局 | 安装与删除都可见 |
| 三个面的不存在性探测 | **3/3** |
| 环境读取的 API 与取值 | **6/6** |
| 每步都跨函数边界的哈希 | **11/85**，见下 |
| 全留在函数内部的 48 步哈希 | **47 个中间值一个都没泄漏**，只有跨边界的最终结果可见 |
| native 派发的 VM 操作数 | **10/10** |
| 运行时 `new Function` 的源码 | 完整 |
| worker 双向消息链 | 完整配对 |
| 请求体 | 摘要预览与 HTTP packet 全量都有 |
| 请求归因到发起函数 | 完整，但**必须走 async 桥**，见下 |

**覆盖率随调用次数呈 U 形，不是单调下降。** 那个 85 步的哈希只还原了 11 步，11 次调用的
`entry_path` 全是 `inline`(9) 与 `stack`(2)、没有一条 `jit`。但**不要**由此得出"只能看到前
11 次"——真实站点的数据否证了这一点。DataDome bundle 里的同类函数：

| 函数 | 记录到的调用数 | entry_path 分布 |
|---|---|---|
| `gA` | 2,258 | `stack` 11、`baseline` 2,247 |
| `s` | 3,603 | `inline` 41、`stack` 14、`jit` 47、`baseline` 3,501 |
| `E`（解密壳） | 11,676 | `inline` 10、`stack` 1,234、`jit` 8,466、`baseline` 1,966 |

规律是三段：

1. **约前 10 次**：C++ 解释器，`inline` / `stack`，一直可见
2. **约 10 到 100 次**：Baseline **解释器**窗口，**没有钩子，这是盲区**
3. **约 100 次以上**：函数被 Baseline **编译器**接手，`MOZ_DOM_JSCALL_BASELINE=1` 重新看见；
   `entry_path=jit` 的调用也是记录在案的（那个标志说的是被调方进了 JIT、其子树被截断，
   不是这条调用没记）

那个 85 次的合成循环恰好整段落在第 2 段里，所以显得最差。**做很多次的事反而比做几十次的
事覆盖得好**，这一点反直觉但可实测。`MOZ_DOM_JSCALL_BASELINE=1` 的价值也因此被
§Baseline 覆盖里那个 16% 低估了——那个数字来自一个停在第 2 段的循环。

同一页面的 `parent_exact` 是 **81.7%**（58/71），远高于真实站点的 1.4%——差别全在热度，
这个页面是冷代码。

#### 两个跨进程 ID 陷阱（写分析工具必看）

**它们不会报错，会给出看起来完全合理的错误答案。** 两个都是实测踩到的：

| id | 陷阱 |
|---|---|
| `targetId` / `globalId` | 六个进程的 `SystemGlobal` 都报 `0xb`。按 id 单独 join 会把无关对象合并 |
| jscall 的 `call_id` | 不带 pid 做 join 时，内容进程的一个 fetch 解析到了父进程 `EnterprisePoliciesParent.sys.mjs` 里的调用链 |

**所有对象与调用 id 都是进程内唯一的，join 必须带 `pid`。**

#### 跨 `await` 的归因：async 桥

页面把指纹 POST 出去时通常已经在 `await` 之后，此时它的同步调用链上只有 `fetch` 自己，
**向上走不到发起它的函数**。要跨过这个断点只能走 async 桥：

```
fetch 记录的 async_context_id
  → 同 async_context_id 的 async_resume（其 execution_context_id 与 fetch 记录一致）
  → 同 async_context_id 的 async_schedule
  → 它的 async_parent_call_id 就是挂起的那个函数
```

实测在自建页面上这条链走通到 `main` at `reconstruct_page.js:228`，cause 为
`async_function_await`。

**这条桥曾经在内容进程里完全不存在。** async 边是**惰性落盘**的（17 万条边只有 0.15%
被引用过，所以改成先驻留、等有记录引用了才写），而 `fetch` / `xhrNative` 记录当时既不带
`execution_context_id` 也不调 flush，于是内容进程一条 async 记录都没有——`main` 只留下
一个 `suspended=true`，没有任何东西说明后面哪些调用是它的续跑。现在两类记录都带上了这
两个 id，并在落盘前 flush 驻留的边。真实站点实测：async 边 645 → 806，带执行上下文的
网络请求 **0 → 301 条（848 条中的 35.5%）**，红线三项与对照臂完全持平。

已验证可做到的事（同一次采集）：把"用户动作 → JS 调用链 → 网络请求"接成完整因果链
（`bindEvent → fire → verify → ajax`，经 `originCallId` 接上真实报文）；拿到完整
且未截断的请求/响应体与两个 `program.wasm`；还原跨边界的数据流（`_rand` 来自
`randomUUID()` 返回值，`encryptToken` 来自服务端下发→存字符串表→`Ka(74)` 取回）；
枚举 13,642 次属性探测并 100% 归因到发起它的 JS 调用。

### 可探测性自测（探针）与它测不到的东西

| 探针 | 测什么 |
|---|---|
| `undetectability_probe.html` | 页面可见状态：`toString`、descriptor、原型链、stack 形状、getter/Proxy trap 计数、递归深度 |
| `fingerprint_probe.html` | 相对时序：冷调用 / `new Function` / 多态调用相对纯计算循环的比值 |
| `ccw_value_capture_probe.html` | 跨 compartment 的 buffer 进入真值抓取时不崩溃（见上） |

**这套 A/B 有一个方法论缺口，别拿它的「全等」当免罪符**：前两个探针比的是**同一颗
二进制**开关量开 vs 关。凡是**编译期就烙进二进制、与开关无关**的差异——常驻结构体
变大导致的递归深度上限下降、恒存在的分支、头文件带来的布局变动——A/B 一律看不到，
于是「无差异」是**假阴性**。真实威胁模型是**改版二进制 vs 官方 Firefox**，要测这条
必须另跑一颗官方版做基线对照。

同样别把「页面可见状态无差异」读成「不可检测」：解释器层冷调用的时序膨胀是真实的，
一个反复跑短命、多态、故意留在解释器里的挑战函数（正是反爬 VM 的典型形态）能用
相对时序量到它。这套方案**相对 CDP / Debugger API / JS 层 Proxy 明显更隐蔽**是站得住
的结论；**绝对不可检测**不是。

### Baseline 覆盖（`MOZ_DOM_JSCALL_BASELINE=1`，默认关）

实测同一个 2 万次的热循环：

| 配置 | 抓到的调用数 | 占比 |
|---|---|---|
| 默认（只有解释器可见边界） | 11 | 0.055% |
| 加 `MOZ_DOM_JSCALL_BASELINE=1` | 3257 | 16.3% |

**它补的是 Baseline 编译器那一层，不是全覆盖。** 剩下的缺口是两个仍然没有钩子的
tier：Baseline 解释器窗口（warmup 10~100）和 **Ion**（warmup ≥1500）。热循环最终
归宿是 Ion，所以比例停在 16% 而不是 100%。这些记录的 `entry_path` 是 `baseline`。

代价：命中脚本的每次 Baseline 调用多两次 `callWithABI`。**不做任何 de-opt**——不
禁 Ion、不阻内联、不改 IC 策略，所以不会重蹈旧实现把反爬 VM 拖到超时的覆辙。默认
关闭，需要时再开。

**已知限制**：
- `call_id` 高 16 位是线程号、低 48 位是线程内计数器（避免全局原子）。跨线程的
  `call_id` 不可比较，同线程内可比较。
- **关闭时只排空「执行关闭的那条线程」自己的残块**，以及所有干净退出的线程（它们
  在线程退出时自己交还）。这不是偷懒：块指针指向各线程的 thread_local，从关闭线程
  去读别人的槽，一头撞上那条线程无锁的 `JSCallEmit`（块可能已被回收），另一头撞上
  它的线程退出（TLS 被释放，留下野指针去 fwrite 和 free）。要挡住这两条只能在**每次
  调用**的路径上加同步，而那正是这套设计唯一不能付的成本。代价是：进程关闭那一刻
  仍在跑 JS 的线程，会丢掉它当前那个未满的块。
- 写盘线程卡在 fwrite（磁盘满/慢盘）时，关闭**不会**无限等待：超时后放弃 join 直接
  弃线程，宁可泄漏一条线程也不让浏览器关不掉。
- 统计计数器是每线程累加、在交块和线程退出时才并入全局，所以 `jscall_stats` 的
  `recorded` 相对真实值最多滞后「每线程一个块」。这是刻意的——原来每次调用一发
  全局 `lock xadd`，恰好抵消了 `call_id` 按线程分配的意义。

## 全量异步事件 Trace

设置 `MOZ_DOM_EVENT_TRACE_FULL=1`，记录指定鼠标、Pointer、键盘、触摸和滚轮事件。事件派发线程只复制原生 C++ 字段并尝试非阻塞入队，不执行文件打开、写入、刷新、JS Realm 切换或 JS 栈采集。

### 启用与输出

```bat
set MOZ_DOM_TRACE=1
set MOZ_DOM_TRACE_FILE=C:\out\trace.jsonl
set MOZ_DOM_EVENT_TRACE_FULL=1
firefox.exe
```

只增加这一个事件专用环境变量。输出继续由 `MOZ_DOM_TRACE_FILE` 派生：

```text
C:\out\event\trace_event_process_<pid>.jsonl
```

未增加事件专用路径、队列、刷新、采样或事件列表环境变量。事件 trace 跟随核心 sink，`MOZ_DOM_TRACE=0`
会把它关掉（jscall 等独立配置的模块不在此列，见 §6）。

### 事件范围与记录语义

```text
mousemove mousedown mouseup click
pointermove pointerdown pointerup
keydown keypress keyup
touchstart touchmove touchend
wheel
```

每次原生派发产生一条 `event_dispatch`。Listener 调用使用 `listener_call`，通过同一个 `event_id` 与当前派发关联；线程局部嵌套作用域负责恢复父事件 ID。

- 14 类原生 `event_dispatch` 全部记录。
- 点击、按键、Pointer down/up、Touch start/end 的 Listener 详情完整记录。
- `mousemove`、`pointermove`、`touchmove`、`wheel` 的 Listener 详情保持 1/32 采样。
- 慢调用、异常、`preventDefault()` 或停止传播的 Listener 在全量模式下强制保留。
- 未启用全量模式时保持原有采样和输出行为。

### 异步写入与性能控制

独立 C++ 实现 `EventTraceLog.cpp` 管理固定队列和 Writer 线程。Producer 不等待 Writer，Writer 独占 `fopen`、`fwrite`、`fflush` 和 `fclose`。

- 512 个固定槽位，每个 16 KiB，总预算约 8 MiB/进程。
- Producer 使用 `std::mutex::try_lock`；锁竞争、队列满或记录超限时丢弃最新记录。
- 批量目标 256 KiB，刷新周期 250 ms，统计周期 1 秒。
- 关闭时最多使用 2 秒排空已排队记录，再安全等待正在执行的 C Runtime 文件操作结束。
- `MOZ_DOM_TRACE_GATE` 关闭时停止接收新记录，并等待 Writer drain 和 `fflush`。
- `event_trace_stats` 提供 attempted、queued、dropped、oversize、shutdown drop 和高水位统计。

### 实现与验证入口

- `EventTraceLog.h/.cpp`：快照接口、队列、Writer、JSONL、统计和关闭流程。
- `dom/events/EventDispatcher.cpp`：原生派发捕获，不读取页面 JS 属性。
- `dom/events/EventListenerManager.cpp`：Listener 元数据、采样和 `event_id` 关联。
- `dom/events/test/gtest/TestEventTraceLog.cpp`：过滤、嵌套关联和队列溢出测试。
- 定向构建：`mach build dom/bindings dom/events --allow-subdirectory-build`。
- GTest 需要启用测试的对象目录；浏览器实测还应检查 JSONL 可解析性、各进程最终统计和丢弃计数。

## Stderr warning triage

Browser stderr is not the same as trace failure. For example, this target URL:

```text
https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=shoes
```

may print:

```text
[stderr] JavaScript warning: <url>, line 1: unreachable code after return statement
```

This is a SpiderMonkey page-script compile warning. It means the target script has
statements after `return`; it does not by itself mean DOMTrace failed.

Treat stderr lines as follows:

| stderr pattern | Meaning | trace failure |
|---|---|---|
| `JavaScript warning:` | Target page / browser JS warning | no |
| `JavaScript warning: ... unreachable code after return statement` | Target script has unreachable code after `return` | no |
| `[DOMTrace] ERROR:` | DOMTrace could not open or write a trace output path | yes |

For automation, judge a trace run by process exit, page load state, trace output
files, and `[DOMTrace] ERROR` lines. Do not fail a run only because stderr contains
`JavaScript warning`.

### `Script terminated by timeout` 的归属必须逐条查

这条警告是卡顿判据里权重最高的一个，但计数本身**不能直接用**，因为它包含了不属于被测站点
的部分。别只数条数，把来源打出来：

```powershell
Select-String -Path browser.stderr.log -Pattern "Script terminated by timeout" |
  ForEach-Object { (($_.Line -split ',')[0]) -replace 'JavaScript warning: ','' } |
  Group-Object | Select-Object Count,Name
```

一次实测的 6 条里，2 条来自 `www.skyscanner.com.au` 和 `gum.criteo.com`——和被测站点毫无
关系。来源是 Firefox 会把 about:newtab **预加载进隐藏浏览器**，即使命令行已经指定了 URL；
那个页面去拉赞助磁贴，于是广告站的脚本也在同一个进程组里跑，超时被算进这次运行。三分之一
的计数是噪音，足以左右一次卡顿结论。

`launch_galaxyticketing_headed.ps1` 现在会往 profile 写 `user.js` 关掉这些。自建启动脚本要
自己加，关键是 `browser.newtabpage.preload=false` 加上
`browser.newtabpage.activity-stream.showSponsoredTopSites=false` 和
`browser.topsites.contile.enabled=false`。这些都是 chrome 侧的 pref，不改变被测页面能观测到
的任何东西，所以不会削弱可探测性这一侧的结论。

剩下的条目再看是不是站点自己的重度脚本——指纹和反爬脚本（`fireyejs`、`et_f.js`、
`sufei_data`）本来就慢，它们超时不等于插桩造成的。

Reusable classifier:

```bat
type browser.stderr.log | python domtrace_stderr_classifier.py
python domtrace_stderr_classifier.py browser.stderr.log
```

Exit code is `1` only when a trace failure pattern is found.

Firefox 定制 trace 系统的全部环境变量开关。所有开关通过环境变量设置（启动 firefox.exe 前
`set` / `export`），进程启动时读取一次。本文档只讲开关：名称、可用值、默认值、功能、怎么用。

---

## 0. 快速上手

最小配置（补环境/页面行为采集，只开 DOM/BOM trace + HTTP packet trace）：

```
set MOZ_DOM_TRACE=1
set MOZ_DOM_TRACE_FILE=C:\out\trace.jsonl
set MOZ_DOM_HTTP_PACKET_TRACE=1
set MOZ_DOM_HTTP_PACKET_TRACE_DIR=C:\out\http_packet
firefox.exe
```

`MOZ_DOM_TRACE_FILE` 是**总锚点**：设一个路径，系统会按模块自动派生出一组文件
（见 §1）。HTTP packet 使用显式的 `C:\out\http_packet` 目录；JSCall 不属于补环境的默认配置，
只有纯算/算法调用链还原时才单独开启。
各模块也可单独指定锚点，但一般只设总锚点即可。

**按目标选择开关：**

- **补环境/页面行为采集**：只开 `MOZ_DOM_TRACE=1` 和 `MOZ_DOM_HTTP_PACKET_TRACE=1`，不需要默认开启 JSCall。
- **纯算/算法调用链还原**：才开 `MOZ_DOM_JSCALL_TRACE=1`，并配合脚本 URL 过滤和数量上限。
- **默认保持 JIT**：不要把 `JIT_OPTION_*` 调成禁用或钉死 Ion 的值。那会显著拖慢页面、可能导致卡死或超时，真实反爬站还可能据此触发时序判断。重写后的 jscall 不再改动任何 JIT 策略，也不再提供强制解释器的开关。

---

## 1. 总开关与输出文件

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_TRACE` | `1` 开 / `0` 关核心 / 不设 | 不设 | 核心主开关。`1` 开 DOM/BOM 等核心 trace；`0` 关闭核心**以及跟随核心落点的模块**（Eval、Event、Descriptor、WASM、Exception）。它**不是**总关闭开关：JSCall、HTTP packet、WebSocket、API override 都独立启用，不受它影响 |
| `MOZ_DOM_TRACE_FILE` | 文件路径 | 空 | 总锚点路径。使用锚点所在目录和扩展名，按模块子目录派生下列文件 |
| `MOZ_DOM_TRACE_ASYNC` | `1` 开 / `0` 同步回退 / 不设 | 开 | 核心 DOMTrace、Eval 和文本 sidecar 使用后台 writer；同步回退只用于对照或排障，不建议用于高流量页面 |
| `MOZ_DOM_TRACE_PTYPE` | 任意字符串，常用 `parent` / `content` | `parent` | 进程类型标签，写入 `trace_init.process_type` 及支持该字段的记录；文件用 `<pid>` 区分，不拼接 PTYPE |
| `MOZ_DOM_TRACE_RUN_ID` | 字符串，最多 127 字节 | `<pid>-<timestamp_ms>` | 把同一次运行的各进程记录关联起来的 ID。显式值中非字母、数字、`_`、`-` 的字符会替换为 `_`；只进记录字段，不参与文件名 |
| `MOZ_DOM_TRACE_LIMIT` | 整数，`0`=无限 | `0` | 核心 trace 事件总数上限（不含 jscall，jscall 有独立 limit） |
| `MOZ_DOM_TRACE_OPERATORS` | `0` 关 / 不设 | 开 | `0` 仅关闭 `typeof`、`instanceof` 和 RegExp operator 记录及其 JIT hook，保留 WebIDL、descriptor、event 等核心输出 |
| `MOZ_DOM_TRACE_INTERNAL` | `1` 开 / 不设或 `0` 关 | 关 | 默认屏蔽 BiDi/Marionette/RemoteAgent、`chrome://`、`resource://`、`about:`、`moz-src://`、内部 `data:` 和 parent 进程非网页 P0 噪声；排查浏览器内部行为时设 `1` 恢复记录 |
| `MOZ_DOM_TRACE_GATE` | 控制文件路径 | 空 | **运行时启停闸门**。设了它即进入"受控模式"：浏览器启动后 trace 默认**暂停**，控制文件**存在=开始落盘、删除=停止**，随时反复开关。未设则恒开（行为与改动前一致）。详见 §8 |
| `MOZ_DOM_TRACE_GATE_POLL_MS` | `20`..`5000` | `200` | 受控模式下后台线程轮询控制文件的间隔（毫秒）。仅在设了 `MOZ_DOM_TRACE_GATE` 时生效 |

**锚点派生的输出文件**（设 `MOZ_DOM_TRACE_FILE=C:\out\trace.jsonl` 后自动生成）：

| 相对 `C:\out` 的路径 | 内容 |
|---|---|
| `domtrace\trace_process_<pid>.jsonl` | 核心 DOM/BOM trace（主文件） |
| `cookie\trace_cookie_process_<pid>.jsonl` | cookie 读写 |
| `storage\trace_storage_process_<pid>.jsonl` | localStorage / sessionStorage |
| `eval\trace_eval_process_<pid>.jsonl` | eval / Function 动态代码（含源码 sha256）；开 `MOZ_DOM_JSCALL_SOURCE` 后执行过的脚本源码记录也在这里，`kind` 为 `script` |
| `eval\script_<sha256>_<字节数>.js` | 执行过的脚本原文（`MOZ_DOM_JSCALL_SOURCE`） |
| `env\env_inventory_<pid>_<session>.json` | 本构建对该 scope 暴露的全量接口/成员清单，每进程一份（`MOZ_DOM_ENV_INVENTORY`，见 §1.0.4） |

### 跨 realm 的对象同一性

消息记录里的 `*_object_id` 和 `*_global_id` 都满足同一条性质：**相等即同一对象，跨进程也成立**。
但达成这条性质的机制每类不同，而记录里看不出用的是哪种，所以列在这里：

| 类型 | 机制 |
|---|---|
| MessagePort | 端口 UUID 的哈希，两端本来就共享 |
| ServiceWorker | descriptor id，父进程统一分配后下发 |
| Window | `WindowID()`，浏览器全局分配 |
| Worker | 进程内计数器，`TraceAllocateInstanceId` 混入进程标识后再发放 |

Worker 那条是**修出来的而不是本来就有的**。裸计数器在每个进程都从 1 开始，实测中 id `1` 同时
活在四个进程里，指向四个不同的 worker；而同一份日志里另有一个端口 id 合法地出现在两个进程，
因为那真是同一个端口。两者外观都是"一个 id 出现在多个进程"，含义却相反。修复后实测 worker
id 跨进程碰撞为 0。

另外 `message_channel_created` 记录在 MessageChannel 构造时就写下 `port1_id ↔ port2_id`，
不必从消息里反推端口配对关系。

### 消息因果链：从一行代码到一个 listener

**记录类型名有个坑先说**：拓扑类记录**不带 `message_` 前缀**——`worker_created`、
`worker_terminated`、`port_transferred`（过去式）、`shared_worker_bound`，只有
`message_channel_created` 带。按前缀过滤会漏掉大半，实测中我据此误判过两次"记录不存在"。
写查询前先把 `type` 枚举一遍。

九个环节各自的记录和字段：

| 环节 | 记录 | 关键字段 |
|---|---|---|
| Worker 创建 | `worker_created` | `script_url` `worker_kind` `owner_window_id` `parent_worker_id` |
| Channel 创建 | `message_channel_created` | `port1_id` `port2_id` `global_id` |
| 端口转移 | `port_transferred` | `stage` `port_id` `port_peer_id` `object_sequence_id` |
| postMessage | `call` | `args` `stack` `origin_call_id` |
| 事件派发 | `message_receive` | `message_id` `receiver_execution_context_id` |
| `event.source` | `message_receive` | `source_global_id` `source_global_kind` |
| `event.ports` | 见下 | 经发送侧归属 |
| 回包 | `message_send` | 递归同上 |
| Worker 销毁 | `worker_terminated` | `reason` |

串起来的 join 路径：

```text
call.origin_call_id
  == message_send.origin_call_id     → message_id
  == message_receive.message_id      → receiver_execution_context_id
  == listener_call.execution_context_id
```

**`event.ports` 的归属走发送侧，不走接收侧。** 接收侧的 `port_transferred` 没有
`origin_call_id`，这是对的而不是缺陷：反序列化不由 JS 调用驱动。端口的身份和顺序在
`postMessage(data, [portA, portB])` 的参数列表里唯一确定，用发送侧的 `origin_call_id` 找到
这次调用转移的端口，再经 `message_id` 认领到具体投递即可。在接收侧再记一份只会引入两侧
不一致的风险。

实测：一次 `postMessage` 转移两个端口（同一 tick、同一时间戳），两个端口的身份、对端、以及
在参数列表里的顺序（靠 `seq`）全部还原；19 条消息从"哪一行 JS 发出"追到"哪个 listener 处理"，
含同一投递被两个 listener 处理的情形——这类场景时间戳无能为力。

`async_context_id` 目前恒为 0。接收侧已经从 token 里取发送侧的值，但发送侧本身是空的：异步
上下文只在 Promise、microtask、window/worker 定时器这五处建立，页面同步代码不在其中。要让它
有值需要给 message task 等加调度点，目前 `execution_context_id` 已足够做因果判断，故未做。
| `event\trace_event_process_<pid>.jsonl` | addEventListener / 事件派发（含回调源码定位） |
| `descriptor\trace_descriptor_process_<pid>.jsonl` | 属性描述符（defineProperty 等） |
| `wasm\trace_wasm_process_<pid>.jsonl` | WASM 边界调用 + 指令反汇编 |
| `jscall\trace_jscall_process_<pid>.jsonl` | JS 函数调用 + opcode trace（主力文件） |
| `exception\trace_exception_process_<pid>.jsonl` | JS pending exception trace |

`<pid>` 是进程号。锚点的文件名主体只用于提供目录和扩展名，不会成为最终输出文件名。

**独立定向路径**（一般不用，想把某模块单独导到别处时才设）：

| 开关 | 功能 |
|---|---|
| `MOZ_DOM_COOKIE_TRACE_FILE` | 单独指定 cookie trace 路径 |
| `MOZ_DOM_STORAGE_TRACE_FILE` | 单独指定 storage trace 路径 |
| `MOZ_DOM_WASM_TRACE_FILE` | 单独指定 wasm trace 路径 |
| `MOZ_DOM_JSCALL_TRACE_FILE` | 单独指定 jscall 锚点；非空时同时启用 jscall，并覆盖总锚点派生路径 |
| `MOZ_DOM_EXCEPTION_TRACE_FILE` | 单独指定 exception trace 锚点路径 |

**cookie trace 网页语义边界**：

cookie trace 在 **C++ 网络层 / Document 层**插桩，不向页面暴露接口，也不主动调用页面 JS：所有 trace 调用一律
传 `cx=nullptr`，不构造 `AutoJSAPI`、不进 JS realm、不抓 JS 栈、不调 `JS_ClearPendingException`，
与 WebSocket 帧 trace（§4.5）同范式。`document.cookie` 的读(`GetCookie`)/写(`SetCookie`)以及
HTTP Set-Cookie、cookie 发送决策、storage 落地全部如此。cookie 记录的 `stack` 字段恒为 `[]`，
不会新增页面可枚举属性、改写 prototype/descriptor 或额外触发 getter/Proxy。开启采集仍有 CPU、
内存和 I/O 开销，不能承诺对 `performance.now()` 等高精度计时侧信道绝对不可检测。

**HttpOnly cookie 全覆盖**：HttpOnly cookie 的特点是 `document.cookie` / 页面 JS 读不到，但在
C++ 层 `Cookie::Value()` 始终是明文（HttpOnly 只是个正交的布尔属性）。我们的 trace 在 parent
进程的多条路径都能拿到 HttpOnly cookie 的**明文 name=value**：

| 路径 | 记录 | 含 HttpOnly 明文 |
|---|---|---|
| HTTP 响应 Set-Cookie 接收 | `cookieSetAttempts` (source=http) | ✓ |
| 存储落地 add/update/touch | `cookieOps` (source=storage) | ✓ |
| HTTP 请求头携带 cookie 发出 | `cookieReads` (source=http, readType=requestHeader) | ✓（`aHttpBound=true` 不过滤） |
| 发送决策 | `cookieSendDecisions` (source=http) | ✓ |
| **非 HTTP 路径（document.cookie/内容进程加载）被丢弃时** | `cookieReads` (source=**httponly**, readType=**httpOnlyDroppedNonHttp**) | ✓（**专门补的缺口**） |

最后一条是关键补强：HttpOnly cookie 出于安全设计**从不下发给内容进程**（parent 在
`CookieService::GetCookiesForURI` 以 `aHttpBound=false` 过滤掉它，不走 IPC），所以内容进程的
document.cookie 视角永远看不到它。trace 在**过滤丢弃那一刻**（cookie 明文还在手）无痕记一条，
补上这个缺口。因此像抖音 `ttwid`、各类 `csrf_token` 这种 HttpOnly 设备/会话 token，无论走哪条
路径，trace 都能拿到明文——而 JS 层补环境（document.cookie）拿不到。

> 历史说明：早期版本 `document.cookie` 读写路径会构造 `AutoJSAPI` 取真实 `JSContext*` 喂给
> trace（用于可选抓栈），即便抓栈默认关，进 realm + `JS_ClearPendingException` 仍是可被时序
> 检测的 JS 状态扰动。现已全部移除（`Document.cpp` 6 处 + `CookieTraceLog.h` 抓栈块），cookie
> trace 结构上无法触碰 JS 状态。曾经的 `MOZ_DOM_COOKIE_TRACE_STACK` 开关已废弃删除。

### 1.1 JS 异常 trace（exception）

记录所有走到 `JSContext::setPendingException` 的 JS pending exception。目标是补环境定位具体异常外观，例如：

```
parameterTest.call({}, 37445)
```

会记录 WebIDL illegal invocation 产生的 `TypeError` message、file、line/column、已有 `SavedFrame`，并带上当前 `origin_call_id` 方便和 jscall 串起来。

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_EXCEPTION_TRACE` | `1` 开 / 不设 | 不设 | exception trace 主开关，可独立于 `MOZ_DOM_TRACE` |
| `MOZ_DOM_EXCEPTION_TRACE_FILE` | 文件路径 | 锚点派生 | 输出锚点。实际文件在同目录 `exception/trace_exception_process_<pid>.jsonl` |
| `MOZ_DOM_EXCEPTION_LIMIT` | 整数，`0`=无限 | `50000` | 异常事件上限。命中上限时写 `{"type":"limit_reached","scope":"exception"}` |
| `MOZ_DOM_EXCEPTION_FLUSH_INTERVAL` | `0`..`1000000` | `256` | 每多少条刷盘一次。进程可能被杀时调小（如 `8`） |
| `MOZ_DOM_EXCEPTION_STACK_FRAMES` | `1`..`64` | `8` | 每条异常最多记录多少帧 `SavedFrame` |
| `MOZ_DOM_EXCEPTION_MAX_STRING_CHARS` | `256`..`131072` | `4096` | message、file、字符串 preview 的字符上限 |

无痕边界：只读 native `ErrorObject` reserved slots、`JSErrorReport`、传入的 `SavedFrame`，以及可 native unwrap 的 `mozilla::dom::Exception` 字段（输出 `native_exception_*`）；不读 JS 层属性、不触发 getter、不调用 `ToString` / `Error.prototype.toString`、不构造 `Error.stack`。

---

## 2. JS 调用 trace（jscall）

把每次 JS 函数调用记录成 enter/leave 事件，可选抓参数+返回值真值。逆向加密算法的主力。

### 2.1 开关与文件

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_JSCALL_TRACE` | `1` 开 / 不设 | 不设 | jscall 主开关 |
| `MOZ_DOM_JSCALL_TRACE_FILE` | 文件路径 | 总锚点或内置路径派生 | jscall 专用锚点。非空时同时启用 jscall，并覆盖总锚点派生路径；实际文件在 `<锚点目录>\jscall\trace_jscall_process_<pid>.<扩展名>` |
| `MOZ_DOM_JSCALL_LIMIT` | 整数，`0`=无限 | `50000` | 每进程普通调用的 `call_id` 阈值。达到后普通调用停止、detail 调用继续，并触发一次 `limit_reached` |
| `MOZ_DOM_JSCALL_PER_FUNC_LIMIT` | 整数，`0`=不限 | `0` | 每个函数各记前 N 条，之后只计数不落记录。与全局 `LIMIT` 的区别是它不会一刀切停掉整条 trace：全局上限到 N 后所有函数都不再记，而这个只塌缩少数被调到数百万次的函数、其余保持完整记录与调用树。详见下节 |
| `MOZ_DOM_JSCALL_DURATION` | `0` 关 / 不设 | 开 | 记录每次调用的耗时 |
| `MOZ_DOM_JSCALL_FUNC_STATS` | `0` 关 / 不设 | 开 | **诊断用**。关闭每函数汇总计数器（`records`/`tiers`/`warmup`，见 `jscall_func_stats`）。它们是每条记录两次原子读改写，落在同一缓存行上，加这个开关是为了单独量它对热函数的成本。不建议常关：关掉后就无法区分"记录流被 JIT 截断"和"函数真的只跑了几次" |
| `MOZ_DOM_JSCALL_BASELINE` | `1` 开 / 不设 | 关 | 扩展到 Baseline 编译帧。**有已知配对缺陷**：生成器恢复、OSR、异常展开等路径的 enter/leave 配不上，会污染 `origin_call_id`。非必要不要开 |
| `MOZ_DOM_JSCALL_SOURCE` | `1`=全部 / URL 子串过滤 / 不设 | 不设 | 把**执行过的每个脚本**的源码原文落盘，见下 |

写入固定走后台批量二进制 writer，没有同步回退开关；也没有强制解释器的开关（重写后不再改动
JIT 策略，见本节开头的覆盖契约）。

#### `MOZ_DOM_JSCALL_PER_FUNC_LIMIT`：塌缩热函数的重复尾部

要在**不加脚本过滤、连 native 都记**的情况下让全量 jscall 不触碰卡顿红线，靠的是这个开关，
不是全局上限。

背景是实测出来的：某些反爬用 JS 写了一个字节码 VM，它的调度循环把每一条虚拟指令都交给少数
几个 builtin。于是这几个 native 被调到数百万次，而别的函数没有。一次不加过滤的全量抓取里，
单个内容进程 1349 万条记录，其中 93.5% 是这类 native——页面因此明显变卡，站点还给出访问频率
拒绝。逐项排查（关 duration、关 func-stats、关 operators）都不是主因：成本是**主线程上记录
的条数**本身，由这几个热 native 主导。

这个开关按 funcId 计数：每个函数各记前 N 条，超过之后这次调用被计数并丢弃——不铸 `call_id`，
所以 leave、参数捕获、关联发布全部走既有的"零 call_id = 未追踪"空操作路径。被塌缩函数的子
调用会归到最近的已记录祖先，是压缩边（和 JIT 省略帧一样），不是悬空边。

它和全局 `MOZ_DOM_JSCALL_LIMIT` 的本质区别：全局上限到 N 后**整条 trace 停记**，所有函数被
一起饿死；这个只塌缩少数被调够多次的函数，其余全部保持完整记录和完整调用树。塌缩的正好是
VM 内部派发 native，它们的操作数是 VM 自己的 typed array，detail 层本来就只记身份——所以
几乎不损失信息。改动只决定 tracer 写不写记录，无任何 JS 可见状态变化、无新钩子或代理，
不影响可探测性红线。

被塌缩的函数在 `jscall_func_stats` 里 `calls` > `records`，`calls` 是真实调用总数；未塌缩
或开关关闭时 `calls` 为 0，此时 `records` 即精确条数。主 `jscall_stats` 行带 `per_func_limit`
供核对。

实测（galaxyticketing，无过滤 + 无全局上限 + 逐函数 4096，正常关闭存档）：内容进程实际
2216 万次调用，只落盘 65.5 万条记录，91 个函数被塌缩，页面不卡、站点不拒。对照同配置去掉
这个开关时 1349 万条记录、卡且被拒。

| 函数 | records / calls |
| --- | --- |
| `load` | 4096 / 6,033,927 |
| `call` | 4096 / 5,452,327 |
| `add` | 4096 / 3,435,656 |
| `store` | 4096 / 2,294,459 |
| `includes` | 4096 / 1,338,209 |

`launch_galaxyticketing_headed.ps1` 的 `-PerFuncLimit`（默认 4096）设置它；`-UnlimitedJscall`
去掉全局上限，两者配合就是上面这组存档配置。

#### `MOZ_DOM_JSCALL_SOURCE`：执行过的脚本源码落盘

在此之前，"拿到执行过的 JS 原文"是靠两个各管一半的机制拼出来的：HTTP 抓包只看得见**过网络**的，
eval sidecar 只看得见 **eval 和 Function 构造器**的。缓存命中、文档内联、`blob:`、`data:` 加载的
脚本跑完之后，盘上没有任何副本。几轮实测之所以看着"都抓到了"，是因为每次都用全新 profile 所以
全部走了网络——那是运气，不是设计。

按**执行**而不是按编译记录：解析了却没进入过的脚本不属于要解释的这条 trace。挂载点在记录过滤器
**之前**，所以收窄 `MOZ_DOM_JSCALL_SCRIPT_URL` 不会连带收窄源码范围。

输出复用 eval 的目录和 writer，文件名前缀 `script_`，记录的 `kind` 是 `script`：

```text
eval\script_<sha256>_<字节数>.js
```

两条约束：

- **需要核心开关开着。** 复用 eval writer 的代价，和 `MOZ_DOM_WASM_DUMP` 一样，`MOZ_DOM_TRACE=0`
  时静默无输出。`launch_galaxyticketing_headed.ps1` 的 `-SourceDump` 会在模式不对时直接报错。
- **建议给过滤器而不是 `1`。** 实测不加过滤会连浏览器自身 879 个 `chrome://` 脚本一起存。

实测（galaxyticketing，过滤 `alicdn;galaxyticketing;damai;imaitix`）：24 个脚本 2.52 MB，
24/24 内容哈希与文件名自洽，其中 20 个与 HTTP 抓包的响应体**逐字节相同**。对不上的那几个正是
HTTP 拿不到的部分，也就是这个开关存在的理由。

源码被引擎丢弃（惰性解析或主动 discard）的脚本拿不到，这种情况计入统计而不是静默跳过。

**开销：24 个脚本 2.52 MB 加约 20 ms，即每 MB 约 8 ms。**

受控实测（`make_source_perf_fixture.py` + `measure_source_dump.ps1`，交替 off/on 各 6 轮）：

| | n | 中位数 | 区间 |
|---|---|---|---|
| 关 | 6 | 44.0 ms | 43.0 – 46.0 |
| 开 | 6 | 64.0 ms | 61.0 – 65.0 |

两组区间不重叠，信号清晰。这 20 ms 是**一次性**的：分摊在 24 个脚本的首次执行上，单个脚本增量
在毫秒级，页面跑起来之后不再有开销。但它落在 JS 执行线程而非后台 writer 上，所以确实推迟首次
绘制；脚本量更大的站点按每 MB 8 ms 线性外推。

远低于触发脚本超时的量级，也不改变任何页面可观测状态。理论暴露面是"每个脚本首次调用略慢"，
页面若给自己计时原理上可察觉。这个数字也是默认建议给 URL 过滤而不是 `1` 的依据。

一个测量方法上的教训：**不要在反爬站点上做 A/B**。同配置连跑两轮，拦截页记录数是 13,627 对
960——站点每轮给的对待相差 14 倍，而要测的信号是几十毫秒，`timeout` 计数在这种条件下纯属噪声。
真实站点适合回答"抓不抓得到""会不会被拒"，量化开销必须用受控页面。

开关优先级：jscall 主开关或非空专用锚点 > gate 当前状态。**`MOZ_DOM_TRACE=0` 不再关闭
jscall**——它只关核心 sink。jscall 是独立配置的一路，和 HTTP 报文、WebSocket trace 一样。

这条以前是反过来的，而且后果不只是"少一个用法"：写 `MOZ_DOM_TRACE=0` 想只留 jscall，得到的是
一条记录都没有，且没有任何输出解释原因。想要"只抓调用"，唯一可行的写法是把 `MOZ_DOM_TRACE`
**不设**，因为"设成表示关闭的那个值"会把整个采集关掉。

解耦要同时改四处，少一处就仍然不通，而且症状各不相同，值得记下来：

| 位置 | 不改的后果 |
|---|---|
| `JSCallTrace.h` 配置 | 直接否决启用 |
| `DOMTraceLog.h` 路径构建 | 提前 return，或落到内置 fallback 锚点 |
| `sandboxBroker.cpp` 沙箱放行 | 子进程正常起来并开始采集，然后**打不开自己的输出文件** |
| `JSCallBinWriter.cpp` 关闭注册 | 进程退出时尾部未写完，且没有 `jscall_stats` 行说明这件事 |

后两处尤其容易漏：前两处改完看起来"已经生效"，失败却出现在完全不同的层面。沙箱那处只有
多进程环境才暴露，关闭注册那处只有真实站点（本地探针的进程太少、退出太快）才暴露。

`MOZ_DOM_TRACE_ASYNC` 只选择核心 sink 的写入后端，不会自动开启采集；未设置和设为 `1` 都是异步，
精确设为 `0` 才回退同步。jscall 固定使用二进制异步 writer，没有回退开关。
只设置 `MOZ_DOM_JSCALL_TRACE=1` 且没有总锚点或专用锚点时，使用源码内置锚点
`C:\firefox\trace\优化\trace_process_parent.jsonl`，最终仍按 `jscall\trace_jscall_process_<pid>.jsonl`
派生。实际使用建议显式设置一个锚点，避免依赖该本机 fallback。

#### 异步 writer 契约

异步模式不改变采集范围、业务事件 schema 或输出路径，但它以页面流畅度优先，是
**best-effort、有损**写入：

- 总计费预算 16 MiB、65536 条，排队和正在写入的记录都计入；每条另计 128 字节开销。
- 普通记录最多约 2 MiB。Producer 通过无锁 MPSC 队列提交，并使用双 slot epoch fence 保证控制
  marker 的顺序；原子竞争会继续重试，不等待控制线程，也不会因固定重试次数丢弃记录。容量不足、
  OOM、记录过大、gate 关闭、shutdown 或 writer 故障仍会拒绝当前普通记录。
- 总预算内为控制记录预留 64 KiB 和 16 条。控制记录可能因预留耗尽或 I/O 故障被拒绝/写失败；
  这不是额外的 64 KiB 内存。确认超时是单独状态：记录已入队，可继续写入。
- Writer 累积 1 MiB 或最多等待 200 ms 后 `fflush`，刷新间隔不可配置。
- 控制记录带 data fence：排在它之前已接收的普通记录先写，然后写控制记录并强制 `fflush`。
  控制记录完成入队后，调用方默认最多再等 2 秒；超时只表示尚未确认落盘，不表示控制记录一定丢失。
- 正常关闭给 writer 设置 2 秒 drain deadline，并强等待 writer 结束；2 秒不是 shutdown 调用的返回
  上限。QuickExit 先设置 500 ms 总
  deadline，前序 trace 清理完成后按剩余时间依次排空 Eval、DOMTrace 和 JSCall writer；到期可 detach。此时未写记录
  及关闭末尾的 I/O 错误未必能进入最终统计，外部强杀同样不保证排空。

异步 `trace_init` 增加 `writer_backend:"async"`、`async_queue_bytes`、`async_batch_bytes` 和
`async_flush_ms`，文件使用 LF。同步回退模式保留原 `trace_init` schema 和 Windows CRLF 行为。

#### `jscall_writer_stats` / `domtrace_writer_stats` / `eval_writer_stats`

异步 writer 在 gate 打开时约每秒输出累计统计，并在正常关闭时输出最终统计。JSCall 使用
`jscall_writer_stats`，核心 DOMTrace 使用 `domtrace_writer_stats`；两者都包含
`dropped_control`，用于标记 session marker 或 flush fence 未能确认落盘：

Eval JSONL 和源码文件同样默认由后台串行 writer 写入，并以 `eval_writer_stats` 收尾。
`MOZ_DOM_TRACE_ASYNC=0` 会同时让核心 DOMTrace、Eval 和文本 sidecar 回退同步。Eval 的
`dropped_dispatch` 表示后台任务队列拒绝记录；其它公共字段语义与下表一致。

| 字段 | 含义 |
|---|---|
| `attempted` / `enqueued` | 尝试进入 writer / 成功接收的记录数 |
| `dropped_contention` | 兼容字段；当前无锁 MPSC producer 不因原子竞争丢弃，健康的新实现应恒为 `0` |
| `dropped_full` / `dropped_oom` / `dropped_oversize` | 容量或条数预算不足 / 分配失败 / 单条记录过大 |
| `dropped_gate` / `dropped_shutdown` | gate 或关闭阶段拒绝、超时后清理的记录 |
| `dropped_control` | session marker 或 flush fence 未能确认落盘的控制记录 |
| `dropped_writer_failed` / `io_errors` | writer 已故障后拒绝 / 实际文件 I/O 错误 |
| `queue_high_water_bytes` | 排队加 in-flight 的最高计费字节数，包含每条 128 字节开销 |

`dropped_control` 是控制记录失败的独立分类，可能同时带有具体原因；session 完整性会单独比较该字段，避免重复计数或漏报。

验收时应逐文件检查最终统计。正常关闭的健康异步文件应以对应的 `*_writer_stats` 为末行；缺失末行或
任一 `dropped_* > 0` 都表示日志不完整。`io_errors` 是写出该统计行之前已观察错误的快照，
`io_errors=0` 不覆盖随后 `fflush`/`fclose` 的错误，也不能单独证明文件完整。周期统计由 writer
直接写入，可能与已排队业务记录出现局部 `seq`
倒序；还原业务调用链应以 `call_id` / `parent_call_id` 和 `ts` 为主，不应把文件行序当成严格全局
`seq` 顺序。

Windows content process 启动时会根据显式 `MOZ_DOM_*_TRACE_FILE` 锚点，为其父目录添加最小
sandbox 写规则。路径必须是位于非根目录下的绝对路径，且不能包含 `*` 或 `?`；无需关闭整个
content sandbox。总锚点规则覆盖由它派生的 DOMTrace、Eval、Event 等模块子目录。

### 2.2 抓哪些调用

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_JSCALL_SCRIPT_URL` | 逗号/分号分隔 URL 子串 | 空 | **记录范围过滤**：只记录 URL 命中任一子串的 jscall。大小写敏感，不是 regex/glob |
| `MOZ_DOM_JSCALL_NATIVE` | `1` / 不设 | 关 | 设了脚本过滤器时仍记录 native/builtin。**不设过滤器时 native 本来就记录**，这个开关只在有过滤器的前提下才有意义 |

没有排除式过滤器，也没有「只记目标函数子树」的开关；重写后只有上面这一个包含式 URL 过滤。

> `SCRIPT_URL` 只决定 jscall **记不记这一条**，不会自动抓参数/返回值真值。
> `DETAIL_SCRIPT_URL` 才是 detail 过滤器。

**示例**：

```bat
:: 只记录某个脚本相关的 jscall
set MOZ_DOM_JSCALL_SCRIPT_URL=challenge.js

:: 多个 token 可混用逗号/分号；任一 token 命中即记录
set MOZ_DOM_JSCALL_SCRIPT_URL=challenge.js;static/crypto,cdn-cgi/challenge-platform

:: caller_url 也参与匹配：只看某个页面发起/承接的调用
set MOZ_DOM_JSCALL_SCRIPT_URL=checkout.html

:: 缩小记录范围 + 对同一脚本抓 args/result 真值
set MOZ_DOM_JSCALL_SCRIPT_URL=target.js
set MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL=target.js
```

### 2.3 detail（抓参数+返回值真值）

只有一个过滤维度：脚本 URL。按函数名、按源码 sha256 的定向方式在重写中未保留。

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL` | 逗号/分号分隔 URL 子串 | 空 | 命中的脚本，其函数的 args、`this`、返回值都抓真值 |
| `MOZ_DOM_JSCALL_MAX_VALUE_BYTES` | `256`..`16777216` | `65536` | 单个值的字节上限，超出截断并标 `truncated` |

两条实测边界，决定了这个功能能回答什么：

- **native 和 WASM 导出取不到值。** `JSCallShouldCaptureValues` 要求 `isInterpreted()`。真实
  反爬站实测 91.7% 的调用是 native（VM 指令处理在 WASM 里），这些只有调用记录没有操作数。
- **对象只有类名。** 实测 83 万个对象值内容一律是类名，其中 97.5% 是 `Proxy`。标量、字符串、
  数组、TypedArray 有真值。

### 2.4 opcode 级 trace

把粒度从「函数」下沉到「每条 JS 字节码 opcode」。写进 jscall 的 opcode 流（`.opcode.bin`），
定长二进制记录，每条含 `call_id`、`func_id`、脚本内偏移和 opcode 字节。

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_JSCALL_OPCODE_URL` | 逗号/分号分隔脚本 URL 子串 | 空 | opcode trace 总开关。**务必收窄到单脚本，量极大** |
| `MOZ_DOM_JSCALL_OPCODE_LIMIT` | 整数，`0`=无限 | `0` | opcode 记录条数上限，与 jscall 的 limit 独立 |

**重写后尚未恢复的三项**：立即数解码（`operands`）、PC 窗口筛选、栈值捕获。旧实现有这些，
新的二进制记录格式里没有。看穿自建字节码 VM 需要的操作数信息因此暂时拿不到。

> opcode 量极大，每条字节码一行。对时延敏感的反爬 VM 开它可能拖慢到触发检测，
> 务必配 `OPCODE_URL` 收窄单脚本 + 非零 `OPCODE_LIMIT`。

## 3. WASM trace

### 3.1 边界调用 + 模块反汇编（dump）

模块编译成功时把整个模块反汇编成 WAT 文本（opcode 名 + 立即数）。**编译期一次性静态
反汇编**，不插桩、不增加运行时开销，对反爬时延检测安全，可全量开。

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_WASM_DUMP` | `1` 开 / 不设 | 关 | WASM 原始模块 dump 开关。默认关，设 `1` 显式开启 |
| `MOZ_DOM_WASM_DUMP_MAX` | 字节数 | `8388608`(8MB) | 单个模块 dump 的字节上限 |
| `MOZ_DOM_WASM_DUMP_TOTAL` | 字节数 | `134217728`(128MB) | 所有模块 dump 累计字节上限 |

### 3.2 指令级 trace（逐函数反汇编）

按函数定向反汇编（比整模块 dump 更细，可单挑某个函数）。

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_WASM_INSN` | `1` / 不设 | 关 | WASM 指令级 trace 开关 |
| `MOZ_DOM_WASM_INSN_FUNC_INDEX` | 函数索引，`-1`=全部 | `-1` | 只反汇编指定索引的函数 |
| `MOZ_DOM_WASM_INSN_MAX_FUNCS` | 整数，`0`=无限 | `0` | 最多反汇编几个函数 |
| `MOZ_DOM_WASM_INSN_MAX_BYTES` | 字节数 | `1048576`(1MB) | 单函数反汇编字节上限 |

> SpiderMonkey 的 wasm 无解释器（全 JIT 编译成机器码），没有「逐执行 wasm 指令」trace；
> 看运行时控制流/迭代次数需结合 JS 侧 opcode trace（若 wasm 由 JS 调度）或边界调用的
> args/return。静态反汇编是覆盖 wasm 算法的可行路线。

### 3.3 边界调用 trace（参数、返回值、线性内存窗口）

静态反汇编说的是模块「能做什么」，这一层说的是它「实际被喂了什么」。每次跨 JS/wasm
边界记一条 `wasm_call`，含按 wasm 签名精确定型的参数与返回值。

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_WASM_CALL` | `1` / 不设 | 关 | 边界调用 trace 开关 |
| `MOZ_DOM_WASM_MEM_BYTES` | 字节数，`0`=不抓内存 | `256` | 每个「指向内存」的 i32 参数抓多少字节，上限 65536 |
| `MOZ_DOM_WASM_CALL_MAX` | 整数，`0`=无限 | `0` | 进程内边界记录条数上限 |

关于代价：跨界次数远少于 JS 调用。实测一次完整验证码流程跨界 **204 次**，同期 JS 调用
**1,269 万次**——这一层读的是几百条记录，不是几百万条，因此可以放开抓。

Emscripten 编译的导出把缓冲区当作线性内存偏移（i32）传，所以只有标量等于没有数据。
落在 `[1, memLength)` 内的 i32 参数都会附带一段内存窗口，**不猜哪个参数才是指针**——
猜错漏掉一个真指针比多抓几段无关字节糟得多；地址 0 是空指针，跳过。enter 与 leave 各
采一次，因为导出的真实输出常写进调用方给的缓冲区而不是靠返回值。

`wasm_export_map` 记录（随模块元数据自动产生，无需开关）把函数索引映射到导出名。这是
必需的：导出函数在引擎里的名字**就是函数索引的数字串**（`Instance::getExportedFunction`
里的 `NumberToAtom(cx, funcIndex)`），所以 jscall 里一条 wasm 记录的 `name` 是 `"36"`
而不是导出名。

> **覆盖边界，必须如实知道**：本层挂在 `Instance::callExport` / `Instance::callImport`
> 这两条既有 C++ 慢路径上，不改任何 stub 生成、不动 JIT 策略。代价是 JIT 快速路径
> （`GenerateJitEntry`、`GenerateImportJitExit`、`GenerateDirectCallFromJit`）会绕过它们，
> 那些跨界不会被记录。实测目标模块的 204 次跨界全部落盘，但这不是普适保证。

---

## 4. HTTP 报文 trace

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_HTTP_PACKET_TRACE` | `0` 关 / 其它=开 | 开 | HTTP 报文 trace 开关（非 `0` 即开） |
| `MOZ_DOM_HTTP_PACKET_TRACE_DIR` | 目录路径 | 锚点目录 | HTTP 报文输出目录 |
| `MOZ_DOM_HTTP_PACKET_TRACE_MAX_PENDING` | `1`..`8192`；默认 `256` | 待写 packet 上限；满载时增加 `dropped_full` 并丢弃该 packet |

---

## 4.5 WebSocket 帧 trace（C++ 网络层，无页面接口改写）

抓 WebSocket 每一帧的收发（text/binary/close/ping/pong）。在 C++ 的 `WebSocketChannel`
层插桩，不 patch `WebSocket.send`/`onmessage`、不改原型、不增加页面 JS 栈帧或改变网络返回
语义。开启采集仍有 CPU、内存和 I/O 开销，不能承诺对高精度计时侧信道绝对不可检测。抓到的 payload 是**真实交换的明文**——入站为 permessage-deflate 解压
后、出站为掩码前的原始数据，即使站点对 send/onmessage 做混淆也不影响。

输出 `websocket_frames.jsonl`，与 `http_packet` 目录同级（共用 `MOZ_DOM_TRACE_FILE` 锚点）。

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_WS_TRACE` | `1` 开 / 不设 | 关 | **WebSocket 帧 trace 总开关**（独立开关，默认关，不设不产出） |
| `MOZ_DOM_WS_ASYNC` | `1` 开 / `0` 同步回退 / 不设 | 开 | 帧与握手走有界后台 writer。正常退出后检查 `websocket_writer_stats`，`dropped_*` 非零表示有缺口；同步回退只用于对照或排障 |
| `MOZ_DOM_WS_MAX_BYTES` | `256`..`16777216` | `65536` | 单帧记录的 payload 字节上限。超出截断，记录带 `truncated:true` + 原始 `payload_len` |

每帧一条 JSONL，字段对齐其它 trace 的 envelope：

| 字段 | 含义 |
|---|---|
| `seq` / `ts` / `pid` / `type` | 公共头；`type` 固定 `"ws_frame"` |
| `dir` | `"send"`（页面发出）/ `"recv"`（收到） |
| `serial` | 连接序号（区分同页多个 WebSocket） |
| `url` | 连接的 host:port |
| `opcode` | RFC6455 操作码：`1`=text `2`=binary `8`=close `9`=ping `10`=pong |
| `fin` / `rsv1` / `rsv2` / `rsv3` / `mask_bit` / `mask` | 帧头标志位 |
| `payload_len` / `captured_len` / `truncated` | 原始长度 / 实抓长度 / 是否截断 |
| `payload_b64` | payload 的 base64（二进制帧可精确还原） |
| `text` | 仅 text 帧：payload 的 UTF-8 预览（已转义） |

> - 默认关：只设 `MOZ_DOM_WS_TRACE=1` 即开，不依赖其它开关（但需 `MOZ_DOM_TRACE_FILE` 定位输出目录）。
> - 抓的是 WS 数据帧，**握手头**（HTTP 101 升级）由 HTTP 报文 trace 那条线覆盖，二者互补。

---

## 5. 常用配方

| 目标 | 最小组合 |
|---|---|
| 只缩小 jscall 记录范围，不抓真值 | `JSCALL_TRACE` + `JSCALL_SCRIPT_URL=xxx` + `JSCALL_LIMIT=0` |
| 不知函数名 / 名字被混淆 | `JSCALL_SCRIPT_URL=xxx` + `DETAIL_SCRIPT_URL=xxx` + `SHALLOW` + `DEEP_LONG_STR=512` |
| 排除无用 JS | `JSCALL_SCRIPT_URL_EXCLUDE=analytics;telemetry` |

### A. 按脚本来源抓函数真值（不需要预先知道函数名）

```
set MOZ_DOM_JSCALL_TRACE=1
set MOZ_DOM_JSCALL_TRACE_FILE=C:\out\jscall.jsonl
set MOZ_DOM_JSCALL_LIMIT=0
set MOZ_DOM_JSCALL_SCRIPT_URL=challenges.cloudflare.com
set MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL=challenges.cloudflare.com
set MOZ_DOM_JSCALL_MAX_VALUE_BYTES=131072
```

实测在真实反爬站上，这套配置抓到了 AWSC `getFYToken` 的完整 2,360 字符 token。注意
detail 只对**解释执行的 JS 函数**产出值，native 和 WASM 导出没有（见 §2.3）。

### B. 只抓调用、完全不开核心 sink

```
set MOZ_DOM_TRACE=0
set MOZ_DOM_JSCALL_TRACE=1
set MOZ_DOM_JSCALL_TRACE_FILE=C:\out\jscall.jsonl
set MOZ_DOM_JSCALL_LIMIT=0
```

jscall 独立于 `MOZ_DOM_TRACE`，所以这是"只要函数调用、不要 DOM/BOM 记录"的正确写法。
负载明显低于全开。

### C. 保存执行过的全部脚本原文

```
set MOZ_DOM_TRACE=1
set MOZ_DOM_TRACE_FILE=C:\out\run.jsonl
set MOZ_DOM_JSCALL_TRACE=1
set MOZ_DOM_JSCALL_TRACE_FILE=C:\out\jscall.jsonl
set MOZ_DOM_JSCALL_SOURCE=alicdn;target.com
```

给 URL 过滤而不是 `1`，否则连浏览器自身几百个 `chrome://` 脚本一起存。需要核心开关开着
（复用 Eval 写入器）。

### D. 拿到 WASM 模块的完整字节

反爬的核心算法常在 WASM 里。`MOZ_DOM_WASM_DUMP` 对 `instantiateStreaming` 加载的模块只
记元数据（字节从网络直接进编译器，dump 点拿不到源字节），所以走 HTTP 抓包更可靠：

```
set MOZ_DOM_TRACE=1
set MOZ_DOM_TRACE_FILE=C:\out\run.jsonl
set MOZ_DOM_HTTP_PACKET_TRACE=1
set MOZ_DOM_HTTP_PACKET_TRACE_DIR=C:\out\http_packet
set MOZ_DOM_WASM_DUMP=1
```

`.wasm` 响应体在 `responseBody.base64` 里，`sha256` 字段可直接与 `sha256sum` 比对。
实测取到过 3.08 MB 的完整模块（7,749 个函数、51 个导出、Emscripten 产物）。

### F. 无痕抓 WebSocket 收发帧

```
set MOZ_DOM_TRACE=1
set MOZ_DOM_TRACE_FILE=C:\out\trace.jsonl
set MOZ_DOM_WS_TRACE=1
set MOZ_DOM_WS_MAX_BYTES=131072
```
产出 `websocket_frames.jsonl`（与 `http_packet` 目录同级），每帧一条，含 `dir`/`opcode`/
`payload_b64`/`text`。C++ 网络层抓取，不 patch 页面 WebSocket API；payload 为入站解压后 /
出站掩码前的真实明文。开启后仍可能产生计时和资源占用侧信道。需要看 WS 收发什么、或站点把
数据藏在 WS 帧里时用。

### G. 验证 WindowProxy 参数来源

用于验证 `Object.keys(window)` 和 `Object.keys(iframe.contentWindow)` 的 jscall detail 能直接区分顶层
`window` 与 iframe 的 `contentWindow`。

终端 1（在 `dom/bindings/domtrace` 目录启动 HTTP 服务）：

```
python -m http.server 8731
```

终端 2（新开 `cmd`，设置 trace 环境变量后启动 Firefox）：

```bat
set MOZ_DOM_TRACE=1
set MOZ_DOM_TRACE_FILE=C:\out\trace.jsonl
set MOZ_DOM_JSCALL_TRACE=1
set MOZ_DOM_JSCALL_TRACE_FILE=C:\out\jscall.jsonl
set MOZ_DOM_JSCALL_LIMIT=0
set MOZ_DOM_JSCALL_NATIVE=1
set MOZ_DOM_JSCALL_SCRIPT_URL=windowproxy_keys.html
set MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL=windowproxy_keys.html

firefox.exe "http://127.0.0.1:8731/verify/windowproxy_keys.html?auto=1&interval=200&limit=0"
```

页面会持续交替调用 `runTopWindowKeys()` 和 `runIframeWindowKeys()`，每次调用前写
`ruyiWindowProxyMarker("top-window", i)` 或 `ruyiWindowProxyMarker("iframe-contentWindow", i)`。
离线看日志时，先用 marker 和 caller 行号对齐，再看紧邻的 native callee：`callee_name:"keys"` 对应
`Object.keys(...)`，`callee_name:"ownKeys"` 对应 `Reflect.ownKeys(...)`。

`schema_version:2` 的 `jscall_detail.args[*]` / `result` 在 WindowProxy 值旁新增 `object`：

```json
{
  "type": "object",
  "value": {"type": "object", "class": "Proxy"},
  "object": {
    "kind": "window_proxy",
    "window_proxy_id": "7f...",
    "outer_window_id": 123,
    "inner_window_id": 456,
    "browsing_context_id": 789,
    "parent_browsing_context_id": 0,
    "is_top": true,
    "relation_to_caller": "current_global"
  }
}
```

判定时优先看 `object.browsing_context_id` / `outer_window_id` / `inner_window_id`。`relation_to_caller` 为
`current_global` 表示传入当前执行上下文的 window，`other_window` 表示 iframe/popup 等其它 WindowProxy；
跨进程 iframe 可能显示 `kind:"remote_window_proxy"`，此时 window id 可能为 0，但 `browsing_context_id`
仍可用于区分。

---

## 6. 优先级与注意事项

- `MOZ_DOM_TRACE=0` 关闭核心 sink 及跟随它的模块（wasm、exception 等），但**不再关闭
  jscall、HTTP 报文和 WebSocket**——这三路是独立配置的。
- jscall 完全独立于核心 trace：`MOZ_DOM_JSCALL_TRACE=1` 或非空的
  `MOZ_DOM_JSCALL_TRACE_FILE` 都会启用，既不需要 `MOZ_DOM_TRACE=1`，也不会被
  `MOZ_DOM_TRACE=0` 关掉。"只抓调用"就写 `MOZ_DOM_TRACE=0` + `MOZ_DOM_JSCALL_TRACE=1`。
- `MOZ_DOM_TRACE_ASYNC` 只选择核心 sink 的写入后端，不启用采集；未设置或 `1` 为异步，精确 `0` 为同步回退。jscall 固定异步，无此开关。
- 所有开关进程启动时读取一次，运行中改环境变量无效。
- 数值型开关超出 clamp 范围时取边界值或忽略（保持默认）。
- 多进程下每个进程各写一份带 `<pid>` 的文件，按 pid 区分。
- **opcode trace 爆量风险**：重型脚本开 `OPCODE_STACK`（尤其 `OPCODE_STACK_FULL`）且不设 `OPCODE_LIMIT`
  会几秒写出 GB 级日志撑爆磁盘。务必 `OPCODE_LIMIT` 限量 + `OPCODE_URL` 收窄单脚本 + `OPCODE_PC_START/END`
  锁 PC 窗口，并在驱动侧监控日志大小。逆向重型商业 JSVM 见配方 E 的两段式流程。

---

## 7. 跨模块事件关联（origin_call_id）

DOM 的 `call`/`get`/`set` 事件，以及 cookie / storage / event / descriptor 各模块的记录，
都带一个 `origin_call_id` 字段：值等于**触发该操作的那次 JS 函数调用的 `call_id`**（即
jscall trace 里 enter/leave 共享的那个 id）。用它把「某个 challenge 函数调用 → 它读了哪些
指纹属性 / 写了哪个 cookie / 存了哪个 localStorage 值」串成一条数据流，逆向时定位「是谁干的」。

- `origin_call_id == 0` 表示当时没有处于被 trace 的 JS 调用栈内——常见于顶层 script 语句
  （不在任何函数里），或操作发生在**没有 JS 执行的进程/线程**（如 cookie/storage 的后端
  `cookieOps`/`storageOps` 跑在 parent/storage 进程，那里 `origin_call_id` 恒为 0）。真正能
  关联的是**内容进程里的 DOM 层** `call`/`get`/`set`（例如 `localStorage.setItem` 作为一条
  DOM `call` 事件，带上调用它的函数 call_id）。
- 关联方法：拿 DOM/副作用记录的 `origin_call_id`，去 jscall 文件里找 `call_id` 相等的那条
  `jscall_frame`(enter) / `jscall`(leave)，即得到函数名、脚本 URL、行列、父调用链。
- 启用 `MOZ_DOM_JSCALL_SCRIPT_URL` 后，被过滤掉的调用不会分配 `call_id`，也不会进入 jscall
  调用栈；其子调用若命中过滤，会挂到最近的未过滤祖先或成为根调用。
  因此 jscall 的 parent/depth/summary 以及跨模块 `origin_call_id` 视角都会被压缩。需要完整调用树时，
  不要用记录范围过滤，只用 detail/opcode 层过滤降噪。

---

## 8. 运行时启停闸门（TRACE GATE）

让 trace 在**浏览器启动后再开始、随时结束**，而不是启动即接收业务记录。逆向真实站点时，
页面加载阶段往往有大量初始化噪声，这个闸门让你**等到关心的时刻（如手动点验证码那一刻）
才开始落盘**，得到干净窗口。

### 8.1 工作原理

- **配置与启停解耦**：「录什么」（URL 过滤 / 槽号 / limit / detail 等）仍走原来的 env、
  启动时读一次定死；闸门只控制「此刻落不落盘」这一个布尔状态。所有现有开关语义不变。
- **不触发 JIT 重编译**：闸门位于各模块 emit/落盘入口，不改变 JIT hook 的编译配置。关闸时
  hook 仍存在，但业务记录会在 gate 检查处返回；开闸不需要 `ReleaseAllJITCode`。
- **关闭态不保留采集设施**：独立后台线程每 `MOZ_DOM_TRACE_GATE_POLL_MS`（默认 200 ms）检查一次
  控制文件；关闸后 HTTP observer、动态 process actor 和栈 observer 会被撤销。原生 trace 热路径只
  读取 atomic 状态，网络异步提交还会复核 session。闸门不会消除已配置 hook、开窗期间采集和
  序列化本身的开销。

### 8.2 开关

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_TRACE_GATE` | 控制文件路径 | 空 | 设了即进入**受控模式**：启动默认暂停，控制文件**存在=开、删除=关**。未设则闸门恒开（向后兼容） |
| `MOZ_DOM_TRACE_GATE_POLL_MS` | `20`..`5000` | `200` | 后台线程轮询控制文件的间隔（毫秒） |

> 控制约定：受控模式下，启动时若控制文件**已存在**则立即开始（允许「启动前先建好文件=即开」）；
> 否则启动暂停，等文件出现才开始。`MOZ_DOM_TRACE=0` 会关闭核心和 Exception，闸门不会绕过它；
> JSCall、HTTP packet、WebSocket 和 API override 保留各自的独立配置语义，不受它影响（JSCall 仍
> 受闸门本身管辖，只是不受这个总开关管辖）。

### 8.3 session 边界标记

每次控制文件状态变化时提交一条控制记录，写入已启用且可打开的核心或 jscall 落点
（jsvmp/opcode 的边界在 jscall 文件里）：

| 记录 | 时机 | 字段 |
|---|---|---|
| `{"type":"session_started","session":N,...}` | 开闸尝试 | `session`=本次会话序号（从 1 递增） |
| `{"type":"session_stopped","session":N,...}` | 关闸边沿 | 同上，与 started 配对 |

同步落点直接写入并 `fflush`。异步 jscall 控制记录带 data fence：writer 会先写完在该控制记录
之前已接收的业务记录，再写 marker 并强制 `fflush`；marker 完成入队后，调用方最多再等待 2 秒
确认。等待超时表示尚未
确认持久化，记录可能仍在队列中。续写同一文件时以 `session` 字段区分各次会话，不切文件。

开闸时先写核心 `session_started`，再提交 jscall marker；只有 jscall marker 已确认持久化
才发布 gate 开启状态。等待超时同样保持 gate 关闭。因此核心文件中的孤立 `session_started`
可能只表示一次开闸尝试，不能单独
证明业务窗口已经开启；应与 jscall marker、后续业务记录和 writer stats 交叉确认。

`limit_reached` 表示对应进程和 scope 命中数量阈值；配对的 `session_stopped` 表示该受控 session
观察到关闸边沿。marker 缺失只说明边界或收尾不完整，可能来自外部强杀、writer/I/O 故障、控制
记录拒绝或关闭预算耗尽，不能单独据此判定退出原因。应结合最终 `jscall_writer_stats` 和进程退出
信息判断。

> 注意：`MOZ_DOM_JSCALL_LIMIT` / `OPCODE_LIMIT` 等 limit 哨兵是**进程级、跨 session 累计、
> 只写一次**——反复开关闸门不会重置 limit 计数（它是防爆盘的硬保险，不该被绕过）。

### 8.4 Gate 配方 —— 等点验证码那刻才开始抓

```
set MOZ_DOM_JSCALL_TRACE=1
set MOZ_DOM_JSCALL_TRACE_FILE=C:\out\jscall.jsonl
set MOZ_DOM_JSCALL_LIMIT=0
set MOZ_DOM_JSCALL_SCRIPT_URL=challenges.cloudflare.com
set MOZ_DOM_JSCALL_DETAIL_SCRIPT_URL=challenges.cloudflare.com
set MOZ_DOM_TRACE_GATE=C:\out\trace.on
firefox.exe
```

操作流：
1. 启动浏览器，页面加载和初始化期间保持关闸。业务采集记录不会被接收；jscall 文件仍可能包含
   `trace_init`，正常关闭时还会有最终 `jscall_writer_stats`。
2. 走到验证码前、准备点击的**前一刻**：`echo. > C:\out\trace.on`（PowerShell：`ni C:\out\trace.on`）。
   后台线程在下一次轮询时提交 `session_started`，控制记录被接收后开闸。
3. challenge 跑完：`del C:\out\trace.on`。后台线程在下一次轮询时停止接收新业务记录，并提交
   `session_stopped`；异步 writer 按 data fence 写完此前已接收的记录并刷新。
4. 业务记录限定在开闸窗口；`autodetect` 也只在窗口内统计 GetElem，少受加载期无关短循环干扰。

反复 `开→关` 即得 session 1/2/3……，挑 `session_started`/`session_stopped` 配对完整、中间无
`limit_reached` 的那一段做对拍。

### 8.5 风险提示

- 闸门**不替代**配置层的收窄纪律。别因为「能随时停」就想「先全开着等手动停」——opcode
  不收窄到单脚本就开闸，你反应过来去关的那一两秒已写出几个 GB。
  `OPCODE_LIMIT` 仍必须设，作为闸门失灵/人停手慢时的硬保险。
- jscall 固定按 1 MiB / 200 ms 刷新，刷新间隔不可配置。应依靠正常关闭和最终 writer stats
  验收；外部强杀不保证队列排空。
- 当前只做**全局总启停**（一个闸门控所有已配置模块同开同关）；不按模块/脚本细分（脚本收窄
  已由 `JSCALL_SCRIPT_URL`/`OPCODE_URL`/`DETAIL_SCRIPT_URL`/`JSVMP_SCRIPT_URL` 在配置层覆盖）。
- HTTP 报文、WebSocket 握手和数据帧均受闸门控制；HTTP-only、WebSocket-only 和
  Exception-only 配置也会独立启动 Gate 轮询，不要求额外开启核心或 JSCall。
- API override 不属于日志 Trace，仍按启动配置独立生效。Trace 不修改网页 API/prototype 或正常
  网络语义，但开窗期间的性能开销可能形成计时侧信道，不能把“无接口改动”理解为绝对不可检测。

### 8.6 日志验收

每次浏览器实测后按进程逐个检查：

1. 每个非空 JSONL 行都可解析，文件尾没有半行。
2. 受控采集的 `session_started` / `session_stopped` 按相同 `pid` 和 `session` 配对；缺失时将该
   session 标为边界不完整，不直接推断退出原因。核心文件中的孤立 `session_started` 只算开闸尝试，
   还需确认 jscall marker 或后续业务记录。
3. 搜索 `limit_reached`，记录其 `scope` 和 `limit`；它表示数量截断，不等同于 writer 丢弃。
4. 异步文件的 `trace_init.writer_backend` 应为 `"async"`，并核对队列、批量和刷新参数。
5. 正常关闭后，每个异步 jscall 文件应以最终 `jscall_writer_stats` 为末行，所有 `dropped_*`
   应为 0。`io_errors` 也应为 0，但它只是该行写出前的错误快照，不覆盖随后 flush/close；缺少
   末行、任一丢弃计数非零或 JSONL 解析失败都表示该进程日志不完整。
6. 若任务要求参数或返回值，确认目标调用同时产出 `jscall_detail`，不能只凭 `jscall`/
   `jscall_frame` 判定已抓到值。
7. writer stats 可能与已排队业务记录局部 `seq` 倒序。还原调用链使用 `call_id`、
   `parent_call_id` 和 `ts`，不要要求文件行序严格按 `seq` 单调。

---

## 9. SOCKS5 密码代理（`--fpfile`）

当前 Firefox 支持 SOCKS5 username/password 认证；凭据不放在环境变量里，也不走标准 pref 用户名/密码，
而是通过启动参数 `--fpfile=<path>` 读取。这里保持现有机制，不新增环境变量。

### 9.1 最小配置

profile 的 `user.js` 设置 SOCKS5 代理：

```js
user_pref("network.proxy.type", 1);
user_pref("network.proxy.socks", "<proxy_host>");
user_pref("network.proxy.socks_port", 1080);
user_pref("network.proxy.socks_version", 5);
user_pref("network.proxy.socks_remote_dns", true);
user_pref("network.proxy.no_proxies_on", "localhost,127.0.0.1");
```

把 `<proxy_host>` 和 `1080` 换成实际代理主机与端口；端口必须是整数，不加引号。

`proxy.fp` 写一行：

```text
<proxy_host>:<proxy_port>:<proxy_username>:<proxy_password>
```

启动时必须用等号形式：

```bat
firefox.exe --new-instance -no-remote -profile C:\path\profile --fpfile=C:\path\proxy.fp "https://example.com/"
```

注意：不要写成 `--fpfile C:\path\proxy.fp`。实测这种空格分隔形式会导致底层拿不到 fpfile 值，SOCKS5 握手只声明
`methods=00`（无认证），不会发送 username/password。`--fpfile=C:\path\proxy.fp` 会声明 `methods=02` 并发送
RFC1929 用户名密码。Firefox stdout 里可能仍出现 `Warning: unrecognized command line flag "-fpfile"`，但等号形式下
`base::CommandLine` 能正确读到该 switch，网络层认证生效。

### 9.2 fpfile 支持格式

普通 SOCKS 认证可以用一行式：

```text
host:port:username:password
```

也可以用键值式：

```text
socksauth.host=<proxy_host>
socksauth.port=<proxy_port>
socksauth.username=<proxy_username>
socksauth.password=<proxy_password>
```

如果走 Ruyi 轮换代理模式，可以在同一个 fpfile 里使用：

```text
proxy.rotate.enabled:true
socks5proxy.rotate.proxy:socks5://<proxy_host>:<proxy_port>:<proxy_username>:<proxy_password>
```

### 9.3 使用注意和自测

- 必须使用 `--fpfile=<path>`，不要写成 `--fpfile <path>`。空格分隔形式会导致底层拿不到 fpfile 值，
  SOCKS5 握手只声明 `methods=00`（无认证），不会发送 username/password。
- 等号形式会让 SOCKS5 握手声明 `methods=02`，并按 RFC1929 发送用户名密码。
- `network.proxy.socks_remote_dns=true` 建议开启，避免目标域名在本地解析，保持 `socks5h` 等价行为。
- 外发文档不记录具体代理服务、账号、密码、出口 IP 或本地证据目录。

自测脚本：

```bat
python test_firefox_socks5_auth_local.py
python test_firefox_socks5_live_proxy.py --host YOUR_PROXY_HOST --port YOUR_PROXY_PORT --user YOUR_PROXY_USERNAME --password YOUR_PROXY_PASSWORD --scheme https
```

---

## 10. 启动时 API 定制（`MOZ_DOM_API_*`）

用于复现和 trace 调试这些浏览器 API：

- `Date.now()` / `new Date()` / `Date()`
- `Math.random()`
- `crypto.getRandomValues()`
- `performance.now()`

这组开关和其他 ruyitrace 开关一样：启动 Firefox 前通过环境变量设置，进程内第一次命中相关 API 时读取并缓存一次，页面运行中修改环境变量无效。
默认不设 `MOZ_DOM_API_OVERRIDE` 时完全走 Firefox 原生行为。

### 10.1 开关

| 开关 | 可用值 | 默认 | 功能 |
|---|---|---|---|
| `MOZ_DOM_API_OVERRIDE` | `1` / `0` / 不设 | 不设 | 总开关。只有 `1` 才允许下面的单项 override 生效；`0` 或不设均为全原生 |
| `MOZ_DOM_API_DATE_NOW` | `native` / `fixed:<ms>` / `increment:<start_ms>:<step_ms>` / `sequence:<ms1>,<ms2>` | `native` | 控制 `Date.now()`、`new Date()`、`Date()` 使用的当前时间 |
| `MOZ_DOM_API_PERFORMANCE_NOW` | `native` / `fixed:<ms>` / `increment:<start_ms>:<step_ms>` / `sequence:<ms1>,<ms2>` | `native` | 控制 `performance.now()`；结果仍会保持非负和不倒退 |
| `MOZ_DOM_API_MATH_RANDOM` | `native` / `fixed:<0..1>` / `sequence:<v1>,<v2>` / `seeded:<seed>` | `native` | 控制 `Math.random()`，返回值必须在 `[0, 1)` |
| `MOZ_DOM_API_CRYPTO_RANDOM_VALUES` | `native` / `seeded:<seed>` / `increment:<0..255>` / `pattern:<hex>` | `native` | 控制 `crypto.getRandomValues()` 填充内容 |

数值说明：

- `Date` 和 `performance` 的单位都是毫秒。
- `seed` 支持十进制或 `0x` 十六进制。
- `pattern:<hex>` 可以写成 `pattern:001122ff` 或 `pattern:00,11,22,ff`。
- `sequence` 最多读取 32 个值；耗尽后重复最后一个值。
- `pattern` 最多读取 64 字节；填充 TypedArray 时循环使用。

### 10.2 示例

只固定 `Date.now()`：

```bat
set MOZ_DOM_API_OVERRIDE=1
set MOZ_DOM_API_DATE_NOW=fixed:1700000000000
firefox.exe
```

复现一组可重复的随机值，同时让 `performance.now()` 稳定递增：

```bat
set MOZ_DOM_API_OVERRIDE=1
set MOZ_DOM_API_MATH_RANDOM=seeded:0x123456789abcdef0
set MOZ_DOM_API_CRYPTO_RANDOM_VALUES=seeded:0xfedcba9876543210
set MOZ_DOM_API_PERFORMANCE_NOW=increment:1000:16.6667
firefox.exe
```

四项全开调试组合：

```bat
set MOZ_DOM_API_OVERRIDE=1
set MOZ_DOM_API_DATE_NOW=fixed:1700000000000
set MOZ_DOM_API_PERFORMANCE_NOW=increment:1000:16.6667
set MOZ_DOM_API_MATH_RANDOM=seeded:0x123456789abcdef0
set MOZ_DOM_API_CRYPTO_RANDOM_VALUES=seeded:0xfedcba9876543210
firefox.exe
```

### 10.3 使用纪律

- 默认保持不设置 `MOZ_DOM_API_OVERRIDE`。
- 只开启当前 trace 需要观测的 API。
- `performance.now()` 不建议长期 `fixed`，优先 `increment` 或 `native`。
- `Math.random()` 和 `crypto.getRandomValues()` 优先 `seeded`，避免固定值或短 pattern 导致页面检测异常。
- 开关不读取 fpfile、不写日志、不做运行时文件 I/O、不全局禁用 JIT。
- 只有 `Math.random()` override 生效时，会阻止对应 JIT 内联路径，避免绕过本开关。

### 10.4 用 `C:\ruyipage` 自动化自测

默认关闭态应满足：

- `Date.now()` 与 `+new Date()` 接近。
- `Math.random()` 返回 `[0, 1)`，连续调用不固定为同一个值。
- `crypto.getRandomValues()` 返回原 TypedArray，数据长度正确且不是全 0。
- `performance.now()` 返回 number，连续读取不倒退。

开启态建议检查：

- `MOZ_DOM_API_DATE_NOW=fixed:1700000000000` 时 `Date.now()` 恒为 `1700000000000`。
- `MOZ_DOM_API_PERFORMANCE_NOW=increment:1000:16.6667` 时连续读取不倒退并按步长增长。
- `MOZ_DOM_API_MATH_RANDOM=seeded:<seed>` 时同一进程内产生可复现序列。
- `MOZ_DOM_API_CRYPTO_RANDOM_VALUES=seeded:<seed>` 时填充结果可复现且仍返回原 TypedArray。

已验证的结果口径：

- 默认关闭态：`ruyipage` smoke 返回 `ok: true`，原生行为保持正常。
- 启动前设置并开启四类 API 后：固定 Date、递增 performance、seeded Math、seeded crypto 均按配置生效。
