# 统一帮助系统 · 游戏侧配置契约

插件提供**一套**帮助系统：渲染、配色、排版、图标全部由插件端解决，
**游戏只需要提供数据**（`data/config/<game>/help.json`）。

- 视觉：官方 UI Kit（token 与组件类名逐字取自
  `frontend/plugin-manager/src/components/plugin/hosted/ui-kit/styles.css`）
- 出图：复用**宿主内置浏览器**（宿主自己的 Playwright + Launcher 注入的
  `PLAYWRIGHT_BROWSERS_PATH`；失败才退到显式路径与系统浏览器）
- 分层：`游戏 → 大类(父分组) → 子分组 → 指令`，每层都能被直接寻址
- **指令可以有自己的图**：`「我的纳戒」`这类查看指令挂上 `blocks`，
  命中时出一张专属图（背包格/装备栏），而不是分组页里的一行说明
- 目录页**按比例自适应**：分组少时单列大卡，分组多时两列；分页按预估高度打包
- 兼容：不写 `groups` 的游戏行为与升级前完全一致（老的扁平单页帮助）

## 0. 渲染桥接（游戏侧 API）

**游戏不参与渲染**——不写 HTML/CSS、不碰 PIL、不碰浏览器、不管主题配色。
插件主体把渲染能力以"**数据进、图片出**"的方式桥接给游戏：

```python
class MyGame(GameAdapter):
    async def handle_action(self, user_id, cmd, args=None):
        # ① 本游戏的帮助图(数据来自 data/config/<id>/help.json, 支持功能主题)
        pages = await self.render_help(topic="纳戒")        # List[bytes]

        # ② 任意页面: 只描述要展示什么, 版式由插件决定
        png = await self.render_page(
            "今日战绩", subtitle="第 3 天",
            blocks=[{"type": "table", "title": "冷却", "rows": [["打劫", "CD 60s"]]}],
            commands=[["签到", "每日签到"]], chips=["连击", "满勤"], tip="加油喵")

        # ③ 渲染 + 推送一条龙(连推送都不用管)
        await self.send_page("图鉴", blocks=[{"type": "bag", "cols": 8, "cells": 16}])

        return {"message": "…", "outcome": "done"}
```

| 接口 | 入参 | 出参 | 说明 |
|---|---|---|---|
| `render_help(topic="")` | 主题词（可省） | `List[bytes]` | 本游戏帮助图，多页时逐页返回 |
| `render_page(...)` | 标题/副标题/版式块/表格/指令/胶囊/提示 | `bytes \| None` | 自定义页面 |
| `send_page(...)` | 同上 + `text` | `{ok, pages, summary}` | 渲染并推送 |
| `render_card(...)` | 标题/条目 | `bytes \| None` | 结果卡片（历史接口，仍可用） |

约定与降级：
- **渲染不可用时返回空**（`[]` / `None` / `{"ok": False}`），游戏自行降级为文本，不会抛异常
- `render_html(html, css, ...)` **已废弃**（仅兼容历史游戏）：游戏不得自带样式，
  需要新视觉请提需求给插件侧，由主题统一解决
- 主题（light/dark）由插件主配置决定，游戏侧不感知

**硬约束：游戏侧不得自己渲染图片。** 不允许 `import PIL`、不允许起浏览器、
不允许自带 HTML/CSS 模板——这类代码会被测试挡下（`test_games_do_not_render_images_themselves`）。
需要一张图时，把内容描述成**版式块数据**交给桥接即可。
`remake` 原来用 400 行 PIL 画人生总结图，已按这条规则改成
`cards(天赋) + stats(属性进度条) + table(人生轨迹)` 数据 → 插件渲染，
游戏侧的 `drawer.py`、图片素材、像素字体一并删除。

---

## 1. 最小可用（零改动）

```jsonc
// data/config/<game>/help.json
{
  "text": "🎣 钓鱼：每日抛竿，收藏稀有鱼获，鱼市换鱼蛋！",
  "commands": [["钓鱼", "抛竿钓鱼"], ["鱼缸", "查看收藏的鱼"]]
}
```

这就是全部要求：老格式照旧出图（扁平单页，自动分页）。**10 个既有游戏不动也能用。**

## 2. 升级为功能分组（推荐）

```jsonc
{
  "text": "...", "commands": [["仍然保留", "面板 get_game_config 会读它"]],
  "title": "诸天修仙",                       // 可省, 默认用游戏名
  "subtitle": "猫娘陪你修仙",
  "theme": "inherit",                        // inherit(跟随插件主题) | light | dark
  "banner": "auto",                          // auto | none | 图片相对路径
  "groups": [
    {
      "id": "bag",                           // 稳定 id（缓存键/日志用）
      "name": "装备与道具",                   // 显示名
      "icon": "bag",                         // 语义图标名（见第 4 节）
      "aliases": ["纳戒", "背包", "储物"],     // 用户可能怎么称呼它
      "blocks": [                            // 可选：分组页顶部的可视化块
        {"type": "slots", "title": "装备栏", "items": [
          {"icon": "sword", "name": "武器", "sub": "剑 / 刀 / 枪"}]},
        {"type": "bag", "title": "纳戒背包", "cols": 8, "cells": 16,
         "filled": [0, 3], "icons": ["pill", "", "", "gem"]}
      ],
      "commands": [
        ["我的纳戒", "查看背包"],                        // 老写法
        {"cmd": "装备 X", "desc": "穿戴装备",            // 对象写法(可选字段)
         "aliases": ["穿戴", "穿上"], "kind": "action",
         "params": ["装备名"], "related": ["卸下 X", "我的装备"]}
      ]
    }
  ]
}
```

## 3. 用户在聊天里怎么说

| 用户输入 | 命中 | 渲染 |
|---|---|---|
| 修仙帮助 / 怎么玩 | 游戏 | **目录页**（大类卡片 + 子分组 + 条数） |
| 修仙帮助 装备道具 | 父分组 | **大类页**（含各子分组区块） |
| 修仙帮助 纳戒背包 / 战斗 | 子分组（含别名） | **子分组页**（版式块 + 指令表） |
| 修仙帮助 我的纳戒 | 指令（带专属图） | **专属图**（背包格 + 用法） |
| 修仙帮助 装备 X | 带参数指令 | 指令页（用法 / 参数 / 同组其他指令） |
| 都没命中 | — | 目录页 + "你是想看 XX 吗？" |

寻址优先级：**精确指令 > 精确分组(父/子) > 分组名整词包含 > 指令包含 > 分组包含**。
帮助意图由插件统一拦截（`帮助/攻略/玩法/怎么玩/说明/指令表/help`），**游戏无需实现**。

> 写数据时的注意：**父分组的别名不要和子分组重名**（比如父组别名写「纳戒」，
> 子组又叫「纳戒背包」，用户说「纳戒」就只会命中父组）。测试里有防重校验。

## 4. 语义图标名

游戏写**语义**，主题决定画成什么（emoji / 主题图标集）：

```
bag sword sect pet furnace coin meditate daily heart star flame
weapon armor trinket herb pill book gem misc help
```

未收录的名字会退化成 🎯，不影响出图。

## 5. 版式块

`blocks` 可以挂在**分组**（分组页顶部）或**指令**（该指令的专属图）上：

| type | 用途 | 关键字段 |
|---|---|---|
| `chips` / `list` | 名称胶囊（默认） | `items`（字符串或 `{name}`）, `limit` |
| `table` | 表格（冷却/条件/兑换/轨迹） | `title`, `headers`（默认 `["指令","说明"]`）, `rows: [[左, 右], ...]` |
| `cards` | 卡片组（天赋/成就/道具） | `title`, `cols`（1–4）, `items: [{icon, name, desc}]` |
| `stats` | 进度条行（属性/总评/熟练度） | `title`, `items: [{icon, label, value, max, note}]` |
| `steps` / `flow` | 阶梯 / 流程链（→） | `title`, `items: [{icon, name, sub}]` |
| `slots` | 槽位示意（装备栏） | `title`, `items: [{icon, name, sub}]` |
| `bag` | 格子（背包/仓库） | `title`, `cols`, `cells`, `filled: [下标]`, `icons: [语义名]` |
| `text` | 说明段 | `text` |

指令级示例（查看类指令出自己的图）：

```jsonc
{"cmd": "我的纳戒", "desc": "查看背包", "kind": "view",
 "aliases": ["背包", "我的背包"],
 "blocks": [{"type": "bag", "title": "纳戒背包", "cols": 8, "cells": 16,
             "filled": [0, 3], "icons": ["herb", "", "", "pill"]}]}
```

## 5.1 两级分组（指令多的游戏）

```jsonc
{"groups": [
  {"id": "gear", "name": "装备道具", "icon": "bag", "aliases": ["装备", "道具"],
   "groups": [                                  // 子分组: 各自挂指令与版式块
     {"id": "gear-bag", "name": "纳戒背包", "icon": "bag", "aliases": ["纳戒", "背包"],
      "commands": [ /* … */ ]},
     {"id": "gear-slot", "name": "装备栏", "icon": "weapon",
      "commands": [ /* … */ ]}
   ]}
]}
```

目录页会显示大类卡（含子分组胶囊与总条数）；进大类页时子分组各自成区块。

## 6. 指令对象字段

| 字段 | 说明 |
|---|---|
| `cmd` | 指令原文（含 `X`/`N` 占位符照旧） |
| `desc` | 一句话说明 |
| `aliases` | 别名（用户可能怎么打） |
| `kind` | `view`（只读面板类）/ `action`（带参数动作）；省略则按有无占位符自动判定 |
| `params` | 参数名列表（指令页「用法/参数」显示） |
| `related` | 关联指令（指令页「同一分类的其他指令」） |
| `blocks` | **该指令的专属版式块**：命中时先出这张专属图（背包格/装备栏…） |

## 7. 结构由游戏决定（插件不擅自改）

**游戏写什么结构，插件就渲染什么结构**：

| 游戏怎么写的 | 插件怎么渲染 |
|---|---|
| 写了 `groups`（一层或两级） | 分组帮助图：总图 = 分组卡 + 代表指令；分组页 = 版式块 + 指令表 |
| 只写 `commands` | 平铺单页（老的扁平帮助，行为与升级前完全一致） |
| 写 `"auto_groups": true` | 游戏**主动请求**插件代劳分组（可选项，不是默认） |

`auto_groups` 的语义是"我懒得写分组，插件帮我分一下"：插件按
`GROUP_RULES`（关键词 → 语义图标 + 分组名）自动聚簇，指令名优先于说明匹配，
并把指令名叫「帮助」的条目跳过（它由插件统一拦截）。

分组没写 `icon` 时，插件会按分组名/指令名**自动推断一个语义图标**，
所以游戏可以只写 `name + commands`，不必操心图标。

当前 10 个游戏的结构（都在各自的 `help.json` 里声明）：

| 游戏 | 结构 |
|---|---|
| xiuxian | 11 个功能分组（修行之路/装备与道具/战斗挑战/宗门/仙宠/生活职业/市集经济…） |
| fishing | 4 组（钓鱼玩法/鱼缸收藏/鱼市与商店/鱼竿与鱼饵） |
| cat_evolution | 4 组（养成起步/探索冒险/猫猫面板/任务与商店） |
| russian | 4 组（对局/每日/我的/排行榜） |
| remake | 4 组（重开人生/选择阶段/我的记录/会话） |
| neko_photo | 3 组（看图/图库与图鉴/会话） |
| soupbubble | 3 组（开始推理/对局状态/会话） |
| history | 2 组（历史漫游/会话） |
| tarot | 2 组（占卜/主题设置） |
| coinflip | 平铺（2 条指令，不需要分组） |

## 8. 主题

- 默认跟随插件主配置：`data/main/config.json` → `{"help": {"theme": "light" | "dark"}}`
- 单游戏可覆盖：`help.json` 的 `theme` 字段
- 素材（猫娘头像 / `yui竖` 背景 / 立绘 / 徽章）全部来自插件自身 `assets/`、
  `static/img/`，以 data URI 内联进图，**离线可用且随插件目录迁移不丢图**
- 渲染结果按 HTML 哈希缓存在插件私有缓存目录，同内容不重复起浏览器

## 9. 猫娘图库（喵图相册）

图库是**用户数据**，位置与打包策略如下：

| 项 | 位置 / 策略 | 说明 |
|---|---|---|
| 图库根目录 | `games/neko_photo/data/<分类>/` | 便于用户直接丢图、建分类 |
| git | **不跟踪该目录** | `.gitignore` 已排除 → 打包发布默认是**空图库** |
| 上传/自建 | 落在同一目录 | 同样不进 git（`git status` 里看不到） |
| 只读安装位置 | 退化到宿主私有存储 | 插件目录不可写时仍能上传/发图 |
| 空库兜底 | 插件自带 `assets/icon.png` | 图库为空也照样出图，不会"指令不出图" |

**发图频率**：由猫娘（LLM）自己判断——想发就发，插件只兜上限：

| 配置键 | 默认 | 作用 |
|---|---|---|
| `send_min_interval` | 90 | 两次发图最短间隔（秒），0 = 不限 |
| `send_max_per_hour` | 6 | 每小时上限 |
| `max_photos_per_day` | 50 | 每日上限 |
| `auto_send_enabled` | false | 定时刷图开关（默认关，频率交给 LLM） |

被闸门拦下时，`send_photo` 工具返回 `ok:false + limited:true + retry_after`，
LLM 看到就知道"刚发过"，不会连续刷图。
