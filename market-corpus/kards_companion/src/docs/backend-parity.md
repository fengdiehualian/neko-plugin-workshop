# KARDS 后端行为 Parity（历史本地证据版）

> 下文为 2026-10-18 的历史测试快照，包含已移除的 OCR、旧模型和录制描述，不可作为当前能力或验收通过证明。当前纯 YOLO 候选版的信息整合、read/respond 和战雷差异见 [信息传递说明](message-integration.md)，6.0.1 收口背景见 [第一版说明](v1-closure.md)。

- 日期：2026-10-18
- 参考基线：`7ef37e232117237215ed1b9a8e4add4eef1d4322`（仅接口/行为对标）
- 契约：`docs/backend-integration-contract.md`
- 证据级别：**本地 Python 3.11 单元/集成测试通过**。本文不声称已完成真实 host、
  真实 KARDS 对局或真实语音/TTS 验证。host 验收仍需单独执行。

状态：`已实现-本地测试` / `部分-待实机` / `未实现` / `待 host 验收`。

## 1. 共享契约

| ID | 目标 | 证据 | 状态 |
| --- | --- | --- | --- |
| CON-01 | EventSpec 全目录；HQ 敌我差异；terminal 严格 TTL | `test_event_contracts.py` | 已实现-本地测试 |
| CON-02 | 六场景 + `category_allowed`；OUT 仅 terminal | 同上 | 已实现-本地测试 |
| CON-03 | 十段 identity（session/game/kind/side/entity/turn/band/edge/occurrence/episode） | 同上 | 已实现-本地测试 |
| CON-04 | level/edge；未知 side 不推 critical | 同上 | 已实现-本地测试 |
| CON-05 | 纯替换 normalize、canonical priority、幂等 | 同上 | 已实现-本地测试 |

## 2. A：场景与 tracker

| ID | 目标 | 证据 | 状态 |
| --- | --- | --- | --- |
| A-01 | `KardsScenarioResolver.update/reset/snapshot`；危机优先；opening/pressure/hysteresis | `test_scenario.py` | 已实现-本地测试 |
| A-02 | `observe_scene` pre-gate；`context_valid/needs_rebase/confirmed_exit`；unknown/draft 不信任 | 同上 | 已实现-本地测试 |
| A-03 | tracker 公共 `reset_session/reset_match/rebase/mark_delivered/rearm_uncommitted/set_session_id/snapshot`；adapter 转发 | `test_tracker.py`、`test_detector_registry.py` | 已实现-本地测试 |
| A-04 | entity/turn/band/occurrence/episode 源头补齐；单位首现 2 帧/消失 3 帧；HQ 两帧确认 | 同上 | 已实现-本地测试 |
| A-05 | delivered 账本；rearm 只重评仍成立条件事件，不刷新 ts、不重放离散事件 | 同上 | 已实现-本地测试 |
| A-06 | once/recovery；menu/draft/unknown 不污染上一 match prev；terminal 仍 close | 同上 | 已实现-本地测试 |
| A-07 | 主类直接使用 `MatchTracker`，已删除私有 rebase 子类 | `test_lifecycle_contract.py` 等 | 已实现-本地测试 |

## 3. B：仲裁与输出安全

| ID | 目标 | 证据 | 状态 |
| --- | --- | --- | --- |
| B-01 | `decide(..., scenario=)`；scenario=None 按 IN_MATCH 门控 | `test_arbiter.py` | 已实现-本地测试 |
| B-02 | `Decision.retry_after` | 同上 | 已实现-本地测试 |
| B-03 | `select` 稳定选择；纯选择不写账本；同刻新事件优先 | 同上 | 已实现-本地测试 |
| B-04 | `OutputSafetyGuard`：manual/auto pause、5/60/12/5、critical 独立时钟；terminal 只绕节奏 | `test_output_safety.py` | 已实现-本地测试 |
| B-05 | snapshot/checkpoint/restore/`remaining()`/fork | 同上 | 已实现-本地测试 |
| B-06 | terminal 不绕 consent/dry/stop/pause；一次性由 session 账本控制 | `test_arbiter.py`、`test_dispatcher.py` | 已实现-本地测试 |

## 4. C：投递与 gate

| ID | 目标 | 证据 | 状态 |
| --- | --- | --- | --- |
| C-01 | 单 pending；每 tick 至多一次最终尝试；TTL 不刷新 | `test_dispatcher.py` | 已实现-本地测试 |
| C-02 | read/respond/context/expire/test 全部经 `HostPushGate` | `test_publisher.py` | 已实现-本地测试 |
| C-03 | dry-run 完整模拟仲裁/安全，最终 no-op，不 mark 真实账本 | `test_publisher.py`、`test_dispatcher.py` | 已实现-本地测试 |
| C-04 | consent=false 对全部 intent（含 expire/test）零真实 push | 同上 | 已实现-本地测试 |
| C-05 | test 需 `authorized_real_test`、非 dry、consent true、gate running；一次性授权 | 同上 | 已实现-本地测试 |
| C-06 | stop：先关 admission，begin_stop 锁外；旧 token 跨 stop/reopen 被拒绝 | `test_lifecycle_contract.py`、`test_publisher.py` | 已实现-本地测试 |
| C-07 | 配置变更 purge pending 并同步 gate | `test_dispatcher.py` | 已实现-本地测试 |
| C-08 | confirmed 后 `on_delivered` 一次；异常不重发 | 同上 | 已实现-本地测试 |
| C-09 | simulated_records 仅结构化摘要 | `test_publisher.py` | 已实现-本地测试 |
| C-10 | target 清洗/路由；空值不伪造；多目标不确定时拒绝 | `test_target.py` | 已实现-本地测试 |

回执类别：`accepted` / `rejected` / `legacy_unconfirmed` / `unknown` / `simulated`。
只有显式 `accepted` 才是 submitted；`None` 旧回执不算接受；`unknown` 不自动重试。
该回执仅是 **SDK 本地提交**，不代表宿主 LLM 生成、TTS 播放或用户实际听到。

## 5. 最终集成（main）

| ID | 目标 | 证据 | 状态 |
| --- | --- | --- | --- |
| INT-01 | 场景 pre-gate → tracker/detector → 每 tick 一次 scenario.update → dispatcher tick → gate | 三份集成 tests | 已实现-本地测试 |
| INT-02 | tick 入口绑定 gate token；主 lifecycle 锁不跨 host push；stop begin_stop 线程执行 | `test_lifecycle_contract.py` | 已实现-本地测试 |
| INT-03 | 不可信帧不跑 OCR/tracker；重复 untrusted 用同一 tick token 重试 expire；首帧 purge | `test_api_contract.py`、`test_lifecycle_contract.py` | 已实现-本地测试 |
| INT-04 | unknown/menu/terminal 锁外 purge + expire；confirmed exit `reset_match` 保留局号 | `test_lifecycle_contract.py` | 已实现-本地测试 |
| INT-05 | consent/dry 统一工具门；blocked 不返回 recent_events/deck/state；config 输出 allowlist | `test_api_contract.py` | 已实现-本地测试 |
| INT-06 | `context_valid` 同时校验 monitoring/paused/trusted snapshot/scene/session/age | 同上 | 已实现-本地测试 |
| INT-07 | target_set / monitor_pause / monitor_resume / monitor_test_say；test 复用 prompt 边界与 metadata | 同上 | 已实现-本地测试 |
| INT-08 | 持久化 future 收尾、失败日志、shutdown flush | `test_lifecycle_contract.py` | 已实现-本地测试
| INT-08b | gate reopen 失败时按 start epoch/session 回滚，不创建 capture/tasks，entry 返回失败 | `test_lifecycle_contract.py` | 已实现-本地测试 |
| INT-09 | manifest 6.0.0、显式 auto_start_monitoring、quiet 60/30/20、critical 5、catalog 文件未改 | `plugin.toml`、全量 tests | 已实现-本地测试 |
| INT-10 | UI 使用 `props.useLocalState`、`{result}`/错误 envelope、仅展示真实字段 | `ui/panel.tsx`（静态检查） | 已实现-待 host 渲染 |

**本地验证命令**：`plugin-py311\.venv\Scripts\python.exe -m pytest tests -q`
→ `345 passed`（Python 3.11）。未启动 host、未启动 KARDS/Steam、未改 runtime overrides。

## 6. 明确未完成 / 未验证

- 真实 host 插件加载、OpenAPI 动作路由、`push_message` 实际回执形态、真实语音播放：**待 host 验收**。
- HUD OCR 真实对局稳定性：**待实机**。
- 单位/前线识别：依赖可选 `models/kards_det.onnx`，**本包未附带**；缺失时单位事件静默。
- 卡名匹配链：**未接通**；导入卡组代码只保存卡组，不代表能识别卡名。
- 指令牌环形缓冲：代码存在，**稳定识别/放大卡面未完成**。
- 遮挡检测：采集核心返回的 `occluded=False` 不能证明未遮挡；状态面公开 `occluded=None` +
  `occlusion_detection="unavailable"`，UI 不显示“未遮挡”。
- 协议/宿主未证实的能力写为 unknown，不写成“已生效”或“未生效”断言。
