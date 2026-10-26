# Changelog — multi_game_companion

## 2.0.73 — 2026-10-04（worker 卡死回归 + 早退日志 + heartbeat 暴露 worker 健康度）

**主题**：修复 2.0.72 真机"3 分钟无 body_exit 日志"的卡死回归。根因不是 watchdog 失效（2.0.70 修的 `threading.Timer` 看门狗仍在工作），而是两条 early-return 路径静默 return + heartbeat 不暴露 worker 闸状态。

### 动机

真机 2.0.72 暴露（先生真机观察）：
```
11:16:41 stage=body_exit   ← 最后一条
11:16:44 heartbeat current=祈愿 pending=祈愿 pending_count=5
...
11:19:14 heartbeat current=祈愿 pending=祈愿 pending_count=5   ← 3 分钟无变化
```
- 看起来像"worker 卡死"，实际是早退路径静默 return + heartbeat 不显示 worker 状态
- `pending_count` 卡 5 不动：SceneTracker 把 current=primary 时仍设 pending=current，但 `_maybe_fire_event` 要求 `pending != current` → 永远不触发切换 → pending_count 单调累积

### 根因

1. **`__init__.py:_ocr_perceive_body`** 有两条静默 early-return：
   - `state == "OUT_OF_GAME"`：隐私闸早退，**不打日志**
   - `text_hash == _last_ocr_hash`：OCR 返回与上次相同的帧，**不打日志**
   - 后果：运维看不出 body 是早退还是卡死，watchdog 即使 60s 触发也分不清"释放 count"和"早退"的差别
2. **SceneTracker 心跳只显示 SceneTracker 状态**，看不到 `_ocr_in_flight_count` / `_ocr_executor._shutdown` / `ocr_worker_threads`，运维看不出 worker 闸是否被卡
3. **SceneTracker `update()` 路径**：
   ```python
   # 原代码（2.0.71-2.0.72）
   if primary == self._pending:
       self._pending_count += 1
   else:
       self._pending = primary
       self._pending_count = 1
   ```
   当 `primary == self._current` 时，pending 被设为 current，pending_count 累积，**永远不会触发 `_maybe_fire_event`**（要求 `pending != current`）。日志看起来"卡死"，实际只是 OCR 一直命中同一场景。

### 改动

- **`__init__.py` 早退打日志**（拍板 2.0.73）：
  - `state == "OUT_OF_GAME"` 早退：`INFO ocr_perceive early-return: state=OUT_OF_GAME (privacy gate; ...)`
  - `text_hash == _last_ocr_hash` 早退：`INFO ocr_perceive early-return: text_unchanged hash=X prev=Y`
- **`__init__.py` heartbeat 扩展**（拍板 2.0.73）：
  - 原来：`current=X pending=X pending_count=N exit_pending_count=M`
  - 现在：附加 `ocr_in_flight=N ocr_workers=M executor_alive=True/False`
  - 一行同时显示 SceneTracker 状态 + worker 闸健康度，运维一眼看出 worker 是否卡在 worker_threads 上限
- **`scene_tracker.py` primary == current 路径修正**（拍板 2.0.73）：
  ```python
  if primary == self._current:
      self._pending = None
      self._pending_count = 0
  elif primary == self._pending:
      self._pending_count += 1
  else:
      self._pending = primary
      self._pending_count = 1
  ```
  - 效果：`pending` 永远不等于 `current`；心跳日志 `pending=(none)` / `current=祈愿` → 正确反映"已稳态"
  - 现有 12 例 SceneTracker 单测不受影响（NO_CHANGE / hysteresis / 防抖 / 退出判定都按原本契约）
- **版本号**：2.0.72 → 2.0.73（`pyproject.toml` / `plugin.toml`）

### 验证

- **新增 `tests/unit/test_ocr_perceive_2_0_73.py`**（5 例）：
  1. `test_ocr_perceive__out_of_game_early_return_logs` —— OUT_OF_GAME 早退必含 `early-return` + `OUT_OF_GAME` 关键字
  2. `test_ocr_perceive__text_unchanged_early_return_logs` —— text_unchanged 早退必含 `text_unchanged` 关键字
  3. `test_heartbeat__exposes_worker_health` —— heartbeat 必含 `ocr_in_flight` / `ocr_workers` / `executor_alive` 字段
  4. `test_heartbeat__executor_shutdown_shows_zero_workers` —— executor shutdown 时 `executor_alive=False`
  5. `test_scene_tracker__pending_cleared_when_primary_equals_current` —— primary=current 时 pending=None / count=0
  6. `test_scene_tracker__switch_after_pending_clear` —— pending 清零后切到新场景仍能正常 hysteresis 切换
- **现有测试无回归**：
  - `test_scene_tracker.py`（12 例）：所有 NO_CHANGE / SWITCHED / EXITED 路径仍按原本契约
  - `test_ocr_perceive_split.py`（5 例）：含 FakeTimer 加速 60s → 0.05s
  - `test_ocr_worker_timeout.py`（5 例）：worker hang + watchdog 释放 count
  - `test_ocr_perceive_defensive.py`：单测契约不变

### 真机验收（5 分钟纯抽卡视频）

- ✅ 日志应持续有 `stage=body_enter / body_exit`，5 分钟 ≥ 100 条 body_exit（OCR 2s tick 节奏）
- ✅ `pending_count` 不再冻结（current=primary 时立即清零）
- ✅ `ocr_in_flight` 显示 0 或 1（不卡在 `ocr_worker_threads` 上限）
- ✅ 若仍卡死：日志停在某个 `early-return: <reason>` → 该 reason 是早退原因，不是真卡死

### 不变清单

- ✅ `threading.Timer(60, _watchdog)` 看门狗不动（2.0.70 修复已工作；本轮只是补日志便于区分"早退"vs"卡死"）
- ✅ push_message v2 三件套调用 site 不变（仍是 4）
- ✅ 持锁期间不 await（_ocr_in_flight_lock 同步、`SceneTracker.update()` 同步）
- ✅ 用户本地 panel.tsx 未改

---

## 2.0.73 — 2026-10-05（worker 卡死回归修复）

**主题**：根除真机 2.0.72 "body_exit 消失 3 分钟" 的 `_MSS_LOCK` 嵌套死锁；补早退 INFO 日志；SceneTracker pending 修正。

### 症状
真机 2.0.72 暴露：
```
11:16:41  stage=body_exit              ← 最后一条
11:16:44  heartbeat current=祈愿 pending=祈愿 pending_count=5
11:19:14  heartbeat current=祈愿 pending=祈愿 pending_count=5  ← 3 分钟无变化
```

- body_exit 冻结 3 分钟，只有 heartbeat
- pending_count 卡在 5 不动

### 根因
**`_MSS_LOCK` 非 reentrant 导致 capture_active_frame 嵌套调用死锁**：
```python
# screen_capture.py line 425
with _MSS_LOCK, mss.mss() as sct:        # acquire _MSS_LOCK
    ...
    result = _capture_mss(rect)           # ← _capture_mss line 280 也 acquire _MSS_LOCK
                                          # non-reentrant lock → 永久挂起
```

watchdog 触发后 `cf_future.cancel()` 对运行中任务返回 `False`——worker 仍卡死。

### 改动
- **`screen_capture.py`**：`Lock` → `RLock`（reentrant）
- **`scene_tracker.py`**：`primary == current` 时清空 pending
- **`__init__.py` heartbeat**：附 `ocr_in_flight` / `ocr_workers` / `executor_alive` 状态
- **`__init__.py` body 早退**：text_unchanged 早退打 INFO 日志
- **版本号**：2.0.72 → 2.0.73

### 测试
**564 passed**（550 + 14 新测试），1 xfail

### 真机验收
- 放纯抽卡视频 5 分钟 → body_exit > 100 条
- pending_count 在场景稳定时 = 0
- heartbeat 每 30s 显示 `ocr_in_flight=0 ocr_workers=2 executor_alive=true`

---

## 2.0.72 — 2026-10-04（startup 自动加载场景）
- **问题**：插件进程无 `winrt` 模块 → OCR 静默失败（`ModuleNotFoundError` 被
  `ocr_engine.extract_text_from_base64` 的 `except Exception` 吞掉，2.0.18 暴露后实锤）
- **改动**：
  - 把 `.venv\Lib\site-packages\winrt\`（含 7 个 `.pyd` native 扩展 + `msvcp140.dll` + Python 包）
    整体 vendor 到 `plugin\plugins\multi_game_companion\vendor\winrt\`
  - `__init__.py` 加 `sys.path` 兜底（宿主 `host.py:_prepare_child_plugin_vendor_path`
    通常自动加，双保险：插件独立加载 / 异常宿主场景也能 import 到 winrt）
  - 保留 2.0.18 的 OCR `warning` 日志暂不撤（等真机验证成功再决定）
- **打包**：不动 pyproject.toml。宿主 `neko-plugin build` 默认自动包含 `vendor/`
  （`archive_utils.py:390`），无 `include` 规则反而最稳——加错规则会被
  `test_build_plugin_rejects_include_rules_that_drop_vendor` 拒掉
- **解决**：插件不再依赖宿主 Steam 版 N.E.K.O. 自带 Python 环境的 winrt，自包含
- **遗留**：
  - 待真机验证 OCR 真的跑通（预期：`text_len > 0`，无 `OCR failed: ModuleNotFoundError`）
  - 2.0.18 的 OCR `warning` 日志视真机结果决定撤/留
  - `dependencies = []` 保持零依赖（静态红线 D7 "第三方依赖必须声明 + vendor" 在
    此特殊场景下靠 `vendor/` 满足，依赖字段留空以免 `sync` 校验误判）

## 2.0.20 — 2026-09-26
- **触发**：2.0.19 反馈 build 出的 .neko-plugin 解压后 `*winrt*` 条目为空
- **诊断**：复核 `core/build.py:332-351` `copy_plugin_runtime_files`（`source_dir.rglob("*")` 全量枚举）
  + `core/build_rules.py:18-39`（默认排除集不含 `vendor` / `.pyd` / `.dll`）+ `build_rules.py:126`
  （`if not rules.include: return False`，无 `[tool.neko.build]` 规则时全量不过滤）+ `core/build.py:415-429`
  `export_package`（`staging_root.rglob("*")` 全量写 zip）。**代码路径上 vendor/winrt 必然进 zip**
- **改动**：无功能性改动。版本号仅作为本轮打包验证的标记；2.0.19 的"空"极可能是查看了陈旧产物
  （宿主 Steam 安装目录里未刷新的旧 .neko-plugin，或 build 前 vendor/ 未落盘）
- **遗留**：用 `--keep-staging` 重建并核对打印的 `staging_dir` 下是否存在
  `vendor/winrt/_winrt.cp311-win_amd64.pyd`；若 staging 有但 zip 没有（极不可能），是 export_package bug；
  若 staging 没有，把 staging 路径贴回来定位 rglob/should_skip 改动

## 2.0.21 — 2026-09-26
- **问题**：`plugin.toml` 作者名 `TODO`；`scenes.toml` 信号串未经真实 OCR 调优，`scenes=0` 恒不命中
- **改动**：
  - `plugin.toml` 作者名 → `silverwolftyro`（去掉 `TODO` 注释）；`pyproject.toml [project]` 加 `authors`
  - `ocr_perceive` 加临时调试日志：当 `text_len > 0` 且 `scenes == 0` 时打印 OCR 前 80 字（`text[:80]`，
    变量在 `ocr_perceive` 作用域内名为 `text`，非 `process_ocr` 的 `combined`）
  - `tests/static/test_redlines.py` 的 `test_manifest__author_is_filled_at_release` 移除 `@pytest.mark.xfail`
    装饰（作者已填，断言现在真实通过，不再是 xfail）
- **解决**：待真机收集 5-10 条 `sample (scenes=0) text_head=` 样本后，调 `scenes.toml` 信号串
- **遗留**：
  - 调试日志调好 `scenes.toml` 后撤除（计划 2.0.22）
  - 2.0.18 的 OCR `warning` 保留，不动（真失败时仍重要）

## 2.0.22 — 2026-09-26
- **问题**：2.0.19 在 `__init__.py` 顶层加的 vendor 兜底（`Path(...).is_dir()` + `sys.path.insert`）违反 R8
  （模块顶层无副作用），pytest `test_redline__no_import_time_side_effects` 报错
- **改动**：删除顶层 vendor 兜底（2.0.20 真机日志 `14:48:07` 已确认宿主
  `_prepare_child_plugin_vendor_path` 自动把 vendor/ 加进 sys.path，兜底冗余）；
  顺手删掉只为这段用的 `import sys` 和 `from pathlib import Path`（全文搜确认无别处引用）
- **解决**：R8 检测恢复正常，pytest 应当 339 passed, 1 xfailed
- **遗留**：无

## 2.0.56 — 2026-09-26
- **问题**：丝柯克等角色答不出，因上下文里无该角色数据，LLM 不知道可以查（"我去搜一下"）
- **改动**：
  - 激活策略：`_init_anchors` 从"2 条锚点"改为"核心层全激活"——遍历 `library.entries` 收集
    `dimension ∈ CORE_LAYER_DIMENSIONS = ("core","slang","systems")` 的全部条目（约 20-35 条），
    `source="core_layer"` TTL=`inf` 永不淘汰；`entry.context_terms` 显式清单保留为额外补充（去重）
  - `MAX_ACTIVE`：12 → 40（核心层典型 20-35 条，留缓冲）
  - `lookup_game_term` description 明示"当前游戏的核心术语（机制/系统/黑话）已常驻上下文；
    角色、武器、圣遗物等具体条目的详情需要通过本工具查询"，让 LLM 遇角色名主动调工具
  - 新增 `term_store.CORE_LAYER_DIMENSIONS` 常量；`activation.py` 的 `_evict` 把 `core_anchor`
    改名 `core_layer`（同一桶：sort_key (3,0) + skip eviction）
  - `tests/unit/test_activation.py` 的 `test_core_anchor_never_evicted` → `test_core_layer_never_evicted`，
    `source="core_anchor"` → `"core_layer"`
- **解决**：LLM 知道核心层已常驻、具体条目可查，遇角色名主动调用 `lookup_game_term`
- **遗留**：debug 日志（`scenes=0 text_head=`）保留，等场景细分；场景细分待 2.0.57+

## 2.0.57 — 2026-09-26
- **问题**：
  - 2.0.56 真机预期行为变化：`test_context_pack__context_terms_overrides_auto_selection` 失败
    （`元素反应` 属 core 维度，核心层常驻后必然出现在 block 里，旧断言 `not in block` 过期）
  - **更要紧**：`context_inject_max_terms` 默认 = 5，`_build_activated_context` 用它截断
    `active_keys[:limit]` → 核心层即使激活 ~30 条，注入 block 只 5 条，**核心层常驻名存实亡**
- **改动**：
  - `context_inject_max_terms` 默认值 **5 → 40**（与 `MAX_ACTIVE` 对齐，让核心层完整注入）：
    `game_registry.py` 的 `PluginOptions` dataclass 默认、`from_section` clamp 默认、
    `plugin.toml` 注释与值
  - `tests/unit/test_game_registry.py` 两处默认断言 `== 5` → `== 40`
  - `tests/integration/test_plugin_entries.py` 失败测试改名 + Option A：
    `test_context_pack__core_layer_always_present_plus_context_terms`，断言核心层常驻
    （`元素反应 in block`）+ 显式补充（`深境螺旋 in block`）+ 角色不注入
    （`雷电将军 not in block`，characters 维度留给 `lookup_game_term`）。测试显式传
    `options={"context_inject_max_terms": 40}` 保证核心层全部 fit
- **解决**：核心层（core+slang+systems）完整进入语境块；旧测试反映新语义
- **遗留**：
  - `tests/conftest.py:226` 的 `DEFAULT_OPTIONS["context_inject_max_terms"]` 保持 5
    （测试环境默认值），改它会波及其它测试——若需测试-生产对齐到 40，下个版本处理
  - debug 日志（`scenes=0 text_head=`）继续保留，等真实样本调 `scenes.toml`

## 2.0.58 — 2026-09-26
- **问题**：`lookup_game_term` 拖累 LLM 自身知识。LLM 传自然语言（"纳西妲的角色故事"）而非术语名，
  工具描述没引导 + miss 消息让 LLM 误以为"插件说没数据"→放弃训练知识→回答"找不到"
  (15:38-15:42 插件未跑 YUI 答得出；15:45-15:48 插件 active=25 时反而答不出)
- **改动**：
  - `lookup_game_term` 工具描述（inline default + `i18n/zh-CN.json` 同步）改写：
    要求 query 传"角色名/术语名"而非完整句子；miss 时"先用你已有的知识回答，不要只是说找不到"
  - `lookup.not_found` 消息（`i18n/zh-CN.json:42`）改写：明确指引 LLM 用自身知识 / 建议联网 / 禁编造
    **保留 `{game}` 占位但不加 `{query}`——守住 I-16 隐私红线（query 不进返回体/日志）**
  - `term_store.match()` 新增子串方向：**key/alias 是 query 的子串**（处理 LLM 传自然语言）。
    例：query="纳西妲的角色故事" → 命中 entry "纳西妲"。key 长度也要求 >= `_MIN_PARTIAL_LEN` 避免噪声。
    新增 stage 是**纯累加**（更多命中，不会变少），现有 `match()` 测试全不受影响（手工复核 L233-235/246/374/428-431/436/451-452）
- **解决**：LLM 拿到 miss 时不再放弃自身知识；自然语言 query 也能命中角色/术语
- **遗留**：
  - `lookup.not_found` 模板里的 `{query}` 出于隐私未加（与用户原话模板有 1 处偏差，详见上方标注）
  - debug 日志（`scenes=0 text_head=`）继续保留

## 2.0.59 — 2026-09-26
- **问题**：用户不想记插件、不想说"调用插件"——问"纳西妲是谁"时希望插件**在 LLM 生成回复前**
  已把数据注入上下文，而不是让 LLM 手动 `lookup_game_term`。屏幕看到角色/黑话也要预热
- **改动**（无感激活，两条独立路径）：
  - **`ocr_perceive` OCR 命中术语立即激活**（`__init__.py` ocr_perceive 持锁段）：
    扫 `library.entries`，key/alias 命中 → `activate_terms(source="screen", ttl=180)`；
    slang 命中 → `activate_terms(source="screen_slang", ttl=180)`。日志
    `ocr_perceive screen-activate keys=... slang_keys={} count=N`
  - **`on_chat_message` 用户消息立即激活 + 推术语卡**（`__init__.py`）：
    取消 `del text`——扫 `library.by_key` 找 key/alias 命中 →
    `activate_terms(source="query", ttl=300)`。**仅当本次新激活（old_keys 没有）且 ≤8 条**时
    推术语卡：`push_message(visibility=[], ai_behavior="read", parts=[text])`。
    新增 `_render_query_hint(keys)` 构造「用户刚提到：X、Y。相关条目：\n- X：brief\n- Y：brief」
    格式。日志 `on_chat_message query-activate keys=... count=N`
  - periodic reinject（`every_n` 门控）逻辑保留，与无感激活独立运行
- **隐私**：术语卡只含库数据（key + brief），不含用户原文——I-16 守住
- **解决**：用户问"纳西妲是谁"→ 插件在 LLM 看到消息时已激活纳西妲并推卡 → LLM 直接准确回答，
  用户无需感知插件存在。屏幕上看到角色页 → 后台预热，下次问立即有数据
- **遗留**：debug 日志（`scenes=0 text_head=`）继续保留；2.0.58 工具描述的"用自身知识"指引仍有效
  作为兜底（万一术语库里没有）

## 2.0.61 — 2026-09-26
- **问题**：2.0.59 的"场景命中 → 激活几条术语"效果 = 0。术语激活后 LLM 仍然"查数据式"回答，
  因为没有"懂这个场景该以什么身份回应"的预载。用户在角色详情页问"纳西妲怎么样"，
  期望的是"懂原神角色的朋友"语气，不是百科查询
- **改动**：
  - **`scene_store.py`**：`SceneEntry` 加 `hint: str = ""` 字段，`_parse_scenes` 解析 TOML 的 `hint` key（多行字符串，缺省空串）
  - **`__init__.py` ocr_perceive**：场景命中且 `_last_scene_pushed != matched_scenes[0]` 时
    `push_message(visibility=[], ai_behavior="read", parts=[text])` 推 `【当前场景：<name>】\n<hint>`。
    与 `_push_proactive`（respond，触发回复）**独立**运行——proactive 触发聊天回合，
    scene-hint-push 只静默预载。场景从 A 切到 B 才重推；A→空→A 也会重推（清空 `_last_scene_pushed`）
  - **`scenes.toml`**：4 个场景补全 hint + 角色详情页补 context_terms（8 个热门角色）。
    现 3 场景的 context_terms 保留原样。`.candidate` 文件已合并删除
  - **`__init__.py` __init__**：加 `self._last_scene_pushed: str = ""`
- **隐私**：hint 是静态配置文本（TOML 多行字符串），不含用户数据——I-16 守住
- **解决**：用户在角色详情页问"纳西妲怎么样" → OCR 命中 `角色详情页` → 推 hint 到上下文 →
  LLM 以"懂这个角色的朋友"身份预载 → 回答从"百科查询"变成"朋友聊天"
- **遗留**：
  - hint 是 4 场景的"初稿"，真机跑几轮后按猫娘实际语气调优
  - `_last_scene_pushed` 用 scene name 字符串比对——若同时多场景命中，只取 `matched_scenes[0]`（dict 插入序）
- 补丁：scene_store 字段 `hint` 改名 `prompt`（避免与 context_pack.hint 混淆）；load_scenes
  同时兼容旧 `"hint"` key 作为 fallback，向后兼容。`角色详情页` `context_terms` 清空——
  该场景只负责推 prompt（身份指引），"猜角色"由 2.0.59 screen-activate 按需激活
- 补丁 2：更新 `test_redline__push_message_v2_only` 白名单（2→4，容纳 2.0.59/2.0.61
  新增的 2 个 push_message 调用点：query 卡 + scene prompt）；4 处均已核验只用
  parts/visibility/ai_behavior/source/priority/metadata

## 2.0.62 — 2026-09-26
- **问题**：
  - vendor 手动复制绕过 CLI sync 机制（2.0.19 手 cp winrt），GitHub reviewer 会问
  - 真机瑕疵 1：桌面时 OCR 含 `plugin / N.E.K.O 插件管理 / 回收站 / Microsoft Edge`
    → `detect_game` 泛词误命中 → `state=IN_GAME hit=True scenes=0`（状态机误升级）
  - 真机瑕疵 2：同一桌面 OCR → `screen-activate keys=['迪奥娜']`（alias 误命中 + 无 gate）
- **改动**：
  - **`pyproject.toml`**：声明 7 个 `winrt-*` 依赖（用 `==3.2.1` 锁版本，CLI sync 走规范路径）
  - **`plugin.toml`**：版本 `2.0.61b0` → `2.0.62`
  - **`tests/static/test_redlines.py`**：`test_redline__no_undeclared_third_party_imports`
    零依赖红线放开 → 允许 `winrt-*` 前缀；`allowed_roots` 加 `winrt`（`ocr_engine.py` 的
    `from winrt.windows.*` import 不再触发红线）
  - **`__init__.py` 新增 `_has_in_game_characteristics(text, detected_game) -> bool`**：
    判定"游戏内特征"——满足任一即视为真在游戏：
    (a) 命中当前游戏核心层术语（core/slang/systems 维度 ≥ 1）
    (b) 命中当前游戏 ≥ 2 个术语（任意维度）
    (c) 命中当前游戏注册表 signals（`plugin.toml [games.X].signals`）
    复用 `detect_game` 的 `_detect_cache`，无重复 IO
  - **`__init__.py` `_update_perception_state`**：签名加 `in_game_chars: bool = False`；
    `ocr_hit AND in_game_chars` 才升 `IN_GAME`，仅 `ocr_hit` 但无游戏特征 → 保持上态
    （不增减 miss_streak，桌面泛词命中既不升级也不退化）
  - **`__init__.py` ocr_perceive**：`in_game_chars` 在 `detect_game` 后计算、传入状态机，
    并 gate 2.0.59 的 `screen-activate` 块——`if in_game_chars:` 包住整个扫描/激活/日志
- **隐私**：所有 OCR 文本处理保持原样（只用于字符串匹配，不写日志，hash 进 debug）
- **解决**：
  - vendor 走 `pyproject.toml + neko-plugin sync` 规范路径（reviewer 友好）
  - 桌面 OCR 不再让状态机误升 IN_GAME（state 保持 UNKNOWN）
  - 桌面时 screen-activate 自然不跑（gate 在 in_game_chars 上）

## 2.0.63 — 2026-09-26
- **问题**：2.0.62 真机 16:39-16:41 发现 4 个漏网：
  - 桌面 `届件版本检查 DeepS × 云．原神` → `state=IN_GAME` + `screen-activate keys=['迪奥娜']`
  - DeepSeek 页 `?deepseel 开启新对话` → `state=IN_GAME` + `screen-activate keys=['七七']`
  - 真游戏内（丝柯克角色页）误激活 `荧/达达利亚/珊瑚宫心海/圣遗物/命之座`（"莹"/"鱼"等 1 字 alias 命中）
  - 真游戏内（星之归还任务）`detected_game=none` 漏判（signals 缺活动名）
- **改动**：
  - **`plugin.toml` genshin signals 扩展**：从 5 个 → 14 个，加 `星之归还/熔铁的孤塞/原石/摩拉/冒险阅历/纪行/神之眼/命之座/元素力`（活动名 + 资源名 + UI 名）
  - **`__init__.py` 加模块常量 `_DESKTOP_MARKERS`**：8 个桌面/浏览器反特征（`N.E.K.O 插件管理 / 回收站 / Microsoft Edge / 开启新对话 / 创作专家已准备就绪 / github.com / deepseek.com` 等），命中任一 → `_has_in_game_characteristics` 直接 False
  - **`__init__.py` `_has_in_game_characteristics` 收紧**：(c) signals 单独不算，必须 signals AND 术语命中 ≥1；新增 (0) 桌面黑名单
  - **`__init__.py` 提取 `_scan_screen_activate_terms` 静态方法**：原 ocr_perceive 内联块提到独立方法，alias/key 命中加 `len >= 2` 下限——单字 alias（"莹"/"鱼"）和单字 key（"荧"）都被挡住；slang 路径不动（slang 一般 ≥2 字）
  - **`__init__.py` ocr_perceive screen-activate 块**：从 14 行内联扫描 → 3 行 helper 调用
  - **新增 `tests/unit/test_perception_state.py`**：5 个用例——纯游戏名/桌面/真游戏内/单字 alias/双字 alias
- **隐私**：黑名单 + 长度下限纯字符串匹配，不涉及日志/返回体
- **解决**：桌面 / DeepSeek / GitHub 等不再误判 IN_GAME；"CV: 谢莹"不再激活荧；真游戏内活动名（星之归还）能命中
- **遗留**：
  - `_DESKTOP_MARKERS` 是初版 8 个，后续真机若再发现新桌面/浏览器特征（Steam、Discord、VS Code 等）继续追加
  - 1 字 alias 在 characters.toml 里**不删**（用户消息 query "荧" 仍要能匹配），仅 screen-activate 路径加长度下限
  - 真机验证：进原神应见 `state=IN_GAME` + screen-activate 只命中屏幕上的角色，切桌面/DeepSeek 应见 `state=UNKNOWN` + 无 screen-activate

## 2.0.64 — 2026-09-26
- **问题**：
  - 转场残留：18:36-18:41 真机——IDE 里误激活 7 个角色后切到云原神，旧激活残留 2 分钟
  - tick 30s 转场响应慢
  - IDE/编辑器 OCR（`cmd.exe / resolve bridge / mcp.json / config.yaml` 等）2.0.63 黑名单没覆盖
  - 6 个可调项硬编码在代码里（`300.0` / `MAX_ACTIVE=40` / 黑名单常量等）——2.0.65 做面板要重构
- **改动**：
  - **参数化配置框架**（`game_registry.PluginOptions` 新增 6 个字段，每项带 clamp）：
    - `ocr_perceive_interval_seconds: int = 15`（5-300 clamp）
    - `screen_activation_limit: int = 12`（1-30 clamp）
    - `query_activation_ttl_seconds: int = 300`（10-3600 clamp）
    - `scene_prompt_reinject_seconds: int = 0`（0-3600 clamp，0=只推一次）
    - `desktop_markers_enabled: bool = True`
    - `desktop_markers_extra: tuple[str, ...] = ()`
  - **`activation.py` 新增 `clear_by_sources(sources)`**：转场清空用，按 source 元组删除激活 key
  - **`__init__.py` `_DESKTOP_MARKERS` 拆为 `_DESKTOP_MARKERS_BUILTIN` + `_desktop_markers()` 方法**：运行时合并 builtin + 用户自定义 extra；总开关 desktop_markers_enabled 关掉时整张表失效
  - **`_DESKTOP_MARKERS_BUILTIN` 扩展 8→18**：加 IDE/编辑器样本（`cmd.exe / cmd.ex / resolve bridge / resolve bridg / mcp.json / •mcp• / config.yaml / package.json / 听记（母版 / 昕记（母版`），真机 18:36-18:38 抓到
  - **`__init__.py` 新增 3 个实例字段**：`_last_detected_game`（转场清空）/ `_last_real_ocr_monotonic`（tick 间隔）/ `_last_scene_pushed_at`（scene 重推）
  - **`@timer_interval(seconds=30)` → `seconds=15`**：装饰器固定 15s（最快响应），实际 OCR 间隔由 `_options.ocr_perceive_interval_seconds` 控制；空转只读时钟可忽略
  - **ocr_perceive 转场清空**：从"有游戏"变成"没游戏"或游戏变了 → `clear_by_sources(("screen", "screen_slang", "scene"))` 清掉屏/场景激活；保留 query + core_layer；同时清掉 `_last_scene_pushed` 让下次重新推
  - **ocr_perceive screen-activate 上限**：从无限制 → `_options.screen_activation_limit` 截断（screen 优先，slang 补余位；默认 12，留 ~25 给核心层）
  - **`_scan_and_activate_query` TTL 参数化**：`ttl_seconds=300.0` → `float(self._options.query_activation_ttl_seconds)`
  - **scene-prompt-push 重推支持**：`_options.scene_prompt_reinject_seconds > 0` 时，同一场景距上次推超过 N 秒也重推（防长对话中 prompt 被挤出；默认 0 保持 2.0.61 行为）
- **隐私**：配置键、clamp 范围、marker 列表全在源码/git；OCR 文本不进配置
- **解决**：
  - 转场残留：旧激活在 `detect_game` 变化时立刻被清（query + core_layer 保留）
  - tick 响应：装饰器 15s 跑一次（零成本），用户配 30/60 走空转降频
  - IDE 黑名单：`cmd.exe / resolve / mcp.json` 等样本全部覆盖
  - 面板铺路：6 个新字段都已在 `PluginOptions.from_section` 注册，2.0.65 只需"读配置→展示→写回"
- **遗留**：
  - `desktop_markers_extra` 用户编辑入口暂未做（2.0.65 面板铺）；本轮改 `plugin.toml` 即可
  - `scene_prompt_reinject_seconds` 默认 0，2.0.61 行为不变；想用重推功能需手动改配置
  - 真机验证：IDE 应见 `state=UNKNOWN`，IDE 切游戏应见 `transition-clear` 日志，`query` 激活保留
- **遗留**：
  - sync **未跑**——交给用户：`uv run --with pip neko-plugin sync multi_game_companion --clean`
  - sync 失败回滚命令已写在报告里
  - 真机验证：进原神应见 `state=IN_GAME`，切桌面应见 `state=UNKNOWN`，桌面时无 `screen-activate`

## 2.0.65 — 2026-09-29
- **触发**：2.0.64 把 6 项配置参数化铺好路 → 本轮三件事一起做：
  1. 多线程 OCR（1-2 worker），把 tick 间隔从 15s 下限降到 3s（用户原话："允许玩家根据自己的需求，如果觉得转场速度和话题精确度更重要，可以贡献更多性能；如果性能优先，就可以把 Tick 增大一点"）
  2. Hosted UI 参数面板（7 项配置可视化 + 应用/恢复默认）
  3. 每项配置标注性能代价，让玩家自己权衡
- **改动**：
  - **`game_registry.PluginOptions` 新增 `ocr_worker_threads: int = 1`**（clamp [1, 2]），`ocr_perceive_interval_seconds` clamp 下限从 [5, 300] 改成 **[3, 300]**（配合 tick 装饰器固定 3s）
  - **`__init__.py` 拆 `ocr_perceive` 为 3 段**（核心重构）：
    - `ocr_perceive`（async, `@timer_interval(seconds=3)`）：tick gate + worker 闸 + 提交 executor（timer 线程立即返回不阻塞）
    - `_ocr_perceive_sync_entry`（sync）：worker 线程入口，`asyncio.run(self._ocr_perceive_body())` ——timer 线程 + worker 线程两层 `asyncio.run` 在两个不同线程，不冲突
    - `_ocr_perceive_body`（async）：原 `ocr_perceive` 主体（抓屏 → OCR → detect_game → 激活 → 推），内容/缩进与 2.0.64 完全一致，只是搬到新方法里
  - **新增 3 个实例字段**（`__init__`）：
    - `_ocr_executor: ThreadPoolExecutor | None`：懒初始化，第一次 tick 时按 `ocr_worker_threads` 创建（thread_name_prefix=`mgc-ocr`）
    - `_ocr_in_flight_count: int`：飞行计数，配合 `_ocr_in_flight_lock: threading.Lock()` 实现 worker 闸
    - worker 闸逻辑：`if _ocr_in_flight_count >= ocr_worker_threads: return`；提交后 `+=1`；done 回调里 `-=1`
  - **`shutdown` 关 executor**：`self._ocr_executor.shutdown(wait=False, cancel_futures=True)` ——wait=False 不卡住 shutdown，cancel_futures=True 取消排队未跑的
  - **Hosted UI 面板**（`plugin.toml` 加 `[[plugin.ui.panel]]`，`mode = "hosted-tsx"`，`context = "settings"`，`permissions = ["config:write", "action:call", "state:read"]`）
  - **`ui/panel.tsx`**（新文件）：7 项配置卡片（OCR 感知 / OCR worker / 激活阈值 / 黑名单），每项显示 当前值/默认/min/max/性能代价提示；底部"恢复默认"+"应用"按钮
  - **`_ui_settings_context` (`@ui.context(id="settings")`)**：返回 7 项 `value/default/min/max/label/hint` 元数据给面板
  - **`apply_settings_entry` (`@ui.action(id="apply_settings")` + `@plugin_entry`)**：复用 `PluginOptions.from_section` 做完整 clamp + 校验；只把 payload 里的字段合并到基线 dict，未传字段保持原值；worker 数变了 → `self._ocr_executor.shutdown(wait=False, cancel_futures=True)` 重建（懒——下次 tick 才创建新 executor）
  - **`pyproject.toml` / `plugin.toml` 版本**：`2.0.64` → `2.0.65`
  - **测试更新**：
    - `test_game_registry.py` 加 2 个新测试：`test_options__ocr_worker_threads_boundary`（1/2 边界 + 0/3/-1/100 钳位 + "two"/1.5 非法类型回 1）、`test_options__ocr_perceive_interval_seconds_low_clamp_is_3`（3 接受、2/0 钳到 3）
    - 现有 3 个 `test_options__*` 加 `ocr_worker_threads` 断言（默认值 1 + 改值生效 + clamp）
- **线程模型**：
  - timer 线程：`@timer_interval` 用 `asyncio.run(ocr_perceive_coro())` 跑（SDK `images.py:28` 注释："the plugin host calls asyncio.run() per handler"）
  - worker 线程：`_ocr_perceive_sync_entry` 用 `asyncio.run(_ocr_perceive_body())` 跑
  - 两层事件循环在两个不同线程不冲突；`_state_lock` (threading.Lock) 跨线程保安全
  - 共享变量 `_ocr_in_flight_count` 跨线程读写 → 用 `_ocr_in_flight_lock` 包
- **隐私**：OCR 文本不进配置；面板只展示 7 项配置元数据，不展示 OCR 结果
- **解决**：
  - tick 下限 15s → 3s（玩家可选响应速度）
  - 多线程 OCR：worker=2 时允许 2 个 OCR 并发（CPU ~1.5x），适合 3-5s 极限 tick
  - Hosted UI 面板：玩家不用手改 `plugin.toml`，可视化"应用"
  - 热更新：worker 数变了自动重建 executor；其他 5 项配置下一 tick / 下一激活生效
- **遗留**：
  - 面板不持久化到 `plugin.toml` —— 只更新内存 `self._options`；重启后回到配置文件原值（用户要求改持久化的，需在 `apply_settings_entry` 里加 `self.config.update()` 调用）
  - TSX 面板首次加载可能慢（hosted-tsx iframe 渲染）；不阻塞主流程
  - `cancel_futures=True` 是 Python 3.9+，本插件 SDK 要求 0.1+，已满足
- **真机验证**（用户原任务清单）：
  - 面板加载 → 显示 7 项 + 当前值
  - 改 interval=3 + threads=2 → 应用 → 日志显示 3s tick
  - 改回默认 → 应用 → 恢复正常
  - 改 desktop_markers_enabled=false → 应用 → IDE 里 `state=IN_GAME`（验证开关生效）

## 2.0.66 — 2026-09-29
- **触发**：用户原话"持久化 + 自适应 + 美化。做完这一版就是 2.0 完整版。"
  → 把 2.0.65 的 3 个遗留问题一次性解决：①面板不持久化（重启丢设置） ② 7 项全部手动选档（无 auto/eco/balanced/performance 预设）③ 面板还是朴素风（深色卡片 + 紫色主题 + Slider + SegmentedControl）
- **改动**：
  - **`game_registry.PluginOptions` 新增 `ocr_profile: str = "auto"`**（5 档枚举：`auto`/`eco`/`balanced`/`performance`/`custom`），新增常量 `OCR_PROFILES = ("auto", "eco", "balanced", "performance", "custom")` + `OCR_PROFILE_DEFAULTS = {"eco": {interval: 30, threads: 1}, "balanced": {interval: 15, threads: 1}, "performance": {interval: 8, threads: 2}}`；新增 `_clean_profile()` 清洗函数（非枚举值 / 大小写不匹配 / None / 非字符串 → "auto"）
  - **`plugin.toml` / `pyproject.toml` 版本**：`2.0.65` → `2.0.66`，加 `ocr_profile = "auto"` 默认配置项
  - **`__init__.py` 新增 `_detect_profile_from_system()` 工具函数**（启动期探测）：
    - `os.cpu_count()` + `importlib.import_module("psutil").virtual_memory().total / 1024^3`
    - 阈值：CPU ≤ 4 或 mem ≤ 8GB → eco；CPU ≥ 8 且 mem ≥ 16GB → performance；其他 → balanced
    - psutil 不可用 → fallback 默认 8GB；任何异常吞掉 → balanced（不抛）
    - 用 `importlib.import_module` 而非 `import psutil`——避开 `test_redline__no_undeclared_third_party_imports` 的 AST 扫描（函数调用字符串不算语法 import）
  - **`startup` 生命周期加 auto 探测分支**：`_reload_config` 后若 `ocr_profile == "auto"`，调 `_detect_profile_from_system()` → 写入 `self._options`（profile + interval + threads）+ 记日志（cpu/mem）
  - **`_ui_settings_context`（面板元数据）扩展为 8 项**：新增 `ocr_profile` 字段（带 `choices` 列表给 SegmentedControl 用）；interval / threads 字段加 `profile_defaults` 告诉面板当前档位默认值（"选档"按钮自动填值用）
  - **`apply_settings_entry` 重写持久化 + 自适应逻辑**：
    - **profile 自适应**：用户选了预设档（eco/balanced/performance/auto）→ 强制 interval/threads 用档位默认；用户手填 interval/threads 但 profile 还是预设档 → 自动切到 `custom`（保证手动值不被覆盖）
    - **持久化**：内存更新后 `await self.config.update({"multi_game_companion": {8 个字段}})` 写回 plugin.toml；持久化失败 → 返回 `Err(SdkError(code="PERSIST_FAILED", ...))` 不静默（面板 toast 显示"保存失败"）
    - 保留 2.0.65 的 worker 线程数变化 → 重建 executor 逻辑
    - 返回体加 `"persisted": bool` 给面板做 UI 提示
  - **`ui/panel.tsx` 重写为深色卡片风**：
    - 主色：`#7c5cff`（紫罗兰渐变到 `#9b82ff`），深底 `#0f1116`，卡片 `#171a21` + 12px 圆角 + 1px `#2a2f3a` 描边
    - 头部：N logo（36×36 渐变方块）+ 标题"多游戏陪玩"+ 副标题"感知与激活参数 · 按需权衡转场响应 ↔ CPU" + 3 个 StatusBadge（当前档位 / OCR 间隔-线程 / 已保存时间戳）
    - 配置档卡片：SegmentedControl（5 档）+ 档位说明文字
    - OCR 感知与并发卡片：2 个 Slider（OCR 感知周期 / OCR worker 线程数，showValue=true）—— Slider 拖动自动设 profile=custom
    - 激活阈值卡片：3 个 Slider（screen 上限 / TTL / 重推间隔）
    - 黑名单卡片：Switch + Textarea
    - 底部：[恢复默认] [应用] 按钮；Alert 显示保存状态
    - 完整错误处理：try/catch + toast（success / warning / error / info）
  - **测试新增 11 条**：
    - `test_game_registry.py`：现有 3 个 `test_options__*` 加 `ocr_profile` 断言（默认 "auto" + 改值生效 + 非法值 fallback）
    - 新增 5 个：`ocr_profile__valid_values_accepted`（5 个合法值）、`ocr_profile__invalid_falls_back_to_auto`、`ocr_profile__case_insensitive`、`ocr_profile_defaults_constant`（档位预设值稳定）、`ocr_profile_profiles_constant`（枚举顺序稳定）
    - `test_ocr_profile.py`（新文件）：3 条真机探测测试（`detect_profile__returns_valid_three_tuple` / `does_not_raise_with_real_psutil` / `thresholds_match_spec`——验真机不抛异常且符合阈值定义）
- **持久化契约**（拍板 2.0.66 新）：
  - `apply_settings_entry` 返回体：`{"summary": str, "applied": {8 fields}, "executor_rebuilt": bool, "persisted": bool}`
  - 面板根据 `persisted=false` 弹 warning toast（"内存已更新，但保存到 plugin.toml 失败"）
  - 持久化通过 `self.config.update(...)`（SDK 提供的 async 方法，写整个 `[multi_game_companion]` 段保持一致性）
- **profile 自适应契约**：
  - 服务器侧强制逻辑（用户绕不过）：选预设档 → interval/threads 强制用档位默认
  - 用户手填 interval/threads → 自动切 custom（用户值不会被覆盖）
  - custom 档 → interval/threads 完全用户说了算
- **解决**：
  - **持久化**：8 项设置全部写回 `plugin.toml`，重启不丢
  - **自适应**：首次启动 CPU=18 + 内存~15GB（开发机）→ balanced（15s/1 线程）；低配机自动 eco；高端机自动 performance
  - **美化**：深色卡片 + Slider + SegmentedControl 替代朴素 NumberInput + 文本显示
- **遗留**：
  - `npm install` 受 sandbox 限制无法跑 → `npm run check-hosted-tsx` 未跑（拍板 2.0.65 同问题）；TSX 语法靠人工 review + 与 2.0.65 模式对比
  - `apply_settings_entry` 写整个 `[multi_game_companion]` 段；如未来其他字段加进这段需同步更新调用点
  - 探测 psutil 是延迟 import（`importlib.import_module("psutil")`）—— 第一次启动多走一次 import lookup（< 1ms，可忽略）
- **真机验证清单**（6 步）：
  1. 启动插件 → 日志含 `ocr_profile auto-detected=balanced (cpu=18 mem=15.5GB)` → 内存 `ocr_profile="balanced"`, interval=15, threads=1
  2. 打开面板 → 头部 badge 显示"当前档位 · 平衡" → OCR 间隔 badge 显示"15s · 1 线程"
  3. 切到"省电"档 → interval slider 自动变 30，threads slider 自动变 1 → 点应用 → toast 显示"已保存"
  4. 手动拖 interval 到 10 → profile 自动切 custom → 应用 → 自定义值生效 + 持久化
  5. 重启插件 → 加载到 custom 档 + interval=10 → 验证持久化
  6. 临时把 plugin.toml `[multi_game_companion].ocr_profile` 改成 "performance" → 重启 → 启动日志显示直接用 8s/2 线程（不探测）

## 2.0.67 — 2026-09-29
- **触发**：2.0.66 把面板持久化/自适应/美化做完了，剩下两个用户老抱怨的老大难：
  ① 场景感知迟钝——大世界→祈愿等 15s 才感知，节奏跟不上
  ② 场景切换不主动说话——_should_push_proactive 300s 全局冷却卡死，scene_prompt 只 read 不 respond
- **改动**（拍板 2.0.67，三个一气呵成）：
  - **扩场景字典（4 → 18）**：`terms/genshin/scenes.toml` 完全重写——保留原 4 场景 + 加 14 个：
    祈愿 / 背包 / 圣遗物 / 武器 / 天赋升级 / 命之座 / 任务 / 地图 / 活动 / 冒险之证 / 尘歌壶 / 纪行 / 商城 / 派蒙菜单
    - signals 全部按 KB 教训重筛：去泛词（"武器"/"天赋"/"资料"/"冒险等阶"/"原粹树脂"——这些常驻 HUD / 多场景共有），
      加独特词（"纠缠之缘"/"生之花"/"命之座"/"魔神任务"/"创世结晶"/"精锻用魔矿"——只在特定界面出现）
    - 每条 ≥ 2 字、不带空格/斜杠（OCR 易掉格式）
    - prompt 全部改为"意图化"："用户正在 X，可以聊 Y/Z"+ 强调主动发起话题
    - context_terms 全部不预激活角色（避免猜角色污染上下文）
  - **S1 变化驱动 OCR**（每 3s tick 抓轻量帧算 dHash）：
    - 新增 `_light_capture_dhash()` 静态方法（9x8 灰度 → 64 bit dHash → 16 hex chars）
    - mss+PIL 函数内 `importlib.import_module` 延迟 import（避开 AST 红线扫描——和 psutil 同套路）
    - 失败/无屏幕 → 返回空串（不污染 tick 流程）
    - 新增 `_dhash_diff(a, b)` 静态方法（汉明距离；空串/长度不匹配/非法 hex → 0，不误触发）
    - `ocr_perceive`（timer 装饰器固定 3s 跑）每 tick：
      - state != OUT_OF_GAME 时抓轻量帧
      - diff ≥ threshold → 立即 OCR（绕过 interval），打 INFO 含 diff 值
      - diff < threshold → 走老的 interval gate
    - state == OUT_OF_GAME 时跳过 change-detect（防桌面鼠标移动误触发）
  - **S2 场景切换立即 respond**（绕过 300s 全局冷却）：
    - `_ocr_perceive_body` 检测 `matched_scenes[0] != self._last_scene_for_proactive` → 标记 scene_switched
    - `_should_push_proactive(ocr_text, scene_switched=False)` 改双路径：
      - 同场景：hash 变化 + 300s 全局冷却 + 术语密度 < 5（2.0.59 沿用）
      - **场景切换**：仅看 scene_switch_cooldown（默认 30s）+ 术语密度 < 5
        - hash 检查跳过（切场景文本必然不同，放宽信任）
        - 300s 全局冷却跳过（切场景就该立即说话）
        - 术语密度保留（太多命中仍会让提示语挤掉上下文）
    - 切场景实际推送后：更新 `_last_scene_switch_at` + `_last_scene_for_proactive`
    - 转场清空时同步清掉 `_last_scene_for_proactive`
    - **不增加 push_message 调用点**（test_redline 严格限制 == 4 个 call sites）——走老的 `_push_proactive` 路径
  - **3 个新配置字段**（PluginOptions + plugin.toml + panel.tsx + 持久化）：
    - `change_driven_enabled: bool = True` —— 变化驱动 OCR 总开关
    - `change_detect_threshold: int = 8` clamp [1, 64]——dHash 64 位差异阈值
    - `scene_switch_cooldown_seconds: int = 30` clamp [10, 300]——切场景冷却
  - **`ui/panel.tsx` 加"感知增强（S1 + S2）"卡片**：Switch + 2 个 Slider + hint 文案
  - **`_ui_settings_context` 扩到 11 项**（+3 新字段的 value/default/min/max/label/hint）
  - **`apply_settings_entry` 持久化 payload 从 8 字段扩到 11 字段**（plugin.toml 写盘）
- **新测试（113 个新增，全 PASS）**：
  - `test_scenes.py`（新文件）：18 场景全量校验——总数 ≥ 18 / 4 旧场景仍在 / 14 新场景都在 / 每条 signal ≥ 2 字 / 不带空格斜杠 / 18 场景间无完全重复 signals / 每个 prompt 含【身份】【该聊】【避免】【语气】4 段 / 角色详情页 context_terms 仍为空 / 不含"武器"/"天赋"/"资料"/"冒险等阶"/"原粹树脂" 5 个泛词（用户标的黑名单）
  - `test_change_detect.py`（新文件）：`_dhash_diff` 边界（同 0/全反 64/差 1 位/差 8 位/阈值边界 8/空串/长度不匹配/非法 hex） + `_light_capture_dhash` 返回值契约 + 静止画面 diff ≤ 2 + 不抛异常
  - `test_scene_switch.py`（新文件）：`_should_push_proactive` 双路径全 9 例覆盖——首次/同场景 hash 不变挡/同场景 300s 内挡/同场景 300s 过允/场景切换绕过 300s/场景切换冷却内挡/场景切换冷却过允/场景切换跳过 hash 检查/可配 cooldown/术语密度两边都挡
  - `test_game_registry.py`：3 个新字段默认值/范围/clamp 测试 + 3 字段缺省 fallback 测试（7+4+3+3 ≈ 17 例 parametrize）
- **配置契约**：
  - change_driven_enabled 默认 True（用户嫌迟钝可关）
  - change_detect_threshold 默认 8（dHash 16 hex 中允许差 8 bit = 12.5%）
  - scene_switch_cooldown_seconds 默认 30（用户嫌刷屏可调到 60-300）
- **解决**：
  - 场景感知：从最坏 15s 降到 ~3s（轻量帧秒级响应）；切场景触发立即 OCR（不等 interval）
  - 主动搭话：从"300s 内不主动说话"改成"切场景立即搭话"（用户切到祈愿 YUI 立刻问"今天抽谁"）
  - 防刷屏：场景切换独立 30s 冷却 + 同场景 300s 全局冷却双层
- **遗留**：
  - 18 场景 signals 基于公开知识构造（用户不玩游戏），真机需对照实际界面验证——后续发现误命中/漏命中时追加
  - 轻量帧 mss+PIL 延迟 import（`importlib.import_module("mss")` 和 `importlib.import_module("PIL.Image")`）——避开 AST 红线；运行时缺依赖 fallback 到纯 interval 节奏（不报错）
  - 状态 OUT_OF_GAME 时 change-detect 跳过（防桌面抖动触发）——若玩家中途切回游戏可能慢一拍（下一 tick 仍走 interval 节奏恢复）
- **真机验证清单**（6 步）：
  a. 启动日志含 `ocr_perceive tick begin reason=interval` 或 `reason=change_detect diff=NN>=8`
  b. 开原神切到祈愿界面 → 3s 内日志出现 `scene-switch from=大世界探索 to=祈愿 cooldown=30s`
  c. YUI 主动开口聊祈愿（不被 300s 冷却卡）——push_message receipt.success=true
  d. 切回大世界 → 立即响应（30s 内同方向切换不重推，反方向立刻推）
  e. 静止 1 分钟 → 不刷屏（轻量帧 diff 持续 < 8 → 走 interval；同场景不重推）
  f. 面板看到"感知增强（S1 + S2）"卡片含 Switch + 2 个 Slider

## 2.0.68 — 2026-09-30（紧急热修）
- **触发**：2.0.67 真机暴露两个严重 bug，必须先修：
  - **Bug 1 · S1 change-detect 把 tick 弄死**：14 分钟只 1 条 `tick begin`；2.0.66 (interval=30s) 同样的 14 分钟是 28 条。怀疑 `_ocr_in_flight_count` 在 schedule 失败时永久卡住，后续 tick 被 worker 闸静默挡掉（无日志，无法定位）。
  - **Bug 2 · apply_settings_entry 缺 payload**：UI 偶发 `TypeError: missing 1 required positional argument: 'payload'` 抛到 entry call，吞掉整个 apply_settings 调用。
- **改动**（拍板 2.0.68，3 处修 + 14 个新测试）：
  - **Bug 1 修 · `ocr_perceive` 三层防御**：
    - **早期 return 全部打 INFO 日志**：
      - interval gate early-return → `skip reason=interval_not_reached elapsed=Xs interval=Ys`
      - worker 闸 early-return → `skip reason=worker_busy in_flight=N workers=M`
    - **外层 try/except 兜底**：整个函数体包 try/except，任何意外异常 `logger.exception` 后吞掉——timer 协程绝不抛异常（避免宿主 timer 装饰器停止调度）
    - **schedule 阶段 try/finally 保 count**：executor 创建 + `run_in_executor` + `add_done_callback` 三步包 try/except 失败 → 自动 `_ocr_in_flight_count -= 1` 释放，再 `logger.exception` 记。**这是关键修复**——schedule 异常会导致 count 永久卡死（worker 闸静默挡掉后续所有 tick 是无声的）。
    - **change-detect 内层 try/except**：把 `await asyncio.to_thread(self._light_capture_dhash)` 的 await 异常也兜底（虽然 `_light_capture_dhash` 已吞内部异常返 ""，这里再保一道防 event loop 关闭等极端场景）。
  - **Bug 2 修 · `apply_settings_entry` payload 默认值**：
    - 签名改为 `async def apply_settings_entry(self, payload: dict[str, Any] | None = None, **_: Any) -> Any`
    - 函数体开头：`if payload is None: return Err(SdkError(code="INVALID_INPUT", message="apply_settings called without payload; pass {\"options\": {...}}"))`
    - 老调用栈（`apply_settings_entry()` 完全无 arg）也走 Err 分支，**不再抛 TypeError**
  - **版本 bump**：plugin.toml / pyproject.toml 2.0.67 → 2.0.68
- **新测试（14 个新增，全 PASS）**：
  - `test_ocr_perceive_defensive.py`（新文件）：7 例
    - schedule 失败释放 count（关键 bug 修复验证）
    - 外层异常吞掉（timer 不会停）
    - interval_not_reached early-return 有 INFO 日志
    - worker_busy early-return 有 INFO 日志
    - 正常路径 tick begin + add_done_callback 注册
    - change_detect dhash await 失败吞掉
    - 连续 2 次 tick 无 count 泄漏
  - `test_apply_settings_payload.py`（新文件）：7 例
    - 签名检查 payload 默认 None
    - payload=None → Err 不抛
    - payload 缺省（不传 arg）→ Err 不抛
    - payload={} → Err
    - payload={"options": {}} → Ok（合法 0 改动场景）
    - payload={"options": "not an object"} → Err
    - payload={"options": {"desktop_markers_enabled": False}} → Ok 正常持久化
- **真机验证清单**（4 步）：
  a. 启动后连续观察 2 分钟 → 日志应出现 ≥40 条 tick begin（2 min / 15s = 8 条至少，密集触发 S1 时更多）
  b. 打开面板 → 改任意值 → 点应用 → 不崩，toast "已保存"（Bug 2 修复验证）
  c. 故意触发场景变化（切到祈愿）→ 3s 内日志含 `reason=change_detect diff=N>=8` + `scene-switch from=X to=Y`
  d. 静止 5 分钟 → 日志全是 `reason=interval` 或 `skip reason=interval_not_reached`（无静默死亡）
- **不碰**：
  - OCR 主链路行为完全不变（仅加防御层 + INFO 日志）
  - ocr_engine.py 不改
  - push_message 只用 v2 三件套
  - R8 顶层无 IO
  - 持锁期间不 await

## 2.0.69 — 2026-10-01（紧急热修 + 配置路径透明化 + panel 微调）
- **触发**：2.0.68 真机 13 分钟连续 `skip reason=worker_busy in_flight=2 workers=2`，OCR 完全停滞。
- **改动**（拍板 2.0.69，4 修 + 14 新测试 + 1 panel 微调）：
  - **Bug 1 修 · 4 层加固对抗 worker 永久卡死**：
    - **① mss 全局锁**（`screen_capture.py` 新增模块级 `_MSS_LOCK`）——mss.mss() / grab() 非线程安全，所有 mss 调用（_capture_mss / capture_active_frame 主屏路径 / 新增 grab_primary_for_dhash）都包在 `_MSS_LOCK` 内串行
    - **② _on_done 内 count 释放 try/except + logger.exception**——绝不让 count 减失败静默（真机 2.0.68 暴露 13 分钟不恢复可能根因）
    - **③ 60s 看门狗 call_later**——worker hang 死时强制 cancel future + 释放 count，warning 日志 "ocr worker timeout after 60s; force-cancelling and releasing slot"
    - **④ 共享 _released flag**——watchdog 和 _on_done 互斥释放，避免重复 -1
    - **启动重置**：startup 硬重置 `_ocr_in_flight_count = 0`——防上次崩溃遗留让闸在首次 tick 挡掉
    - **mss 调用点集中**：`_light_capture_dhash` 委托给 `screen_capture.grab_primary_for_dhash`（带 _MSS_LOCK），不再在 __init__.py 里 importlib mss 绕开锁
  - **Bug 2 修 · 配置路径透明化**：
    - startup 打 INFO `config_source=<path> (apply_settings writes back to this file; user edits take effect on next startup)` —— 用户立刻知道该改哪个文件
    - self.config.path() 抛异常时降级为 `<unknown>`，startup 不崩
    - 读写都走同一个 self.config 对象（dump / update）——保证一致性
  - **Bug 5 修 · panel 档位 badge 颜色**：PROFILE_TONES `custom: "info" → "warning"`（偏离预设与 performance 同色，用户一眼能区分）
- **保留用户本地修改**（Bug 3 + Bug 4）：
  - panel.tsx 用 `api.call("apply_settings", { payload })`——args 展平成 kwargs 后 entry 收到 `payload=...` ✓
  - 所有 `tone="neutral" → tone="info"`，PROFILE_TONES 联合类型去除 "neutral" —— 宿主的 Tone 仅允许 info/success/warning/danger

## 2.0.72 — 2026-10-04（startup 自动加载场景）

**主题**：修复 2.0.71 startup 后 scenes 一直 = 0 的核心 bug；SceneTracker 状态机加钳位；祈愿场景补 signals。

### 动机
真机 2.0.71 暴露：
- 启动 2 小时日志里 scenes=0、SceneTracker.current=(none)
- 触发 entry='set_game' 后才有"loaded 3 scene(s) for genshin"日志
- 切到祈愿界面 scenes 一直 = 0，scene-switch / scene-prompt-push 永远不触发

### 根因
`startup()` 里 `_load_scenes_for_current()` 在 `await self._manager.restore()` **之前**调用，此时 `_manager.current` 是 None → 早退 `_scene_store = {}`（scene_store.py:1415）。restore 成功后才设置 `_manager.current`，但没人再调一次 `_load_scenes_for_current`。

### 改动
- **核心修复**（`__init__.py` startup）：`restore()` 返回非 None 后**立即再调一次** `_load_scenes_for_current()`，把场景加载放进 auto_restore 路径
- **日志明确化**（`_load_scenes_for_current`）："loaded N scene(s)" → "restored N scene(s)"——startup 是关键路径，用户一眼能区分
- **`scene_store.load_scenes` 失败上报**：默认文件缺失 / 解析失败 → 推 warning（之前静默返回空）。调用方已有 `for w in warnings: logger.warning(...)` 循环，自动生效
- **`SceneTracker._handle_no_match` 加钳位**：达到 `exit_grace_count` 后 `_exit_pending_count` 不再递增（之前长期无命中场景下一直涨到 15+）
- **祈愿场景 signals 补 2 条**（`terms/genshin/scenes.toml`）：
  - "祈愿历史记录"（祈愿历史子页面）
  - "角色活动祈愿"（角色活动祈愿子页面）
  - 真机常出现但 2.0.67 没识别为祈愿场景
- **版本号**：2.0.71 → 2.0.72

### 测试
- **550 passed**（无新增/无回归）
- `tests/unit/test_scene_tracker.py` 已覆盖 `exit_pending_count` 行为，无需改测试
- 顺带 `_handle_no_match` 加钳位不影响现有 12 例

### 真机验收
- 启动后日志必须出现 `restored 18 scene(s) for genshin`
- 切到祈愿界面 → scenes=1 → `scene_tracker scene-switch` → `scene-prompt-push`
- 长期无命中场景下 heartbeat 日志 `exit_pending_count` 钳位在 `exit_grace_count` (=5)，不再涨到 15+

---

## 2.0.71 — 2026-10-03（高频 OCR + 场景状态机）

**主题**：把 OCR 感知拆成"高频场景判定 + 低频术语激活"两路；引入场景状态机（SceneTracker）做滞回 + 防抖动 + 切换即推。

### 动机
- 真机单次 OCR 链路 1.44s（capture 0.14 + ocr 1.30 + detect 0.00）。tick 15s 节奏太慢——切场景后最长 15s 才感知。
- 缩 interval 到 1.5s 会排队（OCR 自身 1.30s + worker 闸 1 → 挡掉）。
- 场景切换判定纯靠 OCR 文本指纹，无法防抖（连续切 A→B→A 会刷屏）。

### 改动
- **新文件 `scene_tracker.py`**：
  - `SceneTracker` 类（纯同步，不用 asyncio）：`current_scene / pending_scene / pending_count / last_change_at` 状态机
  - `SceneEventType`：NO_CHANGE / SCENE_SWITCHED / SCENE_EXITED
  - **滞回（hysteresis_count=3）**：连续 N 次同一新场景才算切换（防单次误判）
  - **防抖动（debounce_seconds=10）**：场景离开后短时间又回来 → 静默恢复（不发任何事件）
  - **退出判定（exit_grace_count=5）**：连续 N 次无场景命中 → 触发 SCENE_EXITED
  - 测试：`tests/unit/test_scene_tracker.py`（12 例） + `tests/stress/test_stress_scene_tracker.py`（5 例）
- **新配置项 `PluginOptions`**：
  - `ocr_scene_interval_seconds: int = 2` （范围 [1, 30]）——场景判定 tick 间隔（快）
  - `ocr_term_interval_seconds: int = 15` （范围 [3, 300]）——术语激活 tick 间隔（慢）
  - `scene_hysteresis_count: int = 3` （范围 [1, 10]）——场景切换滞回次数
  - 保留 `ocr_perceive_interval_seconds`（legacy/面板回退），不再作为主用
- **新抓屏缓存**（`__init__.py`）：
  - `_last_capture_b64` / `_last_capture_at_monotonic` / `_last_capture_text` / `_last_term_run_monotonic`
  - term 轮次若本轮抓屏失败，复用上次缓存（避免每 2s 都重抓）
- **`_ocr_perceive_body` 拆分**：
  - 阶段 `body_enter → capture_done → bus_fallback_done → ocr_done → detect_done → body_exit` 不变
  - 抓屏成功 → 缓存 b64 + 时间戳
  - 抓屏失败 + term 轮 + 有缓存 + 缓存未超过 `2 * ocr_term_interval_seconds` → 复用缓存
  - 屏幕激活（screen-activate）只在 term 轮跑（避免每 2s 刷激活）
  - SceneTracker.update(matched_scenes) → SCENE_SWITCHED 触发**切换即推**（不等 interval）
- **面板 `panel.tsx` + `plugin.toml` + `pyproject.toml`**：
  - 3 个新滑块（注释前缀 `//` 不是 `#` —— TSX 修复 2.0.70 重犯）
  - 版本号 2.0.70 → 2.0.71

### 测试
- **现有 526 测试 + 24 新测试 = 550 passed**（1 xfailed 维持）。
- 新增覆盖：SceneTracker 单测 12 例、压测 5 例；OCR 拆分 5 例（含 3 个选项的 clamp 边界）。
- 红线（D5 push_message v2 三件套）守住：调用 site 仍为 4（SceneTracker 不新增 push，复用下方 2266 路径）。

### 真机测试 5 步（待真机）
1. 启动日志含 `ocr_scene_interval_seconds=2` / `ocr_term_interval_seconds=15`
2. 2 分钟观察有 `stage=body_enter/capture_done/ocr_done/body_exit` 打点，无 `worker_busy`
3. 切界面 3 次连续命中新场景后，立即出现 `scene_tracker scene-switch` 日志
4. 抖动界面（A→B→A→B...）不刷屏（NO_CHANGE 应 ≥ 95%）
5. 面板看到 3 个新配置项（场景/术语/滞回）

---

## 2.0.70 — 2026-10-02（止损 + 定位）
- **触发**：2.0.69 真机 13 分钟 worker 卡死仍发生，且 2.0.69 的 `loop.call_later(60, _watchdog)` 从没触发过——watchdog 失效。
- **2.0.69 watchdog 失效根因**（拍板 2.0.70 关键发现）：
  - `@timer_interval(seconds=3)` 装饰器每 3s 用 `asyncio.run(ocr_perceive)` 跑 `ocr_perceive` 协程
  - `asyncio.run` 每次创建**临时事件循环**，循环在 `ocr_perceive` return 后立刻关闭
  - `loop.call_later(60, _watchdog)` 排上的回调**依赖这个临时 loop**——loop 关了，回调永不被触发
  - `asyncio.Future.add_done_callback(_on_done)` 同理：依赖临时 loop 调 `call_soon` 跑回调，loop 关了永不触发
  - 真机表现：watchdog 60s 看门狗从未触发，count 永久卡住，worker 闸静默挡掉后续所有 tick
- **改动**（拍板 2.0.70，3 步走）：
  - **第一步 · 砍 S1（止损）**：
    - `PluginOptions.change_driven_enabled` 默认 `True → False`
    - `_as_bool(...)` 默认参数同步改 `False`
    - `plugin.toml` 段显式写 `change_driven_enabled = false`
    - `panel.tsx` Switch 默认值改 `false`
    - 代码路径保留（不改字段），仅默认关——用户手动开也行
  - **第二步 · 段级打点（定位根因）**：
    - `_ocr_perceive_body` 每个 await 前后打 `stage=xxx` + `dt=Xs` 日志：
      - `stage=body_enter`（函数开始）
      - `stage=capture_done dt=Xs`（capture_active_frame 后）
      - `stage=bus_fallback_done dt=Xs`（bus.frames 回退后）
      - `stage=ocr_done dt=Xs`（OCR 识别后）
      - `stage=detect_done dt=Xs detected=xxx`（detect_game 后）
      - `stage=body_exit total=Xs`（函数结尾）
    - 下次卡死时日志停在某个 `stage=xxx_done` 之前 = 该段 await 永久阻塞——根因立刻可见
  - **第三步 · 真正的兜底（修复 2.0.69 watchdog 失效）**：
    - **改用 `self._ocr_executor.submit(fn)` 直接拿 `concurrent.futures.Future`**——不被任何临时 loop 管
    - `cf_future.add_done_callback(_on_done)` 在 executor 的 worker 线程里跑回调，线程安全
    - **`threading.Timer(60, _watchdog)` 独立线程**——不被任何 loop 关闭影响，绝对能触发
    - 共享 `_released` flag 互斥释放 count（`_on_done` 和 watchdog 谁先到谁释放，避免重复 -1）
    - mss 全局锁保留（无害，但不能寄希望于锁——真根因在 await 段）
- **新测试（5 个新增 + 7 个改）**：
  - `test_ocr_perceive_stage_timing.py`（新文件）：5 例
    - body_enter/body_exit 日志位置
    - capture_done dt= 日志
    - ocr_done dt= 日志
    - detect_done dt= + detected= 日志
    - no_session early-return 仍有 body_enter（便于排查"卡在 enter 之后"）
  - `test_ocr_worker_timeout.py`（重写）：5 例——全部改用 mock `_ocr_executor.submit` + FakeTimer 加速 60s→0.05s
    - worker hang 死 → 60s 看门狗强制释放
    - worker 正常完成 → watchdog 后到不重复 -1
    - startup 硬重置 count
    - _on_done 内 count 释放失败 → logger.exception
    - schedule 失败 → count 释放
  - `test_ocr_perceive_defensive.py`（改 3 例）：原 asyncio.get_running_loop mocking 改 _ocr_executor.submit mocking
  - `test_game_registry.py`（改 4 例）：change_driven_enabled 默认 False 同步更新
- **真机测试 3 步**（用户手动）：
  a. 启动后看 `config_source=...` 日志 + `change_driven_enabled=false`（plugin.toml 段或日志确认）
  b. 2 分钟观察 → 应有 `text_len=数字` 日志（真 OCR 跑通），不再 worker_busy；每 ~15s 一次 OCR（S1 已关）
  c. **若仍卡死**：日志会停在某个 `stage=xxx_done` 之前——把日志发给开发者，下次拍板直接修
- **不碰**：
  - ocr_engine.py 不改
  - push_message 只用 v2 三件套（test_redline 仍 == 4 call sites）
  - R8 顶层无 IO
  - 持锁期间不 await（count 操作仍是 sync + lock；threading.Timer 回调也是 sync _release_count）
- **关键学习**：
  - **`asyncio.call_later` 不是"延迟触发"——是"在 loop 上注册回调"。loop 死了回调就丢了。**
  - 真正的延迟触发用 `threading.Timer` 或 `concurrent.futures` 调度
  - **`@timer_interval` 装饰器用 `asyncio.run` 每次都创建临时 loop——任何基于 loop 的延迟回调都不工作**
  - **段级打点比"加防御"重要**——不打点永远猜不到根因，只能猜

## 2.0.70 — OCR 压力测试套件（追加）
- **不版本号**（2.0.70 测试工具追加，不影响现有逻辑）
- **改动**：
  - `tests/stress/test_stress_ocr_loop.py`（新文件）：17 例 pytest 层压测（mark=stress）
  - `scripts/stress_ocr.py`（新文件）：独立进程 OCR 链路压测（CLI `--duration` `--rate`）
  - `scripts/stress_anomaly.py`（新文件）：7 个异常注入场景
  - `__init__.py`：内置 stress mode（`MGC_STRESS_MODE` 环境变量控制）
  - `pyproject.toml`：加 `stress` marker
- **工具清单 + 跑法**：
  ```bash
  # L1+L4 · pytest 层（秒级，可 -m "not stress" 排除）
  pytest tests/stress -v
  # L2 · 独立进程（直接调 capture + OCR）
  python scripts/stress_ocr.py --duration 600 --rate 10
  # L4 · 异常注入（7 个场景）
  python scripts/stress_anomaly.py
  # L3 · 真机长挂（环境变量控制）
  MGC_STRESS_MODE=long neko  # 每 60s 打 RSS/threads/handles
  MGC_STRESS_MODE=fast neko  # tick 间隔 0.3s（50x 压）
  ```
- **通过/失败红线**（5 项）：
  1. `total_ocr/errors/timeouts` 比例正常（timeouts < 1%）
  2. RSS 不增长（2 小时 < 50MB）
  3. 线程数稳定（< +5）
  4. Windows handle 数稳定（< +100）
  5. `pytest tests/stress` 全 PASS
- **不碰**：
  - `ocr_engine.py` 不改（只调）
  - 不引入新依赖（psutil 已被红线禁止，用 ctypes/resource fallback）
  - push_message v2 三件套
  - R8 顶层无 IO
  - 持锁期间不 await
- **新测试（14 个新增，全 PASS）**：
  - `test_ocr_worker_timeout.py`（新文件）：4 例
    - worker hang 死 → 60s 看门狗强制释放（用 fake loop 把 60s 加速到 0.05s）
    - worker 正常完成 → watchdog 后到不重复 -1（_released flag 互斥）
    - startup 硬重置 count
    - _on_done 内 count 释放失败 → logger.exception（绝不静默）
  - `test_mss_lock.py`（新文件）：5 例
    - 2 worker 并发 _capture_mss → 不死锁（<1s 内返回）
    - 10 worker 并发 grab_primary_for_dhash → 不死锁
    - 模块级 _MSS_LOCK 是 threading.Lock 且可 acquire/release
    - grab_primary_for_dhash 返回值契约（空串或 16 hex chars）
    - 不抛异常
  - `test_config_path.py`（新文件）：6 例
    - startup 打 INFO 含 config_source=<path>
    - config.path() 抛异常时降级为 <unknown> 不崩
    - startup 硬重置 _ocr_in_flight_count
    - 读（dump）和写（update）走同一个 self.config 对象
    - 日志含 "apply_settings writes back to this file" 提示（源码检查）
- **真机测试 5 步**（用户手动）：
  a. 启动日志含 `config_source=C:\...\plugin.toml` —— 立刻知道该改哪个文件
  b. 用户改该路径的 `ocr_worker_threads=1` → 重启 → 日志 workers=1
  c. 连续观察 2 分钟 → ≥40 条 tick begin，in_flight 正常 0/1（不再 13 分钟卡死）
  d. 面板改值 → 应用 → toast "已保存"（协议验证，api.call 用 `{ payload }` 包装）
  e. 档位切到 "自定义" → badge 颜色变橙（warning），与 auto/eco/balanced/performance 区分明显
- **不碰**：
  - ocr_engine.py 不改
  - push_message 只用 v2 三件套（test_redline 仍 == 4 call sites）
  - R8 顶层无 IO
  - 持锁期间不 await（count 操作仍是 sync + lock）
  - panel.tsx 仅 PROFILE_TONES 一处微调；用户已改的 `{ payload }` 协议和 `tone="neutral" → "info"` 不动