"""混合检索：向量相似度（cosine）+ BM25 -> RRF 融合 Top-K，并驱动热度追踪。"""

from __future__ import annotations

from .embedder import OnnxEmbedder
from .hot import HotConfig, record_hit
from .store import (
    DbClient,
    bm25_channel,
    fetch_rows,
    loads_timestamps,
    rrf_fuse,
    vector_channel,
)


async def hybrid_search(
    db: DbClient,
    embedder: OnnxEmbedder,
    *,
    query: str,
    top_k: int = 5,
    use_vec0: bool = False,
    use_fts5: bool = True,
) -> list[dict]:
    """返回 Top-K 命中：content、source、rowid、similarity、rrf、channels。"""
    if not query.strip():
        return []
    caps_k = min(50, max(top_k * 4, top_k))
    import asyncio

    qvec = await asyncio.to_thread(embedder.embed_one, query)
    vec_hits = await vector_channel(db, qvec, caps_k, use_vec0=use_vec0)
    bm_hits = await bm25_channel(db, query, caps_k) if use_fts5 else []
    fused = rrf_fuse(vec_hits, bm_hits, top_k=top_k)
    if not fused:
        return []
    rows = await fetch_rows(db, [s["rid"] for s in fused])
    hits: list[dict] = []
    for slot in fused:
        row = rows.get(slot["rid"])
        if row is None:
            continue  # vec/fts 与 chunks 不同步时丢弃幽灵行
        hits.append(
            {
                "rowid": slot["rid"],
                "content": row["content"],
                "source": row["source"],
                "content_hash": row["content_hash"],
                "similarity": round(
                    max(
                        (
                            s
                            for s in (
                                slot["channels"].get("vector"),
                                slot["channels"].get("bm25"),
                            )
                            if s is not None
                        ),
                        default=0.0,
                    ),
                    4,
                ),
                "rrf": round(slot["rrf"], 6),
                "channels": slot["channels"],
                "_row": row,
            }
        )
    return hits


async def search_with_hot_tracking(
    db: DbClient,
    embedder: OnnxEmbedder,
    *,
    query: str,
    top_k: int,
    use_vec0: bool,
    use_fts5: bool,
    hot_config: HotConfig,
    promote_entry,
) -> list[dict]:
    """检索 + 对每个命中做热度追踪。命中项携带 promotion 状态。"""
    hits = await hybrid_search(
        db,
        embedder,
        query=query,
        top_k=top_k,
        use_vec0=use_vec0,
        use_fts5=use_fts5,
    )
    out: list[dict] = []
    for hit in hits:
        result = await record_hit(db, hit.pop("_row"), hot_config, promote_entry)
        hit["window_hits"] = result["window_hits"]
        hit["promoted"] = result["promoted"]
        out.append(hit)
    return out


__all__ = ["hybrid_search", "search_with_hot_tracking", "loads_timestamps"]
