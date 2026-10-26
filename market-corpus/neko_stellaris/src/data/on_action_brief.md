# Stellaris Cygnus v4.5.2 on_action 挂钩简报

版本：`launcher-settings.json` → `"Cygnus v4.5.2 (9776)"`。
**路径纠正**：任务书写的 `common\on_action\` 不存在，真实目录是复数 `common\on_actions\`（3 文件，489 个 on_action）。

## 一、`log` 效果：确认语法
- 语法 `log = "字符串"`（effects.log:3902-3903，"Prints a message to game.log"，`Supported Scopes: all`）。
- **没有** `log = <变量>` 形式：全库 111 处 `log =` 全是带引号字符串。
- **支持数据引用，但会静默失败**：`events/astral_planes_events.txt:231` 实测 ✅ 输出真名；`common/scripted_effects/00_scripted_effects.txt:6256` 实测 ❌ 三个引用全塌成空串，输出 `AI  in  defaulted as a result of `——不报错、不留占位符。
- **故 `recommended_log_text` 一律用 `NEKO|事件名|键=值`**：静态键在前，名字只当装饰，解析不依赖名字。另 `00_scripted_effects.txt:19` 的 `\\[This.GetName]` 是转义写法（打印字面量），别照抄。

## 二、`log` 输出格式（外部程序必读）
- 行格式：`[04:15:13][effect_impl.cpp:22217]: [2368.1.1] Log effect, <定位段>. <正文>`
- 定位段有事件内 / scripted_effect 内两种形态，用 `line:\s*\d+\.\s(.*)$` 可统一切出正文；更稳的是搜 `NEKO|` 哨兵取到行尾。
- **`game.log` 是 UTF-8 无 BOM，正文含中文国名/星系名**；按 GBK 或系统 locale 读会把名字读坏。

## 三、追加机制：能追加，但本体举不出例子
- Stellaris 是 **MERGE 追加**：自己文件里重复顶层键名即可 → `on_war_beginning = { events = { neko_bridge.1 } }`。
- 任务书的 `on_action = { on_actions = { ... } }` 是 **HOI4 语法**，Stellaris 全库 0 命中，写了不生效。
- ⚠️ **诚实说明**：本体 489 个顶层名 **0 重名**，所以"追加"无法用原版文件举证；证据只有官方 wiki 的 aaa/bbb 合并实例与社区 Modding 页结论，**属文档证据而非文件证据**。
- 本体可证的是自定义 on_action：`fire_on_action = { on_action = <名> }`（effects.log:2317；实例 `events/biogenesis_events.txt:77`）。

## 四、玩家过滤：`is_player` 根本不存在
- ❌ `is_player` / `is_human` 在 4.5.2 **都不是触发器**：triggers.log 无此项，全库 events/ 与 common/ **0 命中**。任务书示例 `is_player = yes` 写进去无法识别。
- ✅ 正确写法 **`is_ai = no`**（triggers.log:760，`Supported Scopes: country`；原版 events/ 用了 478 次）。
- ⚠️ **on_action 块没有 `limit` 字段**。过滤只能写进自己 hidden_event 的 `trigger = {}`（依据 `99_README_ON_ACTIONS.txt` 第 6-8 行：引擎按 trigger 排除事件）。
- ⚠️ 作用域决定写法：`this` 是 fleet/planet/ship/leader 时要 `owner = { is_ai = no }`；`on_rebels_take_planet` 的 `this` 是叛军国（永远 AI），必须写 `from = { owner = { is_ai = no } }`。

## 五、可靠 vs 不可靠
**最可靠、噪声最低的 5 个**（低频 + 作用域干净 + 原版占用少）：
1. `on_starbase_occupied:1531` — 空块、`this` 即国家、零副作用
2. `on_megastructure_built:3656` — 低频，`Root`=国家、`From`=巨构名齐全
3. `on_zone_complete:3034` — 4.5 新系统，原版只占 1 个事件
4. `on_post_communications_established_always_fire:2284` — 通讯建立全覆盖（含 no_message）
5. `on_join_federation:3504` / `on_leave_federation:3518` — 极低频、双方名字都在

**高频噪声源，慎挂**：`on_tech_increased:2684`、`on_building_complete:2953`、`on_district_complete:3022`、`on_ship_destroyed_victim:1392`、`on_survey_planet:1131`、`on_planet_surveyed:1205` 是"脉搏型"，AI 帝国全程刷，不过滤会瞬间写爆 `game.log`。

## 六、踩到的坑
1. `Scope = Carrier` 不是笔误：`carrier` 是真实作用域（scopes.log:398，"from a colony to its carrier"，Output Scope = planet），events/ 下有 1715 个 `carrier_event`；但 scopes.log 的作用域总表没列它，极易误判。
2. 官方 wiki 那页最后校对版本是 **3.7**，已过时：wiki 说 `on_megastructure_build_start` 是 `From = Megastructure`，本体注释写的是 **`From = System`**。以文件为准。
3. 多个 on_action 含 `random_events` 抽签（`on_survey_planet:1180` 抽先驱者、`on_arch_site_finished:4322`）。桥接**必须只追加 `events`**，塞进 `random_events` 会漏事件。
4. 作用域键不统一：`on_megastructure_built` 用 `Root` 取国家（不是 `This`）；`on_ruler_set:2483` 没有 `From=Leader`，要 `This.Ruler`；`on_colony_ownerless:3354` 的 `From` 明确失效。
5. `on_colony_destroyed:1339` 的 owner 即将被清空，取 `owner` 要放在 `immediate` 最前面；同一事件常为双方各触发一次（`on_status_quo`、通讯建立类），外部程序需去重。

## 七、做不到的事件（11 项 not_found）
- **经济赤字 defaulted**：无 on_action。原版逻辑在 scripted effect（`00_scripted_effects.txt:6256`，由 `situation_deficit_events.txt` 调用），只能覆盖脚本效果。
- **天灾降临**：无任何 crisis 钩子；且 triggers.log 里没有 `is_crisis`，连"是不是天灾"都难判。
- **游戏结束/胜利**：无 on_action。玩家失败只能用 `on_country_destroyed:3603` 近似。
- **联邦成立**：无 `on_federation_formed`，只能由 `on_join_federation` 判断"首个成员"。
- **通用特殊项目完成**：无通用钩子，须按类型分别挂 6 个子钩子。
- 其余是命名不符：真名是 `on_megastructure_built` / `on_building_complete` / `on_tech_increased` / `on_country_attacked`。
