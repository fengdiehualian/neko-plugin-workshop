# N.E.K.O 插件契约速查

> 本文件是**速查**，用于快速对齐写法与排错方向。具体字段以官方 SDK 文档为准；本文件未覆盖的标 `[未核验]`。

## 1. 目录结构

```
my_plugin/
  plugin.toml        # 清单
  main.py            # SDK 编排层
  logic.py           # 纯逻辑层（不 import SDK，可单测）
  requirements.txt   # 通常只有 httpx
  README.md
```

## 2. `plugin.toml` 段落

| 段 | 作用 |
|---|---|
| `[plugin]` | 名称、版本、描述、入口声明 |
| `[plugin.author]` | 作者信息 |
| `[plugin.sdk]` | SDK 兼容声明 |
| `[plugin.i18n]` | 多语言名称 / 描述 |
| `[plugin_runtime]` | 运行时参数 |
| `[plugin.store]` | KV 存储声明 |
| 自定义段 | 插件自己的配置（如 `[watch]`） |

## 3. 关键装饰器

| 装饰器 | 用途 | 要点 |
|---|---|---|
| `@plugin_entry` | 暴露入口，可被猫娘对话调用 | 配 `llm_result_fields` 声明返回字段 |
| `@lifecycle` | 启动 / 停止钩子 | 初始化与清理放这里 |
| `@timer_interval` | 周期任务 | **回调必须 try/except，绝不 raise** |
| `@message` | 接收消息 | 注意来源可信度 |

> **常见误写**：用 `@llm_tool` 当入口。官方 `web_search` 用的是 `@plugin_entry` + `llm_result_fields`，不是 `@llm_tool`。

## 4. 数据与推送

- `store`：KV 持久化，适合小状态
- `data_path`：文件持久化，适合大对象；**要有清理策略**
- `push_message` v2：主动推送给猫娘（注意别刷屏，加节流）

## 5. 返回约定

统一 `Ok(...)` / `Err(...)`。`Err` 要带可读原因，不要只给错误码。

## 6. 加载路径（极易踩）

- **运行时从 `.neko-plugin-installations/plugins/<name>/` 加载**
- `plugins/` 只是源码副本 —— **改源码不等于生效**，要同步到安装目录
- 改完先看加载日志，再判断是否成功

## 7. 三类高频故障

| 现象 | 常见根因 | 排查 |
|---|---|---|
| **entry-404** | `plugin.toml` 声明的入口名 ≠ 代码里的 `@plugin_entry` 名 | 两者逐字对比 |
| **启动 failed** | 语法错误 / 缺依赖 / import 了不存在的 SDK 成员 | 直接跑导入看 traceback |
| **model-403** | 模型权限 / 网关配置 / key 缺失 | 看请求与响应，定位哪一层拒绝 |

## 8. feed 字段（若插件涉及动态流）

- **feed list** 用：`preview.text` / `author_identity` / `author_relation` / `source_expectation`
- **feed_get** 用：`content` / `expected_response`
- **两套字段不能混用** —— 混了会拿到空值或错位内容
