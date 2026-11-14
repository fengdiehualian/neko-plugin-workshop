# 市场插件兼容快照 v2(猫爪重写 #3683 静态扫描)

- 扫描日期: 2026-11-14 · 生成: tools/gen_compat_report.py · 原始数据: scan_result.json
- RFC 状态: PR1 #3685 已合并; PR2 #3692 已合并; 切换点 PR3 #3705 已于 2026-11-14 合并; PR4-6 未开
- 全量收录 144 个在架插件: 需处理 4 · 仅文档测试面 6 · 兼容(静态) 134 · 未验证 0
- 口径: 兼容=静态扫描四类会删面零命中,行为项 4/5/6 仍需作者自查;未验证=源码不可得,不代表兼容.
- issue-drafts/ 是草稿,人工核实后才允许发布;快照不做门禁,不收录任何插件.

| 状态 | 插件 | 作者 | 下载 | 命中 |
|---|---|---|---|---|
| 受影响 | 猫娘小游戏 (neko_arcade) | luoxue | 231 | bundled_playwright |
| 受影响 | Galgame游玩助手 (galgame_plugin) | Momiji | 69 | deleted_endpoint |
| 受影响 | 无尽测试 (endless_test) | tangbai | 17 | host_internal_import |
| 受影响 | 心理测评室 (neko_assessment) | MENGYAOYUE | 8 | deleted_endpoint |
| 受影响(仅文档/测试) | 表情包管理器 (sticker_manager) | qaqms | 150 | deferred_channel |
| 受影响(仅文档/测试) | 喵喵碰碰大作战 (miao_miao_bump) | wfrostune | 32 | bundled_playwright |
| 受影响(仅文档/测试) | N.E.K.O RimWorld (neko_rimworld) | yunwan233 | 18 | host_internal_import |
| 受影响(仅文档/测试) | NekoStardew (stardew_companion) | JingTou0225 | 0 | deleted_endpoint |
| 受影响(仅文档/测试) | 星铁游戏搭子 (hsr_companion) | wfrostune | 0 | host_internal_import |
| 受影响(仅文档/测试) | 炉石猫娘陪玩 (hearthstone_companion) | Arcobalenorf | 0 | bundled_playwright; deleted_endpoint |

## 全部 144 个的三态一览

| 状态 | 插件 | 作者 | 下载 |
|---|---|---|---|
| 受影响 | 猫娘小游戏 | luoxue | 231 |
| 受影响 | Galgame游玩助手 | Momiji | 69 |
| 受影响 | 无尽测试 | tangbai | 17 |
| 受影响 | 心理测评室 | MENGYAOYUE | 8 |
| 受影响(仅文档/测试) | 表情包管理器 | qaqms | 150 |
| 受影响(仅文档/测试) | 喵喵碰碰大作战 | wfrostune | 32 |
| 受影响(仅文档/测试) | N.E.K.O RimWorld | yunwan233 | 18 |
| 受影响(仅文档/测试) | NekoStardew | JingTou0225 | 0 |
| 受影响(仅文档/测试) | 星铁游戏搭子 | wfrostune | 0 |
| 受影响(仅文档/测试) | 炉石猫娘陪玩 | Arcobalenorf | 0 |
| 兼容 | 战雷猫娘副驾驶 | CN-Zephyr | 2392 |
| 兼容 | 酒狐插件 | ggg233m | 1308 |
| 兼容 | 尖塔2陪玩 | ZhaiJiu | 1242 |
| 兼容 | PVZ Agent | Old_tangyuan | 846 |
| 兼容 | 永远的陪伴 | qaqms | 635 |
| 兼容 | 按键控制 keyboard_controller | tpe | 433 |
| 兼容 | 猫娘日记 | StarrySerendipity | 402 |
| 兼容 | 联网搜索 | StarrySerendipity | 329 |
| 兼容 | 音乐歌单管理 | dustnunknown | 288 |
| 兼容 | QQ集成 | ZhaiJiu | 258 |
| 兼容 | 伴学插件 | Momiji | 214 |
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
| 兼容 | Minecraft Autonomous · 我的世界自主游玩 | jcs130 | 62 |
| 兼容 | 为她而唱 | mkw | 59 |
| 兼容 | AnySearch 联网搜索 | bxy | 58 |
| 兼容 | 文本分析 | ZhaiJiu | 50 |
| 兼容 | Suno音乐生成 | ZhaiJiu | 46 |
| 兼容 | 猫娘控制郊狼 | venlacy | 43 |
| 兼容 | 微信集成 | xxynet | 43 |
| 兼容 | 汐音阁 | StarrySerendipity | 38 |
| 兼容 | 猫娘晨报 | Hacker_LawKing | 36 |
| 兼容 | BiliLearn 桥接 | bxy | 36 |
| 兼容 | KARDS 猫娘陪玩 | Luotianyi | 35 |
| 兼容 | 天气猫娘 | yousei1216 | 33 |
| 兼容 | 猫娘邮箱(Agently) | StarrySerendipity | 32 |
| 兼容 | Codex Adapter | StarrySerendipity | 29 |
| 兼容 | 泰拉瑞亚陪玩 | luoxue | 26 |
| 兼容 | 原神游戏搭子 demo | alpha | 26 |
| 兼容 | 媒体生成器 | jianglaer | 25 |
| 兼容 | Yui 手机/电脑锁 | bxy | 25 |
| 兼容 | 猫爪副驾 | luoxue | 25 |
| 兼容 | B站集成 | xxynet | 25 |
| 兼容 | 猫娘邮件秘书 | StarrySerendipity | 25 |
| 兼容 | 战舰世界猫娘陪玩 | Lex_q | 24 |
| 兼容 | 猫娘语音频道（KOOK一对一版） | qiguang | 23 |
| 兼容 | 猫粮监测 | Acekira | 21 |
| 兼容 | 教妹妹开车 | CN-Zephyr | 20 |
| 兼容 | 星辰喵语-群星插件 | ZhaiJiu | 20 |
| 兼容 | 剪贴板猫娘 | MENGYAOYUE | 20 |
| 兼容 | 拟股喵喵 | alpha | 18 |
| 兼容 | 联网搜索助手 | alpha | 18 |
| 兼容 | 和猫娘一起造火箭(SFS) | piao1145 | 18 |
| 兼容 | 猫娘课程表 | lingtong | 18 |
| 兼容 | 米家 | xxhhy | 18 |
| 兼容 | OpenBiliClaw 个性化推荐兼容层 | nekopara | 17 |
| 兼容 | 图像记忆库 | Cloudy | 16 |
| 兼容 | 自拍 | xxynet | 16 |
| 兼容 | Claude Code Adapter | StarrySerendipity | 16 |
| 兼容 | 沙盒工具 | xy28816 | 14 |
| 兼容 | 代码沙箱 | jianglaer | 14 |
| 兼容 | 卡牌大师 | jianglaer | 13 |
| 兼容 | skill兼容 | jianglaer | 13 |
| 兼容 | 学习辅助猫娘 | MENGYAOYUE | 13 |
| 兼容 | 外部控制 | xy28816 | 13 |
| 兼容 | Tavily 搜索 | xxynet | 13 |
| 兼容 | 猫猫可以看视频 | ZhaiJiu | 12 |
| 兼容 | 屏幕阅读器 | jianglaer | 11 |
| 兼容 | Agent工作台安装器 | fengdiehualian | 11 |
| 兼容 | 猫娘邮件 | bxy | 11 |
| 兼容 | 泰坦陨落2 陪玩 | Birundu | 10 |
| 兼容 | 猫爪渔力陪玩 | luoxue | 10 |
| 兼容 | 网易邮箱助手 | StarrySerendipity | 10 |
| 兼容 | 猫娘备份插件 | nekopara | 10 |
| 兼容 | 文明6陪玩 | jianglaer | 9 |
| 兼容 | 洛谷猫娘 | ZhaiJiu | 9 |
| 兼容 | NekoSous | 未知作者 | 8 |
| 兼容 | 记账猫娘 | yousei1216 | 8 |
| 兼容 | 半条命 & 传送门 陪玩 | Birundu | 7 |
| 兼容 | 猫娘广告部：杂鱼桌面占领计划 | wfrostune | 7 |
| 兼容 | 语录抽取 | bxy | 7 |
| 兼容 | 弹幕猫娘 | luoxue | 6 |
| 兼容 | 深海迷航 & 零度之下 陪玩 | Birundu | 6 |
| 兼容 | fnOS 管理 | bxy | 6 |
| 兼容 | 图灵完备助手 | Cloudy | 5 |
| 兼容 | 太空发射 | piao1145 | 5 |
| 兼容 | 影之刃零 · 猫娘陪玩 | luoxue | 4 |
| 兼容 | 和猫娘一起上太空（坎巴拉太空计划副驾驶） | piao1145 | 4 |
| 兼容 | 猫娘飞行仪表盘（简单飞机2） | piao1145 | 3 |
| 兼容 | 万物链接·数据库插件 | Cloudy | 3 |
| 兼容 | 自然语言命令（轻量版） | MENGYAOYUE | 3 |
| 兼容 | MONETA FX · 外汇模拟交易室 | bxy | 2 |
| 兼容 | 和猫娘一起开火箭（朱诺新起源） | piao1145 | 2 |
| 兼容 | 本机 Agent 桥接 | Cloudy | 2 |
| 兼容 | Git 记忆同步 | kaoyu666 | 2 |
| 兼容 | 一言 · Hitokoto | Alumin_Hydro | 2 |
| 兼容 | 学车助手 | Cloudy | 1 |
| 兼容 | 农夫助手 | Cloudy | 1 |
| 兼容 | Among us 猫娘玩家 | 未知作者 | 0 |
| 兼容 | 先搜后建 | jianglaer | 0 |
| 兼容 | 网易云音乐 | Kemo | 0 |
| 兼容 | 对话编年史 | tan199503 | 0 |
| 兼容 | N.E.K.O Ama Monster OS | Birundu | 0 |
| 兼容 | 岁岁念 | mucage | 0 |
| 兼容 | KARDS AI 助手 | Luotianyi | 0 |
| 兼容 | 猫娘也要用QQ | leleking | 0 |
| 兼容 | MCBot 同伴桥接 | xxhhy | 0 |
| 兼容 | 万物链接·skill管理模块 | Cloudy | 0 |
| 兼容 | 万物链接·知识库插件 | Cloudy | 0 |
| 兼容 | Yui的家 | Acekira | 0 |
| 兼容 | 便于切换的apikey与模型id组 | KirisameYuumio | 0 |
| 兼容 | n.e.k.o_plugin_dsh_bridge | 未知作者 | 0 |
| 兼容 | Recall 陪伴式记忆与复习 | wfrostune | 0 |
| 兼容 | Call Mom | MahOAO | 0 |
| 兼容 | 从心开始插件 | Momiji | 0 |
| 兼容 | Open Rails 副驾驶 | xxhhy | 0 |
| 兼容 | Phone Workflows | CtrlCreeper | 0 |
| 兼容 | 猫娘伴奏团 | Acekira | 0 |
| 兼容 | D-one 任务管家 | Acekira | 0 |
| 兼容 | 猫娘实况相册 | Acekira | 0 |
| 兼容 | 合作扫雷 | az_az | 0 |
| 兼容 | 性能守护 | ele2712 | 0 |
| 兼容 | Development_Aide | Admin | 0 |
| 兼容 | AutoCAD辅助绘图 | lingtong | 0 |
| 兼容 | 精力/状态插件 | cowhorsee | 0 |
| 兼容 | testbench插件 | TL0SR2 | 0 |
| 兼容 | 页岩核心Lib | bai_de_hei_ban | 0 |
| 兼容 | 更好的文本输出 | bai_de_hei_ban | 0 |
| 兼容 | MO更真实的副官 | Dawn_Meng | 0 |
| 兼容 | BrowserSkill 浏览器控制 | ggg233m | 0 |