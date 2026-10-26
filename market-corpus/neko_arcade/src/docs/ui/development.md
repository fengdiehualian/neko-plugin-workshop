# 🛠️ 开发文档（简版）

> 本页是**开发者接入指引**的简版，供面板快速查阅。
> 完整文档（契约 / 接口 / 配置规范 / 示例）在 GitHub 仓库：
> **`docs/development.md`**（开发文档）与 **`docs/rules.md`**（接入规则）。

## 三步接入一个小游戏

```bash
games/
└── my_game/
    ├── __init__.py      # 声明: game_class = "MyGame"
    └── game.py          # 实现 GameAdapter
```

1. **建包**：`games/my_game/` 放 `__init__.py` + `game.py`
2. **实现**：继承 `GameAdapter`，写 `handle_action(user_id, cmd, args)` 返回
   `{facts, outcome, message, images?}`
3. **配置**：`data/config/my_game/` 放 `config.json` / `help.json` / `keywords.json`

> ✅ **零登记**：插件自动发现游戏包，无需改主插件或文档，面板自动出现。
> ⚠️ 命令必须**游戏专属前缀**（防跨游戏串台），详见完整版文档。

## 帮助与图片：只写数据，不写样式

```jsonc
// data/config/my_game/help.json —— 结构自己定
{ "commands": [["开始", "开始一局"]],            // 只写这个 = 平铺单页
  "groups": [                                   // 写这个 = 功能分组(可两级)
    { "id": "a", "name": "开始游戏", "icon": "star", "aliases": ["开始","开局"],
      "commands": [{"cmd": "我的面板", "desc": "看状态", "kind": "view",
                    "blocks": [{"type": "bag", "title": "背包", "cols": 8, "cells": 16}]}] }
  ]}
```

```python
# 游戏里要一张图时——只给数据, 渲染交给插件
png = await self.render_page("今日战绩", subtitle="第3天",
        blocks=[{"type": "stats", "title": "属性",
                 "items": [{"label": "颜值", "value": 8, "max": 10}]}])
await self.send_page("图鉴", blocks=[{"type": "bag", "cols": 8, "cells": 16}])
```

> ⛔ **游戏不参与渲染**：不要 `import PIL`、不要起浏览器、不要写 HTML/CSS。
> 版式块：`chips` / `table` / `cards` / `stats` / `steps`·`flow` / `slots` / `bag` / `text`。
> 完整契约见 `docs/help-config.md`。
