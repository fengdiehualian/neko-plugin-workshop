# 设计说明 —— source_player

> 目标：让猫娘**真的**玩《半条命》全系列与《传送门》全系列，而不是“看起来在玩”。
> 本文记录架构、关键判断、实测依据和刻意不做的事。

---

## 1. 目标与非目标

### 目标

1. 用户手动打开游戏后，插件能识别是哪一部、窗口是否可用、是否已进入关卡。
2. 在没有 LLM 每个 tick 指挥的情况下，也能持续移动/观察/战斗/互动。
3. 每 25 秒主动把「我看到了什么 + 我判断什么 + 我下一步做什么」推给对话 LLM，
   由猫娘用自己的口吻说出来；LLM 可以改目标或下具体动作。
4. 玩家随时能接手：碰键后在下一次检查（约 0.1–0.4 秒）松手；F8 硬停；说“停手/继续”也有效。
5. 不覆盖存档、不读内存、不作弊、不把按键打进别的窗口。
6. 能通过 N.E.K.O 插件管理导入，并在冻结 Python 3.11 + numpy/Pillow 环境里跑。

### 非目标

* 不做“读游戏内存/自动瞄准/透视”的外挂；
* 不保证 100% 通关，不假装一个脚本能解所有传送门谜题；
* 不自动点主菜单的“新游戏”；
* 不支持 VR（Alyx）和需要第二个玩家的合作模式；
* 不代发 Steam 成就。

---

## 2. 模块

| 模块 | 职责 | 关键点 |
|------|------|--------|
| `__init__.py` | SDK facade：生命周期、LLM 工具、Plugin Center entries | 不在 `startup` 里启动常驻任务 |
| `catalog.py` | 游戏目录、别名、章节提示、默认键位、HUD 风格 | 所有游戏差异集中在这里 |
| `discovery.py` | Steam 库/清单、进程、窗口、前台、菜单判定 | `ClipCursor` 是菜单判定的核心 |
| `capture.py` | DXGI → GDI → PIL 分层截图 | 失败后端隔离 + 冷却重试 |
| `dxgi_capture.py` | 纯 ctypes Desktop Duplication | 不依赖 dxcam/cv2；vtable 槽位有实测 |
| `input.py` | SendInput 扫描码 + 相对鼠标 + 物理键轮询 | 前台守卫、`release_all`、F8 |
| `vision.py` | 亮度/运动/场景哈希/准星/HUD 辅助特征 | 纯 numpy，不做目标识别 |
| `hud.py` | HUD 稳定性、受伤红边、低血猜测、字幕带 | 不 OCR 数字 |
| `policy.py` | 指令解析 + 反应式状态机 + 动作原语 | 黑屏/菜单绝不按键 |
| `service.py` | 捕获/输入/策略/叙事/接管/会话编排 | 所有竞态在这里处理 |
| `prompts.py` | 本地化 cue + 主动解说 prompt | 事实优先，禁止编造 |

---

## 3. 数据流

```
用户语音
  └─ N.E.K.O LLM
       ├─ source_play("半条命重制版")
       │    └─ catalog.resolve_game → 选 profile → service.play
       └─ source_directive / source_command / source_aim / source_sequence
            └─ policy.Directive / Command → 动作原语

service._control_loop (0.35s)
  ├─ ensure_running_game()          # Steam 进程 + 窗口 + 前台
  ├─ capture()                      # DXGI/GDI/PIL
  ├─ vision.analyze + hud.observe
  ├─ 守卫：黑屏 / 菜单 / 失焦 / 玩家接管 / F8 / 会话超时
  ├─ policy.decide → _run_ops()     # 100ms 切片，随时可被打断
  └─ session/事件/状态

service._narration_loop (0.8s)
  ├─ 每 8s push image (ai_behavior="read")
  └─ 每 25s push situation+image (ai_behavior="respond")
       └─ 猫娘主动说话；可调用工具改策略
```

---

## 4. 菜单 vs 游戏：实测出来的判据

最初用 `GetCursorInfo` 的 `CURSOR_SHOWING` 判断菜单，**在窗口化 Source 游戏里是错的**：
半条命/传送门 2 在窗口化游戏时也保持光标“显示”。实测：

| 场景 | ClipCursor | 光标可见 |
|------|------------|----------|
| 半条命 游戏中（窗口 1600×900） | `(160,90,1760,990)` | true |
| 半条命 暂停菜单 | `(0,0,1920,1080)` | true |
| 半条命 关闭菜单后 | `(160,90,1760,990)` | true |
| 传送门 2 游戏中（切关后） | `(0,0,1920,1080)` | **false** |
| 传送门 2 主菜单 | `(0,0,1920,1080)` | true |

传送门 2 在部分关卡会像半条命一样把光标裁剪到客户区，但在切关/加载后又可能
把裁剪释放到整个桌面；此时**隐藏的光标**是更强的游戏信号（菜单总是显示光标）。

因此 `menu_active(hwnd)` 的顺序是：

1. `ClipCursor` == 客户区，且客户区不是全屏 → **游戏中**；
2. `ClipCursor` == 整个虚拟桌面，且客户区更小 → **光标可见=菜单，光标隐藏=游戏中**；
3. 客户区本身就是全屏（独占全屏）→ 回退到“光标可见”；
4. 都拿不到 → 回退到光标可见（保守，宁可等待也不乱按）。

这条规则不依赖分辨率/HUD 像素，所以在 125% DPI、虚拟显示器、窗口/无边框下都稳。
独占全屏下 ClipCursor 两边都是全屏，只能靠光标；这是已知限制，README 里写了
“切无边框最稳”。

---

## 5. 输入

* 键盘：`SendInput` + `KEYEVENTF_SCANCODE`，与物理键盘一致；不 `PostMessage`。
* 鼠标视角：相对 `MOUSEEVENTF_MOVE`，按 120 counts 切片，避免单事件被游戏 clamp。
* 鼠标按键：`mouse_down/up`，`tap` 也分支到鼠标（否则 `mouse1` 会 KeyError）。
* 前台守卫：`foreground_check` 绑定当前游戏窗口；`release_all` 永远允许 key-up。
* 物理键轮询：`GetAsyncKeyState` 读 WASD/空格/Ctrl/Shift/E/R/Q/1-6/鼠标左右中，
  排除注入器自己按住的键；命中即 `soft_yield`。
* F8：独立 `_hotkey_loop` 每 120ms 轮询，**autopilot 停止时也能恢复**。

### 竞态处理

`_run_ops` 的每个 hold 用 `_sleep_watched` 按 100ms 切片：

* 切片结束检查 `_mode`，变成 paused/soft_yield 就立即 `return`；
* 检查物理键，命中就 `_yield_to_human`（release_all + 推 cue + 返回）；
* `finally` 仍然 key_up，不会因为 `return` 留下按键；
* 控制循环即使 `autopilot_enabled=false` 也会兜底检查 `held_keys/held_buttons`
  并 `release_all()`。

实测踩过的坑：`stop_playing()` 与 in-flight hold 竞争时，曾在 stop 后看到
`held_keys=('W',)`；用上面的兜底 + 切片解决，并有测试覆盖。

---

## 6. 策略状态机

`PolicyState`: `waiting / menu / blind / explore / combat / puzzle / stuck / paused`。

### 决策优先级

1. 没有游戏/暂停 → `waiting`，零操作；
2. 菜单 → `menu`，零操作（service 决定是否按 Esc 恢复）；
3. 黑屏 → `blind`，零操作 + 报告原因；
4. 一次性指令（`source_command`）→ 执行一次后清掉；
5. 持续模式（`source_directive` 的 puzzle/fight/explore/vehicle）覆盖默认；
6. 战斗证据 → `combat`；
7. Portal 家族或 puzzle 模式 → `puzzle`；
8. 否则 `explore`；长时间无运动/场景哈希不变 → `stuck` 阶梯。

### 战斗证据（避免把“镜头平移”当敌人）

* 准星 RGB 偏红 > 0.20 → 最强证据（Source 准星在敌人身上变红）；
* 屏幕边缘红 > 0.015 → 受伤；
* 中心局部运动：`motion_strength > 0.09` 且 **全局运动 < 0.018**
  且质心在中心 30%-70% —— 这是为了排除电车/载具/大范围镜头移动。
  实测半条命开场电车：`motion=0.15, motion_strength=0.7`，全局运动大，
  不会被判成战斗。

### 卡住阶梯

`still_for > 2.2s` 且场景哈希不变：

0. 转 45° 看两边；
1. 后退 + 跳；
2. 侧移 + 前进；
3. 按 E + 蹲 + 跳。

每 5 秒升一级，场景变化或出现运动就重置。它不寻路，只避免“对着墙一直走”。

### Portal 解谜

8 相位循环：扫视 → 蓝门 → 转 65° → 橙门 → 前进 → 按 E → 跳。
看到准星发蓝/橙优先打对应门；看到大片白面优先蓝门。
这只是“不断尝试”，真正的解谜由 LLM 用 `source_directive` + `source_sequence` 接管。

---

## 7. 主动解说契约

`prompts.commentary()` 明确要求：

1. 先一句判断，再一句下一步，不念数据；
2. 只使用报告+截图里的事实；看不到血量/弹药就说看不到，不编数字/原因；
3. 改计划用 `source_directive`，立刻动作用 `source_command`；
4. 没有证据不说“通关/打死 Boss”；
5. 这是主动说话，不是回答“怎么了”。

推送参数：

```python
push_message(
    source="source_player",
    visibility=[],
    ai_behavior="respond",
    parts=[{"type": "text", "text": ...}, {"type": "image", "data": jpeg, "mime": "image/jpeg"}],
    priority=4,
    coalesce_key="source_commentary",
)
```

画面流用 `ai_behavior="read"`，`coalesce_key="source_frame"`，避免堆积。

---

## 8. 游戏目录与别名

* 每个 profile 有 `app_id/exe/install_dirs/title_patterns/aliases/keys/chapters/strategy`。
* `hl2.exe` 同时服务 HL2/Ep1/Ep2/Portal/Lost Coast/HL:Source，**先用窗口标题区分**，
  再用安装目录兜底；`_profile_from_process` 有单测。
* `hl.exe` 同时服务 HL1/OP4/Blue Shift；标题能区分则用，否则退回 HL1 并注明。
* 别名按长度降序匹配，保证「半条命2」优先于「半条命」。
* 「半条命重制版」：黑山已安装/运行 → 黑山；否则 HL1 + `assumed=True`，
  回复里明说假设。
* VR/多人 profile 标记 `vr_only/multiplayer_only/coop_only`，`playable=False`，
  工具直接拒绝并解释原因。

---

## 9. 失败模式

| 失败 | 检测 | 行为 |
|------|------|------|
| 游戏没开 | 进程扫描无匹配 | 等待，60s 一次提示“不是故障” |
| 窗口最小化 | `IsIconic` + 矩形 < `min_capture_edge` | 不截图、不按键，提示切回前台 |
| 窗口不在前台 | `GetForegroundWindow`/`GetAncestor` | 松手，等待 |
| 全黑 | `mean<8 and max<28` | 连续 8 帧后停手，报告真实截图错误 |
| DXGI 失败 | 捕获抛异常 | 隔离该后端 30s，降级 GDI/PIL |
| 截图无新帧 | DXGI 正常返回 None | 复用 1.2s 内的上一帧，不误判黑屏 |
| 输入失败 | SendInput 返回 < n | 记录 winerror，松手，限频提示 |
| 会话超时 | 实际游玩秒数 > 45min | 停手 + 提示 |

---

## 10. 测试矩阵

| 文件 | 覆盖 | 数量 |
|------|------|------|
| `test_source_catalog.py` | 身份唯一、别名、重制版回退、VR 拒绝 | 13 |
| `test_source_policy.py` | 指令解析、黑屏/菜单、战斗证据、卡住、传送门、动作原语 | 20 |
| `test_source_vision.py` | 黑屏/运动/红边/白面/准星/dHash/HUD 稳定性 | 10 |
| `test_source_service.py` | 等待/菜单零输入、开始探索、玩家接管、松键、序列 | 8 |
| `test_source_contract.py` | manifest、SDK 桩、entries/tools、push v2、静态扫描 | 15 |
| `test_source_discovery.py` | 句柄、Steam、进程→profile 映射、菜单判定 | 10 |
| `test_source_input.py` | 键表、鼠标 tap、release_all、相对移动、dry_run | 7 |

共约 83 项；全部离线，不启动游戏、不调用真实 `SendInput`。

真实环境：

* `tests/live_source_probe.py`：30+ 项只读检查（Steam、窗口、ClipCursor、
  三后端截图、真实 JPEG、INPUT ABI、键表、manifest）。
* 实机手动验证：半条命/传送门 2 各 1600×900 画面；鼠标视角转动；
  F8 暂停/恢复；按 A 接管后 `soft_yield`；暂停菜单 Esc 恢复。

---

## 11. 打包

`tools/pack_neko_plugin.py source_player --out dist`：

* 布局 `manifest.toml` + `metadata.toml` + `payload/{plugins,profiles,dependencies.toml}`；
* 排除 `__pycache__/*.pyc/.vscode/.git/dist/build/metadata-probe`；
* payload hash = SHA-256 over sorted `path + NUL + bytes + NUL`；
* 打包后 `validate_archive` 会重算 hash 并检查布局。

`tools/verify_source_package.py` 再解包，按宿主路径导入，检查 entries/tools/startup。

---

## 12. 刻意不做的

* 不读进程内存、不注入 DLL、不调用游戏控制台/作弊码；
* 不自动点“新游戏”；
* 不把 HUD 数字当成本地事实（没有 OCR，也不假装有）；
* 不在 `@lifecycle(startup)` 里 `create_task`（transient loop 会取消任务）；
* 不在模块顶层创建 D3D 设备/扫描 Steam（只在 service.start 后做）。
