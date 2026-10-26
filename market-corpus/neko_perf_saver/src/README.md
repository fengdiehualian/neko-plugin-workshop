# 性能守护（neko_perf_saver）

在指定软件运行时降低 N.E.K.O 自身的内存与 GPU 占用，并且**保证可逆**。
## ⚠️ 免责声明 / 反作弊提示（使用前必读）

> **本插件不保证不会触发游戏反作弊，使用本插件引起的后果与本插件无关。**

- **面板**：未确认时打开插件面板会弹出免责声明模态（主按钮「我已了解并继续」）；**在确认之前，面板上的操作按钮不可用** —— 点掉模态才能用面板操作插件。
- 模态里的复选框「**以后都不再提醒**」**默认未勾选**：

| 你的操作 | 结果 |
| --- | --- |
| 勾选「以后都不再提醒」→ 点「我已了解并继续」 | 写入插件配置 `disclaimer_acknowledged = true`，以后打开面板**不再弹** |
| **不勾选** → 直接点「我已了解并继续」 | 只是「这一次知道了」，**不写盘**，下次打开面板**仍会弹** |

- **想让它重新提醒**：在插件管理器的配置页把 `disclaimer_acknowledged` 改回 `false`（管理器里改过的键会立即生效，并作废该键的本地覆盖 —— 见下方「常用入口」），下次打开面板就会重新弹。
- **引擎侧不设硬门**：直接调用入口（如 `apply_now`）时，未确认**不会被拒绝执行**；插件只在返回值里附 `disclaimer_warning`（内含上面那句免责声明）与 `disclaimer_required = true`。⇒ **免责声明是提示，不是权限门**：是否使用本插件、以及使用后果，由使用者自负。
- 相关入口：`get_disclaimer`（只读，返回 `acknowledged` / `required` / `text` / `acknowledged_at`）、`acknowledge_disclaimer`（参数 `dont_remind_again`，语义见上表）。

## 它做什么

触发软件（默认 `DeltaForceClient-Win64-Shipping.exe` / `DeltaForceClient.exe`，即三角洲行动）
一启动，插件立刻对 N.E.K.O 做三件事：

| 动作 | 手段 | 效果 |
| --- | --- | --- |
| 停止界面渲染 | `NtSuspendProcess` 挂起 Electron 渲染进程（可选 GPU / 工具进程） | GPU 占用 → ~0，CPU → ~0，画面定格 |
| 关闭可关闭的进程 | `TerminateProcess` 结束视频采集服务等**可自动重建**的工具进程 | 立刻释放 ~100MB 内存 |
| 内存回收 | `EmptyWorkingSet` 回收 N.E.K.O 全部进程工作集 + 优先级降到 IDLE | 工作集可掉数百 MB，CPU/IO 让给游戏 |

目标软件退出后自动恢复；也可以随时手动「立即优化 / 立即恢复」。

## 恢复保证（重点）

被挂起的进程会被记录在 `saver_session.json`，以下任何情况都会恢复：

1. 目标软件退出（`restore_on_target_exit`）；
2. 插件被**停止 / 禁用** → `shutdown` → 强制恢复；
3. 插件被**冻结**（宿主不再调度）→ `freeze` → 恢复；
4. 插件进程**正常退出**（`sys.exit` / 解释器结束）→ `atexit` 兜底恢复；
5. 插件进程**被强杀**（任务管理器结束任务 / 宿主 `TerminateProcess`）或 N.E.K.O 崩溃 →
   `atexit` **不会**执行（Windows 强杀不跑任何 Python 清理）。唯一的兜底是**下次插件启动**时
   读取 `saver_session.json` 自动救援上次遗留的挂起进程。也就是说，从「插件进程死亡」到
   「下次插件启动」之间存在一段**没人恢复的窗口期**（N.E.K.O 界面会保持定格），
   这是当前设计的已知边界，不是 bug。
6. `NtResumeProcess` 精确逆转挂起，优先级还原为原值（还原前会校验 PID 未被复用）；
7. 被结束的工具进程是 Chromium **按需重建**的（浏览器进程会重新拉起），不依赖插件；
   若用户配置了 `close_extra_processes`，插件会用记录下来的原命令行重新启动它们。

## 安全护栏（不会被误配搞坏系统）

- 插件只操作名字命中 `neko_process_names` 的 N.E.K.O 进程，**绝不触碰游戏或其它软件**；
- `close_extra_processes` 有三重护栏：目标软件 / 插件自身 / 系统关键进程（`explorer.exe`、
  `lsass.exe`、`dwm.exe` …）永远跳过；`*`、`?` 这类过宽的通配模式被拒绝；单次优化最多结束 5 个进程。
  想先看会命中谁，用 `set_option(key="close_extra_processes", value="xxx.exe", dry_run="true")` 预览；
- 恢复前会比对进程创建时间，PID 被系统复用时不碰那个无关进程；
- 关掉自动守护（`disable_saver`）后插件不再巡检、不做任何降负载动作，只保留恢复能力。

## 常用入口

- `status` — 当前状态、已挂起进程、开关一览
- `apply_now` / `restore_now` — 手动优化 / 手动恢复
- `enable_saver` / `disable_saver` / `toggle_saver` — 开关自动守护（关掉时立即恢复）
- `set_target_processes` — 换触发软件（如 `Cyberpunk2077.exe`）
- `set_option` — 改单个开关，如 `set_option(key="close_audio_service", value="true")`；
  会结束进程的 `close_extra_processes` 支持 `dry_run="true"` 预览
- `diagnose` — 环境诊断（进程分类、后端、目标是否在跑）
- 配置文件与入口的关系：`plugin.toml` 是唯一真源；入口（`enable_saver` / `set_option` 等）
  改动会记在 `data/saver_settings.json` 里作为「本地覆盖」。**在插件管理器配置页改了某个键之后，
  该键的本地覆盖会自动作废**（管理器里的值立即生效）。`status` 返回的 `options_source` 会告诉你
  每个键当前来自 `toml` 还是 `override`。

## 注意事项

- 挂起渲染进程时 N.E.K.O 的画面会**定格且显示「无响应」**，这是预期状态；语音也会一起停。
  默认开启 `skip_while_neko_foreground`：把 N.E.K.O 切到前台会自动临时恢复，切回游戏后再次优化。
- 结束音频服务（`close_audio_service=true`）会让 N.E.K.O 静音；需要语音陪伴就保持 `false`。
- 插件只操作名字命中 `neko_process_names` 的 N.E.K.O 进程，**绝不触碰游戏或其它软件**。
- Windows 专用（依赖 `NtSuspendProcess` / `EmptyWorkingSet`）。

## 面板配置表单（7 项开关）

`static/index.html` 的配置表单（`id="panel-config"`）就是这些开关的图形入口，覆盖 7 项：
`saver_enabled`、`target_processes`、`freeze_gpu`、`close_video_capture_service`、
`close_audio_service`、`skip_while_neko_foreground`、`restore_on_target_exit`。

- 初始值一律来自 `status` 返回的 `options`（面板里不写死任何初值），保存后重新读回并回填。
- 布尔项保存为 `set_option(key, "true" / "false")`；`target_processes` 保存为
  `set_target_processes(processes="a.exe,b.exe")`（逗号/分号/换行分隔都接受）。
- **只发有改动的项**：没有任何改动时，一次写入口都不会调用。
- 非法输入在面板侧就拦下（空列表、项内空项如 `a.exe,,b.exe`、过宽通配如 `*` / `*.exe`），
  被拦下时同样**不发任何请求**、生效配置零变化。
- 未确认「使用前提示」前，保存/预览按钮均为 disabled，并且走与其它操作按钮相同的捕获阶段拦截。

### 「预览」为什么不提供服务端 dry-run（重要）

面板的「预览」是**纯客户端、零请求**：只列出「将要发送的入口 + 参数」与「变更摘要（旧值 → 新值）」，
不调用任何入口（`set_option` / `set_target_processes` / `status` 都不调）——点完预览后请求数不变、
配置逐字不变。

原因在插件端：`set_option(key, value, dry_run="")` **只有 `close_extra_processes` 这一个分支会看 `dry_run`**；
其它 key 传来的 `dry_run` 会被忽略，函数照常 `_set_option(...)` 写盘。而 `set_target_processes(processes)`
根本没有 `dry_run` 形参。也就是说，基于 `dry_run` 的「预览」在真实宿主上会**偷偷改配置** ——
那正是「预览」最不该有的行为。因此本面板不提供、也不发送任何服务端 dry-run 请求：
真正的写入只发生在点「保存配置」时。

（这是插件的既有行为，本版本未修改实现 —— 修它需要改 `__init__.py`。
若后端将来让所有 key 都尊重 `dry_run`，面板才会考虑提供真正的服务端预览。）
