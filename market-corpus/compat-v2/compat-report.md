# 市场插件兼容快照 v2(猫爪重写 #3683 静态扫描)

- 扫描日期: 2026-11-14 · RFC 状态: #3685 merged 2026-11-11; #3692 merged 2026-11-12; #3705 merged 2026-11-14; #3714 open(实时) · 原始数据: scan_result.json(含每仓库 commit SHA)
- 全量收录 144 个在架插件: 受影响 2 · 仅文档测试面 4 · 待核 12 · 兼容 126 · 未验证 0
- 口径: 兼容=strong+review 全零命中,行为项 4/5/6 仍需作者自查;待核=只命中 review 级特征(端口号/路由字样/文档提及),不自动发 issue;未验证=源码不可得,不代表兼容。
- issue-drafts/ 是草稿,人工核实后才允许发布;快照不做门禁。

## 需要动作的插件

| 状态 | 插件 | 作者 | 下载 | 命中 |
|---|---|---|---|---|
| 受影响 | 猫娘小游戏 (neko_arcade @f077eee) | luoxue | 231 | bundled_playwright; host_internal_import; playwright_mention |
| 受影响 | Galgame游玩助手 (galgame_plugin @d3748e4) | Momiji | 69 | deleted_endpoint; legacy_agent_port |
| 受影响(仅文档/测试) | 喵喵碰碰大作战 (miao_miao_bump @796efcf) | wfrostune | 32 | bundled_playwright |
| 受影响(仅文档/测试) | N.E.K.O RimWorld (neko_rimworld @35a9fbd) | yunwan233 | 18 | host_internal_import |
| 受影响(仅文档/测试) | 炉石猫娘陪玩 (hearthstone_companion @e6924b9) | Arcobalenorf | 0 | bundled_playwright; deleted_endpoint |
| 受影响(仅文档/测试) | 星铁游戏搭子 (hsr_companion @921f3cf) | wfrostune | 0 | host_internal_import |
| 待核 | 永远的陪伴 (forever_companion @ab6c8ca) | qaqms | 635 | capabilities_unconfirmed |
| 待核 | QQ集成 (qq_auto_reply @1b75286) | ZhaiJiu | 258 | openclaw_name |
| 待核 | 伴学插件 (study_companion @365fcb6) | Momiji | 214 | openclaw_name |
| 待核 | 表情包管理器 (sticker_manager @7479f25) | qaqms | 150 | deferred_symbol |
| 待核 | Minecraft Autonomous · 我的世界自主游玩 (minecraft_autonomous @040c44f) | jcs130 | 62 | capabilities_unconfirmed |
| 待核 | Suno音乐生成 (suno_cn_music @f6b8740) | ZhaiJiu | 46 | openclaw_name |
| 待核 | 微信集成 (wechat_integration @f942332) | xxynet | 43 | openclaw_name |
| 待核 | KARDS 猫娘陪玩 (kards_companion @20da4c3) | Luotianyi | 35 | capabilities_unconfirmed |
| 待核 | 无尽测试 (endless_test @ba0153c) | tangbai | 17 | host_internal_import |
| 待核 | 心理测评室 (neko_assessment @5902ac9) | MENGYAOYUE | 8 | legacy_agent_port |
| 待核 | 本机 Agent 桥接 (agent_bridge @a8fea46) | Cloudy | 2 | openclaw_name |
| 待核 | NekoStardew (stardew_companion @dc81e4a) | JingTou0225 | 0 | legacy_agent_port |

## 待核明细(不要求改动,人工抽查)

- 永远的陪伴 :: `CHANGELOG.md:1320` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `CHANGELOG.md:1327` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `CHANGELOG.md:1388` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `README.md:461` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `__init__.py:38` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `core/intros.py:8` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `core/state.py:125` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `mixins/capabilities.py:3` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `tests/test_agent_surface.py:39` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `tests/test_capabilities.py:4` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `tests/test_i18n_contract.py:7` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `tools/hosted_ui_harness.mjs:61` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 永远的陪伴 :: `tools/HOSTED_UI_QA.md:782` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- QQ集成 :: `qq_official_bind.py:11` openclaw_name — 
- QQ集成 :: `qq_official_bind.py:48` openclaw_name — 
- QQ集成 :: `docs/GROUP-CHAT-MECHANISMS-ROUND2.md:14` openclaw_name — 
- QQ集成 :: `docs/GROUP-CHAT-MECHANISMS-ROUND2.md:117` openclaw_name — 
- QQ集成 :: `docs/GROUP-CHAT-MECHANISMS-ROUND2.md:554` openclaw_name — 
- QQ集成 :: `docs/GROUP-CHAT-MECHANISMS-ROUND2.md:572` openclaw_name — 
- QQ集成 :: `docs/SESSION-HANDOFF.md:3996` openclaw_name — 
- QQ集成 :: `docs/UPSTREAM-LINEAGE.md:49` openclaw_name — 
- QQ集成 :: `docs/UPSTREAM-LINEAGE.md:240` openclaw_name — 
- QQ集成 :: `tests/test_qq_bind_start_entry.py:32` openclaw_name — 
- QQ集成 :: `tests/test_qq_official_bind.py:153` openclaw_name — 
- 伴学插件 :: `docs/认知引擎阶段文档.md:1190` openclaw_name — 
- 表情包管理器 :: `tests/test_split_delivery_contract.py:167` deferred_symbol — 命中 deferred/bind_task 字样。RFC 第 6 节:直达路径保持现有行为;确实在用 deferred 机制的请去 #3683 回帖,不必改代码。
- 表情包管理器 :: `tests/test_split_delivery_contract.py:229` deferred_symbol — 命中 deferred/bind_task 字样。RFC 第 6 节:直达路径保持现有行为;确实在用 deferred 机制的请去 #3683 回帖,不必改代码。
- Minecraft Autonomous · 我的世界自主游玩 :: `docs/superpowers/plans/2026-10-12-agent-mc-world-port.md:42` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- Suno音乐生成 :: `README.md:47` openclaw_name — 
- 微信集成 :: `plugin.toml:3` openclaw_name — 
- 微信集成 :: `plugin.toml:5` openclaw_name — 
- 微信集成 :: `README.md:3` openclaw_name — 
- 微信集成 :: `README.md:14` openclaw_name — 
- 微信集成 :: `wechat_client.py:12` openclaw_name — 
- 微信集成 :: `__init__.py:283` openclaw_name — 
- 微信集成 :: `__init__.py:418` openclaw_name — 
- 微信集成 :: `doc/README.md:3` openclaw_name — 
- 微信集成 :: `doc/README.md:14` openclaw_name — 
- 微信集成 :: `i18n/zh-CN.json:3` openclaw_name — 
- 微信集成 :: `i18n/zh-CN.json:7` openclaw_name — 
- 微信集成 :: `i18n/zh-CN.json:11` openclaw_name — 
- 微信集成 :: `i18n/zh-CN.json:54` openclaw_name — 
- 微信集成 :: `i18n/zh-TW.json:3` openclaw_name — 
- 微信集成 :: `i18n/zh-TW.json:7` openclaw_name — 
- 微信集成 :: `i18n/zh-TW.json:11` openclaw_name — 
- 微信集成 :: `i18n/zh-TW.json:54` openclaw_name — 
- 微信集成 :: `static/index.html:78` openclaw_name — 
- KARDS 猫娘陪玩 :: `delivery/publisher.py:2081` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- KARDS 猫娘陪玩 :: `delivery/publisher.py:2237` capabilities_unconfirmed — 命中 /capabilities 字样,可能是插件自己的路由而非 48915 上的待删接口,需人工确认调用对象。
- 无尽测试 :: `__init__.py:53` host_internal_import — 
- 心理测评室 :: `_yui_link.py:166` legacy_agent_port — 只命中了裸 48915 端口号。若只访问 /health 等仍存活接口则无需改动;若调用了 4.2 具名接口才需要处理。
- 本机 Agent 桥接 :: `plugin.toml:4` openclaw_name — 
- 本机 Agent 桥接 :: `plugin.toml:6` openclaw_name — 
- 本机 Agent 桥接 :: `README.md:6` openclaw_name — 
- 本机 Agent 桥接 :: `bridge/registry.py:189` openclaw_name — 
- 本机 Agent 桥接 :: `bridge/registry.py:190` openclaw_name — 
- 本机 Agent 桥接 :: `bridge/registry.py:191` openclaw_name — 
- 本机 Agent 桥接 :: `bridge/registry.py:192` openclaw_name — 
- 本机 Agent 桥接 :: `bridge/registry.py:235` openclaw_name — 
- 本机 Agent 桥接 :: `docs/harness-landscape.md:36` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/frameworks.toml:200` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/frameworks.toml:201` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/frameworks.toml:207` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/SKILL.md:5` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/SKILL.md:50` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/bin/neko.py:7` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/bin/neko.py:195` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/bin/neko.py:205` openclaw_name — 
- 本机 Agent 桥接 :: `skill/neko-bridge/bin/neko.py:1164` openclaw_name — 
- 本机 Agent 桥接 :: `tests/test_providers.py:141` openclaw_name — 
- 本机 Agent 桥接 :: `tools/inventory.py:48` openclaw_name — 
- NekoStardew :: `README.md:80` legacy_agent_port — 只命中了裸 48915 端口号。若只访问 /health 等仍存活接口则无需改动;若调用了 4.2 具名接口才需要处理。

## 三态一览(全部)

| 状态 | 插件 | 作者 | 下载 |
|---|---|---|---|
| 受影响 | 猫娘小游戏 | luoxue | 231 |
| 受影响 | Galgame游玩助手 | Momiji | 69 |
| 受影响(仅文档/测试) | 喵喵碰碰大作战 | wfrostune | 32 |
| 受影响(仅文档/测试) | N.E.K.O RimWorld | yunwan233 | 18 |
| 受影响(仅文档/测试) | 炉石猫娘陪玩 | Arcobalenorf | 0 |
| 受影响(仅文档/测试) | 星铁游戏搭子 | wfrostune | 0 |
| 待核 | 永远的陪伴 | qaqms | 635 |
| 待核 | QQ集成 | ZhaiJiu | 258 |
| 待核 | 伴学插件 | Momiji | 214 |
| 待核 | 表情包管理器 | qaqms | 150 |
| 待核 | Minecraft Autonomous · 我的世界自主游玩 | jcs130 | 62 |
| 待核 | Suno音乐生成 | ZhaiJiu | 46 |
| 待核 | 微信集成 | xxynet | 43 |
| 待核 | KARDS 猫娘陪玩 | Luotianyi | 35 |
| 待核 | 无尽测试 | tangbai | 17 |
| 待核 | 心理测评室 | MENGYAOYUE | 8 |
| 待核 | 本机 Agent 桥接 | Cloudy | 2 |
| 待核 | NekoStardew | JingTou0225 | 0 |
| 兼容 | 战雷猫娘副驾驶 | CN-Zephyr | 2392 |
| 兼容 | 酒狐插件 | ggg233m | 1308 |
| 兼容 | 尖塔2陪玩 | ZhaiJiu | 1242 |
| 兼容 | PVZ Agent | Old_tangyuan | 846 |
| 兼容 | 按键控制 keyboard_controller | tpe | 433 |
| 兼容 | 猫娘日记 | StarrySerendipity | 402 |
| 兼容 | 联网搜索 | StarrySerendipity | 329 |
| 兼容 | 音乐歌单管理 | dustnunknown | 289 |
| 兼容 | 看视频猫娘 | yousei1216 | 181 |
| 兼容 | 电脑监控 | xy28816 | 173 |
| 兼容 | 猫娘每日计划 | StarrySerendipity | 166 |
| 兼容 | 猫娘姐妹：幸存者 | yousei1216 | 135 |
| 兼容 | 塔罗牌占卜 | StarrySerendipity | 104 |
| 兼容 | Steam管理 | jianglaer | 99 |
| 兼容 | 直播助手 | CN-Zephyr | 98 |
| 兼容 | B站看板 | jianglaer | 89 |
| 兼容 | 应用启动器 | StarrySerendipity | 85 |
| 兼容 | 她的一天 | Coder_Ksheng | 84 |
| 兼容 | 猫娘小游戏屋 | bxy | 81 |
| 兼容 | DeepSeek Harness 桥接 | bxy | 74 |
| 兼容 | 插件商店搜索 | xxynet | 74 |
| 兼容 | 翻译猫娘 | yousei1216 | 68 |
| 兼容 | 猫娘命令操作器 | bxy | 64 |
| 兼容 | 卡拉彼丘陪伴 | CN-Zephyr | 63 |
| 兼容 | 为她而唱 | mkw | 59 |
| 兼容 | AnySearch 联网搜索 | bxy | 58 |
| 兼容 | 文本分析 | ZhaiJiu | 50 |
| 兼容 | 猫娘控制郊狼 | venlacy | 43 |
| 兼容 | 汐音阁 | StarrySerendipity | 38 |
| 兼容 | BiliLearn 桥接 | bxy | 36 |
| 兼容 | 猫娘晨报 | Hacker_LawKing | 36 |
| 兼容 | 天气猫娘 | yousei1216 | 33 |
| 兼容 | 猫娘邮箱(Agently) | StarrySerendipity | 32 |
| 兼容 | Codex Adapter | StarrySerendipity | 29 |
| 兼容 | 原神游戏搭子 demo | alpha | 26 |
| 兼容 | 泰拉瑞亚陪玩 | luoxue | 26 |
| 兼容 | B站集成 | xxynet | 25 |
| 兼容 | 媒体生成器 | jianglaer | 25 |
| 兼容 | 猫娘邮件秘书 | StarrySerendipity | 25 |
| 兼容 | 猫爪副驾 | luoxue | 25 |
| 兼容 | Yui 手机/电脑锁 | bxy | 25 |
| 兼容 | 战舰世界猫娘陪玩 | Lex_q | 24 |
| 兼容 | 猫娘语音频道（KOOK一对一版） | qiguang | 23 |
| 兼容 | 猫粮监测 | Acekira | 21 |
| 兼容 | 剪贴板猫娘 | MENGYAOYUE | 20 |
| 兼容 | 星辰喵语-群星插件 | ZhaiJiu | 20 |
| 兼容 | 教妹妹开车 | CN-Zephyr | 20 |
| 兼容 | 猫娘课程表 | lingtong | 18 |
| 兼容 | 联网搜索助手 | alpha | 18 |
| 兼容 | 米家 | xxhhy | 18 |
| 兼容 | 拟股喵喵 | alpha | 18 |
| 兼容 | 和猫娘一起造火箭(SFS) | piao1145 | 18 |
| 兼容 | OpenBiliClaw 个性化推荐兼容层 | nekopara | 17 |
| 兼容 | Claude Code Adapter | StarrySerendipity | 16 |
| 兼容 | 图像记忆库 | Cloudy | 16 |
| 兼容 | 自拍 | xxynet | 16 |
| 兼容 | 代码沙箱 | jianglaer | 14 |
| 兼容 | 沙盒工具 | xy28816 | 14 |
| 兼容 | 卡牌大师 | jianglaer | 13 |
| 兼容 | 外部控制 | xy28816 | 13 |
| 兼容 | 学习辅助猫娘 | MENGYAOYUE | 13 |
| 兼容 | skill兼容 | jianglaer | 13 |
| 兼容 | Tavily 搜索 | xxynet | 13 |
| 兼容 | 猫猫可以看视频 | ZhaiJiu | 13 |
| 兼容 | 猫娘邮件 | bxy | 11 |
| 兼容 | 屏幕阅读器 | jianglaer | 11 |
| 兼容 | Agent工作台安装器 | fengdiehualian | 11 |
| 兼容 | 猫娘备份插件 | nekopara | 10 |
| 兼容 | 网易邮箱助手 | StarrySerendipity | 10 |
| 兼容 | 猫爪渔力陪玩 | luoxue | 10 |
| 兼容 | 泰坦陨落2 陪玩 | Birundu | 10 |
| 兼容 | 文明6陪玩 | jianglaer | 9 |
| 兼容 | 洛谷猫娘 | ZhaiJiu | 9 |
| 兼容 | 记账猫娘 | yousei1216 | 8 |
| 兼容 | NekoSous | 未知作者 | 8 |
| 兼容 | 猫娘广告部：杂鱼桌面占领计划 | wfrostune | 7 |
| 兼容 | 语录抽取 | bxy | 7 |
| 兼容 | 半条命 & 传送门 陪玩 | Birundu | 7 |
| 兼容 | fnOS 管理 | bxy | 6 |
| 兼容 | 弹幕猫娘 | luoxue | 6 |
| 兼容 | 深海迷航 & 零度之下 陪玩 | Birundu | 6 |
| 兼容 | 太空发射 | piao1145 | 5 |
| 兼容 | 图灵完备助手 | Cloudy | 5 |
| 兼容 | 和猫娘一起上太空（坎巴拉太空计划副驾驶） | piao1145 | 4 |
| 兼容 | 影之刃零 · 猫娘陪玩 | luoxue | 4 |
| 兼容 | 自然语言命令（轻量版） | MENGYAOYUE | 3 |
| 兼容 | 万物链接·数据库插件 | Cloudy | 3 |
| 兼容 | 猫娘飞行仪表盘（简单飞机2） | piao1145 | 3 |
| 兼容 | Git 记忆同步 | kaoyu666 | 2 |
| 兼容 | 一言 · Hitokoto | Alumin_Hydro | 2 |
| 兼容 | 和猫娘一起开火箭（朱诺新起源） | piao1145 | 2 |
| 兼容 | MONETA FX · 外汇模拟交易室 | bxy | 2 |
| 兼容 | 学车助手 | Cloudy | 1 |
| 兼容 | 农夫助手 | Cloudy | 1 |
| 兼容 | N.E.K.O Ama Monster OS | Birundu | 0 |
| 兼容 | Among us 猫娘玩家 | 未知作者 | 0 |
| 兼容 | 便于切换的apikey与模型id组 | KirisameYuumio | 0 |
| 兼容 | AutoCAD辅助绘图 | lingtong | 0 |
| 兼容 | 更好的文本输出 | bai_de_hei_ban | 0 |
| 兼容 | 岁岁念 | mucage | 0 |
| 兼容 | BrowserSkill 浏览器控制 | ggg233m | 0 |
| 兼容 | Call Mom | MahOAO | 0 |
| 兼容 | Development_Aide | Admin | 0 |
| 兼容 | n.e.k.o_plugin_dsh_bridge | 未知作者 | 0 |
| 兼容 | 从心开始插件 | Momiji | 0 |
| 兼容 | KARDS AI 助手 | Luotianyi | 0 |
| 兼容 | MCBot 同伴桥接 | xxhhy | 0 |
| 兼容 | 合作扫雷 | az_az | 0 |
| 兼容 | MO更真实的副官 | Dawn_Meng | 0 |
| 兼容 | 精力/状态插件 | cowhorsee | 0 |
| 兼容 | 对话编年史 | tan199503 | 0 |
| 兼容 | D-one 任务管家 | Acekira | 0 |
| 兼容 | 猫娘伴奏团 | Acekira | 0 |
| 兼容 | 猫娘实况相册 | Acekira | 0 |
| 兼容 | 性能守护 | ele2712 | 0 |
| 兼容 | 网易云音乐 | Kemo | 0 |
| 兼容 | Open Rails 副驾驶 | xxhhy | 0 |
| 兼容 | Phone Workflows | CtrlCreeper | 0 |
| 兼容 | 万物链接·知识库插件 | Cloudy | 0 |
| 兼容 | 万物链接·skill管理模块 | Cloudy | 0 |
| 兼容 | 猫娘也要用QQ | leleking | 0 |
| 兼容 | Recall 陪伴式记忆与复习 | wfrostune | 0 |
| 兼容 | 先搜后建 | jianglaer | 0 |
| 兼容 | 页岩核心Lib | bai_de_hei_ban | 0 |
| 兼容 | testbench插件 | TL0SR2 | 0 |
| 兼容 | Yui的家 | Acekira | 0 |