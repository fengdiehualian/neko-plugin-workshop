# 市场插件全量目录(46 个真实上架插件的学习卡片)

> 每张卡片 = 一个真实上架插件。**源码全部在本机**,需要看完整实现直接读对应 src 目录(你有全局权限)。
> 用法:拿到需求 → 在下面按功能找同类插件 → 读它的 src 学结构与写法 → 动手。
> 四大支柱能力的统计与惯用法见 `market-patterns.md`。


## 综合(56 个)

### neko_calabiqiu — 卡拉彼丘陪伴

- 作者:CN-Zephyr · 装机:6 · 下载:4 · 点赞:1
- 功能:让 N.E.K.O 陪你一起玩卡拉彼丘。支持语音提醒、随游戏自动启停，也可以随时在插件面板中暂停或恢复陪伴。
- 能力:push_v2, push_message | 装饰器:lifecycle×7, plugin_entry×6, neko_plugin×1
- 对外接口:
  - [entry] assistant_control — 助手进程
  - [entry] set_dry_run — 设置 dry_run
  - [entry] pause — 急停
  - [entry] resume — 恢复
  - [entry] test_say — 演示警报
  - [entry] status — 状态
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_calabiqiu\src`

### development_aide — Development_Aide

- 作者:Admin · 装机:0 · 下载:0 · 点赞:1
- 简介:通过上传skill和读取本地文件形式，为您的开发提供乐趣喵~
- 能力:基础 | 装饰器:plugin_entry×11, lifecycle×3, neko_plugin×1
- 对外接口:
  - [entry] import_skill — 导入 Skill 技能包
  - [entry] scan_skill_dirs — 扫描技能目录
  - [entry] list_project_files — 列出项目文件
  - [entry] read_project_file — 读取项目文件（只读）
  - [entry] code_review — 代码审查
  - [entry] error_fix — 错误定位与修复建议
  - [entry] project_summary — 项目结构摘要
  - [entry] multi_file_summary — 多文件汇总
  - [entry] quick_audit — 一键开发审查
  - [entry] save_settings — 保存设置
- 源码:`<WORKSHOP_DIR>\market-corpus\development_aide\src`

### neko_pawpilot — 猫爪副驾

- 作者:luoxue · 装机:10 · 下载:5 · 点赞:0
- 简介:欧卡2陪玩
- 能力:data_path, http, push_message, push_v2, static_ui, store | 装饰器:plugin_entry×19, lifecycle×3, message×1, neko_plugin×1
- 对外接口:
  - [entry] set_mode — 查看当前人设
  - [entry] set_voice_style — 切换播报口吻
  - [entry] pilot_offer — 猫娘智驾
  - [entry] pilot_accept — 同意猫娘开车
  - [entry] pilot_release — 收回驾驶权
  - [entry] set_dry_run — 切换播报开关
  - [entry] pause — 急停
  - [entry] resume — 恢复
  - [entry] set_frequency — 设置播报频率
  - [entry] set_category — 切换播报类别
  - [entry] test_say — 测试推送链路
  - [entry] install_telemetry — 导入遥测文件
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_pawpilot\src`

### hitokoto — 一言 · Hitokoto

- 作者:Alumin_Hydro · 装机:0 · 下载:0 · 点赞:0
- 简介:Random and daily quotes from Hitokoto, with categories, daily greeting, caching, and a settings panel.
- 能力:push_message, http, push_v2, store, static_ui | 装饰器:plugin_entry×7, llm_tool×3, timer_interval×1, lifecycle×2, message×1, neko_plugin×1
- 对外接口:
  - [entry] random_quote — 随机一言
  - [entry] daily_quote — 今日一言
  - [entry] get_panel_state — 读取一言面板状态
  - [entry] save_settings — 保存一言设置
  - [entry] reset_settings — 恢复一言默认设置
  - [entry] test_api — 测试一言 API
  - [entry] clear_daily_cache — 清除每日一言缓存
  - [timer] hitokoto_user_context_poll
- 源码:`<WORKSHOP_DIR>\market-corpus\hitokoto\src`

### neko_wows — 战舰世界猫娘陪玩

- 作者:Lex_q · 装机:7 · 下载:1 · 点赞:2
- 简介:WoWS companion using telemetry and non-isolated cross-plugin shared-screen frames.
- 能力:push_message, push_v2, http, store, data_path | 装饰器:plugin_entry×25, llm_tool×3, lifecycle×3, message×1, neko_plugin×2
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_wows\src`

### course_schedule — 猫娘课程表

- 作者:lingtong · 装机:15 · 下载:14 · 点赞:3
- 简介:它会记录你的课程表，还会提醒你去上课
- 能力:router, push_message, push_v2 | 装饰器:plugin_entry×30, timer_interval×1, lifecycle×3, neko_plugin×1
- 对外接口:
  - [timer] class_reminder_check
- 源码:`<WORKSHOP_DIR>\market-corpus\course_schedule\src`

### neko_mcp_serve — 外部控制

- 作者:xy28816 · 装机:6 · 下载:3 · 点赞:2
- 简介:External HTTP endpoint to make N.E.K.O. speak
- 能力:push_message, push_v2 | 装饰器:plugin_entry×4, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] get_model_slots — Get Model Slots
  - [entry] set_model_slots — Set Model Slots
  - [entry] restore_model_slots — Restore Model Slots
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_mcp_serve\src`

### forever_companion — 永远的陪伴

- 作者:qaqms · 装机:54 · 下载:158 · 点赞:11
- 简介:基于连续状态模拟的桌宠陪伴核心，负责自主情绪、身体节律、长期记忆与相处可视化，低侵入式提示词。插件只递状态、摆工具，如何回应完全由她自己决定。
- 能力:dynamic_entry, push_message, router, push_v2, store | 装饰器:llm_tool×27, plugin_entry×38, timer_interval×5, lifecycle×3, neko_plugin×1
- 对外接口:
  - [timer] tide_tick
- 源码:`<WORKSHOP_DIR>\market-corpus\forever_companion\src`

### qq_auto_reply — QQ集成

- 作者:ZhaiJiu · 装机:10 · 下载:21 · 点赞:1
- 简介:通过 OneBot v11（正向/反向 WebSocket，兼容 NapCat / LLOneBot / go-cqhttp / Lagrange 等任意 OneBot 后端）或 QQ 官方开放平台接入 QQ 的完整机器人集成插件。
- 能力:push_message, push_v2, data_path, http, static_ui, cache_path, list_actions | 装饰器:plugin_entry×10, lifecycle×4, neko_plugin×2
- 源码:`<WORKSHOP_DIR>\market-corpus\qq_auto_reply\src`

### autocad_assistant_tool — AutoCAD辅助绘图

- 作者:lingtong · 装机:0 · 下载:0 · 点赞:4
- 简介:与猫娘对话就可以完成绘图
- 能力:http, static_ui | 装饰器:plugin_entry×8, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] connect — 连接CAD
  - [entry] disconnect — 断开CAD
  - [entry] get_status — 查询绘图状态
  - [entry] get_capabilities — 查询可用绘图命令
  - [entry] draw — 执行绘图命令
- 源码:`<WORKSHOP_DIR>\market-corpus\autocad_assistant_tool\src`

### luogu — 洛谷猫娘

- 作者:ZhaiJiu · 装机:2 · 下载:5 · 点赞:0
- 简介:成为OIer吧！
- 能力:data_path, http, push_message, store, static_ui, push_v2 | 装饰器:plugin_entry×14, lifecycle×2, neko_plugin×1
- 源码:`<WORKSHOP_DIR>\market-corpus\luogu\src`

### mood_state — 精力/状态插件

- 作者:cowhorsee · 装机:0 · 下载:0 · 点赞:4
- 简介:本插件为猫娘添加了简单的精力和心情状态，通过将不同挡位的提示词注入短期记忆实现。
- 能力:push_message, data_path, push_v2 | 装饰器:plugin_entry×5, llm_tool×2, timer_interval×1, lifecycle×3, neko_plugin×1
- 对外接口:
  - [entry] get_state — 查看心情精力状态
  - [entry] adjust_state — 手动调整心情/精力
  - [entry] set_enabled — 启用/停用状态系统
  - [entry] open_gui — 打开状态窗口
  - [entry] evaluate_now — 立刻重新评估状态
  - [timer] state_drift
- 源码:`<WORKSHOP_DIR>\market-corpus\mood_state\src`

### bilibili_integration — B站集成

- 作者:xxynet · 装机:4 · 下载:6 · 点赞:1
- 简介:B站私信与评论通知自动回复，支持白名单。
- 能力:http, store, static_ui, data_path, list_actions | 装饰器:plugin_entry×13, lifecycle×2, neko_plugin×1
- 源码:`<WORKSHOP_DIR>\market-corpus\bilibili_integration\src`

### testbench — testbench插件

- 作者:TL0SR2 · 装机:0 · 下载:0 · 点赞:1
- 简介:……阿巴喵……
……某种不推荐普通用户使用的半公开内部开发测试框架的插件版本……
……到底是谁天天吵着非得要动内部数据啊……
- 能力:data_path | 装饰器:plugin_entry×4, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] start — Start Testbench
  - [entry] stop — Stop Testbench
  - [entry] open — Open UI
  - [entry] status — Status
- 源码:`<WORKSHOP_DIR>\market-corpus\testbench\src`

### bililearn_bridge — BiliLearn 桥接

- 作者:bxy · 装机:7 · 下载:33 · 点赞:1
- 简介:猫娘的 BiliLearn 桥：拉起独立程序并控制它。
- 能力:基础 | 装饰器:plugin_entry×8, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] start — 启动 BiliLearn
  - [entry] stop — 停止 BiliLearn
  - [entry] restart — 重启 BiliLearn
  - [entry] status — BiliLearn 状态
  - [entry] open_panel — 打开面板
  - [entry] bot_start — 启动机器人
  - [entry] bot_stop — 停止机器人
  - [entry] bot_status — 机器人状态
- 源码:`<WORKSHOP_DIR>\market-corpus\bililearn_bridge\src`

### shale_core_lib — 页岩核心Lib

- 作者:bai_de_hei_ban · 装机:0 · 下载:0 · 点赞:1
- 简介:LLM 输出到前端文本框的中间加工层
- 能力:基础 | 装饰器:-
- 源码:`<WORKSHOP_DIR>\market-corpus\shale_core_lib\src`

### coyote_control — 猫娘控制郊狼

- 作者:venlacy · 装机:8 · 下载:11 · 点赞:2
- 简介:让猫娘自主控制 DG-Lab 郊狼设备
- 能力:http, data_path | 装饰器:plugin_entry×1, llm_tool×3, lifecycle×3, neko_plugin×1
- 源码:`<WORKSHOP_DIR>\market-corpus\coyote_control\src`

### anysearch — AnySearch 联网搜索

- 作者:bxy · 装机:34 · 下载:43 · 点赞:5
- 简介:Plug AnySearch unified web search into N.E.K.O: let your catgirl search the web from chat, with a visual panel.
- 能力:基础 | 装饰器:plugin_entry×2, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] search — AnySearch 联网搜索
  - [entry] anysearch_search — 联网搜索
- 源码:`<WORKSHOP_DIR>\market-corpus\anysearch\src`

### qqbot_bridge — QQ机器人桥接

- 作者:bxy · 装机:13 · 下载:28 · 点赞:1
- 简介:Bridge the official QQ bot into N.E.K.O: receive/send messages, auto-reply via echo or a webhook to your N.E.K.O agent.
- 能力:基础 | 装饰器:plugin_entry×3, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] qq_send — 发送 QQ 消息
  - [entry] qq_reconnect — 重连 QQ 机器人
  - [entry] qq_status — QQ 机器人连接状态
- 源码:`<WORKSHOP_DIR>\market-corpus\qqbot_bridge\src`

### sys_monitor — 电脑监控

- 作者:xy28816 · 装机:70 · 下载:54 · 点赞:4
- 简介:监控 CPU/内存/磁盘/电量，异常时让TA主动提醒你
- 能力:push_message, data_path, push_v2 | 装饰器:plugin_entry×5, timer_interval×1, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] c_status — 查询状态(受控)
  - [entry] a_status — 查询状态(全部)
  - [entry] b_status — 查询状态(静默)
  - [entry] d_settings — 监控设置
  - [entry] e_policy — 推送策略
  - [timer] monitor
- 源码:`<WORKSHOP_DIR>\market-corpus\sys_monitor\src`

### mini_games — 猫娘小游戏屋

- 作者:bxy · 装机:22 · 下载:46 · 点赞:4
- 简介:Catgirl mini-games: Gacha pulls (single/ten-pull/pity/collection), daily fortune, dice roll.
- 能力:基础 | 装饰器:plugin_entry×6, llm_tool×6, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] gacha_pull — 抽卡·单抽
  - [entry] gacha_ten — 抽卡·十连
  - [entry] gacha_status — 抽卡·图鉴
  - [entry] gacha_reset — 抽卡·重置
  - [entry] fortune — 今日运势
  - [entry] dice — 摇骰子
  - [llm_tool] mini_games_gacha_pull — 抽一次卡（Gacha），返回稀有度与抽到的猫娘道具。当用户说「抽卡 / 单抽 / 来一发」时使用。
  - [llm_tool] mini_games_gacha_ten — 十连抽（Gacha ten-pull），保底至少 R，更易出货。当用户说「十连 / 十连抽 / 来个十连」时使用。
  - [llm_tool] mini_games_gacha_status — 查看抽卡图鉴与保底进度：累计抽数、当前保底计数、已收集道具数量与 TOP 收集。
  - [llm_tool] mini_games_gacha_reset — 重置当前玩家的抽卡进度与图鉴（保底计数、累计抽数、收集全部清空）。
  - [llm_tool] mini_games_fortune — 摇一摇今日运势：大吉/中吉/小吉/平/凶，外加幸运数字、幸运色、宜做之事。每天同一人结果稳定。
  - [llm_tool] mini_games_dice — 摇骰子，返回每颗结果与合计。可指定颗数与面数（默认 1 颗 6 面）。当用户说「掷骰子 / 丢个骰子」时使用。
- 源码:`<WORKSHOP_DIR>\market-corpus\mini_games\src`

### wechat_integration — 微信集成

- 作者:xxynet · 装机:0 · 下载:5 · 点赞:1
- 简介:微信个人号扫码登录与集成。
- 能力:http, static_ui, data_path, list_actions | 装饰器:plugin_entry×9, lifecycle×2, neko_plugin×1
- 源码:`<WORKSHOP_DIR>\market-corpus\wechat_integration\src`

### hearthstone_companion — 炉石猫娘陪玩

- 作者:Arcobalenorf · 装机:0 · 下载:0 · 点赞:3
- 简介:读取并理解炉石与酒馆战棋公开局势，以结构化事实和情绪信号驱动当前 N.E.K.O 角色自然陪伴，并支持带来源的酒馆建议。
- 能力:push_message, data_path, cache_path, store, push_v2, report_status | 装饰器:plugin_entry×9, llm_tool×2, lifecycle×3, message×1, neko_plugin×1
- 源码:`<WORKSHOP_DIR>\market-corpus\hearthstone_companion\src`

### proactive_recommender — OpenBiliClaw 个性化推荐兼容层

- 作者:nekopara · 装机:15 · 下载:14 · 点赞:1
- 简介:Privately hands OpenBiliClaw recommendations to the current NEKO character to decide whether to speak.
- 能力:http, push_message, store, push_v2 | 装饰器:plugin_entry×4, timer_interval×1, lifecycle×3, neko_plugin×1
- 对外接口:
  - [timer] recommendation_cycle
- 源码:`<WORKSHOP_DIR>\market-corpus\proactive_recommender\src`

### custom_music_list — 音乐歌单管理

- 作者:dustnunknown · 装机:94 · 下载:152 · 点赞:8
- 简介:网易云/QQ音乐/B站歌单导出与歌曲管理，支持 LLM 点歌轮播
- 能力:http, push_message, store, static_ui, data_path, list_actions | 装饰器:plugin_entry×49, llm_tool×11, lifecycle×1, neko_plugin×1
- 对外接口:
  - [entry] start_browser_login — 浏览器登录（推荐）
  - [entry] login_with_cookie — Cookie 登录
  - [entry] get_login_status — 获取登录状态
  - [entry] logout — 登出
  - [entry] list_playlists — 获取歌单列表
  - [entry] export_playlist — 导出歌单到 txt
  - [entry] list_exported_files — 列出已导出文件
  - [entry] load_song_list — 加载歌曲列表
  - [entry] save_song_list — 保存歌曲列表
  - [entry] upload_song_file — 上传音频文件
  - [entry] play_song — 播放歌曲
  - [entry] playlist_get — 获取播放列表
- 源码:`<WORKSHOP_DIR>\market-corpus\custom_music_list\src`

### better_txt_output — 更好的文本输出

- 作者:bai_de_hei_ban · 装机:0 · 下载:0 · 点赞:5
- 简介:RP 动作/心理/语气标记与显示剥离
- 能力:基础 | 装饰器:-
- 源码:`<WORKSHOP_DIR>\market-corpus\better_txt_output\src`

### neko_tlm — 酒狐插件

- 作者:ggg233m · 装机:73 · 下载:1141 · 点赞:37
- 简介:控制车万女仆：跟随、坐下、切换工作模式、攻击目标、装备物品、执行指令、触发技能
- 能力:push_v2, store, push_message, http, data_path | 装饰器:llm_tool×14, plugin_entry×12, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] switch_maid_work — 切换 Minecraft 女仆工作
  - [entry] mine_ore_autonomously — 让 Minecraft 女仆自动找矿
  - [entry] gather_nearby_blocks — 让 Minecraft 女仆砍树或采集附近资源
  - [entry] switch_maid_follow — 切换 Minecraft 女仆跟随或驻守
  - [entry] move_maid_to_destination — 让 Minecraft 女仆前往常用目的地
  - [entry] navigate_maid_to_coordinates — 让 Minecraft 女仆自主寻路到坐标
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_tlm\src`

### app_launcher — 应用启动器

- 作者:StarrySerendipity · 装机:13 · 下载:56 · 点赞:4
- 简介:管理软件快捷方式和可执行文件路径，让猫娘可以帮主人打开指定软件，支持开机自启功能。内置猫娘专属工具：可直接把文件路径发给猫娘完成设置（名称可商量或由猫娘取名）、按精准名称启动、查询可启动应用列表、语音删除已设置应用，操作后前端界面实时同步。
- 能力:store | 装饰器:plugin_entry×12, llm_tool×10, lifecycle×2, neko_plugin×1
- 源码:`<WORKSHOP_DIR>\market-corpus\app_launcher\src`

### shell_cmd — 猫娘命令操作器

- 作者:bxy · 装机:37 · 下载:49 · 点赞:3
- 简介:Catgirl shell/cmd operator: run Shell/CMD/PowerShell in chat, manage local files, with timeout & dangerous-command guard.
- 能力:基础 | 装饰器:plugin_entry×5, llm_tool×5, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] run — 执行命令
  - [entry] list_dir — 列出目录
  - [entry] read_file — 读取文件
  - [entry] write_file — 写入文件
  - [entry] get_status — 查看状态
  - [llm_tool] shell_cmd_run — 执行一条 Shell / CMD / PowerShell 命令，返回 stdout、stderr 与退出码。
  - [llm_tool] shell_cmd_list_dir — 列出某个目录下的文件与子目录。当用户想看看目录里有什么时使用。
  - [llm_tool] shell_cmd_read_file — 读取一个文本文件的内容。当用户想让猫娘查看某个文件时使用。
  - [llm_tool] shell_cmd_write_file — 把文本内容写入一个文件。可覆盖或追加。当用户想让猫娘创建/修改文件时使用。
  - [llm_tool] shell_cmd_get_status — 查看命令操作器的当前配置状态(超时、工作目录、危险命令放行等)。
- 源码:`<WORKSHOP_DIR>\market-corpus\shell_cmd\src`

### mail_all — 猫娘邮件

- 作者:bxy · 装机:12 · 下载:11 · 点赞:1
- 简介:Catgirl mail plugin: send/receive email via any SMTP/IMAP/POP3 account. Multi-account, auto-check, meow notifications.
- 能力:push_message, store, push_v2 | 装饰器:plugin_entry×8, timer_interval×2, lifecycle×3, neko_plugin×2
- 源码:`<WORKSHOP_DIR>\market-corpus\mail_all\src`

### neko_mail — 猫娘邮件秘书

- 作者:StarrySerendipity · 装机:17 · 下载:23 · 点赞:1
- 简介:让猫娘帮你管理QQ邮箱，生成邮件摘要、判断优先级、标记已读、发送邮件（支持附件）。支持新邮件轮询推送、智能分类、去重校验。清新诗意的前端界面，支持i18n多语言。配置详见 README。
- 能力:push_message, push_v2 | 装饰器:plugin_entry×24, llm_tool×24, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] get_summary — 获取今日邮件摘要
  - [entry] get_unread — 获取未读邮件
  - [entry] get_all_emails — 获取所有邮件
  - [entry] get_email_detail — 获取邮件详情
  - [entry] search — 搜索邮件
  - [entry] mark_read — 标记邮件已读
  - [entry] batch_mark_read — 批量标记邮件已读
  - [entry] mark_all_read — 标记所有邮件已读
  - [entry] send — 发送邮件
  - [entry] list_folders — 列出文件夹
  - [entry] check_new_emails — 检查新邮件
  - [entry] batch_delete — 批量删除邮件
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_mail\src`

### neko_mail_agently — 猫娘邮箱(Agently)

- 作者:StarrySerendipity · 装机:18 · 下载:24 · 点赞:2
- 简介:给猫娘注册并管理独属于她自己的 Agently 邮箱（@agent.qq.com）。猫娘可引导主人安装 Agently CLI、打开网页扫码登录、创建专属邮箱；支持收发邮件、邮件摘要、新邮件自动提醒、两阶段确认防误操作。完整使用教程见插件描述与 README。
- 能力:push_message, push_v2 | 装饰器:plugin_entry×19, llm_tool×19, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] auth_status — 检查授权状态
  - [entry] get_user_info — 获取用户信息
  - [entry] list_emails — 列出邮件
  - [entry] get_email — 获取邮件详情
  - [entry] search_emails — 搜索邮件
  - [entry] send_email — 发送邮件
  - [entry] reply_email — 回复邮件
  - [entry] forward_email — 转发邮件
  - [entry] trash_email — 删除邮件
  - [entry] upload_attachment — 上传附件
  - [entry] download_attachment — 下载附件
  - [entry] refresh_auth — 刷新授权
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_mail_agently\src`

### store_search — 插件商店搜索

- 作者:xxynet · 装机:29 · 下载:42 · 点赞:3
- 简介:Search and discover plugins from the N.E.K.O plugin store.
- 能力:http | 装饰器:plugin_entry×2, llm_tool×2, lifecycle×3, neko_plugin×1
- 对外接口:
  - [entry] search_plugins — 搜索插件
  - [entry] plugin_detail — 插件详情
- 源码:`<WORKSHOP_DIR>\market-corpus\store_search\src`

### tavily_search — Tavily 搜索

- 作者:xxynet · 装机:10 · 下载:10 · 点赞:2
- 简介:Search the web and extract pages through Tavily.
- 能力:http | 装饰器:plugin_entry×2, lifecycle×3, neko_plugin×1
- 对外接口:
  - [entry] search — Tavily 搜索
  - [entry] extract_webpage — 提取网页内容
- 源码:`<WORKSHOP_DIR>\market-corpus\tavily_search\src`

### neko_163mail — 网易邮箱助手

- 作者:StarrySerendipity · 装机:6 · 下载:10 · 点赞:1
- 简介:让猫娘帮你管理网易163邮箱,生成邮件摘要、判断优先级、标记已读、发送邮件。清新诗意的前端界面,支持附件发送。
- 能力:push_message, push_v2 | 装饰器:llm_tool×28, plugin_entry×26, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] get_summary — 获取今日邮件摘要
  - [entry] get_unread — 获取未读邮件
  - [entry] get_all_emails — 获取所有邮件
  - [entry] get_email_detail — 获取邮件详情
  - [entry] search — 搜索邮件
  - [entry] mark_read — 标记邮件已读
  - [entry] batch_mark_read — 批量标记邮件已读
  - [entry] mark_all_read — 标记所有邮件已读
  - [entry] send — 发送邮件
  - [entry] reply — 回复邮件
  - [entry] forward — 转发邮件
  - [entry] download_attachment — 下载附件
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_163mail\src`

### neko_diary — 猫娘日记

- 作者:StarrySerendipity · 装机:60 · 下载:224 · 点赞:6
- 简介:Catgirl diary — record daily moments, mark moods, cherish every memory.
- 能力:基础 | 装饰器:plugin_entry×9, llm_tool×9, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] write — 写日记
  - [entry] browse — 浏览日记
  - [entry] search — 搜索日记
  - [entry] get_today — 获取今日日记
  - [entry] get_mood_stats — 获取心情统计
  - [entry] throwback — 历史上的今天
  - [entry] delete — 删除日记
  - [entry] get_names — 获取称呼配置
  - [entry] set_names — 设置称呼配置
  - [llm_tool] neko_diary_write — 写一篇日记。记录今天发生的小事，可以标记心情、添加标签、附带图片。支持设置可见性（公开/仅自己可见）。
  - [llm_tool] neko_diary_browse — 浏览日记时间线。可以按日期范围查看，支持分页，支持按可见性过滤（public/private/全部）。
  - [llm_tool] neko_diary_search — 搜索日记。可以按关键词搜索内容、标题、标签。
- 源码:`<WORKSHOP_DIR>\market-corpus\neko_diary\src`

### claude_code_adapter — Claude Code Adapter

- 作者:StarrySerendipity · 装机:3 · 下载:15 · 点赞:1
- 简介:Expose Claude Code CLI as catgirl-callable LLM tools. Run Claude Code tasks from the main project.
- 能力:store, push_message, push_v2 | 装饰器:plugin_entry×5, llm_tool×17, lifecycle×2, neko_plugin×1
- 源码:`<WORKSHOP_DIR>\market-corpus\claude_code_adapter\src`

### web_searching — 联网搜索

- 作者:StarrySerendipity · 装机:78 · 下载:213 · 点赞:3
- 简介:Multi-engine web search (Zhihu/Baidu/Bing) + smart dedup + snippet fallback + page content extraction.
- 能力:static_ui, data_path, http, list_actions | 装饰器:plugin_entry×5, llm_tool×3, lifecycle×3, neko_plugin×1
- 对外接口:
  - [entry] search — 网络搜索
  - [entry] get_history — 获取搜索记录
  - [entry] clear_history — 清空搜索记录
  - [entry] get_stats — 搜索统计
  - [entry] get_status — 插件状态
- 源码:`<WORKSHOP_DIR>\market-corpus\web_searching\src`

### keyboard_controller — 按键控制 keyboard_controller

- 作者:tpe · 装机:1 · 下载:386 · 点赞:10
- 简介:Control a game/software window with keyboard & mouse input, screenshot + OCR read-screen & text location, shell command execution (with confirmation), workspace file read/write for vibe-coding, and host audio analysis.
- 能力:push_message, store, data_path, router, push_v2 | 装饰器:llm_tool×34, plugin_entry×62, timer_interval×4, lifecycle×2, neko_plugin×1
- 对外接口:
  - [timer] diary_auto_flush
  - [timer] reactive_window_watch
  - [timer] trigger_engine_tick
  - [timer] gif_capture_tick
- 源码:`<WORKSHOP_DIR>\market-corpus\keyboard_controller\src`

### data_backup — 猫娘备份插件

- 作者:nekopara · 装机:0 · 下载:0 · 点赞:2
- 简介:创建、恢复和管理本地数据快照。
- 能力:static_ui, data_path, list_actions | 装饰器:plugin_entry×6, timer_interval×1, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] backup_status — 查看备份状态
  - [entry] backup_set_directory — 更改备份目录
  - [entry] backup_create — 创建数据快照
  - [entry] backup_set_schedule — 设置定时快照
  - [entry] backup_restore — 恢复数据快照
  - [entry] backup_delete — 删除数据快照
  - [timer] scheduled_backup
- 源码:`<WORKSHOP_DIR>\market-corpus\data_backup\src`

### codex_adapter — Codex Adapter

- 作者:StarrySerendipity · 装机:8 · 下载:20 · 点赞:1
- 简介:通过 @llm_tool 将 OpenAI Codex CLI 注册为猫娘可调用的工具集。猫娘可以直接调用 Codex 执行项目开发任务：写代码、改 bug、跑测试、查文档。支持会话恢复（thread_id）、Web 搜索、快速模式、瞬态错误自动重试和 CODEX_HOME 管理。
- 能力:store | 装饰器:plugin_entry×1, llm_tool×9, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] execute — 执行 Codex 任务
- 源码:`<WORKSHOP_DIR>\market-corpus\codex_adapter\src`

### catgirl_daily_planner — 猫娘每日计划

- 作者:StarrySerendipity · 装机:35 · 下载:137 · 点赞:11
- 简介:猫娘每日为主人定制专属计划,并在指定时间提醒主人该做什么事。支持早安播报、任务清单、复盘收尾。
- 能力:push_message, store, push_v2 | 装饰器:plugin_entry×24, llm_tool×24, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] create_plan — 创建今日计划
  - [entry] add_task — 添加任务
  - [entry] set_task_meta — 更新任务属性
  - [entry] get_current_time — 获取当前时间
  - [entry] mark_done — 标记任务完成
  - [entry] acknowledge_task — 确认任务提醒
  - [entry] delay_task_reminder — 延迟任务提醒
  - [entry] list_today — 查看今日计划
  - [entry] delete_task — 删除任务
  - [entry] clear_plan — 清空计划
  - [entry] set_schedule — 修改早晚播报时间
  - [entry] get_schedule — 查看早晚播报时间
- 源码:`<WORKSHOP_DIR>\market-corpus\catgirl_daily_planner\src`

### xiyin_pavilion — 汐音阁

- 作者:StarrySerendipity · 装机:8 · 下载:31 · 点赞:2
- 简介:汐音阁 · XiYin Pavilion —— 本地音乐推送插件。用户在 Web UI 上传音频文件后，猫娘可获取已上传歌曲列表，按歌名/歌手精准选歌推送到主对话播放，支持定时队列与歌词绑定。✨ 小巧思：上传歌曲时可选绑定歌词，之后每次猫娘推送歌曲时，会随机提取歌词片段内容与用户雅俗共赏。
- 能力:push_message, static_ui, data_path | 装饰器:plugin_entry×16, llm_tool×2, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] upload_music_file — 上传音乐并自动推送
  - [entry] push_music_url — 推送音乐链接
  - [entry] run — 通用推歌入口
  - [entry] get_push_settings — 获取推送设置
  - [entry] set_push_settings — 设置推送开关
  - [entry] list_music_items — 列出音乐素材
  - [entry] delete_music_item — 删除音乐素材
  - [entry] create_schedule_task — 创建定时任务
  - [entry] update_schedule_task — 更新定时任务
  - [entry] delete_schedule_task — 删除定时任务
  - [entry] run_schedule_task_now — 立即执行任务
  - [entry] stop_schedule_task — 停止执行任务
- 源码:`<WORKSHOP_DIR>\market-corpus\xiyin_pavilion\src`

### suno_cn_music — Suno音乐生成

- 作者:ZhaiJiu · 装机:12 · 下载:34 · 点赞:1
- 简介:N.E.K.O 的 Suno.cn AI 音乐生成插件，通过对话即可生成原创音乐。
- 能力:push_message, store, http, push_v2 | 装饰器:plugin_entry×10, lifecycle×2, neko_plugin×1
- 对外接口:
  - [entry] get_user_info — 查询账户信息
  - [entry] query_task — 查询任务状态
  - [entry] generate_music — 生成音乐
  - [entry] list_music — 查询音乐列表
  - [entry] get_lyrics — 获取歌词
  - [entry] extend_music — 续写音乐
  - [entry] gen_lyrics — AI 生成歌词
  - [entry] play_audio — 播放音频
  - [entry] open_url — 打开链接
  - [entry] set_api_key — 设置 API Key
- 源码:`<WORKSHOP_DIR>\market-corpus\suno_cn_music\src`

### writer_power_analysis — 文本分析

- 作者:ZhaiJiu · 装机:27 · 下载:42 · 点赞:1
- 简介:基于 AI 技术的专业文本分析平台，为创作者提供多维度写作评估与深度洞察。系统通过多种 AI 模型与可切换的评分视角，从写作风格、内容质量、语言表达、叙事结构等维度进行全面分析，输出量化评分、雷达图可视化报告和可操作的改进建议。
- 能力:http, settings, push_message, static_ui, data_path, push_v2 | 装饰器:plugin_entry×9, lifecycle×3, neko_plugin×1
- 对外接口:
  - [entry] status — 文本分析状态
  - [entry] list_models — 获取作家分析模型列表
  - [entry] get_neko_model — 获取 Neko 当前模型
  - [entry] load_platform_presets — 加载平台预设
  - [entry] save_platform_preset — 保存平台预设
  - [entry] delete_platform_preset — 删除平台预设
  - [entry] analyze_text — 文本分析
  - [entry] get_analysis_status — 查询分析状态
  - [entry] synthesize_author_style_profile — 统合作者文风画像
- 源码:`<WORKSHOP_DIR>\market-corpus\writer_power_analysis\src`

### browser_skill — BrowserSkill 浏览器控制

- 作者:ggg233m · 装机:0 · 下载:0 · 点赞:5
- 简介:Primary browser automation plugin for all web tasks, using the user's logged-in Chrome or Edge through BrowserSkill.
- 能力:http, push_message, push_v2, report_status, dynamic_entry, static_ui, data_path | 装饰器:plugin_entry×6, llm_tool×1, lifecycle×3, neko_plugin×1
- 对外接口:
  - [entry] run_browser_task — 运行浏览器任务
  - [entry] get_browser_skill_dashboard — 读取 BrowserSkill 控制台
  - [entry] save_browser_skill_settings — 保存 BrowserSkill 设置
  - [entry] browser_skill_control — BrowserSkill 连接控制
  - [entry] get_browser_task_status — 查询浏览器任务实时状态
  - [entry] steer_browser_task — 引导正在执行的浏览器任务
- 源码:`<WORKSHOP_DIR>\market-corpus\browser_skill\src`


## 建议学习路径

- 第一次做「对话触发」功能 → 读 shell_cmd / mini_games / neko_diary(llm_tool 重度用户)
- 做「联网查询/聚合」→ anysearch / tavily_search / store_search
- 做「定时提醒/监控」→ sys_monitor / mail_all / catgirl_daily_planner
- 做「聊天记录/日记/存储」→ neko_diary / data_backup
- 做「大型多功能」→ neko_arcade / galgame_plugin(多入口组织、router 拆分)
