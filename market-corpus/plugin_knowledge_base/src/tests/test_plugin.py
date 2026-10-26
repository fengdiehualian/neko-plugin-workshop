"""plugin_knowledge_base 验收测试。

任务书验收链路：导入 3 个 .md 文件 -> 执行 5 次检索 -> hot_knowledge 表出现
对应记录（滑动窗口 7 天 / 阈值 5）。sqlite + vec0 + FTS5 真实链路；
嵌入模型用插件自带的 all-MiniLM-L6-v2 ONNX（CPU 推理）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# kb_engine 由 tests/conftest.py 在收集阶段用 importlib 注册进 sys.modules
# （绕开宿主 SDK）。pytest 保证 conftest.py 先于测试模块被导入。
# 分块/文件读取这些纯引擎测试因此**不依赖** sibling plugin_database，
# 在只检出本仓库的 CI 上也能跑。
from kb_engine import (
    HotConfig,
    OnnxEmbedder,
    import_document,
    init_index,
    search_with_hot_tracking,
)
from kb_engine.hot import sweep_demotions  # 测试专用：生产路径走 plugin_database:hot_demote

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SIBLING_DB = PLUGIN_DIR.parent / "plugin_database"

#: 集成测试需要同级 plugin_database 的真实适配器与 hot_schema。
#: 单独检出本仓库时（如 CI）这些模块不存在 —— 用 module 级 skip 标记，
#: 而不是让顶层 import 抛 ImportError 把纯引擎测试一起带走。
_NEEDS_SIBLING = pytest.mark.skipif(
    not (SIBLING_DB / "adapters" / "__init__.py").is_file(),
    reason=f"需要同级 plugin_database（{SIBLING_DB}）；见 README「测试」",
)

if (SIBLING_DB / "adapters" / "__init__.py").is_file():
    from plugin_database.adapters import SQLiteAdapter
    from plugin_database.hot_schema import register_hot_schema
else:  # 仅为让模块可导入；带 _NEEDS_SIBLING 的测试不会执行
    SQLiteAdapter = None
    register_hot_schema = None

# ---------------------------------------------------------------------------
# DbClient：进程内直连 sqlite 适配器（与跨插件入口同构）
# ---------------------------------------------------------------------------


class DirectDb:
    async def execute(self, sql, params=()):
        return await ADAPTER.execute(sql, tuple(params))

    async def fetch_all(self, sql, params=()):
        return await ADAPTER.fetch_all(sql, tuple(params))

    async def fetch_one(self, sql, params=()):
        return await ADAPTER.fetch_one(sql, tuple(params))

    async def capabilities(self):
        return {"driver": "sqlite", "vec0": True, "fts5": True}


ADAPTER: SQLiteAdapter = None  # 由 fixture 填充


@pytest.fixture()
async def kb(tmp_path):
    global ADAPTER
    # 依赖缺失时给出可操作的跳过原因，而不是让整套测试以难懂的
    # ModuleNotFoundError 崩掉（全新克隆最常见：models/ 与 vendor/ 未补齐）。
    model_dir = PLUGIN_DIR / "models" / "all-MiniLM-L6-v2"
    if not (model_dir / "model.onnx").is_file():
        pytest.skip(f"嵌入模型不在 {model_dir}（models/ 被 .gitignore 排除，见 README「模型」）")
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        pytest.skip("sqlite_vec 不可用（plugin_database/vendor/ 未补齐，见其 README「依赖」）")

    ADAPTER = SQLiteAdapter()
    db_path = tmp_path / "kb.db"
    await ADAPTER.connect({"path": str(db_path)})
    import sqlite3

    import sqlite_vec

    raw = sqlite3.connect(str(db_path))
    raw.enable_load_extension(True)
    raw.load_extension(str(Path(sqlite_vec.__file__).parent / "vec0.dll"))
    raw.close()
    conn = ADAPTER._conn
    await conn.enable_load_extension(True)
    await conn.load_extension(str(Path(sqlite_vec.__file__).parent / "vec0.dll"))
    await register_hot_schema(ADAPTER)
    db = DirectDb()
    caps = await init_index(db)
    embedder = OnnxEmbedder(model_dir)
    embedder.load()
    yield db, embedder, caps
    await ADAPTER.close()


def _write_docs(kbdir: Path) -> None:
    (kbdir / "db.md").write_text(
        "# 数据库事务\n\n事务具有 ACID 特性：原子性、一致性、隔离性、持久性。\n\n"
        "SQLite 支持 WAL 模式，可以提高并发读写性能。\n\n连接池用于复用数据库连接。",
        encoding="utf-8",
    )
    (kbdir / "kb.md").write_text(
        "# 知识库\n\n滑动窗口热度追踪会在窗口内命中次数达到阈值时，把知识块提升到热数据表。\n\n"
        "分块策略按段落切分，块大小 512 tokens，重叠 64 tokens。",
        encoding="utf-8",
    )
    (kbdir / "misc.md").write_text(
        "# 闲聊\n\n今天天气不错，适合出去散步。\n\n猫娘喜欢小鱼干。",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 分块
# ---------------------------------------------------------------------------


def test_chunker_paragraph_and_overlap():
    from kb_engine.chunker import chunk_text

    class FakeEnc:
        def __init__(self, ids):
            self.ids = ids

    ids_by_len = {}

    def encode_fn(text):
        n = len(text) // 10 + 1
        ids = list(range(n))
        ids_by_len[text] = ids
        return FakeEnc(ids)

    def decode_fn(ids):
        return "x" * len(ids)

    paras = ["a" * 100, "b" * 100, "d" * 3000]  # d 段 301 tokens，超预算触发滑窗
    chunks = chunk_text("\n\n".join(paras), encode_fn, decode_fn, max_tokens=30, overlap=5)
    assert chunks
    # 超长段落的滑窗路径产生了解码窗口（纯 x 串，长度受限）
    windows = [c for c in chunks if set(c) == {"x"}]
    assert windows and all(len(w) <= 30 for w in windows)
    # 合并路径：非窗口块的 token 预算受限
    assert all(len(encode_fn(c).ids) <= 30 for c in chunks if not set(c) == {"x"})
    with pytest.raises(ValueError):
        chunk_text("t", encode_fn, decode_fn, max_tokens=8, overlap=8)


# ---------------------------------------------------------------------------
# 验收主链路：3 文件导入 -> 5 次检索 -> hot_knowledge 出现记录
# ---------------------------------------------------------------------------


@_NEEDS_SIBLING
async def test_import_search_promotion_chain(tmp_path, kb):
    db, embedder, caps = kb
    assert caps["vec0"] and caps["fts5"]

    kbdir = tmp_path / "knowledge"
    kbdir.mkdir()
    _write_docs(kbdir)
    total = 0
    for p in sorted(kbdir.glob("*.md")):
        r = await import_document(db, embedder, source=p.name, text=p.read_text(encoding="utf-8"))
        total += r["inserted"]
    assert total >= 3

    hot_cfg = HotConfig(window_days=7, threshold=5, demotion_days=30)
    promoted = []

    async def promote_entry(payload):
        promoted.append(payload["content_hash"])
        await ADAPTER.execute(
            "INSERT OR REPLACE INTO hot_knowledge (source, content_hash, content, embedding,"
            " hit_count, first_hit_at, last_hit_at, promoted_at, status)"
            " VALUES (?,?,?,?,?,?,?,?, 'active')",
            (
                payload["source"], payload["content_hash"], payload["content"],
                payload["embedding"], payload["hit_count"], payload["first_hit_at"],
                payload["last_hit_at"], "2026-09-26T00:00:00",
            ),
        )

    query = "事务的 ACID 特性是什么"
    last = None
    for _ in range(5):
        last = await search_with_hot_tracking(
            db, embedder, query=query, top_k=3,
            use_vec0=True, use_fts5=True,
            hot_config=hot_cfg, promote_entry=promote_entry,
        )
    assert last and last[0]["source"] == "db.md"
    assert promoted, "5 次检索后应触发提升"
    rows = await ADAPTER.fetch_all("SELECT content_hash, hit_count, status FROM hot_knowledge", ())
    assert any(r["status"] == "active" and r["content_hash"] in promoted for r in rows)

    # 提升后 hit_timestamps 被清空：紧接着再查一次不应立即重复提升
    before = len(promoted)
    await search_with_hot_tracking(
        db, embedder, query=query, top_k=1,
        use_vec0=True, use_fts5=True,
        hot_config=hot_cfg, promote_entry=promote_entry,
    )
    assert len(promoted) == before, "清空窗口后紧接的一次检索不得重复提升"


@_NEEDS_SIBLING
async def test_demotion_sweep(tmp_path, kb):
    """``sweep_demotions`` 是**测试专用**的旧降级实现（生产走
    ``plugin_database:hot_demote``，见 engine/hot.py 的 deprecated 说明）。
    这里继续钉住它的语义：cutoff 之前置 demoted、只动 active 行。
    真正在生产链路里跑的是 kb_demote_sweep 入口，由 tests/stub_run.py 覆盖。
    """
    db, embedder, caps = kb
    await ADAPTER.execute(
        "INSERT OR REPLACE INTO hot_knowledge (source, content_hash, content, embedding,"
        " hit_count, first_hit_at, last_hit_at, promoted_at, status)"
        " VALUES (?,?,?,?,?,?,?,?, 'active')",
        ("knowledge_base", "old", "旧知识", None, 5, "2000-01-01T00:00:00", "2000-01-01T00:00:00", "2000-01-01T00:00:00"),
    )
    hot_cfg = HotConfig(window_days=7, threshold=5, demotion_days=30)
    n = await sweep_demotions(db, hot_cfg, make_demote_entry())
    assert n == 1
    row = await ADAPTER.fetch_one("SELECT status FROM hot_knowledge WHERE content_hash = 'old'", ())
    assert row["status"] == "demoted"


def make_demote_entry():
    async def demote_entry(cutoff: str) -> int:
        from plugin_database.hot_schema import demote_sql

        sql, prm = demote_sql("sqlite", cutoff)
        return await ADAPTER.execute(sql, prm)

    return demote_entry


# ---------------------------------------------------------------------------
# 导入去重与不支持的文件类型
# ---------------------------------------------------------------------------


@_NEEDS_SIBLING
async def test_import_dedup(tmp_path, kb):
    db, embedder, caps = kb
    (tmp_path / "a.md").write_text("# 标题\n\n唯一内容，用于去重测试。", encoding="utf-8")
    r1 = await import_document(db, embedder, source="a.md", text="# 标题\n\n唯一内容，用于去重测试。")
    r2 = await import_document(db, embedder, source="a.md", text="# 标题\n\n唯一内容，用于去重测试。")
    assert r1["inserted"] == 1
    assert r2["inserted"] == 0 and r2["skipped"] == 1


def test_unsupported_file_rejected(tmp_path):
    from kb_engine.files import read_document

    f = tmp_path / "x.docx"
    f.write_text("nope", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        read_document(f)


# ---------------------------------------------------------------------------
# numpy 余弦兜底通道（vec0 不可用时的真实执行路径）
# ---------------------------------------------------------------------------


async def _noop_promote(payload):
    return None


@_NEEDS_SIBLING
async def test_numpy_fallback_channel(tmp_path, kb):
    db, embedder, caps = kb
    kbdir = tmp_path / "kb2"
    kbdir.mkdir()
    _write_docs(kbdir)
    for pth in sorted(kbdir.glob("*.md")):
        await import_document(db, embedder, source=pth.name, text=pth.read_text(encoding="utf-8"))
    hits = await search_with_hot_tracking(
        db, embedder, query="WAL 模式 并发", top_k=3,
        use_vec0=False, use_fts5=True,
        hot_config=HotConfig(), promote_entry=_noop_promote,
    )
    assert hits and hits[0]["source"] == "db.md"
    assert "vector" in hits[0]["channels"]


# ---------------------------------------------------------------------------
# 中文 BM25：trigram 分词下，逐字子串查询必须命中 bm25 通道
# ---------------------------------------------------------------------------


@_NEEDS_SIBLING
async def test_chinese_bm25_channel(tmp_path, kb):
    db, embedder, caps = kb
    kbdir = tmp_path / "kb3"
    kbdir.mkdir()
    _write_docs(kbdir)
    for pth in sorted(kbdir.glob("*.md")):
        await import_document(db, embedder, source=pth.name, text=pth.read_text(encoding="utf-8"))
    hits = await search_with_hot_tracking(
        db, embedder, query="滑动窗口热度追踪会在窗口内命中次数达到阈值时", top_k=3,
        use_vec0=True, use_fts5=True,
        hot_config=HotConfig(), promote_entry=_noop_promote,
    )
    assert hits, "中文检索应有命中"
    assert "bm25" in hits[0]["channels"], "逐字子串查询应命中 trigram BM25 通道"
    assert hits[0]["source"] == "kb.md"


# ---------------------------------------------------------------------------
# 分块：真实 tokenizer（不截断计数）下超长段落必须切成多个窗口内小块
# ---------------------------------------------------------------------------


def test_chunker_real_tokenizer_windows():
    from kb_engine.chunker import chunk_text

    kb_dir = PLUGIN_DIR / "models" / "all-MiniLM-L6-v2"
    if not (kb_dir / "tokenizer.json").exists():
        pytest.skip("bundled model tokenizer not present")
    from kb_engine.embedder import OnnxEmbedder

    emb = OnnxEmbedder(kb_dir)
    emb.load()
    long_para = "数据库事务的原子性保证要么全部操作成功要么全部不执行。" * 40
    chunks = chunk_text(long_para, emb.encode, emb.decode)
    assert len(chunks) > 1, "超过窗口的长段落应切成多个块"
    for c in chunks:
        assert len(emb.encode(c).ids) <= 240


def test_chunker_counter_tokenizer_not_truncated_english():
    """tokenizer.json 自带 128 截断配置；计数分词器必须显式关断，
    否则英文长文段落计数恒 ≤128、滑窗永不触发、块尾无向量覆盖。"""
    from kb_engine.chunker import chunk_text

    kb_dir = PLUGIN_DIR / "models" / "all-MiniLM-L6-v2"
    if not (kb_dir / "tokenizer.json").exists():
        pytest.skip("bundled model tokenizer not present")
    from kb_engine.embedder import OnnxEmbedder

    emb = OnnxEmbedder(kb_dir)
    emb.load()
    para = "the quick brown fox jumps over the lazy dog near the river bank " * 40
    n = len(emb.encode(para).ids)
    assert n > 240, f"计数分词器仍被截断: {n}"
    chunks = chunk_text(para, emb.encode, emb.decode)
    assert len(chunks) > 1, "超过预算的英文长段应切成多个块"
    for c in chunks:
        # 重编码含 [CLS]/[SEP] 特殊 token（+2）与滑窗边界漂移；
        # 真正的不变量是不超过推理窗口 256（保证整块都有向量覆盖）
        assert len(emb.encode(c).ids) <= 256
