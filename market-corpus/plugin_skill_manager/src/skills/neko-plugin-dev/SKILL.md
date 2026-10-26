---
name: neko-plugin-dev
description: 开发、修复或审查 N.E.K.O. 插件时使用。涵盖插件目录结构、plugin.toml 必填字段、SDK 装饰器（plugin_entry/lifecycle/llm_tool）、跨插件调用、vendor 依赖规则与常见启动故障（504/看门狗/嵌入式解释器缺标准库）。
license: Apache-2.0
metadata:
  author: neko-plugin
  version: "1.0"
---

# N.E.K.O. 插件开发指南（本地权威摘要）

开发、修复、审查 N.E.K.O. 插件，或插件"启动失败 / 504 / 入口超时"时的排查手册。
全文检索官方文档站用 `neko_docs` 插件（源 `neko`）；本页是经真机验证的本地摘要。

## 何时使用

- 要写一个新插件、或把现有插件改造/拆分/合并
- 插件启动失败、入口调用超时、依赖导入失败
- 不确定 plugin.toml 字段、装饰器用法、vendor 依赖规则

## 插件最小骨架

```
<plugin_id>/            目录名必须等于 id
├── plugin.toml         清单（id/name/version/entry 四字段必填）
├── __init__.py         entry 指向的 NekoPluginBase 子类
├── config.example.toml 首次运行配置模板（不得含 [plugin] 段！）
└── tests/
```

```toml
[plugin]
id = "my_plugin"        # ^[A-Za-z0-9_-]+$，与目录名一致
name = "我的插件"
version = "0.1.0"
entry = "plugin.plugins.my_plugin:MyPlugin"

[plugin_runtime]
enabled = true
auto_start = false
timeout = 120           # 启动就绪窗口（默认仅 10s！重初始化插件必须调大）
startup_failure = "warn" # warn=保留进程标记降级 | fail | ignore
```

```python
from plugin.sdk.plugin import NekoPluginBase, Ok, Err, SdkError, neko_plugin, plugin_entry, lifecycle

@neko_plugin
class MyPlugin(NekoPluginBase):
    @lifecycle(id="startup")          # 必须 async def；返回 Ok/Err
    async def on_startup(self, **_): return Ok({...})

    @plugin_entry(id="my_entry", timeout=30.0)
    async def my_entry(self, q: str = "", **_):   # 入口必须接受 **_
        return Ok({"answer": q})                   # 永远返回 Ok/Err，不抛异常
```

## 硬性规则（真机踩坑换来的）

1. **入口与生命周期必须 `async def`**；同步 IO/重 CPU 一律 `asyncio.to_thread`。
   torch/transformers 等重库**不要在 startup 或模块顶层 import**——嵌入式解释器的
   启动就绪窗口只有 10 秒，超时即 504 且进程被终止。做法：懒加载 + `to_thread` +
   startup 里起后台预热线程。
2. **`[plugin_runtime] timeout`**：重初始化插件（大模型、大索引）显式调大（≤300）。
3. **config.example.toml 禁止 `[plugin]` 段**（校验器 [FAIL]）。业务配置写在
   `[plugin_runtime]` 之后的自定义段；首装时宿主复制它到用户数据 `config/plugin.toml`。
4. **PluginStore 在构造期被冻结**：`[plugin.store] enabled=true` 可能不生效，
   startup 里手动兜底 `if not self.store.enabled: self.store.enabled = True`。
5. **依赖 vendor/ 规则**：pyproject 声明依赖 → `uv run neko-plugin sync <id>` 落入
   `vendor/`；插件 startup 自己把 `<plugin_dir>/vendor` 插到 sys.path（宿主不保证）。
   宿主已自带：numpy/scipy/onnxruntime/tokenizers/aiohttp/yaml/pydantic 等，
   不要重复 vendor；纯 Python 包最稳，编译扩展需 cp311-win_amd64。
6. **嵌入式解释器缺标准库**：宿主是 PyInstaller 风格嵌入式 CPython 3.11，`cProfile`、
   `profile` 等纯 py 标准库模块可能缺失。缺什么就从 CPython 3.11 `Lib/*.py` 拷进
   `vendor/`（`_lsprof` 等为内建模块，无需拷）。
7. **拆分/合并插件**：`previous_ids = ["旧id"]` 阻止新旧身份共存；合并后拆回时，
   每个新插件 `previous_ids = ["合集id"]`，并迁移各自的 `config/` 运行配置与 `data/`。
8. **FTS5 中文检索**：unicode61 分词器对连续汉字无效，建表用 `tokenize='trigram'`
   （查询需 ≥3 字）。sqlite-vec 加载失败要"尽力而为"：能力探测如实上报 + 退化路径。

## 排障速查

| 症状 | 根因 | 处置 |
|------|------|------|
| start 返回 504 / PLUGIN_START_TIMEOUT | startup 超 10s（重 import、模型加载） | `timeout` 调大 + 懒加载 + to_thread + 后台预热 |
| 入口报 "event ... timed out after Ns" | 入口同步阻塞了事件循环（IPC 应答也发不出） | 阻塞调用全部 to_thread |
| ModuleNotFoundError: cProfile/profile 等 | 嵌入式解释器缺标准库纯 py 模块 | 从 CPython 3.11 Lib/*.py 拷入 vendor/ |
| ModuleNotFoundError: AutoTokenizer | transformers 惰性导入的底层依赖缺失 | 看异常 `__cause__` 链找真模块 |
| "registered but not running" | 进程没起来（依赖插件未启动 / startup fail） | 查插件日志 `/plugin/{id}/logs` |
| 真机调用失败但桩测试全绿 | 跨插件 IPC 字节序/编码差异、vendor 未注入、启动顺序 | DbClient 边界统一 b64 包装；自愈式惰性初始化 |

## 验证路径（不要只跑单元测试）

- 官方 CLI：`uv run neko-plugin init/check/sync/build <id>`（check 0 错误 0 警告才算过）
- 桩 SDK 端到端：仿 `tests/stub_run.py`——shim 包树 + 桩 ctx，真实驱动入口 Ok/Err
- 宿主真机：用户插件服务 `http://127.0.0.1:48916`——
  `POST /runs {plugin_id, entry_id, args}` 调入口；`POST /plugins/reload` 重扫；
  `POST /plugin/{id}/start|stop`；`GET /runs/{id}` 看结果；
  `GET /plugin/{id}/logs` 看日志。仅用只读/常规端点。

## 完整文档

- 官方文档站全文检索：`neko_docs` 插件（`docs_search` / `docs_read`，源 `neko`）
- 在线原文：https://project-neko.online/zh-CN/plugins/
  （plugin-development / quick-start / plugin-toml / sdk-reference / decorators /
  tool-calling / best-practices / cli）
