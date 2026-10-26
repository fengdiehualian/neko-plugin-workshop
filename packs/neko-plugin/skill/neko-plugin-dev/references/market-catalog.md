# 猫娘插件社区规范与全量索引

> **数据来源**:https://market.project-neko.cn/#/plugins 官方接口 `/api/v1/plugins`(2026-10-26 抓取)
> **覆盖**:在架 **127 个插件**,本机落盘 **126 份可读源码**(其余仅有 README/LICENSE 或仓库无 plugin.toml)
> **源码位置**:`<WORKSHOP_DIR>\market-corpus\<slug>\src`(只保留代码/配置/文档,二进制资产已剔除,总体积 ~125MB)
> **怎么用**:写插件前先读第 1–5 章的规范,再去第 6 章找同类插件抄结构;需要深度 API 细节时查同目录 `sdk-reference.md` / `decorators.md` / `hosted-ui.md`。
>
> 本文件替代旧版"46 个插件学习卡片"。旧版只有卡片、没有规范;本版把**规范**放前面、**索引**放后面,并且覆盖全部在架插件。

---

## 1. 目录结构:社区实际在用的四种形态

**所有插件都是"一个目录 = 一个插件",`plugin.toml` 在插件根目录。** 语料中 125/126 个插件的 `id` 与目录名完全一致——这是硬约定。

### A. 最小可用(后台逻辑,无界面)

```
my_plugin/
├── plugin.toml            # 唯一必需的配置入口
├── __init__.py            # 必须:插件类 + @neko_plugin
├── README.md              # 建议:市场展示与安装说明
└── config.example.toml    # 建议:需要用户填 key/路径时提供样例(72 个插件都有)
```

### B. 带静态面板(市场主流,47 个)

```
my_plugin/
├── plugin.toml
├── __init__.py
├── static/                # register_static_ui 注册的纯前端
│   └── index.html
├── i18n/
│   ├── zh-CN.json
│   └── en.json
└── tests/
    └── test_smoke.py
```

### C. 带 Hosted UI(TSX 面板,38 个插件有 ui/)

```
my_plugin/
├── plugin.toml            # [plugin.ui] + [[plugin.ui.panel]]
├── __init__.py
├── ui/
│   ├── panel.tsx
│   └── tutorial.md
└── i18n/
```

### D. 大型插件(多文件拆分,>3000 行的都用这个)

```
forever_companion/
├── plugin.toml
├── __init__.py            # 只做装配:导入子模块、注册入口
├── core/                  # 状态机 / 调度
├── services/              # 外部 IO(HTTP、SQLite、文件)
├── entries/               # 按功能域拆的入口函数
├── static/  ui/  i18n/  tests/  docs/
└── config.example.toml
```

**反面教材**:把 8000 行全塞进 `__init__.py`(语料里最长 8291 行的那个)。能跑,但改不动。平均 1088 行,超过 2000 行就该拆包。

---

## 2. `plugin.toml` 配置格式

### 2.1 完整样例(取自 `shell_cmd`,带注释)

```toml
[plugin]
name = "猫娘命令操作器"                  # 市场卡片标题(中文,面向用户)
description = "让猫娘在对话里执行 Shell / CMD / PowerShell 命令……"  # 长描述:决定 Agent 能否理解能力
short_description = "Catgirl shell/cmd operator: ..."   # 一句话(英文亦可)
keywords = ["shell", "cmd", "命令", "猫娘"]              # 用户可能说的词,越大越容易被选中
version = "1.0.1"                        # 语义化版本
id = "shell_cmd"                         # 必须 = 目录名 = Python 包名
entry = "shell_cmd:ShellCmdPlugin"       # <id>:<类名>,类在该包 __init__.py 里

[plugin.author]
name = "baixiangyuan"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"           # 语料里 121 个都写这一档
supported = ">=0.1.0,<0.3.0"

[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"

[plugin_runtime]
enabled = true
auto_start = true                        # 62 个插件开自动启动(常驻类必须开)

[plugin.store]
enabled = true                           # 用 self.store 键值存储就必须开(68 个开了)

[plugin.ui]                              # 有界面才写
enabled = true

[[plugin.ui.panel]]
id = "main"
title = "命令面板"
entry = "ui/panel.tsx"
context = "main"
permissions = ["state:read", "action:call"]

# 插件自己的配置段:段名 = 插件 id
[shell_cmd]
timeout = 30
workdir = ""
allow_dangerous = false
```

### 2.2 字段规则

| 字段 | 必填 | 规则 | 语料实测 |
|---|---|---|---|
| `plugin.id` | ✅ | 小写 + 下划线,与目录名、Python 包名一致 | 125/126 一致 |
| `plugin.entry` | ✅ | `<模块>:<类名>`。**118 个用裸 id,只有 8 个老包写 `plugins.<id>:...`** | 以裸 id 为准 |
| `plugin.name` | ✅ | 中文产品名,不要写技术名 | 全部中文 |
| `plugin.description` | ✅ | 写清"能做什么 + 什么时候用",Agent 靠它选插件 | 越长越具体越好 |
| `plugin.short_description` | 建议 | 一行英文/中文摘要,市场卡片用 | 绝大多数都有 |
| `plugin.keywords` | ✅ | 数组,含用户口语与英文同义词 | 平均 6–10 个 |
| `plugin.version` | ✅ | `x.y.z`,跟 Git tag 同步 | 语义化 |
| `plugin.author.name` | ✅ | 市场账号名 | — |
| `plugin.sdk.recommended/supported` | ✅ | PEP 440 区间;新包统一 `<0.2.0` | 121 个一致 |
| `plugin_runtime.auto_start` | 建议 | 常驻/定时类 `true`,纯工具类 `false` | 62 true |
| `plugin.store.enabled` | 按需 | 用 `self.store` 才开 | 68 true |
| `plugin.ui.panel[]` | 按需 | `entry` 指向 `ui/*.tsx`,`permissions` 最小化 | 80 个有 ui |
| `[<plugin_id>]` 自定义段 | 按需 | 段名必须等于 id;每项写注释说明范围与默认值 | 72 个有 example |

### 2.3 三个最容易写错的地方

1. **`entry` 写错**:写成 `plugins.<id>` 只有历史包能跑,新包一律 `<id>:<ClassName>`。
2. **自定义段名不等于 id**:用户改配置时平台按 `[<id>]` 注入,段名写错就读不到。
3. **`keywords` 只写技术词**:用户不会说 "http tool",要写"搜一下""帮我查""来一首"。

---

## 3. 命名约定

| 对象 | 约定 | 示例 | 依据 |
|---|---|---|---|
| 目录 / `id` / 包名 | `snake_case`,单词间下划线 | `neko_diary` | 125/126 一致 |
| 插件类 | `XxxPlugin`,PascalCase | `NekoDiaryPlugin` | 102/124 以 Plugin 结尾 |
| 文件名 | `snake_case.py`;大型插件按域拆 `core/`、`services/` | `shell_core.py` | 语料惯例 |
| 入口 id(`@plugin_entry`) | `snake_case`,动词开头 | `write`、`get_summary`、`mark_all_read` | 109 个插件用 |
| LLM 工具名 | `<插件id>_<动作>`,全局唯一 | `shell_cmd_run`、`neko_diary_write` | 65 个插件用 |
| 定时器 id | `snake_case`,名词 | `monitor`、`tide_tick` | 19 个插件用 |
| 配置键 | `snake_case`,带注释与默认值 | `max_output_lines` | 全语料一致 |
| i18n key | 点分层级 | `panel.title`、`errors.network` | 65 个有 i18n 目录 |
| UI 面板 id | 小写单词 | `main`、`settings` | `[[plugin.ui.panel]]` |

**反例**:用中文/大写/连字符做 id(`My-Plugin`)——包名与 import 会直接失败。市场里没有这种包,平台审核也会打回。

---

## 4. 依赖声明

### 4.1 优先级(社区共识)

1. **标准库优先**:`urllib`、`json`、`sqlite3`、`asyncio`、`pathlib`。搜索类插件半数以上只依赖标准库。
2. **官方 SDK**:`from plugin.sdk.plugin import ...`,版本区间写进 `plugin.toml`,不要写 `>=0.0.0`。
3. **第三方库**:`httpx`(异步 HTTP 首选)、`pydantic`(SDK 已带)。避免 `requests`(阻塞事件循环)。
4. **重二进制**(`numpy`/`opencv`/`onnxruntime`):只有 5 个插件走 `vendor/` 自带,且体积巨大——能不引就不引。

### 4.2 声明位置

| 场景 | 做法 | 语料占比 |
|---|---|---|
| 纯标准库 + SDK | 不写依赖文件,只写 `[plugin.sdk]` | 多数 |
| 有第三方依赖 | `pyproject.toml` 写 `dependencies` | 91 个有 pyproject |
| 需要锁定/离线 | `requirements.txt` 或 `vendor/` 目录 | 少量 |
| 需要用户填 key | `config.example.toml` + README 说明 | 72 个 |

**打包边界**:`.neko-plugin` 只带插件目录本身;`vendor/`、`static/`、`ui/` 会一起打进去,`tests/`、`docs/` 也会,注意别放模型权重(单文件 >5MB 就该外置下载)。

---

## 5. 接口规范

### 5.1 装饰器矩阵(语料使用率)

| 装饰器 | 用途 | 使用插件数/124 | 要点 |
|---|---|---|---|
| `@neko_plugin` | 标记插件类 | 全部 | 必须有,类继承 `NekoPluginBase` |
| `@lifecycle` | 启动/停止/配置变更 | 117 | 启动里做初始化,`stop` 里收尾;不要阻塞 |
| `@plugin_entry` | 面板/Agent 显式入口 | 110 | `id` 全局唯一,`timeout` 必设 |
| `@llm_tool` | 让猫娘在对话里调用 | 66 | **市场第一大能力**,对话触发必须注册 |
| `@timer_interval` | 定时任务 | 20 | 回调里绝不 raise |
| `@message` | 监听总线消息 | 9 | 跨插件协作用 |
| `@dynamic_entry` | 运行时动态注册入口 | 少数 | 大型插件才用 |

### 5.2 入口函数签名

```python
@llm_tool(name="my_plugin_do", description="什么时候该用它……", parameters={...}, timeout=30.0)
@plugin_entry(id="do", name="做事", description="……", llm_result_fields=["ok", "result"])
async def do(self, target: str, **_):
    try:
        ...
        return Ok({"ok": True, "result": r})
    except Exception as e:
        return Err(SdkError(f"失败:{e}"))
```

硬规则:
- **必须 `async def`**,耗时操作 `await`;禁止 `time.sleep`、禁止在 import 期做 IO。
- 返回值只用 `Ok(...)` / `Err(SdkError(...))`,不要抛异常穿透。
- `**_` 收尾,容忍平台多传参数。
- 大结果用 `llm_result_fields` 裁剪,否则撑爆上下文。

### 5.3 四类典型能力

| 能力 | 写法 | 语料用量 |
|---|---|---|
| 主动说话 | `self.push_message(source=..., visibility=["chat"], ai_behavior="respond", parts=[{"type":"text","text":...}], priority=3)` | 55 |
| 持久化 | `self.data_path("data.json")` 读写 JSON;简单 KV 用 `self.store` | 44 / 19 |
| 联网 | `httpx.AsyncClient(timeout=...)`,失败给中文兜底 | 39 |
| 面板 | `static/` + `register_static_ui`,或 `ui/panel.tsx` + `[[plugin.ui.panel]]` | 34 / 38 |

### 5.4 i18n / 错误文案 / 日志

- 有界面的插件多数带 `i18n/`(66 个);无界面插件也建议所有用户可见文案走 `self.i18n.t("key")`。
- 错误返回给用户的话必须是**中文人话**,技术细节进 `self.logger.warning(...)`。
- 定时器/后台任务里所有异常必须吞掉并记日志——一个 raise 会让整个插件进程挂掉。

---

## 6. 全量插件索引(127 个)

> 入口数 = `@plugin_entry` 数量;工具数 = `@llm_tool` 数量;"源码"列 ✓ 表示本机有完整 `src/plugin.toml`。
> 下载量/作者等元数据见市场页面;这里只做**功能定位**用。


### 游戏 / 陪玩(46 个)

| 插件 | 名称 | 标签 | 入口 | 工具 | 源码 |
|---|---|---|---|---|---|
| `neko_tlm` | 酒狐插件 | 游戏/陪玩 | 12 | 13 | ✓ |
| `neko_warthunder` | 战雷猫娘副驾驶 | 游戏/陪玩 | 11 | 0 | ✓ |
| `pvz_agent` | PVZ Agent | 游戏/娱乐 | 12 | 11 | ✓ |
| `sts2_autoplay` | 尖塔2陪玩 | 游戏/辅助 | 22 | 6 | ✓ |
| `neko_calabiqiu` | 卡拉彼丘陪伴 | 游戏/陪玩 | 6 | 0 | ✓ |
| `mini_games` | 猫娘小游戏屋 | 游戏 | 6 | 6 | ✓ |
| `galgame_plugin` | Galgame游玩助手 | 游戏/辅助 | 0 | 0 | ✓ |
| `neko_arcade` | 猫娘小游戏 | 游戏/工具 | 16 | 2 | ✓ |
| `sticker_manager` | 表情包管理器 | 陪玩/互动 | 30 | 3 | ✓ |
| `neko_pawpilot` | 猫爪副驾 | 游戏/互动 | 19 | 0 | ✓ |
| `genshin_game_buddy` | 原神游戏搭子 demo | 游戏/查询 | 7 | 3 | ✓ |
| `neko_wows` | 战舰世界猫娘陪玩 | 游戏/陪玩 | 0 | 0 | ✓ |
| `neko_survivors` | 猫娘姐妹：幸存者 | 游戏/娱乐 | 3 | 0 | ✓ |
| `minecraft_autonomous` | Minecraft Autonomous · 我的世 | 游戏/互动 | 7 | 0 | ✓ |
| `neko_voice_bridge` | 猫娘语音频道（KOOK一对一版） | 游戏/陪玩 | 2 | 0 | ✓ |
| `kards_companion` | KARDS 猫娘陪玩 | 陪玩/游戏 | 12 | 4 | ✓ |
| `neko_sous` | NekoSous | 游戏/陪玩 | 4 | 0 | ✓ |
| `sfs_bridge` | 和猫娘一起造火箭(SFS) | 游戏/娱乐 | 9 | 18 | ✓ |
| `card_master` | 卡牌大师 | 游戏/陪玩 | 6 | 31 | ✓ |
| `neko_fishpower` | 猫爪渔力陪玩 | 游戏/情感 | 14 | 0 | ✓ |
| `endless_test` | 无尽测试 | 游戏/陪玩 | 8 | 2 | ✓ |
| `neko_rimworld` | N.E.K.O RimWorld | 游戏/查询 | 0 | 0 | ✓ |
| `civ6_buddy` | 文明6陪玩 | 游戏/攻略 | 0 | 17 | ✓ |
| `juno_bridge` | 和猫娘一起开火箭（朱诺新起源） | 游戏/辅助 | 3 | 8 | ✓ |
| `keyboard_controller` | 按键控制 keyboard_controller | 游戏/娱乐 | 62 | 0 | ✓ |
| `ksp_bridge` | 和猫娘一起上太空（坎巴拉太空计划副驾驶） | 游戏/查询 | 2 | 8 | ✓ |
| `miao_miao_bump` | 喵喵碰碰大作战 | 游戏/娱乐 | 0 | 0 | ✓ |
| `neko_stellaris` | 星辰喵语-群星插件 | 陪玩 | 12 | 0 | ✓ |
| `neko_terraria` | 泰拉瑞亚陪玩 | 游戏/互动 | 0 | 0 | ✓ |
| `tc_companion` | 图灵完备助手 | 游戏/攻略 | 7 | 7 | ✓ |
| `ai_learns_to_drive` | 学车助手 | 游戏/攻略 | 5 | 4 | ✓ |
| `from_the_heart` | 从心开始插件 | 游戏/陪玩 | 1 | 0 | ✓ |
| `hearthstone_companion` | 炉石猫娘陪玩 | 陪玩 | 9 | 2 | ✓ |
| `hsr_companion` | 星铁游戏搭子 | 游戏/陪玩 | 31 | 11 | ✓ |
| `mcbot_bridge` | MCBot 同伴桥接 | 游戏/陪玩 | 3 | 7 | ✓ |
| `minesweeper` | 合作扫雷 | 游戏/陪玩 | 2 | 4 | ✓ |
| `mo_realistic_adjutant` | MO更真实的副官 | 游戏/陪玩 | 0 | 0 | ✓ |
| `mock_stock_meow` | 拟股喵喵 | 游戏/互动 | 28 | 8 | ✓ |
| `moneta_fx` | MONETA FX · 外汇模拟交易室 | 游戏/辅助 | 0 | 0 | ✓ |
| `neko_ads` | 猫娘广告部：杂鱼桌面占领计划 | 游戏/互动 | 4 | 0 | ✓ |
| `openrails_copilot` | Open Rails 副驾驶 | 游戏/陪玩 | 5 | 0 | ✓ |
| `source_player` | 半条命 & 传送门 陪玩 | 游戏/查询 | 6 | 12 | ✓ |
| `sp2_copilot` | 猫娘飞行仪表盘（简单飞机2） | 游戏/查询 | 2 | 8 | ✓ |
| `teach_sister_drive` | 教妹妹开车 | 游戏/辅助 | 6 | 0 | ✓ |
| `tfwr_companion` | 农夫助手 | 游戏/攻略 | 6 | 6 | ✓ |
| `yui_home` | Yui的家 | 游戏/陪玩 | 9 | 0 | ✓ |

### 工具 / 系统 / 监控(31 个)

| 插件 | 名称 | 标签 | 入口 | 工具 | 源码 |
|---|---|---|---|---|---|
| `sys_monitor` | 电脑监控 | 系统/监控 | 5 | 0 | ✓ |
| `web_searching` | 联网搜索 | 工具/开发 | 5 | 3 | ✓ |
| `app_launcher` | 应用启动器 | 工具 | 12 | 10 | ✓ |
| `shell_cmd` | 猫娘命令操作器 | 监控/系统 | 5 | 5 | ✓ |
| `tarot` | 塔罗牌占卜 | 工具/娱乐 | 21 | 11 | ✓ |
| `writer_power_analysis` | 文本分析 | 辅助/功能 | 9 | 0 | ✓ |
| `neko_token_monitor` | 猫粮监测 | 监控 | 3 | 2 | ✓ |
| `bili_viewer` | B站看板 | 工具/API | 2 | 60 | ✓ |
| `neko_clipboard_watcher` | 剪贴板猫娘 | 辅助/监控 | 3 | 1 | ✓ |
| `neko_mail` | 猫娘邮件秘书 | 工具/AI | 24 | 24 | ✓ |
| `yuilock` | Yui 手机/电脑锁 | 管理/监控 | 6 | 4 | ✓ |
| `steam_manager` | Steam管理 | 工具 | 1 | 15 | ✓ |
| `suno_cn_music` | Suno音乐生成 | 辅助/API | 10 | 0 | ✓ |
| `codex_adapter` | Codex Adapter | 工具/开发 | 1 | 9 | ✓ |
| `neko_assessment` | 心理测评室 | 辅助/工具 | 0 | 1 | ✓ |
| `tavily_search` | Tavily 搜索 | 工具/AI | 2 | 0 | ✓ |
| `mail_all` | 猫娘邮件 | 系统/监控 | 0 | 0 | ✓ |
| `neko_natural_command` | 自然语言命令（轻量版） | 系统/辅助 | 8 | 1 | ✓ |
| `git_memory` | Git 记忆同步 | 工具/功能 | 2 | 0 | ✓ |
| `workbench_installer` | Agent工作台安装器 | 工具/代码 | 0 | 0 | ✓ |
| `code_sandbox` | 代码沙箱 | 工具 | 1 | 1 | ✓ |
| `neko_163mail` | 网易邮箱助手 | 工具/功能 | 26 | 26 | ✓ |
| `skill_compat` | skill兼容 | 工具 | 3 | 10 | ✓ |
| `neko_morning_brief` | 猫娘晨报 | 工具/API | 3 | 2 | ✓ |
| `claude_code_adapter` | Claude Code Adapter | 系统/代码 | 0 | 1 | ✓ |
| `neko_ledger` | 记账猫娘 | 工具/查询 | 6 | 5 | ✓ |
| `better_txt_output` | 更好的文本输出 | 辅助/工具 | 0 | 0 | 仅文档 |
| `development_aide` | Development_Aide | 工具/API | 10 | 0 | ✓ |
| `neko_perf_saver` | 性能守护 | 系统/游戏 | 11 | 0 | ✓ |
| `screen_reader` | 屏幕阅读器 | 辅助/API | 1 | 2 | ✓ |
| `shale_core_lib` | 页岩核心Lib | 辅助/功能 | 0 | 0 | 仅文档 |

### 查询 / 数据 / API(16 个)

| 插件 | 名称 | 标签 | 入口 | 工具 | 源码 |
|---|---|---|---|---|---|
| `qq_auto_reply` | QQ集成 | API/互动 | 7 | 0 | ✓ |
| `study_companion` | 伴学插件 | 学习/AI | 0 | 0 | ✓ |
| `anysearch` | AnySearch 联网搜索 | 查询/系统 | 2 | 0 | ✓ |
| `store_search` | 插件商店搜索 | 查询/功能 | 2 | 2 | ✓ |
| `deepseekharness_bridge` | DeepSeek Harness 桥接 | 查询/功能 | 7 | 1 | ✓ |
| `neko_study_copilot` | 学习辅助猫娘 | 查询/辅助 | 20 | 17 | ✓ |
| `bililearn_bridge` | BiliLearn 桥接 | 查询/互动 | 8 | 0 | ✓ |
| `neko_weather` | 天气猫娘 | 查询/功能 | 4 | 5 | ✓ |
| `meeting_insight` | 联网搜索助手 | API/AI | 1 | 4 | ✓ |
| `data_backup` | 猫娘备份插件 | 数据/工具 | 6 | 0 | ✓ |
| `luogu` | 洛谷猫娘 | 查询/学习 | 11 | 0 | ✓ |
| `quote_extract` | 语录抽取 | 查询/生成 | 3 | 1 | ✓ |
| `apibook` | 便于切换的apikey与模型id组 | 数据/API | 6 | 0 | ✓ |
| `hitokoto` | 一言 · Hitokoto | API/情感 | 7 | 3 | ✓ |
| `recall` | Recall 陪伴式记忆与复习 | 学习/功能 | 13 | 0 | ✓ |
| `testbench` | testbench插件 | 数据/系统 | 4 | 0 | ✓ |

### 互动 / 情感 / 增强(18 个)

| 插件 | 名称 | 标签 | 入口 | 工具 | 源码 |
|---|---|---|---|---|---|
| `forever_companion` | 永远的陪伴 | 情感/增强 | 0 | 2 | ✓ |
| `neko_diary` | 猫娘日记 | 互动/情感 | 9 | 9 | ✓ |
| `better_web_search` | 更好的网络搜索 | 增强/功能 | 14 | 0 | ✓ |
| `neko_live` | 直播助手 | 互动/辅助 | 34 | 0 | ✓ |
| `proactive_recommender` | OpenBiliClaw 个性化推荐兼容层 | 互动/增强 | 4 | 0 | ✓ |
| `bilibili_integration` | B站集成 | AI/工具 | 13 | 0 | ✓ |
| `mijia` | 米家 | 互动/工具 | 13 | 0 | ✓ |
| `coyote_control` | 猫娘控制郊狼 | 情感/互动 | 1 | 3 | ✓ |
| `neko_mcp_serve` | 外部控制 | 互动/辅助 | 4 | 0 | ✓ |
| `selfie` | 自拍 | 互动/情感 | 0 | 0 | ✓ |
| `space_launch` | 太空发射 | AI/功能 | 8 | 6 | ✓ |
| `neko_community_chat` | 喵宇宙社区聊天 | 互动/功能 | 0 | 4 | ✓ |
| `wechat_integration` | 微信集成 | 互动/AI | 9 | 0 | ✓ |
| `browser_skill` | BrowserSkill 浏览器控制 | 增强/功能 | 6 | 1 | ✓ |
| `call_mom` | Call Mom | 情感/功能 | 3 | 0 | ✓ |
| `mood_state` | 精力/状态插件 | 增强/情感 | 5 | 2 | ✓ |
| `plugin_knowledge_base` | Plugin Knowledge Base | 增强/功能 | 11 | 0 | ✓ |
| `plugin_skill_manager` | Plugin Skill Manager | 增强/功能 | 7 | 0 | ✓ |

### 媒体 / 创作(5 个)

| 插件 | 名称 | 标签 | 入口 | 工具 | 源码 |
|---|---|---|---|---|---|
| `custom_music_list` | 音乐歌单管理 | 音乐/查询 | 49 | 11 | ✓ |
| `neko_video` | 看视频猫娘 | 视频/陪玩 | 3 | 0 | ✓ |
| `neko_translate` | 翻译猫娘 | 翻译 | 3 | 3 | ✓ |
| `xiyin_pavilion` | 汐音阁 | 音乐/工具 | 16 | 2 | ✓ |
| `media_generator` | 媒体生成器 | 图像/生成 | 2 | 2 | ✓ |

### 其他(11 个)

| 插件 | 名称 | 标签 | 入口 | 工具 | 源码 |
|---|---|---|---|---|---|
| `catgirl_daily_planner` | 猫娘每日计划 | 提醒 | 24 | 24 | ✓ |
| `course_schedule` | 猫娘课程表 | 功能/工具 | 0 | 0 | ✓ |
| `neko_mail_agently` | 猫娘邮箱(Agently) | 功能/工具 | 19 | 19 | ✓ |
| `phone_workflows` | Phone Workflows | 功能/工具 | 1 | 4 | ✓ |
| `agent_bridge` | 本机 Agent 桥接 | 功能/工具 | 4 | 5 | ✓ |
| `autocad_assistant_tool` | AutoCAD辅助绘图 | 功能/工具 | 5 | 0 | ✓ |
| `dsh_bridge` | n.e.k.o_plugin_dsh_bridge | 功能/工具 | 2 | 0 | ✓ |
| `neko_done_bridge` | D-one 任务管家 | 功能 | 5 | 0 | ✓ |
| `neko_jam_band` | 猫娘伴奏团 | 功能 | 7 | 0 | ✓ |
| `neko_live_album` | 猫娘实况相册 | 功能 | 2 | 0 | ✓ |
| `plugin_database` | Plugin Database | 功能/增强 | 13 | 0 | ✓ |


---

## 7. 学习路径与选型

| 要做的东西 | 先读谁 | 学什么 |
|---|---|---|
| 对话里能触发的小工具 | `shell_cmd` / `mini_games` / `neko_diary` | `@llm_tool` + `@plugin_entry` 双注册、结果裁剪 |
| 联网查询/聚合 | `anysearch` / `tavily_search` / `web_searching` | httpx 超时、失败兜底、结果限流 |
| 定时提醒/监控 | `sys_monitor` / `catgirl_daily_planner` | 防重复提醒状态机、`push_message` v2 |
| 记录/日记/存储 | `neko_diary` / `data_backup` | `data_path` JSON 结构、迁移与备份 |
| 带网页面板 | `anysearch`(TSX)/ `xiyin_pavilion`(static) | 面板注册、state:read/action:call 权限 |
| 大型多功能 | `forever_companion` / `keyboard_controller` | 拆分 `core/services/entries`、动态入口 |
| 游戏陪玩/遥测 | `neko_wows` / `hearthstone_companion` | 读取公开状态 → 结构化事实 → 情绪信号 |

## 8. 交付前自检清单(逐条打勾)

1. 目录名 = `plugin.id` = Python 包名,全小写下划线
2. `plugin.entry` = `<id>:<ClassName>`,类继承 `NekoPluginBase` 且带 `@neko_plugin`
3. `description` 写清"做什么 + 何时用",`keywords` 含用户口语
4. `[plugin.sdk]` 写区间,不写 `*`
5. 所有入口 `async def` + `**_`,有 `timeout`
6. 对话可触发的能力都注册了 `@llm_tool`,名字是 `<id>_<动作>`
7. 返回 `Ok/Err`,不抛裸异常;错误文案是中文人话
8. 用 `self.store` 时 `[plugin.store] enabled = true`
9. 持久化走 `self.data_path()`,不用相对路径/绝对路径
10. 定时器回调吞异常;有防重复触发
11. 网络请求有超时 + 兜底;不阻塞事件循环
12. 有界面时 `[[plugin.ui.panel]]` 的 `permissions` 最小化
13. 用户可见文案走 i18n(至少中文);`config.example.toml` 覆盖所有可配置项
14. `python -m plugin.neko_plugin_cli check <id>` 通过,无 error
15. `build` 产物只含必要文件,单文件不超过 5MB

---

### 附:本次抓取说明

- 接口:`GET https://market.project-neko.cn/api/v1/plugins?page=N&page_size=100`(共 127 条,2 页)
- 下载:按 `repo_url` 取 GitHub `archive/refs/heads/{main,master}.zip`,镜像 `ghfast.top` 优先
- 落盘:126/127 份源码;其余仓库无 `plugin.toml`(纯库/纯文档),已标注
- 瘦身:剔除图片/音频/模型/可执行文件与 >1.5MB 资产,只留代码、配置、文档;总体积约 125MB
- 统计口径:126 份 `plugin.toml`、124 份 `__init__.py`
