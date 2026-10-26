# 半条命 & 传送门 陪玩 —— N.E.K.O 插件包

一个可被 **N.E.K.O → 插件管理 → 导入** 正常导入的 Python 插件。

装好以后，你**先手动打开游戏并进入关卡**，对猫娘说：

> 「去玩半条命重制版」
>
> 「玩传送门二」
>
> 「你来打半条命 2 第一章」

她会先判断当前是哪部游戏、窗口在不在、画面是不是菜单，然后说明自己的判断和计划，
开始截屏观察、用硬件扫描码操作键盘/鼠标，一边玩一边解说。你随时可以：

* 直接碰 WASD / 鼠标开火 —— 她会在下一次检查（约 0.1–0.4 秒）松手，把键盘让给你；
* 按 **F8** —— 硬暂停/继续；
* 说「停手」「我来」—— 立刻停手并松开所有按键；
* 说「继续」「你接着玩」—— 重新接管；
* 说「往前走」「按 E」「打蓝门」—— 和她一起操作。

> **重要边界（不骗人）**：这是一个“视觉 + 反应式策略 + 对话 LLM”的陪玩插件，
> 不是读内存的外挂，也不是保证 100% 通关的速通脚本。它能真正接管操作、
> 持续尝试推进、主动解说，并在复杂谜题/关卡里接受你的口头指挥；
> 但《半条命》的堆箱子、阀门、载具，《传送门》的后期光桥/凝胶谜题，
> 仍然需要 LLM 或你来一起决策。详见文末「已知限制」。

---

## 交付物

| 文件 | 说明 |
|------|------|
| **`dist/source_player.neko-plugin`** | **要导入的插件包**（官方 `neko-plugin build` 产出，v1.0.0） |
| [`source_player/`](.) | 插件源码（11 个 Python 模块 + i18n + 本文档 + DESIGN.md + PUBLISHING.md） |
| [`PUBLISHING.md`](PUBLISHING.md) | 市场发布手册：已完成的验证、首次审核步骤、审核关注点 |
| [`tools/pack_neko_plugin.py`](../tools/pack_neko_plugin.py) | 备用打包器（含 payload hash），正式发布用官方 CLI |
| [`tools/verify_source_package.py`](../tools/verify_source_package.py) | 解包后按宿主路径导入并验证 entries/tools/startup |
| [`tests/test_source_*.py`](../tests) | 83 项契约/策略/视觉/服务/输入测试（严格 SDK 桩） |
| [`tests/live_source_probe.py`](../tests/live_source_probe.py) | 30+ 项真实环境只读探测（不按键、不启动游戏） |

> **市场发布状态**：`check --release --market-release`、CI 同参数 ruff、打包后导入
> 与活体宿主启动均已通过（详见 [`PUBLISHING.md`](PUBLISHING.md)）。首次审核需要在
> 市场网页用作者账号提交仓库，之后 `neko-plugin publish` 才会生成可安装版本。

---

## 支持的系列

| 游戏 | Steam AppID | 识别方式 | 说明 |
|------|-------------|----------|------|
| 半条命（HL1） | 70 | `hl.exe` + 标题 | 默认键位取自本机 `valve/config.cfg` |
| 半条命：起源 | 280 | `hl2.exe` + 标题 Source | HL1 的 Source 移植 |
| 针锋相对 | 50 | `hl.exe` + 标题 | 资料片；标题被改过时会退回 HL1 |
| 蓝色行动 | 130 | `hl.exe` + 标题 | 资料片 |
| 黑山（Black Mesa） | 362890 | `bms.exe` | 「半条命重制版」首选 |
| 半条命 2 | 220 | `hl2.exe` + 标题 | 含重力枪/载具提示 |
| 半条命 2：第一章 | 380 | `hl2.exe` + 标题 Episode One | |
| 半条命 2：第二章 | 420 | `hl2.exe` + 标题 Episode Two | |
| 消失的海岸线 | 340 | `hl2.exe` + 标题 Lost Coast | 短演示关 |
| 传送门 | 400 | `hl2.exe` + 标题 Portal | 左键蓝门 / 右键橙门 |
| 传送门 2 | 620 | `portal2.exe` | 只支持单人；合作模式需要两个人，不支持 |
| 半条命：爱莉克斯 | 546560 | `hlvr.exe` + 标题 | **VR 专用，明确拒绝**，不假装能玩 |

「半条命重制版」在装了《黑山》时指向黑山；没装时会退回你机器上的《半条命》，
并在回复里说清这个假设，不会偷偷换游戏。

---

## 安装

1. 打开 **N.E.K.O → 插件管理**
2. 点 **导入**，选择 `dist/source_player.neko-plugin`
3. 启用「半条命 & 传送门 陪玩」
4. （可选）先点 **半条命/传送门环境自检**，确认能截到画面、输入 ABI 正常

> 插件默认 `auto_start = false` + `autopilot_enabled = false`：
> **仅仅启用插件不会抢键盘、也不会自动开游戏**。必须显式开始。

---

## 第一次使用

1. **你自己打开游戏**（Steam 或桌面快捷方式都行），进入关卡/读一个档。
   插件**不会**替你启动游戏，也**不会**点「新游戏」覆盖进度。
2. 对猫娘说「去玩半条命重制版」或「玩传送门2」。
3. 如果游戏窗口已经在，她会立刻接管；如果还在主菜单，她会说：
   「请先进入关卡/读档，我不点新游戏」。
4. 进入游戏后她会自己开始。想停就按 F8，或者说「停手」。

> 首次使用建议先把 `[source].dry_run` 改成 `true`：只观察、解说、不按键，
> 确认她能看见画面、认得菜单，再关掉 dry_run 让她真正玩。

---

## 她怎么“自己玩”

每个 tick（默认 0.35 秒）：

```
截屏 → 画面特征（亮度/运动/准星/HUD/红边/白面）
     → 菜单/前台/黑屏守卫
     → 反应式策略（探索 / 战斗 / 解谜 / 卡住阶梯）
     → 执行按键宏
```

同时，后台叙事循环会：

* 每 8 秒把一张 JPEG 画面推进 LLM 视觉上下文（`ai_behavior="read"`，不触发回复）；
* 每 25 秒推一条结构化「实时战况 + 我的判断 + 下一步」，
  触发猫娘主动开口（`ai_behavior="respond"`）；
* 战斗/卡住时额外推一条短提示，让她别闷头打。

猫娘看到战况后可以调用这些工具：

| 工具 | 用途 |
|------|------|
| `source_play` | 开始/切换某一部游戏（必须已经手动打开） |
| `source_status` | 查状态、窗口、前台、截图、策略、最近动作 |
| `source_look` | 立刻截一张图并返回画面特征 |
| `source_command` | 立刻做一个动作：「往前走」「按 E」「打蓝门」 |
| `source_aim` | 把视角转向画面中的归一化坐标 (x,y)，可立刻开火 |
| `source_shoot` | 按左键/右键（蓝门/橙门） |
| `source_directive` | 设置这一阶段的目标和打法（explore/fight/puzzle/vehicle/wait） |
| `source_sequence` | 一次执行最多 12 步连续动作（解谜组合拳） |
| `source_stop` / `source_resume` | 停手 / 继续 |
| `source_games` | 列出支持、已安装、正在运行的游戏 |
| `source_selfcheck` | 只读环境自检 |

### 反应式策略在做什么

* **探索**：前进 bursts + 周期性左右扫视；画面长时间不动就爬“卡住阶梯”
  （看两边 → 后退跳 → 侧移绕 → 按 E/蹲/跳）。
* **战斗**：准星发红（Source 准星在敌人身上变红）→ 短点射；
  画面边缘泛红（受伤）→ 后退还击；中心局部运动 → 先把视角转过去。
  不把“镜头平移动”误判成敌人（这是实测踩过的坑）。
* **传送门解谜**：看白面 → 蓝门/橙门交替试 → 前进/按 E；
  卡住时接受 LLM 的 `source_directive` / `source_sequence` 指挥。
* **HUD**：只判断“有没有 HUD / 血量区域是否偏红 / 是否在挨打”，
  **不在本地 OCR 血量数字**——数字请让猫娘从截图里读，读不到就明说读不到。

---

## 你能对她说什么

| 你说 | 她的行为 |
|------|----------|
| 去玩半条命 / 玩传送门2 / 黑山启动 | 选游戏并接管（没开就等窗口） |
| 往前走 / 往右走 / 后退 / 冲刺 | 按住对应方向 |
| 跳 / 蹲下 / 按 E / 开门 / 捡起来 | 对应动作 |
| 打 / 开火 / 换弹 / 换手枪 / 开手电 | 对应动作 |
| 打蓝门 / 打橙门 / 重力枪 | 传送门/重力枪动作 |
| 看右边 / 往左转 / 看上面 | 转视角 |
| 停手 / 别玩了 / 我来 / 暂停 | 松开所有键，硬停 |
| 继续 / 你接着玩 / 交给你 | 重新接管 |
| 现在什么情况 / 打到哪了 | `source_status` / `source_look` |
| 你自己看着办 / 去解谜 / 先打怪 | `source_directive` 改策略 |

---

## 安全设计

| 风险 | 处理 |
|------|------|
| 打字到别的窗口 | 只有游戏窗口在前台才注入；`require_foreground=true` |
| 游戏最小化/1×1 窗口 | 明确拒绝，不按键；退回全屏截图并说明 |
| 全黑画面 | 连续 8 帧全黑 → 停止按键，报告真实截图错误 |
| 菜单里乱点 | 没进入过游戏时只等待；进入过后只按 **Esc** 关暂停菜单，**绝不按 Enter** 点新游戏 |
| 玩家突然接手 | 轮询 WASD/开火/E 等物理键（排除自己按住的键）→ 下次检查（约 0.1–0.4 秒）松手；停留 12 秒后自动恢复（可关） |
| 硬暂停 | **F8**；停手后控制循环每 tick 兜底 `release_all()`，不会留下卡住的键 |
| 会话失控 | `max_session_minutes=45` 硬停 |
| 作弊/改存档 | 不读内存、不写存档、不执行控制台命令、不代发成就 |
| 独占全屏截不到 | 自带纯 ctypes DXGI Desktop Duplication，不依赖 cv2/dxcam |

---

## 配置（`plugin.toml` 的 `[source]`）

| 键 | 默认 | 含义 |
|----|------|------|
| `capture_backend` | `auto` | `auto/dxgi/gdi/pil`，失败后端会被隔离并降级 |
| `target_fps` | `4.0` | 观察频率（实际 tick 由 `autopilot_tick_seconds` 控制） |
| `frame_stream_interval_seconds` | `8.0` | 画面推进 LLM 的间隔 |
| `commentary_interval_seconds` | `25.0` | 主动解说间隔 |
| `require_foreground` | `true` | 前台守卫 |
| `autopilot_enabled` | `false` | 是否自动操作（建议用 `source_play` 打开） |
| `dry_run` | `false` | 只观察不按键 |
| `max_session_minutes` | `45` | 单次会话上限 |
| `look_units_per_degree` | `15.15` | 鼠标灵敏度换算（Source: 1/(sens·m_yaw)） |
| `yield_on_human_input` | `true` | 玩家碰键就松手 |
| `human_input_grace_seconds` | `12` | 玩家停手多久后自动恢复 |
| `pause_hotkey_enabled` | `true` | F8 硬暂停 |
| `auto_resume_from_menu` | `true` | 暂停菜单自动按 Esc 恢复 |
| `start_in_game_required` | `true` | 没进入游戏就不操作 |
| `objective` / `plan_steps` | 空 | 初始目标/计划，可被 `source_directive` 覆盖 |
| `[source.keys]` | 空 | 逐动作覆盖键位，例如 `forward = "w"` |

插件同时提供 `config.schema.json`：插件管理 →「配置」页会直接显示上面这些字段的
中文名称、说明、取值范围和 `capture_backend` 的下拉选项，改完由 `config_change`
生命周期热重载，不用重启。`config.example.toml` 是同一份默认值的可复制模板。

**灵敏度校准**：默认值来自本机 `Half-Life/valve/config.cfg` 与
`Portal 2/portal2/cfg/config.cfg`（sensitivity 3、m_yaw 0.022）。
如果你关掉了“提高指针精确度”或改了灵敏度，转视角会过冲/不足；
`source_aim` 是相对转动，多调一次会收敛。也可以用 `[source].look_units_per_degree` 调。

---

## 验证结果（本机实测）

```
官方 CLI check --release --market-release        0 error / 0 warning，payload hash 校验通过
官方 CLI 同参数 ruff（E4,E7,E9,F,I）            All checks passed
插件内契约测试（Linux CI 可跑）                  10 tests passed
打包器结构 + 排除规则 + payload hash          OK
解包后按宿主路径导入 + startup                OK（6 entries / 12 tools）
活体宿主机启动 + 只读工具                      selfcheck/games/status 全部成功
契约/策略/视觉/服务/输入测试                   83 tests, 83 passed
真实环境只读探测                              30+ checks, 0 failed
半条命真实窗口                                1600×900，DXGI 截图非黑
传送门 2 真实窗口                              1600×900，DXGI 截图非黑
菜单/游戏判定                                  ClipCursor 实测：
  半条命游戏中                                (160,90,1760,990) → 游戏中
  半条命暂停菜单                              (0,0,1920,1080)  → 菜单
  传送门 2 游戏中（部分场景）                  (0,0,1920,1080) + 光标隐藏 → 游戏中
  传送门 2 主菜单                              (0,0,1920,1080) + 光标显示 → 菜单
输入 ABI                                      INPUT=40 字节，SendInput 可用
鼠标视角                                      实机转动成功（半条命/传送门 2）
F8 暂停/恢复                                  实机成功
玩家按 A 接管                                 实机 soft_yield + release_all
暂停菜单自动恢复                              实机 Esc 恢复
```

> 这些数字来自本机（1920×1080、125% DPI、虚拟显示器、Steam 版
> Half-Life 70 与 Portal 2 620）。换机器/分辨率后，菜单判定仍成立
> （它比较的是窗口客户区与 ClipCursor，不依赖具体像素），
> 但 HUD 区域和鼠标灵敏度可能需要微调。

---

## 已知限制（请先看）

1. **不是读内存的外挂**。插件只能看画面、按键；不知道你的血量精确值、
   弹药数、地图坐标、Boss 血条。血量/弹药由 LLM 从截图里读，读不到会明说。
2. **不能保证通关**。它能持续探索、战斗、按 E、试传送门，但《半条命》的
   堆箱/阀门/载具，《传送门》后期的光桥/凝胶/激光谜题，需要 LLM 或你介入。
   这也是为什么工具有 `source_directive` / `source_sequence`：让猫娘把
   “看画面 → 定策略 → 做组合动作”串起来，而不是假装一个万能脚本。
3. **主菜单不自动点**。为了不覆盖存档，它只在你已经进入关卡后接管；
   在主菜单会明确让你先读档/进关。
4. **合作模式不支持**。《传送门 2》双人需要两个玩家；《半条命：爱莉克斯》
   是 VR 专用，键鼠无法正常游玩——插件会明确拒绝，不假装能玩。
5. **独占全屏的菜单判定**依赖光标可见性（ClipCursor 全屏时无法区分）；
   窗口/无边框最稳。若你在独占全屏下看到“她以为在菜单”，按 Alt+Enter
   切无边框，或把 `auto_resume_from_menu` 关掉手动 F8。
6. **HUD 数字未 OCR**；`hud.present` 是“HUD 稳定性”推断，不是读数字。
7. **卡住阶梯是启发式**，不是寻路。它会在同一个房间反复试，直到场景变化
   或 LLM 改策略；不会瞬移/穿墙。
8. **不代发 Steam 成就**。成就是游戏自己授予的；插件只记录画面可观测的里程碑
   （如果以后加）。

---

## 排障

| 现象 | 先看什么 |
|------|----------|
| 说去玩但没反应 | 游戏是否已进入关卡？看 `source_status` 的 `menu_active` / `focused` |
| 截不到画面 | 跑 `source_selfcheck`，看 `capture` 每个后端的真实错误 |
| 按键没反应 | 窗口是否前台？`INPUT` 是否 40 字节？游戏是否在菜单？ |
| 视角转太多/太少 | 调 `look_units_per_degree`，或让猫娘用 `source_aim` 二次微调 |
| 她一直说“菜单” | 独占全屏/光标可见性误判；切无边框，或关 `auto_resume_from_menu` |
| 她抢键盘 | 按 F8；或把 `yield_on_human_input=true` 保持开启 |
| 解说太频繁 | 调大 `commentary_interval_seconds` / `frame_stream_interval_seconds` |

日志：N.E.K.O → 插件管理 → 该插件 → 日志；错误会带真实异常原文，不会让你去装 cv2。

---

## 重新打包 / 测试

```powershell
# 打包
python tools/pack_neko_plugin.py source_player --out dist

# 验证包（解包 + 宿主路径导入 + entries/tools/startup）
python tools/verify_source_package.py dist/source_player.neko-plugin

# 离线测试（不启动游戏、不按键）
python -m unittest tests.test_source_catalog tests.test_source_policy `
  tests.test_source_vision tests.test_source_service tests.test_source_contract `
  tests.test_source_discovery tests.test_source_input

# 真实环境只读探测（不按键、不启动游戏）
python tests/live_source_probe.py
```

> 测试的 tripwire：`tests/test_source_service.py` 用假捕获/假注入器；
> 任何测试都不会调用真实 `SendInput` 或启动游戏。
