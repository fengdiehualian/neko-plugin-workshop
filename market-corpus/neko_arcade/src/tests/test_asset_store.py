"""AssetStore 单测：外部素材"下载一次 + 校验 + 缓存"。

覆盖：
- 缓存命中（不重复下载）
- 首次下载 + sha256 校验通过
- sha256/大小不符 → 丢弃、不写缓存、返回 None（绝不用坏图）
- 主源失败 → 自动换备源
- 清单里没有的路径 → None（调用方回退占位图）
- 关闭自动下载 → 不联网，直接 None
- 内置 manifest 兜底（远端拉不到时）

不联网：通过 monkeypatch `AssetStore._fetch` 注入假响应。
临时目录放仓库内（Windows 沙箱下系统 temp 不可写，见 pitfalls §15）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

from plugin.plugins.neko_arcade.adapters.asset_store import (
    DEFAULT_BASE_URLS,
    AssetStore,
)

_TMP = Path(__file__).resolve().parent.parent / ".tmp_asset_store_test"
PAYLOAD = b"WEBPDATA-0123456789"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _tmp_dir(name: str) -> Path:
    d = _TMP / name
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_bytes(*, sha: str = SHA, size: int = len(PAYLOAD), path: str = "tarot/ba/card.webp") -> bytes:
    return json.dumps({
        "version": "1", "count": 1, "total_size": size,
        "files": [{"path": path, "sha256": sha, "size": size}],
    }, ensure_ascii=False).encode()


class _Fetcher:
    """假的网络层：按 URL 前缀给响应，并记录请求。"""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float = 0.0) -> bytes | None:
        self.calls.append(url)
        for prefix, body in self.responses.items():
            if url.startswith(prefix):
                return body
        return None


def _store(tmp: Path, fetcher: _Fetcher, **kw) -> AssetStore:
    store = AssetStore(object(), cache_dir=str(tmp / "cache"),
                       base_urls=["https://primary/", "https://mirror/"], **kw)
    store._fetch = fetcher            # type: ignore[assignment]
    return store


def test_downloads_then_serves_from_cache():
    """第一次下载（校验通过），第二次直接命中缓存，不再联网。"""
    async def run():
        tmp = _tmp_dir("hit")
        f = _Fetcher({"https://primary/manifest.json": _manifest_bytes(),
                      "https://primary/tarot/": PAYLOAD})
        store = _store(tmp, f)

        p1 = await store.ensure("tarot/ba/card.webp")
        assert p1 is not None and p1.read_bytes() == PAYLOAD
        assert store.stats["download"] == 1
        assert p1 == tmp / "cache" / "tarot" / "ba" / "card.webp"

        before = len(f.calls)
        p2 = await store.ensure("tarot/ba/card.webp")
        assert p2 == p1 and store.stats["hit"] == 1
        assert len(f.calls) == before, "命中缓存不该再发请求"

    asyncio.run(run())


def test_sha_mismatch_is_rejected():
    """sha256 不符 → 不落盘、返回 None（宁缺勿错图）。"""
    async def run():
        tmp = _tmp_dir("badsha")
        bad = _Fetcher({"https://primary/manifest.json": _manifest_bytes(),
                        "https://primary/tarot/": b"WRONG",
                        "https://mirror/tarot/": b"WRONG"})
        store = _store(tmp, bad)
        assert await store.ensure("tarot/ba/card.webp") is None
        assert not (tmp / "cache" / "tarot" / "ba" / "card.webp").exists()
        assert store.stats["fail"] == 1

    asyncio.run(run())


def test_falls_back_to_mirror():
    """主源拿不到 → 自动换备源，仍是同一份清单/素材。"""
    async def run():
        tmp = _tmp_dir("mirror")
        f = _Fetcher({"https://mirror/manifest.json": _manifest_bytes(),
                      "https://mirror/tarot/": PAYLOAD})
        store = _store(tmp, f)
        p = await store.ensure("tarot/ba/card.webp")
        assert p is not None and p.read_bytes() == PAYLOAD
        assert any(u.startswith("https://mirror/") for u in f.calls)

    asyncio.run(run())


def test_unknown_path_returns_none():
    """清单里没有的路径 → None（调用方回退占位图，不是异常）。"""
    async def run():
        tmp = _tmp_dir("unknown")
        f = _Fetcher({"https://primary/manifest.json": _manifest_bytes()})
        store = _store(tmp, f)
        assert await store.ensure("tarot/ba/not-in-manifest.webp") is None

    asyncio.run(run())


def test_disabled_store_never_touches_network():
    """asset_auto_download=false → 直接 None，不发任何请求。"""
    async def run():
        tmp = _tmp_dir("disabled")
        f = _Fetcher({"https://primary/manifest.json": _manifest_bytes(),
                      "https://primary/tarot/": PAYLOAD})
        store = _store(tmp, f, enabled=False)
        assert await store.ensure("tarot/ba/card.webp") is None
        assert f.calls == []

    asyncio.run(run())


def test_bundled_manifest_fallback():
    """远端拉不到 manifest → 用内置兜底（离线也能知道有哪些素材）。"""
    async def run():
        tmp = _tmp_dir("bundled")
        bundled = tmp / "assets.manifest.json"
        bundled.write_bytes(_manifest_bytes())
        f = _Fetcher({"https://primary/tarot/": PAYLOAD})   # 没有 manifest 响应
        store = _store(tmp, f, bundled_manifest=str(bundled))
        p = await store.ensure("tarot/ba/card.webp")
        assert p is not None and p.read_bytes() == PAYLOAD
        assert store.snapshot()["files"] == 1

    asyncio.run(run())


def test_local_code_dir_wins_before_download():
    """开发期代码目录里有图 → 不该联网（塔罗本地兜底优先，见 game._card_image）。"""
    async def run():
        tmp = _tmp_dir("local")
        f = _Fetcher({})
        store = _store(tmp, f)
        # 缓存目录里直接放一份（等价于"开发期已就位"）
        target = tmp / "cache" / "tarot" / "ba" / "card.webp"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PAYLOAD)
        p = await store.ensure("tarot/ba/card.webp")
        assert p == target
        # 清单要取（用来核对大小/sha），但素材本身不该再下载
        assert not any("/tarot/" in u for u in f.calls), "本地已有就不该下载素材"

    asyncio.run(run())


def test_default_sources_prefer_gitee_then_github():
    """缺省素材源：Gitee 主源在前，GitHub 兜底在后（都实测可拉）。"""
    assert DEFAULT_BASE_URLS[0].startswith("https://gitee.com/")
    assert any("raw.githubusercontent.com" in u for u in DEFAULT_BASE_URLS)
    assert all(u.endswith("/") for u in DEFAULT_BASE_URLS), "base url 必须带结尾斜杠才能拼路径"


class _FakePlugin:
    """带 plugin_dir / cache_path 的假插件（用于"本地优先"用例）。"""

    logger = None

    def __init__(self, plugin_dir: Path):
        self.plugin_dir = plugin_dir

    def cache_path(self, *parts):
        p = Path(self.plugin_dir) / "cache" / Path(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


def test_local_mirror_wins_over_download():
    """随包的素材（代码目录里已有）→ 直接本地返回，不联网。

    这一条保证"分门别类上传 Gitee"不会把图标/面板图变成网络依赖：
    分类在 DEFAULT_LOCAL_MAP 里登记后，本地有就本地用。
    """
    async def run():
        tmp = _tmp_dir("localmirror")
        (tmp / "assets").mkdir(parents=True, exist_ok=True)
        icon = tmp / "assets" / "icon.jpg"          # 本地是 jpg，清单里是 webp
        icon.write_bytes(PAYLOAD)
        f = _Fetcher({"https://primary/manifest.json": _manifest_bytes(path="ui/icon.webp"),
                      "https://primary/ui/": b"SHOULD-NOT-BE-USED"})
        store = AssetStore(_FakePlugin(tmp), cache_dir=str(tmp / "c"),
                           base_urls=["https://primary/"])
        store._fetch = f            # type: ignore[assignment]
        p = await store.ensure("ui/icon.webp")
        assert p == icon, "本地镜像应优先于下载（扩展名不同也要认出来）"
        assert not any("/ui/" in u for u in f.calls), "本地已有不该下载素材"
        assert store.stats["local"] == 1 and store.stats["download"] == 0

    asyncio.run(run())


def test_stats_and_snapshot():
    """面板/日志要能看到命中/下载/失败计数与缓存位置。"""
    async def run():
        tmp = _tmp_dir("stats")
        f = _Fetcher({"https://primary/manifest.json": _manifest_bytes(),
                      "https://primary/tarot/": PAYLOAD})
        store = _store(tmp, f)
        await store.ensure("tarot/ba/card.webp")
        snap = store.snapshot()
        assert snap["download"] == 1 and snap["files"] == 1
        assert snap["cache"].endswith("cache")
        assert snap["sources"][0].startswith("https://primary/")

    asyncio.run(run())
