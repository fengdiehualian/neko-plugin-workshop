# KARDS 后端跨 Agent 集成契约（冻结）

- 日期：2026-10-17
- 参考基线：`7ef37e232117237215ed1b9a8e4add4eef1d4322`（仅接口/行为对标，不复制源码）
- 共享实现：`runtime/contracts.py`
- 修改需同时更新本文件、`runtime/contracts.py` 和所有权内测试。

模块归属：

- A：`kards_companion.state.tracker`（兼容导入；可在 `runtime/tracker.py` 重导出/扩展）与 `runtime` 内场景解析器。
- B：`kards_companion.delivery.arbiter` 的 `Decision/Selection/CommentaryArbiter`；`kards_companion.policy.safety` 的 `OutputSafetyGuard/OutputSafetyConfig`。
- C：`kards_companion.delivery.contracts` 的 `HostPushGate/HostPushConfig/GateResult`；`policy.dispatcher` 的 `DeliveryDispatcher`；`delivery.publisher` 的 `LiveStatePublisher`。

---

## 1. 共享事件契约 `runtime/contracts.py`

导出：`EventSpec`、`event_spec`、`event_level`、`event_edge`、`event_identity`、`event_max_age_seconds`、`category_allowed`、`normalize_event`；场景常量 `OUT_OF_BATTLE/OPENING/IN_MATCH/PRESSURE/CRITICAL/ENDED`；类别、等级、方向常量及 `SCENARIOS/CATEGORIES/LEVELS/EDGES`。

`EventSpec` 字段固定：

```python
EventSpec(kind, category, priority, cooldown_seconds, max_age_seconds,
          preempt, terminal, once_per_match, wants_recovery)
```

目录与 side 覆盖：

| kind | 规范值 |
| --- | --- |
| `match_start` | opening / 5 / maxAge 4 / once true / wants_recovery false |
| `turn_switch` | state / 4（默认可参与）/ maxAge 6 |
| `hq_damage` | pressure/5；我方 7；敌方 battle/5；**永不 preempt**；maxAge 6 |
| `hq_critical` 我方确认 | critical/8/preempt true；enter 或 upgrade；maxAge 6 |
| `hq_critical` 敌方确认 | battle/6/warning；不 preempt |
| `hq_critical` side 未知 | pressure/6/warning；**不得推导 critical** |
| `hq_critical` recovery | recovery/4/notice；低优先；不 preempt |
| `match_end` | terminal / 9 / maxAge 8 / terminal true / preempt false / once true / wants_recovery false |
| `unit_*`、`order_card_seen` | battle / 4–6 / maxAge 4–6 |
| `chatter` | chatter / 5 / maxAge 6 |

只有**已确认我方**的 HQ 危机可以 `preempt=True`。`preempt` 是 dispatcher 替换 pending 的唯一依据；`match_end` 明确不 preempt。

`event_level`：recovery 先于 `details.level`，固定 `notice`；其余情况 `details.level` 优先；仅确认我方 HQ 危机为 `critical`；敌方/未知 side 最高 `warning`；terminal 为 `terminal`。

`event_edge` 是战雷式条件边沿，不是“玩家收益方向”：

- `enter`：条件首次成立（开局、单位登场、我方 HQ 危机 band 1）；
- `upgrade`：条件升级（危机 band 2、单位推进）；
- `recovery`：条件解除（HQ 回到阈值以上）；
- `steady`：离散/无状态变化事件（掉血、阵亡、回合、chatter、终局）。

显式 `details.edge` 必须是上述四值之一；不做广泛别名。

`event_identity` 固定十段：

```text
session|game|kind|side|entity|turn|band|edge|occurrence|episode
```

缺失为 `-`。`entity` 只接受稳定 id（`entity/entity_id/unit_id/instance_id`），不得用描述文本。`occurrence` 区分同回合真实发生的两次同类事件；`episode` 标识持续条件的一次生命周期。二者必须由 tracker 在源头写入，否则 identity 会错误合并事件。

`event_max_age_seconds`：普通与 terminal 一律 `min(spec.max_age, configured)`；`configured <= 0` 用 spec；不做终局补报，不存在 60 秒 terminal 特例。

`category_allowed`：

| 场景 | 允许类别 |
| --- | --- |
| `OUT_OF_BATTLE` | 仅 terminal（draft 不支持、unknown 不信任） |
| `OPENING` | opening/state/battle/pressure/**critical**/recovery/terminal |
| `IN_MATCH` | 全部类别 |
| `PRESSURE` | state/battle/pressure/critical/recovery/terminal |
| `CRITICAL` | pressure/critical/recovery/terminal |
| `ENDED` | 仅 terminal |

`category_allowed` 只是选择门，不是隐私/停止门。`normalize_event` 使用 `dataclasses.replace`：不修改输入，写入 session/category/level/edge/max_age/identity，并**改写为规范 priority**；幂等。

---

## 2. A：场景解析与 tracker

### 2.1 `KardsScenarioResolver`

```python
update(state, events=(), *, now=None) -> str
reset() -> None
snapshot() -> dict
```

判定与时间规则：

- 危机优先于开局；确认终局/离场 → `ENDED`。
- 开局最多持续 6 秒；有匹配事件/时间推进后进入 `IN_MATCH`，不得“无事件永远 opening”。
- 压力窗口 8 秒；压力/危机退出需连续 `recovery_hysteresis_seconds`（默认 3 秒）条件不成立，避免抖动。
- 瞬时 unknown/draft 不重新打开对局；`context_valid=False`，draft 不支持、unknown 不信任。
- `OUT_OF_BATTLE` 只放行 terminal；开局由 `OPENING` 表达；`OPENING` 允许 critical。
- `now`、`state.detected_at` 参与计时；无时间源时使用注入 `now_fn`，不得用事件缺失推断无限开局。

A 必须提供公开的分类确认/离场接口（例如 `confirm_exit(result)`、`invalidate(reason)`；具体名称由 A 冻结并在实现中导出），且 `snapshot()` 至少包含：

```python
{
  "scenario": str, "reason": str, "since": float | None,
  "updated_at": float | None, "game_number": int,
  "context_valid": bool, "needs_rebase": bool, "confirmed_exit": bool,
}
```

### 2.2 `MatchTracker` 公共方法

```python
reset_session() -> None
rebase() -> None
reset_match() -> None
mark_delivered(event) -> None
rearm_uncommitted() -> list[GameEvent]
```

- `reset_session`：清 session/局/单位/危机/delivery 账本；保留实例、阈值、时钟。
- `rebase`：下一可信帧作 baseline；不产生 turn/HQ/单位 diff；终局仍可正常关闭。
- `reset_match`：只清当前局，保留 session 与累计局号。
- `mark_delivered`：按 `event_identity` 记有界 FIFO，不产生事件。
- `rearm_uncommitted`：只返回仍然成立的**条件事件**（`wants_recovery=True`），保留原 `ts`、未过期、不 refresh TTL；禁止重放旧 `match_start/match_end/unit_*` 等离散事件。允许新 episode 重新评估条件，但不得靠改 ts 复活旧事实。

tracker 源头必须补：`entity`（稳定单位 id）、`turn`、`band`、`occurrence`、`episode`；adapter 同步转发五个公共方法，`reset()` 等价 `reset_session()`。

---

## 3. B：仲裁与输出安全

`Decision(allowed, reason, retry_after=0.0)`；`Selection(event | None, decisions: list[Decision])`。

```python
decide(event, state, *, scenario=None, now=None) -> Decision
select(events, state, *, scenario, now=None) -> Selection
mark_submitted(event, *, now=None) -> None
```

- `select` 评估顺序：terminal → 已确认我方 critical/preempt → spec.priority 降序 → ts 降序（newest 优先） → 输入顺序；`decisions` 与输入顺序一一对应。
- `select` 纯选择：不写 submitted/cooldown 账本，不占等待槽；等待槽归 dispatcher。
- `mark_submitted` 是唯一提交点，只在宿主确认后调用。
- `scenario=None` 的旧 API 仍应用新规则：按 `IN_MATCH` 做类别门，允许 opening，不得退回旧版“开局禁言”。
- `retry_after` 仅表示时间型拒绝的剩余秒数，否则 0。

`OutputSafetyGuard` **不是第二个 breaker**，不做半开探测/自动恢复：

```python
OutputSafetyConfig(
    failure_threshold=5,
    failure_window_seconds=60.0,
    min_output_interval_seconds=12.0,
    critical_cooldown_seconds=5.0,
)
```

```python
pause(reason="", *, auto=False, now=None) -> None
resume(*, auto=False, now=None) -> None
allow(*, critical=False, terminal=False, now=None) -> Decision
remaining(*, now=None) -> float | None
mark_output(*, critical=False, terminal=False, now=None) -> None
record_failure(*, now=None) -> None
snapshot() -> dict
output_clock_checkpoint() -> dict
restore(checkpoint) -> None
reset() -> None
```

- `manual_paused` 与 `auto_paused` 分开；失败阈值只在 60 秒窗口内计数，达到 5 次置 `auto_paused`，必须显式 `resume(auto=True)`。
- normal 输出间隔 12 秒；critical 使用 5 秒冷却并跳过 normal 间隔；terminal **只跳过节奏**，不绕过 manual/auto pause、consent、dry_run 或 stop。
- terminal 一次性由 arbiter/session 的 delivered 账本控制，不是 guard 终生只允许一次。
- `remaining()`：暂停中返回 `None`；否则有限非负秒数。`snapshot()` JSON-safe，至少含 `manual_paused/auto_paused/pause_reason/failures/failure_window_started_at/remaining/last_output_at/last_critical_at`。
- `output_clock_checkpoint()/restore()` 明确保存和恢复：`last_output_at/last_critical_at/failure_count/failure_window_started_at/manual_paused/auto_paused/pause_reason`。

---

## 4. C：投递与 HostPushGate

### 4.1 `DeliveryDispatcher.tick`

```python
tick(candidates, state, *, scenario, session_id, match_id,
     target_lanlan=None, now=None) -> list[DispatchOutcome]
```

- 单一 pending；每 tick 至多一次宿主最终尝试；失败留到下一 tick。
- 内部必须使用 `arbiter.select/decide`；只有确认回执后 `mark_submitted` 并调用 `on_delivered`。
- `on_delivered(event, state, details)` 只成功一次；回调异常视为已接受，不重发。
- `submit/drain` 保留兼容，但生产路径均为 single-slot；旧无 scenario 调用也走新规则。
- `update_config` 含 `dry_run`、`llm_data_consent`、`target_lanlan`；任一变更立即 purge pending 并隔离旧 session，不把旧候选带入新配置。
- `set_context(scenario/session_id/match_id/target_lanlan)`、`set_state(state)`；tick 显式参数优先；空 target 不等于未提供。
- `simulated_records` 只存结构化摘要（kind/priority/reason/identity/timestamp），绝不存 prompt、OCR、图片或原始 kwargs。

### 4.2 统一 Gate

```python
HostPushGate(push_message, *, config=None, now_fn=time.time)
push(intent, *, critical=False, terminal=False, context_token=None, **kwargs) -> GateResult
begin_stop(reason) / finish_stop() / reopen() / update_config() / snapshot()
```

- intent：`read/respond/expire/context/test`；gate 是唯一真实 push 出口。
- `dry_run`：完整 tick/arbiter/safety 模拟，最终 push no-op，不 mark 真实账本；模拟 cooldown 独立；退出 dry-run 时 purge 并重新评估当前条件。
- `llm_data_consent=False`：**所有 intent（含 expire/test）零真实 push**。
- `test`：仅当 `authorized_real_test=True`、`dry_run=False`、`llm_data_consent=True` 且 gate 运行中，允许一次真实连接测试；不得强制走模拟。
- `terminal`：不绕过 manual/auto pause、consent、dry-run 或 stop。
- `begin_stop`：关闭 respond/read/context；expire 仅在 `dry_run=False` 且 consent true 时允许；**不再接受新 terminal**，只有已经开始、已进入宿主调用的请求可完成。
- `finish_stop`：全部关闭；`reopen` 产生新 epoch/context_token，旧 tick 跨 stop/start 必须被拒绝。
- gate 只在自身 commit 锁内实际 push。主 lifecycle lock 内禁止调用 gate；`begin_stop` 可能等待 in-flight，因此主流程必须**无锁 begin_stop → 等待/清理 → 再 reset**。
- 停止时序：持锁只改内存/关 admission → 放锁 → `begin_stop` 等待 in-flight → 允许已入宿主调用完成 → 必要时在锁外 expire → `finish_stop` → 再持锁 reset 内存。

`LiveStatePublisher.publish` 用 `intent="read"`，`expire` 用 `intent="expire"`；dry-run/拒绝不得报告 `SUBMITTED`。

---

## 5. 测试所有权

| 所有者 | 实现 | 独占测试 |
| --- | --- | --- |
| A | `runtime/tracker.py`（或 `state/tracker.py` 内 A 所有权）、场景解析 | `test_scenario.py`、`test_event_contracts.py`、`test_tracker.py`、`test_detector_registry.py` |
| B | `delivery/arbiter.py`、`policy/safety.py` | `test_arbiter.py`、`test_output_safety.py` |
| C | `adapters/*`、`delivery/contracts.py`、`delivery/publisher.py`、`policy/dispatcher.py` | `test_target.py`、`test_dispatcher.py`、`test_publisher.py` |
| 最终集成 | `__init__.py`、`plugin.toml`、`ui/*`、`README.md` | `test_api_contract.py`、`test_pipeline_contract.py`、`test_lifecycle_contract.py` |

生产 tick：A 产出事件/场景 → `normalize_event` → C `tick` → B `select/decide` + guard → gate → 确认后 `mark_submitted`/`on_delivered` → A `mark_delivered`。任何组件不得绕过 gate 直接调用宿主。
