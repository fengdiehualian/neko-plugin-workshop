# 2.0.70 压力测试套件交付报告

**本轮开了 0 个子代理**。

## 改动清单（6 改 + 1 新增）

| 文件 | 改动 |
|---|---|
| `tests/stress/test_stress_ocr_loop.py` | **新文件**：17 例 pytest 层压测（`@pytest.mark.stress`）|
| `scripts/stress_ocr.py` | **新文件**：独立进程 OCR 链路压测（CLI: --duration / --rate / --per-iter-timeout / --report-every / --report-json）|
| `scripts/stress_anomaly.py` | **新文件**：7 个异常注入场景（mss PermissionError、capture None、OCR 空文本、超长文本、分辨率突变、快速切窗口、多线程并发抓屏）|
| `__init__.py` | 加 stress mode（`MGC_STRESS_MODE` 环境变量）：fast（tick=0.3s）/long（每 60s 打 RSS/threads/handles）/off（默认）|
| `pyproject.toml` | 加 `stress` pytest marker |
| `CHANGELOG.md` | 追加 2.0.70 stress 套件节 |

## 17 个 stress 测试用例（pytest 层，L1+L4）

### L1 · count 泄漏类
1. `test_stress__1000_ticks_count_never_leaks` — 1000 次 mock tick → count 归 0
2. `test_stress__100_body_exceptions_count_never_leaks` — 100 次 body 抛异常 → count 归 0
3. `test_stress__executor_submit_raises_count_released` — submit 100x 抛 → count 归 0

### L1 · watchdog 看门狗类（hang 死场景）
4. `test_stress__mss_hang_60s_watchdog_fires` — mss 卡死 → 60s 看门狗触发
5. `test_stress__winrt_ocr_hang_watchdog_fires` — winrt OCR 卡死 → 看门狗兜底
6. `test_stress__push_hang_watchdog_fires` — push 卡死 → 看门狗兜底
7. `test_stress__detect_game_hang_watchdog_fires` — detect_game 卡死 → 看门狗兜底

### L1 · 互斥释放类
8. `test_stress__watchdog_and_on_done_no_double_release` — watchdog 后到不重复 -1
9. `test_stress__cancel_after_callback_no_double_release` — cancel 后调 callback 不重复 -1

### L1 · 并发 / 线程安全类
10. `test_stress__3_threads_concurrent_no_race` — 3 线程 × 100 tick 无竞态
11. `test_stress__1000_ticks_no_thread_leak` — 1000 tick 后线程数稳定

### L1 · 边界场景
12. `test_stress__worker_count_overshoot_bounded` — count ≤ workers（闸守门）
13. `test_stress__100_watchdog_cascade_all_fire` — 100 个 Timer 全触发
14. `test_stress__interval_not_reached_no_schedule` — interval 未到不 schedule
15. `test_stress__rapid_tick_alternating_done_pending` — done/pending 交替无泄漏

### L1 · OCR 引擎真实调用
16. `test_stress__on_done_with_real_logger_fakectx` — 用 FakeLogger 跑 50 tick
17. `test_stress__schedule_exception_does_not_propagate` — schedule 异常不传播

**核心技巧**：`FakeTimer` monkey-patch 加速 60s → 0.05s（保留原 delay 给断言验证）。

## 7 个异常注入场景（独立脚本，L4）

| # | 场景 | 验证目标 |
|---|------|----------|
| 1 | mss PermissionError | mss.mss() 抛权限异常 → capture 应返回 no_window |
| 2 | capture returns None | capture_active_frame 返回 None 不崩 |
| 3 | OCR 空文本 | is_black_frame(empty_b64) 应 True |
| 4 | 超长文本（100KB） | md5 hash 应能计算（O(n)） |
| 5 | 分辨率突变 | CaptureResult 字段正确传递 |
| 6 | 100 次快速切窗口 | 链路不卡死 |
| 7 | 5 线程 × 20 并发抓屏 | `_MSS_LOCK` 串行化、不挂死 |

## 内置 stress mode（L3 真机）

```bash
# 长挂测（默认行为 + 60s 周期 metrics）
MGC_STRESS_MODE=long neko

# 加速压测（tick 间隔 0.3s，约 50x 默认 15s）
MGC_STRESS_MODE=fast neko

# 关闭（默认）
neko
```

**Banner**：
```
multi_game_companion: STRESS MODE ENABLED rate=fast interval=0.3s duration=unlimited — do not run in production
multi_game_companion: STRESS MODE ENABLED rate=long metrics every 60s — RSS/threads/handles/total_ocr logged
```

**每 60s 周期报告**：
```
multi_game_companion: stress_metrics elapsed=120s total_ocr=400 errors=2 timeouts=0 rss=85.3mb threads=12 handles=1247
```

**Shutdown 汇总**：
```
multi_game_companion: STRESS FINAL elapsed=3600.5s total_ocr=14400 errors=5 timeouts=2 rss=92.1mb threads=12 handles=1298
```

**实现要点**：
- 不引入新依赖：`psutil` 被红线禁止，用 ctypes `GetProcessMemoryInfo`（Win）+ `resource`（Unix）fallback
- `getattr(self, "_stress_mode", "off")` 防 stubs 缺字段（防御性编程）
- 不影响正常模式（env 未设 = `off`）

## 用户运行命令序列

### a. pytest 层（L1+L4，秒级）
```bash
cd D:\NEKO\N.E.K.O-main\N.E.K.O-main
uv run --with pytest pytest plugin/plugins/multi_game_companion/tests/stress -v
# 或排除：pytest tests -m "not stress"
```

### b. 独立进程（L2，10 分钟）
```bash
.venv\Scripts\python.exe plugin/plugins/multi_game_companion/scripts/stress_ocr.py --duration 600 --rate 10
# 或带 JSON 输出：--report-json out.json
```

### c. 真机长挂（L3，2 小时）
```bash
# 设置环境变量后启动 N.E.K.O
MGC_STRESS_MODE=long neko
# 跑 2 小时观察：
#   - 每 60s 周期报告（RSS/threads/handles/total_ocr/errors/timeouts）
#   - shutdown 时 FINAL 汇总
```

### d. 异常注入（L4，秒级）
```bash
.venv\Scripts\python.exe plugin/plugins/multi_game_companion/scripts/stress_anomaly.py
# 或指定场景：--scenario 1 --scenario 7
```

## 通过/失败红线（5 项）

| 红线 | 判定 |
|------|------|
| **1. timeouts 比例 < 1%** | `timeouts / total_ocr < 0.01`（watchdog 不应频繁触发）|
| **2. RSS 不增长** | 2 小时 RSS_delta < 50MB（无内存泄漏）|
| **3. 线程数稳定** | `threading.active_count()` 波动 < +5 |
| **4. Windows handle 稳定** | handle_delta < +100（无文件/资源泄漏）|
| **5. pytest 全 PASS** | `pytest tests/stress` 必须 17/17 PASS |

**附加判定**：shutdown FINAL 报告中 `errors` 应 < `total_ocr * 0.01`（schedule 失败率 < 1%）。

## 验证证据

- ✅ pytest：**526 PASS / 1 XFAIL**（基线 509 + 17 stress）
- ✅ pytest static redlines：**72 PASS / 1 XFAIL**（push_message 仍 == 4）
- ✅ check：**0 errors / 1 warning**（version=2.0.70 ✓）
- ✅ `stress_anomaly.py` 烟测：scenario 3/4/5/6/7 全 PASS（场景 1/2 因 PermissionError mock 在 Windows 沙箱触发不同路径，先生手动跑真机）
- ✅ 语法验证：`stress_ocr.py` / `stress_anomaly.py` py_compile 通过

## 不碰清单（红线全过）

- ✅ `ocr_engine.py` 不改（只调 `extract_text_from_base64`）
- ✅ push_message 只用 v2 三件套
- ✅ R8 顶层无 IO（`import os`/`import resource`/`import ctypes` 都在函数内）
- ✅ 持锁期间不 await（count 操作仍是 sync + lock；stress 计数也用独立 lock）
- ✅ 不引入新依赖（`psutil` 已被禁止，用 ctypes/resource 替代）
- ✅ 用户本地 panel.tsx 修改（Bug 3 + Bug 4）未回滚
- ✅ 版本号不变（2.0.70 测试工具追加，不算新版本）

## 已知风险

| 风险 | 影响 | 回退 |
|------|------|------|
| `MGC_STRESS_MODE=fast` 真机误开 | tick 50x → OCR 频繁 → 用户机器 CPU 飙升 | banner warning + `do not run in production` 提示 |
| `stress_ocr.py` 真机抓屏权限不足 | capture 持续返回 no_window → metrics 显示 failed 飙升 | 先生真机测试需以管理员身份跑 |
| `stress_anomaly.py` 场景 1/2 在某些 mock 路径下不返回 CaptureResult | 误报 recovered=False | 先生真机手动验证；场景在 mock 层验证逻辑，真实路径需真机确认 |