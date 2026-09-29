---
name: neko-plugin-dev
description: N.E.K.O.(猫娘计划)插件开发权威规范。写/改/修任何 N.E.K.O. 插件(plugin.toml、入口、SDK API、tool calling、打包、发布)时必须使用,按需读 references/ 下对应文档。
---

# N.E.K.O. 插件开发(写死规则,不可省略)

你在为 N.E.K.O.(猫娘计划,Python 插件框架,进程隔离 + ZMQ IPC)开发插件。以下规则是硬性约束,任何插件代码都必须遵守;细节文档在 `references/` 目录,按需读取。

## 铁律(违反=插件无效)

1. **包类型只有两种**:独立功能用 **Plugin**(`from plugin.sdk.plugin import ...`),外部协议桥接(MCP/NoneBot)用 **Adapter**。Extension 已删除,不要生成。
2. **plugin.toml 必填四字段**:`id`、`name`、`version`、`entry`。`id` 必须小写字母开头、仅小写字母/数字/下划线(`^[a-z][a-z0-9_]*$`)且全局唯一(与目录名一致,发布后不可改);`entry` 必须是 `module.path:ClassName`,解析到 `NekoPluginBase` 子类,不能是 PluginRouter。
3. **目录名 = 插件 ID**:打包/发布/安装都按 ID 建目录,源码目录名必须与 entry 包路径一致。发布包不允许清单不完整。
4. **不要自作主张改 ID**:改 ID = 新插件身份,不迁移旧数据。可选 `previous_ids` 只防新旧共存。
5. **入口两种,别混淆**:`[plugin].entry` 是宿主加载入口(NekoPluginBase 子类);`greet` 这类运行时入口 ID 来自 `@plugin_entry(id=...)` 或 `register_dynamic_entry(...)`,加载成功后 Agent 才会调用。
6. **插件在独立进程**里跑,通过 ZMQ IPC 与宿主通信;插件内不做长阻塞同步 IO(会拖死自己的事件循环)。
7. **CLI 用法(便携/离线环境)**:`python plugin\neko_plugin_cli\cli.py check <插件目录>` 检查;`... build <插件目录> --out <输出.neko-plugin>` 打包。**禁止 `uv run`**(会触发全量依赖重建,数 GB 下载+超时)。依赖放 `vendor/` 或 plugin.toml 声明,CLI 不自动装依赖。
'8. **check 全绿才能 build**;build 产物 `.neko-plugin` 落在指定 --out 路径。
9. **已移除 API 黑名单(v0.9 起)**:`type=script`/`type=extension`、`[plugin.host]`、`plugin._types.result`、`plugin.sdk.extension`、Bus `where_*` 查询、`get_message_plane_all`、`self.memory`/MemoryClient、push_message v1 字段(`message_type`/`content`/`delivery`/`reply`/`fast_mode`)——一律禁止使用;push_message 用 v2(parts/visibility/ai_behavior/priority)。'

## 文档索引(references/,按需读)

| 文件 | 内容 |
|---|---|
| `index.md` | 系统概览:进程隔离架构、Plugin vs Adapter 选型 |
| `plugin-development.md` | 开发入门:目录构成、完整制作流程 |
| `quick-start.md` | Plugin CLI 全流程:init/check/build/发布;安装版 DevMode 开发 |
| `cli.md` | 插件市场发布教程(发布工作流) |
| `plugin-toml.md` | plugin.toml 全部字段(必填/可选/示例) |
| `entries.md` | 入口与参数:@plugin_entry、动态入口、参数模型 |
| `decorators.md` | 全部装饰器(从 plugin.sdk.plugin 导入) |
| `router.md` | PluginRouter 拆分大型插件 |
| `sdk-reference.md` | SDK 能力全集:bus/config/data/UI/权限等 |
| `tool-calling.md` | LLM 工具调用注册(Tool Calling) |
| `migration-v0.9.md` | SDK v0.9 迁移指南(旧插件升级) |
| `hosted-ui.md` | Hosted TSX/Markdown 插件界面(管理器内 UI) |
| `examples.md` | 官方示例:Result 类型、配置、定时、消息等 |
| `advanced.md` | Adapter 与并发编程(网关管线、线程安全) |
| `best-practices.md` | 最佳实践:Result 一致性、超时、日志、资源清理 |
| **`market-patterns.md`** | **市场插件写法统计与惯用法(46 个真实上架插件分析)——动手前必读** |
| **`market-catalog.md`** | **全部 46 个市场插件的学习卡片(功能/接口/能力/源码路径)——按功能找同类参考** |
| **`market-examples/`** | **3 个完整市场插件源码:shell_cmd(llm_tool 双注册)/ tavily_search(httpx 联网)/ sys_monitor(定时推送状态机)** |

## 常用骨架(Plugin)

```toml
[plugin]
id = "my_plugin"          # 与目录名一致;小写字母开头,仅小写字母/数字/下划线
name = "我的插件"
version = "0.1.0"
type = "plugin"            # 可省略(默认);adapter 才写 "adapter"
description = "一句话说明"
entry = "plugin.plugins.my_plugin:MyPlugin"   # module.path:Class
```

```python
from plugin.sdk.plugin import NekoPluginBase, neko_plugin, plugin_entry, lifecycle, Ok, Err, SdkError

@neko_plugin
class MyPlugin(NekoPluginBase):
    @lifecycle(id="startup")          # 初始化一律走 lifecycle,不要依赖 auto_start
    async def on_startup(self, **_):
        return Ok(None)

    @plugin_entry(id="hello", name="你好", description="打个招呼")
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"Hello, {name}!"})   # 入口必须返回 Ok/Err,不抛异常
```

## 声明式配置(PluginSettings,宿主官方机制)

可配置项继承 `PluginSettings`,字段用 `SettingsField`;hot=True 的字段改动后随 `config_change` 生命周期事件实时生效,无需重启:

```python
from plugin.sdk.plugin import PluginSettings, SettingsField
from plugin.sdk.plugin.settings import create_settings_safe

class MySettings(PluginSettings):
    model_config = {"toml_section": "settings"}   # 对应 plugin.toml 用户配置的 [settings] 段
    interval: int = SettingsField(30, hot=True, ge=5, le=86400, description="间隔(秒)")

@neko_plugin
class MyPlugin(NekoPluginBase):
    async def _load_cfg(self):
        cfg = await self.config.dump(timeout=5.0)          # 官方读取方式,返回整个 plugin.toml 映射
        section = cfg.get("settings") if isinstance(cfg, dict) else None
        self._cfg = create_settings_safe(MySettings, section if isinstance(section, dict) else None)

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        await self._load_cfg()
        return Ok(None)

    @lifecycle(id="config_change")               # 用户改配置时自动触发
    async def on_config_change(self, **_):
        await self._load_cfg()
        return Ok(None)
```

## 常用能力速查(全部来自宿主 SDK,查不到细节就读 references)

- **LLM 工具**:`@llm_tool`(让猫娘对话里直接调用,如「夸我一句」「帮我搜…」)——市场 50% 插件的核心能力;标准做法是与 `@plugin_entry` **叠加双注册**(见 `market-patterns.md` 惯用法 1 / `market-examples/shell_cmd/`);响应包 `{"output": ...}`。详见 `tool-calling.md`。
- **动态入口**:`self.register_dynamic_entry(entry_id, handler)` 运行时注册/注销入口。详见 `entries.md`。
- **定时/生命周期**:`@timer_interval(id=..., seconds=..., auto_start=True)`;lifecycle id 只有 `startup/shutdown/reload/freeze/unfreeze/config_change` 六种。
- **消息推送 v2**:`self.push_message(source=..., visibility=["chat","hud"], ai_behavior="respond"|"read", parts=[{"type":"text","text":...}], priority=N)` → 返回 TypedDict,判 `result.get("submitted")`。定时提醒类必须有防重复状态机(`market-examples/sys_monitor/`)。
- **联网**:`httpx.AsyncClient(timeout=30)` 异步调用,禁止阻塞式 requests(`market-examples/tavily_search/`)。
- **存储**:`self.data_path("x.json")` 持久数据 / `self.cache_path(...)` 缓存 / `self.store` 键值存储(plugin.toml 开 `[plugin.store] enabled=true`)。
- **静态 UI**:`self.register_static_ui("static")` 挂插件自带网页;TSX 面板走 plugin.toml `[[plugin.ui.panel]]`。详见 `hosted-ui.md`。

## 工作流(本工坊)

1. 生成/修改代码 → `python plugin\neko_plugin_cli\cli.py check <dir>`(N.E.K.O 仓库根执行)
2. 有 error 必须修到全绿;warning 酌情处理
3. `python plugin\neko_plugin_cli\cli.py build <dir> --out <dir>.neko-plugin`
4. 把产物路径告诉用户
5. **用户想发布上架时**(Market 要求,缺一步都过不了审):
   - 插件目录必须是**独立 Git 仓库**,仓库名 `n.e.k.o_plugin_<插件ID>`;
   - 标准仓库文件(`.vscode/`、`.github/workflows/verify.yml` 与 `release.yml` 等)由
     `python plugin\neko_plugin_cli\cli.py setup-repo <dir> --git --github-actions` 生成
     (工坊生成流程已自动跑过不带 `--git` 的版本;首次建仓时带 `--git` 可一并初始化 git 仓库,
     缺了就补跑,`--upgrade-github-actions` 可升级旧工作流);
   - 推送 GitHub 后提交 **Market 首次审核**,审核通过后运行 `neko-plugin publish <id>`
     创建 GitHub Release 并发布 Market 版本;
   - **本地 build 的 `.neko-plugin` 只是安装包,不能代替 GitHub Release**;严禁用复制目录、
     安装包导入或符号链接伪装成发布流程。细则(含失败重试)读 `references/cli.md`。

拿不准 API 细节时,先读对应 reference 再写代码,不要凭空编造 SDK 方法。

## 防遗忘强规则(每轮生成代码前自检)

- 本文件与 `references/` 是**唯一事实来源**。文档站(project-neko.online)更新时,由工作台同步到这里;不要依赖训练记忆里的旧版 SDK。
- 写任何装饰器/SDK 调用前,若在本文件或 references 里找不到,视为**不存在**,换文档里的等价 API。
- **动手前先读 `references/market-patterns.md`**(46 个市场插件的真实写法统计):用户一句话能触发的功能必须 `@llm_tool` + `@plugin_entry` 双注册;联网用 httpx 异步+超时;定时提醒必须有防重复状态机;持久化优先 data_path/store。然后翻 `references/market-catalog.md` 按功能找同类插件,直接读其完整源码(全在本机 `<WORKSHOP_DIR>\market-corpus\<slug>\src`,你有全局权限);`references/market-examples/` 里有 3 个精读范例。
- 生成完整插件后,对照上方铁律 1–8 逐条自检;修他人代码时,先用 `check` 拿到结构化 issues 再动手。
- 涉及 UI、Adapter、示例写法时,必须先读对应 reference(`hosted-ui.md` / `advanced.md` / `examples.md`),不要凭印象写。
