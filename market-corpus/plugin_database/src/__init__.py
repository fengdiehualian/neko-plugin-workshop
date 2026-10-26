"""plugin_database —— N.E.K.O 统一数据库插件（基础层）。

对外（跨插件）入口分四组：

* 通用 SQL：``db_execute`` / ``db_fetch_all`` / ``db_fetch_one``
* 能力与切换：``db_status`` / ``db_capabilities`` / ``db_switch_driver``
* 热数据表：``register_hot_schema`` / ``hot_promote`` / ``hot_demote`` / ``hot_query``
* Skill 统计：``skill_stat_record`` / ``skill_stat_query``

驱动由 plugin.toml 的 ``[database] driver`` 选择：sqlite（默认，零配置）/
postgres / mysql / mongo。所有入口返回 ``Ok`` / ``Err(SdkError)`` 结构化结果：
适配器把驱动异常统一转成 ``AdapterError``，入口层再兜底捕获任何参数/驱动异常，
任何失败都不会让宿主崩溃。
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, ui

from . import hot_schema
from .adapters import AdapterError
from .service import DatabaseService, encode_value, ensure_vendor_path


def _fail(exc: BaseException) -> Err:
    return Err(SdkError(str(exc)))


def _entry(fn):
    """入口兜底：任何未转成结构化错误的异常都在这里变成 ``Err``。

    适配器已把驱动异常统一包成 ``AdapterError``；这一层额外覆盖参数校验、
    依赖导入、JSON/base64 解码等入口自身可能抛出的异常，兑现 README 的
    "任何失败都是结构化错误，不会拖垮宿主"。
    """

    @functools.wraps(fn)
    async def wrapper(self, *args, **kwargs):
        try:
            return await fn(self, *args, **kwargs)
        except Exception as exc:
            return _fail(exc)

    return wrapper


@neko_plugin
class PluginDatabasePlugin(NekoPluginBase):
    """统一 DatabaseAdapter：SQLite / PostgreSQL / MySQL / MongoDB。"""

    @ui.context(id="plugin_database_panel")
    async def _ui_panel_state(self):
        """Hosted 面板状态:插件元信息 + 入口清单(生成,方向C)。"""
        return {
            'plugin': {
                'id': 'plugin_database',
                'name': 'Plugin Database',
                'version': '0.1.0',
                'description': '统一数据库访问基础层：DatabaseAdapter 抽象 + SQLite（默认零配置）/ PostgreSQL / MySQL / MongoDB 四驱动，含 hot_knowledge 热数据表、Skill 调用统计表与 vec0/FTS5 能力探测，供知识库与 Skill 插件跨插件调用。',
            },
            'entries': [
                {
                    'id': 'db_execute',
                    'name': '执行 SQL',
                    'description': '执行 DML/DDL，返回受影响行数（MongoDB 传 JSON 文档操作）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'db_fetch_all',
                    'name': '查询多行',
                    'description': '执行 SELECT，返回全部行（bytes 字段自动 base64 包装）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'db_fetch_one',
                    'name': '查询单行',
                    'description': '执行 SELECT，返回第一行或 null',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'db_transaction',
                    'name': '批量事务',
                    'description': '一次调用内在单个事务中执行整组语句，任一失败整体回滚',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'db_status',
                    'name': '数据库状态',
                    'description': '当前驱动与连接状态',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'db_capabilities',
                    'name': '驱动能力',
                    'description': '报告当前驱动的 vec0 / FTS5 能力（供知识库选择检索通道）',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'db_switch_driver',
                    'name': '切换驱动',
                    'description': '运行时切换数据库驱动（driver + 对应连接配置），切换即重连',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'register_hot_schema',
                    'name': '注册热数据表',
                    'description': '按当前驱动创建 hot_knowledge 表/集合（知识库初始化时调用）',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'hot_promote',
                    'name': '写入热知识',
                    'description': '按驱动语义 upsert 一条 hot_knowledge（去重键 content_hash）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'hot_demote',
                    'name': '冷数据降级',
                    'description': '把 last_hit_at 早于 demotion_days 天的热记录标记为 demoted',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'hot_query',
                    'name': '查询热知识',
                    'description': '按 last_hit_at 倒序查询热数据（默认不含 demoted）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'skill_stat_record',
                    'name': '记录 Skill 调用',
                    'description': '累加一次 Skill 调用统计（次数、错误、耗时）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'skill_stat_query',
                    'name': '查询 Skill 统计',
                    'description': '返回各 Skill 的调用次数 / 错误数 / 平均耗时',
                    'has_required': False,
                    'has_params': False
                },
            ],
        }

    def __init__(self, ctx):
        super().__init__(ctx)
        self._service: DatabaseService | None = None
        # startup 解析好的 [database.sqlite] 段（含 vec0_path / 默认库路径）。
        # db_switch_driver 只覆盖调用方显式给出的键，其余保持启动时的值。
        self._sqlite_cfg: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        cfg = await self.config.dump()
        db_cfg = dict(cfg.get("database") or {})
        driver = str(db_cfg.get("driver", "sqlite")).lower()
        sqlite_cfg = dict(db_cfg.get("sqlite") or {})
        if driver == "sqlite":
            default_vec0 = self.plugin_dir / "vendor" / "sqlite_vec" / "vec0.dll"
            # 语义：vec0_path 缺失或留空 = 自动（vendor 里的 vec0.dll，若存在）；
            # "none" = 显式禁用；其他值 = 路径（相对路径按插件目录解析）
            if "vec0_path" not in sqlite_cfg or not str(sqlite_cfg["vec0_path"]).strip():
                if default_vec0.exists():
                    sqlite_cfg["vec0_path"] = str(default_vec0)
                else:
                    sqlite_cfg.pop("vec0_path", None)
            elif str(sqlite_cfg["vec0_path"]).strip().lower() == "none":
                sqlite_cfg.pop("vec0_path")  # 显式禁用
            else:
                vp = Path(str(sqlite_cfg["vec0_path"]))
                if not vp.is_absolute():
                    # 模板里的相对路径按插件目录解析（进程 CWD 不可依赖）
                    sqlite_cfg["vec0_path"] = str(self.plugin_dir / vp)
            if not sqlite_cfg.get("path"):
                sqlite_cfg["path"] = str(self.storage_dir / "data" / "plugin_database.db")
        self._service = DatabaseService(self.storage_dir / "data")
        self._sqlite_cfg = sqlite_cfg
        ensure_vendor_path(self.plugin_dir)
        try:
            info = await self._service.start({**db_cfg, "sqlite": sqlite_cfg})
        except AdapterError as exc:
            return _fail(exc)
        except Exception as exc:
            return _fail(exc)
        try:
            await self._ensure_skill_stats()
        except Exception as exc:
            return _fail(exc)
        self.logger.info("database ready: driver={}", self._service.driver)
        return Ok({"driver": self._service.driver, **info})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        if self._service is not None:
            await self._service.stop()
            self._service = None
        return Ok({"status": "closed"})

    def _svc(self) -> DatabaseService | None:
        return self._service

    # ------------------------------------------------------------------
    # 通用 SQL
    # ------------------------------------------------------------------


    @ui.action(label="执行 SQL", refresh_context=True)

    @plugin_entry(
        id="db_execute",
        name="执行 SQL",
        description="执行 DML/DDL，返回受影响行数（MongoDB 传 JSON 文档操作）",
        timeout=30.0,
    )
    @_entry
    async def db_execute(self, sql: str = "", params: list | None = None, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        try:
            n = await svc.execute(sql, params or [])
        except AdapterError as exc:
            return _fail(exc)
        return Ok({"rowcount": n})


    @ui.action(label="查询多行", refresh_context=True)

    @plugin_entry(
        id="db_fetch_all",
        name="查询多行",
        description="执行 SELECT，返回全部行（bytes 字段自动 base64 包装）",
        timeout=30.0,
    )
    @_entry
    async def db_fetch_all(self, sql: str = "", params: list | None = None, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        try:
            rows = await svc.fetch_all(sql, params or [])
        except AdapterError as exc:
            return _fail(exc)
        return Ok({"rows": rows, "count": len(rows)})


    @ui.action(label="查询单行", refresh_context=True)

    @plugin_entry(
        id="db_fetch_one",
        name="查询单行",
        description="执行 SELECT，返回第一行或 null",
        timeout=30.0,
    )
    @_entry
    async def db_fetch_one(self, sql: str = "", params: list | None = None, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        try:
            row = await svc.fetch_one(sql, params or [])
        except AdapterError as exc:
            return _fail(exc)
        return Ok({"row": row})

    # ------------------------------------------------------------------
    # 能力与切换
    # ------------------------------------------------------------------


    @ui.action(label="批量事务", refresh_context=True)

    @plugin_entry(
        id="db_transaction",
        name="批量事务",
        description="一次调用内在单个事务中执行整组语句，任一失败整体回滚",
        timeout=60.0,
    )
    @_entry
    async def db_transaction(self, statements: list | None = None, **_):
        """statements: [{"sql": str, "params": [..]}, ...]。

        跨插件 IPC 无法传递事务句柄，也不提供 begin/commit/rollback 三段式
        （调用方中途失联会留下悬挂事务）；需要多条语句的原子写入请整组打包
        到本入口。MongoDB 不支持 SQL 事务，返回结构化 Err（原子性用单文档
        原子操作/replace upsert）。
        """
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        stmts = list(statements or [])
        if not stmts:
            return Err(SdkError("statements is required"))
        if svc.driver == "mongo":
            return Err(SdkError(
                "mongo adapter does not support SQL transactions; "
                "use single-document atomic operations"
            ))
        from .service import decode_params

        rowcounts: list[int] = []
        try:
            async with await svc.adapter.transaction():
                for st in stmts:
                    if not isinstance(st, dict) or not st.get("sql"):
                        raise AdapterError("each statement needs a 'sql' key")
                    n = await svc.adapter.execute(
                        str(st["sql"]), decode_params(st.get("params"))
                    )
                    rowcounts.append(n)
        except AdapterError as exc:
            return _fail(exc)
        return Ok({"rowcounts": rowcounts, "count": len(rowcounts)})

    @ui.action(label='数据库状态', tone="primary", refresh_context=True)

    @plugin_entry(
        id="db_status", name="数据库状态", description="当前驱动与连接状态"
    )
    @_entry
    async def db_status(self, **_):
        svc = self._svc()
        if svc is None or svc.adapter is None:
            return Ok({"driver": None, "connected": False})
        return Ok({"driver": svc.driver, "connected": True})

    @ui.action(label='驱动能力', tone="primary", refresh_context=True)

    @plugin_entry(
        id="db_capabilities",
        name="驱动能力",
        description="报告当前驱动的 vec0 / FTS5 能力（供知识库选择检索通道）",
    )
    @_entry
    async def db_capabilities(self, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        caps = {"driver": svc.driver, "vec0": False, "fts5": False, "vec0_error": None}
        if svc.driver == "sqlite":
            caps["vec0_error"] = getattr(svc.adapter, "_vec0_error", None)
            # 探测建在 temp schema（连接级，断开即消失），不往用户库里留 _cap_* 残留。
            for key, ddl in (
                ("fts5", "CREATE VIRTUAL TABLE IF NOT EXISTS temp._cap_fts USING fts5(c)"),
                ("vec0", "CREATE VIRTUAL TABLE IF NOT EXISTS temp._cap_vec USING vec0(e float[4])"),
            ):
                try:
                    await svc.adapter.execute(ddl, ())
                    caps[key] = True
                except Exception:
                    # 模块缺失 / 无扩展加载支持 -> 能力为 False，仍返回 Ok
                    caps[key] = False
        return Ok(caps)


    @ui.action(label="切换驱动", refresh_context=True)

    @plugin_entry(
        id="db_switch_driver",
        name="切换驱动",
        description="运行时切换数据库驱动（driver + 对应连接配置），切换即重连",
        timeout=30.0,
    )
    @_entry
    async def db_switch_driver(self, config: dict | None = None, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        cfg = dict(config or {})
        if not cfg.get("driver"):
            return Err(SdkError("config.driver is required (sqlite/postgres/mysql/mongo)"))
        driver = str(cfg["driver"]).strip().lower()
        if driver == "sqlite":
            # 与 startup 解析好的 sqlite 段合并：只覆盖调用方显式给出的键，
            # 保住 vec0_path / 默认库路径，否则切到 sqlite 后 vec0 会静默失效。
            merged = {**self._sqlite_cfg, **dict(cfg.get("sqlite") or {})}
            cfg = {**cfg, "sqlite": merged}
        try:
            info = await svc.start(cfg)
        except AdapterError as exc:
            return _fail(exc)
        except Exception as exc:
            return _fail(exc)
        # 新库需要与 startup 相同的 schema 初始化，否则切换后
        # skill_stat_record / hot_promote / hot_query 会因缺表而失败。
        try:
            await self._ensure_skill_stats()
            await hot_schema.register_hot_schema(svc.adapter)
        except Exception as exc:
            # 驱动已切成功，但 schema 没建起来：如实报告，不回滚连接。
            return _fail(exc)
        return Ok({"driver": svc.driver, **info})

    # ------------------------------------------------------------------
    # 热数据表（schema 注册由知识库插件初始化时调用）
    # ------------------------------------------------------------------

    @ui.action(label='注册热数据表', tone="primary", refresh_context=True)

    @plugin_entry(
        id="register_hot_schema",
        name="注册热数据表",
        description="按当前驱动创建 hot_knowledge 表/集合（知识库初始化时调用）",
        timeout=30.0,
    )
    @_entry
    async def register_hot_schema(self, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        try:
            info = await hot_schema.register_hot_schema(svc.adapter)
        except AdapterError as exc:
            return _fail(exc)
        except Exception as exc:
            return _fail(exc)
        return Ok(info)


    @ui.action(label="写入热知识", refresh_context=True)

    @plugin_entry(
        id="hot_promote",
        name="写入热知识",
        description="按驱动语义 upsert 一条 hot_knowledge（去重键 content_hash）",
        timeout=30.0,
    )
    @_entry
    async def hot_promote(
        self,
        source: str = "knowledge_base",
        content_hash: str = "",
        content: str = "",
        embedding: dict | list | bytes | None = None,
        hit_count: int = 0,
        first_hit_at: str = "",
        last_hit_at: str = "",
        embedder: str = "",
        **_,
    ):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        if not content_hash or not content:
            return Err(SdkError("content_hash and content are required"))
        now = hot_schema.now_iso_text()
        try:
            emb = _normalize_embedding(embedding)
            params = (
                source,
                content_hash,
                content,
                emb,
                int(hit_count),
                first_hit_at or now,
                last_hit_at or now,
                now,
                str(embedder or ""),
            )
        except (AdapterError, ValueError, TypeError) as exc:
            return _fail(exc)
        driver = svc.driver
        try:
            if driver == "sqlite":
                n = await svc.adapter.execute(
                    "INSERT OR REPLACE INTO hot_knowledge (source, content_hash, content,"
                    " embedding, hit_count, first_hit_at, last_hit_at, promoted_at, status, embedder)"
                    " VALUES (?,?,?,?,?,?,?,?, 'active', ?)",
                    params,
                )
            elif driver == "postgres":
                n = await svc.adapter.execute(
                    "INSERT INTO hot_knowledge (source, content_hash, content, embedding,"
                    " hit_count, first_hit_at, last_hit_at, promoted_at, status, embedder)"
                    " VALUES (?,?,?,?,?,?,?,?, 'active', ?)"
                    " ON CONFLICT (content_hash) DO UPDATE SET source = EXCLUDED.source,"
                    " content = EXCLUDED.content, embedding = EXCLUDED.embedding,"
                    " hit_count = EXCLUDED.hit_count, first_hit_at = EXCLUDED.first_hit_at,"
                    " last_hit_at = EXCLUDED.last_hit_at, promoted_at = EXCLUDED.promoted_at,"
                    " status = 'active', embedder = EXCLUDED.embedder",
                    params,
                )
            elif driver == "mysql":
                n = await svc.adapter.execute(
                    "REPLACE INTO hot_knowledge (source, content_hash, content, embedding,"
                    " hit_count, first_hit_at, last_hit_at, promoted_at, status, embedder)"
                    " VALUES (?,?,?,?,?,?,?,?, 'active', ?)",
                    params,
                )
            elif driver == "mongo":
                doc = {
                    "source": source,
                    "content_hash": content_hash,
                    "content": content,
                    "embedding": _mongo_safe(embedding),
                    "hit_count": int(hit_count),
                    "first_hit_at": first_hit_at or now,
                    "last_hit_at": last_hit_at or now,
                    "promoted_at": now,
                    "status": "active",
                    "embedder": str(embedder or ""),
                }
                n = await svc.adapter.execute(
                    json.dumps(
                        {
                            "op": "replace_one",
                            "collection": hot_schema.MONGO_COLLECTION,
                            "filter": {"content_hash": content_hash},
                            "replacement": doc,
                            "upsert": True,
                        }
                    ),
                    (),
                )
            else:
                return Err(SdkError(f"unknown driver {driver!r}"))
        except AdapterError as exc:
            return _fail(exc)
        return Ok({"promoted": content_hash, "rowcount": n, "driver": driver})


    @ui.action(label="冷数据降级", refresh_context=True)

    @plugin_entry(
        id="hot_demote",
        name="冷数据降级",
        description="把 last_hit_at 早于 demotion_days 天的热记录标记为 demoted",
        timeout=30.0,
    )
    @_entry
    async def hot_demote(self, demotion_days: int = 30, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        try:
            cutoff = hot_schema.cutoff_iso(int(demotion_days))
            if svc.driver == "mongo":
                n = await svc.adapter.execute(hot_schema.mongo_demote_op(cutoff), ())
            else:
                sql, prm = hot_schema.demote_sql(svc.driver, cutoff)
                n = await svc.adapter.execute(sql, prm)
        except AdapterError as exc:
            return _fail(exc)
        except (TypeError, ValueError) as exc:  # 非数字 demotion_days -> 结构化 Err
            return _fail(exc)
        except Exception as exc:
            return _fail(exc)
        return Ok({"demoted": n, "cutoff": cutoff})


    @ui.action(label="查询热知识", refresh_context=True)

    @plugin_entry(
        id="hot_query",
        name="查询热知识",
        description="按 last_hit_at 倒序查询热数据（默认不含 demoted）",
        timeout=30.0,
    )
    @_entry
    async def hot_query(self, limit: int = 50, include_demoted: bool = False, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError) as exc:
            return _fail(exc)
        try:
            if svc.driver == "mongo":
                flt = {} if include_demoted else {"status": "active"}
                rows = await svc.adapter.fetch_all(
                    json.dumps(
                        {
                            "op": "find",
                            "collection": hot_schema.MONGO_COLLECTION,
                            "filter": flt,
                            "sort": [["last_hit_at", -1]],
                            "limit": limit,
                        }
                    ),
                    (),
                )
            else:
                where = "" if include_demoted else " WHERE status = 'active'"
                rows = await svc.adapter.fetch_all(
                    "SELECT id, source, content_hash, content, embedding, hit_count,"
                    " first_hit_at, last_hit_at, promoted_at, status, embedder, created_at"
                    f" FROM hot_knowledge{where} ORDER BY last_hit_at DESC LIMIT ?",
                    (limit,),
                )
        except AdapterError as exc:
            return _fail(exc)
        return Ok({"rows": [encode_value(r) for r in rows], "count": len(rows)})

    # ------------------------------------------------------------------
    # Skill 调用统计（skill 插件经知识库插件转发写入）
    # ------------------------------------------------------------------

    async def _ensure_skill_stats(self) -> None:
        svc = self._svc()
        if svc is None:
            return
        if svc.driver == "mongo":
            await svc.adapter.execute(
                json.dumps(
                    {
                        "op": "create_index",
                        "collection": "skill_stats",
                        "keys": {"skill": 1},
                        "unique": True,
                    }
                ),
                (),
            )
            return
        dialect = {
            "sqlite": "CREATE TABLE IF NOT EXISTS skill_stats ("
            " skill TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,"
            " errors INTEGER NOT NULL DEFAULT 0, total_ms REAL NOT NULL DEFAULT 0,"
            " last_called_at TEXT, last_ok INTEGER NOT NULL DEFAULT 1)",
            "postgres": "CREATE TABLE IF NOT EXISTS skill_stats ("
            " skill TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0,"
            " errors INTEGER NOT NULL DEFAULT 0, total_ms DOUBLE PRECISION NOT NULL DEFAULT 0,"
            " last_called_at TEXT, last_ok INTEGER NOT NULL DEFAULT 1)",
            "mysql": "CREATE TABLE IF NOT EXISTS skill_stats ("
            " skill VARCHAR(64) PRIMARY KEY, calls INT NOT NULL DEFAULT 0,"
            " errors INT NOT NULL DEFAULT 0, total_ms DOUBLE NOT NULL DEFAULT 0,"
            " last_called_at VARCHAR(40), last_ok INT NOT NULL DEFAULT 1)",
        }
        stmt = dialect.get(svc.driver)
        if stmt:
            await svc.adapter.execute(" ".join(stmt.split()), ())


    @ui.action(label="记录 Skill 调用", refresh_context=True)

    @plugin_entry(
        id="skill_stat_record",
        name="记录 Skill 调用",
        description="累加一次 Skill 调用统计（次数、错误、耗时）",
        timeout=30.0,
    )
    @_entry
    async def skill_stat_record(
        self, skill: str = "", duration_ms: float = 0.0, ok: bool = True, **_
    ):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        if not skill:
            return Err(SdkError("skill name is required"))
        now = hot_schema.now_iso_text()
        try:
            if svc.driver == "mongo":
                n = await svc.adapter.execute(
                    json.dumps(
                        {
                            "op": "update_one",
                            "collection": "skill_stats",
                            "filter": {"skill": skill},
                            "update": {
                                "$inc": {
                                    "calls": 1,
                                    "errors": 0 if ok else 1,
                                    "total_ms": float(duration_ms),
                                },
                                "$set": {"last_called_at": now, "last_ok": bool(ok)},
                            },
                            "upsert": True,
                        }
                    ),
                    (),
                )
            else:
                # 幂等 upsert：先插入占位，再累加
                if svc.driver == "sqlite":
                    await svc.adapter.execute(
                        "INSERT OR IGNORE INTO skill_stats (skill) VALUES (?)", (skill,)
                    )
                elif svc.driver == "postgres":
                    await svc.adapter.execute(
                        "INSERT INTO skill_stats (skill) VALUES (?)"
                        " ON CONFLICT (skill) DO NOTHING",
                        (skill,),
                    )
                else:
                    await svc.adapter.execute(
                        "INSERT IGNORE INTO skill_stats (skill) VALUES (?)", (skill,)
                    )
                n = await svc.adapter.execute(
                    "UPDATE skill_stats SET calls = calls + 1,"
                    " errors = errors + ?, total_ms = total_ms + ?,"
                    " last_called_at = ?, last_ok = ? WHERE skill = ?",
                    (0 if ok else 1, float(duration_ms), now, 1 if ok else 0, skill),
                )
        except AdapterError as exc:
            return _fail(exc)
        return Ok({"skill": skill, "rowcount": n})

    @ui.action(label='查询 Skill 统计', tone="primary", refresh_context=True)

    @plugin_entry(
        id="skill_stat_query",
        name="查询 Skill 统计",
        description="返回各 Skill 的调用次数 / 错误数 / 平均耗时",
        timeout=30.0,
    )
    @_entry
    async def skill_stat_query(self, **_):
        svc = self._svc()
        if svc is None:
            return Err(SdkError("database plugin not started"))
        try:
            if svc.driver == "mongo":
                rows = await svc.adapter.fetch_all(
                    json.dumps({"op": "find", "collection": "skill_stats", "sort": [["calls", -1]]}),
                    (),
                )
            else:
                rows = await svc.adapter.fetch_all(
                    "SELECT skill, calls, errors, total_ms, last_called_at, last_ok"
                    " FROM skill_stats ORDER BY calls DESC",
                    (),
                )
        except AdapterError as exc:
            return _fail(exc)
        out = []
        for r in rows:
            calls = int(r.get("calls") or 0)
            total = float(r.get("total_ms") or 0)
            out.append(
                {
                    **encode_value(r),
                    "avg_ms": round(total / calls, 2) if calls else 0.0,
                }
            )
        return Ok({"rows": out, "count": len(out)})


def _normalize_embedding(value: Any) -> Any:
    """sqlite/pg/mysql 存 BLOB：b64 包装 dict / list[float] / bytes 统一为 bytes。"""
    if value is None:
        return None
    if isinstance(value, dict) and "__bytes_b64__" in value:
        import base64

        return base64.b64decode(value["__bytes_b64__"])
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, list):
        import struct

        return struct.pack(f"<{len(value)}f", *[float(x) for x in value])
    raise AdapterError("embedding must be bytes / {'__bytes_b64__': ...} / list[float]")


def _mongo_safe(value: Any) -> Any:
    """embedding（bytes/b64 包装/list）转成 mongo 文档可存的 list[float]。"""
    if value is None:
        return None
    if isinstance(value, dict) and "__bytes_b64__" in value:
        import base64
        import struct

        raw = base64.b64decode(value["__bytes_b64__"])
        return list(struct.unpack(f"<{len(raw) // 4}f", raw))
    if isinstance(value, (bytes, bytearray)):
        import struct

        return list(struct.unpack(f"<{len(value) // 4}f", bytes(value)))
    return list(value)
