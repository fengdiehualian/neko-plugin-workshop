# 插件设计说明（DESIGN）

> 本文件是插件的设计文档，不参与运行。遵循 N.E.K.O 插件契约：所有可写内容都在本插件目录内。
## 免责声明 / 反作弊提示（设计条款）

> **本插件不保证不会触发游戏反作弊，使用本插件引起的后果与本插件无关。**

这是**使用前提示**，不是权限门 —— 设计上分两层，互不耦合：

| 层 | 位置 | 行为 |
| --- | --- | --- |
| **面板层**（挡住操作） | hosted UI（`static/index.html`） | 面板载入时调 `get_disclaimer`；`required=true` ⇒ 显示免责模态（遮罩 + `#disclaimer-modal`），**模态显示期间操作按钮不可用**，不存在「未确认也能点立即优化」的路径。点主按钮「我已了解并继续」⇒ 调 `acknowledge_disclaimer(dont_remind_again=<「以后都不再提醒」复选框状态>)`，成功后关闭模态 |
| **引擎层**（只提示，不拒绝） | 入口返回值 | 未确认时 `apply_now` 的返回里附 `disclaimer_warning`（逐字含上面的免责句）与 `disclaimer_required=true`，**但绝不因此拒绝执行**（状态照常变 `applied`）；已确认时不带这两个字段 |

- **判定真源**：插件配置键 `disclaimer_acknowledged`（`_DEFAULTS` 默认 `false`；`plugin.toml` 的 `[perf_saver]` 同值）。`get_disclaimer.required = not acknowledged`。配置通道与其它键完全一致：`plugin.toml` 是唯一真源，入口写盘落在数据目录的本地覆盖里（见「配置真源与本地覆盖（G1）」）。
- **「以后都不再提醒」**：`acknowledge_disclaimer(dont_remind_again=true)` ⇒ `_set_option("disclaimer_acknowledged", True)` 持久化，并在数据目录写 `disclaimer_ack.json`（`{acknowledged, acknowledged_at, dont_remind_again}`，仅供面板显示人可读时间戳 —— 「确认过没有」一律以配置键为准，不以该文件为准）。`dont_remind_again=false` ⇒ **本次放行、不写盘**，`required` 仍为 `true`（下次打开仍提醒）。
- **幂等**：重复 `dont_remind_again=true`，第二次起 `changed=false`，不再重复写盘、不重复更新确认时间。
- **只读入口**：`get_disclaimer` 不写盘、不枚举也不触碰任何进程（与 `status` / `diagnose` 同级的只读入口）。
- **明确不做**：不做「未确认就禁止引擎侧执行」的硬拒（会与「随时开关插件、不受游戏是否运行影响」的可用性诉求冲突）；不校验、不探测、也不试图规避任何游戏的反作弊实现 —— 提示只负责把**后果归属**说清楚。

## Identity Lock

| 项 | 值 |
| --- | --- |
| 插件目录 | `neko_perf_saver` |
| `[plugin].id` | `neko_perf_saver` |
| `[plugin].name` | 性能守护 |
| `[plugin].entry` | `plugin.plugins.neko_perf_saver:NekoPerfSaverPlugin` |
| 包类型 | `plugin`（独立功能插件，非 extension / adapter） |
| 版本 | 1.1.0 |

## 目标

游戏（或任何指定软件）运行时，N.E.K.O 不应该继续抢 GPU / 内存。插件把
「停止界面渲染 + 关闭可关闭进程 + 回收内存」自动化，并且**任何退出路径都必须恢复**。

## 边界

- 只操作 `neko_process_names` 命中的 N.E.K.O 进程；
- 目标软件与其它进程绝不触碰（除非用户显式配置 `close_extra_processes`）；
- 不使用 `plugin.sdk.shared.*` 私有接口；
- 只用 Windows 原生 API（ctypes）+ 宿主自带的 psutil，不引入第三方依赖；
- 定时器 3 秒一跳，用配置里的 `watch_interval_seconds` 做节流，避免空转。

## 状态机

```
        target 运行                      target 退出 / saver 关闭 / 插件 shutdown·freeze
IDLE ─────────────────► APPLIED ──────────────────────────────────────────────► IDLE
                          │  ▲
   N.E.K.O 切到前台 ───────┘  └─────── 切回后台（maintenance 检测）
                       PAUSED(soft)
```

`APPLIED` 期间每个 `maintenance_interval_seconds` 做一次维护：把新出现的渲染进程也挂起、
按 `trim_interval_seconds` 重复回收内存。

## 恢复矩阵

| 触发 | 处理 |
| --- | --- |
| 目标软件退出 | `restore_on_target_exit` → 全量恢复 |
| 插件停止/禁用/重载 | `lifecycle shutdown` → 全量恢复（同步、快速） |
| 插件被冻结 | `lifecycle freeze` → 全量恢复 |
| 手动 `restore_now` / `disable_saver` | 全量恢复 |
| 插件进程**正常退出** | `atexit` → `_emergency_restore`（恢复 + 优先级还原） |
| 插件进程**被强杀** / 宿主崩溃 | ⚠️ `atexit` **不执行**（Windows `TerminateProcess` 跳过 Python 清理）→ 下次 `startup` 读取 `saver_session.json` 救援（挂起进程 + 优先级 + 不可重建的额外进程）。窗口期：插件下次启动之前 |
| N.E.K.O 整个退出 | 挂起随进程消失，文件残留由下次启动清理 |

### 配置真源与本地覆盖（G1）

- `plugin.toml`（`[perf_saver]`）是**唯一真源**；入口（`enable_saver` / `set_option` /
  `set_target_processes`）写入的改动记在 `data/saver_settings.json`，作为「本地覆盖」压在 toml 之上，
  以便在管理器没有配置页字段时也能持久化。
- 覆盖**不会永久绑架**配置：`config_change` 时，若某个键在 toml 里的值发生了变化（= 用户在管理器里动过），
  该键的覆盖立刻作废；覆盖值与 toml 相等时也会被顺手回收。`status.options_source` 暴露每个键的来源。
- 官方回写通道（`await self.config.update(...)` / `await self.ctx.update_own_config(...)`）不会回派
  `config_change`，所以任何走那条路的新代码都必须自己刷新派生状态。

### 安全护栏（G2 / G5）

- `close_extra_processes`：目标软件、插件自身、系统关键进程（`explorer.exe`/`lsass.exe`/`dwm.exe`…）
  永不结束；过宽通配（`*`/`?`）拒绝；单次上限 `_MAX_EXTRA_KILLS = 5`；入口支持 `dry_run` 预览。
- 所有跨进程还原（恢复挂起 / 还原优先级）前用 `create_time` 校验 PID 未被复用
  （容差 0.5s，`_identity_ok()`），拿不到当前值时退化放行（不阻塞恢复）。

## 被结束的进程如何回来

- Electron 工具进程（视频采集/音频/网络）是 Chromium **按需重建**的 utility 进程：
  被结束不会破坏应用，下一次需要时会由浏览器进程重新拉起 → 不需要插件插手。
- 用户自定义的 `close_extra_processes`：记录 `cmdline`/`cwd`，恢复时用
  `CreateProcess`（`DETACHED_PROCESS`）原样重启，失败会在返回里明确报错。

## 不做什么（第一版范围外）

- 不注入、不 hook、不修改 N.E.K.O 源码；
- 不降低游戏自身占用、不碰反作弊进程；
- 不提供图形界面（用入口 + 插件管理器配置页即可满足开关需求）；
- 不做跨平台（Linux/macOS 下判定为不支持，只会在诊断里写明）。

## 面板配置表单与「预览」的零请求契约（US-004）

### 表单 ↔ 入口的映射

| 表单项 | 控件 | 保存时调用的入口 | 形态 |
| --- | --- | --- | --- |
| `saver_enabled` / `freeze_gpu` / `close_video_capture_service` / `close_audio_service` / `skip_while_neko_foreground` / `restore_on_target_exit` | checkbox | `set_option` | `set_option(key, "true" / "false")` |
| `target_processes` | textarea（逗号/分号/换行分隔） | `set_target_processes` | `set_target_processes(processes="a.exe,b.exe")` |

- 初值与每次操作后的回填都来自 `status.options`（唯一的权威来源，面板不缓存初值）。
- 只提交有改动的项；无改动 ⇒ 0 个写请求。
- 前端预检与后端 `_pattern_refusal`（`_MIN_WILDCARD_LITERAL = 3`）同口径拦下过宽通配；
  `set_target_processes` 自身不校验 `*`，所以这道前端拦截是**必须的**，不是锦上添花。

### 「预览」为什么是纯客户端零请求

服务端 `dry_run` 在本插件里**不是通用语义**：

- `__init__.py:961` `set_option(key, value, dry_run="", **_)` —— 只有 `key == "close_extra_processes"`
  分支消费 `dry_run`（护栏预检 + 预览返回，L968-990）；**其它 key 的 `dry_run` 被忽略**，
  函数继续走 `L991-993` 的 `_set_option(...)` 真实写盘。
- `__init__.py:944` `set_target_processes(processes, **_)` —— 没有 `dry_run` 形参，
  字符串按 `[,;\n\r]+` 切分后落库为 list。

⇒ 任何「用 `dry_run` 做预览」的实现都会在真实宿主上**静默修改配置**，比不做预览更糟。
因此面板的「预览」只做**本地计算**：展示「将要发送的入口 + 参数」与「变更摘要（旧值 → 新值）」，
**不发任何请求**（不调 `set_option` / `set_target_processes` / `status`）。

- 这条契约由静态自检（`preview 函数体内不得出现 callEntry / fetch / cfgRun / refreshStatus`）
  与假宿主 + 真实浏览器实测（点预览后请求日志零新增、`status.options` 前后逐字不变）双向保证。
- 免责声明未确认时，`#cfg-preview` 与 `#cfg-save` 同样 disabled，
  并同样被捕获阶段的 `guardActionClick` 拦截 —— 表单没有绕过免责声明的后门。

### 已公开但未修的后端项（超出本版本范围）

`set_option` 对非 `close_extra_processes` 的 key 忽略 `dry_run` 属于后端语义陷阱：
调用方无法从签名看出「预览可能写盘」。修它要动 `__init__.py`，故本版本只在面板与 README 里
如实披露，未改变实现。
