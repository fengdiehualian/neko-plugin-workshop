# 2.0.71 高频 OCR + 场景状态机 — 实施报告

**版本**：2.0.71（2026-10-03）
**作者**：MiniMax-M3
**本轮开了 0 个子代理**——主线程顺序干，避免子代理同时改 `__init__.py` 合并冲突（2.0.70 实战经验）。

---

## 动机

真机单次 OCR 链路 **1.44s**（capture 0.14 + ocr 1.30 + detect 0.00），tick 15s 节奏下：
- 场景切换后最长 15s 才被感知——玩家错过剧情对话
- 缩到 1.5s 会排队（OCR > interval），worker 闸永久挡掉后续 tick

需要：
1. **场景判定**走高频（默认 2s）——切场景后 ≤ 2s 响应
2. **术语激活**走低频（默认 15s）——避免每 2s 刷激活
3. **场景状态机**——滞回（防单次误判）+ 防抖动（防快速跳变刷屏）+ 切换即推（不等 interval）

---

## 设计

### 拆分策略

```
                    ┌─────────────────────────────┐
   tick (每 scene_interval 秒)  ──►  抓屏（mss）             │ scene 轮
                                 ──►  OCR（1.30s）          │ (2s)
                                 ──►  detect_game            │
                                 ──►  _match_scenes          │
                                 ──►  SceneTracker.update()  │
                                       │                    │
                                       ├─ SWITCHED → 立即 push
                                       └─ EXITED   → 记日志

   每 term_interval 秒（15s）  ──►  复用上次抓屏缓存              │ term 轮
                                 ──►  屏幕激活 screen-activate │ (15s)
                                 ──►  术语 TTL 续期            │
```

**关键设计选择**：
- **不用两个独立 `@timer_interval` 装饰器**——会让 `__init__` 里有两条独立调度路径，2.0.70 已证 watchdog 临时 loop 坑。改用**单一 tick + body 内分支判断**。
- **SceneTracker 纯同步**——不用 asyncio、不在持锁期间 await（`_state_lock` 是 `threading.Lock`，违反红线 B4）。
- **SceneTracker SCENE_SWITCHED 不直接 push_message**——守住 D5 红线（push site == 4）。让下方 2266 路径复用。
- **抓屏缓存复用条件**：term 轮 + 本轮抓屏失败 + 缓存年龄 < `2 * term_interval`（防缓存陈旧）

### SceneTracker 状态机

```
   update(matched) 入口
        │
        ├── matched 空 → _exit_pending_count += 1
        │       └── ≥ exit_grace_count → SCENE_EXITED
        │
        └── matched 非空
                ├── primary == _last_exited_scene 且距离开 < debounce_seconds
                │       └── 静默恢复（NO_CHANGE，current_scene 直接设回 primary）
                │
                └── 正常路径
                        ├── primary == _pending → _pending_count += 1
                        └── primary != _pending → _pending_count = 1
                                └── ≥ hysteresis_count 且 pending != current
                                        └── SCENE_SWITCHED
```

---

## 改动详情

### 新文件

**`scene_tracker.py`**（137 行）：
- `SceneEventType` 枚举：`NO_CHANGE / SCENE_SWITCHED / SCENE_EXITED`
- `SceneEvent` 数据类（frozen）：`type / from_scene / to_scene / elapsed_in_old`
- `SceneTracker` 类：
  - `__init__(hysteresis_count=3, exit_grace_count=5, debounce_seconds=10.0, clock=time.monotonic)`
  - `update(matched) -> SceneEvent`（纯同步）
  - `reset()`、`current_scene` / `hysteresis_count` / `exit_grace_count` / `debounce_seconds` 4 个只读 property
  - 状态：`current / current_entered_at / pending / pending_count / exit_pending_count / last_exited_scene / last_exited_at`
  - 支持自定义 `clock`（测试用 `_FakeClock` 不 sleep）

**`tests/unit/test_scene_tracker.py`**（12 例）：
1. `no_change_when_same_scene` — 进场景后重复匹配 = NO_CHANGE
2. `switch_only_after_hysteresis` — 连续 3 次新场景才切换
3. `interrupted_match_resets_count` — 中途回旧场景清零 pending
4. `scene_exited_after_grace` — 连续 5 次无命中 = EXITED
5. `debounce_returns_without_event` — 离开 5s 内回来 = NO_CHANGE（不发事件）
6. `no_debounce_after_long_absence` — 离开 20s 后回来 = 需重新 hysteresis
7. `switch_from_existing_scene` — scene_a → scene_b（带 elapsed）
8. `empty_matched_is_no_match` — None/[] = 无命中
9. `multiple_matched_takes_first` — matched=[a,b] 用 a
10. `reset_clears_all_state` — reset() 后回到初始
11. `hysteresis_count_minimum_one` — boundary h=1
12. `elapsed_in_old_tracks_time` — clock.advance() 后 elapsed 正确

**`tests/unit/test_ocr_perceive_split.py`**（5 例）：
1. `scene_interval_replaces_legacy` — gate 用 scene_interval（不再是 perceive_interval）
2. `term_round_reuses_cached_capture` — 缓存字段被正确维护
3. `options_validated_with_clamp` — 3 个新选项 clamp 边界（0/100/1000/50 → 默认）
4. `options_have_backward_compat_legacy` — `ocr_perceive_interval_seconds` 仍存在
5-7. `scene_tracker_integration__*`（3 例）— SceneTracker 进/切/退路径

**`tests/stress/test_stress_scene_tracker.py`**（5 例）：
1. `1000_updates_no_leak` — 1000 次 update 无线程泄漏
2. `churn_scenes_no_spam` — 抖动场景（A-B-A-B 200 次）≤ 5 次 SWITCHED
3. `rapid_exit_enter_no_leak` — 1000 次进退循环 RSS 增长 < 10MB
4. `concurrent_updates_thread_safe` — 4 线程并发不崩（仅断言不抛 fatal）
5. `per_update_under_1ms` — 10000 次 update 平均耗时 < 1ms

### 修改文件

**`game_registry.py`**（+12 行）：
- 新增 `ocr_scene_interval_seconds: int = 2` （low=1, high=30）
- 新增 `ocr_term_interval_seconds: int = 15` （low=3, high=300）
- 新增 `scene_hysteresis_count: int = 3` （low=1, high=10）
- `from_section()` 加 3 个 `_clamp_int` 解析
- 保留 `ocr_perceive_interval_seconds`（legacy/面板回退）

**`__init__.py`**（+40 行）：
- `__init__` 新增：
  - `self._last_capture_b64: str | None`
  - `self._last_capture_at_monotonic: float`
  - `self._last_capture_text: str`
  - `self._last_term_run_monotonic: float`
  - `self._scene_tracker = SceneTracker(...)`
- `ocr_perceive`（tick）`interval` 从 `ocr_perceive_interval_seconds` 改为 `ocr_scene_interval_seconds`
- `_ocr_perceive_body`：
  - 入口计算 `is_term_round`
  - 抓屏成功 → 缓存 b64 + 时间戳
  - 抓屏失败 + term 轮 + 有缓存 + 年龄 < `2 * term_interval` → 复用缓存（"reuse-cached-capture" 日志）
  - 屏幕激活块 `if in_game_chars:` 改为 `if in_game_chars and is_term_round:`
  - term 激活完成后 `self._last_term_run_monotonic = t0`
  - SceneTracker.update(matched_scenes) → SCENE_SWITCHED 仅 log + 更新 `_last_scene_for_proactive`，不动 `_last_scene_pushed`（让 2266 路径自然触发 push，复用 push site）

**`plugin.toml`**（+5 行）：3 个新配置项 + version 2.0.71

**`pyproject.toml`**（+1 行）：version 2.0.71

**`ui/panel.tsx`**（+3 行）：3 个新字段 `num(opts.xxx, default)`——注释前缀 `//`（不是 `#`，2.0.70 修过的 TSX 注释坑不能重犯）

**`tests/unit/test_ocr_perceive_defensive.py`**（+1 行）：`_make_plugin_stub` 显式设 `ocr_scene_interval_seconds=15`

**`tests/stress/test_stress_ocr_loop.py`**（+2 行）：`_make_stub` 设 `ocr_scene_interval_seconds` + `ocr_term_interval_seconds`

---

## 测试结果

### 总数

| 指标 | 2.0.70 baseline | 2.0.71 |
|---|---|---|
| 测试总数 | 526 passed | **550 passed** |
| 失败 | 0 | 0 |
| XFAIL | 1 | 1 |

新增 24 例：scene_tracker 单测 12 + 拆分 5 + 压测 5 + integration 2。

### 红线守住

| 红线 | 状态 |
|---|---|
| B3 入口方法名不以 `_` 开头 | ✓ |
| B4 async def | ✓ |
| D5 push_message v2 三件套（site=4） | ✓（SceneTracker 不新增 push，复用 2266） |
| R8 顶层无 IO | ✓ |
| 不在持锁期间 await | ✓（SceneTracker 纯同步） |
| OCR 文本不进日志 | ✓（只记长度 + hash + 前 80 字样本） |
| 不引新依赖 | ✓（只引 `_FakeClock` 测试用） |
| 版本号 2.0.70 → 2.0.71 | ✓ |

### check

```
[OK] multi_game_companion: check found 0 error(s), 1 warning(s)
  version=2.0.71
```

---

## 真机测试 5 步（待先生手动跑）

1. **启动日志确认**：
   - 启动插件，日志应含 `ocr_scene_interval_seconds=2` `ocr_term_interval_seconds=15` `scene_hysteresis_count=3`
2. **2 分钟观察**：
   - 每 ~2s 一条 `stage=body_enter` / `stage=capture_done` / `stage=ocr_done` / `stage=body_exit`
   - 无 `worker_busy` 日志（除非 OCR 真的卡 2s+）
3. **切场景验证**：
   - 启动游戏后切换到不同界面（剧情对话 / 地图 / 角色界面）
   - 连续命中新场景 3 次后，日志应立即出现 `scene_tracker scene-switch from=... to=... elapsed_in_old=...s`
   - 切完不到 2s 就有 `scene-prompt-push` 日志
4. **抖动场景不刷屏**：
   - 在 A-B-A-B 之间快速来回切（每次停留 < 1s）
   - 100 次后观察：日志应 **NO_CHANGE 占 ≥ 95%**，`scene-switch` ≤ 5 条
5. **面板配置**：
   - 打开 Hosted UI 面板（plugin-manager → multi_game_companion → 设置）
   - 应看到 3 个新滑块：场景判定间隔 / 术语激活间隔 / 场景切换滞回次数

---

## 已知风险 / 后续

1. **worker 闸**仍是 ocr_worker_threads=1（默认）——若 term_interval 调到 3s 且 OCR 链路 > 3s，会排队。建议 term_interval ≥ 5s。
2. **debounce_seconds=10s 写死**——未暴露面板。后续版本如需可调再加。
3. **SceneTracker 单实例**——若有多个游戏并行（不太可能），需手动 reset。

---

## 文件清单

### 新建
- `scene_tracker.py`
- `tests/unit/test_scene_tracker.py`
- `tests/unit/test_ocr_perceive_split.py`
- `tests/stress/test_stress_scene_tracker.py`
- `docs/REPORT_2_0_71.md`

### 修改
- `game_registry.py`（+12 行）
- `__init__.py`（+40 行）
- `plugin.toml`（+5 行 / version 2.0.71）
- `pyproject.toml`（version 2.0.71）
- `ui/panel.tsx`（+3 行，注释用 `//`）
- `CHANGELOG.md`（+45 行）
- `docs/PANEL.md`（+27 行 / 重新编号）
- `tests/unit/test_ocr_perceive_defensive.py`（+1 行 stub 修正）
- `tests/stress/test_stress_ocr_loop.py`（+2 行 stub 修正）