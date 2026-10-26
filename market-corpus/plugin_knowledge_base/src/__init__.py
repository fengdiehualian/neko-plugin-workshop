"""plugin_knowledge_base —— N.E.K.O 知识库插件（应用层）。

流程：文件导入 -> 分块（512 tokens / 重叠 64）-> 向量化（all-MiniLM-L6-v2 ONNX）
-> 索引存储（vec0 + FTS5，sqlite 驱动）-> 语义搜索（向量+BM25 RRF 融合）
-> 滑动窗口热度追踪 -> 触发数据库插件的 hot_knowledge 提升。

跨插件依赖：所有持久化经 ``plugin_database`` 入口完成；本插件不直接持有数据库
连接（numpy 余弦扫描所需的向量 BLOB 也从数据库入口读取）。

Skill 插件的统计与查询也经本插件转发到数据库插件（Skill -> KB -> DB 链路）。
"""

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, ui

from .engine import (
    EmbedderError,
    HotConfig,
    OnnxEmbedder,
    StoreError,
    hybrid_search,
    import_document,
    init_index,
    read_document,
    rebuild_index,
    scan_directory,
    search_with_hot_tracking,
)

# 嵌入器真正需要的文件（ready 的判据），与下载清单 MODEL_FILES 区分：
# 后者多含 tokenizer_config/special_tokens_map/config，缺它们不影响推理。
from .engine.embedder import MODEL_FILES as REQUIRED_MODEL_FILES

DEFAULT_MODEL = "all-MiniLM-L6-v2"
DEFAULT_MIRROR = "https://hf-mirror.com"
_DOWNLOAD_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
"""下载模型时显式携带的 UA。

hf-mirror 对 urllib 默认 UA（``Python-urllib/3.x``）在部分文件上直接回 403
Forbidden（实测 ``tokenizer.json`` / ``config.json`` 命中，``model.onnx`` 反而不
受影响），显式带普通浏览器 UA 即可稳定 200。
"""
MODEL_FILES = {
    "model.onnx": "onnx/model.onnx",
    "tokenizer.json": "tokenizer.json",
    "tokenizer_config.json": "tokenizer_config.json",
    "special_tokens_map.json": "special_tokens_map.json",
    "config.json": "config.json",
}


def _fail(exc: BaseException) -> Err:
    return Err(SdkError(str(exc)))


def ensure_vendor_path(plugin_dir) -> bool:
    """把 ``<plugin_dir>/vendor`` 加进 sys.path（幂等）。

    与 plugin_database.service.ensure_vendor_path 同一约定（两个插件各自
    持有一份，避免跨插件导入宿主内部模块）。必须在 pypdf 等惰性 import
    之前执行——本插件的 pypdf 在 read_document() 里才加载。
    """
    import sys

    vendor = Path(plugin_dir) / "vendor"
    if not vendor.is_dir():
        return False
    s = str(vendor)
    if s not in sys.path:
        sys.path.insert(0, s)
    return True


class DbEntryClient:
    """把数据库插件的入口包装成引擎需要的 DbClient 接口。"""

    def __init__(self, plugins, target: str, logger=None) -> None:
        self._plugins = plugins
        self._target = target
        self._log = logger

    async def _call(self, entry: str, payload: dict | None = None):
        result = await self._plugins.call_entry(f"{self._target}:{entry}", payload or {})
        if isinstance(result, Ok):
            return result.value
        raise StoreError(f"{self._target}:{entry} -> {result.error}")

    async def execute(self, sql: str, params=()) -> int:
        out = await self._call(
            "db_execute", {"sql": sql, "params": [_wrap(p) for p in params]}
        )
        return int(out.get("rowcount", 0))

    async def fetch_all(self, sql: str, params=()) -> list[dict]:
        out = await self._call(
            "db_fetch_all", {"sql": sql, "params": [_wrap(p) for p in params]}
        )
        return [_unwrap_row(r) for r in out.get("rows", [])]

    async def fetch_one(self, sql: str, params=()) -> dict | None:
        out = await self._call(
            "db_fetch_one", {"sql": sql, "params": [_wrap(p) for p in params]}
        )
        return _unwrap_row(out.get("row")) if out.get("row") is not None else None

    async def capabilities(self) -> dict:
        return await self._call("db_capabilities")

    async def register_hot_schema(self) -> dict:
        """任务书要求：热数据表 Schema 由知识库插件在初始化时注册。"""
        return await self._call("register_hot_schema")


def _wrap(value):
    return {"__bytes_b64__": base64.b64encode(value).decode("ascii")} if isinstance(
        value, (bytes, bytearray)
    ) else value


def _unwrap_row(row: dict | None) -> dict | None:
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if isinstance(v, dict) and set(v) == {"__bytes_b64__"}:
            out[k] = base64.b64decode(v["__bytes_b64__"])
        else:
            out[k] = v
    return out


@neko_plugin
class PluginKnowledgeBasePlugin(NekoPluginBase):
    """知识库：导入 / 检索 / 热度提升。"""

    @ui.context(id="plugin_knowledge_base_panel")
    async def _ui_panel_state(self):
        """Hosted 面板状态:插件元信息 + 入口清单(生成,方向C)。"""
        return {
            'plugin': {
                'id': 'plugin_knowledge_base',
                'name': 'Plugin Knowledge Base',
                'version': '0.1.0',
                'description': '知识库：导入 .md/.txt/.pdf 文件，按段落分块（默认 240 tokens / 重叠 64，可配置；MiniLM 窗口 256），用 all-MiniLM-L6-v2（ONNX，384 维，CPU）向量化，vec0 + FTS5 混合检索（向量余弦 + BM25，RRF 融合）；滑动窗口热度追踪在窗口内命中达到阈值时自动把知识块提升到数据库插件的 hot_knowledge 表。',
            },
            'entries': [
                {
                    'id': 'kb_import',
                    'name': '导入知识文件',
                    'description': '扫描目录（默认 <数据>/knowledge/）导入 .md/.txt/.pdf 并建立索引',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'kb_search',
                    'name': '语义搜索知识库',
                    'description': '向量 + BM25 混合检索 Top-K；命中会计入滑动窗口热度',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'kb_hot_list',
                    'name': '查询热知识',
                    'description': '列出 hot_knowledge 中的高频知识（转发数据库插件）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'kb_hot_export',
                    'name': '导出热知识',
                    'description': '把热数据导出为 Markdown / JSON 文件（默认写到插件数据目录 exports/）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'kb_stats',
                    'name': '知识库统计',
                    'description': '索引块数与热知识条数',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'kb_demote_sweep',
                    'name': '冷数据降级扫描',
                    'description': '立即执行一次降级扫描',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'kb_rebuild',
                    'name': '重建索引',
                    'description': '清空并按当前嵌入模型重建索引（切换嵌入模型后必须执行；热数据表不受影响）',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'kb_model_status',
                    'name': '嵌入模型状态',
                    'description': '检查本地嵌入模型是否就绪',
                    'has_required': False,
                    'has_params': False
                },
                {
                    'id': 'kb_model_download',
                    'name': '下载嵌入模型',
                    'description': '从镜像（默认 hf-mirror.com）下载 all-MiniLM-L6-v2 ONNX 模型（约 90MB）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'kb_record_skill_stat',
                    'name': '记录 Skill 统计',
                    'description': '转发 skill_stat_record 到数据库插件（供 skill 插件调用）',
                    'has_required': True,
                    'has_params': True
                },
                {
                    'id': 'kb_query_skill_stats',
                    'name': '查询 Skill 统计',
                    'description': '转发 skill_stat_query 到数据库插件',
                    'has_required': False,
                    'has_params': False
                },
            ],
        }

    def __init__(self, ctx):
        super().__init__(ctx)
        self._db: DbEntryClient | None = None
        self._embedder: OnnxEmbedder | None = None
        self._caps: dict = {}
        self._hot: HotConfig | None = None
        self._cfg_cache: dict = {}
        self._started = False
        # shutdown 之后本实例必须"死透"：宿主已按生命周期关掉它，
        # 任何残留调用都不得重新加载 90MB 模型、重建数据库连接（见 _load_config/_ensure_started）。
        self._shutdown = False
        import asyncio

        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def _model_dir(self) -> Path:
        """解析模型目录。**调用方必须先 ``await self._load_config()``**，
        否则 ``_cfg_cache`` 为空、会忽略用户配置的 ``models_dir``（历史缺陷：
        ``kb_model_status`` startup 前报错目录，且与 ``kb_model_download`` 自相矛盾）。
        """
        cfg = self._cfg_cache
        custom = str(cfg.get("knowledge", {}).get("models_dir", "") or "").strip()
        if custom:
            return Path(custom)
        bundled = self.plugin_dir / "models" / DEFAULT_MODEL
        if (bundled / "model.onnx").exists():
            return bundled
        return Path(self.storage_dir) / "models" / DEFAULT_MODEL

    def _model_dir_source(self) -> str:
        """与 :meth:`_model_dir` 同源的来源标签（config / bundled / storage）。"""
        custom = str(self._cfg_cache.get("knowledge", {}).get("models_dir", "") or "").strip()
        if custom:
            return "config"
        if (self.plugin_dir / "models" / DEFAULT_MODEL / "model.onnx").exists():
            return "bundled"
        return "storage"

    async def _load_config(self) -> dict:
        """按需读配置（幂等）。不触碰数据库与嵌入模型。

        ``kb_model_status`` / ``kb_model_download`` 必须在 startup 之前也给出正确
        答案，所以配置读取不能只藏在 ``_ensure_started`` 里。已 shutdown 的实例
        直接报结构化错误，避免插件在宿主生命周期之外自我复活。
        """
        if self._shutdown:
            raise StoreError(
                "knowledge base plugin is shut down; 需宿主重新启动该插件后再调用入口"
            )
        if not self._cfg_cache:
            cfg = await self.config.dump()
            self._cfg_cache = cfg or {}
            self._hot = HotConfig.from_dict(self._cfg_cache.get("hot_promotion"))
        return self._cfg_cache

    async def _require_db(self) -> DbEntryClient:
        if self._db is None:
            raise StoreError("knowledge base not started (db client missing)")
        return self._db

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        # 宿主默认 startup_failure=warn：startup 返回 Err 时进程保留但处于降级态。
        # 因此初始化做成幂等的 _ensure_started：此刻失败的（如数据库插件还没启动），
        # 会在下一次任意入口调用时自动补齐初始化。
        self._shutdown = False  # 宿主可以重启同一实例：此处解除 shutdown 闸门
        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        demoted = 0
        try:
            demoted = await self._demote_pass()
        except StoreError as exc:
            self.logger.warning("startup demotion sweep failed: {}", exc)
        return Ok({
            "index": self._caps,
            "model_dir": str(self._embedder._dir),
            "model_dir_source": self._model_dir_source(),
            "dimension": self._embedder.dimension,
            "model": self._embedder.model_id,
            "hot": {
                "window_days": self._hot.window_days,
                "threshold": self._hot.threshold,
                "demotion_days": self._hot.demotion_days,
                "demoted_at_startup": demoted,
            },
        })

    async def _ensure_started(self) -> None:
        """幂等初始化：依赖检查 -> 模型加载 -> 索引与热表注册。"""
        async with self._init_lock:
            if self._shutdown:
                raise StoreError(
                    "knowledge base plugin is shut down; 需宿主重新启动该插件后再调用入口"
                )
            if self._started and self._db is not None and self._embedder is not None:
                return
            # vendor 注册必须与自愈路径同行：宿主没走 startup 而由入口自愈时，
            # 惰性 import 的 pypdf（PDF 导入）同样要能在 sys.path 上找到。
            ensure_vendor_path(self.plugin_dir)
            await self._load_config()
            # 依赖检查走 call_entry 直接探测（注册表 PLUGIN_QUERY 在宿主繁忙时
            # 可能长时间无应答，call_entry 是跨插件主通路）
            probe = await self.plugins.call_entry(
                "plugin_database:db_capabilities", {}, timeout=15.0
            )
            if not isinstance(probe, Ok):
                raise StoreError(
                    f"dependency plugin_database not available: {probe.error}; "
                    "请先启动 plugin_database（本插件会在下次调用时自动重试）"
                )
            self._db = DbEntryClient(self.plugins, "plugin_database", self.logger)
            # 模型先于索引初始化：vec0 维度与元数据校验需要真实维度
            if self._embedder is None or not self._embedder.loaded:
                self._embedder = OnnxEmbedder(self._model_dir())
                await self._embedder.load_async()  # 90MB 模型加载不能阻塞 IPC
            if not self._embedder.loaded:
                # 兜底：绝不让未加载的嵌入器（dimension 只是类默认值 384、
                # model_id 为空）把伪造元数据写进 kb_meta。
                raise EmbedderError("embedder is not loaded; refusing to touch the index")
            self._caps = await init_index(
                self._db,
                dimension=self._embedder.dimension,
                model_id=self._embedder.model_id,
            )
            await self._db.register_hot_schema()
            self._started = True

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        async with self._init_lock:
            self._shutdown = True
            self._started = False
            self._db = None
            self._embedder = None
            self._caps = {}
        return Ok({"status": "closed"})

    async def _demote_pass(self) -> int:
        out = await self.plugins.call_entry(
            "plugin_database:hot_demote", {"demotion_days": self._hot.demotion_days}
        )
        if isinstance(out, Ok):
            return int(out.value.get("demoted", 0))
        raise StoreError(f"hot_demote -> {out.error}")

    def _promote_entry(self):
        async def _promote(payload: dict) -> None:
            payload = dict(payload)
            # 跨 IPC 传输 bytes 用 base64 包装；入库条目带上嵌入器标签
            payload["embedding"] = _wrap(payload.get("embedding"))
            payload["embedder"] = f"{self._embedder.model_id}:{self._embedder.dimension}"
            out = await self.plugins.call_entry("plugin_database:hot_promote", payload)
            if not isinstance(out, Ok):
                raise StoreError(f"hot_promote -> {out.error}")

        return _promote

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------


    @ui.action(label="导入知识文件", refresh_context=True)

    @plugin_entry(
        id="kb_import",
        name="导入知识文件",
        description="扫描目录（默认 <数据>/knowledge/）导入 .md/.txt/.pdf 并建立索引",
        timeout=300.0,
    )
    async def kb_import(self, source_dir: str = "", single_file: str = "", **_):

        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        db = await self._require_db()
        default_dir = Path(self.storage_dir) / "knowledge"
        if single_file:
            candidates = [Path(single_file)]
        else:
            root = Path(source_dir) if source_dir else default_dir
            try:
                candidates = scan_directory(root)
            except FileNotFoundError as exc:
                return _fail(exc)
        if not candidates:
            return Ok({
                "imported": 0, "skipped": 0, "chunks": 0, "files": [], "failed": [],
                "note": f"no supported files in {source_dir or default_dir}",
            })
        kcfg = self._cfg_cache.get("knowledge", {}) or {}
        # 分块参数校验：chunker 要求 max_tokens > overlap，否则抛 ValueError。
        # 这里在**读文件之前**就把非法组合转成结构化 Err —— 历史缺陷是
        # chunk_tokens=100/overlap_tokens=200 这类配置一路穿到 chunker，
        # 变成未捕获的 ValueError（kb_import 只接 StoreError/EmbedderError）。
        try:
            chunk_tokens, overlap_tokens = self._chunk_params(kcfg)
        except ValueError as exc:
            return _fail(exc)
        # 逐文件读 + 逐文件导入：单个坏文件（损坏 PDF、权限、非 UTF-8 之外的问题）
        # 只记为该文件失败，不再让整批归零。历史缺陷是先列表推导全量预读，
        # 任一文件抛错就在索引任何内容之前 return Err —— 实测 3 好 + 1 坏 -> chunks=0。
        results: list[dict] = []
        failed: list[dict] = []
        for path in candidates:
            try:
                text = read_document(path)
            except (FileNotFoundError, ValueError, OSError) as exc:
                failed.append({"source": Path(path).name, "stage": "read", "error": str(exc)})
                continue
            try:
                r = await import_document(
                    db, self._embedder, source=Path(path).name, text=text,
                    max_tokens=chunk_tokens, overlap=overlap_tokens,
                )
            except (StoreError, EmbedderError, ValueError) as exc:
                # import_document 逐块 execute 已提交：此处中断会留下**部分写入**，
                # 如实报告该文件失败并附带已写入的进度，不假装整批成功也不回滚
                # （跨插件通路无事务句柄，见 plugin_database:db_transaction 的说明）。
                failed.append({
                    "source": Path(path).name, "stage": "index", "error": str(exc),
                    "partial": True,
                })
                break
            results.append(r)
        payload = {
            "imported": sum(r["inserted"] for r in results),
            "skipped": sum(r["skipped"] for r in results),
            "chunks": sum(r["chunks"] for r in results),
            "files": results,
            "failed": failed,
            "files_ok": len(results),
            "files_failed": len(failed),
        }
        if failed:
            payload["note"] = (
                f"{len(failed)} 个文件导入失败（其余 {len(results)} 个已导入）："
                + "; ".join(f"{f['source']}({f['stage']}): {f['error']}" for f in failed)
            )
        return Ok(payload)

    @staticmethod
    def _chunk_params(kcfg: dict) -> tuple[int, int]:
        """解析并校验分块参数，保证 ``max_tokens > overlap``（chunker 的硬约束）。

        历史实现只做 ``max(64,...)`` / ``max(32,...)`` 钳制，两个下界可共存
        （如 100/200），于是 ValueError 一路穿透成未捕获异常。
        """
        chunk_tokens = max(64, int(kcfg.get("chunk_tokens", 240)))
        overlap_tokens = max(32, int(kcfg.get("overlap_tokens", 64)))
        if chunk_tokens <= overlap_tokens:
            raise ValueError(
                f"invalid chunking config: chunk_tokens={chunk_tokens} must be greater than "
                f"overlap_tokens={overlap_tokens}; 请调整 [knowledge] chunk_tokens/overlap_tokens"
            )
        return chunk_tokens, overlap_tokens

    # ------------------------------------------------------------------
    # 检索（含热度追踪）
    # ------------------------------------------------------------------


    @ui.action(label="语义搜索知识库", refresh_context=True)

    @plugin_entry(
        id="kb_search",
        name="语义搜索知识库",
        description="向量 + BM25 混合检索 Top-K；命中会计入滑动窗口热度",
        timeout=60.0,
    )
    async def kb_search(self, query: str = "", top_k: int = 5, track_hot: bool = True, **_):

        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        db = await self._require_db()
        if not query.strip():
            return Err(SdkError("query is required"))
        try:
            if track_hot:
                hits = await search_with_hot_tracking(
                    db,
                    self._embedder,
                    query=query,
                    top_k=max(1, min(int(top_k), 20)),
                    use_vec0=self._caps.get("vec0", False),
                    use_fts5=self._caps.get("fts5", False),
                    hot_config=self._hot,
                    promote_entry=self._promote_entry(),
                )
            else:
                hits = await hybrid_search(
                    db,
                    self._embedder,
                    query=query,
                    top_k=max(1, min(int(top_k), 20)),
                    use_vec0=self._caps.get("vec0", False),
                    use_fts5=self._caps.get("fts5", False),
                )
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        return Ok({"query": query, "hits": hits, "count": len(hits)})

    # ------------------------------------------------------------------
    # 热知识（供 database-query / database-export Skill 使用）
    # ------------------------------------------------------------------


    @ui.action(label="查询热知识", refresh_context=True)

    @plugin_entry(
        id="kb_hot_list",
        name="查询热知识",
        description="列出 hot_knowledge 中的高频知识（转发数据库插件）",
        timeout=30.0,
    )
    async def kb_hot_list(self, limit: int = 50, include_demoted: bool = False, **_):

        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        out = await self.plugins.call_entry(
            "plugin_database:hot_query",
            {"limit": max(1, min(int(limit), 500)), "include_demoted": bool(include_demoted)},
        )
        if isinstance(out, Ok):
            return Ok(out.value)
        return _fail(StoreError(f"hot_query -> {out.error}"))


    @ui.action(label="导出热知识", refresh_context=True)

    @plugin_entry(
        id="kb_hot_export",
        name="导出热知识",
        description="把热数据导出为 Markdown / JSON 文件（默认写到插件数据目录 exports/）",
        timeout=60.0,
    )
    async def kb_hot_export(self, path: str = "", fmt: str = "md", include_demoted: bool = False, **_):

        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        out = await self.plugins.call_entry(
            "plugin_database:hot_query",
            {"limit": 500, "include_demoted": bool(include_demoted)},
        )
        if not isinstance(out, Ok):
            return _fail(StoreError(f"hot_query -> {out.error}"))
        rows = out.value.get("rows", [])
        fmt = fmt.lower()
        if fmt not in ("md", "json"):
            return Err(SdkError(f"unsupported format {fmt!r} (md|json)"))
        dest = Path(path) if path else Path(self.storage_dir) / "exports" / (
            f"hot_knowledge.{fmt}"
        )
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if fmt == "json":
                payload = [
                    {k: (v.decode("utf-8", "replace") if isinstance(v, bytes) else v) for k, v in r.items() if k != "embedding"}
                    for r in rows
                ]
                dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                lines = [f"# 导出的热知识（{len(rows)} 条）\n"]
                for i, r in enumerate(rows, 1):
                    lines.append(f"## {i}. {r.get('content_hash', '')}")
                    lines.append(
                        f"- 来源：{r.get('source', '')} ｜ 命中：{r.get('hit_count', 0)}"
                        f" ｜ 最近命中：{r.get('last_hit_at', '')} ｜ 状态：{r.get('status', '')}"
                    )
                    content = r.get("content", "")
                    lines.append("")
                    lines.append(content if isinstance(content, str) else str(content))
                    lines.append("")
                dest.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            return _fail(exc)
        return Ok({"path": str(dest), "count": len(rows), "format": fmt})

    # ------------------------------------------------------------------
    # 索引统计与模型管理
    # ------------------------------------------------------------------

    @ui.action(label='知识库统计', tone="primary", refresh_context=True)

    @plugin_entry(id="kb_stats", name="知识库统计", description="索引块数与热知识条数", timeout=30.0)
    async def kb_stats(self, **_):

        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        db = await self._require_db()
        try:
            chunks = await db.fetch_one("SELECT COUNT(*) AS n FROM kb_chunks", ())
            active = await db.fetch_one(
                "SELECT COUNT(*) AS n FROM hot_knowledge WHERE status = 'active'", ()
            )
            demoted = await db.fetch_one(
                "SELECT COUNT(*) AS n FROM hot_knowledge WHERE status = 'demoted'", ()
            )
        except StoreError as exc:
            return _fail(exc)
        return Ok({
            "index": self._caps,
            "chunks": int(chunks["n"]),
            "hot_active": int(active["n"]),
            "hot_demoted": int(demoted["n"]),
        })

    @ui.action(label='冷数据降级扫描', tone="primary", refresh_context=True)

    @plugin_entry(id="kb_demote_sweep", name="冷数据降级扫描", description="立即执行一次降级扫描", timeout=30.0)
    async def kb_demote_sweep(self, **_):

        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        try:
            n = await self._demote_pass()
        except StoreError as exc:
            return _fail(exc)
        return Ok({"demoted": n})

    @ui.action(label='重建索引', tone="primary", refresh_context=True)

    @plugin_entry(
        id="kb_rebuild",
        name="重建索引",
        description="清空并按当前嵌入模型重建索引（切换嵌入模型后必须执行；热数据表不受影响）",
        timeout=60.0,
    )
    async def kb_rebuild(self, **_):
        # 与其它入口一致：先走初始化守卫，DB 不可用 / 模型未加载都返回结构化 Err。
        # 历史缺陷：这是唯一不先 _ensure_started() 就直接 _require_db() 的入口，
        # 且不检查模型是否加载 ——
        #   (a) DB 不可用 -> StoreError 抛在 try 之外，宿主看到未处理异常；
        #   (b) DB 可用但模型未加载 -> 返回 Ok 并写入**伪造元数据**
        #       （dimension 来自类默认 384、model_id 来自空目录的目录名），
        #       把索引推进到「以别的模型建立」的不一致状态。
        try:
            await self._ensure_started()
        except (StoreError, EmbedderError) as exc:
            return _fail(exc)
        db = await self._require_db()
        embedder = self._embedder
        if embedder is None or not embedder.loaded:
            return _fail(EmbedderError(
                "embedding model is not loaded; refusing to rebuild the index with "
                "fabricated metadata. 请先让模型就绪（kb_model_status 查看，"
                "kb_model_download 下载）后重试。"
            ))
        try:
            caps = await rebuild_index(
                db,
                dimension=embedder.dimension,
                model_id=embedder.model_id,
            )
        except StoreError as exc:
            return _fail(exc)
        return Ok({"index": caps, "note": "索引已清空，请重新执行 kb_import"})

    @ui.action(label='嵌入模型状态', tone="primary", refresh_context=True)

    @plugin_entry(id="kb_model_status", name="嵌入模型状态", description="检查本地嵌入模型是否就绪")
    async def kb_model_status(self, **_):
        # 按需读配置（不加载模型、不连数据库）：startup 之前也必须按文档声明的
        # 解析顺序（config.knowledge.models_dir > 插件自带 models/ > 插件数据目录）
        # 报出**实际**使用的目录。历史缺陷是 _model_dir() 读只在 _ensure_started
        # 里赋值的 _cfg_cache，于是 startup 前忽略 models_dir、报错目录；
        # 自带 models/ 存在时还会把 ready 报成 True，与 kb_model_download 的实际
        # 失败直接矛盾。
        try:
            await self._load_config()
        except StoreError as exc:
            return _fail(exc)
        d = self._model_dir()
        missing = [f for f in REQUIRED_MODEL_FILES if not (d / f).exists()]
        return Ok({
            "model_dir": str(d),
            "model_dir_source": self._model_dir_source(),
            "ready": not missing,
            "missing_files": missing,
            "checked_files": list(REQUIRED_MODEL_FILES),
            "model": DEFAULT_MODEL,
            "dimension": 384,
        })


    @ui.action(label="下载嵌入模型", refresh_context=True)

    @plugin_entry(
        id="kb_model_download",
        name="下载嵌入模型",
        description="从镜像（默认 hf-mirror.com）下载 all-MiniLM-L6-v2 ONNX 模型（约 90MB）",
        timeout=600.0,
    )
    async def kb_model_download(self, mirror: str = DEFAULT_MIRROR, **_):
        # 刻意**不**调 _ensure_started()：它必然加载嵌入模型，而模型缺失时正好抛
        # EmbedderError —— 于是「用来下载缺失模型的入口」在唯一需要它的场景下
        # 先失败，错误信息还让用户去运行刚刚运行过的入口（实测下载 0 个文件）。
        # 本入口只需要：配置可读 + 目标目录可写（plugin_database 不是必需项，
        # 下载与索引无关）。
        try:
            await self._load_config()
        except StoreError as exc:
            return _fail(exc)
        d = self._model_dir()
        base = f"{(mirror or DEFAULT_MIRROR).rstrip('/')}/sentence-transformers/{DEFAULT_MODEL}/resolve/main"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _fail(EmbedderError(f"model directory not writable: {d}: {exc}"))
        fetched: list[str] = []
        try:
            for fname, remote in MODEL_FILES.items():
                dest = d / fname
                if dest.exists() and dest.stat().st_size > 0:
                    continue
                self.report_status({"status": "downloading", "message": fname})
                await self._download(f"{base}/{remote}", dest)
                fetched.append(fname)
        except Exception as exc:
            return _fail(EmbedderError(
                f"model download failed at {fetched or 'start'} ({base}): "
                f"{type(exc).__name__}: {exc}"
            ))
        ready = all((d / f).exists() for f in REQUIRED_MODEL_FILES)
        return Ok({
            "model_dir": str(d),
            "model_dir_source": self._model_dir_source(),
            "downloaded": fetched,
            "ready": ready,
            "missing_files": [f for f in REQUIRED_MODEL_FILES if not (d / f).exists()],
        })

    @staticmethod
    async def _download(url: str, dest: Path, chunk: int = 1 << 20) -> None:
        import asyncio

        def _do() -> None:
            tmp = dest.with_suffix(dest.suffix + ".part")
            # hf-mirror 对 urllib 默认 UA（Python-urllib/3.x）在部分文件上直接回
            # 403 Forbidden（实测 tokenizer.json / config.json 命中，model.onnx
            # 反而不受影响）—— 这正是「下载入口在需要它时下不动」的第二个成因。
            # 显式带上普通 UA 即可稳定 200。
            req = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_UA})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
                while True:
                    block = resp.read(chunk)
                    if not block:
                        break
                    f.write(block)
            tmp.replace(dest)

        await asyncio.to_thread(_do)

    # ------------------------------------------------------------------
    # Skill 统计转发（Skill -> KB -> DB）
    # ------------------------------------------------------------------


    @ui.action(label="记录 Skill 统计", refresh_context=True)

    @plugin_entry(
        id="kb_record_skill_stat",
        name="记录 Skill 统计",
        description="转发 skill_stat_record 到数据库插件（供 skill 插件调用）",
        timeout=30.0,
    )
    async def kb_record_skill_stat(self, skill: str = "", duration_ms: float = 0.0, ok: bool = True, **_):
        if not skill:
            return Err(SdkError("skill name is required"))
        out = await self.plugins.call_entry(
            "plugin_database:skill_stat_record",
            {"skill": skill, "duration_ms": float(duration_ms), "ok": bool(ok)},
        )
        if isinstance(out, Ok):
            return Ok(out.value)
        return _fail(StoreError(f"skill_stat_record -> {out.error}"))

    @ui.action(label='查询 Skill 统计', tone="primary", refresh_context=True)

    @plugin_entry(
        id="kb_query_skill_stats",
        name="查询 Skill 统计",
        description="转发 skill_stat_query 到数据库插件",
        timeout=30.0,
    )
    async def kb_query_skill_stats(self, **_):
        out = await self.plugins.call_entry("plugin_database:skill_stat_query", {})
        if isinstance(out, Ok):
            return Ok(out.value)
        return _fail(StoreError(f"skill_stat_query -> {out.error}"))
