"""索引存储与检索通道（sqlite：vec0 向量 + FTS5 BM25）。

存储布局（运行在数据库插件的 sqlite 连接上，经跨插件入口读写）：

* ``kb_chunks``：chunk 明文 + embedding BLOB + ``hit_timestamps`` JSON 数组
* ``kb_vec``：vec0 虚拟表（float[384]），rowid 对齐 kb_chunks；宿主 sqlite
  未加载 vec0 扩展时，向量通道退化为 numpy 全量余弦扫描（BLOB 向量）
* ``kb_fts``：FTS5 external-content 表，触发器同步；FTS5 不可用时跳过 BM25 通道

索引要求 sqlite 驱动（vec0/FTS5 都是 sqlite 能力）；热数据表由数据库插件
按驱动建表，任意驱动可用。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from .chunker import chunk_text
from .embedder import OnnxEmbedder

_log = logging.getLogger(__name__)

KB_CHUNKS_DDL = (
    "CREATE TABLE IF NOT EXISTS kb_chunks ("
    " content_hash TEXT NOT NULL UNIQUE,"
    " source TEXT NOT NULL,"
    " ordinal INTEGER NOT NULL DEFAULT 0,"
    " content TEXT NOT NULL,"
    " embedding BLOB,"
    " hit_timestamps TEXT NOT NULL DEFAULT '[]',"
    " created_at TEXT NOT NULL DEFAULT '')"
)

# trigram：unicode61 会把连续汉字当成一个 token，中文 MATCH 永远不命中；
# trigram 按 3-gram 切分，>=3 字的中文查询可命中（<3 字无效，见 README）。
KB_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5("
    "content, tokenize='trigram', content='kb_chunks', content_rowid='rowid')"
)

KB_META_DDL = "CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT)"

KB_FTS_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS kb_fts_ins AFTER INSERT ON kb_chunks BEGIN "
    "INSERT INTO kb_fts(rowid, content) VALUES (new.rowid, new.content); END",
    "CREATE TRIGGER IF NOT EXISTS kb_fts_del AFTER DELETE ON kb_chunks BEGIN "
    "INSERT INTO kb_fts(kb_fts, rowid, content) VALUES ('delete', old.rowid, old.content); END",
    "CREATE TRIGGER IF NOT EXISTS kb_fts_upd AFTER UPDATE ON kb_chunks BEGIN "
    "INSERT INTO kb_fts(kb_fts, rowid, content) VALUES ('delete', old.rowid, old.content); "
    "INSERT INTO kb_fts(rowid, content) VALUES (new.rowid, new.content); END",
)


class DbClient(Protocol):
    """数据库插件的最小客户端接口（进程内直连或跨插件 call_entry 均可）。"""

    async def execute(self, sql: str, params: Any = ()) -> int: ...

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict]: ...

    async def fetch_one(self, sql: str, params: Any = ()) -> dict | None: ...

    async def capabilities(self) -> dict: ...


class StoreError(Exception):
    """索引存储结构化错误。"""


async def init_index(
    db: DbClient, dimension: int = 384, model_id: str = ""
) -> dict:
    """建索引表，探测 vec0/FTS5 能力，校验嵌入模型一致性。

    向量空间由 (model_id, dimension) 标识：元数据里记录的模型与当前模型不一致时
    报结构化错误（不同模型的向量不可比，绝不能混进同一索引），
    由调用方走 ``rebuild_index`` 重建后再导入。

    标识换过一版（旧版用模型目录的**目录名**，见 ``embedder.model_identity``）：
    老库里记着目录名、新标识带着权重哈希，两者不可比。这里**刻意不做自动迁移**：
    放宽成「目录名能对上就放行」会削弱守卫（那正是被修掉的 BUG），
    而老库只需一次 ``kb_rebuild`` 重建（错误信息已直接给出该指引）。
    """
    caps = await db.capabilities()
    if caps.get("driver") != "sqlite":
        raise StoreError(
            f"knowledge index requires the database plugin's sqlite driver "
            f"(current: {caps.get('driver')!r}); vec0/FTS5 are sqlite features"
        )
    await db.execute(KB_CHUNKS_DDL, ())
    await db.execute(KB_META_DDL, ())
    prev_model = await db.fetch_one(
        "SELECT value FROM kb_meta WHERE key = 'embedder_model'", ()
    )
    prev_dim = await db.fetch_one(
        "SELECT value FROM kb_meta WHERE key = 'embedder_dim'", ()
    )
    if prev_model is not None and str(prev_model["value"]) != str(model_id):
        raise StoreError(
            f"index was built with embedder {prev_model['value']!r}, "
            f"current is {model_id!r}; vectors from different models must not be mixed. "
            "调用 kb_rebuild 重建索引后重新导入。"
        )
    if prev_dim is not None and int(prev_dim["value"]) != int(dimension):
        raise StoreError(
            f"index was built with dimension {prev_dim['value']}, "
            f"current embedder outputs {dimension}; 调用 kb_rebuild 重建索引。"
        )
    await db.execute(
        "INSERT OR REPLACE INTO kb_meta (key, value) VALUES ('embedder_model', ?)",
        (str(model_id),),
    )
    await db.execute(
        "INSERT OR REPLACE INTO kb_meta (key, value) VALUES ('embedder_dim', ?)",
        (str(int(dimension)),),
    )
    fts_ok = bool(caps.get("fts5"))
    if fts_ok:
        await db.execute(KB_FTS_DDL, ())
        for trig in KB_FTS_TRIGGERS:
            await db.execute(trig, ())
    vec_ok = bool(caps.get("vec0"))
    if vec_ok:
        await db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS kb_vec USING vec0(embedding float[{int(dimension)}])",
            (),
        )
    return {"driver": "sqlite", "vec0": vec_ok, "fts5": fts_ok, "dimension": int(dimension), "model": str(model_id)}


async def rebuild_index(db: DbClient, dimension: int = 384, model_id: str = "") -> dict:
    """丢弃全部索引数据后按当前 (model_id, dimension) 重建（热数据表不受影响）。"""
    for drop in ("DROP TABLE IF EXISTS kb_vec", "DROP TABLE IF EXISTS kb_fts",
                 "DROP TABLE IF EXISTS kb_chunks", "DROP TABLE IF EXISTS kb_meta",
                 "DROP TRIGGER IF EXISTS kb_fts_ins", "DROP TRIGGER IF EXISTS kb_fts_del",
                 "DROP TRIGGER IF EXISTS kb_fts_upd"):
        await db.execute(drop, ())
    return await init_index(db, dimension=dimension, model_id=model_id)


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

async def import_document(
    db: DbClient,
    embedder: OnnxEmbedder,
    *,
    source: str,
    text: str,
    max_tokens: int = 240,
    overlap: int = 64,
    batch_size: int = 32,
) -> dict:
    """分块 -> 嵌入 -> 入库（按 content_hash 去重）。返回导入统计。"""
    from .files import content_hash

    def encode_fn(t: str):
        return embedder.encode(t)

    def decode_fn(ids) -> str:
        return embedder.decode(ids)

    import asyncio

    # 分块计数走 tokenizer（CPU 密集），推理批跑 onnxruntime —— 都不能占事件循环
    pieces = await asyncio.to_thread(
        chunk_text, text, encode_fn, decode_fn, max_tokens, overlap
    )
    caps = await db.capabilities()
    inserted = 0
    skipped = 0
    for start in range(0, len(pieces), batch_size):
        batch = pieces[start : start + batch_size]
        vectors = await asyncio.to_thread(embedder.embed, batch)
        for i, (piece, vec) in enumerate(zip(batch, vectors)):
            chash = content_hash(piece)
            existing = await db.fetch_one(
                "SELECT rowid FROM kb_chunks WHERE content_hash = ?", (chash,)
            )
            if existing is not None:
                skipped += 1
                continue
            await db.execute(
                "INSERT INTO kb_chunks (content_hash, source, ordinal, content, embedding,"
                " hit_timestamps, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    chash,
                    source,
                    start + i,
                    piece,
                    _pack_vec(vec),
                    "[]",
                    _now_iso(),
                ),
            )
            row = await db.fetch_one(
                "SELECT rowid FROM kb_chunks WHERE content_hash = ?", (chash,)
            )
            rid = int(row["rowid"])
            if caps.get("fts5"):
                await db.execute(
                    "INSERT INTO kb_fts(rowid, content) VALUES (?, ?)", (rid, piece)
                )
            if caps.get("vec0"):
                await db.execute(
                    "INSERT INTO kb_vec(rowid, embedding) VALUES (?, ?)",
                    (rid, _pack_vec(vec)),
                )
            inserted += 1
    return {"source": source, "chunks": len(pieces), "inserted": inserted, "skipped": skipped}


# ---------------------------------------------------------------------------
# 检索通道
# ---------------------------------------------------------------------------

def _fts_query(query: str, max_terms: int = 12) -> str:
    terms = re.findall(r"\w+", query, re.UNICODE)[:max_terms]
    return " ".join(f'"{t}"' for t in terms)


async def vector_channel(
    db: DbClient, query_vec, top_k: int, use_vec0: bool
) -> list[tuple[int, float]]:
    """向量通道：rowid + 相似度（0..1）。vec0 用 L2 距离换算。"""
    if use_vec0:
        rows = await db.fetch_all(
            "SELECT rowid, distance FROM kb_vec WHERE embedding MATCH ? "
            "ORDER BY distance LIMIT ?",
            (_pack_vec(query_vec), top_k),
        )
        return [(int(r["rowid"]), 1.0 / (1.0 + float(r["distance"]))) for r in rows]
    # numpy 全量余弦（BLOB 向量）
    rows = await db.fetch_all("SELECT rowid, embedding FROM kb_chunks", ())
    if not rows:
        return []
    import numpy as np

    mat = np.vstack([_unpack_vec(r["embedding"]) for r in rows])
    sims = mat @ query_vec
    order = np.argsort(-sims)[:top_k]
    return [(int(rows[i]["rowid"]), float(sims[i])) for i in order]


async def bm25_channel(
    db: DbClient, query: str, top_k: int
) -> list[tuple[int, float]]:
    """BM25 通道：rowid + 归一化得分（越大越好，取值 (0,1]）。FTS5 不可用时返回空。

    FTS5 的 ``bm25()`` 返回**负值且越小越相关**（-10 比 -1 更相关）。对外契约是
    「越大越好」的正分，因此必须取绝对值再归一化：``abs(rank)/abs(best)`` 落在
    ``(0,1]``，best 得 1.0。旧实现写成 ``rank/abs(best)``，best<0 时结果**恒为负**，
    与注释自相矛盾；``search.py`` 对通道取 ``max()``，只命中 BM25 的块就会对外
    暴露负 similarity。``rrf_fuse`` 只用排名，排序不受影响。
    """
    fts_q = _fts_query(query)
    if not fts_q:
        return []
    try:
        rows = await db.fetch_all(
            "SELECT rowid, bm25(kb_fts) AS rank FROM kb_fts WHERE kb_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (fts_q, top_k),
        )
    except Exception as exc:
        # 旧实现是裸 ``except Exception: return []`` —— 真实 DB 故障（连接断开、
        # kb_fts 损坏）会被伪装成「无 BM25 命中」，日志无痕、静默退化成纯向量检索。
        #
        # 这里**不再静默**：一律记录 warning（含异常文本），失败可见。
        # 保留「让位给向量通道」的降级语义是刻意的：
        #   * 跨插件通路上 plugin_database 把驱动异常统一包成 AdapterError -> Err，
        #     本层只能看到 StoreError，无法与「MATCH 语法/表缺失」区分；
        #   * 真实 DB 故障不会因此被整体掩盖 —— 同一查询的向量通道同样走该连接，
        #     失败会在 vector_channel 处冒泡成结构化 Err（见 kb_search）。
        # 缩小捕获范围需要 plugin_database 暴露错误种类，超出本插件范围。
        _log.warning("bm25 channel failed, degrading to vector-only: {}", exc)
        return []
    if not rows:
        return []
    # bm25() 返回负值、绝对值越大越相关 -> abs 换算成 (0,1] 的正相关分数
    best = min(float(r["rank"]) for r in rows)
    denom = abs(best) if best < 0 else 1.0
    return [(int(r["rowid"]), abs(float(r["rank"])) / denom if denom else 1.0) for r in rows]


def rrf_fuse(
    *channels: list[tuple[int, float]], top_k: int, k: int = 60
) -> list[dict]:
    """倒数排名融合。返回 [{rid, rrf, channels:{name:score}}] 按 rrf 降序。"""
    fused: dict[int, dict] = {}
    for name, ranked in zip(("vector", "bm25"), channels):
        for rank, (rid, score) in enumerate(ranked):
            slot = fused.setdefault(rid, {"rid": rid, "rrf": 0.0, "channels": {}})
            slot["rrf"] += 1.0 / (k + rank + 1)
            slot["channels"][name] = score
    out = sorted(fused.values(), key=lambda s: -s["rrf"])[:top_k]
    return out


async def fetch_rows(db: DbClient, rids: list[int]) -> dict[int, dict]:
    if not rids:
        return {}
    marks = ",".join("?" * len(rids))
    rows = await db.fetch_all(
        f"SELECT rowid, content_hash, source, content, embedding, hit_timestamps"
        f" FROM kb_chunks WHERE rowid IN ({marks})",
        tuple(rids),
    )
    return {int(r["rowid"]): r for r in rows}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _pack_vec(vec) -> bytes:
    import numpy as np

    return np.asarray(vec, dtype=np.float32).tobytes()


def _unpack_vec(blob: bytes):
    import numpy as np

    return np.frombuffer(bytes(blob), dtype=np.float32)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def loads_timestamps(raw: str | None) -> list[str]:
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


__all__ = [
    "DbClient",
    "StoreError",
    "bm25_channel",
    "fetch_rows",
    "import_document",
    "init_index",
    "loads_timestamps",
    "rrf_fuse",
    "vector_channel",
]
