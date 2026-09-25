# mood_state — 心情精力状态插件 (N.E.K.O Plugin)


本插件为猫娘添加了简单的精力和心情状态，通过将不同挡位的提示词注入短期记忆实现。

## 功能

- **双维度状态**：心情 `mood` + 精力 `energy`，各 0~100 分，写在 `data/state.json`
- **10 档提示词**：每 10 分一档，从 `prompts/state_prompts.json` 选取档位文案（可自由改文案，别动结构）
- **静默注入上下文**：状态块通过 `push_message(ai_behavior="read", visibility=[])` 注入——AI 读得到，聊天窗看不到，但是也许你翻看记忆页面看得到
- **LLM 自评自调**：
  - `get_my_current_state` 工具：查询当前状态与档位文案
  - `update_my_state` 工具：对话中发生影响情绪的事时自行增减分数
- **手动调节**：`adjust_state` 入口（ ±1/±5 按钮）
- **手动调节小 GUI**：插件启动自动弹出小小的GUI（`gui_port` 默认 48930），
  N.E.K.O 蓝白圆角风格，两个 270° 仪表盘（红→黄→绿），±1 按钮、Shift 点击 ±5，
  启用开关，2 秒轮询刷新；纯标准库实现（http.server + 单文件 HTML）
- **启发式评估**：`evaluate_now` 入口基于最近对话的关键词规则打分（正面/负面/兴奋/长消息）
- **状态漂移**：定时任务按 `drift_per_hour` 向基线回归，并周期性重注入，防止状态被上下文遗忘


## 配置（`config.example.toml` → 用户数据目录 `config/plugin.toml`）

```toml
[mood_state]
enabled = true              # 总开关
initial_mood = 50           # 首次使用的初始值
initial_energy = 50
baseline_mood = 50          # 漂移基线
baseline_energy = 50
drift_per_hour = 2.0        # 每小时向基线回归的分数，0=关闭
push_refresh_minutes = 30   # 状态块重注入上下文的最小间隔
push_on_change = true       # 状态变化后立即重注入
```

## 开发

歪比。

## 发布

歪比。
