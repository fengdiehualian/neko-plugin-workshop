# 学车助手（ai_learns_to_drive）

给《AI Learns To Drive》用的 N.E.K.O. 陪玩插件。它不注入游戏进程，只读写游戏自己的存档：

- `%APPDATA%\AI Learns To Drive\settings\local.settings`
- `%APPDATA%\AI Learns To Drive\evolution\*.evolution`

两个文件都是 **gzip + JSON**（已从游戏程序集和真实存档确认）。

## 能做什么

| 入口 | 用途 |
|---|---|
| `ald_status` | 游戏是否在跑、最新基因组、代数与最好成绩 |
| `ald_settings` | `get` 查看 / `set` 修改 / `preset` 套预设 / `recommend` 看建议 |
| `ald_genomes` | `list` 列表 / `inspect` 详情 / `trend` 训练曲线 / `mutate` 生成变异副本 |
| `ald_autopilot` | `start` 后监听游戏退出，读取训练曲线并自动调参 |

可调字段：`MutationRate`、`SelectionSize`、`UnitsCount`、`WeightDecay`、`TimeLimit`、`PreserveParentsSize`、算法编号、奖励 `Rewards`、`MaxPopulation2` 等。白名单之外的字段一律拒绝。

## 安全边界

- 每次写盘前自动生成 `*.bak-时间戳` 备份。
- 游戏运行时默认拒绝写设置（游戏退出时会用内存里的设置覆盖文件）。
- `allow_write = false` 可一键关掉所有修改。
- 自动巡航默认关闭，需要在 `config/plugin.toml` 里手动打开。

## 安装

1. N.E.K.O. 插件管理器 → 开发模式 → 加载未打包插件。
2. 选择本目录（目录名必须等于插件 ID `ai_learns_to_drive`）。
3. 校验 → 加载 → 启动 → 在入口点里触发 `ald_status`。

如果宿主要求源码树路径，把本目录放到 N.E.K.O 的 `plugin/plugins/ai_learns_to_drive` 即可。

## 已知限制

- 不模拟点击，不会替你点「开始进化」；它管的是两次运行之间的参数与存档。
- `SelectionAlgorithm` / `CrossoverAlgorithm` 等枚举以游戏内的整数编号暴露，未做中文映射。
- 没有做交叉（crossover）算子；`mutate` 只做权重/偏置扰动。

## 自检

```powershell
python ald.py
```

会用一个临时目录验证读写、备份、校验、建议和变异逻辑。
