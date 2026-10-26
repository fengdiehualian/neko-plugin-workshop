# 翻译猫娘 · neko_translate

给 N.E.K.O 猫娘装上真正的翻译能力。**不需要任何 API Key**，装好就能用。

## 为什么做这个

我把插件市场整个目录拉下来统计过：85 个插件里，**没有任何一个真正的翻译插件**（唯一命中的「剪贴板猫娘」是剪贴板监控，不提供翻译）。对一个天天陪你聊天、看屏幕、听语音的 AI 来说，这是最反常的空白，也是最好用的基础能力。

## 功能

| 能力 | 说明 |
|---|---|
| 对话中翻译 | 猫娘在你说「翻译一下」「这句日语什么意思」时会自己调用 |
| 30+ 语言 | 中（简/繁）、英、日、韩、法、德、西、葡、俄、意、阿、泰、越、印尼、马来、印地、土、波、荷、乌克兰、瑞典、捷克、希腊、希伯来、波斯、罗马尼亚、匈牙利、丹麦、芬兰、挪威 |
| 语言写法宽松 | `日语` / `Japanese` / `ja` / `jp` / `日文` 都能识别 |
| 自动检测源语言 | 用 MyMemory 的 `Autodetect`，比按字符集猜准得多 |
| 长文本分块 | 按段落/句末标点切块，公共接口限流更稳，长度不超限 |
| 批量翻译 | 一次最多 20 段，返回顺序一致的结果 |
| 结果推聊天框 | 译文作为一条消息出现在聊天里，猫娘可以直接念出来 |
| **猫娘语气** | 译文是中文时结尾加「喵～」，可自定义或关闭 |
| **念出来** | `translate_aloud` 工具让猫娘把译文读给你听 |

## 聊天里怎么用

| 工具 | 会命中的说法 |
|---|---|
| `translate_text` | 翻译一下 / 这句日语什么意思 / 帮我译成英文 |
| `detect_language` | 这是什么语言 |
| `translate_aloud` | 念给我听 / 读一下这句英文 / 说给我听 |

`translate_aloud` 会把译文以「需要回应」的方式推送，从而**触发角色语音念出来**；`translate_text` 只是返回译文。

### 猫娘语气

译文是中文时的效果：

> What a lovely day for a walk. → 多么美好的一天，适合散步。喵～

只对**中文目标语言**加后缀 —— 往英语/日语译文里塞「喵」会破坏译文本身，所以 `translate_text` 的目标是英语时译文保持干净。

```toml
[translate]
cute = true        # 关掉就没有语气后缀
cat_suffix = ""    # 留空用「喵～」，可换「呜喵」「nya～」
```

## 安装方式

### 先确认你的版本

「加载未打包插件」所在的页面只存在于 **2026-09-11 之后构建的 N.E.K.O**。
侧边栏入口叫 **「开发插件」**，页面大标题才叫「开发模式」——在导航里找「开发模式」是找不到的。
Steam 的 v0.9.0 Patch 2 构建时间戳是 `2026-09-11 17:27`，比开发页面那个提交（`20:34`）早 3 小时，
所以那一版没有这个页面；用下面的方式二即可。

### 方式一：开发插件页（需要较新构建）

1. 打开 N.E.K.O → 插件管理器 → 左侧「服务器日志」下方的 **开发插件**，打开开关
2. 点 **加载未打包插件**，选择本目录：`<你放的位置>/neko-plugins/neko_translate`
   （必须选到含 `plugin.toml` 的那一层，且填的是**运行后端那台机器**上的绝对路径）
3. 点 **校验**，确认名称/ID/版本/入口都对；再点 **加载未打包插件**
4. 卡片上点 **启动**，然后去「入口点」页触发 `list_languages` 验证一下

### 方式二：用自带打包接口（**旧版本也能用**，推荐）

把本目录放到 N.E.K.O 的用户插件根目录，然后调用它自带的 `plugin-cli` 打包接口：

```powershell
# 用户插件根目录（官方只允许打包位于这里的源码）
$root = "$env:LOCALAPPDATA\N.E.K.O\.neko-plugin-installations\plugins"

# 把本目录复制进去
Copy-Item "<本插件所在目录>" $root -Recurse -Force

# 再调用 plugin-cli/build 生成安装包（可用浏览器打开 http://127.0.0.1:48916/docs 查看接口）
```

生成的 `.neko-plugin` 放在 `%LOCALAPPDATA%\N.E.K.O\.neko-plugin-packages\`，
然后在插件管理器的 **包管理** 页面导入即可。

> 接口细节：`POST http://127.0.0.1:48916/plugin-cli/build`
> body `{"mode":"single","plugin":"<插件目录绝对路径>"}`

### 方式三：官方 CLI（要发布到插件市场时）

把 `neko_translate` 整个目录放到 N.E.K.O 源码的 `plugin/plugins/` 下，然后：

```bash
uv run neko-plugin check neko_translate
uv run neko-plugin build neko_translate --out neko_translate.neko-plugin
```

## 配置

编辑 `<用户数据根目录>/plugins/neko_translate/config/plugin.toml`
（Windows 默认是 `%LOCALAPPDATA%\N.E.K.O\plugins\neko_translate\config\plugin.toml`），
也可以在插件管理器的「配置」页里改（本项目带了 `config.schema.json`，会渲染成表单）。

```toml
[translate]
default_target = "zh-CN"   # 省略 target 时用这个
default_source = "auto"    # auto = 自动检测
providers = ["mymemory", "gtrans"]
lingva_base = "https://lingva.ml"
timeout_seconds = 15
push_to_chat = true
max_input_chars = 3000
```

### 关于提供方（实测结论，2026-10）

| 提供方 | 状况 |
|---|---|
| `mymemory` | **默认首选，实测稳定**。匿名有每日额度，超了会返回状态码而不是静默失败 |
| `gtrans` | Google 公开 web 端点，国内基本不可达，仅作兜底 |
| `lingva` | 公共实例被 Cloudflare 拦住，**需要你自建实例**后把地址填进 `lingva_base`，再加进 `providers` |

代码里还有一层保护：如果提供方把输入原样返回（MyMemory 猜错源语言时会出现，例如把法语当英语），会被判定为失败并继续降级；显式指定源语言失败时还会自动重试一次自动检测。

## 目录结构

```
neko_translate/
├── plugin.toml            # 清单：身份、入口、能力声明
├── config.example.toml    # 运行配置模板
├── config.schema.json     # 配置页表单定义
├── __init__.py            # 插件类：入口 / LLM 工具 / 生命周期
├── _languages.py          # 语言别名表 + 字符集判断（纯函数）
├── _http.py               # 标准库 urllib 封装：超时、重试、退避
├── _providers.py          # 三个免密钥提供方 + 降级链（纯逻辑）
└── i18n/                  # zh-CN / en / ja 文案
```

## 实现说明

- **零第三方依赖**：只用标准库 `urllib` + `asyncio.to_thread`。插件跑在 N.E.K.O 自带的
  Python 运行时里，不能假设 `httpx` / `requests` 存在，所以刻意不引入任何外部包。
- **模块导入期无副作用**：不建连接、不读文件，符合官方 `plugin-creation-workflow` 的要求。
- **运行时入口全部 `async def`**：同步入口会被宿主拒绝。
- **错误用 `SdkError` + `Err(...)` 返回**，不往上抛裸异常。

## 自测

仓库自带一个脚手架自检（官方 `neko-plugin init` 的标准内容）：

```bash
python -m pytest tests -q
```

更完整的开发者自测（293 项离线 + 45 项联网）在作者的开发工作区里，用于验证清单契约、
装饰器契约、语言别名表、分块逻辑与实时接口连通性。要发布前完整校验，用官方 CLI：

```bash
uv run neko-plugin check <插件目录> --release
```

## 已知限制

- 公共翻译接口是免费的，**有每日额度**，重度使用会撞限流；需要更稳可以自己接一个商业 API。
- 译文质量取决于公共翻译记忆库，专有名词、代码、长难句不如商业 LLM 翻译。
- 一次最多 20 段、单次最多 3000 字（可配）。超长内容请分段。
- 暂未接入「选中的文字直接翻译」——那需要宿主暴露剪贴板/选区事件，属于平台侧能力。

## 许可

MIT
