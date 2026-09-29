# 市场插件写法统计与惯用法(来自 46 个官方市场插件的源码分析)

> 数据来源:market.project-neko.cn 全量插件(2026-09 抓取,46 个有效源码仓库)。
> **写任何插件前先读本文**——这是真实上架插件的写法统计,比个人直觉可靠。
> 有同类功能时,优先模仿 `market-examples/` 里的完整范例源码。
> **全部 46 个插件的功能卡片与源码索引见 `market-catalog.md`**;完整源码都在本机 `<WORKSHOP_DIR>\market-corpus\<slug>\src`,直接读。

## 一、能力使用率(46 个插件中)

| 能力 | 使用率 | 一句话说明 |
|---|---|---|
| `push_message`(v2 字段) | 57% | 主动给聊天窗口/HUD 推消息,提醒类必备 |
| `self.data_path()` | 41% | 持久化数据(JSON/SQLite),重启不丢 |
| **`@llm_tool`** | **50%** | **让猫娘在对话里直接调用你的功能——市场第一大主流能力** |
| http 客户端(httpx/requests) | 39% | 联网类插件(搜索/音乐/邮件)的标准配置 |
| `self.store` | 37% | 官方键值存储(需 plugin.toml 开 `[plugin.store] enabled=true`) |
| 静态 UI(`register_static_ui`) | 28% | 插件自带网页面板 |
| `@timer_interval` | 20% | 定时任务 |
| `PluginSettings` 声明式配置 | 少数但官方推荐 | hot 字段热更新 |
| 动态入口 / PluginRouter / list_actions | <10% | 大型插件才用 |

**结论**:`@plugin_entry` + `@llm_tool` 双注册、`push_message` v2、`data_path`/`store` 持久化,是市场插件的四个支柱。做任何「用户一句话能触发」的功能,**必须同时注册为 llm_tool**。

## 二、惯用法 1:llm_tool 与 plugin_entry 双注册(shell_cmd 实录)

同一个方法,既是面板/Agent 手动入口,又是猫娘对话里可调用的工具——**两个装饰器叠加,不要写两份代码**:

```python
@llm_tool(
    name="shell_cmd_run",
    # description 是给 LLM 看的:必须写清「什么场景该调用它」
    description="执行一条 Shell / CMD / PowerShell 命令,返回 stdout、stderr 与退出码。"
                "当用户想让猫娘直接在系统里跑命令时使用。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "shell": {"type": "string", "description": "执行环境: auto(默认) / cmd / bash / powershell"},
        },
        "required": ["command"],
    },
    timeout=30.0,
)
@plugin_entry(
    id="run",
    name="执行命令",
    description="执行 Shell / CMD / PowerShell 命令",
    llm_result_fields=["ok", "exit_code", "stdout", "stderr"],  # 只把这些字段喂给 LLM,省 token
)
async def run(self, command: str, shell: str = "auto", **_):
    ...
```

要点:
- `llm_tool.name` 用 `插件主题_动作` 格式(全局唯一,如 `shell_cmd_run`)。
- `description` 写使用时机,LLM 靠它决定是否调用——写「当用户想…时使用」。
- `llm_result_fields`(plugin_entry 上)控制返回给 LLM 的字段,大结果必须过滤。
- 参数 schema 写在 llm_tool 的 `parameters`;plugin_entry 用类型注解自动生成。

## 三、惯用法 2:联网插件标准结构(tavily_search 实录)

```python
import httpx

_SEARCH_URL = "https://api.tavily.com/search"

async def _search(self, query: str, max_results: int = 5):
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(_SEARCH_URL, json={...}, headers={...})
        r.raise_for_status()
        return r.json()
```

要点:
- 用 **httpx 异步客户端 + 显式超时**(阻塞式 requests 会拖死插件事件循环)。
- API key 从配置读(`config.example.toml` 提供样例),**绝不硬编码**。
- 上游失败 → `return Err(SdkError("..."))`,不抛异常。
- 结果做截断/限流(如 `[:5]`),避免撑爆上下文。

## 四、惯用法 3:定时提醒类状态机(sys_monitor 实录)

定时类最常见的坑是**重复轰炸用户**。市场标准解法:上次提醒键值 + 时间戳去重:

```python
async def check(self):
    try:
        alerts = self._collect_alerts()
        alert_key = ",".join(sorted(alerts))
        now = time.time()
        last_key = getattr(self, "_last_alert_key", "")
        last_at = getattr(self, "_last_alert_at", 0)
        if alerts and (alert_key != last_key or now - last_at > self._repeat_interval):
            self._last_alert_key = alert_key
            self._last_alert_at = now
            self.push_message(
                source="sys_monitor",
                visibility=["chat"],
                ai_behavior="respond",
                parts=[{"type": "text", "text": "提醒:" + "；".join(alerts)}],
                priority=5 if critical else 3,
            )
        elif not alerts:
            self._last_alert_key = ""   # 恢复正常时重置,下次异常可再次提醒
            self._last_alert_at = 0
    except Exception as e:
        self.logger.warning("check failed: %s", e)   # 定时回调必须吞异常,不许 raise
```

要点:
- `visibility=["chat"]` 只进聊天窗;要弹 HUD 加 `"hud"`;`priority` 紧急用 5 普通 3。
- **定时回调里绝不 raise**——异常吞掉记日志,返回 `Ok(...)`。

## 五、惯用法 4:持久化选择

| 场景 | 用法 |
|---|---|
| 结构化数据(笔记/记录/列表) | `self.data_path("notes.json")` + json 读写 |
| 简单键值(缓存状态/开关) | `self.store.set/get`(plugin.toml 开 `[plugin.store] enabled=true`) |
| 可丢弃缓存 | `self.cache_path(...)` |

## 六、让插件被 Agent 选中(plugin.toml 的可见性字段)

市场插件的共性:`description` 写完整功能、`short_description` 一句话、`keywords` 写用户可能说的词(正则数组)。这三样决定你的插件会不会被猫娘的 Agent 选中执行:

```toml
description = "随机返回一句古诗,支持按作者/主题搜索"
short_description = "古诗随抽与检索"
keywords = ["古诗", "诗", "唐诗", "宋词", "来一首诗"]
```

## 七、完整范例(references/market-examples/)

| 范例 | 学什么 |
|---|---|
| `shell_cmd/` | llm_tool+plugin_entry 双注册、子进程执行、多文件组织(shell_core.py 拆分) |
| `tavily_search/` | httpx 异步联网、API key 配置、搜索结果裁剪、llm_result_fields |
| `sys_monitor/` | 定时监控、push_message v2、防重复提醒状态机、静态 UI |

对照需求:做「对话触发」的看 shell_cmd;做「联网查询」的看 tavily_search;做「定时提醒/监控」的看 sys_monitor。
