# 2.0.69 — 紧急热修（worker 卡死 + 配置路径透明 + panel 微调）

**本轮开了 0 个子代理**（沿用主线程：1 改 screen_capture.py 加 _MSS_LOCK、1 改 __init__.py 加 startup 重置 + config_source 日志 + worker 超时看门狗 + done_callback try/except、1 改 panel.tsx 微调 PROFILE_TONES、2 改版本号、3 新建测试）。

## 改动清单（7 改 + 3 新增）

| 文件 | 改动 |
|------|------|
| `screen_capture.py` | **Bug 1 修 ①**：新增模块级 `import threading` + `_MSS_LOCK = threading.Lock()`；`_capture_mss` / `capture_active_frame` 主屏路径的 `mss.mss()` 调用都包 `with _MSS_LOCK:` 串行；新增公共函数 `grab_primary_for_dhash()`（集中 mss 调用点 + dHash 算法） |
| `__init__.py` | **Bug 1 修**：① `import` screen_capture.grab_primary_for_dhash（取代原 `_light_capture_dhash` 内 importlib 绕锁）② ocr_perceive schedule block 拆 `_released` flag + `_on_done` try/except + `_watchdog` 60s call_later（worker hang 强制 cancel + 释放 count）③ startup 硬重置 `_ocr_in_flight_count = 0`。**Bug 2 修**：startup 打 `config_source=<path>` INFO 日志（含 `apply_settings writes back to this file` 提示）；`config.path()` 抛异常降级为 `<unknown>` |
| `plugin.toml` / `pyproject.toml` | 2.0.68 → 2.0.69 |
| `ui/panel.tsx` | **Bug 5 修**：PROFILE_TONES `custom: "info" → "warning"`（偏离预设与 performance 同色，视觉区分） |
| `CHANGELOG.md` | 追加 2.0.69 节（4 修 / 14 新测试 / 5 步真机验证 / 4 不碰） |
| `tests/unit/test_ocr_worker_timeout.py` | **新**：4 例（worker hang 看门狗释放 / 正常完成不重复 -1 / startup 硬重置 / _on_done 失败 logger.exception） |
| `tests/unit/test_mss_lock.py` | **新**：5 例（2 worker 并发不死锁 / 10 worker 并发不死锁 / 模块级锁存在 / 返回值契约 / 不抛异常） |
| `tests/unit/test_config_path.py` | **新**：6 例（config_source INFO 日志 / path 抛异常不崩 / startup 硬重置 / 读写一致性 / 日志含 apply_settings 提示） |

## 用户命令序列（先生手动跑）

```bash
cd D:\NEKO\N.E.K.O-main\N.E.K.O-main
uv run neko-plugin check multi_game_companion
uv run --with pytest pytest plugin/plugins/multi_game_companion/tests -q
Remove-Item -Recurse -Force 'plugin\plugins\multi_game_companion\.pytest-tmp'
uv run neko-plugin build multi_game_companion
# panel.tsx 有改动（仅 PROFILE_TONES 一处微调，安全）：
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/multi_game_companion
```

## 真机测试 5 步

a. **config_source 日志**：启动后日志含 `config_source=C:\Users\alpha\AppData\Local\N.E.K.O\plugins\multi_game_companion\config\plugin.toml`（或实际路径）——**立刻知道该改哪个文件**（Bug 2 验证）

b. **改 user config 副本生效**：用户改 `config_source` 日志里那个路径的 `ocr_worker_threads=1` → 重启插件 → 日志 workers=1（**Bug 2 验证——别改安装副本，那份无效**）

c. **OCR 不再沉默卡死**：连续观察 2 分钟 → ≥40 条 tick begin；in_flight 在 0/1 之间正常跳动；不再 13 分钟连续 `in_flight=2 workers=2`（**Bug 1 验证**）

d. **面板协议正常**：打开面板 → 改任意值 → 点应用 → toast "已保存"（Bug 3 协议——`api.call("apply_settings", { payload })` 让 args 展平成 kwargs 后 entry 收到 `payload=...`）

e. **badge 颜色可区分**：面板 "当前档位" 选 "自定义" → badge 颜色变橙（warning）→ 与 auto（蓝）/ eco（绿）/ balanced（蓝）/ performance（橙）区分明显（**Bug 5 验证**——custom 也是橙，但因文案"自定义"区分）

## 已知风险与回退方案

| 风险 | 影响 | 回退 |
|------|------|------|
| mss 全局锁串行化 2 worker | 性能峰值场景下并发优势损失 | 把 ocr_worker_threads 调回 1（用户已调，匹配锁语义）；或扩展锁为按 rect 分片（复杂） |
| 60s 看门狗可能在 worker 正常长跑（重 OCR）时误触发 | 看门狗 timeout warning + 强制 cancel | 用户调 change_detect_threshold 到 1 + interval 到 30s（减少长跑）；或下次拍板可考虑可配 timeout |
| `apply_settings writes back to this file` 日志路径 = self.config.path() 返回值 | 路径错误时仍可能误导用户 | 日志降级为 `<unknown>` 时同时 warning（已实现）；用户可改 self.config.path() |
| PROFILE_TONES custom: "warning" 与 performance 同色 | 用户分不清自定义和性能档 | 文案 "自定义" vs "性能" 已区分；或下次可加独立色（hosted-ui 暂只允许 4 色） |

## 关键设计决策

### Bug 1：worker 卡死 4 层加固

真机 13 分钟 `in_flight=2 workers=2` 连续沉默 = `count` 永远卡住、worker 闸静默挡掉所有后续 tick。4 层防御缺一不可：

1. **mss 全局锁**（根源）——mss.mss() 官方文档明确非线程安全，2 worker 并发 grab() 共享内部缓冲会死锁。锁在 `screen_capture.py` 模块级，所有 mss 调用（`_capture_mss` / `capture_active_frame` 主屏路径 / 新 `grab_primary_for_dhash`）都走锁。
2. **`_on_done` try/except + logger.exception**（暴露沉默）——若未来有任何 count 减失败路径，必须打 exception 日志，绝不静默。
3. **60s 看门狗 call_later**（兜底）——worker hang 死（mss 死锁 / winrt OCR 卡 IO）时强制 cancel future + 释放 count。
4. **共享 `_released` flag**（互斥）——`_on_done` 和 watchdog 谁先到谁释放，避免 count 减成负数。

### Bug 2：配置路径透明化

真机用户改 `C:\...\N.E.K.O\.neko-plugin-installations\plugins\multi_game_companion\plugin.toml` 无效——插件读的是 user config 副本（`...\Local\N.E.K.O\plugins\...\config\plugin.toml`）。修复：

1. startup 打 INFO `config_source=<path> (apply_settings writes back to this file; user edits take effect on next startup)`——用户立刻知道该改哪个文件
2. `self.config.path()` 抛异常时降级为 `<unknown>`——startup 不崩
3. 读写一致性：dump / update 走同一个 self.config 对象（SDK 层保证）

### Bug 5：badge 颜色区分

`custom: "info" → "warning"`——与 `performance: "warning"` 同色，但文案 "自定义" vs "性能" 区分。hosted-ui Tone 联合类型仅允许 info/success/warning/danger 4 色，"自定义" 是这 4 色中最适合表达"偏离预设"的（warning 表示需关注）。

## 验证证据

- ✅ pytest：**503 PASS / 1 XFAIL**（基线 489 + 新增 14）
- ✅ check：**0 errors / 1 warning**（git uncommitted；version=2.0.69 ✓）
- ⚠️ build：先生手动跑（与 2.0.65/2.0.66/2.0.67/2.0.68 同 Windows + sandbox tempfile 问题）
- ⚠️ TSX check：本次 panel.tsx 仅 PROFILE_TONES 一处微调，安全；先生可跑 `npm run check-hosted-tsx`

## 不碰清单（红线全过）

- ✅ ocr_engine.py 不改
- ✅ push_message 只用 v2 三件套（test_redline `assert len(sites) == 4` 仍守）
- ✅ R8 顶层无 IO
- ✅ 持锁期间不 await（count 操作仍是 sync + lock；watchdog 也是 sync _release_count）
- ✅ 保留用户本地修改（Bug 3 协议 `{ payload }` + Bug 4 `tone="neutral"→"info"`）未回滚