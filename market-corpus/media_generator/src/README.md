# 媒体生成器（media_generator）

让 YUI 直接生成图片与视频的 N.E.K.O 插件。对话中说"帮我生成一张图 / 做个视频"，YUI 即调用对应生成服务并把结果以 URL 返回给你。

## 功能

| 工具 | 说明 | 服务商 |
|---|---|---|
| `generate_image` | 文生图 | 通义万相（DashScope）/ OpenAI 兼容 API |
| `generate_video` | 文生视频 | 火山方舟 Seedance（豆包）/ 可灵（Kling） |

- 图片默认 1024×1024，支持自定义尺寸
- 视频 5 秒或 10 秒，按服务商支持范围自动处理
- 未配置任何 Key 时给出明确提示，不会报错崩溃

## 安装

1. 打开 N.E.K.O 客户端 → 插件中心
2. 搜索「媒体生成器」，点击安装
3. 配置下方任一服务商的 API Key（环境变量，见下）

## 配置（环境变量）

| 服务 | 环境变量 |
|---|---|
| 通义万相（推荐） | `DASHSCOPE_API_KEY` |
| OpenAI 兼容 | `OPENAI_API_KEY` |
| 火山方舟 Seedance | `DOUBAO_ARK_API_KEY` + `DOUBAO_ARK_ENDPOINT` |
| 可灵 Kling | `KLING_API_KEY` + `KLING_SECRET_KEY` |

> Key 只在环境变量中读取，对话中不暴露密钥；生成结果以 URL 返回。

## 使用示例

- 用户：帮我生成一张赛博朋克风格的城市夜景图
- YUI：调用 `generate_image(prompt=...)` → 返回图片 URL

- 用户：做一个 10 秒的太空飞船穿梭视频
- YUI：调用 `generate_video(prompt=..., duration=10)` → 返回视频 URL

## 安全说明

- API Key 只通过环境变量读取，绝不出现在对话与日志中
- 服务商请求超时兜底，网络异常返回明确错误
- 插件不保存你的图片/视频内容，仅中转服务商返回的 URL

## 开发与构建

```bash
# 测试
python -m pytest plugin/plugins/media_generator/tests/ -q
# 发布前检查
python -m plugin.neko_plugin_cli.cli check -r media_generator
```
