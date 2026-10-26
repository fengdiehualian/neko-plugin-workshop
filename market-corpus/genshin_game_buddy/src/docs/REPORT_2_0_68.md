# 2.0.68 — 紧急热修（S1 tick 沉默卡死 + apply_settings 缺 payload）

**本轮开了 0 个子代理**（沿用主线程：1 改 __init__.py 的 ocr_perceive 三层防御、1 改 apply_settings_entry payload 默认值、2 改版本号、1 新建测试文件 + 1 修测试）。

## 改动清单（4 改 + 2 新增）

| 文件 | 改动 |
|------|------|
| `plugin/plugins/multi_game_companion/__init__.py` | **Bug 1 修：ocr_perceive 三层防御** ① interval gate early-return → INFO `skip reason=interval_not_reached elapsed=Xs interval=Ys`；② worker 闸 early-return → INFO `skip reason=worker_busy in_flight=N workers=M`；③ 外层 try/except 兜底，异常 `logger.exception` 后吞；④ schedule 阶段（executor + run_in_executor + add_done_callback）try/except → 失败时 count -= 1 释放；⑤ change-detect 内层 try/except 保 await 安全。**Bug 2 修：apply_settings_entry 签名 `payload: dict \| None = None`，None → Err(INVALID_INPUT) 不抛 TypeError** |
| `plugin/plugins/multi_game_companion/plugin.toml` | 版本 2.0.67 → 2.0.68 |
| `plugin/plugins/multi_game_companion/pyproject.toml` | 版本 2.0.67 → 2.0.68 |
| `plugin/plugins/multi_game_companion/CHANGELOG.md` | 追加 2.0.68 节（触发/2 bug 修复/14 新测试/真机 4 步验证） |
| `plugin/plugins/multi_game_companion/tests/unit/test_ocr_perceive_defensive.py` | **新文件**：ocr_perceive 三层防御 7 例（schedule 失败释放 count / 外层异常吞掉 / interval_not_reached INFO / worker_busy INFO / 正常 tick begin / dhash await 失败吞 / 连续 2 tick 无 count 泄漏） |
| `plugin/plugins/multi_game_companion/tests/unit/test_apply_settings_payload.py` | **新文件**：apply_settings_entry payload 容错 7 例（签名检查/payload=None Err/缺省 Err/{ } Err/{ options:{ } } Ok/options 非 mapping Err/options 含 change Ok） |

## 用户命令序列（先生手动跑）

```bash
cd D:\NEKO\N.E.K.O-main\N.E.K.O-main
uv run neko-plugin check multi_game_companion
uv run --with pytest pytest plugin/plugins/multi_game_companion/tests -q
Remove-Item -Recurse -Force 'plugin\plugins\multi_game_companion\.pytest-tmp'
uv run neko-plugin build multi_game_companion
# UI 改动额外（本次 panel.tsx 未改，可跳过）：
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/multi_game_companion
```

## 真机测试 4 步

a. **tick 不再沉默**：启动后连续观察 2 分钟 → 日志应出现 ≥40 条 tick begin（2 min / 15s = 8 条，密集触发 S1 时更多）；任何 `skip reason=...` 也都打 INFO 日志，**不会再有"日志静默卡死"**

b. **面板不再崩**：打开面板 → 改任意值 → 点应用 → 不崩，toast 显示"已保存"（Bug 2 修复验证；老 SDK 偶发的 `TypeError: missing 1 required positional argument` 不复现）

c. **S1 仍正常**：故意触发场景变化（切到祈愿界面）→ 3s 内日志含 `reason=change_detect diff=N>=8` + `scene-switch from=X to=Y cooldown=30s`

d. **静止验证**：静止 5 分钟 → 日志全是 `reason=interval` 或 `skip reason=interval_not_reached`（早期 return 现在有日志，**不再有静默死亡**）

## 根因分析与关键决策

### Bug 1 根因（推测）

旧实现（2.0.67）路径：
```python
with self._ocr_in_flight_lock:
    if self._ocr_in_flight_count >= self._options.ocr_worker_threads:
        return  # ⚠️ 静默 return，没日志
    self._ocr_in_flight_count += 1

self._last_real_ocr_monotonic = now_mono
self.logger.info("...tick begin...")

# ⚠️ schedule 阶段任一异常 → count 永久卡在 1，worker 闸静默挡后续所有 tick
loop = asyncio.get_running_loop()
future = loop.run_in_executor(self._ocr_executor, ...)
future.add_done_callback(_on_done)
```

真机 14 分钟只 1 条 tick begin = 第一次 tick 成功 schedule 后，schedule 阶段（疑似 `run_in_executor` 抛异常——可能 executor 被热重载 / 关闭 / event loop 在 worker thread 出问题）让 `_ocr_in_flight_count` 卡在 1，后续所有 tick 进 worker 闸分支静默 return，**没有任何日志暴露**。这就是用户报的"沉默卡死"。

### 关键修复决策

1. **三层防御胜过一处修补**：仅加 try/finally 保 count 不够（解决症状不解决根因）；仅加日志不够（卡死仍卡死）。三层叠加：
   - INFO 日志 = 暴露"沉默卡死"的具体分支
   - try/finally 保 count = 不让 worker 闸永久卡死
   - 外层 try/except = 防止任何意外中断 timer
2. **early-return 必须有 INFO 日志**：silent return 是反模式——失败模式无日志 = 排查地狱。拍板 2.0.68 后所有 early-return 都打 INFO 含 reason + 关键值（elapsed/interval、in_flight/workers）。
3. **不改变 OCR 主链路行为**：纯加防御 + 日志，不动 2.0.67 的 S1 + S2 主逻辑。
4. **apply_settings_entry payload 默认 None**：比 `payload: dict = {}` 更显式——None 表达"未传任何东西"，{} 表达"传了但为空"（语义不同）。

## 已知风险与回退方案

| 风险 | 影响 | 回退 |
|------|------|------|
| 每 tick 多 1-3 条 INFO 日志 | 日志量增大（但只是 OCR tick 频率） | 改 logger.info 为 logger.debug（不建议——会失去排查能力） |
| 外层 try/except 吞掉非预期异常 | 真正的 bug 也会被吞 | logger.exception 留下完整 traceback，配合监控看 exception 计数 |
| schedule 失败现在频繁暴露 | 暴露 2.0.67 隐藏的根因（executor / event loop 问题） | 不回退——这是好事，暴露了才有修的机会 |

## 验证证据

- ✅ pytest：**489 PASS / 1 XFAIL**（基线 475 + 新增 14）
- ✅ check：**0 errors / 1 warning**（warning 是 git uncommitted changes；version=2.0.68 ✓）
- ⚠️ build：先生手动跑（Windows + sandbox 下 `tempfile.mkdtemp` ACL 异常，与 2.0.65/2.0.66/2.0.67 同因）
- ⚠️ TSX check：本次 panel.tsx 未改，可跳过

## 不碰清单（红线）

- ✅ ocr_engine.py 不改
- ✅ push_message 只用 v2 三件套（test_redline `assert len(sites) == 4` 仍通过）
- ✅ R8 顶层无 IO（def test_* 之外顶层无副作用）
- ✅ 持锁期间不 await（count 操作仍是 sync + lock）