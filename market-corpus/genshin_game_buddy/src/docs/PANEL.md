# 原神游戏搭子 — 参数面板说明

> 拍板 2.0.67：Hosted UI 参数面板文档。每项配置标注**默认值 / 范围 / 性能代价 / 推荐组合**。
> 2.0.66 增补：① 配置档（auto/eco/balanced/performance/custom）+ 启动期自适应探测 ② 持久化（应用后写回 plugin.toml） ③ 深色卡片 + Slider + SegmentedControl UI 美化。
> 2.0.67 增补：④ S1 变化驱动 OCR（轻量帧 dHash）⑤ S2 场景切换立即 respond（绕过 300s 冷却，走独立 30s 冷却）。

## 面板布局（2.0.66）

```
┌──────────────────────────────────────────────┐
│ [N] 原神游戏搭子                       ← logo  │
│ 感知与激活参数 · 按需权衡转场响应 ↔ CPU       │
│                                              │
│ [档位·平衡] [OCR 15s · 1 线程] [已保存·19:40] │
├──────────────────────────────────────────────┤
│ ⚡ 配置档                                     │
│  [自动|省电|平衡|性能|自定义]   ← SegmentedCtrl │
│  auto=启动时探测;eco=30s/1;balanced=15s/1;    │
│  performance=8s/2;custom=自定义               │
├──────────────────────────────────────────────┤
│ 🔍 OCR 感知与并发                             │
│  OCR 感知周期 · 当前 15s    ────●──  [15]     │
│  OCR worker 线程数 · 当前 1   ─●─     [1]     │
├──────────────────────────────────────────────┤
│ 📊 激活阈值（context token 权衡）             │
│  screen-activate 上限          ──●─  [12]    │
│  用户消息激活 TTL              ──●── [300s]   │
│  scene prompt 重推间隔         ●────  [0]     │
├──────────────────────────────────────────────┤
│ 🛡️ 桌面/IDE 黑名单                            │
│  [●━━] 启用桌面/IDE 反特征黑名单   ← Switch   │
│  ┌────────────────────────────────────┐      │
│  │ 用户自定义额外桌面特征（每行一个）    │      │
│  │ Steam                               │      │
│  │ Discord                             │      │
│  └────────────────────────────────────┘      │
├──────────────────────────────────────────────┤
│       [恢复默认]              [应用]          │
│                                              │
│ ✓ 已保存到 plugin.toml · 19:40                │
└──────────────────────────────────────────────┘
```

## 11 项配置详解

### 1. `ocr_profile`（OCR 配置档）— 2.0.66 新增

- **默认**：`"auto"`（首次启动时探测机器性能自动选档）
- **5 档枚举**：
  - `auto`：启动期探测（CPU ≤ 4 或 mem ≤ 8GB → eco；CPU ≥ 8 且 mem ≥ 16GB → performance；其他 → balanced）；**首次启动后被解析为具体档位写入 plugin.toml**
  - `eco`：省电档，`interval=30s`, `threads=1`
  - `balanced`：平衡档，`interval=15s`, `threads=1`（默认）
  - `performance`：性能档，`interval=8s`, `threads=2`
  - `custom`：自定义档，interval/threads 用户说了算
- **阈值定义**（拍板 2.0.66）：
  - `cpu_count <= 4` 或 `mem_gb <= 8` → eco
  - `cpu_count >= 8` 且 `mem_gb >= 16` → performance
  - 其他 → balanced
- **何时选 eco**：笔记本 / 低配 / 续航优先
- **何时选 performance**：高端台式机 / 响应速度优先
- **何时选 balanced**：默认 / 不知道选哪个 / 大多数场景
- **何时选 custom**：知道自己在做什么，需要精确参数
- **自动化契约**：
  - 用户在面板选了预设档（eco/balanced/performance/auto）→ 服务器强制把 interval/threads 设为档位默认（用户绕过无效）
  - 用户拖 Slider 改 interval/threads → 自动切 custom（用户值不被覆盖）
  - 首次启动探测后 `ocr_profile` 从 "auto" 解析为具体档（balanced / eco / performance）写入 plugin.toml，下次启动直接用

### 2. `ocr_perceive_interval_seconds`（OCR 感知周期）

- **默认**：15 秒
- **范围**：[3, 300]
- **控制**：OCR tick 实际间隔。tick 装饰器固定 3 秒跑一次；本值 ≤ 3 时每次 tick 都跑 OCR；> 3 时跳过（空转只读时钟，~30ns）
- **性能代价**：
  - 3s（极限）：CPU 约 10% 单核；转场秒响应
  - 15s（平衡，默认）：CPU 约 2% 单核；转场最坏 15s 感知
  - 60s（省 CPU）：CPU < 0.5% 单核；转场最坏 1 分钟感知
- **何时调低**：高刷新率玩家（FPS ≥ 144）、高频切换场景、对话需要"秒跟进"
- **何时调高**：笔记本 / 低配机器、CPU 紧张、对话节奏慢
- **2.0.66 联动**：手动改此值 → profile 自动切 custom

### 3. `ocr_worker_threads`（OCR worker 线程数）

- **默认**：1
- **范围**：[1, 2]
- **控制**：OCR 并发上限。1 = 串行（每次只跑一个 OCR）；2 = 允许 2 个并发 OCR
- **性能代价**：
  - threads=1：CPU ~baseline（~2% @15s tick）
  - threads=2：CPU ~1.5x（~3% @15s tick；适合 3-5s 极限 tick）
- **何时调高**：选了 performance 档（interval=8s），需要并发跑多个 OCR 才能跑满周期
- **何时调低**：低配机 / 单核机器
- **热更新**：worker 数变了 → 自动重建 ThreadPoolExecutor（旧的 OCR 自然结束、排队被取消）
- **2.0.66 联动**：手动改此值 → profile 自动切 custom

### 4. `screen_activation_limit`（单次 screen-activate 上限）

- **默认**：12
- **范围**：[1, 30]
- **控制**：单次屏幕 OCR 命中后，最多往 context 推几个术语
- **何时调高**：游戏术语量大 / 想让 AI 知道更多背景
- **何时调低**：省 token / 简洁上下文

### 5. `query_activation_ttl_seconds`（用户消息激活 TTL）

- **默认**：300 秒（5 分钟）
- **范围**：[10, 3600]
- **控制**：用户说了一句话后，多久内仍视为"在聊这个话题"（不需要再次 OCR 触发）
- **何时调高**：长对话 / 话题延续久
- **何时调低**：节奏快的多场景切换

### 6. `scene_prompt_reinject_seconds`（scene prompt 重推间隔）

- **默认**：0（只推一次）
- **范围**：[0, 3600]
- **控制**：游戏场景 prompt 每 N 秒重推一次（防止被挤出上下文窗口）
- **0 = 默认**：scene 只在进入时推一次，依赖 LLM 自己的记忆
- **>0**：每隔 N 秒重推（消耗 token 但保 prompt 永远在上下文里）

### 7. `ocr_scene_interval_seconds`（场景判定 tick 间隔）— 2.0.71 新增

- **默认**：`2`（秒）
- **范围**：`[1, 30]`
- **控制**：tick 调度 OCR 的间隔。快 = 场景判定响应快、CPU 高；慢 = 省 CPU、判定慢
- **何时调小**：希望切场景后 ≤ 2s 响应（如剧情过场快速切换）
- **何时调大**：CPU 紧张的机器（≥5s）
- **真机 1.44s 链路**：interval < 1.5s 会排队（worker 闸挡），建议 ≥ 2s
- **注意**：旧的 `ocr_perceive_interval_seconds` 仍保留作为 legacy，但 2.0.71+ 不再使用它

### 8. `ocr_term_interval_seconds`（术语激活 tick 间隔）— 2.0.71 新增

- **默认**：`15`（秒）
- **范围**：`[3, 300]`
- **控制**：屏幕激活（screen-activate）和术语 TTL 续期的间隔。慢 = 减少刷激活；快 = 术语激活更新频繁
- **何时调小**：希望 OCR 命中术语后立即影响上下文（但每 2s 抓屏会刷屏，不推荐）
- **何时调大**：默认 15s 已经够快，30s+ 用于极致省 CPU

### 9. `scene_hysteresis_count`（场景切换滞回次数）— 2.0.71 新增

- **默认**：`3`（次）
- **范围**：`[1, 10]`
- **控制**：SceneTracker 状态机判定"场景切换"需要连续 N 次同一新场景命中
- **何时调小**：场景变化剧烈、希望最快感知（1 = 第一次匹配就切换，可能误判）
- **何时调大**：界面元素不稳定（如战斗中的 UI 闪动），避免误切场景（5-7 次更稳）

### 10. `change_driven_enabled`（变化驱动 OCR 总开关）— 2.0.67 新增

- **默认**：`True`
- **控制**：开 = 每 3s tick 抓轻量帧做 dHash 对比，差异 ≥ threshold 立即 OCR；关 = 走老 interval 节奏
- **何时关**：玩家嫌轻量帧耗 CPU；或机器性能极差（轻量帧 mss+PIL 也吃不消）
- **何时开（推荐）**：场景变化频繁、希望最快感知

### 11. `change_detect_threshold`（变化检测阈值）— 2.0.67 新增

- **默认**：8（dHash 64 位中允许差 8 位 = 12.5%）
- **范围**：[1, 64]
- **控制**：dHash 差异 ≥ 本值就绕过 interval 触发 OCR
- **1**：极敏感（任意 UI 变化都触发，CPU 升 5x）
- **8**：平衡（场景切换 + 跳页都能感知；静止画面不会触发）
- **64**：几乎不触发（≈关闭）

### 12. `scene_switch_cooldown_seconds`（场景切换冷却）— 2.0.67 新增

- **默认**：30 秒
- **范围**：[10, 300]
- **控制**：切场景后多久内不再主动搭话（**绕过** 300s 全局冷却；同场景内仍走 300s）
- **10**：敏感（频繁切都搭话）
- **30**：平衡（默认）
- **300**：几乎不搭话（≈关闭 S2）

### 13. `desktop_markers_enabled`（桌面/IDE 黑名单开关）

- **默认**：`True`
- **控制**：开关桌面/浏览器/IDE 反特征黑名单
- **开（推荐）**：IDE/浏览器/桌面场景不会误判为 IN_GAME
- **关**：可能误识别（玩家在 IDE 里偶尔能看到 IN_GAME 状态）

### 14. `desktop_markers_extra`（用户自定义额外桌面特征）

- **默认**：空列表 `()`
- **类型**：字符串数组，每行一个特征
- **控制**：追加到内置 18 个桌面/IDE 特征串之后；空 = 用内置
- **示例**：`Steam`、`Discord`、`VS Code`
- **何时用**：自己用了个新 IDE / 远程桌面 / 浏览器没在黑名单里

## 面板使用流程

### 应用 → 持久化（2.0.66 新）

1. 用户改任何配置 → 改本地 form state（不立即生效）
2. 点"应用"按钮 → 调用 `apply_settings_entry`（@ui.action）
3. 服务器侧：
   - 校验 + clamp（复用 `PluginOptions.from_section`）
   - profile 自适应（选预设档 → 强制 interval/threads；手填 → 切 custom）
   - 内存更新（`self._options = new_opts`）
   - 持久化（`await self.config.update({"genshin_game_buddy": {8 字段}})`）
   - 如 worker 线程数变 → 重建 ThreadPoolExecutor
4. 面板收到响应：
   - `Ok` → 显示"已保存到 plugin.toml · 时间戳" + 顶部 badge 刷新
   - `Err(PERSIST_FAILED)` → 弹 warning toast"内存已更新，但保存到 plugin.toml 失败（请检查文件权限）"
   - `Err(APPLY_FAILED)` → 弹 error toast 显示错误信息
5. `props.api.refresh()` 重新拉 context → UI 反映新值

### 恢复默认

- 按钮：本地 form 重置为 `state.options` 中的 default（不立即应用）
- 用户点应用后才生效

## 启动期自适应（2.0.66 新）

`startup` 生命周期：

```python
@lifecycle(id="startup")
async def startup(self, **_):
    await self._reload_config()  # 加载 plugin.toml → self._options
    if self._options.ocr_profile == "auto":
        detected, cpu, mem = _detect_profile_from_system()
        defaults = OCR_PROFILE_DEFAULTS[detected]
        self._options = dataclasses.replace(
            self._options,
            ocr_profile=detected,                # auto → 具体档
            ocr_perceive_interval_seconds=defaults["interval"],
            ocr_worker_threads=defaults["threads"],
        )
        self.logger.info(...)
    asyncio.create_task(self._ocr.warmup_async())
    ...
```

- 探测失败（无 psutil）→ fallback 默认 8GB → balanced
- 任何异常 → 吞掉返回 balanced（不阻塞启动）
- 探测后 `ocr_profile` 不再是 "auto"（变 "balanced"/"eco"/"performance"），写回 plugin.toml，下次启动不再探测

## 感知增强 S1 + S2（2.0.67 新）

### S1 变化驱动 OCR

- **目的**：每 3s tick 抓轻量帧（9x8 灰度 dHash），与上一帧对比，差异 ≥ threshold 立即 OCR
- **跳过条件**：state == OUT_OF_GAME（玩家离开游戏很久）→ 不抓轻量帧（防桌面抖动误触发）
- **算法**：
  1. mss 抓主屏
  2. PIL 灰度 + resize 到 9x8
  3. 横向差分：左 < 右 → 1，否则 0（共 64 bit）
  4. 16 hex 字符串
  5. 与上一帧 dHash XOR → bit_count → 差异值
- **决策**：差异 ≥ change_detect_threshold → 立即 OCR（绕过 interval）；否则按 interval gate
- **隐私**：不存帧，只算哈希；失败/缺依赖 → 返回空串（不影响 interval 节奏）

### S2 场景切换立即 respond

- **目的**：玩家从大世界→祈愿时 YUI 立即主动搭话（不被 300s 全局冷却卡）
- **检测**：`matched_scenes[0] != self._last_scene_for_proactive` → 标记 scene_switched
- **冷却**：
  - 场景切换 → 走 `scene_switch_cooldown_seconds`（默认 30s）
  - 同场景内 → 走 300s 全局冷却（沿用 2.0.59）
- **路径**：场景切换路径**跳过** hash 检查（切场景文本必然不同）+ **跳过** 300s 全局冷却；术语密度限制（>5 命中仍挡）保留
- **state machine**：实际推送后更新 `_last_scene_switch_at` + `_last_scene_for_proactive`；转场清空时同步清掉
- **限制**：不增加 push_message 调用点（test_redline 严格限制 == 4 call sites）——走老的 `_push_proactive` 路径

## 视觉设计（2.0.66 新）

- **主色**：紫罗兰 `#7c5cff` 渐变到 `#9b82ff`
- **底色**：`#0f1116`（深色，几乎纯黑）
- **卡片**：`#171a21` + 12px 圆角 + 1px `#2a2f3a` 描边 + 微阴影
- **副底**：`#1f232c`（次级文本框）
- **字色**：`#e6e8ee`（主）/ `#8b94a3`（副）/ `#5d6678`（弱化）
- **状态色**：`#4ade80`（绿/success） / `#fbbf24`（黄/warning） / `#f87171`（红/danger） / `#60a5fa`（蓝/info）
- **字体族**：系统默认无衬线（标题加粗）
- **间距**：8px / 12px / 16px / 24px 阶梯
- **组件**：Slider / SegmentedControl / Switch / Textarea / StatusBadge / Alert / Toast / Button
- **入场动画**：opacity 淡入（禁用 transform 位移——流式防抖）