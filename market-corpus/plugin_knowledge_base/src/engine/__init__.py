"""知识库引擎包：文件导入 -> 分块 -> 向量化 -> 混合检索 -> 热度提升。"""

from .chunker import chunk_text
from .embedder import EmbedderError, OnnxEmbedder
from .files import content_hash, read_document, scan_directory
from .hot import (  # noqa: F401  (sweep_demotions: 测试专用，见 hot.py 的 deprecated 说明)
    HotConfig,
    record_hit,
    sweep_demotions,
)
from .search import hybrid_search, search_with_hot_tracking
from .store import (
    DbClient,
    StoreError,
    bm25_channel,
    fetch_rows,
    import_document,
    init_index,
    rebuild_index,
    rrf_fuse,
    vector_channel,
)

__all__ = [
    "DbClient",
    "EmbedderError",
    "HotConfig",
    "OnnxEmbedder",
    "StoreError",
    "bm25_channel",
    "chunk_text",
    "content_hash",
    "fetch_rows",
    "hybrid_search",
    "import_document",
    "init_index",
    "read_document",
    "rebuild_index",
    "record_hit",
    "rrf_fuse",
    "scan_directory",
    "search_with_hot_tracking",
    "sweep_demotions",  # 测试专用（生产走 plugin_database:hot_demote），见 hot.py
    "vector_channel",
]
