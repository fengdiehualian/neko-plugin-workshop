# 运行时沙盒（N.E.K.O Runtime Sandbox）

> 沙盒位置：`~/WorkBuddy/2026-11-04-21-16-11/neko-runtime-sandbox`
> 独立于专家包，工作区内的可执行工具。**不装成 skill**，避免污染 `~/.workbuddy/skills/` 基线。

## 1. 它解决什么

写插件最容易的错觉是「代码写完了 = 插件能用」。沙盒让你在**不启动 N.E.K.O** 的情况下真跑一遍：

- `plugin.toml` 能不能解析
- 入口有没有真的注册上（**entry-404 的根因就在这里**）
- 生命周期会不会跑、定时器回调会不会 raise
- 返回值是不是 `Ok`/`Err`（不是裸 dict）
- `push_message` 到底推了什么

## 2. 双模式（必须先看这条）

| 模式 | 触发条件 | 保真度 |
|---|---|---|
| **real** | 本机装了 `pydantic` / `msgpack`，`import plugin.sdk.plugin` 成功 | 100%，用真 SDK |
| **stub** | 上述 import 失败 | 契约对齐，签名/枚举/返回语义照上游抄，但**不是真宿主** |

> **本机现状（2026-11-07）**：已装 `pydantic 2.13.5` + `msgpack 1.2.4`，沙盒自动使用 **real 模式**。`check` 会输出 `[info] sdk.real checked against real SDK 0.1.0`。
>
> **real 模式多抓到的真问题**：`demo_plugin` 的 `greet` 在 stub 下正常，在真 SDK 的 pydantic 校验下抛 `ValidationError: expected dict payload, got Ok` —— 这类问题只有真 SDK 会暴露。

**每条 CLI 输出都会显式标注当前模式与原因。** 看到 `sdk mode : stub` 就要知道：pydantic 校验是空操作、模型面不可用、跨插件 bus 不可用。

## 3. 五条命令

```bash
cd ~/WorkBuddy/2026-11-04-21-16-11/neko-runtime-sandbox

# ① 看插件结构：列出入口 / 生命周期 / 定时器，并标出"声明了但代码里没有"的入口
python -m sandbox.cli inspect <plugin_dir>

# ② 触发入口
python -m sandbox.cli run-entry <plugin_dir> --id greet --args '{"name":"Neko","times":2}'

# ③ 走生命周期（startup / shutdown / reload / freeze / unfreeze / config_change）
python -m sandbox.cli run-lifecycle <plugin_dir> --id startup

# ④ 手动推进定时器（不是等真时间，直接折算触发次数）
python -m sandbox.cli tick <plugin_dir> --seconds 180

# ⑤ 契约体检（专抓交付前该抓的问题）
python -m sandbox.cli check <plugin_dir>
```

全部支持 `--json`。

## 4. `check` 会报什么（这是交付闸门）

实测输出（demo_plugin）：

```
[ERROR] entry.declared_missing  entry 'ghost_entry' is declared in plugin.meta.json but the code does not register it
[ERROR] entry.raises           entry 'boom' raised ValueError: boom: this entry always raises
[warn ] sdk.stub                checks ran against the contract-aligned stub
[warn ] entry.not_result        entry 'plain' handler returned dict, not Ok/Err
[info ] entry.err               entry 'net_probe' returned Err(... network disabled in sandbox ...)
2 error(s), 2 warning(s), 3 info
```

**退出码非 0 即代表有 `[ERROR]`** —— 尚书省验收时以此为准。

## 5. 接进朝议流程

| 阶段 | 谁 | 用沙盒做什么 |
|---|---|---|
| 5. 实现 | 工部 | 写完先自己跑 `inspect` + `run-entry`，确认入口注册上 |
| 6. 安装验证 | 兵部 | 跑 `check`，把 `[ERROR]` 逐条修掉；`tick` 验定时器不 raise |
| 4/6. 验收 | 尚书省 | **不接受"文件写完"**，要求附 `check` 输出（退出码 0） |
| 3. 审议 | 门下省 | 方案阶段可先 `inspect` 一个相似插件，确认要用的能力真的注册得上 |

## 6. 不能模拟什么（诚实边界）

1. **pydantic 校验缺失**：`params=Model` 生不出 schema，`model_validate` 是空操作
2. **不是安全边界**：禁网只拦老实代码，`ctypes`/`subprocess` 可绕
3. 定时器按"折算次数"触发，`time.time()` 仍是真实时钟
4. 无模型面：`ctx.models.get_client()` 抛 `TransportError`，不伪造假客户端
5. 无跨插件 / bus 数据：返回 `Err(TransportError)`，并记入 `refused` 列表
6. 超时**不强杀**：`join(timeout)` 后标记并留线程
7. hook 执行顺序是近似（上游 `HookExecutorMixin` 本身 contract-only）
8. i18n 只做 JSON 加载 + `{name}` 插值，无 ICU 复数

## 7. 安全约束（必须遵守）

- 默认**禁网**（patch `socket.connect/sendto/getaddrinfo`），`--allow-network` 才放开
- 写入全部落在 `<tmp>/neko-sandbox-runs/<run_id>/`，**不碰插件目录以外的用户文件**
- 单次 entry 默认 10s 超时
- **只用于自有或已授权插件**，它不抵御恶意代码

## 8. 已验证过的真实插件（说明沙盒不是只跑玩具）

用 `neko-src/plugin/plugins/` 的实际插件验证过 loader：

| 插件 | 结果 |
|---|---|
| `jukebox_controller` | 正常加载，`0 error(s), 1 warning`（warning 就是 stub 提示） |
| `memo_reminder` | 加载成功（含 `self.store._read_value` 私有用法） |
| `lifekit` / `web_search` | 报 `ModuleNotFoundError: httpx`（本机确实没装） |

**顺带发现两个真实环境缺口**：
- `memo_reminder` 报 `ZoneInfoNotFoundError: No time zone found with key Asia/Shanghai` —— 本机 Windows 缺 tzdata，**真实环境也会踩**
- `httpx` 未装 —— 涉及网络的插件在本机直接跑不起来

## 9. 测试

```bash
cd ~/WorkBuddy/2026-11-04-21-16-11/neko-runtime-sandbox && python -m unittest discover tests -v
# Ran 40 tests — OK
```
