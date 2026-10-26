# 2.0.70 — 止损 + 定位（核心 bug：2.0.69 watchdog 用了错误的机制）

**本轮开了 0 个子代理**（沿用主线程：1 改 game_registry 默认值、1 改 plugin.toml、1 改 panel.tsx、1 改 __init__.py 的 ocr_perceive + _ocr_perceive_body、2 改版本号、3 改测试 + 1 新建测试）。

## 改动清单（6 改 + 1 新增）

| 文件 | 改动 |
|------|------|
| `game_registry.py` | **第一步 · 砍 S1**：`change_driven_enabled` dataclass 字段默认 `True → False`；`_as_bool(...)` 默认参数 `True → False` |
| `plugin.toml` | **第一步**：显式 `change_driven_enabled = false` |
| `pyproject.toml` / `plugin.toml` | 2.0.69 → 2.0.70 |
| `ui/panel.tsx` | **第一步**：`change_driven_enabled` 默认 `true → false`（Switch 默认 OFF） |
| `__init__.py` | **第一步**：`change_driven_enabled` 字段不变，仅默认关。**第二步 · 段级打点**：`_ocr_perceive_body` 每个 await 前后打 `stage=xxx` + `dt=Xs` 日志。**第三步 · 真正的兜底**：`ocr_perceive` 改用 `self._ocr_executor.submit(fn)` 拿 `concurrent.futures.Future`（不被临时 loop 管）+ `threading.Timer(60, _watchdog)` 独立线程（不依赖任何 loop） |
| `CHANGELOG.md` | 追加 2.0.70 节 |
| `tests/unit/test_ocr_perceive_stage_timing.py` | **新**：5 例（body_enter/exit 日志位置 / capture_done dt= / ocr_done dt= / detect_done dt=+detected= / no_session 早退仍记 body_enter） |
| `tests/unit/test_ocr_worker_timeout.py` | **重写**：5 例全部改用 mock `_ocr_executor.submit` + FakeTimer 加速 60s→0.05s |
| `tests/unit/test_ocr_perceive_defensive.py` | **改 3 例**：原 asyncio.get_running_loop mocking 改 _ocr_executor.submit mocking |
| `tests/unit/test_game_registry.py` | **改 4 例**：change_driven_enabled 默认 False 同步更新 |

## 2.0.69 watchdog 失效根因（核心发现）

`@timer_interval(seconds=3)` 装饰器每 3s 用 `asyncio.run(ocr_perceive)` 跑 `ocr_perceive` 协程。`asyncio.run` 每次创建**临时事件循环**，循环在 `ocr_perceive` return 后立刻关闭。2.0.69 的：

```python
loop.call_later(60.0, _watchdog)  # 排到临时 loop——loop 关了回调永远不触发
future.add_done_callback(_on_done)  # 同样依赖临时 loop 调 call_soon
```

**这俩机制在 `@timer_interval` 下都不工作**——临时 loop 死后回调丢失。真机表现：watchdog 60s 看门狗从未触发，count 永久卡住，worker 闸静默挡掉后续所有 tick。

## 2.0.70 修复：换机制

1. **直接 submit**：`self._ocr_executor.submit(self._ocr_perceive_sync_entry)` 返回 `concurrent.futures.Future`，不被任何临时 loop 管
2. **`cf_future.add_done_callback(_on_done)`** 在 executor 的 worker 线程里跑回调，线程安全
3. **`threading.Timer(60.0, _watchdog)`** 独立线程，不被任何 loop 关闭影响——**绝对能触发**

## 关键学习

- **`asyncio.call_later` 不是"延迟触发"——是"在 loop 上注册回调"。loop 死了回调就丢了。**
- 真正的延迟触发用 `threading.Timer` 或 `concurrent.futures` 调度
- **`@timer_interval` 用 `asyncio.run` 每次创建临时 loop——任何基于 loop 的延迟回调都不工作**
- **段级打点比"加防御"重要**——不打点永远猜不到根因，只能猜

## 用户命令序列（先生手动跑）

```bash
cd D:\NEKO\N.E.K.O-main\N.E.K.O-main
uv run neko-plugin check multi_game_companion
uv run --with pytest pytest plugin/plugins/multi_game_companion/tests -q
Remove-Item -Recurse -Force 'plugin\plugins\multi_game_companion\.pytest-tmp'
uv run neko-plugin build multi_game_companion
# panel.tsx 有改动（仅默认 false 微调，安全）：
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/multi_game_companion
```

## 真机测试 3 步

a. **启动正常**：日志含 `config_source=...` + `change_driven_enabled=false`（plugin.toml 默认） + 启动期不卡

b. **OCR 跑通**：连续观察 2 分钟 → 应有 `text_len=数字` 日志（真 OCR 跑通），不再 `skip reason=worker_busy`；每 ~15s 一次 OCR（S1 已关，回到 2.0.66 纯 interval 节奏）

c. **若仍卡死**（极低概率，但段级打点已就位）：
   - 日志会停在某个 `stage=xxx_done` 之前——把日志发回，下一次拍板直接修该段
   - 关键观察点：
     - 停在 `stage=capture_done` 之前 → capture_active_frame await 永久阻塞（mss/win32 死锁）
     - 停在 `stage=ocr_done` 之前 → OCR 引擎 await 永久阻塞（winrt OCR hang）
     - 停在 `stage=detect_done` 之前 → detect_game await 永久阻塞（术语库扫/场景匹配 hang）
     - 停在 `stage=body_exit` 之前 → push_message 或 state update 阻塞

## 已知风险与回退方案

| 风险 | 影响 | 回退 |
|------|------|------|
| S1 默认关 → 用户错过"更快感知"特性 | 大世界→祈愿等场景切换仍要走 interval（默认 15s）才有响应 | 用户手动开 `change_driven_enabled = true`（不建议——真根因未定位前开 S1 仍可能卡死） |
| 段级打点每 OCR 多 5-7 条 INFO 日志 | 日志量增大 | 拍板 2.0.71 根因定位后可降为 DEBUG |
| threading.Timer 启动 60s 后可能 cancel 失败 | count 减不了，下个 tick 仍卡 | 真机无法验证；理论 fallback：下次 tick 进来时 worker 闸返回前先 force-reset（2.0.69 startup 重置逻辑） |
| `_released` flag 多线程竞争 | Python 列表赋值原子，但 list[0]=True 跨线程理论上可能不一致 | 用 `threading.Event` 或 `itertools.count` 替代（2.0.71 优化） |

## 关键决策

### 砍 S1（止损）的决策依据

**前提**：用户说 watchdog 从没触发过 = count 永久卡死 = OCR 完全停滞。S1 change-detect 路径里有 `await asyncio.to_thread(self._light_capture_dhash)`——如果这个 await 阻塞（mss/PIL 在 worker 线程死锁），也会让整个 tick hang 死。即使 mss 锁修了，dhash 路径仍可能有问题。

**保守做法**：默认关 S1，先恢复 OCR 跑通，再找根因。用户手动开也行（默认关的代码路径保留）。

### 段级打点的决策依据

**前提**：根因可能在 `_ocr_perceive_body` 的任意 await 段。不打点只能猜（加更多 try/except、加更多 timeout）——治标不治本。

**段级打点 = 单次曝光**：下次卡死时日志停在 `stage=xxx_done` 之前 = 该段阻塞 = 根因定位。下次拍板直接修该段，不用再猜。

## 验证证据

- ✅ pytest：**509 PASS / 1 XFAIL**（基线 503 + 新增 6）
- ✅ check：**0 errors / 1 warning**（git uncommitted；version=2.0.70 ✓）
- ⚠️ build：先生手动跑（与 2.0.65~2.0.69 同 Windows + sandbox tempfile 问题）
- ⚠️ TSX check：本次 panel.tsx 仅默认 false 微调，安全

## 不碰清单（红线全过）

- ✅ ocr_engine.py 不改
- ✅ push_message 只用 v2 三件套（test_redline 仍 == 4 call sites）
- ✅ R8 顶层无 IO
- ✅ 持锁期间不 await（count / Timer 回调都是 sync + lock）
- ✅ 用户本地 panel.tsx 协议修改（Bug 3 + Bug 4）未回滚