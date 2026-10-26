"""滑动窗口热度追踪与冷数据降级。

数据结构：``kb_chunks.hit_timestamps`` 是 JSON 数组，保存窗口内每次命中的
ISO 时间戳。流程（每次检索命中）：

1. 追加当前时间戳
2. 剔除超出 ``window_days`` 的过期项
3. ``len >= threshold`` 时调用数据库插件的 ``hot_promote`` 写入 hot_knowledge
4. 提升后清空 ``hit_timestamps``，避免重复提升

冷数据降级：``last_hit_at`` 超过 ``demotion_days`` 的热记录标记 ``demoted``
（由数据库插件入口执行，数据保留但不再参与热查询）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .store import DbClient, loads_timestamps


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class HotConfig:
    window_days: int = 7
    threshold: int = 5
    demotion_days: int = 30

    @classmethod
    def from_dict(cls, cfg: dict | None) -> "HotConfig":
        cfg = cfg or {}
        return cls(
            window_days=max(1, int(cfg.get("window_days", 7))),
            threshold=max(1, int(cfg.get("threshold", 5))),
            demotion_days=max(1, int(cfg.get("demotion_days", 30))),
        )


async def record_hit(
    db: DbClient,
    row: dict,
    config: HotConfig,
    promote_entry: Any,
) -> dict:
    """记录一次命中。``row`` 是 kb_chunks 行（含 rowid/content_hash/content/
    embedding/hit_timestamps）。达到阈值时调用 ``promote_entry``（协程函数，
    封装数据库插件的 hot_promote 入口）执行提升。"""
    rid = int(row["rowid"])
    now = now_iso()
    cutoff = (datetime.now() - timedelta(days=config.window_days)).isoformat(
        timespec="seconds"
    )
    stamps = [t for t in loads_timestamps(row.get("hit_timestamps")) if t >= cutoff]
    stamps.append(now)
    promoted = False
    if len(stamps) >= config.threshold:
        await promote_entry(
            {
                "source": "knowledge_base",
                "content_hash": row["content_hash"],
                "content": row["content"],
                "embedding": row.get("embedding"),
                "hit_count": len(stamps),
                "first_hit_at": stamps[0],
                "last_hit_at": now,
            }
        )
        stamps = []  # 提升后清空，避免重复提升
        promoted = True
    await db.execute(
        "UPDATE kb_chunks SET hit_timestamps = ? WHERE rowid = ?",
        (json.dumps(stamps, ensure_ascii=False), rid),
    )
    return {"promoted": promoted, "window_hits": len(stamps), "content_hash": row["content_hash"]}


async def sweep_demotions(db: DbClient, config: HotConfig, demote_entry: Any) -> int:
    """把 last_hit_at 超过 demotion_days 的热记录标记为 demoted。

    .. deprecated:: 生产路径已改走数据库插件入口
       ``plugin_database:hot_demote``（见 ``__init__.py`` 的 ``_demote_pass``），
       本函数**在生产代码里没有任何调用点**：降级 SQL 由 ``plugin_database``
       按驱动生成（sqlite / mongo 各自实现），插件自身再拼一遍 SQL 是重复实现。
       保留仅为兼容旧的引擎级测试；新代码请勿使用。
    """
    cutoff = (datetime.now() - timedelta(days=config.demotion_days)).isoformat(
        timespec="seconds"
    )
    return int(await demote_entry(cutoff))
