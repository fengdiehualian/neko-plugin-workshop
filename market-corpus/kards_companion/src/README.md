# KARDS 猫娘陪玩（kards_companion）

KARDS 被动读屏插件。画面经采集与双 YOLO 模型（界面检测 + 卡牌分类）识别，场景门控和单位履历转成结构化事件，再由仲裁与统一 gate 输出宿主短解说。只读屏：不读游戏内存、不抓包、不注入键鼠、不自动操作。无 OCR、无录像。

当前清单版本：`6.1.0`（`plugin.toml`）。这是第一个提交到 N.E.K.O 市场的测试版；宿主目标运行时为 Python 3.11。read、respond 和局势查询共用 `kards_situation_v1`；与战雷的对照和限制见 [信息传递说明](docs/message-integration.md)。

开发待办及对应文件位置见 [TODO](docs/TODO.md)。

> 第一版只启用已验证的卡背和回合按钮位置检测。可信卡牌白名单为空，具体卡名、单位事件、回合归属和胜负识别尚未交付。两个 ONNX 可在 CPU 加载；加载成功不代表识别效果通过。完整离线依赖包未构建，正式版不晋级。修复与证据边界见 [第一版说明](docs/v1-closure.md)。

## 兼容性

| 项 | 当前值 |
| --- | --- |
| 插件版本 | `6.1.0`（市场测试版） |
| SDK `recommended` | `>=0.1.0,<0.2.0` |
| SDK `supported` | `>=0.1.0,<0.3.0` |
| Python | 3.11（宿主冻结运行时） |
| 插件形态 | `type="plugin"`、`passive=true`、Hosted UI panel `main`（dashboard） |

兼容范围以 `plugin.toml` 为准。`0.2.x` 只落在 `supported` 内，不属于 `recommended`，也没有真实验收证据。

## 当前状态

| 范围 | 状态 | 证据/限制 |
| --- | --- | --- |
| 事件契约、场景解析、tracker、仲裁、gate、单槽投递 | 已实现，本地测试 | `tests/`、`docs/backend-parity.md`；未做 host 验收 |
| Hosted UI 面板 | 静态实现 | `ui/panel.tsx`；宿主渲染未验证 |
| N.E.K.O 宿主加载、动作路由、`push_message` 实际回执 | 测试版待验收 | 6.1.0 需在 dev 通道重新加载并取得新鲜回执；6.0.1 的历史结论不替代当前版本 |
| 界面检测模型（ui_det） | 受限交付 | card_back 与 end_turn_button 逐类达标；frontline/unit_board/card_hand/played_card 未达冻结线（启发式标签噪声上限）；hq/active/victory/defeat 零样本 unsupported |
| 卡牌分类模型（card_cls） | 候选，未验收通过 | 输出映射为 1083 类；实际训练 1023 类，60 个冲突类未启用；当前白名单 0，所有卡名均为 unknown |
| 宿主 LLM 实际生成 | 未验证 | SDK 本地提交不等于宿主已消费或生成 |
| TTS 实际播放 | 未验证 | 没有播放证据 |
| 卡图资源 | `blocked_unlicensed` | 1083 条来源 URL 元数据；图片字节不随仓库分发 |
| 离线依赖包 | `blocked` | `vendor/`、assets marker、manifest 均缺失；本测试版不提供离线依赖包 |

本 README 只记录仓库中可确认的事实。不要把上表中的未验证项写成已完成能力。

## 目录

| 路径 | 作用 |
| --- | --- |
| `__init__.py` | 插件主类、生命周期、面板动作、LLM tools、感知 tick 集成 |
| `perception/` | 窗口发现与采集、采集健康、双 YOLO 加载与校验（models.py）、界面分类（screens.py，由检测框驱动）、单位检测与跟踪（detector.py）、采样调度（scheduling.py） |
| `runtime/` | 事件契约、场景解析器、tracker 适配器、检测器注册表、快照、诊断时间线 |
| `state/` | `MatchState`/`GameEvent` 等纯数据模型与 `MatchTracker` |
| `delivery/` | 仲裁器、统一 `HostPushGate`、read/respond 发布器 |
| `policy/` | 单槽 dispatcher、输出节奏与暂停守卫 |
| `adapters/` | 目标角色解析与清洗 |
| `knowledge/` | 内置卡库、卡牌目录同步、卡组代码解码、类别映射（card_classes.json / ui_classes.json）、模型清单（models_manifest.json）、可信卡白名单（trusted_cards.json） |
| `worker/` | 离线 worker 第一阶段：bootstrap、JSON-lines 协议、bundle 自检 |
| `scripts/` | bundle manifest、安装同步、宿主只读验收 |
| `ui/` | `panel.tsx` Hosted UI |
| `docs/` | 集成契约、parity、部署、离线包、卡图、宿主验收、YOLO 契约 |
| `tests/` | Python 3.11 单元/集成测试 |
| `models/` | ONNX 权重（ui_det.onnx / card_cls.onnx），不进 git，随部署分发 |
| `assets/`、`vendor/` | 离线包资源位置；当前不存在 |

训练工具与数据不在本仓库内，而是同级的 `training/` 目录，详见“训练状态”。

## 架构链

```text
capture
  → capture health / trusted frame
  → RuntimeSnapshot
  → scene pre-gate（dHash 变化门控）
  → UI YOLO 检测（ui_det）→ 场景/回合态 + UnitTracker
  → 卡牌分类（card_cls，已验证区域裁图）→ 卡面观测（不推断出牌或单位身份）
  → MatchTracker + detector registry
  → normalize_event (runtime/contracts.py)
  → scenario.update
  → kards_situation_v1（当前事实 / 旧观测 / 未知项 / 模型能力 / 来源时间）
  → read 上下文 / respond 提示词 / kards_get_live_state 共用
  → CommentaryArbiter.select/decide + OutputSafetyGuard
  → DeliveryDispatcher (single pending slot)
  → HostPushGate
  → SDK push_message
```

关键门控：

- **可信帧**：只有窗口存在、帧可用、帧龄未过期且图像非空才进入识别。无有效帧、窗口最小化、`process_not_found`、`stale_frame` 时识别与解说暂停，不推进 tracker diff，也不补造单位/HQ/回合事件。
- **场景前置门**：场景为 `OUT_OF_BATTLE` / `OPENING` / `IN_MATCH` / `PRESSURE` / `CRITICAL` / `ENDED`。`menu`/`draft`/`unknown` 帧的 `allow_diff=false`，不喂 tracker；连续 3 帧明确 `menu`/`draft` 才确认离场，`unknown` 不会被当作离场。
- **tracker**：HQ 数字字段（血量/Kredits）第一版恒 `None`（不读动态数字，不填 0）；单位首次出现需要两个不同的真实观测帧确认。单位连续 3 次缺失只记录 `unit_lost` 诊断，**绝不因漏检生成 `unit_died`**。
- **卡牌身份**：仅由 card_cls YOLO 分类产生；conf/margin 双阈值不足、原始最高分类为 conflict/缺图、裁图过小或不在可信白名单时输出 `unknown`。不让次高类别顶替存疑的第一名。放大牌面只产生带帧号的卡面观测，不产生出牌事件，不自动给任何棋盘单位命名；敌方卡背不推断隐藏手牌。
- **能力门控**：ui_det 仅返回模型清单 `capabilities` 中已通过验证的类别。当前是 `card_back` 和 `end_turn_button`；其余八类均被拦截。按钮位置不证明行动方，`my_turn` 保持 `null`。动态血量和资源也保持 `null`。
- **信息整合**：`runtime/situation.py` 生成同一份有界局势视图，包含来源时间、可用能力、当前/保留单位、侧别未知单位、短时卡面观察和不可读字段。`read`、事件 `respond` 和 `kards_get_live_state` 共用；不可识别的棋盘不写成空盘，动态数字未知不写成 0。卡面跨 session 或超过 2 秒后剔除；UI 观测超过 1.5 秒失效，静态画面以新帧心跳复查。
- **上下文生命周期**：可信观测时幂等建立只读上下文，再发布实时事实；退出、暂停、失效或切角色时分别失效原 core/context 槽，并提示恢复日常聊天。source 时间保留真实观测时刻，发布和重试不会刷新事实年龄。consent/dry-run 限制下不为清理而绕过最终门。
- **投递**：`DeliveryDispatcher` 只保留一个 pending 战斗事件，每 tick 至多一次宿主最终尝试，事件有 TTL。所有 `read`/`respond`/`context`/`expire`/`test` 都必须经过 `HostPushGate`；不存在绕过 gate 的直接推送。
- **回执**：只有显式 `accepted` 才算 SDK 本地提交；`legacy_unconfirmed` 和 `unknown` 永远不当作已接受。**SDK `submitted` 只表示本地提交，不代表宿主 LLM 已生成、TTS 已播放或用户已听到。**

## 运行与安装

本仓库是插件源码，不是独立可执行程序，必须由 N.E.K.O 加载。`pytest` 只用于离线测试。

### 安装到宿主

1. 关闭 N.E.K.O。
2. 把整个仓库目录复制到首选安装根：
   `%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\kards_companion\`。
   旧文档中的 `%LOCALAPPDATA%\N.E.K.O\plugins\kards_companion\` 不是首选位置。
3. 本插件 id 为 `kards_companion`，与商店旧版同 id。先卸载/停用旧版，避免双实例。
4. 启动 N.E.K.O，确认插件列表加载 `6.0.1`。

### 开发通道

```bat
py -3.11 scripts\sync_installations.py --check --channel dev
py -3.11 scripts\sync_installations.py --apply --channel dev
```

脚本只写 dev 安装副本；目标侧 `config/`、运行时状态和未知文件保留，不删除。正式通道必须等真实宿主验收证据齐全后再执行，见“发布门禁”。

### 启动后的默认行为

- `[plugin_runtime].auto_start = true`，且 `[kards].auto_start_monitoring = true`。插件启动后会自动开始监控；游戏未启动时进入安全暂停，不会用旧帧推断对局。
- 面板有开始/停止、暂停/恢复、模式切换、dry-run 和感知设置。首次验证建议先开 dry-run，只写 `events.jsonl`，不真实推送。
- 当前源码直接依赖宿主 Python 3.11 环境中的 `numpy`、`Pillow`；仓库没有 `vendor/`，不能当作完整离线依赖包分发。
- 采集后端、检测模型或宿主接口缺失时，功能降级或暂停；模型未加载时对应任务静默，不会伪造。

## 配置

配置键位于 `plugin.toml` 的 `[kards]`。宿主显式写入的值优先，manifest 只提供默认值。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `mode` | `companion` | `companion` / `coach` / `silent` |
| `dry_run` | `false` | 完整跑仲裁/安全链，最终 gate 为 no-op，只写 `events.jsonl` |
| `llm_data_consent` | `true` | 为 `false` 时所有宿主 push（含 `expire`/`test`）为 0 次真实推送 |
| `auto_start_monitoring` | `true` | 插件启动后自动开始监控 |
| `perception_preset` | `balanced` | 采样预设：`low_cost` 15/8/2/3，`balanced` 20/10/4/6，`quality` 30/12/6/10（采集/变化/界面/卡牌）；`custom` 使用四项 FPS |
| `capture_fps` / `change_fps` / `unit_fps` / `card_fps` | `20` / `10` / `4` / `6` | 采集、变化检测、界面识别、卡牌识别 FPS（目标值，面板同时显示实测频率） |
| `min_priority` | `4` | 事件解说最低优先级 |
| `cooldown_seconds` | `25.0` | 普通事件冷却 |
| `critical_cooldown_seconds` | `5.0` | 关键事件冷却 |
| `chatter_interval_seconds` | `60.0` | 平稳期主动点评间隔；`0` 关闭 |
| `do_not_disturb` | `false` | 只静默主动发言，`read` 通道不受影响 |
| `battle_event_ttl_seconds` | `12.0` | 战斗事件最大年龄 |
| `target_lanlan` | `""` | 固定目标角色；空值跟随当前角色，绝不伪造角色名 |
| `plugin_owned_output_enabled` | `false` | 默认由宿主 LLM 生成短回复；`true` 才允许插件自有输出 |
| `user_chat_quiet_window_seconds` | `60.0` | 用户刚聊天时的普通播报安静窗 |
| `battle_output_quiet_window_seconds` | `30.0` | 普通战斗播报安静窗 |
| `battle_output_backpressure_seconds` | `20.0` | 普通播报背压窗 |
| `reply_max_chars` | `28` | 短 TTS 台词字数上限 |
| `catalog_enabled` | `false` | 可选卡牌目录联网更新；内置卡库始终离线加载 |
| `catalog_url` | `""` | 空值使用内置候选；自定义 URL 需通过 SSRF 校验 |
| `vlm_enabled` | `false` | 可选 VLM 兜底；密钥只从 `KARDS_VLM_API_KEY` 读取，绝不写入配置 |
| `imported_deck_code` | `""` | `deck_import` 写入的卡组代码 |

`perception_preset` 与四项 FPS 均支持热更新；面板同时展示目标值与实测频率。

## 动作

### 面板动作

面板当前暴露以下动作：

| 动作 ID | 参数 | 说明 |
| --- | --- | --- |
| `monitor_start` | 无 | 开始监控 |
| `monitor_stop` | 无 | 停止监控，并让实时读上下文过期 |
| `monitor_pause` | 无 | 暂停识别与推送；采集和监控保持运行 |
| `monitor_resume` | 无 | 恢复；首帧重建基线，不补报暂停期事件 |
| `monitor_status` | 无 | 采集健康、场景/上下文、检测器、投递统计 |
| `monitor_diagnostics` | 无 | 诊断时间线、gate 回执、模拟记录、最后快照 |
| `mode_set` | `{ "mode": "companion" \| "coach" \| "silent" }` | 切换解说模式 |
| `dry_run_set` | `{ "enabled": true \| false }` | 切换完整模拟/最终不推送 |
| `perception_settings_set` | `perception_preset`、四项 FPS（capture/change/unit/card） | 严格白名单；未知字段拒绝；旧 `hud_fps`/`roi_auto_calibrate`/`recording_*` 键兼容忽略并告警 |

### 后端接口/开发调试

以下动作仍由后端注册，但当前面板没有入口。只用于直接调用或开发调试，不属于常规上手流程。

| 动作 ID | 参数 | 边界 |
| --- | --- | --- |
| `target_set` | `{ "target": "会话名" }`，空值清除 | 设置固定目标；空值跟随当前角色 |
| `monitor_test_say` | `{ "target": "会话名", "text": "...", "authorized_real_test": true }` | 一次真实连接测试；必须显式授权、有真实目标、非 dry-run、consent 开启、gate 运行且输出安全门允许。回执只表示 SDK 本地提交 |
| `deck_import` | `{ "code": "%%xx\|..." }` | 只保存/解析卡组代码；**不代表画面能识别卡名** |

连接测试的载荷明确 `non_game=true`、`no_history=true`，不读取或声称对局历史；`llm_generated` 和 `tts_played` 保持 `unknown`。

### LLM tools

| Tool | 说明 |
| --- | --- |
| `kards_get_live_state` | 当前结构化状态；无有效帧/分类、consent 关闭或 dry-run 时 `state=null` |
| `kards_search_cards` | 按卡名/关键词查询内置卡牌目录 |
| `kards_import_deck` | 导入卡组代码，行为同 `deck_import` |
| `kards_get_match_history` | 近期对局记录；consent 关闭或 dry-run 时不返回对局数据 |

## 数据与读屏边界

- **读屏只读**：不读游戏内存、不抓包、不注入键鼠、不自动出牌或操作界面。
- **图像不外发**：采集帧只在内存中处理，不落盘；不上传截图、裁剪图或 prompt。诊断时间线只保留阶段、结果、原因和结构化元数据。
- **可选联网**：卡牌目录更新默认关闭；启用后失败保留本地数据，自定义 URL 做 SSRF 校验。VLM 默认关闭，密钥只从环境变量读取。两者都不是运行必需。
- **用户聊天 hook**：只记录用户发言的**时间**和目标是否存在；不读取、不保存聊天文字或语音转写。安静窗只影响普通播报，不阻断 `read` 通道。
- **卡图**：`knowledge/cards_builtin.json` 有 1083 张卡的文本元数据和卡图来源 URL，公开再分发授权未确认；图片字节不随插件分发。运行时认卡只依赖 card_cls 模型权重，不需要卡图文件。
- **卡名**：由卡牌分类 YOLO 产生，白名单（`knowledge/trusted_cards.json`）之外输出 `unknown`；`deck_import` 只保存卡组代码，不代表画面能识别卡名。

### 采集后端

源码顺序为：

1. 显式配置的 host WGC bridge（`host_capture_base`，或环境变量 `NEKO_CAPTURE_API_BASE` / `NEKO_HOST_API_BASE` / `KARDS_CAPTURE_API_BASE`）；
2. `dxcam` 桌面复制；
3. `PIL.ImageGrab` 降级。

host WGC 是显式 opt-in，不是默认路径，也没有 6.0.1 真实宿主验收。`dxcam`/`ImageGrab` 读取桌面，要求 KARDS 在前台并通过已知顶层窗口的遮挡检查；`occlusion_detection` 公开为 `unavailable`，因此后台、被遮挡、独占全屏和虚拟桌面切换的行为**未证实**，不能承诺“后台可读”。健康状态包括 `disabled`、`process_not_found`、`window_not_found`、`minimized`、`frame_unavailable`、`stale_frame`、`capturing`、`degraded`、`stopped`。

### consent、dry-run 与回执

- `llm_data_consent=false`：`read`/`respond`/`context`/`expire`/`test` 全部为 0 次真实推送；实时状态 tool 也不返回对局数据。
- `dry_run=true`：完整执行场景、仲裁、安全链，最终 gate 为 no-op；只写 `events.jsonl`，不触碰真实投递账本，不把模拟记录标成已推送。
- `accepted` 只表示 SDK 本地提交；`legacy_unconfirmed`、`unknown`、`simulated` 不当作已接受、已生成或已播放。

## 离线 bundle 状态

当前仓库不是完整离线包。`worker/` 只有第一阶段骨架，没有模型推理、没有真实 `vendor/`，也不能接收帧或直接向宿主推送。

| 组件 | 状态 |
| --- | --- |
| `worker/bootstrap.py`、`protocol.py`、`package_check.py` | 存在；JSON-lines v1 的 `ping`/`selfcheck`/`shutdown` 可用，`frame.process` 未实现 |
| `vendor/python/`、`vendor/python/Lib/site-packages/` | 缺失 |
| `models/ui_det.onnx`、`models/card_cls.onnx`（可选） | 部署产物；不在 git |
| `assets/cards/manifest.json` | 缺失 |
| `bundle-manifest.json` | 未生成 |
| `worker-runtime.json` | 未生成（可选） |
| `requirements-bundle.txt` | `BLOCKED_PENDING_BUILD` 模板，不是 pip lock，不能直接安装 |
| `THIRD_PARTY_NOTICES.md` | `BLOCKED_PENDING_BUILD` 模板，禁止直接发布 |

因此 `py -3.11 worker/bootstrap.py --check` 的预期结果是退出码 `2`、`status=blocked`。blocked 资源包括 distribution manifest、assets manifest、vendor、vendored Python/site-packages、第三方声明。检查过程不会创建目录、下载资源或改写 manifest。

`worker-runtime.json` 是可选的自检元数据；缺失时 `--check` 最多为 `degraded`，不会达到 `ready`。要发布 `ready` 包，构建必须生成它，并证明所有依赖的 import origin 都在 `vendor/` 内。

**不能把当前仓库称为完整离线包。** 构建完整包必须补齐 vendor、assets、真实第三方许可证、manifest 和构建生成的 lock，见 `docs/offline-bundle.md`。

## 训练状态

训练工具和数据集位于仓库外的同级 `training/`（脚本 `training/yolo_ui/`、`training/yolo_card/`），不随插件发布。raw 帧来自本地 NVIDIA 录屏素材，原始视频不入库、只读。

当前快照（详见 `training` 侧 REPORT 与 `docs/contracts-yolo-v1.md`）：

- 界面检测（ui_det）：8 个录屏组、组级切分 train 388 / val 172 / test 127；启发式预标（provenance 完整保留）；yolo11s 640。冻结线判定：card_back、end_turn_button 达标，frontline / unit_board / card_hand / played_card 未达标（标签噪声上限），hq / end_turn_active / victory / defeat 零样本 unsupported。
- 卡牌分类（card_cls）：1083 类公开卡图训练（weserv 代理取官方图）；60 类同画变体标记 conflict 恒 unknown；真实画面独立验证按 card_id × 外观白名单门控。
- ONNX 导出均通过源框架一致性对比与 CPU 推理验证。
- 同日跨 split 的会话级泄漏风险、以及启发式标签噪声的量化上限，均在训练报告如实声明。

## 测试

测试数量以当前命令输出为准。历史通过数字不代表当前模型或识别质量通过；测试覆盖、真实 ONNX 加载、离线画面效果和宿主加载分别记录，见 [第一版说明](docs/v1-closure.md)。

```bat
py -3.11 -m pytest tests -q
py -3.11 -m pytest tests/test_bundle_runtime.py tests/test_bundle_manifest.py tests/test_card_assets.py -q
py -3.11 -m py_compile worker\__init__.py worker\bootstrap.py worker\protocol.py worker\package_check.py
py -3.11 worker\bootstrap.py --check
py -3.11 scripts\host_acceptance.py --probe
```

- `worker/bootstrap.py --check` 在当前源码树下应返回 `blocked`，这是 fail-closed 的预期结果。
- `scripts/host_acceptance.py --probe` 默认只读；宿主未启动时会如实返回非 0 和原因，不代表验收通过。
- 测试不启动宿主、不启动 KARDS/Steam、不生成游戏输入。

## 发布门禁

发布前必须同时满足：

1. **bundle 完整**：`vendor/python/`、`vendor/python/Lib/site-packages/`、`assets/cards/manifest.json`、`bundle-manifest.json`、`worker-runtime.json`、构建生成的 lock 和核验后的第三方声明都存在；`worker/bootstrap.py --check` 达到 `ready`。禁止用空文件、假 vendor、假许可证或首次运行下载“通过”门禁。
2. **卡图授权**：公开卡图的再分发授权未确认前，`distribution_status` 保持 `blocked_unlicensed`；不得声明 `licensed_public_release`，也不得公开分发卡图。ONNX 权重的来源和分发权利需单独核验。
3. **宿主验收**：formal apply 需要真实报告包含 `plugin_loaded=true`、`delivery.submitted=true`、`sdk.accepted=true`，且 `source_manifest`/`artifact` fingerprint 与当前源一致；两个 dev 副本需先同步。`scripts/host_acceptance.py` 当前的 `neko-host-acceptance/1` 报告不含这些字段，**不能直接作为 formal 证据**。
4. **UI 实机渲染**：`ui/panel.tsx` 目前只做过静态检查；宿主渲染和动作路由未验证前不能发布正式版。
5. **版本**：当前清单版本为 `6.0.1`；正式同步脚本不会越过验收证据修改通道身份或写入正式目录。

在这些条件满足前，只能使用 dev 通道做只读/模拟验证，不执行 formal apply。

## 许可与第三方

- 本仓库代码按根目录 `LICENSE` 分发：GNU Affero General Public License v3.0（AGPL-3.0）。`LICENSE` 是 AGPLv3 全文；`ui/panel.tsx` 文件头标注 `SPDX-License-Identifier: AGPL-3.0-only`。
- 卡组代码格式参考 [PJ1059/KARDS-Deck-Tracker](https://github.com/PJ1059/KARDS-Deck-Tracker)（GPL-3.0，Copyright (C) 2026 PJ1059），本项目为独立重写，按 AGPL-3.0 分发；仅参考格式与公开行为，不复制其实现。
- 架构参考：[CN-Zephyr/n.e.k.o_plugin_neko_warthunder](https://github.com/CN-Zephyr/n.e.k.o_plugin_neko_warthunder)。该仓库是 [TL0SR2/project-N-E-K-O-Warthunder-8111-data-plugin](https://github.com/TL0SR2/project-N-E-K-O-Warthunder-8111-data-plugin) 的 fork，截至 2026-10-18 页面中未看到 LICENSE 文件。本项目的 README 只把它作为事件/场景/仲裁架构参考，不据此声称其许可证，也不并入其 WarThunder 遥测或业务代码。
- KARDS 的名称与卡图权利归其权利人；本插件与 KARDS 发行方无隶属、赞助或授权关系。
- `THIRD_PARTY_NOTICES.md` 仍是 `BLOCKED_PENDING_BUILD` 模板；离线包发布前必须逐项核验版本、许可证文本、版权行和来源，不能补写猜测值。第三方组件的许可证声明以构建时核验结果为准，本文不替任何组件作推断。

更多接口与部署细节见 `docs/backend-integration-contract.md`、`docs/backend-parity.md`、`docs/deployment.md`、`docs/offline-bundle.md`、`docs/card-assets.md`、`docs/host-acceptance.md`、`docs/contracts-yolo-v1.md`。
