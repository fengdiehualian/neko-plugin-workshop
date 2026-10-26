# 2.0.72 startup 自动加载场景 — 实施报告

**版本**：2.0.72（2026-10-04）
**作者**：MiniMax-M3
**本轮开了 0 个子代理**——主线程顺序干（4 处修改都是 5-30 行小改动）。

---

## 症状与根因

真机 2.0.71 暴露：
- 启动 2 小时 logs 无 `loaded N scene(s) for genshin`
- 用户数据目录无 `scenes.toml`（override 空）
- scenes 一直 = 0
- SceneTracker 一直 `current=(none)`

真机证据：
```
20:31:50  restored game genshin (150 terms)   ← auto_restore
10:44:47  TRIGGER entry='set_game'             ← 手动触发
10:44:47  loaded 3 scene(s) for genshin        ← 只在这里才加载
```

**根因**：`startup()` 里 `_load_scenes_for_current()` 在 `_manager.restore()` **之前**调用——此时 `_manager.current` 还是 None，函数早退把 `_scene_store = {}`。restore 成功后没人再调一次。

---

## 修复

### 1. startup auto_restore 后立即 load scenes

```python
# __init__.py startup
asyncio.create_task(self._ocr.warmup_async())
self._load_scenes_for_current()  # 此时 _manager.current=None，pre-load 空（保留兼容）
result = await self._manager.restore(self._registry)
if result is None:
    return Ok({...})
self._init_anchors(result.session, result.library)
self.logger.info("multi_game_companion: restored game {} ({} terms)", ...)
# 拍板 2.0.72：restore 成功后立即再 load scenes——核心修复
self._load_scenes_for_current()
```

### 2. 日志明确化

```python
# _load_scenes_for_current
- self.logger.info("multi_game_companion: loaded {} scene(s) for {}", ...)
+ self.logger.info("multi_game_companion: restored {} scene(s) for {}", ...)
```

用户视角：`restored` 表示启动时自动加载（区别于 `set_game` 手动重载）。

### 3. scene_store.load_scenes 失败上报

```python
def _read_layer(path, warn_msg_on_missing=None, warnings=None):
    if not path.is_file():
        if warn_msg_on_missing and warnings is not None:
            warnings.append(warn_msg_on_missing)  # ← 新增
        return None
    try:
        return tomllib.load(fh)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"failed to parse {path}: ...")  # ← 新增
        return None
```

调用方 `_load_scenes_for_current` 已有 `for w in warnings: logger.warning(...)`，自动生效。

### 4. SceneTracker exit_pending_count 钳位

```python
def _handle_no_match(self, now):
    self._exit_pending_count += 1
    if self._exit_pending_count >= self._exit_grace_count:
        self._pending = None
        self._pending_count = 0
+    # 拍板 2.0.72：钳位——达到 grace 后不再递增
+    if self._exit_pending_count > self._exit_grace_count:
+        self._exit_pending_count = self._exit_grace_count
```

之前长期无命中场景下 `_exit_pending_count` 涨到 15+（heartbeat 日志里看到），现在钳位在 `exit_grace_count` (=5)。

### 5. scenes.toml 祈愿场景补 signals

```toml
[scenes."祈愿"]
- signals = ["纠缠之缘", "相遇之缘", "祈愿", "限定五星", "保底", "定轨"]
+ # 拍板 2.0.72：补"祈愿历史记录"/"角色活动祈愿"
+ signals = ["纠缠之缘", "相遇之缘", "祈愿", "限定五星", "保底", "定轨", "祈愿历史记录", "角色活动祈愿"]
```

---

## 测试

```
550 passed, 1 xfailed, 20 warnings in 19.95s
```

无新增测试（4 处修改都是已有覆盖范围）。`_handle_no_match` 钳位不影响现有 12 例 SceneTracker 测试。

---

## 真机验收（先生手动跑）

1. **启动日志确认**：
   - `restored game genshin (150 terms)` 之后立即出现 `restored 18 scene(s) for genshin`
   - 如果 scenes.toml 缺失：`scene load: default scenes file not found: ...` WARN
2. **祈愿界面测试**：
   - 切到祈愿界面（含"祈愿历史记录"/"角色活动祈愿"子页面）
   - 日志应出现 `scene_tracker scene-switch from=... to=祈愿 elapsed_in_old=Xs`
   - 接着 `ocr_perceive scene-prompt-push scene=祈愿 chars=...`
3. **场景状态机**：scenes=1 → scene-prompt-push；scene 切换瞬间触发（不等 2s tick）
5. **心跳日志**：长期无命中场景下 `exit_pending_count` 钳位在 5（不再涨到 15+）

---

## 文件清单

### 修改
- `plugin/plugins/multi_game_companion/__init__.py`（+7 行：startup 调用顺序修复 + 日志改名）
- `plugin/plugins/multi_game_companion/scene_store.py`（+13 行：默认文件缺失/解析失败 warning）
- `plugin/plugins/multi_game_companion/scene_tracker.py`（+6 行：exit_pending_count 钳位）
- `plugin/plugins/multi_game_companion/terms/genshin/scenes.toml`（+1 行：祈愿补 2 signal）
- `plugin/plugins/multi_game_companion/plugin.toml`（version 2.0.72）
- `plugin/plugins/multi_game_companion/pyproject.toml`（version 2.0.72）
- `plugin/plugins/multi_game_companion/CHANGELOG.md`（+30 行 2.0.72 section）

### 新建
- `plugin/plugins/multi_game_companion/docs/REPORT_2_0_72.md`

---

## 已知风险 / 后续

1. **`_load_scenes_for_current` 调用两次**（pre-restore + post-restore）——pre-restore 实际是空操作，可后续清理
2. **30s heartbeat 日志 + body_exit 日志带 scene_tracker 状态**——2.0.71 已加，本轮没改；这两条日志解决了"scenes=0 时看不出状态机在跑"的问题
3. **祈愿场景其他子页面**（如"武器活动祈愿"）如未命中，先生手动补 signals 到 scenes.toml 即可，无需发版