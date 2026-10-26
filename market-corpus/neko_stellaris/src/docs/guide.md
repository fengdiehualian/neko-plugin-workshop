# YUI（neko_stellaris）

把 N.E.K.O 接进《群星》。**一个 mod，两件事。**

| | 游戏内顾问语音 | 实时解说 |
|---|---|---|
| 声音从哪出 | **游戏自己的音频通道** | N.E.K.O 的音频通道 |
| 时机 | 游戏事件触发那一刻，精确 | 读完日志后 1~3 秒内 |
| 内容 | 预生成，但**可以在游戏运行中被改写** | **每句都是当场生成** |
| 需要装 mod | 是（就是这一个） | 否，但装了信息更全 |

那个 mod 叫 **YUI**，一个文件夹里同时装着两半：

```
mod/nekko_stellaris_advisor/
├── sound/            顾问语音（248 条录音 + soundgroup + voice type）
├── localisation/     "YUI" 这个名字在游戏下拉框里的显示
├── gfx/              顾问图标
├── events/           ← 事件桥：47 个隐藏事件，只写日志
└── common/on_actions/  ← 事件桥：把上面那些事件挂到原版钩子上
```

### 为什么合成一个，而不是拆成两个

拆开本来也能跑，但会带来两件真实的问题：

- **静默的半装。** 只勾语音包、忘了勾桥接，一切看起来都正常——游戏照播 YUI 的台词，插件照样读到事件——只是解说变得含糊，因为桥接提供的细节全没了。唯一的症状是「角色比平时更笼统」，这种症状没人会来报。
- **重复挂载。** 两个 mod 都给同一个 on_action 追加事件时，引擎会追加两次，于是每个被挂钩的事件都触发两遍、日志写两行。合并从根上消除了这个可能；安装器还会主动退掉旧的独立桥接 mod，**不让升级留下两个**。

两半仍然是各自的构建器，合并层只负责把它们产出的目录拼到一起——所以每件事只有一份实现。

---

## 为什么游戏内语音「不能真·实时」

你想要的是「AI 实时生成 → 推进游戏内顾问通道」。这条路只能走到一半：

- 游戏播哪条语音，是它在事件触发那一刻按 `.asset` 里**已经注册好的声音名**决定的。**新增**声音名做不到。
- 但**已注册名字对应的 wav 文件，是引擎在播放时从磁盘读的**——这一条是实测出来的（游戏运行中覆盖 248 个槽位，触发顾问语音听到的却是探针句，进程未重启）。所以**内容可以随时改**。
- 剩下唯一的硬约束是时序：游戏在事件后约 **100 毫秒**内就播了，而生成一句要 2~4 秒。**「因为这次事件而生成的台词」永远赶不上这次播放**，这是结构性的。

所以每一半都放在它能真正工作的地方：**要「在游戏里」就用预生成**，**要「实时生成」就走 N.E.K.O 自己的音频**。两者同时开着，听感上就是兰兰在陪着你打这一局。

---

## 一、游戏内顾问语音（mod 的前一半）

### 它长什么样

不是「替换原版语音」，而是**新增一个顾问语音类型**，在 `设置 → 声音 → 顾问语音` 里选「N.E.K.O 顾问语音（兰兰）」。

生成 248 个 wav，覆盖原版默认顾问能触及的**全部 82 个声音名**：

| 组 | 声音数 | 每名变体 | 说明 |
|---|---|---|---|
| notification | 54 | 4 | 科研完成、被宣战、舰队被击毁…… |
| tutorial | 4 | 2 | 教程提示 |
| sample | 3 | 1 | 设置界面试听音色时播的自我介绍 |
| click | 16 | 1 | 点 UI 时的应答（原版自己就会在这 16 条里随机抽） |
| generic | 5 | 1 | 通用短句 |

**必须全部覆盖**：默认语音组（`l_english`）自己不带任何覆盖，所有声音都落到 `sound/sound.asset` 里的英文录音。只覆盖一部分的话，游戏会在一条句子里混着播中文和英文——`advisor_clicking_respons_01` 这个 soundeffect 单独就有 4 个成员音。

### 怎么用

```
stellaris_voice_plan     # 先看看要生成什么，不花 TTS
stellaris_build_voice    # 开始生成（后台，几分钟）
stellaris_status         # 看进度
```

生成完会自动装到 `文档\Paradox Interactive\Stellaris\mod\`、在 `dlc_load.json` 登记，**并写进启动器的当前播放集**。

### 这里有个坑，值得单独说

Paradox 启动器的行为是两段的：它扫描 `mod/` 目录，给每个 `.mod` 存根在数据库里建一行（`status='ready_to_play'`，还会把 `path=` 改写成绝对路径）——**这一步它做得很好**。但它**不会**把新发现的 mod 加进播放集（`playsets_mods` 表）。后果是：

> mod 出现在启动器的 mod 清单里，却不在播放集里 → 点开始游戏时 `dlc_load.json` 被写成 `{"enabled_mods":[]}` → 游戏当没装。

如果 mod 是在启动器**已经开着**的时候装进去的，就一定会撞上。所以安装器拷完文件后会**再去写一行 `playsets_mods`**——就是勾那个复选框的等价物，只动这张 4 列的表，绝不碰启动器自己拥有的 41 列 `mods` 表。

而且它**检测到启动器在运行就拒绝写入**：启动器退出时会用内存里的状态重写数据库，现在写的会被丢掉。宁可明确报错，也不要给一个「成功」然后什么都没发生。

撞上了就这样补：

```
# 先完全关闭启动器（含托盘图标）
python plugin/plugins/neko_stellaris/build_cli.py playset
```

（下面所有 `plugin/plugins/neko_stellaris/...` 都是**宿主布局**的写法。直接在本仓库里跑就把前缀去掉，例如 `python build_cli.py playset`——这些脚本都在自己所在目录找 `core/`。）

对话里也可以用 `stellaris_enable_in_launcher`。**实在不行就在启动器 mod 列表里手动勾这两个 checkbox**，那永远有效。

### 发布之后：本地那份会被启动器删掉

把 mod 发布到创意工坊、再订阅它，启动器会做一次**迁移**：删掉 `Documents/.../mod/<mod_id>/` 和它的 `.mod` 存根，改为启用 `mod/ugc_<id>.mod`，由 Steam 托管那份副本。

这会让所有"按固定路径找已装包"的代码答错：自检会说「游戏里没装顾问语音包」（它其实正在被加载），自动重建会在本地**再装一份**。两份同名的后果不是难看而已——桥接的 on_action 会被追加两次，**每个事件触发两遍**，日志也写两行。

所以工具链现在会：

- 通过 `mod/ugc_*.mod`（或 `pdx_*.mod`）里的 `path=` 找到工坊副本，并确认它确实是我们的——判据是只有我们会生成的文件（`sound/<mod_id>.asset`）或标记文件里记着的 `mod_id`，**不是显示名**（名字会翻译，也可能撞名）；
- 把它当作**活动安装**：`stellaris_voice_check` 与 `build_cli.py check` 报的是**它的**状态；
- 本地也有一份时本地优先（那是插件自己拥有、自己会重写的那份）；
- **自动重建与安装入口都拒绝再装第二份**，并把原因说出来；要更新工坊版本请重新上传工坊条目。

只有命令行是逃生口——那是明确的操作：

```
python build_cli.py combined --install      # 就是要本地一份（比如做测试）
```

卸载同样不会碰工坊副本（那是 Steam 管的），只会在结果里提醒你去 Steam 取消订阅。

### 换音色

```
stellaris_list_voices              # 看有哪些
stellaris_build_voice voice_id=... # 换一个重生成
```

默认去读你 `core_config.json` 里正在用的音色——**包括你自己克隆的音色**。缓存按「文本+音色+端点」做键，所以换音色会全部重生成，换变体数量则不会。

改台词：编辑 `data/lines.zh-CN.json` 后重新生成。每个事件是一个字符串数组，写几条就出几条变体。

### 启动自检：游戏里那份，还是你现在这个音色吗？

**这件事本来是完全静默的。** 你在 N.E.K.O 里换了音色，游戏里那 248 条还是旧声音，没有任何地方会告诉你。反过来也一样：我改了台词表或数据集，游戏里那份同样过期。

所以语音包现在带一份**构建记录**（`pack.json` 里的 `stamp`），记下它是由哪个音色、哪版台词、哪版数据集生成的。插件启动时会拿它和当前设置对比：

```
语音包自检：N.E.K.O 的音色变了，游戏里还是旧音色；需重新合成 248 条
            （游戏里用的是 cosyvoice-…wanxuanbl，现在是 voice-tone-RcH2svtsrw）
```

**不一致就后台自动重建**（`auto_refresh_voice = true`），只补缓存没命中的部分——所以代价差别很大：

| 变的是什么 | 要重合成 |
|---|---|
| 只改一行台词 | **1 条**（几秒） |
| 改 20 行台词 | **20 条** |
| 只改数据集（音频其实没变） | **0 条**，只重写资产 |
| 换图标/封面或改取景（音频没变） | **0 条**，只重绘图标与封面 |
| 换音色 | **248 条**（几分钟） |

缓存键包含音色，所以换音色必然全部重来——这不是浪费，是没法复用。

手动检查或强制重建：

```
stellaris_voice_check              # 只报告
stellaris_voice_check action=refresh
```

命令行同样有：`build_cli.py check`（退出码 0=最新，1=需要重建，方便脚本化）。构建记录里除了音色与台词，还记了**图标/封面指纹**（来源图内容 + 取景常量 + 图标尺寸）——否则换了图或改了取景，`check` 会一直说"最新"，封面就一直停在旧图上。

自检失败不会挡住启动——它只是报告，最坏情况是打印一条日志。

---

## 二、实时解说（插件侧，配合 mod 的后一半）

`stellaris_live` 有个 `action` 参数：

| action | 作用 |
|---|---|
| `start` | 开始尾随 `game.log`，让角色回应刚发生的事 |
| `stop` | 停止 |
| `once` | 立刻读一次新日志 |
| `flush` | 把攒着的小事立刻说出来 |
| `status` | 看统计（说了几次、丢了多少 AI 事件、攒了多少待摘要） |

要开机自动开，把 `plugin.toml` 里的 `live_enabled` 改成 `true`。

### 事件从哪来

**原版就会写。** `game.log` 里每条事件选择都有一行：

```
[05:40:22][eventcommands.cpp:88]: Event crisis.7515 added info about event selection.
                                  selectedOption 0, human 1, playerEventId 1705
```

`human 1` 标记了这是玩家参与的事件（不是 AI 国家的）。所以**不装任何 mod 也能用**。

### 桥接 mod 补什么

原版日志告诉你「发生了什么」，但不告诉你「谁对谁做了什么」——「你被宣战」和「邻居勘测了行星」在原版日志里长得一模一样。桥接 mod `nekko_stellaris_bridge` 用脚本效果 `log` 补上这层：

```
stellaris_build_bridge      # 生成 + 安装
```

它给 **47 个 on_action** 各追加一个隐藏事件，只写日志，**不改变任何游戏内容**。覆盖宣战/被宣战/战争结束、舰队被击毁/战斗胜负、殖民/建造/叛乱、星港与空间站失守、首次接触/通讯/联邦、领袖更替、巨型工程、科研与勘测、特殊项目、帝国灭亡。

**为什么不是全部 63 个**：单舰级事件（`on_ship_destroyed_victim`）在大舰队战里每秒刷几十行，会写爆日志；`survey_planet_by_ship` 同样的道理。这些默认排除，理由写在生成的 mod 自己的 README 里。

### 防刷屏

`game.log` 不是为对话准备的：真实一局里每 10~30 秒就有一条事件选择，而且大部分是 AI 国家的。天真的实现会让角色说个不停。所以有个策略层，三层 + 两道独立刹车：

| 层 | 事件 | 行为 |
|---|---|---|
| urgent | 被宣战、舰队被毁、行星叛乱、天灾 | 自己就能开口，但 45 秒内只开一次 |
| notable | 我方宣战、战争结束、首次接触、联邦、殖民、巨型工程 | 有空就说，同时到几条就并成一句 |
| digest | 科研完成、建造完成、勘测完成 | **绝不单独开口**，攒起来每 120 秒概括一次 |
| silent | AI 国家的一切 | 直接丢 |

刹车是**每分钟 2 条**的上限和 **12 秒**的最小间隔——光靠分层管不住爆发，5 秒里来 10 个「紧急」事件还是 10 个事件。这两个数是从玩家的角度定的，不是从日志的角度：一直保持每 30 秒一句，是「陪伴」和「旁白」的分界线。

实测你现有那份 `game.log`（250 条事件）：191 条 AI 事件被丢，34 条进摘要队列，只有 1 条够紧急。

调参在 `plugin.toml` 的 `[stellaris]`：`urgent_cooldown_s` / `digest_interval_s` / `max_per_minute` / `min_gap_s`。

---

## 三、运行时再生（游戏内语音动态化）

**这一节是实测结论支撑的，不是推测。**

### 实测：引擎按需读盘

我在游戏**运行中**把 248 个 wav 槽位全部覆盖成同一句探针台词（不动 `.asset`），然后在游戏里触发顾问语音——**听到的是探针句**。进程自检确认游戏自始至终是同一个 PID、没有重启，所以排除假阳性。

这意味着：

| | 能不能改 |
|---|---|
| 声音**名** → 文件路径的映射 | ❌ 启动时固定，加不了新声音 |
| 那个 wav **文件的内容** | ✅ **随时可改，下一次播放就生效** |

名字不变、字节可变，在引擎看来没有区别。所以**不需要** reload、不需要控制台、不抢焦点、不掉成就。

### 仍然做不到的：逐事件实时

游戏在事件发生后约 **100 毫秒**内播顾问语音，而生成一句台词要 **2~4 秒**（模型 + TTS）。所以「因为这次事件而生成的台词」永远赶不上这次播放。这是结构性的，不是优化能解决的。

### 实际做法：池轮换

每个 soundeffect 是一个游戏随机抽取的**池**（本包宽 2~4）。每次事件发生后，N.E.K.O. 只为**这一个池重写一个槽位**，并按池轮换。K 次事件之后整池都被重写过，此后任何一次触发都有 (K−1)/K 的概率播到「上一次事件之后生成的」台词。

每次事件只花**一次**调用是刻意的：事件每 10~30 秒一个，池宽 4，全池重写就是每次 4 次模型 + 4 次 TTS——远超免费端点的承受能力，也远超收益。轮换用一次调用就能在 4 次事件内达到全覆盖。

**没有先例的事件**（本次游戏第一次发生）播的是构建期台词——这保证了任何事件永远有声音，不会出现沉默。

### 依赖

`ctx.models` 需要一个**绑定到 analysis 用法**的模型槽位：

1. 打开 N.E.K.O 的插件详情页
2. 给「顾问台词创作」（manifest 里的 `analysis`）绑定一个模型槽位

没绑定时实时台词自动停用（清单里 `required = false`），其余功能不受影响，`stellaris_live_voice` 会明确告诉你原因。

### 控制

```
stellaris_live_voice status   # 池数、待办、预算、最近一条台词
stellaris_live_voice once     # 立刻生成一条并写盘
stellaris_live_voice off      # 关掉，退回纯静态语音包
```

预算与节流在 `plugin.toml`：`live_budget_per_minute`（默认 4）、`live_min_seconds_between`（默认 8）。

### 安全

写入是**原子**的（临时文件 + `os.replace`）：引擎现在会在播放时读这些文件，普通写入会让恰好在那一瞬触发的事件拿到一个被截断的 WAV。所有原始字节在首次改写前都会备份，`probe-restore` 一条命令全部还原。

## 四、配置

改 `plugin.toml` 的 `[stellaris]` 段，或用环境变量 `NEKO_STELLARIS_<大写的键名>`（例如 `NEKO_STELLARIS_VOICE_ID`）。

读取顺序：`plugin.toml` → 环境变量 → 代码默认值。**实际生效来源会显示在 `stellaris_status` 的 `settings_source` 里**——一个静默没生效的配置是这里最贵的 bug。

| 键 | 默认 | 说明 |
|---|---|---|
| `voice_id` | 空 | 空 = 用 `core_config.json` 里你正在用的音色 |
| `tts_url` | 空 | 空 = 按 `region` 选免费端点 |
| `region` | `cn` | `cn` → `www.lanlan.tech`；`intl` → `www.lanlan.app` |
| `live_enabled` | `false` | 启动时自动开实时解说 |
| `poll_interval_s` | `2.0` | 日志轮询间隔 |
| `variants` | `4` | 每个事件变体上限（按组另有分档，这是全局上限） |
| `auto_install` | `true` | 生成完自动装 |

### 面板（Hosted UI）

`ui/panel.tsx` 是插件面板，由 N.E.K.O 的插件管理器渲染。它**只读**：所有数字都来自
`@ui.context(id="dashboard")` 返回的那一份状态（与 `stellaris_status` 同一批辅助函数算出来，
所以两者不会说法不一致），按钮就是调现有入口。

| 卡片 | 内容 |
|---|---|
| 统计 | 游戏内安装来源（创意工坊 / 本地）、版本、台词条数、是否与当前音色一致 |
| 自检 | `stellaris_voice_check` 的结论 + 「重建并安装」按钮 |
| 安装详情 | 路径、文件数、体积、`dlc_load` 是否登记、工坊条目号 |
| 音色与通道 | 音色、来源、端点、密钥是否配置 + 「试听一句」 |
| error.log 健康度 | 四个计数（桥接事件报错 / Invalid context switch / No sound scripted / unsupported scope），全为 0 即干净 |
| 危险操作 | 「卸载游戏内的 mod」（带确认对话框） |

构建是后台任务：面板检测到 `running_job` 时每 2 秒刷新一次，进度用 `Progress` 显示。

**双副本警告**：工坊副本与本地副本同时存在、或同时在 `dlc_load` 里启用时，面板顶部常驻一条
`Warning` —— 两个都启用会让桥接钩子被追加两次，每个事件触发两遍。

面板的接线由离线检查固定：清单里的 `entry` 必须指向存在的文件、面板驱动的每个入口必须在
Python 里存在且挂了 `@ui.action`、面板用到的每个 i18n 键必须在两种语言里都有。
**渲染效果本身没法离线验证**，需要打开插件管理器看一眼。

---

## 五、一个必须说清楚的假设

桥接 mod 依赖 **Stellaris 按顶层名字合并 on_action**：在自己的文件里重复写 `on_country_attacked = { events = { ... } }` 是**追加**，不是覆盖。

这是所有大型 mod 扩展 pulse 的标准做法，但**无法用原版文件举证**——原版 489 个顶层名里一个重名都没有，找不到例子。它属文档行为，而且如果它是覆盖语义，现存几乎每个 mod 都会坏。

正因为这个假设是承重的，包里放了**心跳**：`on_bi_yearly_pulse_country` 上挂了 `neko_bridge.0`，每两年写一行 `NEKO|v1|heartbeat|`（选它是因为原版在该钩子上只有 2 个事件，假设万一不成立时被破坏的东西最少）。

**验证方法**：进游戏、存档、退出，打开 `文档\Paradox Interactive\Stellaris\logs\game.log` 搜 `NEKO|`。搜不到就先看心跳行在不在。

同样地，语音包本身**没有在真机游戏里验证过**——mod 目录结构、资产语法、DDS 图标格式都是对着原版 4.5.2 的文件逐字段核对出来的（详见下），但从没进过游戏。第一次加载如果顾问语音列表里没出现，请把 `logs/error.log` 发我。

### 已经核对过的原版事实

| 事实 | 依据 |
|---|---|
| 顾问语音组按 `l_<key>` 命名，voice type 文件必须同名 | `sound/advisor_voice_types/00_advisor_voice_types.txt` 的注释与全部既有条目 |
| mod 可以在自己的 `.asset` 里定义 soundgroup | DLC 的 `l_venerable_scientist_vir` 就定义在 `sound/vo/chinese/voiceovers_chinese.asset` |
| `.asset` 里的路径相对于该 `.asset` 自身所在目录 | `voiceovers_chinese.asset` 用 `assets/...`，文件在 `sound/vo/chinese/` |
| 默认顾问能触及 82 个声音名 | 从 `sound/soundeffects.asset` 反解 74 个默认组 soundeffect 的成员 |
| 音频格式 PCM16 / 单声道 / 44100 Hz | 逐字节读 `vo/notifications/*.wav` 的 RIFF 头 |
| 顾问图标是**未压缩 BGRA DDS**，29×29，无 mipmap，3492 字节 | 读 `gfx/interface/icons/advisor_vo/*.dds` 的完整头（`pfFlags=0x41`, `rgbBits=32`） |
| 本地化文件必须带 UTF-8 BOM | 原版与参考 mod 的 yml 前 4 字节都是 `EF BB BF 6C 5F` |
| `log` 效果语法是 `log = "字符串"`，作用域 all | `script_documentation/effects.log` |
| `is_player` 不是触发器，正确写法是 `is_ai = no` | triggers.log 无此触发器，全库 0 命中；原版 events/ 用了 478 次 |
| 手装的本地 mod 在启动器里**没有**封面可设 | 启动器只在有 `thumbnailUrl` 时才填 `thumbnailPath`（`fetchThumbnail` 里 `if (!e.thumbnailUrl) return null`）；本地 mod 的 `.metadata/thumbnail.png` 那条路要求 `formatVersion == 1.1`，而启动器游戏注册表里 `stellaris` 是 `formatVersion: null`；描述符的 `picture=` 它不读（启动器包内 0 命中）。`picture="thumbnail.png"` 仅作社区约定保留 |
| **日志里能插值的只有作用域链和访问器，触发器值不行** | `last_increased_tech` 全库 211 处用法都是 `last_increased_tech = <tech_key>` 这种比较，本地化里也从未插值过它，`on_tech_increased` 只给 `this = country` —— 所以 `[This.last_increased_tech]` 每条都渲染成空（实测 561/561）。科技名只能靠一件事换：生成一个遍历全部科技键的 if/else 事件 |
| **不存在的作用域链是报错，不是静默跳过** | `owner = { … }` 用在刚勘测完的行星上（勘测发生在殖民之前，此时无主），引擎每次触发都记 `Invalid context switch [owner] from <planet>` —— 单个事件一局刷了 32,383 行；`on_destroying_station` 同理（中立站无主）。修法：先 `exists = <链>`，或改用原版注释写明的作用域（`on_planet_surveyed` 的 `From = Country`） |
| **顶层 `sound` 块是全局的，重名时先声明者胜** | 自造的变体名写成 `advisor_..._02` 这类与原版同形的名字，与 `soundgroups.asset` 里为流派语音池准备的别名撞名，error.log 报 102 行 `Sound 'X' already scripted, second occurance at 'sound/nekko_stellaris_advisor.asset'` —— 引擎保留先声明的原版那份，于是每个池子 4 个候选有 3 个播的是原版顾问。组内的 `soundfileoverrides` / `soundeffectoverrides` 才是组作用域（只影响自己这一路语音，不动原版） |
| on_action 块没有 `limit` 字段 | 过滤只能写进自己事件的 `trigger = {}` |

### 这些是在真机游戏里被确认（以及推翻）的

前两版都在这上面栽过，每一条都有 error.log 或 game.log 作为证据：

| 事实 | 证据 |
|---|---|
| **引擎在播放时从磁盘读 wav**：名字不变、只改内容，即刻生效 | 游戏运行中覆盖 248 个槽位，触发顾问语音听到的却是探针句；进程 PID 未变，排除假阳性 |
| **`soundfileoverrides` 只能覆盖已存在的名字，不能创建新名字** | error.log 64 行 `No sound scripted for soundoverride 'advisor_notification_…_04'`。新增必须用顶层 `sound = { name file }`——参考 mod 正是这么做的，我当时没看出这一点 |
| **事件类型必须匹配作用域**：`country_event` 挂在行星/舰队钩子上会炸 | error.log 1439 行 `attempted to execute an event on an unsupported scope`，其中 `on_planet_surveyed` 一条就占 1374 次。正确类型可从原版机械读取（该钩子上原版触发的事件声明成什么类型） |
| **on_action 的 MERGE 语义成立**（重复顶层名即追加） | game.log 里出现了 `NEKO\|v1\|heartbeat\|`——心跳钩子确实被追加并触发了。这正是当初无法用原版文件举证的那个假设 |
| **`log` 里的数据引用有效** | `NEKO\|v1\|first_contact_with_empire\|a=联合圣约国\|b=不明帝国\|system=泰伊格\|`——真实帝国名与星系名 |
| `soundeffect` 不写进 `category` 会被警告（非致命） | error.log 110 行 `does not have a category assigned`；参考 mod 同样有 |

### 关于参考 mod，我得更正一句

我先前说它是「3.4 格式，在 4.5 已不适用」。**理由错了**：它用的机制（voice type 名 == soundgroup 名 + `soundeffectoverrides`）正是 4.5 原版在用的。而且它用顶层 `sound = {}` 声明新声音名——**这才是创建新名字的正确做法，我的第一版没照做，于是栽了。**

它真正过时的是**名字表**：52 个覆盖目标里有 5 个在 4.5 不存在（`…_alien_empire_communication` 应为 `…_communication_01`、`debri_analysed` 拼写、`spacebourne_encounter` 拼写等）。死掉的 `soundeffectoverrides` 是**静默失败**的，游戏会退回英文原声。

**这一点后来被游戏自己证实了**：error.log 里那 5 行 `Undefined sound effect '…' referenced in sound effect override in soundgroup 'advisor_2022_08_12_07_22_52'`——数量与我静态分析的 5 个死引用**完全一致**。

---

## 六、已知限制

1. **游戏内语音做不到实时生成**（见开头）。这是引擎限制，不是实现取舍。
2. **桥接 mod 的合并假设未经原版文件举证**，靠心跳可验证。
3. **语音包未在真机游戏内验证过**，只做了结构级核对。
4. **`log` 里的数据引用会静默失败**：`[This.GetName]` 有时输出真名（实测 `astral_planes_events.txt:231`），有时塌成空串且不报错（实测 `00_scripted_effects.txt:6256`）。所以事件载荷的解析**绝不依赖名字**，空值会被跳过。
5. **免费 TTS 端点限流罚的是连接数**：实测单连接连发 16 条零错误、0.54s/条；每条重开连接（哪怕只开 2 条）约一半请求吃 `1008 Too many concurrent requests`，每次重试几秒退避。现在整批复用一条连接（`TtsSession`），248 条约 3 分钟，实测 0 次限流重试。
6. **天灾降临、经济赤字、游戏结束没有 on_action 可挂**，只能在原版事件选择的粒度上近似。
7. 屏幕上的**顾问通知文本仍是原版的**——游戏只让 mod 换音频，不让换那行字。

---

## 七、不开 N.E.K.O 也能用

入口是常规做法，但它需要宿主在跑。下面这个 CLI 走的是**同一套 `core` 模块**（不复制逻辑，所以两条路不会漂移），适合脚本化重建、或者在没有桌面端的机器上生成：

```
python plugin/plugins/neko_stellaris/build_cli.py plan              # 预览，不联网不写盘
python plugin/plugins/neko_stellaris/build_cli.py combined --install  # 生成并安装（一个 mod 两半齐全）
python plugin/plugins/neko_stellaris/build_cli.py check             # 是否已过期（退出码 0=最新）
python plugin/plugins/neko_stellaris/build_cli.py status
python plugin/plugins/neko_stellaris/build_cli.py uninstall
```

上面的路径是**宿主布局**的写法。这些脚本按**自身所在目录**去找 `core/`，所以直接在本仓库里跑时去掉前缀就行（例如 `python build_cli.py plan`）——脚本不去碰插件入口，因此不需要宿主 SDK 在场。本仓库的根目录就是插件目录（`plugin.toml` 在根上），所以「去掉前缀」正是正确写法。

（`voice` 和 `bridge` 子命令还在，只生成单独一半，留给排查问题用。日常用 `combined`。）

换名字：

```
python plugin/plugins/neko_stellaris/build_cli.py combined --name "由依" --install
```

它和插件**共用同一个运行时数据根**，所以两边的 TTS 缓存是同一份——用 CLI 生成过的东西，插件里不会再花一次钱。

## 八、测试

```
.venv\Scripts\python.exe smoke_offline.py
```

在宿主里、以及在本仓库里（旁边有 N.E.K.O 检出时）都是 **226 项检查**；找不到宿主 SDK 时是 223 项通过 + 3 项跳过——那三项分别是：两项要经插件入口读 `Settings`、一项要把 `plugin.toml` 交给宿主的 pydantic schema 校验——都需要宿主 SDK，缺了它会**显式报告跳过**而不是静默少算。找宿主的顺序是 `NEKO_ROOT` 环境变量 → 自身的各级上级目录 → 旁边的 `../N.E.K.O`，所以两种布局用同一条命令。

**不联网、不碰 TTS、不碰游戏**，断言的是插件产出的**格式**——因为这里才是昂贵的错误所在：一个没有覆盖全部可达声音的 soundgroup、一个少了 BOM 的本地化文件、一个头部与原版不符的 DDS、一个没有任何 on_action 会触发的桥接事件。

这套检查已经抓到过三个真 bug：

- 去重逻辑把「从未见过」当成「在 t=0 见过」（`.get(key, 0.0) >= cutoff`）。生产环境里 `time.time()` 是个大数所以侥幸不炸，但它会吞掉整条事件流。
- 桥接事件的去重键漏了挂钟时间，导致同一天内两次不同的科研完成被当成重复。
- 策略的 `spoken` 计数键和外层结果里的 `spoken` 列表撞名，把列表覆盖成了整数。

## 九、目录

```
plugin/plugins/neko_stellaris/
├── plugin.toml              清单 + [stellaris] 配置
├── __init__.py              插件入口（12 个 entry + 3 个 lifecycle）
├── core/
│   ├── audiowav.py          任意 WAV → PCM16 单声道 44100
│   ├── tts.py               free 通道流式 TTS + 断点续传缓存
│   ├── ddsicon.py           手写未压缩 BGRA DDS（对齐原版 3492 字节）
│   ├── voicepack.py         语音包规划与产出
│   ├── combined.py          把语音包与事件桥合成一个 mod
│   ├── standalone.py        导出不依赖插件的独立语音包
│   ├── bridge.py            桥接 mod 生成
│   ├── installer.py         安装/卸载（带所有权保护）
│   ├── gamelog.py           game.log 增量尾随与解析
│   ├── livevoice.py         运行时再生：游戏开着时改写顾问将要说的话
│   ├── policy.py            解说策略与防刷屏
│   └── nekoconfig.py        复用用户 N.E.K.O 配置与路径发现
├── data/
│   ├── advisor_sounds.json  从 4.5.2 提取的 82 个可达声音名
│   ├── on_action_hooks.json 审计过的 on_action 钩子（61 verified）
│   ├── on_action_brief.md   审计结论与踩坑
│   ├── lines.zh-CN.json     台词表（82 key / 386 条）
│   └── persona_brief.md     人设结论（自称「本喵」、称玩家「碳基生物」的来源）
├── assets/                  顾问图标原图（游戏内 DDS 与工坊缩略图的上游）
├── build_cli.py             不依赖宿主的命令行入口
├── smoke_offline.py         226 项离线回归检查（宿主内运行）
├── i18n/
└── ui/panel.tsx             面板（Hosted UI，只读状态 + 调现有入口）
```

这是**部署进宿主之后**的样子。仓库里同一批文件就摆在仓库根上（`plugin.toml` 在根），另外仓库还带 `tests/`、`tools/`、`pyproject.toml`、`config.example.toml`，以及不参与部署的 `docs/`、`workshop/`、`.vscode/`、`.github/`。

`data/on_action_hooks.json` 与 `advisor_sounds.json` 都是从安装目录里**实测提取**的，游戏更新后可以重新提取而不是靠猜。

---

## 十、独立语音包（不依赖插件）

**音频本来就不依赖 N.E.K.O。** 一个替换了顾问声音组的 mod，本身离线可用、永久可用——插件不在跑也照响。所以这一节要做的不是「让它不依赖插件」，而是**把只服务插件的东西从里面拿走**，产出一份可以自己装、交给别人、或发创意工坊的干净包。

```
python plugin/plugins/neko_stellaris/build_cli.py standalone
```

产出 `nekko_stellaris_advisor-standalone/` 加一个 `nekko_stellaris_advisor-1.0.1.zip`（30.9 MiB）。

**被移走的东西**（这些没有插件时要么是死代码，要么只是给插件看的）：

| 移除 | 原因 |
|---|---|
| `events/`、`common/on_actions/` | 事件桥。它存在的唯一目的是喂实时解说；没有插件时，它只是给 47 个 on_action 各挂一个没人读的事件 |
| `pack.json` | 把原版音效名映射到槽位的清单，供运行时改写用。纯插件簿记 |
| `.neko_stellaris_pack.json` | 安装器判定「这个目录能不能被覆盖」的所有权标记 |
| 原 README | 那份是写给调试集成的人看的，不是写给用语音包的人看的 |

**留下的东西**就是语音包本身：`soundgroup` + 248 个 wav + voice type + 图标 + 两份本地化，加一份面向使用者的 README（怎么装、装完在哪选、怎么卸）。导出后会**自动校验**目录里确实没有残留任何插件内容。

导出的包**默认不安装**——整合版才是在服务实时解说，悄悄用纯语音包覆盖它，会看起来像一次普通的导出，实际却把事件桥抽走了。要装就加 `--install`，那时会明确告诉你会失去什么。

```
build_cli.py standalone --install                  # 装成纯语音包（会覆盖整合版）
build_cli.py standalone --keep-bridge              # 想连桥一起分发
build_cli.py standalone --name "由依" --mod-id yui_voice
```

---

## 十一、卸载
```
stellaris_uninstall                  # 插件装过的东西都卸
```

只会删除本插件自己创建的目录（靠目录里的 `.neko_stellaris_pack.json` 标记识别），遇到不相识的目录会拒绝而不是覆盖。`dlc_load.json` 改动前会留一份 `.neko-backup`。

独立语音包不经插件安装，所以也不由它卸载——在启动器里取消勾选，然后删掉 `mod\nekko_stellaris_advisor\` 和 `mod\nekko_stellaris_advisor.mod` 即可。
