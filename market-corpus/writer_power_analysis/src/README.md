# 文本分析插件

基于 AI 技术的专业文本分析平台，为创作者提供多维度写作评估与深度洞察。系统通过多种 AI 模型与可切换的评分视角，从写作风格、内容质量、语言表达、叙事结构等维度进行全面分析，输出量化评分、雷达图可视化报告和可操作的改进建议。

```参考仓库
https://github.com/ave-mygo/ink-battles
```

插件运行时会按后端逻辑拼接三个分片，并用评分模式说明替换 `{{MODE_INSTRUCTION}}`。保留的核心能力：

- `analyze_text`：输入作品文本，调用模型并返回结构化评分报告。
- `list_models`：从 `base_url + model_list_path` 获取可用模型列表。
- `status`：查看当前配置状态，不会回显 API key。

## 提示词与评分模式

完整提示词文本:

- `prompts/system/system-prompt-01.md`
- `prompts/system/system-prompt-02.md`
- `prompts/system/system-prompt-03.md`

`analyze_text` 使用完整后端提示词，不再使用精简本地提示词。`mode` 支持中文模式名，也支持常见英文别名：

- `standard` / `标准模式`
- `beginner` / `初窥门径`
- `strict` / `严苛编辑`
- `reader` / `宽容读者`
- `judge` / `文本法官`
- `fan` / `热血粉丝`
- `anti-modern` / `反现代主义者`
- `quick` / `速写视角`
- `fragment` / `碎片主义护法`

多个模式可以用逗号分隔，例如：`"严苛编辑,碎片主义护法"`。

完整后端提示词要求模型输出 16 个维度，如果模型没有显式返回 `overallScore`，会按“基础维度分数之和 × 经典性 × 新锐性”计算综合战力分。

## 配置

在 `plugin.toml` 的 `[writer_power_analysis]` 里配置，或直接通过前端面板填写：

```toml
base_url = "https://api.openai.com"
api_key = ""
model_list_path = "/v1/models"
default_model = "gpt-4.1-mini"
```

也可以在调用 `analyze_text` 或 `list_models` 时临时传入 `api_key`、`base_url`、`model` 覆盖配置。插件会同时发送 `Authorization: Bearer <key>` 和 `x-api-key: <key>`。

Gemini 模型会走 `/v1beta/models/{model}:generateContent`，其他模型走 OpenAI 兼容 `/v1/chat/completions`。

## 调用示例

```json
{
  "plugin_id": "writer_power_analysis",
  "entry_id": "analyze_text",
  "args": {
    "article_text": "这里粘贴要分析的作品正文",
    "mode": "standard",
    "model": "gpt-4.1-mini",
    "api_key": "只建议临时传入，不要提交到仓库"
  }
}
```

返回结果包含：

- `analysis`：结构化评分对象。
- `report_markdown`：适合直接展示或让 N.E.K.O 转述的中文报告。
- `model` / `endpoint`：实际使用的模型和接口地址。
