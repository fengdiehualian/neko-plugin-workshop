# 媒体生成器（media_generator）使用说明

让 YUI 生成图片和视频。

## 工具

| 工具 | 功能 | 支持的 provider |
|---|---|---|
| `generate_image` | 文生图 | dashscope（通义万相）/ openai（OpenAI 兼容） |
| `generate_video` | 文生视频 | doubao（火山方舟 Seedance）/ kling（可灵） |

## 配置 API Key（环境变量）

**文生图：**
- `DASHSCOPE_API_KEY` — 通义万相（阿里云百炼，国内直连）
- `OPENAI_API_KEY` — OpenAI 或兼容端点（可选 `OPENAI_BASE_URL` 指向任意兼容服务，如硅基流动 / 智谱等；可选 `OPENAI_IMAGE_MODEL` 指定模型，默认 `dall-e-3`）

**文生视频（任选其一）：**
- 豆包 Seedance：`DOUBAO_ARK_API_KEY` + `DOUBAO_ARK_ENDPOINT`（火山方舟的模型 endpoint 或 model id）；可选 `DOUBAO_ARK_REGION`（默认 cn-beijing）
- 可灵：`KLING_API_KEY` + `KLING_SECRET_KEY`

自动选择逻辑：优先 dashscope（图）/ doubao（视频），未配置则尝试 openai / kling。

## 示例对话

- “帮我画一张赛博朋克风格的城市夜景，紫色霓虹灯” → 触发 `generate_image`
- “生成一段 5 秒的猫在窗台晒太阳的视频” → 触发 `generate_video`

## 注意

- 视频生成较慢（约 1–4 分钟），期间请耐心等待
- API Key 仅在环境变量中配置，不会在对话中出现
- 未配置 Key 时工具会明确提示缺少哪个环境变量
