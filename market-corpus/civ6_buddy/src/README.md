# 文明6陪玩（civ6_buddy）

让 YUI 陪玩家打文明6：深度监控（解析 .Civ6Save 存档）获知当前局面，据此给出陪玩建议；
进阶版可运营托管（处理弹窗、点下一回合），底层通道（FireTuner）自动装好后可
真实读取城市数据并执行官方市民焦点分配。

## 能力

| 工具 | 作用 |
|---|---|
| `civ6_list_saves` | 列出本机全部文明6存档（最新在前，含自动/手动标记） |
| `civ6_situation` | 自动读最新存档 → 局面摘要（回合/速度/地图/你的文明/对手/城邦/MOD/陪玩提示） |
| `civ6_parse_save` | 按名字/关键字解析指定存档 → 完整局面 |
| `civ6_civ_profile` | 查询文明/领袖特性（中英均可，如"刚果"/"Kongo"） |
| `civ6_advise` | 基于局面给分主题建议（开局/科技/建城/战争/胜利/外交） |
| `civ6_ops_takeover` | 进入运营托管：运营归 YUI（弹窗/下一回合/精细运营），打仗归你 |
| `civ6_ops_tick` | 执行一回合运营：Tuner 战争检测（官方 API）→ 清弹窗 → 点下一回合 → 验证回合推进 |
| `civ6_ops_handoff` | 交还运营控制权 |
| `civ6_ops_citizens` | 精细运营：读真实城市数据 → 按目标（balanced/growth/production）执行官方焦点分配 |
| `civ6_ops_focus` | 直接给全城设产出焦点（FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT） |
| `civ6_tuner_status` | 探测 FireTuner 底层通道（4318）是否可用 |
| `civ6_city_details` | FireTuner 读全部城市实时人口与六项产出 |
| `civ6_ensure_sdk` | **自动处理前置链路**：检测/触发 Steam 安装 SDK（appid 597260）+ 自动启用 Tuner 配置 + 探测 4318 |

## 玩法

1. 玩家打开文明6开始一局（产生 AutoSave 自动存档）；
2. 对 YUI 说"看看我这局什么情况" → `civ6_situation` 读出局面；
3. 说"这局怎么玩/科技怎么点/我该不该开战" → `civ6_advise` 给建议；
4. 想了解某文明 → `civ6_civ_profile`；
5. 想托管运营 → `civ6_ops_takeover`，之后每回合 `civ6_ops_tick`；
6. 想精细运营（市民/地块分配）→ `civ6_ops_citizens`（需先 `civ6_ensure_sdk` 装好底层通道）。

## 底层通道（FireTuner）一键就绪

FireTuner 是游戏调试端口（默认 4318），可读真实城市数据、执行官方焦点分配。
需要：Steam 装免费工具 SDK（appid 597260）+ 游戏启用 Tuner + 重启游戏。

- `civ6_ensure_sdk(action='check')`：汇总链路状态与缺口；
- `civ6_ensure_sdk(action='install')`：自动触发 Steam 下载 SDK（玩家在 Steam 弹窗点确认）；
- `civ6_ensure_sdk(action='fix_tuner')`：自动写 `AppOptions.txt` 启用 Tuner（带备份）。

## 技术说明

- 存档解析：插件内嵌 [pydt/civ6-save-parser](https://github.com/pydt/civ6-save-parser)（MIT），
  通过 `civ6_save_parser/wrapper.js` 以 JSON 输出，Python 侧整理为中文局面。
- 依赖：本机 node >= 16（`node -v` 可查）；精细运营额外需要 numpy + Steam SDK。
- 只读：不修改存档、不写任何游戏文件（Tuner 焦点分配为游戏官方 API 操作）。
- 存档目录默认 `Documents\My Games\Sid Meier's Civilization VI\Saves`，
  可在运行配置 `[civ6_buddy] saves_dir` 覆盖。

## 版本

- v0.1.0：首版。5 个工具，局面解析 + 规则化建议模板 + 文明知识库（40+ 文明/领袖中英映射、40 城邦）。
- v0.2.0：屏幕自动化运营托管（处理弹窗/点下一回合/战争横幅检测交还），复用 steam_manager 键鼠。
- v0.3.0：FireTuner 底层通道（读城市数据/官方焦点分配），AppOptions EnableTuner 自动配置。
- v0.4.0：civ6_ensure_sdk 自动装 SDK + 自动修 Tuner 配置 + 4318 探测；战争检测改 Tuner 官方 API；
  精细运营真实执行（读数据→按目标设焦点）。
- v0.4.1：复查修复——tick 内 await 替换 asyncio.run（修 N.E.K.O 事件循环崩溃）；SDK 配置写入去重、
  [Debug] 节去重；战争 Lua 加 pcall 容错；测试补异常路径。
- v0.4.2：SDK 目录名宽松匹配（实测下载为 "Sid Meier's Civilization VI SDK Assets" 变体）；
  本机 Tuner 配置已实际写入（AppOptions EnableTuner 1）。
- v0.4.3：建议逻辑重构为独立模块 civ6_advice（可测）；未知建议主题回退"综合"缺陷修复；
  Tuner Lua 全部 pcall 加固（API 差异不挂死）；城市数据字段强类型化；新增真实存档集成测试（共 41 例）。
