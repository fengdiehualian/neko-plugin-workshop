# MONETA FX · N.E.K.O. 插件 + 独立云端服务端

把 `MONETA-FX-2026-10-10T14-37-19-962Z`（虚构外汇模拟交易室）整体移植成两套可独立运行的产物，共用同一个 Python 交易引擎：

| 产物 | 位置 | 用途 |
| --- | --- | --- |
| N.E.K.O. 插件 | `moneta_fx/` | 在插件管理器里运行的交易终端（Hosted TSX 面板） |
| 独立云端服务端 | `server/` | 原版 UI 1:1 + Python 后端，可部署到任意云主机 |

> 全部行情、资金与成交均为虚构游戏数据，不对应任何真实市场，不构成投资建议。

---

## 一、目录结构

```text
neko-moneta-fx/
├── moneta_fx/                 ← N.E.K.O. 插件源码（目录名必须等于插件 ID）
│   ├── plugin.toml            ← 清单：id / name / entry / UI / i18n / 运行时
│   ├── __init__.py             ← 惰性导出 MonetaFxPlugin（裸 import 不拉 SDK）
│   ├── plugin.py               ← 插件类：生命周期、入口、@ui.context
│   ├── market.py               ← 虚构市场模型（移植自 public/js/market.js）
│   ├── engine.py               ← 交易引擎：下单/保证金/隔夜/强平（移植自 engine.js）
│   ├── store.py                ← 共享市场存档结构与校验（移植自 store.js）
│   ├── auth.py                 ← 会话、口令散列、登录限流
│   ├── world.py                ← 共享运行时：行情、账户、管理、资金调度
│   ├── ui/panel.tsx            ← Hosted TSX 交易终端
│   ├── docs/quickstart.md      ← 使用指南
│   ├── i18n/{zh-CN,en}.json    ← 插件文案
│   └── config.schema.json      ← 通用配置页表单
├── server/                     ← 独立云端服务端
│   ├── app.py                  ← FastAPI 应用（原版 API 契约）
│   └── static/                 ← 原版 UI 1:1（index.html / styles.css / js/*）
├── scripts/
│   ├── patch_ui.py             ← 把原版 UI 的真实行情文案改为服务端共享行情
│   ├── e2e_cloud.py            ← 云端端到端测试（35 项）
│   └── debug_*.py              ← 定位用脚本
├── tests/
│   └── stub_plugin_sdk.py      ← 本地测试用 SDK stub（不随插件发布）
├── smoke_test.py               ← 引擎单元冒烟测试（41 项）
└── data/                       ← 云端运行时生成的共享市场存档
```

---

## 二、作为 N.E.K.O. 插件安装

### 开发模式（推荐先这样验证）

1. 打开安装版 N.E.K.O. 的插件管理器 → 左侧「服务器日志」下方的「开发模式」→ 开启开关。
2. 点击「加载未打包插件」，选择包含 `plugin.toml` 的目录：
   `C:\Users\ADMIN\Downloads\neko-moneta-fx\moneta_fx`
   网页端要填**运行后端的机器上的绝对路径**。
3. 点击「校验」，核对名称、ID、版本、入口和路径，再点「加载未打包插件」。
4. 在自己编辑器里改文件，点卡片上的「重载」；改依赖需要重启插件。
5. 点「打包」下载 `.neko-plugin`，在另一套 N.E.K.O. 的普通导入入口安装验证。

### 源码版

把整个 `moneta_fx` 目录复制到 N.E.K.O. 源码的 `plugin/plugins/moneta_fx/`，然后：

```bash
uv run neko-plugin check moneta_fx
cd frontend/plugin-manager
npm run check-hosted-tsx -- plugin/plugins/moneta_fx
```

> 源码目录名必须与插件 ID 一致（都是 `moneta_fx`），否则打包器不会改写源码，安装会按 ID 另建目录。

### 配置

在插件管理器的配置页（或运行时 `plugin.toml` 的 `[moneta]` 段）里改：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `initial_balance` | `100000` | 新账户初始模拟资金 |
| `tick_interval_ms` | `500` | 市场心跳间隔 |
| `persist_interval_ms` | `5000` | 自动存档间隔 |
| `developer_key` | `moneta-dev-key` | 开发者密钥，**上线前必须改** |
| `server_url` | `""` | 云端后端地址，留空 = 本地离线模式 |
| `use_cloud` | `false` | 置 `true` 且填了 `server_url` 时走云端 |

#### 连接云端后端

插件**默认连到已部署的服务端**（`http://8.152.0.186:4173`），装上去就能操作云端共享账户。要改成本地离线模式，在插件管理器的配置页把 `server_url` 改成 `""`、`use_cloud` 改成 `false`。

生效后：

- 注册 / 登录 / 下单 / 平仓 / 改单 / 撤单 / 账户分析全部打到服务端
- N.E.K.O. 面板和浏览器端看到的是**同一个世界**，账户、持仓、行情共享
- 行情控制台、账户管理、资金调度需要先用服务端的 `MONETA_DEVELOPER_KEY`
  或 `MONETA_ADMIN_PASSWORD` 登录
- 本地不再自动存档（数据在服务端）
- 服务端不可达时自动回退本地模式，不影响插件启动

两种模式的返回结果都带 `mode` 字段（`"cloud"` / `"local"`），便于区分当前连的是哪套后端。

---

## 三、独立云端服务端（原版 UI 1:1）

```bash
cd C:\Users\ADMIN\Downloads\neko-moneta-fx
uv run --with fastapi --with "uvicorn[standard]" --with pydantic \
  python server/app.py
```

或固定依赖后直接用 uvicorn：

```bash
uv venv
uv pip install fastapi "uvicorn[standard]" pydantic
uv run uvicorn server.app:app --host 0.0.0.0 --port 4173
```

Windows 上一键启动：双击 `start-server.cmd`。

打开 `http://<服务器IP>:4173/` 就是原版交易室界面。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MONETA_HOST` | `0.0.0.0` | 监听地址 |
| `MONETA_PORT` | `4173` | 监听端口 |
| `MONETA_DATA_DIR` | `./server/data` | 共享市场存档目录 |
| `MONETA_DEVELOPER_KEY` | `moneta-dev-key` | 开发者密钥 |
| `MONETA_ADMIN_PASSWORD` | 空 | 管理员面板口令；未设置时只允许本机访问 |
| `MONETA_PUBLIC_DOMAIN` | 空 | 对外域名，用于识别隧道转发请求 |
| `MONETA_INITIAL_BALANCE` | `100000` | 新账户初始资金 |
| `MONETA_TICK_MS` | `250` | 市场心跳间隔 |
| `MONETA_PERSIST_MS` | `5000` | 自动存档间隔 |

### 对外暴露（类比原版的域名服务）

原版用 `cloudflared` 把本地服务暴露到公网。移植版保留同一套思路，只是隧道工具需要自行准备：

```bash
# 示例：任选一种
cloudflared tunnel --url http://127.0.0.1:4173
# 或
ngrok http 4173
# 或直接在有公网 IP 的主机上运行，并设置
export MONETA_PUBLIC_DOMAIN=your.domain
```

服务端检测到 `Host` 等于 `MONETA_PUBLIC_DOMAIN`、来源是回环、带 `X-Forwarded-Proto: https` 和合法 `CF-Connecting-IP` 时，按隧道转发请求处理，Cookie 会加 `Secure`。

---

## 四、两种模式

| 模式 | 配置 | 行情来源 | 账户保存 |
| --- | --- | --- | --- |
| 本地离线（默认） | `server_url` 留空 | 插件进程内生成 | N.E.K.O. 插件存储 |
| 云端共享 | `server_url` + `use_cloud = true` | 服务端统一生成 | 服务端 `MONETA_DATA_DIR` |

云端模式下，N.E.K.O. 面板与浏览器端访问同一个世界：同一账户、同一持仓、同一行情，
开发者控制台与资金调度在两端都可用。

---

## 五、交易规则（与原版一致）

- **杠杆** 1:10 / 1:20 / 1:30 / 1:50 / 1:100 / 1:200 / 1:500，仅空仓且无挂单时可改。
- **点差** 按货币对动态计算：主要货币对 1.2 点基准，自选 2.4，其他 6， JPY 报价 >100 的 80；可用倍率 0.5–10 调整。
- **滑点** 市价成交在 0 至上限之间随机产生不利滑点（默认 0.3 点）。
- **佣金** 每手每边收取一次（默认 3 USD），部分平仓按实际手数收取。
- **隔夜费** UTC 22:00 结算，周三三倍，周末不结算；多单默认 -6、空单 +1.5 USD/手。
- **保证金** `手数 × 100000 × 基础货币USD价值 / 杠杆`；可用保证金不足拒绝成交。
- **强平** 保证金水平低于 50% 时，按浮动盈亏从亏到盈依次强平。
- **挂单** 限价单与突破单方向规则与原版一致；触发后按挂单价成交（限价单取更优价）。
- **幂等** 每笔交易带 `requestId`，同一编号重复提交不会重复执行。

---

## 六、开发者控制台

在共享模式登录开发者账户后可见：

- **直接调整价格**：单次可调至当前价的 50%–200%，相关交叉汇率联动。
- **编排走势**：以当前价为起点，保留过程波动，在指定模拟秒内到达目标（幅度 ±30%，时长 5 秒–24 小时）。
- **时间与波动**：1× / 5× / 20× / 100× 倍速；暂停时钟（仍可交易）；波动强度 1–20。
- **虚构事件**：美元突然走强、欧元利好、日元干预冲击、流动性风暴。
- **账户管理**：查看普通账户、持仓、挂单、成交历史、操作记录；入金 / 扣款 / 转账，全部留痕。

---

## 七、测试

```bash
# 插件单元测试（70 项，含云端客户端，无需 N.E.K.O. 环境）
uv run --python 3.12 --with pytest python -m pytest -c pytest.ini -q

# 引擎冒烟测试（41 项）
uv run --python 3.12 python smoke_test.py

# 云端服务端端到端测试（35 项，需要服务端已启动）
uv run --python 3.12 python scripts/e2e_cloud.py
```

当前状态：插件单元测试 70/70 通过，引擎冒烟 41/41 通过，云端 E2E 35/35 通过，
官方 `check-hosted-tsx` 通过，插件市场 verify + release workflow 全绿。

---

## 八、与原版的差异

| 项目 | 原版 | 移植版 |
| --- | --- | --- |
| 行情源 | Twelve Data 真实参考价 + 本地虚构 | 纯虚构（服务端或本地生成），无外部依赖 |
| 后端语言 | Node.js | Python |
| 账户体系 | scrypt + Cookie + 登录限流 | scrypt + Cookie + 登录限流（逻辑一致） |
| 停机补绘 | `resume-market.mjs` 只补图表 | 同逻辑移植到 `market.resume_market` |
| 隧道 | `cloudflared` 随包分发 | 需自行准备隧道工具 |
| UI | 原版 HTML/CSS/JS | 云端服务端 1:1 复用；插件面板为 Hosted TSX 重绘 |

---

## 九、安全提醒

- `developer_key` 与 `MONETA_ADMIN_PASSWORD` 必须在部署前修改。
- 服务端默认绑定 `0.0.0.0`，请配合防火墙或反向代理限制访问。
- 登录接口有每地址 15 次 / 10 分钟的限制；口令使用 scrypt 散列，不落明文。
- 全部数据都是模拟数据，但仍建议不要把服务端直接暴露在公网而不加认证。
