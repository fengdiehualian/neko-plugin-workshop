# 自拍插件

根据固定角色参考图调用图生图服务，生成一张自拍并通过 `ctx.images.upload()` 和
`push_message(visibility=["chat"], ai_behavior="blind")` 提交到聊天框。
图片以带插件来源标签的系统消息显示，不额外触发角色回复。

## 配置

在插件管理器的配置页面填写 `selfie` 设置。插件默认启动，但不会在未配置时请求生图。

- `provider`：`volcengine`（火山引擎方舟）或 `chat`（聊天生图兼容接口）。
- `base_url`：HTTPS 基础地址或完整请求端点。
- `model`、`api_key`：图生图模型 ID / 方舟推理接入点 ID 和对应密钥。
- `reference_image`：角色参考图的本地路径或公开 HTTPS 图片 URL；相对路径以本插件目录为基准。也支持图片 data URI。参考图是必填项，不会退化成纯文生图。
- `size`：方舟图像尺寸，默认 `2K`；聊天协议不发送此参数。
- `timeout_seconds`：参考图读取、生成、下载、上传与推送的总期限，默认 120 秒，范围 5–240 秒。
- `cooldown_seconds`：两次生图请求之间的间隔，默认 60 秒；已请求生图但失败也会消耗冷却时间。
- `target_lanlan`：目标角色名。配置后图片定向推送，原生 AI 工具也仅对该角色开放；留空使用宿主默认路由，并向所有角色开放工具。多角色使用时请明确配置。
- `locale`：插件提示及配置后原生工具描述的语言，支持项目全部八种 locale。

### 火山引擎

```toml
[selfie]
provider = "volcengine"
base_url = "https://ark.cn-beijing.volces.com/api/v3"
model = "your-image-to-image-model-or-endpoint-id"
api_key = ""
reference_image = "reference.png"
size = "2K"
locale = "zh-CN"
```

填入自己的密钥和图生图模型。请求发送至 `/images/generations`，包含参考图、提示词，
请求 `b64_json`，也兼容返回图片 URL。保留服务商生成水印。
模型能力和支持尺寸以[火山引擎官方图片生成 API 文档](https://docs.volcengine.com/docs/ark/image-generation-api?lang=en)为准。

### v1/chat 聊天生图

```toml
[selfie]
provider = "chat"
base_url = "https://your-provider.example/v1"
model = "your-chat-image-to-image-model"
api_key = ""
reference_image = "reference.png"
locale = "zh-CN"
```

`/v1` 基础地址会补全为 `/v1/chat/completions`。也可直接填写完整
`/v1/chat/completions` 或 `/v1/chat` 端点。使用非流式多模态 `messages`，
将参考图放入 `image_url`，从 `choices[0].message` 提取生成图片：
结构化 `images` / `content` 图片字段、Markdown 图片、内联 Base64 data URI 或带图片扩展名的 HTTPS 链接。
普通聊天文本、没有图片的响应会作为失败处理；服务商必须实际支持此聊天图生图协议。

## 调用

原生 LLM 工具为 `send_selfie`，调用参数如下：

```json
{"scene": "花园里拿着手机自拍，微笑，穿着浅色外套"}
```

工具说明允许用户请求自拍时调用，也允许角色在对话中主动决定分享自拍时调用。
自拍仅由对话模型的原生工具链调用。插件不再向独立用户插件 Agent 暴露同名普通入口，避免同一句请求被两条链路重复执行。
是否调用由现有 AI 工具选择机制决定，本插件没有定时自动生成任务。
切换语言或目标角色后会重新注册工具。

提示词要求保持参考角色的脸、发型与风格，并加入调用时提供的场景。
只生成并发送第一张图片；同一时刻只允许一次生成，不自动重试付费请求。
成功结果的 `submitted=true` 仅表示宿主 SDK 接受了本地提交，不是浏览器显示确认。
生成失败或图片提交失败时返回明确错误，不声称已发送。

图片上传使用宿主的临时内存存储，服务重启或缓存淘汰后历史图片链接可能失效。
下载只接受 HTTPS 公开地址，不携带生图 API 的授权头，并限制重定向、响应和图片大小。
本地参考图在后台线程读取，模型响应、密钥、原始提示词和图片内容不会由插件写入日志。

## 验证

```powershell
uv run --no-sync pytest plugin/plugins/selfie/tests -q
uv run --no-sync python -m plugin.neko_plugin_cli build selfie --keep-staging
```

本插件复用宿主已有的 httpx 与 Pillow，不新增生产依赖。仓库包含官方生成的 IDE 配置和 GitHub 工作流，可执行严格发布检查。

测试使用模拟 HTTP 和宿主，不调用真实生图接口或产生生图费用。
源码修改后需重新构建，并将 staging 中的 `plugin.meta.json` 复制回本目录。

## 独立仓库

本仓库是 N.E.K.O. 的插件，运行时使用宿主的插件 SDK、httpx 和 Pillow，不是独立应用。
从 GitHub 克隆到 N.E.K.O. 项目的 `plugin/plugins/selfie` 目录后，在插件管理器中刷新并配置。

```powershell
git clone https://github.com/xxynet/n.e.k.o_plugin_selfie.git plugin/plugins/selfie
```

本地 `profiles.toml`、`profiles/`、环境文件和私有参考图不会提交到 Git，构建也会排除它们。
请通过插件管理器填写自己的密钥，不要把真实密钥写入公开的 `plugin.toml`。
插件遵循 Apache-2.0 许可证，许可证见 `LICENSE`。

## GitHub 工作流

`verify.yml` 在 push、pull request 或手动触发时执行官方插件验证。
`release.yml` 在推送 `v*` 标签时执行官方 Market 发布检查，生成插件包、检查报告和证据文件，并发布 GitHub Release。
发布标签必须与 `plugin.toml` 的版本一致，例如 `0.1.0` 对应 `v0.1.0`；手动运行 Release 工作流也必须选择该标签。
