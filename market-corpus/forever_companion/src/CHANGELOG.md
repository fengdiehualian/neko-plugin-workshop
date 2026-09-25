# CHANGELOG — 永远的陪伴（forever_companion）

各版本的过程性变更记录（含条目内的审查结论与踩坑记录），自 DESIGN.md 拆分归档；
设计契约看 DESIGN.md，用户视角的功能说明看 README.md。历史条目只追加不改写。
0.7.0 及更早的变更未单独成条（机制本身写在 DESIGN 正文）。

---

### 0.7.1：面板重构 + 写作辅助

- **面板**：日记页改为页内 Tabs（时光日记/个人日记）；时光日记按日期分组
  （组头吸顶）+ 碎片原话语气泡样式 + "加载更多"分页（`get_diary` 加 offset，
  dashboard 仍只发最近 12 条）；个人日记改"目录 + 单页纸质阅读"双视图
  （目录行带页码圆徽/日期区间/心情彩色圆点——复用状态栏 `moodDotColor`；
  阅读页衬线字体、大行距）
- **结构化写作**：`mood_journal_write` 拆成引导字段
  events/thoughts/feelings/extra（至少填一），`assemble_journal_entry` 拼装成
  带引导小标题的成段日记（只填 extra 不加标题，尊重自由发挥）；单条上限放宽 900；
  续写衔接行带落笔日期前缀（"（2026-08-22 写的）…"）
- **邀请素材**：邀请文案带自上次落笔以来的心情词频 top3 + 新碎片数；新增
  `invite_journal` 入口（面板「请她写一篇」按钮，force 跳过节奏与 24h 节流，
  仍尊重 [journal].enabled）


### 0.9.0：陪伴型信息架构重构（总览仪表盘 + 精简状态条 + 模型通道）

定位升级：不做单纯的周期模拟，做"赋予 AI 更全面陪伴感"的插件。信息架构从
"版本地层堆砌"改为按用户心智组织：

- **顶部状态条**：瘦身为"她是谁 · 一句话状态 · 心情胶囊 · 总开关"，
  点击跳总览；月相环/天数/情绪详情全部下放总览页
- **总览页（overview.tsx）**：她的现在（月相环 C 位 + 阶段 + 当前心情卡 +
  情绪徽标）+ 近况与相处（三本日记速览 tile + **相处信号卡**）。
  相处信号卡是陪伴型可视化的标准化阵地：首两个信号 = 近 7 天相处活跃度
  （时光日记近 7 天条目数，纯统计）+ 我的日记素材进度；未来新信号往这里长
- **页签重划**：总览 / 日历 / 周期（她周期+状态注入合并）/ 情绪（情绪系统+
  语气感知+模型通道）/ 日记（三本浏览+日记功能设置同页）/ 设置（时区/调试/
  外观/危险区/角色名单）
- **模型通道卡（settings_tone.tsx 重写）**：三个小模型槽位（语气/碎片/成文）
  集中一处，各带状态灯（dashboard 下发 channel_status，复用
  diagnose_slot_dormancy）：ok / free_route（宿主免费端点服务端校验拒第三方
  直连，面板直说原因与解法）/ no_model / disabled；语气行为设置拆到
  settings_emotion.tsx，槽位只在通道卡
- dashboard 新增轻量字段：channel_status（三通道灯）+ week_activity
  （近 7 天时光日记条目数）；moodgauge.tsx 从情绪页移除（总览的
  MoodSummaryCard 替代），settings 里 diary 卡只留行为开关（槽位归通道卡）

zh-CN / en i18n

### 0.9.1：体验修正（随机锚点 + 总览愉悦度条 + 上传中文化）

- **随机化默认锚点**（cycle.randomized_default_anchor）：锚点缺失时不再默认
  "今天"（旧默认 = 安装当天必是潮汐日第一天，且所有用户都一样）；反推一个
  过去日期使今天落在本轮平稳期的随机位置，预留 3 天尾量（装完前几天不会
  立刻进潮汐期）。锚点经 _refresh_config 写入 shard 即固化，重启不重随；
  极端周期参数（平稳期容不下尾量）有兜底分支。日历据此反推出"过去"的
  阶段--她早就有自己的节律，只是今天才开始被观测
- **总览愉悦度条改为与 0~100 分制同向的左起填充**：旧双向条（中心为零点、
  从中心向两侧延伸）在静息态 50 分时只剩中点一小段颜色、左半全灰，观感
  像"异常"；现改为从左端填到当前分值（静息态=半条），中心刻度保留标出
  "中性 50 分"位置，填充色仍按正负取暖琥珀/灰蓝
- **面板外观上传中文化**：ImageUpload 显式传 placeholder（宿主 runtime 默认
  英文 "Upload image"），文件过大错误消息本地化（8 语言 i18n 新增
  uploadPlaceholder / errorTooLarge / errorTooLargeShort 三键）
- **月相环穿模修复**：扫掠暗盘（.tm-moon-shadow）原为半透明渐变 + backdrop-filter
  压暗下方亮盘，但面板运行环境该滤镜不生效--暗盘只剩自身 10~15% 透明渐变，
  该遮住的区域整个透出亮盘与陨石坑纹理（即用户看到的"穿模"）。改为实体不透明
  暗玻璃（亮色 #ccd5e2/#b3bfd2、暗色 #2a3548/#1e293b 双主题色），无头浏览器
  渲染 21 个月相 × 2 主题逐相验证：暗区零亮斑、零纹理透出、明暗分界干净
- **我的日记提示文案**：panel.review.hint 由"关于这段时间你怎么待她的客观
  评价…"换为"对我的记录…"（8 语言同步；语义更贴"这是关于我的记录"的
  用户视角）

### 1.1.0：相处统计（时光页 = 里程碑 + 热力图 + 月报）

第四块陪伴能力：把"看得见的相处"从 7 天信号升级为长期统计。
纯逻辑在 `stats.py`（按天聚合/徽章/连续天数/热力图窗口/月报封卷/纪念日判定，
与 review.py 同款"数据进数据出"纪律），落盘与埋点由主类完成：

- **数据安全（更新不丢）**：宿主 PluginStore 落在独立数据目录
  （`resolve_plugin_data_dir` → 数据根/plugins/forever_companion/data/store.db，
  与插件源码目录物理分离）——三条更新路径（Market 更新 / 导入 .neko-plugin
  覆盖 / 删除插件）都只动源码目录，`replace_plugin` 原子事务失败自动回滚，
  stats@<角色> 与全部陪伴记录在升级中原样存活。旧用户升级无 stats key 时
  `_ensure_shard` 走回填分支（三本日记时间戳点亮历史天）；卸载后重装数据
  同样留存——用户彻底清除走插件清零入口（README「平台机制」节已说明）
- **存储**：`stats@<角色>`（Store，与我的日记素材同款 per-lanlan 分片），
  `{first_seen, days: {YYYY-MM-DD: {turns, v_sum/v_n, tone, cold, made_up, warm}},
  milestones: {first_diary/first_journal/first_review}, anniversary: {last_pushed},
  months: {YYYY-MM: 封卷月报}}`；days 保留 730 天、月报保留 24 个月，
  首载时从三本日记时间戳回填活跃天（`backfilled` 标记幂等）
- **埋点**（与我的日记共用驱动点但口径独立——不受 [review].enabled 闸、
  成文永不清零）：`_handle_new_user_message`（每轮 + valence 采样）、
  `_feed_tone_affect` weight=1.0 主路径（当日语气分布）、
  `_apply_mood_action`（情绪事件，origin=user 不计）、
  `tool_feeling_better`（previous∈冷战类 → made_up 一次）、
  三本日记的写入点（第一篇里程碑）
- **纪念日**：满 30/60/…/365/… 天当天 `_maybe_anniversary_push` 递一条
  read 轻语（与阶段开场白同构，`[stats].anniversary_inject=false` 可关，
  当天去重水位）；她知不知道由她决定说不说
- **月报封卷**：tick 里 `seal_due_months` 跨月时快照上个月进 months
  （空月不封卷、幂等、超限淘汰最旧）；当月即时聚合实时变
- **口径**（README 有用户版说明）：相伴天数从首条互动起算；冷战只数
  origin=self 的冷战类动作；和好 = 她在负面情绪中主动调转晴（到期自然
  消散不算）；连续天数容忍"今天还没聊"从昨天延续
- **UI**：新「时光」页签 `moment.tsx`（徽章墙 + 数字摘要 + 热力图 div 格 +
  月报翻月）；dashboard 5s 轮询带 `stats_summary`（摘要+徽章，即时计算），
  热力图/月报走 `get_stats` 入口按需拉取；`clear_stats` 危险区入口；
  纪念日开关在日记页设置卡。i18n 8 语言 66 键
- 不注入她的上下文（纪念日轻语是唯一例外，可关）、不注册任何 LLM 工具、
  零模型调用（月报是模板组装不是成文）

### 1.1.6：热力图改日历年视图（起点截断 + 年份选择）

- 旧行为 = 固定滚动近 12 个月窗口，无论有没有数据都铺满网格——
  萌新装完第一眼是一整屏灰空格，与"记录相处的日子"的直觉相悖
- 新行为 = GitHub 式自然年视图：`heatmap_payload(stats, today, year=None)`
  下发 `start/end/years/year`；start = max(first_seen, 视图年 1 月 1 日)，
  end = min(视图年 12 月 31 日, 昨天)；years 从当年降序回退到最早明细年
  （受 730 天保留窗约束，通常至多跨 3 个自然年）
- 前端：铺格窗口改由后端 start/end 决定（`buildCalendarDays` 删除）；
  年份导航复用月报 `tm-month-nav` 样式（‹ ›，仅一年时隐藏，当年显示"今年"）；
  无可展示日子（全新安装 / 首互动就是今天）→ EmptyState，不再铺默认网格
- 窄网格弹性公式换档（`tm-gh-w12/w26`）：列数 ≤12/≤26 时换分母，
  格子不再被 /49 公式压到 7px 下限、缩成左上窄条
- `get_stats` 入口新增 `year` 参数（YYYY 字符串，非法/越界回落当年）；
  翻年份时面板保住当前正在看的月份，月报不跟着跳回当月

### 1.1.7：关闭状态提示并入状态条卡内

- 旧行为：模拟关闭/情绪关闭各渲染一条页级独立 Alert 条（tm-warnstrip），
  带边框底色与外边距，夹在状态条与内容区之间把页面拦腰切割，且与
  状态条"已关闭"徽标、右上角开关按钮信息四重重复
- 新行为：提示收进状态条卡片内部作第二行（.tm-statusbar 本就 flex-wrap，
  整宽子元素自动换行）：「模拟已关闭 · 点击开启」（行内链直接触发总开关，
  走原确认弹窗）、「情绪系统已关闭 · 去「情绪」页开启」（跳转情绪页签）；
  两条同现时并排自动换行；无提示时状态条与旧版完全一致
- 报错 danger Alert 保留原样（真错误需要醒目）；i18n 删
  panel.mood.disabledWarn、增 4 键（panel.offHintState/Action、
  panel.moodOffHintState/Action，8 语言）；旧 panel.disabledHint 本就
  只存在于代码 defaultValue，无 i18n 键残留

### 1.2.0：面板外观升级——图片图库 + 可调背景

旧"单图上传 + 遮罩滑杆"升维为两件事：**图库**（图片留存、自选壁纸、逐张增删）
与**背景调节**（十项参数，实时预览 + 卡底「保存外观」钮整包生效，另有「还原」）。

- **存储**（全部全局一份，与角色无关；均走 Store，不进 plugin.toml——图片
  data URL 是大 payload，`update_own_config` 有 4.5s 硬超时且面板无 config 写通道）：
  `panel_appearance`（归一后的参数 dict）、`gallery_index`（`{items:[{id,name,mime,
  size,added_at,thumb}], next}`，条目不含原图）、`gallery_img/<id>`（每张图一条
  `{data_url,mime,size,added_at}`，本体绝不进 5s 轮询 context）。
  纯逻辑（data URL 校验/参数 clamp/索引操作/旧图迁移）在 `core/appearance.py`，
  顺带消灭了 0.7.2 时代 panel.py 与 `__init__.py` 各写一份校验的"同款复刻"漂移隐患。
- **旧数据迁移**：旧 `panel_bg` 单图记录在首次 `get_panel_gallery` 时以后端
  （无图像库，生成不了缩略图）一次性迁成图库条目 `legacy`（dim 一并带走），
  原 key 保留作回滚备份；缩略图由面板拿到本体后用 canvas 画一张经
  `gallery_set_thumb` 回填（SVG 会污染 canvas，缩略图尽力而为，失败走棋盘占位样式）。
- **上传压缩管线（前端 canvas）**：导入方式二选一（默认自动压缩）——
  auto 缩到长边 2560 转 WebP q0.82（失败降 JPEG；仍不划算则回退原图），
  raw 原样入册（字节闸 4.4MB≈base64 5.9M 字符，卡在后端 6M 字符上限内）；
  GIF/SVG 不转码（动图/矢量语义）等同 raw。256px 缩略图两档都顺手生成。
  图库上限 24 张（4.5MB×24 是 store.db 体积与实用性的折中）。
- **入口**（六个，全 `@ui.action`+`@plugin_entry` 双装饰）：`get_panel_gallery`
  （索引+参数+迁移，一个请求开面板）、`gallery_add`、`gallery_remove`
  （删在用图时后端顺带清 `bg_id` 并回新 appearance）、`gallery_set_thumb`、
  `get_gallery_image`（按需拉本体）、`set_panel_appearance`（**整包替换**语义：
  未传字段回默认；悬空 bg_id 静默解除；参数宽容 clamp 不报错）。
- **渲染参数化**：styles.ts 原本无 CSS 变量，新增经 `tm-appearance-root` 包装层
  （div `display:contents`，不改布局也不改变 .tm-bg 的包含块）落成
  `--tm-glass`（毛玻璃半径）/ `--tm-card-k`（卡片底色不透明度系数，
  `rgba(var(--tm-card-rgb), calc(.72*k))`，主题 rgb 由暗色 media 切换）/
  `--tm-text-color`+`--tm-text-shadow`（文字浓度：`color-mix(in srgb, var(--text)
  N%, transparent)` + 随浓度自动加深的投影；老内核双声明回退原色）。
  背景本体（填充/位置/滤镜链/遮罩 opacity/模糊外扩 bleed）全走 .tm-bg 内联样式，
  blur>0 时 `inset` 负扩防滤镜边缘露底。所有默认值精确复刻 1.1.x 观感。
- **语义分工**：图库是资产库（增删即时持久）；"用哪张+怎么调"是设置
  （draft 实时预览、saved 才生效，切页签不丢 draft，关面板未保存即回退）。
- i18n 净增 63 键 ×8 语言（旧单图链路的 entries/fields/panel.appearance 死键清理）。

### 1.2.1：修复"重启后模拟开关复位"（Store 通电时序）

- **根因不在插件读写链**：toggle 落 `cycle@<角色>.enabled` 正确、`_enabled()` 的
  "分片 > [tide].enabled" 优先级也正确（用生产等价配置做全链路模拟时 enabled 能保住）。
  真因是**宿主启动时序**：SDK 在构造插件实例时 `ctx._effective_config` 还是 None，
  按 `resolve_store_enabled({})` 把 `PluginStore` 建成 `enabled=False`；disabled 态下
  `get` 静默返回 default、`set` 静默丢弃、都不报错。而 `startup()` 第一行就是
  `_load_state()`（全部持久状态在此读），通电只发生在其后的 `_refresh_config()` 里
  第一次 `config.dump()` 回灌时——于是每次重启都"读到空"，开关退回 fail-closed 的
  false、锚点重新随机，且 `shutdown()` 会把这份幻影整体回写，把上次真实保存的
  开关/锚点/快进/三本日记覆写掉（用户机 store.db 里 `enabled:False` +
  `lanlan_index`/`stats@` 两键从未出现，即此路径的痕迹）。
- **修法（只改插件，不动宿主）**：`mixins/shards.py` 新增 `_ensure_store_ready()`，在
  `_load_state()` 读任何东西之前探测 `store.enabled`，未通电则读一次 effective config
  触发宿主 `refresh_runtime_config` 翻转开关，最多 3 次、间隔 0.2s、单次读超时 2s；
  **读配置本身抛错则立即放弃**（宿主不可达时重试唤不醒，硬等会顶到
  `[plugin_runtime].timeout` 拉起超时）。探测只依赖公开属性 `store.enabled`，
  用 `getattr(..., True)` 兜底：FakeStore 等无该属性的实现按可用处理，未来宿主去掉
  门控也自动退化。
- **防覆写降级**：`_load_state()` 首行置 `self._state_trusted`；False 期间
  `shutdown()` 跳过全部分片整体回写并回 `saved:False`，`_ensure_shard` 的
  `lanlan_index`/`stats` 一次性落盘与 0.4.0 legacy 迁移一并推迟（此刻写下去的都是
  从空 store 推出的幻影）。宁可本次不落盘，也不毁历史；会话内单点写入仍各自落。
  `_save_shard_cycle` 在 store 未通电时补一条 warning，让"当场生效、重启即丢"可诊断。
- **默认 `_state_trusted=False`**：startup 之前（含 startup 失败）的任何 shutdown
  都不该覆写。
- **测试**：`tests/test_store_readiness.py` 用 `_GatedStore` 复刻宿主 enabled 门控
  （`get/set` 在未通电时静默空转），5 条覆盖迟通电必须读回 / 永不通电不得覆写 /
  正常路径照常落盘 / 无 enabled 属性不误伤 / 宿主不可达快速降级。已反向验证：
  把源码回退到 1.2.0 后这些测试以正确理由红（`_enabled()` 出 False、盘上
  `enabled`/`advance_days`/`params` 被抹、锚点 2026-08-01 漂到 2026-08-17）。
  `tests/conftest.py` 加 `boot_factory`（不预跑 `_ready`、可注入自定义 store/config）。
- **勘误（1.1.0「数据安全（更新不丢）」那条不成立）**：该节按"数据根与源码目录物理
  分离"断言三条更新路径都不碰数据。实测在**安装版**里
  `resolve_plugin_storage_dir` 与插件安装目录同一个根（
  `%LOCALAPPDATA%/N.E.K.O/plugins/<id>/` 既放源码也放 `data/store.db`），数据目录
  嵌套在插件目录内部，导入覆盖包会连同它一起重建（store.db 的 ctime 与全部行的
  created_at 都等于导入后首次启动时刻，且当天无任何 reset/clear 触发）——记录实际归零。
  开发源码树布局下确实分离，所以这个差异只在安装版显现。已把 README 相应三条改为
  如实描述（升级前不得假定记录保留；Market 路径待验证）。1.2.1 只修"读不回"，
  不修"被清掉"——后者属宿主侧安装布局，不在插件工作区内改。
  **（2026-09-07 再确认：本条的宿主布局前提已被推翻——#2943 起代码/状态已物理
  分离，升级/卸载不再清数据；见文末「2026-09-07 复核」条目。）**
- **残留风险**：若宿主是靠 startup 返回后才处理的 `CONFIG_UPDATE` 推送通电（startup
  期间下行消息被缓冲），则本次读配置唤不醒 store，走降级分支——症状当次仍在但数据
  不再受损，日志会明确留痕；根治需宿主在绑定 `ctx._instance` 后即刷新 runtime config，
  属平台侧时序，不在插件工作区内改。

### 1.2.2：自动保存链路加固（保存失败传播 + 中途通电门控 + 增量即时落盘）

针对"交互完重启软件丢数据"的全链路审计后分两批修复。审计结论：交互→落盘主干
本身健康（写入口都即时 `await store.set`，宿主 SQLite 逐写 commit，对强杀免疫），
漏洞集中在三处——shutdown 回写覆盖不全、宿主未通电窗口的假成功、不可信载入后的
幻影覆写残余路径。

**批次1（防线）**：
- shutdown 整体回写补 `stats@`/`review@`（此前只回写 cycle/mood/diary/journal：
  语气分布/情绪事件/和好等增量与我的日记素材平时"等下一条用户消息搭车落盘"，
  最后几轮聊完直接关软件必丢，正常退出也丢）。补刷有内容才写，不给空角色造壳键。
- `_ensure_shard` 顶部新增**中途通电门控**（`_retrust_state`）：启动未通电的
  不可信会话里内存分片是从"空"起步的幻影；v1.2.1 只挡住了 shutdown 整体回写,
  入口单路写没有门控——store 会话中后期回电后，任何一次面板操作/消息落盘都会
  把幻影整包覆写盘上真实历史（1.2.1 修复后仅存的毁数据路径）。门控检测到
  "不可信 + 已回电"即重跑 `_load_state` 换回盘上真实数据再放行本次写入；
  `_retrusting` 防重入。仍未回电则维持"当场生效、重启即丢"的既定降级契约。
  所有写路径（面板入口/工具归因/tick）都先过 `_ensure_shard`，挂点即全覆盖。
- 回归：`tests/test_store_readiness.py` +4（中途通电重载/未通电降级不毁盘/
  shutdown 补刷 stats·review/不可信 shutdown 连带跳过新键）；均已反向验证
  （回退修复后以正确理由红）。

**批次2（错误传播与搭车缺口）**：
- 统一出口 `_store_write`（not-ready warning + 真失败 warning + 回传 Result）/
  `_store_read`（读 Err 留痕，与"值不存在 Ok(None)"区分——过去读失败静默表现为
  "数据全空"零线索）/ `_persist_error`（多键聚合）；九个保存方法全部返回
  `Result[None]`。cycle 的 not-ready 文案语义保留（`(enabled=)` 后缀并入统一格式）。
- 用户可见写入口按结果传播 Err（toggle/锚点/快进/重置/update_settings/解除情绪/
  触发情绪/删碎片/三处 clear/write_review_now/prune_lanlan + 12 情绪工具 +
  手记/日记工具）：真写失败（磁盘满/DB 锁）不再"面板显示成功、盘上没写"；
  未通电空转不算失败（降级契约不变）。多键写任一 Err 即报 Err，前面不回滚
  （真失败极罕见、重试幂等，保持简单）。
- `_maybe_write_review` 落盘失败整体回滚：快照还原篇目与素材、不记里程碑、
  返回 `persist_failed`（面板映射为 Err）——过去"先改内存+清零素材再写且不看
  结果"，写失败会同时丢这篇与该段素材，且重启后旧素材复活重复成文。
- 搭车缺口补刷（消灭失真注释）：语气分析收尾 `_save_tone_sense_state`
  （mood+stats+review 同刷）、`_apply_mood_action`（动作事件即时进
  stats@/review@）、rising_tide 和好计数、tool_write_diary 的 first_diary
  里程碑即时落盘（对齐 first_journal/first_review）。
- 回归：`tests/test_persist_errors.py` 10 条（`_ErrStore` 对指定 key 注入真
  Err）；回退批次2 源码后 9/10 以正确理由红。
- 已知残留（有意不动）：tick/whisper/shutdown/host_coord 等非用户入口写失败仅
  留 warning 不回 Err（后台链路不该变成会失败的入口，shutdown 兜底已覆盖）；
  prune_lanlan 部分失败后重试会被"角色不在名单"拒绝（Err 已如实报，残留键可见
  需带外处理）；同轮多 1-2 次本地 SQLite 写（批次1 审计已认可开销可忽略）。

### 1.2.2 审查轮（复核批次1/2 的复审修复，同版未发布故并入 1.2.2）

批次2 的"全链路审计"存在三处漏网与一处文档不实，复核后同轮修复：

- **P1 外观/图库并入统一出口**：图库与外观的全部读写（`gallery_index` /
  `gallery_img/<id>` / `panel_appearance` / `panel_bg` 迁移读）当年直连
  `self.store.set/get/delete`，绕过 `_store_write`/`_store_read`——未通电期间
  导入壁纸/保存外观"面板显示成功、盘上没写"且零 warning（README 却声称
  "外观也有此预警"）。现状：全部改道统一出口（新增 `_store_delete`：未通电留
  not-deleted 预警、真失败留痕回传）；未通电仍走"当场生效"降级契约，但自此
  全键一致留痕。幻影覆写在该块本不成立（图库无内存缓存，每次读盘合并再写）；
  `gallery_remove` 索引写失败回滚 blob 由入口 Err 传播、blob 删除保持
  best-effort（索引先除名即对用户不可见，残留 blob 无引用不致错乱）。
- **P2 主动搭话水位原子化**：`proactive_state` 四处落盘全不检查且顺序为
  "先翻总开关→后存水位"——水位真失败+强杀留下"开关已关+盘上无水位"，重启
  被误判"用户本来就没开"，主动搭话永久卡死。修复：新增 `_snapshot_proactive`
  （纯函数，`core/state.py`）+ `_proactive_persisted` 脏检查快照，
  `_persist_proactive_state` 成为水位唯一落盘出口（成功才同步快照，失败留脏，
  监督循环每趟 10s 收尾自动补写）；暂停改为**水位先落盘才准翻开关**（写失败
  撤销标记、回传 Err、不制造无据可查的关闭），恢复改为**开关先复原再清水位**
  （清除失败保留记录幂等重试）；`_maybe_sync_proactive_pause` 回传 Err，由
  `_apply_mood_action`/`lift_mood`/`reset_all`/`prune_lanlan` 纳入
  `_persist_error` 聚合，对用户可见。存量已卡死用户（修复前产生的状态）无法
  与"用户自己关的"区分，不做猜测恢复，README 如实记为限制。
- **P3 tick 自愈门控上提**：`_ensure_shard` 的中途通电门控要求"先有人碰分片"
  才触发，而 tick 在 `any_shard_enabled` 处即短路（幻影全关），"不可信启动 +
  store 中途回电 + 用户没碰面板/工具"组合下注入与情绪链路整场静默死掉（数据
  安全、功能停摆）。修复：同款门控（三行属性判断零成本）提上 `_supervise_once`
  第一行——tick/面板轮询 dashboard/各入口都先经此，恢复自愈；`_ensure_shard`
  门控保留（覆盖首条 tick 之前的入口调用）。
- **P4 随机锚点可信即落盘**：`_refresh_config` 的随机默认锚点过去只写内存、
  靠"后续任意 cycle 写捎带"，注释却写"立即落盘固化"（自相矛盾）；强杀在首笔
  cycle 写之前 → 锚点重新随机。修复：可信会话当场 `_save_shard_cycle`；
  不可信会话仍只写内存（落盘即幻影覆写）。注释按实际行为改写。
- **P5 成文失败可观测性**（真机反馈追查后追加）：面板「立即写一篇」的
  `compose_failed` 提示写着"详见插件日志"，日志里却一行都没有——直连请求
  异常只有 debug 级（不进日志文件），"HTTP 通了但响应非 OpenAI 形态"与
  "回复为空/剥壳后无正文"则完全静默。修复：`_post_chat_completion` 两类
  失败各留一条 warning（坏响应带前 80 字符预览）；`_maybe_write_review` 的
  compose_failed 按 `request failed` / `empty or unparsable reply
  (len=…, head=…)` 区分留痕，用户测试日志中的"6 次点击 1 次成文"类问题
  自此可直接诊断（2026-09-05 案例：4 次素材不足 10 轮软拒 + 1 次成文调用
  失败 + 1 次成文成功后素材清零再软拒，均无数据损伤）。
- 回归：`tests/test_save_chain_review.py` 10 条（水位写失败不翻开关+恢复先翻
  后清/未通电外观留痕/索引写失败回滚 blob/外观真失败传播/tick 自愈/锚点即
  落盘/成文失败留痕两分支/直连 warning 升级）；回退源码后 7/10 以正确理由红
  （其余 3 条锁修复前既有行为面）。全套 296 绿。

### 1.2.3：面板日记体验修复（排队成文 + 邀请当面递到 + 续写自动刷新）

用户实测反馈：日记页点「立即写一篇」后卡住无反馈，过一会才凭空刷出一篇。
调查确认属实，根因是**同步等待模型成文**挂在了面板动作的请求-响应链上：

- `write_review_now` 入口 `await _maybe_write_review(force=True)`，整条链
  （角色解析 HTTP ≤4s + 素材摘样 + 直连成文 urlopen ≤15s + 落盘）要 5～20 秒；
- 前端按钮无 pending 态（裸 Button + async onClick），toast 与刷新都要等
  `api.call` resolve——等待期界面一片寂静；
- 超时链压线：浏览器 axios 默认 30s 与宿主 hosted action 的
  `PLUGIN_EXECUTION_TIMEOUT=30s` 几乎同时到期，模型稍慢即"先报超时、后出文章"
  （文章照常落盘、由 5s 轮询自己冒出来），关面板断连更会走 499 取消路径把
  写到一半的 await 掐死、篇目连同落盘一起丢失；
- 无并发防抖：等待期连点每次都过素材门槛，可并发跑多趟模型写多篇。

修法（方案 A：异步队列 + 轮询回流，插件无常驻事件循环，tick 是唯一可靠执行体）：

- **入口改受理式**：只做秒级门控预检（开关/素材 ≥10 轮/槽位可解析——与成文
  共用新抽的 `_review_write_gate`，口径唯一），通过即在 shard 上打
  `pending_review_write` 排队标记并秒回 `accepted:true`；当场拒绝沿用旧
  written=False+note 形状。队列只有一个槽位：pending 或在飞时重复点击回
  `already_writing`，不叠加。
- **tick 头部消费队列**：`_drain_pending_review_writes` 放在 tick 的 enabled
  拦截**之前**（"我的日记"只认 `[review].enabled`，潮汐总开关 fail-closed
  时队列也必须能被写掉，与 `_supervise_once`/`_ensure_tools_registered` 同
  先例）；清标记→跑成文→结果写 `shard.review_write_result`
  （`{ts, written, reason}`）。撞上在飞锁则放回 pending 下趟重试，不记失败。
- **在飞锁**：`_maybe_write_review` 外层包 `_review_writing: set[str]`，
  同角色成文期间再入立即 `(False, "in_flight")`——封死队列写/tick 自动写/
  调试强写并发跑两趟模型互踩"追加+素材清零"快照的路。主体更名
  `_review_compose`（语义不变）。
- **回流通道**：dashboard `review_brief` 新增 `writing`（按钮禁用态+
  "正在写…"文案）与 `last_result`；`ui/panel.tsx` 按 `last_result.ts` 去重弹
  一次完成 toast（挂载时先认领当前值，旧结论不补弹；persist_failed 按
  1.2.2 口径弹 error）。新篇目进目录由既有的 `entries` 变化自动重拉承接。
- **代价与边界**：点击到开写最多多等一拍（≤10s，有"正在写…"提示兜着）；
  pending/result 都是内存位——插件重启即弃（面板写作态消失、用户可重按），
  断连不再丢篇（成文与客户端死活无关）。入口不再回 Err，
  `persist_failed` 改由结果通道如实上报。
- **个人日记邀请可见性（顺带）**：递邀后到她落笔前是纯静默（read 注入、
  她完全自主），面板同样显得"没反馈、凭空多一页"。新增
  `_journal_invite_pending`：比较节流水位 `last_journal_invite_ts` 与书页
  末段 ts（零新增持久字段），dashboard 下发 `journal_invite_pending`，
  日记页挂"正等她落笔"提示，她写出新页自动解除。

**第二轮（同版本）：个人日记的"点了但她不知道"与"续写看不见"**——调查显示
个人日记没有长等待问题（递邀/落笔工具全内存毫秒级），但有两处体验断层：

- **F1 续写刷新检测面错位（bug）**：`ui/diary.tsx` 的自动重拉条件是**页数**
  变化，而 `mood_journal_write` 默认续写在当前页——页数不变，她连写几段面板
  毫无动静（旧注释"页数不变内容变极少、手动刷新兜底"低估了：续写是默认路径）。
  修法：检测面换成"页码:段数:末笔时刻"逐页拼接的**书指纹**（journal_index
  本就下发这两个字段，后端零改动），指纹变即重拉；切角色时基线作废重认。
  阅读页翻开态按 page_no 定位，重拉后仍停在原页并显示新内容。
- **F2 手动递邀改当面递到（行为变化，经用户确认）**：read 邀请要等用户**下一次
  开口**才流进上下文——按钮点完"邀请已递出"，她其实还不知道，等待时长完全
  取决于用户何时再聊，是"点了没反馈"在个人日记侧的根源。参照阶段开场白先例
  （`ai_behavior="respond"` 立即起轮）把投递分双档：**面板 force 递邀走
  respond**，点击当场起轮、她即刻收到并自主决定（落笔则写；不想写按提示"轻轻
  放下"，不必硬找话说）；**tick 周期递邀保持 read**（安静的生命节律，每 7 天
  不该起轮打扰）。respond 档带 10 分钟冷却（`_JOURNAL_RESPOND_COOLDOWN_SEC`，
  复用节流水位判龄）：刚递过再按回落为 read 补递、note 如实说明"悄悄提醒"，
  防连点成骚扰；同 `coalesce_key` 在宿主主动队列里还会折叠只留最新一条。
  `_maybe_journal_invite` 返回改为 `(invited, deliver)`，`deliver ∈
  ""/respond/read`，面板入口按三态给 note；supervise 周期调用忽略返回值，
  调试入口回显 `deliver`。fail-closed 复核：`_journal_enabled` 是
  潮汐 ∧ 情绪系统 ∧ `[journal].enabled` 三道闸，respond 档同样受辖，
  总开关关着当面递邀也绝不起轮。
- **验证**：`tests/test_journal.py` 新增 4 条（首递 respond→冷却回落 read→
  水位拨旧恢复 respond；周期恒 read；三闸拒绝；入口三态 note）。全套 305 绿。

### 1.2.4：v1.2.3 回归审查修复（提交门禁 / 队列异常 / 递邀回执）

对 v1.2.2→v1.2.3 逐项复审的结果：五处修复 + 一处文档补述，全部不改 1.2.3 的产品语义。

- **A1 hosted-tsx 门禁染红（工具链破口，运行时不炸）**：`ui/diary.tsx` 的
  `DiaryPane` props 里 `reviewBrief` 是内联复刻的窄类型
  （`{enabled, entries, progress_turns, turns_threshold}`），而 1.2.3 新读的
  `reviewBrief.writing` 只声明在 `ui/types.ts` 的 `ReviewBrief` 上。
  `frontend/plugin-manager/scripts/check-hosted-tsx.mjs:878-899` 是**建 TS program
  收 pre-emit 诊断**的（`strict:false` 也拦不住未知属性访问），于是三处 TS2339 把
  DESIGN 自己承诺的提交前门禁弄成红的。市场 CI 不跑这个门禁（只有 ruff + `check -r`），
  所以 v1.2.3 的 Release 照样产出成功——**"门禁存在但不在发布链上"就是这次的漏网**。
  修法：props 直接用 `ReviewBrief`。对照实测（同一套 compilerOptions）：v1.2.2 exit 0、
  v1.2.3 exit 2 三条红、修后 exit 0。运行时一直是对的（类型擦除，后端确实下发
  `writing`），所以这属提交纪律而非面板故障。
- **A2 队列成文的异常石沉大海**：`_drain_pending_review_writes` 在 `await` 之前就把
  pending 清零（防一条队列卡住两趟 tick），于是成文抛出未预期异常时结果位永远不写——
  用户已收到"已开始写"，随后按钮自己变回可点、什么都不弹，比 1.2.2 的同步链路
  （api.call 失败至少弹 error）更不可见。修法：drain 内兜住异常、warning 留痕、写
  `{written:false, reason:"compose_raised"}` 走既有回流通道；在飞锁由
  `_maybe_write_review` 的 `finally` 释放，篇目与素材一分未动。
- **A3 切角色补弹陈旧结果**：`reviewResultSeen` 只按 ts 去重，但 `last_result` 与
  pending 都是**按角色**存的内存位——从 A 切到 B 时，B 上一轮会话里早已看过的旧结论
  ts 与 seen 不等，会被当成"刚写完"补弹一次。复位必须写在 effect 内部（新增
  `reviewResultRole`：角色变即按挂载语义认领当前值、不弹），因为面板那个
  `[state.lanlan]` effect 声明在它之后，同一轮 render 里等它跑完 toast 已经弹出去了。
  证据是同版 diary.tsx 的书指纹 ref 就正确复位了（`[lanlan]` effect 里
  `bookFpSeen.current = null`）——一处做了一处漏了的不对称。
- **A4 递邀不看提交回执**：`push_message` 返回 `submitted`，插件四处推送都不看。
  1.2.3 把话从"悄悄流进上下文"升到"已当面递到她手上"，并挂"正等她落笔"直到她写出
  新页——通道拒收（宿主背压/不可用）时递空的那一次会让假提示一直挂到下一个 7 天节奏。
  修法：只有**显式** `submitted=False` 判失败（返回 None 或不含该键的旧形状不误伤）；
  手动档回滚水位，用户可立刻重按；周期档**不回滚**，免得通道一直坏时每趟监督重推
  刷屏（按 24h 节流等下一轮）；入口按 `mode="failed"` 给独立文案，不再与"开关未开启"
  共用一句。面板同步分色：`mode="failed"` 走 `toast.error`，与"开关未开启"的 info
  软提示分开（新增 `panel.journal.inviteFailed` 键 ×8 语言，i18n 净增 1 键 → 402）。
- **A5 `debug_journal(force=true)` 漏传 force（1.2.3 顺带照出的旧账）**：调试入口调的
  是非 force 档，除 24h 节流外还压着 `journal_due` 的 7 天节奏闸——昨天刚写过日记的
  机器上它静默 `invited=false`，与 README 承诺的"立即推一次邀请"以及 1.2.3 自己新写的
  注释"必走 respond 当面递到档"都不符（行为自 0.7.0 起如此，注释是这次写错的）。
  修法：传 `force=True`（只保留 `_journal_enabled` 三道闸），note 说明 `deliver` 语义。
- **文档补述（不改行为）**：README「平台机制」的排队成文条补上真实代价——成文那一趟
  会占住后台轮询约 20 秒（模型直连 ≤15s + 素材摘样一次），期间只在 tick 里跑的用户
  消息注入/语气感知/碎片捕获顺延一拍；限时情绪到期解除与主动搭话恢复不受牵连
  （面板 5s 轮询也驱动 `_supervise_once`）。这是"把慢操作交给唯一可靠执行体"的必然
  代价，不为此另开成文线程。
- **测试**：`test_review.py` +1（异常必须变成可见结论、锁必须释放、篇目与素材不得动）、
  `test_journal.py` +4（手动档回滚 / 周期档不回滚 / 无 submitted 键不误伤 / debug 档
  真能强制）。反向验证：四个源文件退回 1.2.3 后 5 条里 4 条以正确理由红（第五条锁的
  正是"旧形状不误伤"的容忍面，两版都绿）。全套 310 绿。
- **有意不动**：A2 只兜住异常、不给 pending 加 TTL——队列唯一消费者是 tick，而 tick
  头两步（`_supervise_once` 全量 try/except、`_ensure_tools_registered` 用返回标志
  不抛）都不会挡住它，且 pending 是内存位、插件重启即清，加 TTL 只换来一条新漂移面。
  A1 未顺手把 `check-hosted-tsx` 接进插件仓库 CI（那是平台侧 workflow 的事，越界）。

### 2026-09-07 复核：升级/卸载链路的记录安全（宿主代码/状态分离）

- **宿主布局**（#2943 起，2026-08-27 合入，09-03 安装版构建已覆盖）：
  代码根 `get_user_plugin_exec_root()` → `<数据根>/.neko-plugin-installations/plugins/<id>/`；
  状态根 `get_plugin_state_root()` → `<数据根>/plugins/<id>/`（`config/data/cache`
  三件套，`store.db` 在其中）。`plugin/settings.py` 两处 docstring 明文规定
  安装/升级/回滚/卸载不得把状态根当包替换目标；
  `ensure_plugin_exec_state_roots_separated` 对两根重合 fail-closed；升级事务的
  `_validate_replacement_targets` 硬拒任何与状态根重叠的替换目标。
- **升级事务**（`installation_transactions/replace.py`）：代码目录整体 rename →
  `.upgrade-backups/<id>.bak.<ts>` → 装新包 → 身份校验 → 只并回 manifest 邻接
  profiles → 成功清备份 / 失败原子回滚 + 重启。rename 与 preserve 的目标全部在
  代码根内，`data/` 根本不在被操作树里。Market 更新（market_bridge
  `_replace_market_plugin_transaction`）与本地 `.neko-plugin` 覆盖导入（plugin_cli
  service）共用这套 `replace_plugin`——1.2.1 勘误里"Market 路径待验证"就此收口：
  两条路径的保留语义一致。
- **卸载**（`installation_transactions/uninstall.py`）：代码目录同盘 rename 进
  `.uninstall-backups` 做提交式删除（rmtree 只发生在 staged 副本上）；所有触及
  状态根的路径判断都是保护性护栏（目标落在状态根内即拒绝），用户偏好标
  `preserved`。`store.db` 卸载后原地留存、重装读回——**此为代码审查结论，
  卸载+重装尚未真机实测**（升级路径有 2026-09-07 实证，见下）。
- **布局迁移**（`layout_migration.py`）：新宿主首启把状态根内的代码一次性拷到
  代码根（`_copy_legacy_plugin_tree` 排除 `config/data/cache`），写账本
  `.neko-plugin-layout-v1.json`，账本条目使旧拷贝被"已迁移"跳过、防止复活。
  **残留注意**：状态根内旧代码副本不会被删除（本机 `plugins/forever_companion/`
  下留有 v1.2.2 的 .py 文件，与数据目录同居），靠账本压制；账本文件丢失或被
  手动搬动数据目录时，旧代码有被重新扫描顶掉新版的可能，操作时要留意。
- **实证（本机 2026-09-07）**：14:04 布局迁移（账本生成、代码入 exec 根）；
  14:36:45 旧实例停止 → 14:36:57 v1.2.3 启动，插件日志
  `plugin store ready after 1 attempts` + `startup ok: enabled=True phase=luteal
  day=24`（与升级前逐项一致）→ 14:38:36 `store.db` 继续写入。迁移与升级全程未
  碰记录；当日全部日志 `store not ready`/`persist failed`/`untrusted` 危险行零条。
  1.1.0 那条"更新不丢"断言在 1.2.1 勘误一轮反转后，最终由宿主 #2943 落地成立。
- **README 同步**：「⚠ 覆盖导入目前会清掉全部记录」改写为「升级与卸载后记录保留
  （2026-09-07 复核更新）」，清记录风险仅保留在 2026-08-27 之前的旧宿主构建。
- **残留缺口**：记录保护依赖宿主布局，插件自身仍无用户侧导出/备份通道
  （`store.db` 单点），列为后续功能候选。

### 1.2.5：日记页刷新按钮移除（自动更新链路已是唯一事实源）

用户反馈「个人日记/我的日记要手动刷新」。复查结论：面板 5s 轮询 + 1.2.3 书页
指纹（页码:段数:末笔时刻）与 review_brief 篇数检测已覆盖新页/续写/新篇全部
常见变化，手动刷新按钮与自动链路功能重叠，且它的存在反而暗示「不点就不更新」。

- **改动**：`ui/diary.tsx` 移除个人日记与我的日记工具栏的「刷新」按钮；
  `reloadBook`/`reloadReview` 保留，仍由挂载/切角色/指纹检测自动调用。
  `panel.journal.refresh` 键无任何 i18n 词条（纯 defaultValue），随按钮一并消失，
  8 个 locale 文件零改动。
- **边界**：时光日记（碎片流）本来就没有刷新按钮，不在本次范围；已知的三个
  自动检测盲区（分页展开后冻结、我的日记净数不变、时光页热力图单次拉取）
  未在本次修复，列为后续候选（移除按钮后这些盲区从「可手动兑底」变成
  「真盲区」，下个版本应优先补）。
- **验证**：esbuild 转译通过；`neko-plugin check` 0 错误；Python 侧零改动。

### 1.2.6：新手引导 + 配置引导（首次向导 + 就绪清单）

解决"装完插件不知道从哪开始"：新宿主装完只有 fail-closed 的关闭态，
用户面对满页签不知道第一步是什么。两块能力：

- **首次向导（OnboardingWizard，Modal 分步）**：仅**安装后从未完成/跳过**时
  自动弹出（全局 store key `guide`，`wizard ∈ ""/done/skip`）。四步：
  ①欢迎与能力全貌（情绪/节律/三本日记/时光统计）→ ②开启陪伴（读当前
  enabled，就地调 toggle）→ ③模型通道体检（channel_status 三色灯，缺失只
  说明"该功能休眠、核心不受影响"，不阻塞）→ ④完成指引（就绪清单在总览、
  深度设置在各页签）。"稍后再说"= skip，同样不再自动弹；管理页可"再看一次
  新手引导"（reopen 把 wizard 清回 ""）。
  向导**不改任何配置**（除用户主动点的 toggle），锚点策略沿 1.1.0 的
  randomized_default_anchor（"她早就有节律，只是今天开始被观测"），向导只
  解释这一点，不要求用户设锚点。
- **配置引导（就绪清单 GuideCard，总览页常驻）**：把散落的健康信号收敛成
  一张清单，纯本地即时计算进 dashboard 轮询（零模型开销、零新增 IO）：
  ①开启她的节律（must）②周期起点已确认（must：anchor 非空）③情绪系统已
  开启（suggest）④至少一个模型通道在线（suggest：碎片/成文/语气任一可用）
  ⑤你们已开始相处（suggest：stats.first_seen 存在，仅展示无动作）。每项带
  直达页签跳转；must 全过且无 suggest 欠账时整卡收起。`readiness_ok` 一并
  进 dashboard（供未来 HUD/提示复用）。
- **数据**：`guide` 一个全局 key `{wizard, at, version}`（不 per-lanlan——向导
  是安装级一次性事件；角色级欠账由就绪清单实时算，无状态）。经 `_load_state`
  载入（`_retrust_state` 一并重置），写走 `_store_write/_read` 统一出口；
  未通电会话按"当场生效、重启即丢"的既定降级契约。入口 `set_onboarding`
  （action ∈ done/skip/reopen）为面板专用 `@ui.action`，写失败传播 Err。
- **UI**：新文件 `ui/onboarding.tsx`（OnboardingWizard + GuideCard 两个 export，
  遵守 hosted-tsx：export 先于 JSX 闭合标签、无 SVG、步骤点用 div）；
  panel.tsx 挂 Modal（`wizard_pending && 本会话未关过` 双闸——5s 轮询滞后期
  不靠 server state 关闭弹窗）；overview.tsx 顶部插 GuideCard；manage.tsx 加
  reopen 按钮。i18n 新增 `onboarding.*` / `panel.guide.*` 约 40 键 ×8 语言。

### 1.2.7：能力中心（功能模块解耦与统一开关体系）

背景：功能闸散落各 mixin（`_mood_enabled`/`_fragments_enabled`/…各写各的），
闸与闸之间隐式层级无单一事实源；新 LLM 功能接入要改 tick/面板/配置/日志/测试
五处。本版本把"模块开关"抽成一层薄地基：

- **声明层 `core/capabilities.py`（纯数据零 SDK）**：九项能力（whisper/
  phase_openers/activity_sense/anniversary/mood_engine/tone_sense/fragments/
  journal/review）各登记：分组、依赖链、**绑定的既有配置键**（toml 段不迁移
  不重排）、占用工具（共 12）、LLM 触点类型（injection/tool/direct/host_http/
  none）；`evaluate_capabilities(root, config_flags, overrides)` 按声明序
  （拓扑序）解析出每能力的生效态与**不生效原因**（master_off/user_off/
  config_off/upstream_off），原因一路传到面板。
- **运行层 `mixins/capabilities.py`**：`_cap_effective(cap_id, shard/lanlan)`
  唯一判定入口；既有闸（_mood_enabled/_fragments_enabled/_review_enabled/
  _journal_enabled/_emotion_sense_enabled、whisper/opener/activity/anniversary
  注入点）全部改为一行转发——**方法名与语义不变**，收编零行为变化
  （无否决时与 1.2.x 完全等价，旧测试 321 个全绿不改）。
- **否决式覆盖层（按角色）**：面板开关只写 `caps@<角色>` / 全局 `caps@*` 的
  "关"集合；打开 = 撤否决回落配置默认，绝不存在"设置页关、功能页硬开"两张皮；
  强行点亮失败时入口回 `note=reverted_to_default`，面板如实回弹。旧设置页/
  toml 路径零改动；prune 孤儿角色一并清 caps@。未知名宽容放行（拼错 id
  不得悄悄关掉功能）。
- **工具显隐（高级选项，全局 `[capabilities].hide_disabled_tools`）**：默认
  温和模式（在位、调用拒，既有语义）；开启后对所有已登记角色都不生效的能力，
  其工具经官方 `LLM_TOOL_UNREGISTER/REGISTER` IPC 摘挂——**只动宿主可见性，
  本地 `_llm_tools`/动态入口/运行闸全部不变**；每趟 tick 纯内存差集比对，
  `_reemit_missing_tools` 巡检跳过隐藏名单防抵消；宿主工具注册无角色通道，
  故显隐按"任一角色生效即留"的并集口径。
- **面板**：新「功能」页签 `ui/features.tsx`——清单由声明表自动渲染
  （开关 + LLM 徽标 + 不生效原因）；**数据源随 dashboard 5s 轮询下发**
  （后端 `_cap_view` 小载荷纯内存计算，与总开关/其它页设置同帧一致——
  首版"进页按需拉一次"会让状态条拨了总开关后功能页横幅停在旧态，
  发布前实测定为陈旧窗口不可接受而改此方案）；`list_capabilities` 入口
  保留（API/调试，与轮询共用同一构建器口径唯一）；i18n 新增 47 键 ×8 语言；
  `@ui.action` 三入口 list_capabilities/set_capability/set_capability_flags。
- **后续新 LLM 模块接入成本**：写 service + 声明表一行 + 闸走 `_cap_effective`，
  开关/面板/持久化/工具生命周期/多角色全自动。

### 1.2.7（增补一）：功能介绍卡（面板「功能管理」每行→居中 Modal 单页介绍，纯增量）

- **需求**：每个功能行加一个文字按钮「功能介绍」，点开 Modal 卡片展示：
  功能作用 / 主要场景 / 依赖 / 限制与注意事项 / 原理演示图；单页看完不滚动；
  只做介绍类展示，不改其它模块行为、不新增配置键。
- **文案事实源在 Python**（用户明确要求后端下发、不接受前端写死）：新建
  `core/intros.py`（纯数据 + 纯函数，零 SDK 依赖，与 core 层同一纪律）：
  `CAP_INTROS` 表按能力 id 登记中文原文（purpose/scenarios/limits/flow），
  i18n key 由 cap id 派生（panel.capintro.<id>.purpose / scene<N> / limit<N> /
  flow<N>，N 从 1 起）；`build_intro_payload(spec, ref)` 把文案组装成
  `tr()` 引用 + 从声明表现场取结构化事实（deps/config_keys/llm/tools），
  介绍卡与开关状态永远同源同口径。新增能力忘登记 → payload `found: false`，
  面板如实显示「介绍暂缺」不瞎编。
- **传输通道（调研结论，重要）**：宿主只对 dashboard context 做 `resolve_i18n_refs`
  （ui_query_service L1617），**action 返回值不解析** → 后端把 `tr()` 引用 dict
  原样透传，前端 `resolveText()` 用 `t($i18n, {defaultValue: default})` 按宿主
  语言展开（zh 走 default、en 进 i18n 文件）；payload 同时兼容裸字符串（测试
  桩环境 tr 直返中文），两种形态一套渲染代码都吃得下。**不进 5s 轮询**：
  新增按需入口 `get_capability_intro`（@ui.action，与 set_capability 同级），
  点开才拉、前端按能力 id 缓存一次（文案静态；实时状态由行数据现场叠加，
  缓存不会陈旧）；避免 9×5 段长文案搭 dashboard 便车每 5 秒重发。
- **多语言范围（用户决定：中英双语）**：本期只登记 zh-CN.json + en.json
  （共 +111 内容键 +3 入口元数据键）；其余 6 语言按 default_locale=zh-CN 回落
  中文，将来补翻译只需往对应文件加同名 key，三层零改动。zh 值由生成脚本
  从 CAP_INTROS 自动导入（事实源单二），并有测试逐字对账防漂移。
- **原理图：hosted 运行时确认无 SVG（三重封死）**：渲染器 `document.createElement`
  全程无 `createElementNS`（JSX <svg> 成未知元素）、`dangerouslySetInnerHTML`/
  `innerHTML`/`srcdoc` 被 patchProps 黑名单无视、`img src` 的 data-URI 白名单不含
  svg；ring.tsx 头注早已踩坑在案。改用**纯 CSS 流程图**：流程数据（kind/
  icon/label）在 core/intros.py 声明（emoji 图标，kind 只决配色
  src蓝/proc紫/gate琥珀虚线/out绿，未知 kind 视觉回落中性），
  `ui/capintro.tsx` 的 `IntroFlow` 通用渲染（胶囊节点 + 文字箭头，可换行）；
  字符串纪律：介绍正文内引用一律「」角括号（ASCII 双引号截断 Python 字符串、
  全角弯引号会被工具链规范化掉，都踩过；测试有禁 ASCII 引号断言）。
- **文件落点**：新建 `core/intros.py` + `ui/capintro.tsx`（Modal+流程图+
  LLM_BADGES 徒迁至此统一维护，features.tsx 反向 import）；`mixins/capabilities.py`
  只新增 `get_capability_intro` 入口（既有入口/判定/轮询零改动）；
  `ui/features.tsx` 行内加按钮 + 状态缓存；`ui/panel.tsx` 接线 `onLoadIntro`；
  `ui/types.ts` TFunc 第二参开放意插值键（运行时 interpolateI18n 本就支持
  {n}/{{n}}，校验器实测卡出）；`ui/styles.ts` 新增 `.tm-ci-*` 样式块（Modal
  className 挂 backdrop，宽度选择器从这层下钻）。
- **踩坑记录（1.2.7 白屏事故复盘，重要）**：
  ① **链接器导出扫描器会在带 JSX 的函数体后丢位，漏掉其后所有
  `export function`**（不管那个 JSX 函数是不是导出）：编译产物残留裸
  `export` 是**解析期** SyntaxError，整个 iframe `<script>` 直接死亡，连
  __showHostedError 错误卡片都跑不到——面板"纯白无报错"的根因就是它，
  本地官方 tsc 校验器（check-hosted-tsx）查不出，只有真实链接器
  （bundleHostedTsxSource + sucrase）能复现。生存规则：模块内**任何带 JSX
  的函数之后不得再有 export 语句**；JSX 辅助组件不导出、放文件尾靠函数
  声明提升引用（仓库既有注释"辅助组件放文件尾部靠函数声明提升"即此），
  必须跨文件用的组件把全部 export 排在首个 JSX 函数之前；命名类型
  导出（export type）安全（sucrase 最终抹掉）。capintro.tsx 头部已立
  "签名纪律"注释；今后任何面板改动后必须跑一次链接器泄漏自检：
  `node -e` 用 bundleHostedTsxSource 包一遵，grep 产物里 `^\s*export\s` 必须为 0。
  ② 复现 harness 的保真度陷阱：编译段必须包在 try{} 块内（真宿主如此，
  否则 entry 顶层 `const { Alert }` 与 runtime 全局 function Alert 在 harness
  里假阳性冲突）；__hostedProps 必须 `...window.NekoUiKit` 全量展开（否则
  props.t undefined）；api.call 桩返回宿主信封 {plugin_id, action_id, result}
  形态（unwrapCallResult 认这个）。
  ③ hosted-tsx 校验器不识别泛型尖括号层级的逗号，`export const X: Record<
  string, {a,b}>` 被误判"多声明符"——用类型别名绕开（utils.ts 既有同款注释）。
  ④ 声明表 config 段名不带方括号，组装展示键时才包 `[{section}]`。
  ⑤ 介绍正文禁 ASCII 引号（曾截断 Python 字符串），统一用「」。

- **测试**：`tests/test_intros.py` 15 项：声明表与能力表同步/结构合规/首尾
  src→out、payload key 派生与同源字段、未登记 found=false、入口未知拒绝/
  可序列化、i18n zh/en 覆盖 + zh 逐字对账 + 孤儿 key 检测。

### 1.2.7（增补二）：单页化改造（发版前用户实测反馈）

- **用户反馈两点**：① 流程图 emoji 图标不要（全部删除）；② 功能页签上下滚动
  麻烦，要单页——追问确认**两处都要**（页签 + 介绍卡）。
- **emoji 彻底移除**：`FlowNode` 数据类删 `icon` 字段、`build_intro_payload`
  不再携带、`IntroFlow` 不渲染、`.tm-ci-node-icon` 样式删除、
  `tests/test_intros.py` 断言翻转（payload 含 icon 即红，防回潮）；
  类型区分全靠配色胶囊（gate 虚线），i18n 无需动（flow 键只存文字）。
- **功能页签单页**：`.tm-feat-cols` 三组卡横向并排（4/2/3 行，总高≈最高列），
  高级选项压成单行窄条（长说明挂 title 悬停，右侧状态文本复用既有
  hideToolsOn/Off 键零新增）；媒体查询回落 900px→双栏、620px→单栏。
- **介绍卡单页**：弹窗加宽 820→980px；五段重排双栏网格（左作用+场景 /
  右限制+依赖，流程图通栏置底）；全套留白/字号紧化；工具名独立行收进
  计数 chip 的 title 悬停。**真浏览器实测（Playwright，两档视口 1280x860 /
  1000x700）：页签与 9 张卡全都 overflow=0px**，窄窗回落滚动属预期保险。
- **验证基建升级**：复现 harness 进化为常驻测量脚本（逐卡开合测
  scrollHeight-clientHeight + emoji 残留计数 + 链接器泄漏扫描前置），
  面板布局改动后必跑。


### 1.2.7（增补三）：介绍卡 → 页内子页 + 链接检查门固化（发版前真机反馈）

- **两轮反馈收敛到载体**：① 默认窗口出横向滚动条（“突兀且麻烦”）——居中弹窗
   的 95vw 与 backdrop 的 2×20px 留白/滚动条互不计账，双栏断点又看 iframe
   视口而非弹窗宽；② “卡过大且被压缩”——弹窗与面板框体脱节。**根因是载体
   选错**：改页内子页——`features.tsx` 在 introItem 置位时早返回整页切换，
   `capintro.tsx` 导出 `CapIntroView`（头部一行：返回+功能名+LLM 徽标+生效态），
   正文直接内嵌 `.tm-content` 自然宽度；双栏→单栏用
   `auto-fit minmax(min(330px,100%),1fr)` 按可用宽度折行，长文本全线
   `overflow-wrap: anywhere` 兜底；`.tm-ci-modal`/`.tm-ci-foot` 时代样式与视口级
   media query 全部删除，横向溢出物理上不存在。
- **白屏二进宫（踩坑记录①复发，实锤）**：重写时把 `IntroFlow`（带 JSX、
   不导出）提到 `CapIntroView` 之前 → 扫描器撞漂吞掉其后 export → 裸 export
   残留 → 整面白屏。用宿主同源 scanner 双向复现（HEAD 版提前置 FAIL /
   移回尾部 OK）证明坑记①“带 JSX 的函数不分导出不导出”成立；问题在纪律
   未机器化成门、靠人肉自觉。IntroFlow 固定文件末尾，函数声明提升保前向引用。
- **检查门固化**：`tools/check_hosted_link.mjs`（宿主同源 `hostedTsxModule.mjs`
   真链接；入口从 plugin.toml 动态解析；scanner 路径支持 env/宿主挂载/并排
   仓三种布局；丢导出 exit 1、环境缺失 exit 2）+ `tests/test_hosted_link.py`
   （pytest 主链封装，降级 skip）；反向对照＝临时仓复刻白屏布局，门正确 FAIL。
   `tools/` 进 pyproject `[tool.neko.build]` 排除，发行包零混入。
- **i18n**：新增 `panel.capintro.back`、更新 `loadError` 文案（提示重启插件
   服务），按 chrome key 惯例只登记 en + zh-CN（其余语种 default 兜底）；
   test_intros 孤儿白名单加 `back`。
- **验证**：pytest 363 全绿（含链接门）；反向对照（白屏布局）门 exit 1；
   `neko-plugin check` 0 错误；发行包内容抽查不含 tools/、IntroFlow 在尾布局。



### 1.2.8：面板页签重排（用户反馈）

- **左侧主链按叙事顺序相邻**：总览 → 日历 → 日记 → 时光 → 功能 → 周期 →
  情绪 → 设置。原顺序中周期/情绪卡在日历与日记之间，打断"日历看节律、
  日记读记录、时光数日子、功能管开关"的主链；周期/情绪属机制调节页，
  后移至功能之后、设置之前。
- **改动面**：仅 `ui/panel.tsx` 的 `tabs` 数组与 `VALID_TABS`（后者只做
  合法性校验，同步保序避免误导）；内容区为 `activeTab` 独立条件渲染，
  onboarding/guidecard 与文档均无页签顺序硬编码，i18n key 不随序变化。
- **验证**：pytest 363 全绿（含 hosted 链接门）。


### 1.2.9：小窗滚动可用性（用户反馈三轮收敛）

- **左侧导航可滚**：宿主插件详情窗口高度不足时 shell（100vh+overflow:hidden）
  把末页页签物理裁掉且无从触达——`.tm-tabs` 改 `overflow-y: auto` +
  `overscroll-behavior: contain`，滚动条本体彻底隐藏（用户终稿反馈：看得见
  不如纯滚动手感舒服；历经常驻细条→悬停浮现→无条纯滚三版，注释留痕防回退），
  `scrollbar-width:none` 不占 gutter、侧栏宽度零损失
- **内容区滚动条静默化**：`.tm-content` 竖条默认全透明，仅两种时刻浮现——
  鼠标落入右缘 16px 命中带（`:hover` 命不中滚动条槽，panel.tsx
  `onPointerMove` 判距切 `tm-content-hot`），或正在滚动（`onScroll` 续命，
  停 900ms 归静；拖动滑块时 pointermove 不进元素但拖动即滚动，onScroll 接棒）。
  槽 8px 恒占位防浮现抽动；卸载清 scroll timer
- **日历页横向条根因修复（用户反馈）**：两坑叠加——①`scrollbar-color:
  transparent transparent` 触发 Chromium「设非 auto 即整体屏蔽
  ::-webkit-scrollbar 定制」，垂直条退回 ~17px 经典宽挤窄内容区；②只写
  `overflow-y:auto` 时 `overflow-x` 的 visible 连坐按 auto 计算，
  `tm-slide-in` 的 translateX(12px)/`repeat(7,1fr)` min-content 不收缩等
  几 px 溢出即弹横条。修法：删 scrollbar-color 改纯 webkit 单轨、内容区显式
  `overflow-x: hidden`（全站纵向流式布局，无合法横滚需求）、日历网格
  `repeat(7, minmax(0,1fr))` 治本。两坑写入注释防复发
- **验证**：pytest 363 全绿（含 hosted 链接门）；构建包抽查三项（横滚封死/
  无 color 陷阱/网格 minmax）全过


### 1.3.0：日记页拟真书本（个人日记 = 她手写的一本 / 我的日记 = 第三者铅印卷宗）

用户提出：面板日记区的两本书想要"真书的感觉"。四问定案（拟真度 B 档 /
两本性格区分 / 连续卷轴 + 分页视觉 / 目录改书脊书架），逐条落地：

- **目录 → 书架**：`TocView`/`ReviewProgressView` 的列表行换成 `.tmb-shelf` +
  `.tmb-spine`——每页一根站着的书脊（顶端页码方块、竖排起始日期、书根段数），
  **书脊皮色 = 该期心情均值**（复用状态栏 `moodDotColor`，近零自动落灰，与全站
  心情语义同口径）；悬停整本抽出 12px + 影子拉长。旧版周记迁移页在脊上贴角签
- **两本分岔（设计即叙事）**：个人日记走**手写本**——布面书脊（线装三孔 + 竖排
  题签）、暖纸四层渐变（纤维/四边压暗/左上受光/基色）、楷体、段间翻页压痕、
  丝带书签、每段续写是一张自己的纸（自己的边影 + ≤0.18° 歪斜 + 和纸胶带贴角）；
  我的日记走**铅印卷宗**——冷灰打孔纸（左三孔）、宋体正文 + 1px 油墨压痕、
  卷首等宽口径行（轮数/统计区间/她自主起的情绪次数）、文末靠右一枚旋转 -5.5°
  的朱印落款（"盖章必不正"），不贴角不歪斜——档案是打好的，不是她手边摊的
- **分节显示层解析（关键约束）**：`splitSections` 把正文里的
  `【这段时间】/【我在想】/【对他的感觉】/【想说的】` 反解成分节小标题。
  **只解析显示，存储与注入文本一字不改**——这些小标题是 `core/journal.py`
  与 prompt 共用的中文声明制词表，改不得；标题原样呈现不做二次翻译（她说出口的词）
- **版式取舍**：正文随 `.tm-content` 自然滚，`.tmb-foot`（放回书架 / 上一页 /
  页码 / 下一页 / 段数）用 `position: sticky; bottom: 0` 常驻下缘。不做定高
  单页的理由写进注释：单页可达 8 段 × 900 字，定高必然出现"页内还有滚动条"
  的两重滚动，小窗尤甚。**纸面一律不加 `overflow:hidden`**——它会变成 sticky
  的滚动容器当场废掉常驻，丝带/页脚外扩改由 `clip-path` 与负外边距自处理
- **样式归属与预算**：新增 `ui/styles_book.ts`（书本调色板 20 个局部变量全部
  有定义有消费，暗色孪生与 `prefers-reduced-motion` 同文件收口），与
  `PANEL_STYLES` 并列第二个 `<style>` 注入；同时从 `styles.ts` 删除被替换的
  死样式（`.tm-book*`/`.tm-toc*`/`.tm-paper` 及其暗色孪生，51 行）。
  hosted 依赖预算 25/32 文件、345/512 KiB
- **i18n**：登记 en + zh-CN 各 +12 key（题签/页眉引导词/卷首三项口径/朱印/
  放回书架等，其余 6 语种按仓惯例回落 `defaultValue`）；顺手清掉重做后零引用的
  `panel.journal.backToToc`/`pageShort`/`panel.review.pieceShort` 三键（615→624）
- **编号自查（真机前，两条都是"看起来对、其实是谎"）**：
  ① 页脚 `{page.page_no} / {totalPages}` —— `page_no` 是**全书累计页码**（后端刻意
  "淘汰最旧页后编号仍连续"），`totalPages` 是**架上现存本数**，淘汰过一次即显示
  「63 / 52」。这个式子从旧版 `PageReader` 继承，但 1.3.0 把书号做成了视觉主角
  （书脊顶端白方块 + 页脚居中大号），隐性错误变成显性——修法是两个数各归各位：
  页眉继续用累计页码（版权页那个位），页脚改用架上位置（与上一页/下一页同口径，
  新增 `position` prop）。
  ② 我的日记盒号 `entries.length - i` —— 倒序列表里现算，**最旧一篇被裁时全架编号
  集体 -1 平移**（上周"卷 3"这周变"卷 2"）。修法是不显示会变的数：盒脊三样全换成
  永不漂移的事实（印章=互动轮数 + 单位字、竖排=成文日、统计区间进悬停 `fileTip`
  与卷首口径行），轮数徒章为三位数加宽到 27px。
  ③ 顺手清掉重做后零引用却仍被新代码摸到的 `panel.review.pieceShort`（语义也不对：
  那个位置显示的是轮数，单位"篇"是错的），新增 `panel.review.turnsUnit`/`tipComposed`
  并登记 en+zh-CN（624→626）

- **全屏真机反馈「书有点扁」（用户截图 + 逐像素实测）**：截图 1920×1032 原生，量得纸页
  **728×366 ≈ 2.0:1**（横条），丝带落在 x 1427-1441、页眉深色文字右端顶到 1441 →
  丝带确实划过页眉末字。三处成因与修法：
  ① **纸高无下限（主因）**：旧版纸高完全由内容决定，她只写 3 段就塌成 366px。
     `.tmb-page` 加 `min-height: clamp(430px, 68vh, 700px)` + flex 列，`.tmb-page-body`
     设 `flex:1 1 auto` —— 写得少时纸仍是一整页：空白落在页底、页脚钉在最下（真书如此）；
     页脚同时去掉负 bottom margin（flex 列下会在页脚下面留一条纸底白缝）。
  ② **行宽失控（次因）**：780 书宽 → 正文列 ≈ 686px ≈ **47 字/行**，超出中文书舒适
     行宽（30-38 字）一档；书宽收到 **640** 并把正文 14.5→15px → 实测 501px ≈ **33 字/行**。
  ③ **丝带车道开错位置**：先只给页眉加 padding-right 是错的（正文行尾仍会从丝带下面
     穿过）——车道应开在**纸页右内边距**（62px；丝带占 46~61），页眉与正文一并避让；
     小窗按丝带新位（右 24 + 宽 12）收到 40px。
  附带：整卡内容收进与书同宽的居中列（`--tmb-col` 挂在 Card 的 `.tmb-card` 上，工具条
  `max-width` 同值），「请她写一篇」不再贴到 2K 屏最右缘、与书脱节
  > 复盘：本轮第一次答复时我先把截图当 2560 宽、用"150% 缩放"解释宽度对不上，又断言
  > "书脊比纸页向下多出 14px" —— 两条都是**没量就下结论**。实测：截图 1920 原生、
  > `max-width:780` 正常生效、书脊（y 523→880）比纸页（524→890）**短** 10px。
  > 结论方向没变（确实是扁），但依据必须是像素实测。教训与 1.2.x 那条同源：
  > **看不清就先量，别用假设补全观察**；写代码时的"应该没问题"不算证据。

- **时光日记页签未动**：它是"时间流便签"，与"书"是两种物件，本次不在需求内
- **验证（第一轮）**：pytest 363 全绿；hosted 链接门 23 模块 0 丢导出；按宿主同款选项跑
  tsc 类型检查 0 错（宿主 `check-hosted-tsx` 只认仓内路径，故用等价配置外跑）；
  `neko-plugin check` 0 错 0 警。**真机观感待验收**：楷体/仿宋在非中文 Windows
  回落 SimSun→serif；sticky 页脚若被祖先链某处的 overflow 打断会退化成
  "沉在纸堆末尾"（不影响可读，只影响常驻）

#### 1.3.0 第二轮：藏书阁——写满 52 页后，淘汰从"静默的丢"改成"静默的搬家"

DESIGN 定下的下轮任务并入本版本（1.3.0 尚未发行，同版续写）：

- **纯逻辑层**：`journal_write` 返回值三元组扩为四元组
  `(pages, page_no, tail, evicted)`——淘汰页随结果带出（时间正序、内容完整），
  不再是函数内部的静默丢弃；新增 `archive_brief(pages)` 纯函数（只显本数 +
  时段事实，不派生会随淘汰平移的序号，遵 1.3.0 显示层纪律）
- **存储层**：新键 `journal_archive@<角色>`（只追加、不入当前书 blob——写日记
  的热路径永远只重写活架那块，阁 blob 只在淘汰那一拍重写）；两条收集路径守住
  一切截断：工具路径（`tool_write_journal` 把 evicted 入阁）与保存路径
  （`_save_shard_journal` 兜住旧版磁盘残留/周记迁移超长）；阁内再满
  `_JOURNAL_ARCHIVE_MAX_PAGES = 104`（约四年周更体量）才从最旧一页真删；
  零淘汰的常态写**不多花一次存储写**（空 evicted 不碰键，测试锁死）；
  shard 加载回读、prune 角色清除一并清阁 key
- **倒计数不挪位**：入阁序时间正序存储、显示倒序（最新在前）——阁满裁旧时
  新侧的位置数不平移，与 1.3.0 "盒号漂移"自查同一条纪律；翻阅按 page_no
  定位（累计页码永不重编），brief 本数变化即视为缓存过期重拉
- **面板**：书架末尾多一根横放的小书摞（ArchiveStack：三块布面板微错位堆叠 +
  切口纸口线 + 右上朱色"藏"字小印（盖章必不正同款 -4°）+ 探出的丝带，
  坐同一根木隔板；纯 CSS，不标本数、不提 52 上限，悬停只给时段事实）；点开
  复用 JournalBook 只读翻阅（题签换口径"藏书阁·合订本"，页眉页码/日期/心情
  照常——都是存储里现成的事实）；切角色即作废重拉
- **入口**：新增 `get_journal_archive`（ui.action，只读）；dashboard 新增
  `journal_archive_brief`（极轻量，进 5s 轮询）；全量翻阅按需拉取不进轮询
- **i18n**：登记 en + zh-CN 各 +8 key（入口三键 + archiveBadge/archiveLoading/
  archiveSeal/archiveTip），其余 6 语种按仓惯例回落 defaultValue
- **调试入口 `debug_journal_fill`（应真机验收需求新增）**：`core/journal.py`
  新增 `fabricate_demo_pages` 纯函数（确定性假页：四栏小标题轮转验分节/首字下沉，
  affect 四档轮转验脊皮色，一半的页两段验纸堆叠，带 demo 标记）；入口把活架垫满
  后连翻 `pages` 页（默认 3、上限 8）**走生产 journal_write→evicted→
  _append_journal_archive 链路**——验的是真搬家不是假渲染；真实 journal@/阁键
  注入前整包备份到 `journal@<角色>|pre-debug`（连续注入不覆盖最早备份），
  `restore=true` 两键整包还原 + 清备份（debug_stats 同款纪律）；同档只动
  当前角色日记两键，周期/情绪/时光日记/统计一概不碰
#### 1.3.0 第三轮：真机验收反馈——藏书阁 403 根因与双通道、我的日记假卷宗

- **真机反馈「注入后点藏没有用」**（Steam 宿主实机，日志定位）：宿主侧
  `Hosted UI action rejected … reason=action_not_exposed` 连排 403。根因：面板
  action 授权 = 子进程 live actions 白名单 **∩** 宿主进程启动时扫描的静态
  entry_ids；覆盖导入只重启子进程（子进程 collect 日志里明明有
  get_journal_archive），宿主不重扫静态白名单，stop/start 与 refresh
  （返回 unchanged）都不刷新——新静态入口在整启宿主前不可达。实测排除：
  磁盘代码新、子进程新、surface/context/权限声明全部与 get_journal 同款。
  **插件侧防御**：面板拉取改 `get_journal(scope="archive")` 搭已白名单旧入口
  加参数（两通道同数据，测试钉死不串架），`get_journal_archive` 保留为规范
  入口；纪律入 DESIGN：面板新增拉取类需求优先给既有已暴露入口加参数，
  纯新入口只供 API/跨插件。另：拉取失败不再静默（openArchive 失败弹
  archiveLoadError toast），"点了没反应"这类哑弹面板一律就地消灭
- **`debug_review_fill`（应"缺我的日记的注入"）**：`core/review.py` 新增
  `fabricate_demo_reviews` 纯函数（假篇目结构对齐 review_record：成文日 14 天
  等距、span 区间、轮数印章、自主情绪计数，正文四模板冷暖轮换含争执/深夜
  作息等如实样例，带 demo 标记）；注入/还原与 debug_journal_fill 同款纪律
  （review@ 整包备份到 `review@<角色>|pre-debug`，restore 一键回滚，只动当前
  角色 review 一键，素材统计随篇目一并备份不受污染）
- **旧宿主布局实勘**：用户 Steam 版无 `.neko-plugin-installations`，插件住在
  `N.E.K.O\plugins\forever_companion`（代码数据同居旧布局），实测覆盖导入后
  `data/store.db` 存活；升级前自行备份重要记录的保守建议仍适旧宿主
- **验证（第三轮）**：pytest 374 全绿（+3：scope 双通道不串架/fabricate
  形状/我的日记注入还原）；hosted 链接门 23 模块 0 丢导出；ruff check 全绿；
  宿主 check-hosted-tsx 重跑 0 错
- **验证**：pytest 371 全绿（新增 test_journal_archive.py 8 篇：工具淘汰入阁/
  保存溢出入阁/零淘汰不写阁/阁满裁旧/brief 只显事实/载入回读/入口只读/
  debug_journal_fill 注入+还原全链路）；
  hosted 链接门 23 模块 0 丢导出；宿主仓内 `check-hosted-tsx`（含同款 tsc）
  0 错；`neko-plugin check` 0 错（唯一 warning 为寄放工作区无独立 git 仓，
  属实情非问题）；ruff check 全绿（ruff format 非本仓关卡，HEAD 自身即 36 件
  不合默认配置，不强刷免无关 diff）

#### 1.3.0 第四轮：真机反馈「日记一页大一小」——书宽被宿主 Card 缩成看文字脸色

- **真机现象**（两张对比截图，同角色同书不同页）：第 10 页（1 段短句）与
  第 11 页（2 段长句）并排一看，纸页一大一小、书脊左右错位。逐像素实测：
  页 10 全书盒 1157→~1690（宽 ≈533px），页 11 全书盒 1099→~1756（宽 ≈657px，
  顶满 --tmb-col 640）；左缘随居中漂移——宽度不是被设计定的，是被**该页最长
  正文行**定的
- **根因（宿主层布局陷阱，插件侧可修）**：宿主 ui-kit 的 Card 内容区是
  `.neko-card-body { display: grid }`，而 `.tmb-book` 写的是
  `max-width: 640px + margin: auto 居中`——**grid 子项一旦带 auto margin，
  默认 stretch 即退化为 shrink-to-fit**，书宽 = min(内容 max-content, 640)：
  短句页收缩、长句页顶满，同一本书翻页就变形（藏书阁/卷宗共用本类，一并受影响；
  这也解释了上一轮「全屏书扁」修正后小屏观感反复——纸高钉住了，纸宽一直在飘）
- **修法一行**：`.tmb-book` 补显式 `width: 100%`（对 grid area 取满、封顶
  max-width 640，auto margin 继续负责居中，窄窗照旧 media query 收缩）；
  全仓排查同模式，仅此一处受影响（`.tm-journal-toolbar` 的 shrink-to-fit
  是预期行为——内容紧贴居中的工具条，不装纸页）；约束注释就地落在 styles_book.ts
- **纪律**：宿主 Card body 是 grid——插件在 Card 直子层想要「固定宽度居中块」，
  必须 `width + max-width + margin:auto` 三件套齐写，只写 max-width 等于把
  宽度外包给了内容；面板几何异常先量截图盒线再读宿主 runtime.js 的容器声明，
  不在自家 CSS 里猜（本轮从截图到定位根因三步：量盒线 → 查 Card → 验 grid）
- **验证（第四轮）**：pytest 374 全绿（纯样式改动，无行为变化）；hosted 链接门
  23 模块 0 丢导出；宿主仓内 `check-hosted-tsx` 0 错；ruff check 全绿；
  **真机观感待验收**：两本书逐页宽度应不再随文字长短呼吸，翻页时书脊不横移

#### 1.3.0 第五轮：「我的日记」对齐个人日记完成度——档案室搬家、本卷依据、跨卷翻页、双门槛可见、六语 i18n 清账

- **档案室（review_archive@）——把自家纪律用回自己身上**：1.3.0 第二轮给个人日记
  立了「淘汰从静默的丢改成静默的搬家」，但「我的日记」攒满 52 卷仍是
  `append_review` 直接裁旧。本轮补齐：`append_review` 签名改为返回
  `(新篇表, 被淘汰卷)`（不兼容是故意的，防漏改静默不入阁），成文路径带出的
  淘汰卷搬进独立 key `review_archive@<角色>`（只追加、不入 review@ blob，
  上限 104 卷再满才真删——藏书阁同款三维度）；`_save_shard_review` 补溢出兜底
  （磁盘残留/调试注入超长一律搬家）；成文失败回滚三件套（篇目/素材/档案室快照
  一起退回，archive 写失败篇目已成不白写、下次淘汰整包重写自愈）；
  `clear_review` 连档案室一并清、`prune_lanlan` key 清单补 `review_archive@`
- **面板档案室**：档案架末尾加一只冷灰档案盒摞（复用 ArchiveStack，`file` 变体
  换配色、题印「档」、丝带换口取纸标签；brief 只显时段事实不标卷数——显示层
  纪律照抄藏书阁）；点开按需拉全量（`get_review(scope=archive)` 旧入口加参数，
  403 双通道防御直接复用；`get_review_archive` 规范入口保留 API/跨插件），
  拉取失败当场弹错不演「点了没反应」
- **素材快照进卷宗（「本卷依据」）**：过去成文后 stats 清零，模型写卷宗真正用过的
  证据（语气分布/心情均值/原话摘录）永久丢失，卷宗口径行只剩轮数/区间/自主情绪
  三个数。现在 `review_record` 把快照三栏随正文固化进篇目（tone 计数降序、
  mood_avg 同款 prompt 口径、quotes 至多 6 条截 40 字），卷宗页新增「本卷依据」
  附页栏（虚线压顶、等宽小字；语气词复用 panel.stats.tone.* 词表、原话 kind
  复用碎片 kind 词表）；旧篇目缺字段整栏自动隐藏（零迁移）
- **跨卷翻页**：ReviewBook 补 前一卷/后一卷 + `position / totalFiles`——过去抽
  一卷看完只能「放回架上再抽下一盒」，日记本早有连续翻阅；档案室模式同一套纸
  两处出身（题签换「档案室·旧卷宗」口径）
- **双门槛进度都看得见**：成文条件是「满 N 轮 或 满 M 天」先到先写，过去进度条
  只画轮数——天数到了突然动笔用户无从对照。`get_review.progress` 补 `days`
  （`elapsed_days` 纯函数，与 review_due 同口径），档案架加天数副行 +
  `due/due_reason` 到期的朱色小徽标「到时候了，会自己动笔」
- **debug_review_fill 升格验真链路**：注入上限 8→60，`entries=53+` 时假卷宗经
  真实落盘链溢出入档案室（与 debug_journal_fill「验的是真链路不是面板假渲染」
  同款）；备份/还原扩至 review@ + review_archive@ 两键整包；假篇目带快照三栏
  与冷暖/空 quotes 轮转，一次验齐卷宗页各栏有/缺两态
- **六语 i18n 清账（1.2~1.3 双语迭代欠的全账）**：ja/ko/pt/ru/es/zh-TW 各补齐
  137 缺键（capintro 全家 + 日记/卷宗阅读页 + 新入口）+ 本轮新增 ~26 键，
  八语 652 键全对齐；顺带清掉六语里 3 个 zh-CN 已废的死键（backToToc/pageShort/
  pieceShort）；get_review/clear_review 涉档案室的描述八语同步改写
- **验证（第五轮）**：pytest 全绿（新增 test_review_archive.py 14 篇：成文淘汰入阁/
  零淘汰不写阁/保存溢出兜底入阁/阁满裁旧/双通道不串架/清空与 prune 覆盖档案室/
  重开回读/快照三栏/天数进度维度/brief 只显事实/注入溢出+还原全链路；
  append_review 签名迁移钉死）；hosted 链接门 23 模块 0 丢导出；宿主仓内
  `check-hosted-tsx` 0 错；`neko-plugin check` 0 错（3 warning 均为部署副本
  实情）；ruff check 全绿；**真机待验收**：注入 55 卷 → 架末档案盒出现、
  点开只读翻阅可翻页、新卷宗「本卷依据」三栏齐、天数副行与到期徽标可见


#### 1.3.0 第六轮：精确性缺陷清账 + 三道"文档/代码同源"门（纯修复，无功能变化）

拉取远端后做的一轮全仓体检，修的都是"看起来对、其实是谎/其实是债"那一类，
并且**每一条复发路径都上了机器门**——上一轮之前这类账全靠人眼盯，
所以 0.6.8 改了默认值却没人回头同步 README，一挂就是 8 个版本：

- **README 配置表纠偏 + 补全**：`[mood].default_action_minutes` 文档写 `20`
  实际 `10`、`[emotion_sense].window_turns` 文档写 `3` 实际 `2`（都是 0.6.8
  改值后没回来同步）。顺带补上 **9 个从来没进过表的键**（`[tide]`
  `inject_interval_n`/`trigger_keywords`/`advance_days`/`forbidden_words`、
  `[mood]` `arousal_baseline`/`extreme_invite_threshold`/`extreme_invite_after_minutes`/
  `extreme_invite_cooldown_minutes`、`[emotion_sense]` `user_affect_weight`；其中 8 个在
  README 全文零提及，`advance_days` 只在上方的多角色说明里活著），小节标题也补齐
  `[review]`/`[stats]`/`[capabilities]`/`[emotion_sense]`。**表里不写词表条数**
  （一度写了"内置 17 词"，实际 16 项）——数量本身会变，改写成"内置屏蔽词表
  （可自由增删）"，与 DESIGN 1.3.0 "不显示会变的数"同源结论。
- **DESIGN.md 定时器口径自相矛盾**：`:32` 写"每 20 秒轮询"，`:248` 与代码
  `@timer_interval(seconds=10)` 都是 10 秒。同一份文档两个数。
- **`_slot_dormancy_hint` 四份复刻合一**：主类 + senses/panel/debug_entries
  各存一份逐字节相同的文案，注释理由是"避免循环导入"——**这个理由不成立**
  （`services/tone_slot.py` 不 import 任何 mixin，且三处 mixin 本来就 import
  该模块）。收进 `tone_slot.py` 紧贴 `diagnose_slot_dormancy`：reason 每多一个
  文案只多一份，分处两地必然漂移。`__init__.py` 侧用"导入即再导出"保
  `tm._slot_dormancy_hint` 锚点不动（测试零改动）。
  同批清掉 `_journal_entries`：主类那份是**定义了零调用的死代码**，活的那份在
  shards.py——纯函数按分层落到 `core/journal.py`（更名 `journal_entries`）。
- **日记邀请页码说谎**：`whisper.py` 邀请文案"这篇会写进你的第 N 页"用
  `len(shard.journal) + 1` 现算，而写入路径 `journal_write` 早就刻意改成
  "按上一页号递增而非列表长度"（注释写明是为了淘汰后编号连续）。1.3.0 把淘汰
  改成搬藏书阁之后两者正式分叉：书到第 60 页、架上剩 52 页时，邀请说"第 53 页"、
  她真写下去拿到第 61 页。修法不是只改文案，而是抽出 `next_page_no()` 做**唯一
  来源**，写入与邀请两边都走它——同一个概念两份算法正是这个 bug 的形状。
- **情绪分析 Origin 不再钉死端口**：`services/emotion_sense.py` 无条件发
  `Origin: http://127.0.0.1:48911`。查宿主 `_validate_local_mutation_request`
  确认它要求 **CSRF token ∧ Origin 同时成立**，允许集来自
  `AUTOSTART_ALLOWED_ORIGINS`（按 `MAIN_SERVER_PORT` 生成）∪ 请求自身
  `base_url`：自定义端口下写死的值精确匹配不上，只是靠"hostname 降级分支"
  勉强过关，而那条分支注释写明是给 Docker 端口映射用的、随时可能收紧，
  一旦收紧就是 403 → 语气感知静默休眠。改为注入 `api_base`（与 `_http` 拼
  URL 同一个已解析 base，复用 host_coord 已有的端口解析与缓存），Origin 恒等于
  实际请求 base。测试从"断字面量端口"升级为**断不变式** `Origin == api_base()`，
  另加自定义端口用例。
- **config.example.toml 补 `[stats]` / `[emotion_sense]` 整段**（后者 9 键从未
  出现在示例里）+ `[tide]` 三个漏项；文件头"全局共享"清单同步补齐。
  `forbidden_words` 保留短示例表（示例值允许与出厂默认不同）。
- **三道门（都做过反向对照，确认会红而不是恒绿）**：
  `test_config_docs_sync.py`——README 覆盖每个配置键 / 反引号字面量默认值必须
  等于 plugin.toml / config.example.toml 覆盖每个键且不含 `[plugin*]` 段；
  `test_design_numbers_sync.py`——DESIGN 的 timer 秒数对 `@timer_interval` AST、
  面板轮询秒数对 `ui/panel.tsx` 的 `setInterval` 毫秒值；
  `test_journal.py` 内静态门——tokenize 扫 `len(...) + 1` 与"页"同现的代码行
  （**只看代码 token**，否则自家 docstring 会误抢），钉死页码单一来源。
- **验证（第六轮）**：pytest 398 全绿（388 → +3 配置文档同源门 +2 文档数字同源门
  +4 页码单一来源 +1 Origin 不变式，新增 2 个测试文件）；ruff check 全绿；hosted 链接门 23 模块 0 丢导出；
  `neko-plugin check` 0 错（1 warning = 本轮未提交，提交即消）。本轮**不触碰
  `ui/` 与 i18n**（无新增用户可见串，八语 649 键维持对齐）。
  **遗留（记录不修，需要时再开轮）**：`config.example.toml` 与 README 的默认值
  仍有"示例有意不同"的空间（本轮只对 README 取值、对示例只查覆盖）；
  `debug_entries.py` 直接调 `self.store` 绕过 1.2.2 统一出口、以及
  `emotion_sense._load_core_config` 在 async 路径做阻塞 `read_text`（5s 缓存
  缓解）属另两类账，不在本轮范围。


### 1.3.0 第八轮：发版校验链打通（独立仓跑不到宿主门的问题根治）

**根因**：本仓是宿主仓的**同级**目录，而宿主工具链假设插件住在
`<host>/plugin/plugins/<id>/`，于是两类命令在独立仓里跑不了：

- `neko-plugin check forever_companion` 按 id 找不到目录（只能传路径）；
- `frontend/plugin-manager` 的 `check-hosted-tsx` 用 `realpathSync` 断言目标仍在
  宿主仓内（`assertPathInsideRepo`），**所以 junction/符号链接也过不了**，必须真实副本。
  第五轮说明里的「宿主仓内 check-hosted-tsx 0 错」一直靠手工复制，没有可复现入口。

**做法**：新增 `tools/release_gate.py`，把五道门一次跑完、任一红即非零退出：
pytest / ruff / hosted 链接门 / `neko-plugin check <路径>` / `check-hosted-tsx`。
最后一道由脚本自己管副本生命周期——复制到宿主仓 `plugin/plugins/
.forever_companion-gate-probe`（点前缀，与内置插件一眼区分）→ 跑门 →
**finally 无条件清理**：宿主 .gitignore 没有本插件条目，留残就是给人家仓库刷脏。
`--keep` 可保留副本便于反复迭代，`--only hosted,link` 跑子集，`--host-root`/
`$NEKO_HOST_ROOT` 指定宿主仓位置。

**两个 Windows 坑（都实测踩过）**：
① `npm` 在 Windows 上是 `npm.cmd`，`subprocess(shell=False)` 不走 PATHEXT，直接传
  `"npm"` 抛 WinError 2 —— 统一经 `shutil.which` 解析再执行；
② 控制台默认码面 GBK，summary 里一个 emoji 就能让**全绿的运行**以 traceback 收场、
  CI 判成门禁失败 —— 脚本顶部把 stdout/stderr 重设为 UTF-8 并容忍不可编码字符。

**`.vscode` 配置纠偏**：`nekoPlugin.repoRoot` 是脚手架按 `plugin/plugins/<id>` 深度
生成的 `../../..`，在独立仓布局下解析到 `D:\`（错三层）——同级布局应为 `../N.E.K.O`；
`python.analysis.extraPaths` 同步。tasks.json 全部改走路径形式，并新增 release gate
为默认构建任务（pytest/ruff/link 在插件目录跑，neko-plugin 系在宿主仓根跑，cwd 分开）。

**顺带核实**：`neko-plugin sync <路径>` 与 `build <路径>` 都接受路径形式（build 实测
产出成功，探测产物已删）。`check -r` 的发版路径因此同样可脚本化。

- **验证（第八轮）**：`tools/release_gate.py` 全链五门 OK、退出码 0、跑完宿主仓
  `git status` 干净无残留；pytest 406 全绿；ruff 全绿。本轮不改运行时代码。


### 1.3.0 第九轮：面板 i18n 契约——后端不再往用户可见渠道塞裸串

**根因**：前端 toast 一律 `String(r.note || t(...))`，后端 `note` 只要存在就永远
压过 `t()`；非中文用户看到的是后端硬编码中文。`SdkError` 的英文裸串同理经
`api.call` reject 直进 toast。`tr()` 延迟引用在动作返回值里**不会被宿主解析**
（`call_surface_action` 不走 `resolve_i18n_refs`），所以后端永远不可能"自己翻译"
——动作调用时后端根本不知道请求方的 UI locale（`_ctx` 里没有 locale）。

**新契约**：面板可达入口（`mixins/panel.py` / `capabilities.py` / `shards.py`）的
`"note"` 字面量与 `Err(SdkError(...))` 只准携带稳定 ASCII 码（`^[a-z][a-z0-9_]*$`），
动态细节进日志；数据类文案改发字段（`mode` / `reason` / `turns` / `min_turns` /
`dormant_reason`），前端按码分支翻译。豁免（有意保留）：`mood_actions.py` 的 note
是给模型的行为指令、`debug_entries.py` 是开发者面向调试入口，都不进面板通路。

- **八处中文 note 清账**：invite_journal 四态改纯 `invited/mode` 码（新增
  `panel.journal.invitedRespond/invitedQuiet` 两支按 `r.mode` 分支，不再共用一条
  "已递出"）；write_review_now 拒绝档改发 `reason` 码 + `turns/min_turns/dormant_reason`
  数据字段（数字进插值不进文案；slot 休眠复用 `onboarding.channels.noModel/freeRoute`
  零新增键）；lift_mood/set_mood 的 note 改码 `no_active_mood/mood_applied`
  （消费者是宿主 Agent 的模型，结构化字段仍在）。
- **34 个错误稳定码**：panel/capabilities/shards 全部英文 SdkError（gallery is full /
  item_id required / persist failed: <细节>…）与中文 prune 动态串改码，
  `ui/utils.ts` 新增 `errorText(err, t)`：`^[a-z][a-z0-9_]*$` 形态的 reject
  message 按 `panel.errors.<camelCase>` 翻译，非码文本（宿主自身错误/超时）原样直出；
  panel.tsx 17 处 catch 全部收编。`set_capability` 的 `persist_error` 字段同改码。
  timezone 非法从"failed to save settings: KeyError"误报里拆出独立码。
- **reset_all 脱离 ActionButton**：kit 的错误展示是内联裸串（会把稳定码直喷给用户），
  改受控 Button + confirmDialog + errorText；label/confirm 与后端 `@ui.action`
  的 `actions.reset.*` 同一对键，两端同源。
- **八语 649 → 756**：补齐 **69 个「TSX 引用但从未进 bundle」的旧债键**
  （panel.calendar.* / panel.ring.* / panel.messages.* / panel.errors.* 整族——
  第五轮"八语对齐"只对拍了 bundle 间键集，没对拍引用面，defaultValue 中文直喷
  非中文用户的其实是这批）；新增本轮 38 键（含 6 处按 1.md 的必新增 key）；
  清 1 死键（`errors.no_valid_fields`，后端不再经 `self.i18n.t` 消费）。
  日文字稿自查修一处混入的汉字（"で触发"→"で起動"）。
- **回归门 `tests/test_i18n_contract.py`（5 条，全部反向对照过）**：
  ① note/SdkError 字面量只准稳定码（塞中文/f-string/str(exc) 即红）；
  ② 每个发出的码在全部 8 locale 有 camelCase 键（白名单：`reverted_to_default` →
  既有 `panel.features.reverted`）；③ TSX/TS 字面量 `t("key")` 引用必须在八语齐全
  ——这条是"defaultValue 中文泄露"的根治；④ 八语键集完全一致；
  ⑤ 本轮淘汰的中文原句不得复活。
- **CJK 拼接键参数化**：面板里"第{n}天/{n}天后/周期第 N 天"这类逐词拼接在拉丁语系
  必然碎（词序与格变化不成立），ring/overview/statusbar/calendar/settings_cycle 的
  6 处拼接点改成带 `{n}` 插值的整句键（`panel.ring.dayN` / `inNDays` /
  `cycleDayN` / `untilTideN` / `panel.advancedN` / `panel.settings.tideNDays` 等）。
- **release_gate 码面隐患修复**：`_run` 的 `subprocess.run(text=True)` 默认按本地码页
  （Windows=GBK）解码子进程管道，子进程吐 UTF-8 中日韩字节即崩 reader 线程
  （本轮全链跑时实测 `UnicodeDecodeError 0x93`：判定靠 returncode 不受连累，
  但门日志整段丢失）；统一钉 `encoding="utf-8", errors="replace"`，与脚本自身 stdout 同口径。
- **验证（第九轮）**：pytest 411 全绿（406 + 5 条契约门）；ruff 全绿；
  五道回归门全部反向对照（塞中文/f-string 码/TSX 缺键/单语言缺键/旧句复活，逐条确认真会红）；
  hosted 链接门 / hosted-tsx / `neko-plugin check` 见 release_gate 全链。

### 1.3.0 第十轮：隐私与日志脱敏契约（问题清单 §2.2 清账）

- **根因/动机**：宿主规范（best-practices + 发布检查清单）要求"日志与进程输出均
  不包含原始对话、密钥或私有 payload"，但直连通道（语气感知/碎片提取/我的日记
  成文共用）的留痕形态有四处在界外：①失败日志记裸 `exc`——urllib 家族异常
  message 可能携带完整端点 URL，自定义服务商以 `?api-key=` 查询参数或
  `user:pass@` userinfo 形态带凭据时（宿主 logging_config REDACT 正则不覆盖
  连字符 `api-key` 与裸 `sk-` 形态，兜不住）key 原样落进插件日志文件；
  ②无可用内容日志记响应前 80 字符切片——网关错误页/回显型上游会把对话
  内容映进响应；③`fragment captured` 把用户原话 `quote=` 直写日志（对话原文
  进日志文件，最重的一处）；④成文失败日志带模型回复前 40 字符预览。
- **修法**：`services/tone_slot.py` 新增 `_exc_shape`（类型名 + 若有 `.code` 则
  状态码，永不 str(exc)）与 `_payload_shape`（顶层键名/choices 条数/字节长度，
  零值输出）两个脱敏 helper，两条 warning 收编；`mixins/senses.py` 碎片日志改
  `quote_len=`、成文失败改 `shape=` 形态分类（json-like/html-like/fenced-like/
  plain，"判断话风问题"的初衷保住）。诊断线索不降级：类型名/状态码/长度/键名
  都在，缺的只有内容本体——面板与日记页本来就有内容可看，日志只需定位。
- **回归门 `tests/test_privacy_hygiene.py`（7 条，全反向对照）**：两条行为门——
  假 urlopen 抛"message 里塞满 key/端点/prompt 哨兵串"的异常、返回"值里回显
  对话"的坏 JSON，断言 logger 全部输出零命中哨兵且诊断形态（类型名/code=/
  keys=/bytes=）在位；正路不回归（可用回复零 warning）。一条 AST 静态门——
  出货代码里 logger 数据实参禁直出 `api_key/prompt/quote/user_text/her_text/raw/
  core_cfg/resolved/text` 及其下标/切片/属性链（根是 Call 的 helper 包裹形态豁免：
  脱敏发生在被调用侧；`payload.get("<字面量>")` 信封字段豁免：那是 error 码位
  不是正文）；加已淘汰写法整文件文本钉死（含注释——历史说明不得残留可被
  复制粘贴复活的违规样例，本轮 senses/tone_slot 新注释因此用"前 80 字符切片"
  措辞绕开三串字面量本身）。反向对照直接喂违规源码字符串给扫描器，不碰真文件。
- **旧契约收编**：`test_save_chain_review.test_tone_direct_completion_logs_warnings`
  过去正向钉"坏响应留痕必须带 `upstream rejected` 预览"——那正是本轮禁掉的
  行为；改为断言结构摘要在位 + 响应值**不得**出现（旧断言原样保留即自相矛盾，
  属契约更迭非放宽）。
- **文档**：README 新增「隐私与数据边界」节——三条出域通道一张表（送什么/截断
  多少/送到哪/怎么关，截断数字对拍代码 500/300/120/6×60/8×80）、明文 key 的
  边界声明（只本地读、只进该服务商 Authorization 头、不进日志/面板回显）、
  日志脱敏摘要、全不出域的关法组合；修日志阅读指南三行（failed/no usable
  content / fragment captured / review compose failed 的新形态）；语气感知节
  交叉引用。DESIGN 新增「隐私与日志脱敏契约」节（凭据/异常/响应/内容四面 +
  回归门形态，长期有效）。
- **顺带核账（不动）**：`bus memory read failed: {exc}` 等传输层异常留痕不含
  对话内容（Result.error 是 SDK 信封）；`cycle.TideConfigError` 的 `{text!r}` 是
  配置解析入参（锚点日期）非对话——均界内，静态门规则 A 不报（根是嵌套 Call
  的名字不进数据实参射程）。JSONDecodeError 的 str() 只含位置不含文件内容
  （实测），core_config 读取失败的 debug 留痕不泄 key。
- **验证（第十轮）**：pytest 418 全绿（411 + 7 条隐私门）；ruff 全绿；
  静态门反向对照（api_key 直出/resolved["api_key"] 下标/user_text[:200] 切片/
  已禁字面量复活，逐条确认真会红）、豁免面反向不误报（_exc_shape/
  payload.get("error")/len(quote)/_slot_dormancy_hint(core_cfg…)）；
  hosted 链接门 / hosted-tsx / `neko-plugin check` 见 release_gate 全链。

### 1.3.0 第十一轮：我的日记成文反馈闭环（用户实测"写一篇等完什么都没有"根修）

**根因（2026-09-10 用户实机日志钉死）**：`panel.tsx` 队列成文完成反馈 effect 的
认领顺序错了——`!result return` 挡在"挂载/角色首帧认领"之前：挂载时
last_result 为空 → 认领永不发生；面板生命周期内**第一条**真结论到达时，
角色 ref 仍是初始 null → 被当成"挂载期旧值"认领吞掉。首次"立即写一篇"的
成败 toast 必消失（本次事故里成文因测试残留的坏槽位 401 失败，吞掉失败
toast + 列表无新篇 = 用户视角"石沉大海"；日志实测 22:56-22:58 四次点击、
两条 compose failed、面板零反馈）。1.2.4 修的"排队异常静默消失"是同族
另一半——结论位没写；这次是写了没人弹。

- **修法 1（panel.tsx）**：认领改看"角色首帧"不看"首条结果"——无结果帧也
  完成认领（seen 记 0），随后任何 ts>0 的结论都是新事照弹；删除
  `seen===0` 的旧吞币分支（ts 来自 time.time() 恒 >0，0 只会是哨兵）。
  成功/持久失败/其余失败三分支文案与键不动。
- **修法 2（diary.tsx 常驻兜底）**：toast 是瞬时通道，面板关着等写完再重开
  仍会错过（"旧结论不补弹"是 1.2.3 有意设计）。成文失败改加**内联警示行**：
  `last_result && !written && !writing` 时日记页写入口下方常驻一行
  "上一篇没写成，素材还留着，可以再点一次"（复用 tm-derived 样式，零新
  组件），下一次点击受理时后端作废结论位、警示行自动消失。新键
  `panel.review.lastFailed` 八语同步（756→757，译文人工撰写）。
- **验证（第十一轮）**：release_gate 五门全绿（pytest 418 同数——TSX 行为
  面无 py 门，hosted-tsx/链接门过编译与导出面；i18n 键集门确认八语 757 对齐）；
  README「立即写一篇」段同步反馈闭环描述。

### 1.3.0 第十二轮：日记书本扁平绘本化（浅色系可爱化，用户定方向的四问改造）

用户提出：日记页两本"拟真旧书"与面板/宿主的淡蓝白磨砂风脱节，要二次元、可爱、
浅色；封皮布纹/圆柱高光/线装孔"太写实"，同批优化。四问定案（浅色系化+克制萌点缀 /
粉 vs 蓝紫继续区分 / 保留暗色孪生 / 圆润字体 + 扁平绘本风封面），只动
`ui/styles_book.ts` 一个文件（+README/DESIGN 描述同步），diary.tsx 结构、数据层、
交互与 1.3.0 三条书本约束（显示层分节/sticky 祖先链无 overflow/CSS-only 图形）
一字未改：

- **封皮/书脊去写实**：布面交叉肌理、圆柱明暗渐变、线装三孔、深压影全部退场，
  换纯色马卡龙底 + 一处左上柔光 + 大圆角（12px 封面圆角、书脊 8px）；装订改一条
  白缝线（dashed border，同线迹两本共用）；题签从古籍长条签变圆角小贴纸（±1.2°
  微旋转贴歪感）
- **个人日记 → 奶油粉手账**：纸面 #fffafc→#fbe6ef 奶油浅樱渐变 + 极淡粉点阵
  （点纹手账纸，替换宣纸纤维四层）；封皮柔玫瑰 #f2aabd、丝带樱粉 #f48fb1、
  胶带角贴换马卡龙条纹（粉白 repeating-linear-gradient）、木隔板换奶白软垫板；
  正文换圆润无衬线字面栈（PingFang/YaHei/游ゴシック等），小标题/落笔时间/题签
  保留楷体手写感；首字下沉墨色改玫瑰
- **我的日记 → 浅蓝紫观测手账**：淡长春花蓝封皮 #aec0ea + 云白→淡蓝紫纸
  （浅蓝横格 + 更淡点阵）；"数据感"不靠冷和重、靠版式保留——等宽口径行、
  表格线、居中标题原样；朱红落款印褪成软靛 #7082c4 圆角章（旋转 -5.5°"盖章
  必不正"的仪式感保留）；铅字 white text-shadow 压痕退役；进度条圆角糖果化
- **书脊皮色 = 心情均值（moodDotColor）的机制不动**——那是好设计；档案盒脊
  由深灰 #7e8794 换淡长春花蓝；藏书阁/档案室 ArchiveStack 三板与印章同步换
  粉/蓝紫扁平孪生
- **暗色孪生翻面为雾感莫兰迪**：粉褪 dusty rose（纸 #2f2731 系）、蓝紫褪 slate
  lavender（纸 #262c3e 系），封皮/丝带/胶带/印章各给暗档值，两本冷暖相对关系不变
- **文档同步**：README「两本真书的手感」段改「扁平绘本手感的小书」措辞、
  「朱印落款」「灰色档案盒」×3 处换口径；DESIGN 桌面物件层描述与约束 ③ 用词同步
  （历史条目中的旧措辞不回写）

验证（第十二轮）：release_gate 五门全绿（pytest 418——样式为纯 CSS 字符串、
无行为面 py 门；hosted-tsx/链接门过编译与导出面；check 0 错）。

### 1.3.0 第十二轮b：粉味收淡 + 书脊与封皮外内色系修复（真机反馈）

第十二轮包实机后用户两点反馈：①粉味整体过重；②个人日记架上书脊"蓝色或褐色"、
翻开却是粉纸，外内两个色系观感突兀。②的根因不是渲染 bug，而是 1.3.0 的
"书脊皮色 = moodDotColor(心情均值)"——全站口径是**灰蓝(低落)/琥珀(开心)**，
和粉色封皮本不同族，拟真版靠棕褐封皮还能盖住，扁平粉本下彻底暴露：

- **外内一致（diary.tsx）**：`shelfInk` 换成本书粉紫系暖冷色阶——开心落樱粉
  (244,167,195)、低落落灰紫(179,158,189)、近零落奶粉(224,206,214)，|mood| 线性
  推进的机制与"暖=好/冷=坏"语义方向不变，只搬色相；页眉心情圆点同函数，架上/
  页内一个色系。`moodDotColor` 直用随之退出 diary.tsx（import 移除，状态栏/
  总览等全站圆点口径不动）
- **粉味降档（styles_book.ts，1.3.1b）**：封皮 #f2aabd→#f0c0cd（奶茶玫瑰）、
  丝带 #f48fb1→#eeb0c2、纸面三档整体提亮近白（#fdf5f8 系）、点纹透明度
  .11→.07、全部粉影 opacity 降 .05~.12、题签/页码/按钮/首字下沉的玫瑰墨色
  各褪一档；蓝紫侧同调——封皮 #aec0ea→#c3cdec（奶薰衣草）、格纹/墨色/落款印
  /进度条/到期胶囊同步；藏书阁与档案室板色褪淡
- **验证（第十二轮b）**：release_gate 五门全绿；重建导入包 round12b

### 1.3.0 第十二轮c：换配方——「白底彩点」（真机第二轮反馈：粉蓝两系仍齁）

用户第二轮反馈：1.3.1b 收淡后"味道还是浓，看着不舒服"。定量调查（HSL/色距对拍
宿主 UI Kit）钉死根因：**宿主的大面积永远近白无色（底 #f7f9fc 色距仅 5，卡片纯白
玻璃，彩色以 7~10% alpha 弥散），高饱和只住小件（主蓝按钮）；我们的书却是
"大面积彩面"配方——粉纸 S56~66%、粉皮 S61%、脊端 S77%，色相 H340 又与宿主
H216 冷白对撞。浓度调参救不了配方，1.3.1c 照宿主 DNA 重配**（用户选定
「白底彩点·对齐宿主」）：

- **大面全部褪到近白**（色距目标 ≤20）：个人日记纸改暖白三档
  （#fffefc→#faf5ef，只剩一丝米暖）、我的日记纸改冷白（#f3f5f9，与宿主底色
  同档）；封皮灰粉 #e9d5da / 灰蓝 #d8dfea；点纹纸纹、蓝紫格线、条纹胶带
  **全部撤除**（1.3.1b 已拉淡仍属"图案噪音"，本轮归零），素色半透明胶带贴角
  保留形状撤掉条纹
- **颜色只住小件**：书脊皮（shelfInk 重定档：开心端 #da9ab4/低落端 #9d96bd/
  中性 #d5ccd1，仍是全书最大的彩点但色距从 64~90 压到 39~64）、丝带 #e3b7c6、
  题签字、落款印、进度条填充、到期胶囊；正文墨色转中性（暖灰 #4d4448/冷灰蓝
  #4a5266），仅首字下沉留一丝玫瑰
- **浅脊配深字**：书脊皮褪淡后白字对比度不足，脊上日期/段数翻面成墨色 + 白描边
- **暗色孪生同步翻配方**："白"变深中性纸（暖炭 #262220 / 冷板岩 #1c1f27），
  封皮 #6b5c63/#555d6e，点缀色提一档保可读
- 造型叙事不动：书架/抽脊/翻页/缝线/丝带/贴纸胶带形/歪斜纸叠/落款印旋转全数
  保留——可爱感靠形不靠色
- **文档**：README 书本段、DESIGN 桌面物件层描述换「白底彩点」口径；
  styles_book.ts 文件头记全三轮配方沿革（8 度→6 度→换药方的账别再有人重走）
- **验证（第十二轮c）**：release_gate 五门全绿；重建导入包 round12c

### 1.3.0 第十三轮：我的日记卷宗阅读页——双栏档案袋 + 小字整体提档

用户实测反馈"翻开一卷感觉乱糟糟、排版不好"。显示层病灶诊断五条：①正文前堆了
页眉/居中标题/口径行/依据框四层公文，150~300 字的主体被压到首屏以下；②信息重复
（轮数全页 3 处、区间/日期各 2 处）；③层级倒置（标题 13px 比正文 14px 小，
楷/圆/等宽三字体混战）；④卷宗无丝带却共享 .tmb-page 的 62px 丝带车道→正文偏左，
朱印又 float 压进首段；⑤原话摘录 10.5px 等宽 + anywhere 断行的贴纸墙。
四版式静态预览（现状/A 极简/B 双栏/C 微调）对拍后**用户选 B 双栏档案袋**，
窄窗降级选"正文优先"：

- **ReviewBook 重构（diary.tsx）**：纸页内分两栏——左窄栏=成文日+徽号、三行卷首
  事实（区间/轮数/她自主起的情绪）、「本卷依据」（语气分布/心情走向/原话摘录，
  不折叠全量可见）、落款印住栏尾；右宽栏=居中标题+纯正文。旧页眉行与 tmb-dossier
  口径行退役（字段并入左栏、全页去重）；页脚删重复的「{n} 轮」；朱印从 float 改
  静态落款。**DOM 序正文在前、信息栏在后**：宽窗靠 grid 显式栏位拉回左列，
  窄窗（<460px）塌单列时天然就是「正文优先」，竖分隔线换顶部虚线。档案室旧卷宗
  同一组件两处同时生效
- **样式（styles_book.ts）**：新增 .tmb-file-cols/.tmb-file-main/.tmb-file-aside
  （左栏 190px）；依据块在左栏去大面底色、标签/值改上下行；卷宗页右内边距
  62→26px（丝带车道是她那本专用），页脚负外边距跟手；暗色孪生同步；
  tmb-dossier 基座与卷宗专属旧页眉规则随 JSX 退场删除（个人日记那本的
  tmb-head/tmb-entry/丝带/贴角零牵连）。i18n 零新键、数据层零触碰、
  书本三条长期约束（显示层分节/sticky 祖先链无 overflow/CSS-only）全守
- **字号两连提（真机反馈追加）**：13b"两本字号统一、整体偏小"——卷宗正文不再
  单独压 14/1.95，与个人日记完全同档 15px/2.08（温差只留暖冷墨色），左栏小字
  10.5→12~16px、素材进度条行 10.5→11.5、栏宽 178→190；13c"小字还是看不清"——
  最小地板从 9~10.5px 抬到 10.5~11.5px：卷宗事实行 13、依据/摘录 12.5、落款印
  12.5，并顺带个人日记页眉/落笔时间/分节标题、书脊页码/书根字/封面题签同向
  提档；正文 15px 与阅读区仍隔一档层级
- 文档：README 卷宗段两处旧口径（"卷首等宽小字列出…"/"等宽口径行"）换双栏口径；
  .gitignore 收编 .pytest-tmp/
- **验证（第十三轮）**：每子轮 release_gate/链接门+pytest+ruff 全绿（pytest 418、
  链接门 23 模块、hosted-tsx 过）；导入包 round13/13b/13c 三发，用户实机验收
  13c"整体没问题"后推送

### 1.3.0 第十四轮：书架书脊色阶统一——单色族明度阶（1.3.1d）

用户反馈封面"浅粉×浅灰不搭"，四案+现状静态对拍后澄清：两本封面与翻开页都
维持原判不用动，真正刺眼的是**个人日记书架上一排书脊不统一**。病根诊断：
1.3.1c 的 shelfInk 以暖灰 rgb(213,204,209) 为中点、向灰玫（H345）与雾紫
（H268）两端线性插值——摆幅近 77°，且冷端明显更暗更沉，相邻两本 mood_avg
一正一负时架上冷暖交错，读作"乱"不读作"梯度"。书架书脊色阶对拍页
（现状/R1 单色族明度阶/R2 窄幅双色/R3 统一脊皮+心情圆点）**用户选 R1**：

- **shelfInk 一处收编（diary.tsx）**：全书脊色相锁死在封皮同族粉（H≈340），
  心情只驱动浓淡——开心落鲜亮粉 rgb(240,182,203)、低落落深灰玫瑰
  rgb(197,164,178)、无数据/近零落雾粉 rgb(232,214,220)；|mood|/0.85 归一
  保持对拍预览的饱和点；目录行心情圆点同函数自动跟。浓=好/淡灰=坏的语义
  方向不变，情绪信息只降强度不消失
- **显示层零牵连**：CSS 回退色同步 rgb(232,214,220)（styles_book.ts
  .tmb-spine 注释与 fallback）；封面/丝带/落款印/藏书阁/档案室零改动；
  明暗共用一支色阶为本函数既有现状（暗色孪生块不覆写书脊底色），本轮不动
  架构；文件头配方沿革补 1.3.1d 一条并写明诊断口径——**统一感来自色相
  锁死，不来自降饱和**
- 文档：README 书本段书脊描述换明度阶口径；i18n 零新键、数据层零触碰、
  书本三条长期约束全守
- **验证（第十四轮）**：release_gate 五门全绿；导入包 round14

### 1.3.0 第十五轮：书架书脊精简——编号圆贴与书根小字退场（1.3.1e）

用户实机看过 round14 后反馈"整体色彩行了"，但架上"2、1、2、1"和顶上的编号多余，
我的日记档案架同款问题。三选一对拍（日期保留/纯色脊条/只去底部）后**用户选日期
保留**：脊面只留页口白条 + 竖排日期 + 旧版角签，被删信息全部收进悬停 tooltip
（shelfTip/fileTip 本就带页码、区间、段数、轮数、心情，无信息损失）。

- **JSX（diary.tsx）**：JournalShelf 删页码方块与书根段数两枚 span（含 spacer），
  ReviewShelf 删轮数方块与书根「轮」字两枚 span（含 spacer）——架上原有的
  "盒上不写会变的号"注释随退场改写（page_no 累计页码仍进 tooltip 与翻开页眉，
  显示层不说谎纪律不变）
- **CSS（styles_book.ts）**：.tmb-spine-no/.tmb-spine-foot/.tmb-spine-spacer 及
  --file 变体、暗色孪生共六条规则随 JSX 退场删除（第十三轮纪律）；
  .tmb-spine-date margin-top 7→9px（上面没了圆贴，与页口白条稍拉开）
- **i18n**：死键 `panel.review.turnsUnit` 八语同删 757→756（键集门要求八语一致，
  留死键须挂小留账，不如删净）；其余零新键
- README 书本段与调试段两处口径同步；数据层/后端零触碰
- **验证（第十五轮）**：release_gate 五门全绿；导入包 round15

### 1.3.0 第十六轮：面板开关统一动画化——TmSwitch 替换宿主 Kit 原生 checkbox（纯 ui 显示层+交互层）

用户反馈面板"按钮想整体改成动画开关"，范围对拍定为**只统一开关系**：一切
是/否语义的开关统一换成自绘动画开关；一次性动作按钮（保存/快进/写一篇/清除等）
维持按钮形态不改。风格对拍定案：经典顺滑型（无回弹）、开启态**浅蓝色系**
（与面板淡蓝磨砂底 rgba(147,197,253) 弥散光同族，非 iOS 绿）。

- **组件（ui/tmswitch.tsx 新增）**：隐藏原生 checkbox（保留键盘可达，
  focus-visible 环画在轨道上）+ 轨道/滑块两 span，滑块 0.25s
  cubic-bezier 平移、轨道同步渐变换色；small 档（34×19）供状态条/引导行窄位
- **乐观回滚契约**：onChange 可返回 Promise——resolve `false`（确认取消/调用
  失败/被否决回弹）时开关动画回落权威值；其余保持本地态待 checked prop 变化
  收编。这是把 README「开关拨不动会如实回弹」从文案变成行为的载体
- **替换面**：11 处 Kit Switch（settings_cycle/inject/mood/emotion/diary、
  manage 调试模式、features 能力行×9 与高级选项）+ 状态条「关闭模拟/开启模拟」
  按钮 + 引导卡「开启」按钮（后两处按钮文案退役为悬停 title，键引用面不丢）
- **panel.tsx 返回值收编**：onToggle/onToggleCap/onToggleHideTools 改为显式
  回布尔——reverted_to_default 与 catch 回 false（开关回弹），persist_error
  属"内存已生效"既定契约回 true 不回弹
- **CSS（styles.ts）**：.tm-sw 段 + 暗色孪生（板岩底/雾蓝渐变/暖白滑块）；
  Kit `.neko-checkbox` 在面板内自此零引用
- i18n 零新键（756×8 不变）、后端/数据层零触碰；onboarding 向导步骤按钮是
  流程动作非开关，有意保留按钮形态
- 踩坑登记：python `re.sub("<Switch\b")` 在 heredoc 里 `\b` 被 shell 层吃掉
  生成 `\x08` 退格符混进替换产物，hosted-tsx 门当场逮住（"Invalid character"）
  ——文本批量替换后必须过编译门，勿裸 grep 行数了就交付
- **16b（实机追加反馈）**：「开启态扩散色泽过浓」——外发光 `0 2px 8px/40%` 收为
  `0 1px 3px/16%`（只贴轨道描一圈、不向卡面洇光），渐变提亮降浓一档
  （#a5cdff/#7db4f5 → #aed2fd/#8cbef6）；暗色孪生与 hover 光圈同向收敛；
  调档沿革写进 .tm-sw--on 注释防后人回调时丢口径。包 round16b

### 1.3.0 第十七轮：调试入口补「清空个人日记」档（debug_journal_fill clear）

用户实机巡面板发现调试侧缺一个「清空个人日记」的入口点——盘点属实：时光日记有
clear_diary、我的日记有 clear_review（皆面板常驻危险区），个人日记却在面板无清空
入口、调试侧也只有注入/还原两档；清它是为了"验完书架/藏书阁回到干净状态再验
首写邀请"，走 reset_all 又会连周期/情绪一起清掉。

- **落位 debug_journal_fill 第三档 clear=true**（对齐 debug_stats 的
  seed/restore/clear 单入口三档纪律，不新增入口 id、注册表/注销链/面板零触碰）；
  restore/clear 互斥闸同 stats
- **与 stats 的 clear 有意不同档**：个人日记是她手写的真内容，清空前先走既有
  备份通道（pre_debug@journal@<角色>，首次才备、连清不覆盖最早备份），误清
  restore=true 原样找回；合订本一并清（写空列表 blob，clear_review 同口径）
- 备份状态 unreadable 时中止清空（与注入档共用"读失败即不动"纪律）
- 测试 +2：test_journal_archive 扩 clear→restore→互斥全矩阵（含两键盘面同步
  断言）；test_persist_errors 反向对照 clear 档 unreadable 中止（418→419）
- README 调试表藏书阁行补 clear 档口径；插件注册表/Store 布局/注入链路零触碰
- **验证（第十七轮）**：release_gate 五门全绿（pytest 419）；导入包 round17

### 1.3.0 第十八轮：清空个人日记提成独立调试入口 debug_journal_clear（实机反馈）

用户实机找「清除个人日记」没找到——十七轮把清空藏在 debug_journal_fill 的
clear 参数档里，入口列表看不见、不会读 description 的人等于没有。提成独立条目：

- **_DEBUG_ENTRIES 加一行 debug_journal_clear**（「调试：清空个人日记」，带可选
  lanlan）；handler 一行转发 `_debug_journal_fill(clear=True)`——逻辑零复刻，
  备份/中止纪律/互斥闸全复用；注册表驱动注销链/sync 零触碰
- fill 的 clear 档保留（同一契约两个门面），README 调试表藏书阁行改指独立入口
- 测试 +1：转发真走 clear 档断言（清后两键空、备份在位、经 fill restore 找回）
- **验证（第十八轮）**：release_gate 五门全绿（pytest 420）；导入包 round18
- **验证（第十六轮）**：release_gate 五门全绿（pytest 418/ruff/链接门 24 模块/
  check/hosted-tsx）；导入包 round16；16b 复跑五门全绿，包 round16b

---

### 1.3.1：新能力「生日轻语」——她记得主人的生日

用户诉求：插件能记住玩家生日，生日当天用轻语告诉猫娘，由她送上祝福。
四问定案：仅生日（不做可扩展日期系统）／当天首条消息时提醒（非零点主动、
非前一天预告）／轻语内容＝基本识别 + 当天写进她的日记作留念／面板呈现＝
总览「我的日记」旁加生日卡。

**数据层**（`core/birthday.py`，纯函数零 SDK 依赖）：
- 日期全局配置 `[birthday].date`（YYYY-MM-DD，或兼容 MM-DD）；**年份只用于
  合法性校验，从不参与年龄计算**——面板/存储/注入文案三处都绝不告诉她几岁
- `parse_birthday` 用真日历校验月日组合（2-30/13-01 当场拒），坏数据一律按
  未设置处理（fail-closed：宁可不提醒，不对着坏 date 提醒）
- `birthday_is_today` / `days_until_birthday`：闰日 02-29 平年按 02-28 观察
  （宁早勿漏），倒数因此永远 ≤365、不存在"等四年"读数
- 去重水位 `stats["birthday"]["last_pushed"]`（与 `stats["anniversary"]` 同构）；
  `mark_birthday_pushed` 返回新 dict、不原地改；`make_birthday_diary_record`
  产与 drift_bottle 同形的手记（`source=self` + `kind=birthday`，时间线零改动兼容）

**能力中心**：`CAPABILITY_SPECS` 登记 `birthday`（group=rhythm、
config=[birthday].enabled、llm=injection），`CAP_INTROS` 补介绍——「功能管理」
页开关、就绪清单、工具显隐全部自动接入。

**触发线**（`mixins/whisper._maybe_birthday_push`）：挂在 `_handle_new_user_message`
的静默情绪闸之后、`inject_mode` 频控之前——生日是一年一度的重要信号，不该被
每 N 条的节流挡掉，也不该在冷战/已读不回时硬塞；不受频控但**服从沉默闸**，
静默解除后当天仍能补递（水位以 push `submitted≠False` 为前提才盖，1.2.4 契约）。
`keep_diary` 开启时当天顺手在时光日记代笔一条纪念手记。

**面板契约**：入口复用 `update_settings`（不新建动作）——`_EDITABLE_SETTINGS` /
`_settings_snapshot` / input_schema 三处补 `birthday_date`/`birthday_enabled`/
`birthday_keep_diary`，写 settings 覆盖层 `[birthday]` 段；非法日期回稳定码
`invalid_birthday_date`（i18n 契约，细节进日志）；dashboard 下发 `birthday` 视图
（date/set/is_today/days_until/keep_diary，纯本地零 IO）。

**UI**：`overview.tsx` 三本日记速览格下加 `BirthdayCard`——未填引导、已填倒数、
当天暖色高亮；就地改日期用原生 date input + `TmSwitch`（当天留纪念手记），
保存走 update_settings 定向字段，空串清除。styles.ts 加生日卡样式（含暗色孪生）。

**i18n**：八语各 +28 键（capintro 12 + 生日卡/错误/字段/功能描述 16），zh-CN 的
介绍文案经脚本逐字回读 `core/intros.py` 事实源登记；ja/ko 译文人工清掉混入的
简中汉字（当天→当日、插件→플러그인 等，正则门复扫零残留）。

**文档**：README 加「生日轻语」小节 + 功能表/纪念日段/配置表三键/配置段标题；
DESIGN 加「生日轻语（1.3.1）」契约节 + state/config 段补 [birthday]；
plugin.toml/config.example.toml 版本号升 1.3.1、加 [birthday] 段、keywords 补生日。

- 当天卡片副文案实机反馈改口：「说不说、怎么庆祝，都是她的自由」→「祝主人生日快乐！」（todaySub 八语同改 + TSX defaultValue 同步，键数不变）
- **改版（用户实机反馈：移出总览页）**：总览 `BirthdayCard` 三态形态整套退役——生日卡从总览「近况与相处」下架，移入「时光」页最底部（相处徽章收藏墙之后）并**降级为纯设置项** `BirthdaySettingsCard`（文本框式日期按钮 + 自绘月历弹层 + 纪念手记开关 + 清除；无倒数、无当天高亮、无提示小字——一版 hint 行按用户反馈撤下；当天提醒由她的轻语口播承担）。
- **二次改版（用户反馈：原生日历里的「今天」按钮想换成「确认」）**：原生 `input type=date` 的月历弹层是浏览器内建 UI——文案与行为都改不了，整套退役，换成**自绘月历**（全 CSS 零 SVG，周一起、星期表复用「日历」页 `panel.calendar.wd1~7`，月份标题复用 `monthLabel`/`panel.stats.monthFormat`；« » 步进年、‹ › 步进月，未来日期不可选）。月历形态首版做成绝对定位浮层，被 Card 玻璃层（overflow/backdrop-filter）裁没（实机反馈 2026-09-14），同日改**内嵌展开**：点开时日历长在卡片正常流里、卡片随之变高，取消=关面板弃草稿）。交互口径：**「确认」=直接落盘**（旧「保存」按钮退役，仍走 update_settings 定向字段，不新建动作）；纪念手记开关在已设日期时即时保存（TmSwitch 乐观回滚），未设时存草稿随确认一并提交；「清除」只在已设日期时出现。i18n 随形状换键：退 save、增 pick/confirm/cancel（八语各 778→780；上一枚「增 1 键」累计口径变为净增 3：title/pick/confirm/cancel − save）。dashboard `birthday` 视图收窄为 date/set/keep_diary（`_birthday_view` 不再算日期差），`core/birthday.days_until_birthday` 无消费者随之下架（`_observed` 闰日折算仍归 `birthday_is_today` 用）；i18n 八语退 7 枚展示文案（countdown/notSet/today/todaySub/cta/edit/cancel，上一枚 todaySub 改口随之作废）、增 1 键（title；hint 随提示行撤下），八语各 784→778（其后二次改版再 −save +pick/confirm/cancel 至 780，见下条）。样式 `.tm-ov-birthday*` 整段换成 `.tm-bday*`（暗色孪生同收）；README/DESIGN/plugin.toml 入口口径同步。

**测试**（`tests/test_birthday.py`，15 篇）：纯函数逐门（含闰年回落、
水位、记录形制）+ 能力闸 + update_settings 往返/非法码/dashboard 视图
（改版后钉死视图键形制）+ whisper 桩直调（触发/休眠/清水位重试/keep_diary
开关/submitted=False 不盖水位）。能力计数断言 9→10。验证：release_gate 五门全绿。

**增补（1.3.1 修订轮·Agent 可见面收敛）——「44 个入口点要不要合并」的正确答案是隐藏优先、不合并**：

用户问"44 个入口点要不要优化减少"。AST 精确盘点：**32 个静态 @plugin_entry +
12 个 @llm_tool 后备动态入口 = 前端"入口点"计数 44**（此前各处 grep 的 34/30 把
docstring 提及计了重）。核对宿主源码后结论：数量本身宿主无硬限制、面板刷新 O(N)
可忽略，**合并是错的杠杆**——真问题有两个：①宿主 Agent 评估路由
（`brain/task_executor.py::_build_plugin_desc_lines`）把全部 Agent 可见入口逐条
塞进分析器 prompt，全插件总描述 >3000 token 触发 Stage 1（多一次 LLM 粗筛 +
BM25 top-10 截断），本插件 32 行是最大单块，挤占的是宿主里所有插件的分发精度；
②Agent 触发直连 IPC、不经面板 confirm——`reset_all`/`clear_diary`/`clear_review`/
`clear_stats`/`prune_lanlan`/`update_settings` 挂在可见面，聊天一句话就可能误触发
免确认破坏操作。另核出：未声明 `llm_result_fields` 的入口被 Agent 触发后只回
"执行完成"（宿主 `utils/result_parser.py` fallback），读态入口留可见面既无信息
增益纯是噪音（真要在聊天里读日记，模型有 mood_drift_bottle/recall_fragments）。

**修法**：按消费者面逐入口挂 `metadata={"agent_hidden": True}`（宿主判定
`_is_plugin_entry_agent_hidden`，agent_auto/agent_exposed/llm_exposed=False 同效，
本仓统一 agent_hidden 一种写法）。可见白名单 6 枚：`get_status`/`toggle`/
`set_mood`/`lift_mood`/`list_capabilities`/`set_capability`（状态查询、总开关、
情绪——第九轮账明载消费者是模型与命令面板、能力开关是真实聊天意图）；其余 26 枚
一律隐藏。`get_panel_gallery`/`get_gallery_image` 装饰器原有
`metadata={"result_kind": "event"}` 合并不覆盖。入口契约、ui.action、跨插件调用、
命令面板零改动——面板 5s 轮询与全部按钮原样工作。

**常驻门** `tests/test_agent_surface.py`（AST，2 篇）：门 1 白名单外每个
@plugin_entry 必须带字面量 agent_hidden=True（**新入口默认必须隐藏**）；门 2
白名单↔代码可见面互为充要（不留死名、不许白名单内自相矛盾、静态入口 id 不撞名）。
反向对照：摘 clear_diary 标记 → 门 1 红；给 get_status 补标记 → 门 2 红；还原全绿。

**留账**：`debug_*` 动态入口宿主 `register_dynamic_entry` 不透传 metadata，
debug_mode 开启期间仍对 Agent 可见——开发者态知情接受；要堵需宿主加参数
（escalation，见 DESIGN 新节）。

**文档**：DESIGN 加「Agent 可见面契约（1.3.1 修订轮，长期有效）」节；README
「平台机制与已知限制」补聊天可见面条目；版本号按 1.3.x 修订号归位维持 1.3.1
（本增补记入 1.3.1 节内，不另起版本）。i18n 零新键、ui/ 零触碰、数据层零触碰。
测试 435→437。验证：release_gate 五门全绿。

---

**增补（1.3.1 修订轮二 · 发版前审查修复）**：

对 1.3.0→1.3.1 全量 diff 做逐块审查，核出三处并修复：

- **B1（真 bug·UI）生日卡纪念手记开关的失败回滚失效**：`moment.tsx`
  `onToggleKeep` 在即时保存分支先 `setDraftKeep(v)` 再 `onSave`——TmSwitch
  乐观回滚契约是"失败翻回 checked"，而 checked 正是被提前写脏的草稿值，
  失败时翻回"已改未存"的假状态；5s 轮询因服务端值没变（deps 不变）不会
  纠正，假状态挂到用户点确认补存或重开面板。修法：即时保存分支只在
  ok 后动草稿值（成功时 refresh 收编在前、就地同步做双保险）；未设日期
  的草稿分支纯本地，保持提前写。DESIGN 生日节同步记录该契约。
- **B2（真 bug·文本）whisper.py 重复注释行**：生日轻语接线时"静默类情绪
  动作期间不注入身体轻语"注释写了两遍，删一行。
- **D1（文档口径）生日轻语的链路前提从未写明**：触发挂在
  `_handle_new_user_message`，上游有 tick 的 fail-closed 全局拦截
  （`[tide].enabled` ∧ 各角色总开关都关着时整条注入链路不跑）与
  `_enabled(msg_shard)` 逐角色闸——只开 `[birthday]` 不开潮汐模拟的
  新装用户会永远等不到轻语，而 README「真机验证」与功能介绍都没提。
  纪念日轻语同病（1.1.0 起一贯如此），本轮按家族口径明载：README 生日节
  补「链路前提」条 + 「平台机制与已知限制」补"一切轻语都挂在潮汐模拟
  开关上"总条、plugin.toml/config.example.toml `[birthday]` 段注释镜像、
  DESIGN 生日节补同条、capintro 生日 limits 增补第 5 条（八语 +limit5，
  zh-CN 与 `core/intros.py` 事实源逐字同）。功能页四道闸本就会标
  "总开关未开"，故只修口径不动链路（生日挪链路会破掉"消息驱动、当天
  首聊"的设计语义，得不偿失）。

**核对过不是问题的**：`PushMessageResult` 实为 TypedDict（运行时真 dict），
`submitted is False` 水位保护在生产路径有效；`invalid_birthday_date` 走
`codeToCamel` 通用映射有八语译文；`update_settings` 早退非原子
（cycle 字段先落盘再参数拒收）是六枚稳定码共有的既有模式且面板从不混发
字段，不构成回归；debug_\* 动态入口 Agent 可见泄漏已在修订轮留账（待宿主）。
测试 437 不变（B1 属 hosted-tsx 编译面、B2/D1 属注释文档面）；验证：
release_gate 五门全绿。

---

**增补（1.3.1 修订轮三 · 新手向导扩到六步：记生日 + 看壁纸）**：

用户点名：给新手引导加"设置生日"和"壁纸设置"的引导。1.3.1 的生日能力默认开
但入口藏得深（「时光」页底部），壁纸/图库同理（「设置」页「面板外观」卡）——
都指望用户自己发现不如引导里走一遍。

**修法**：向导四步 → **六步**，`0 认识 → 1 开启节律 → 2 通道体检 → 3 记住生日
→ 4 她的房间（壁纸）→ 5 收尾`。第 4 步把「时光」页的生日设置卡**原地内嵌**进
向导：先抽 `ui/birthdaycard.tsx`（组件与注释整体迁出 moment.tsx，B1 修复随行），
时光页与向导 import 同一份，保存仍走 `update_settings` 定向字段——两处行为
永远一致，向导不复刻第二套日期逻辑。第 5 步只做引导与跳转（说明 + "去设置页
看看"按钮 → `closeWizard("done") + setTab("settings")`，真实外观卡原地可用），
不在向导里复刻图库/上传。向导仍然不改任何配置：生日页是用户主动填、壁纸页
只是领路；wel 页"接下来两页"改去计数化措辞（八语同改）。

**改版重弹（核心契约变更）**：`_GUIDE_VERSION` "1"→"2"，`wizard_pending` 从
"wizard=='' 才弹"扩为"done/skip 但版本不符也再弹"——老用户（含升级实机）会
再见到一次六步向导，走完盖新版本号收工；坏记录/无版本记录 fail-open 到
"多看一次向导"侧（与 norm 既有哲学同）。reopen 语义不变。

**i18n**：新增 9 键 ×8 语（onboarding.bday.lead/privacy/done/later +
onboarding.room.lead/what/privacy/hint/btn），改 1 键（wel.note 去"两页"计数），
每语 780→790；TSX defaultValue 与 zh-CN 逐字对齐（自造一次性对拍查出）。
DESIGN 页签清单行同步（birthdaycard 登记 + 六步与重弹契约一句）；README
快速开始改六步口径 + 老用户重弹说明。

**测试**：`test_onboarding.py` 改 `test_wizard_pending`（版本相符才 False）+
新增 `test_wizard_pending_regates_on_guide_version_bump`（旧版 done/skip、无版本
记录重弹；走完新向导盖章后不再弹）。其余门零触碰。验证：release_gate 五门全绿。
