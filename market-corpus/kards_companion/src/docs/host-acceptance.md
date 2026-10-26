# N.E.K.O 宿主验收（预备阶段）

本文档配套只读验收脚本 `scripts/host_acceptance.py`，用于在真实 N.E.K.O 宿主上确认
`kards_companion` / `kards_companion_dev` 的安装、启用、注册与只读状态接口。

**当前阶段只准备，不执行真实连接测试。** 宿主、Steam、KARDS 均未启动；本次没有执行
`--launch-host`，也没有调用任何插件动作。真实连接测试需在实现完成并获明确授权后进行。

## 安全边界

- 只访问 `127.0.0.1`；不访问外网，不跟随 HTTP 重定向。
- 默认只读：进程、端口、`GET /openapi.json`、`GET /plugins`、已安装插件元数据。
- `--diagnostics` 是显式开关；只允许调用 `monitor_status` 与 `monitor_diagnostics`
  两个只读动作，绝不按名称调用其它动作。脚本只承认已观察到的 Hosted UI 形状
  `/plugin/{plugin_id}/hosted-ui/action/{action_id}`；operation 文档若存在必须指向
  Hosted UI。其它路径（包括仅含 action/invoke/entry 字样的 POST）一律不执行；
  形状不符或查询参数/请求体未确认时返回 `blocked` / `unconfirmed`，不猜。
- `--launch-host` 是唯一会启动宿主的入口；必须显式传入，且只启动固定验证路径的
  `E:\Steam\steamapps\common\n.e.k.o\N.E.K.O.exe`，最多等待 60 秒。
- 不启动 KARDS/Steam，不生成游戏输入，不读内存，不抓包。
- `%USERPROFILE%\.neko\bridge.json` 中的 token 只在内存中用于一次 401/403 重试；
  不写入报告、不写日志、不持久化。报告中仅有布尔值 `bridge_auth_used` /
  `bridge_auth_persisted`。
- 报告不包含原始配置文件、token、用户聊天、prompt、截图或原始 OCR 文本。
- 不读取旧 Documents 目录，也不读取旧 v5 密钥。

## 已确认的本地元数据（2026-10-17 快照）

- 宿主：`E:\Steam\steamapps\common\n.e.k.o\N.E.K.O.exe`；
  backend：`resources\bin\projectneko_server.exe`。
- 端口来源：`%APPDATA%\N.E.K.O\port_config.json`；当前为 MAIN 48911、
  USER_PLUGIN 48916。脚本同时接受 `MAIN_SERVER_PORT` / `MAIN_SERVER` 与
  `USER_PLUGIN_SERVER_PORT` / `USER_PLUGIN_SERVER`；若键缺失，会记入
  `ports.defaults_used`，不静默掩盖配置缺失。
- 插件扫描根：
  `%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins`。
- 启用状态来源：
  `%LOCALAPPDATA%\N.E.K.O\config\plugin_runtime_overrides.json`。
- 当前快照：`kards_companion_dev` 启用、`kards_companion` 停用；两者均已安装，
  `plugin.toml` 版本 0.1.0。
- 安装源静态注册（以实际扫描结果为准）：7 个 entry / UI action
  （`monitor_start`、`monitor_stop`、`monitor_status`、`monitor_diagnostics`、
  `mode_set`、`dry_run_set`、`deck_import`），4 个 LLM tool
  （`kards_get_live_state`、`kards_search_cards`、`kards_import_deck`、
  `kards_get_match_history`）。
- 注意：静态扫描针对当前装饰器写法；若宿主实际注册与源码不同，以 live OpenAPI /
  `/plugins` 和宿主日志为准。

## 用法

在仓库根目录运行（Python 3.11+，仅标准库）：

```powershell
# 默认只读探测；宿主未启动时如实返回 exit 2 和原因
python scripts/host_acceptance.py --probe

# 显式允许只读诊断动作（仅在宿主已启动后使用）
python scripts/host_acceptance.py --diagnostics --plugin-id kards_companion_dev

# 显式启动已校验的宿主并等待最多 60 秒（本阶段不要执行）
python scripts/host_acceptance.py --launch-host --diagnostics
```

可选参数：`--plugin-id`、`--timeout`（单次请求超时，默认 2 秒）。`--probe` 是默认行为。

## 报告与退出码

默认将脱敏 JSON 打到 stdout，向 stderr 打一行摘要。核心字段：

- `host`：固定 exe 是否存在、进程探测、launch 结果（`reason` 如实给出）；
- `ports`：配置来源、解析后的端口、`defaults_used`、TCP 是否可连；
- `openapi`：每个已开端口上的 OpenAPI 摘要（路径数、疑似 action 路径、是否观察到
  Hosted UI 模板）；
- `plugins`：dev/formal 是否安装、`enabled`/`auto_start`、静态 entry/tool/UI action
  注册（`registration_source=installed_static_source`，不等同 runtime verified）、
  所选插件、`/plugins` 运行时列表；
- `diagnostics`：仅在显式 `--diagnostics` 时出现；记录 route、动作结果与脱敏摘要；
- `policy`、`reasons`、`manual_actions`、`limitations`。

退出码：

| 码 | 含义 |
| --- | --- |
| 0 | 请求的探测/诊断完成，宿主可达，未发现阻断原因 |
| 1 | 未捕获的脚本异常（不应作为成功） |
| 2 | 宿主未运行，或插件端口不可达 |
| 3 | 运行时 API 不可验证、插件未安装/未启用，或诊断路由无法确认 |
| 4 | 显式请求了 `--launch-host` 但启动失败/超时/宿主已运行 |

任何非 0 结果都必须保留 `reasons`，不得当作验收通过。

## 真实接入时的验收流程（后续、需授权）

1. 确认只启用 dev 或 formal 之一，避免重复 entry / 双实例；确认旧版 v5 未同时加载。
2. 仅在明确授权后执行 `--launch-host`；确认宿主进程、backend 进程和 48916 端口。
3. 运行 `--probe`：确认 `/openapi.json` 或 `/plugins` 可达，且选中的插件为启用候选。
4. 核对静态注册与运行时注册：entry / UI action / LLM tool 名称与数量一致；不一致时
   以 live OpenAPI `/plugins` 为准并记录差异。
5. 运行 `--diagnostics`：确认只出现 `monitor_status`、`monitor_diagnostics` 两个动作，
   且实际路由为已观察到的 Hosted UI 模板；时间线只保留 stage/result/reason 等元数据。
   若返回 `blocked` / `unconfirmed`，保留原因，等真实 schema 确认后再继续，不得为
   了“跑通”而放宽路由匹配。
6. 手工连接测试（后续由集成实现）：实现 `monitor_test_say`（名称可能变化，必须以
   live OpenAPI/action 实际为准）。只调用一次并记录返回。
   **`submitted` 只表示本地已提交，不等同于 `played`/已发声/已听到**；不得把提交
   成功写成播放成功，除非另有独立证据。
7. 保存本次脱敏 JSON、时间戳、宿主版本与插件源哈希。不得保存 token 或原始聊天。

## 通过标准（建议）

- 宿主与插件端口可达；OpenAPI 或 `/plugins` 至少一个可验证。
- 仅一个候选启用；安装源与运行时注册无未解释差异。
- `--diagnostics` 完成且仅含两个约定只读动作；无未知 action 调用。
- 无 KARDS/Steam 启动、无游戏输入、无外网请求。
- 报告无 token、原始配置、用户聊天或 prompt。
- 手工 test-say 的结论明确区分 `submitted`（本地提交）与 `played`（实际播放）。

## 当前限制

- 还没有真实 API 运行结果；action 路由、`/plugins` 响应形状和运行时注册字段均未实机
  确认。脚本只接受已观察到的 Hosted UI 模板；该模板或 operation 文档未确认时，
  `--diagnostics` 会保持 `blocked` / `unconfirmed`，不执行未知动作。
- entry/tool/UI action 为安装源静态装饰器扫描；格式变化或多行参数可能漏计，需与
  live OpenAPI/宿主日志交叉确认。
- 宿主版本升级后 OpenAPI、动作名称、端口和插件启用状态都可能变化；每次验收重新运行
  探测，不沿用旧结论。
- 本阶段没有启动宿主，也没有进行连接测试；不要用本文件代替真实验收证据。
