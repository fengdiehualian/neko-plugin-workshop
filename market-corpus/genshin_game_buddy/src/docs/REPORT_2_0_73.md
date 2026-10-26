# 2.0.73 worker 卡死回归修复 — 实施报告

**版本**：2.0.73（2026-10-05）
**作者**：MiniMax-M3
**本轮开了 0 个子代理**——主线程顺序干（4 处修改 + 14 个新测试）。

---

## 症状

真机 2.0.72 暴露：
```
11:16:41  stage=body_exit              ← 最后一条
11:16:44  heartbeat current=祈愿 pending=祈愿 pending_count=5
11:19:14  heartbeat current=祈愿 pending=祈愿 pending_count=5  ← 3 分钟无变化
```

- body_exit 冻结 3 分钟，只有 heartbeat
- pending_count 卡在 5 不动
- watchdog 触发但 worker 仍卡死

---

## 根因（深入排查）

### 排查路径
1. **watchdog 是否还在正确位置？** ✅ `_ocr_executor.submit` 后挂 `threading.Timer(60, _watchdog)`（line 1953）——位置正确
2. **拆链路后是否引入新 await 阻塞点？** ✅ 无新 await（SceneTracker 是同步，缓存复用是同步）
3. **抓屏缓存复用逻辑是否死锁？** ✅ 逻辑无死锁
4. **mss 全局锁是否嵌套死锁？** ❌ **找到根因**

### 根因
**`_MSS_LOCK` 是非 reentrant Lock + 嵌套调用 = 永久死锁**：

```python
# screen_capture.py:425  capture_active_frame 内
with _MSS_LOCK, mss.mss() as sct:        # acquire _MSS_LOCK (1)
    monitors = sct.monitors or []
    ...
    result = _capture_mss(rect)           # ← _capture_mss:280 也 acquire _MSS_LOCK (2)
                                          # non-reentrant → 永久挂起
```

### watchdog 救不了的原因
```python
# ocr_perceive tick
def _watchdog():
    if not cf_future.done():
        cf_future.cancel()  # ← 对运行中任务返回 False，不抛，只标 cancelled
        # worker 仍卡死
```

`concurrent.futures.Future.cancel()` 对**已运行中**的任务返回 `False`（不能强制终止），只标 cancelled 状态。worker 卡在 Python 解释器层面，Timer 救不了。

---

## 修复（4 处）

### 1. **核心修复**：`_MSS_LOCK = threading.RLock()`

```python
# screen_capture.py
-_MSS_LOCK = threading.Lock()
+_MSS_LOCK = threading.RLock()  # reentrant——capture_active_frame → _capture_mss 嵌套不死锁
```

跨线程仍 mutex（RLock 是 reentrant mutex），只解除同线程嵌套死锁。

### 2. **SceneTracker pending 修正**

```python
# scene_tracker.py update() 正常路径
self._exit_pending_count = 0
if primary == self._pending:
    self._pending_count += 1
else:
    self._pending = primary
    self._pending_count = 1
+ # 拍板 2.0.73：current 已稳态时清 pending——避免 pending_count 无限增长
+ if primary == self._current:
+     self._pending = None
+     self._pending_count = 0
```

之前 100 次重复命中同一场景 → pending_count 自增到 100、永不触发 switch（条件 `pending != current` 永远 false）。现在稳定场景下 pending_count = 0。

### 3. **heartbeat 附 worker 状态**

```python
# __init__.py body_enter 心跳
self.logger.info(
    "multi_game_companion: scene_tracker heartbeat "
    "current={} pending={} pending_count={} exit_pending_count={} "
    "ocr_in_flight={} ocr_workers={} executor_alive={}",
    ...
    self._ocr_in_flight_count,
    self._options.ocr_worker_threads,
    not getattr(self._ocr_executor, "_shutdown", False),
)
```

之前只看 SceneTracker；现在能看出 worker 闸是否被卡、executor 是否活着。

### 4. **text_unchanged 早退打 INFO 日志**

```python
# __init__.py body text_hash == _last_ocr_hash 分支
+ self.logger.info(
+     "multi_game_companion: ocr_perceive early-return: text_unchanged hash={} prev={}",
+     text_hash[:8], (self._last_ocr_hash or "")[:8],
+ )
```

之前静默 → 真机 "OCR 一直返回同一帧" 看起来像"卡死"。现在留痕。

---

## 测试

```
564 passed, 1 xfailed, 20 warnings in 26.53s
```

新增 14 例：

### RLock 验证（4 例）
1. `_MSS_LOCK` 是 `RLock`（`type(threading.RLock())`）
2. 同线程嵌套 acquire 3 次不死锁
3. 跨线程仍互斥（mutex 语义保留）
4. `capture_active_frame` 集成测试：mock mss 跑完整路径，2s 内完成不死锁

### SceneTracker pending 修正（4 例）
5. 当前场景稳定时 pending 不累加
6. 切到新场景时 pending 重新计数
7. 100 次稳定场景下 pending_count = 0
8. 无匹配 → EXITED + exit_pending_count 钳位

### OCR 早退 + heartbeat（6 例）
9. OUT_OF_GAME 早退 INFO 日志
10. text_unchanged 早退 INFO 日志
11. heartbeat 包含 ocr_in_flight / ocr_workers
12. heartbeat 包含 executor_alive
13. black_frame 早退日志未回归
14. no_session 早退日志

---

## 真机验收

### 1. 启动后日志确认

启动 5 分钟内应该看到：
```
multi_game_companion: stage=body_enter
multi_game_companion: stage=capture_done dt=0.143s
multi_game_companion: stage=ocr_done dt=1.302s
multi_game_companion: stage=body_exit total=1.521s scene_tracker current=祈愿 pending=(none) pending_count=0
```

每 2s 一轮 body_enter + body_exit（不再冻结）。

### 2. 抽卡视频 5 分钟

- `body_exit > 100 条`（之前 3 分钟就冻结）
- `pending_count = 0`（场景稳定，pending 不再累加）
- `text_unchanged` 日志偶尔出现（OCR 文本未变时）

### 3. heartbeat 30s 一条

```
multi_game_companion: scene_tracker heartbeat 
current=祈愿 pending=(none) pending_count=0 exit_pending_count=0 
ocr_in_flight=0 ocr_workers=2 executor_alive=true
```

- `ocr_in_flight=0`：worker 闸未满
- `ocr_workers=2`：2 线程配置生效
- `executor_alive=true`：ThreadPoolExecutor 活着

### 4. 场景切换

切到角色详情页：
```
multi_game_companion: scene_tracker scene-switch from=祈愿 to=角色详情页 elapsed_in_old=120.5s
multi_game_companion: ocr_perceive scene-prompt-push scene=角色详情页 chars=842
```

---

## 文件清单

### 修改
- `plugin/plugins/multi_game_companion/screen_capture.py`（+5 行注释 / Lock → RLock）
- `plugin/plugins/multi_game_companion/scene_tracker.py`（+5 行 / primary == current 清 pending）
- `plugin/plugins/multi_game_companion/__init__.py`（+10 行 / heartbeat 状态字段 + text_unchanged 日志）
- `plugin/plugins/multi_game_companion/tests/unit/test_mss_lock.py`（断言改成 RLock）
- `plugin/plugins/multi_game_companion/plugin.toml`（version 2.0.73）
- `plugin/plugins/multi_game_companion/pyproject.toml`（version 2.0.73）
- `plugin/plugins/multi_game_companion/CHANGELOG.md`（+50 行 2.0.73 section）

### 新建
- `plugin/plugins/multi_game_companion/tests/unit/test_screen_capture_mss_lock.py`（4 例）
- `plugin/plugins/multi_game_companion/tests/unit/test_scene_tracker_pending_fix.py`（4 例）
- `plugin/plugins/multi_game_companion/tests/unit/test_ocr_perceive_2_0_73.py`（6 例）
- `plugin/plugins/multi_game_companion/docs/REPORT_2_0_73.md`

---

## 已知风险 / 后续

1. **RLock 比 Lock 略慢**（每次 acquire 检查 owner thread）——但 mss 抓屏本身就 100ms+，lock overhead 可忽略
2. **concurrent.futures cancel 对运行中任务无效**是 Python 限制——只能改代码逻辑绕开（已修）；没有更暴力的杀进程方法
3. **scene_switch_cooldown** 仍是 30s（2.0.67 设的）——如需更激进，先生手动改 plugin.toml 即可，无需发版

---

## 给运维的话

如果真机仍卡死：
1. 看最新 heartbeat 日志的 `ocr_in_flight` —— 如果 ≥ ocr_workers，就是闸满了；下一条 `worker_busy` 日志会确认
2. 看 `executor_alive` —— 如果 false，ThreadPoolExecutor 已死，需要重启插件
3. 看 `pending_count` 和 `current` —— 应该 consistent；不一致就是 SceneTracker 状态机 bug
4. 看 watchdog 日志 —— 60s 内应触发一次（worker timeout）；如未触发说明 Timer 线程死了