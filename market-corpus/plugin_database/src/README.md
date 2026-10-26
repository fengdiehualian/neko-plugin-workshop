# plugin_database

N.E.K.O 统一数据库插件（基础层）。提供 `DatabaseAdapter` 抽象与四个完整驱动实现，
默认 SQLite 零配置运行；切换 PostgreSQL / MySQL / MongoDB 只需改 `plugin.toml` 的
`[database] driver` 与对应连接段。

## 架构

```
plugin_database（本插件，基础层）
├── adapters/base.py            DatabaseAdapter 抽象 + AdapterError + 事务骨架
├── adapters/sqlite_adapter.py  aiosqlite 单连接 + WAL（默认）
├── adapters/postgres_adapter.py asyncpg 连接池（? -> $n 转译）
├── adapters/mysql_adapter.py   aiomysql 连接池 + DictCursor（? -> %s 转译）
├── adapters/mongo_adapter.py   motor 文档模型（JSON 操作协议，不映射关系表）
├── hot_schema.py               hot_knowledge 多方言 DDL + 降级 SQL + Mongo 索引
└── service.py                  驱动选择 / bytes<->base64 编解码 / vendor 注入
```

## 接口（DatabaseAdapter）

```python
await adapter.connect(config)            # 建连接/池
n = await adapter.execute(sql, params)   # DML，返回受影响行数
rows = await adapter.fetch_all(sql, params)   # -> list[dict]
row = await adapter.fetch_one(sql, params)    # -> dict | None
async with await adapter.transaction():  # 异常回滚 / 正常提交
    ...
await adapter.close()
```

- 占位符统一写 `?`；postgres 适配器内部转 `$n`，mysql 转 `%s`，sqlite 原生支持。
- `transaction()` 是 async 方法，返回异步上下文管理器（`async with await ...`）。
- MongoDB 不支持 SQL：`sql` 传 JSON 字符串编码的文档操作，见
  `adapters/mongo_adapter.py` 模块 docstring（insert/find/update/delete/count/
  create_index）。

## 入口（跨插件调用）

| 入口 | 说明 |
|------|------|
| `db_execute` / `db_fetch_all` / `db_fetch_one` | 通用 SQL（params 中的 bytes 用 `{"__bytes_b64__": ...}` 包装） |
| `db_status` / `db_capabilities` / `db_switch_driver` | 状态 / vec0+FTS5 能力探测 / 运行时切换驱动 |
| `register_hot_schema` | 按当前驱动创建 `hot_knowledge` 表/集合（知识库初始化时调用） |
| `hot_promote` | 按 driver 语义 upsert 一条热知识（`content_hash` 去重） |
| `hot_demote` | 把 `last_hit_at` 早于 N 天的记录标记 `demoted` |
| `hot_query` | 按 `last_hit_at` 倒序查询（默认不含 demoted） |
| `skill_stat_record` / `skill_stat_query` | Skill 调用统计（次数/错误/耗时） |

所有入口返回 `Ok` / `Err(SdkError)`，任何驱动失败都是结构化错误，不会拖垮宿主。

## 配置（config.example.toml）

```toml
[database]
driver = "sqlite"            # sqlite | postgres | mysql | mongo

[database.sqlite]
path = ""                    # 留空 = <插件数据>/data/plugin_database.db
vec0_path = "vendor/sqlite_vec/vec0.dll"   # sqlite-vec 扩展；空串禁用

[database.postgres]
dsn = "postgresql://user:pass@127.0.0.1:5432/neko"

[database.mysql]
dsn = "mysql://user:pass@127.0.0.1:3306/neko"

[database.mongo]
uri = "mongodb://127.0.0.1:27017"
database = "neko"
```

## 依赖

`pyproject.toml` 声明，`uv run neko-plugin sync plugin_database` 同步到 `vendor/`
（宿主运行时由插件自行把 vendor 加入 sys.path）。宿主自带的 onnxruntime /
numpy / pyyaml 不在本插件依赖里。

> **克隆本仓库后 `vendor/` 是空的**（`.gitignore` 排除，其中含 `vec0.dll`、
> asyncpg 的 cp311 二进制等约 16 MB 平台相关文件）。补齐方式：
>
> ```bash
> uv run neko-plugin sync plugin_database
> ```
>
> 若 `vendor/` 缺失，插件仍能启动（`ensure_vendor_path` 只是把它加进 `sys.path`，
> 不存在就跳过），但非 SQLite 驱动与 **sqlite-vec（vec0）** 会不可用 ——
> 后者会让向量检索退化为 numpy 余弦扫描。`db_capabilities` 会如实报
> `vec0` / `fts5` 的可用性，可用来确认补齐是否成功。
>
> 注意 vec0 是 `cp311-win_amd64` 二进制：`pyproject.toml` 因此把
> `requires-python` 收紧到 `>=3.11,<3.12`，3.12+ 上 ABI 不匹配会静默失效。

## 测试

```bash
uv run neko-plugin sync plugin_database   # 先补齐 vendor/，否则见下
python -m pytest tests -q
python tests/stub_run.py                  # 桩链路检查（16 项）
```

> **`vendor/` 缺失时测试会失败**，报 `ModuleNotFoundError: No module named
> 'aiosqlite'`（`tests/conftest.py` 把 `vendor/` 加进 `sys.path`，
> 见其文件头说明）。这不是测试本身的问题 —— SQLite 适配器依赖 `aiosqlite`，
> 而它随 `vendor/` 分发。先跑一次 `neko-plugin sync` 即可。

## hot_knowledge 表结构

任务书 schema + 一处显式扩展：`status TEXT NOT NULL DEFAULT 'active'`
（`'demoted'` 表示冷数据降级标记，数据保留但不参与热查询）。
PostgreSQL / MySQL / MongoDB 方言见 `hot_schema.py`。

## 测试

```bash
uv run pytest plugin/plugins/plugin_database/tests -q   # 单元 + SQLite 全链路
python plugin/plugins/plugin_database/tests/stub_run.py # 桩 SDK 驱动入口层端到端
```

PostgreSQL / MySQL / MongoDB 的真实服务 CRUD 测试默认跳过，设环境变量启用：

```bash
NEKO_TEST_PG_DSN=... NEKO_TEST_MYSQL_DSN=... \
NEKO_TEST_MONGO_URI=... NEKO_TEST_MONGO_DB=...
```

## 已知边界

- MySQL 提供 params 时，SQL 中的字面 `%` 需写成 `%%`（PyMySQL 规则）。
- 事务：`async with await adapter.transaction():` 期间，本适配器的
  execute/fetch 经 contextvar 自动路由到事务连接（pg/mysql 从池里钉住连接）；
  SQLite/PostgreSQL/MySQL 实测覆盖，MongoDB 多文档事务要求副本集（单机 mongod
  返回结构化 Err，原子性用单文档原子操作/replace upsert）。
- 跨插件 IPC 传不了事务句柄，也不提供 begin/commit/rollback 三段式（调用方
  中途失联会留悬挂事务）；多条语句的原子写入用 `db_transaction` 入口一次提交。
- vec0 尽力而为：加载失败（缺 DLL / 宿主 sqlite 未编译扩展支持 / ABI 不匹配）
  只记录原因并在 `db_capabilities.vec0_error` 如实上报，不阻止启动；知识库自动
  退化 numpy 余弦扫描。仅显式 `require_vec0 = true` 时才视为致命。
- 相对 `vec0_path` 按插件目录解析（不依赖进程 CWD）。
