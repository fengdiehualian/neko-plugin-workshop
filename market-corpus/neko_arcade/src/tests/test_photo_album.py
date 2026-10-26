# -*- coding: utf-8 -*-
"""喵图相册测试: 图库在 games/neko_photo/data(不进 git) + 空库也出图 + 发图频率闸门。

覆盖用户报的问题:
1. 指令发不出图 / 无法自动发图 → 空图库时必须有兜底图(插件自带)
2. 上传会进包 → 图库目录不进 git(.gitignore 排除), 打包发布默认空
3. 发图频率 → 由 LLM 决定, 插件只兜上限(间隔/每小时/每日)

注: 本环境禁止系统临时目录(pytest tmp_path 会 PermissionError),
    因此用仓库内的工作目录并在每个用例开始时重建。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

import pytest
from plugin.plugins.neko_arcade.adapters.photo_bridge import PhotoBridge

REPO = Path(__file__).resolve().parents[1]
WORK = Path(__file__).resolve().parent / "_photo_tmp"
GALLERY = REPO / "games" / "neko_photo" / "data"


class StubPlugin:
    """模拟宿主: config_dir=插件目录(图库就在其下), data_path=私有存储(兜底用)。"""

    def __init__(self, storage: Path):
        self.config_dir = str(REPO)
        self._storage = storage

    def data_path(self, *parts: str) -> Path:
        p = self._storage.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


class StubPush:
    def __init__(self):
        self.calls = []

    async def text_with_image(self, text: str, image_bytes: bytes,
                              mime: str = "image/png") -> None:
        self.calls.append({"text": text, "bytes": len(image_bytes), "mime": mime})

    async def text(self, msg: str, **_: Any) -> None:
        self.calls.append({"text": msg})


def _fresh() -> Path:
    """干净的仓库内工作目录。"""
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK


def _storage(tmp: Path) -> Path:
    d = tmp / "storage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bridge(push=None) -> PhotoBridge:
    return PhotoBridge(StubPlugin(_storage(_fresh())), push=push or StubPush(), img=None)


@pytest.fixture(autouse=True)
def _clean_gallery():
    """用例前后都清掉仓库里的图库目录(它是本地数据, 不该留在工作树)。"""
    shutil.rmtree(GALLERY, ignore_errors=True)
    yield
    shutil.rmtree(GALLERY, ignore_errors=True)


# ── 图库位置: games/neko_photo/data, 但不进 git ──────────
def test_gallery_root_is_game_data_dir() -> None:
    """图库就在 games/neko_photo/data/(便于直接丢图/分类), 不存在时自动建。"""
    bridge = _bridge()
    assert bridge._img_root() == GALLERY
    assert GALLERY.is_dir()


def test_gallery_is_ignored_by_git() -> None:
    """图库目录**不进 git**——所以打包发布默认就是空图库。"""
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "games/neko_photo/data/" in ignore, ".gitignore 必须排除图库目录"
    tracked = subprocess.run(["git", "ls-files", "games/neko_photo/data"],
                             cwd=str(REPO), capture_output=True, text=True)
    assert not (tracked.stdout or "").strip(), f"图库不该被 git 跟踪: {tracked.stdout}"


def test_upload_lands_in_gallery_and_stays_untracked() -> None:
    bridge = _bridge()
    data = b"\x89PNG" + b"0" * 64
    r = asyncio.run(bridge.upload_photo("u1", "cat.png", data_bytes=data,
                                        category="我的猫图"))
    assert r["ok"], r
    saved = GALLERY / "我的猫图" / "cat.png"
    assert saved.is_file() and saved.read_bytes() == data
    assert bridge.get_categories() == ["我的猫图"]
    status = subprocess.run(["git", "status", "--porcelain", "games/neko_photo/data"],
                            cwd=str(REPO), capture_output=True, text=True)
    assert not (status.stdout or "").strip(), \
        f"上传的图不该出现在 git 状态里: {status.stdout}"


# ── 空图库也必须能出图 ─────────────────────────────────
def test_empty_gallery_still_delivers_default_photo() -> None:
    """空图库(默认状态)下「发图」类指令必须仍有图可发——用插件自带图兜底。"""
    bridge = _bridge()
    assert bridge.scan_images() == []
    r = asyncio.run(bridge.pick_for_delivery())
    assert r["ok"], r
    assert r["image"]["bytes"], "兜底图不能是空的"
    assert r["image"]["mime"].startswith("image/")


def test_empty_gallery_auto_send_works() -> None:
    """无指令的自动发图同样不能因为空库而失败。"""
    push = StubPush()
    bridge = _bridge(push=push)
    r = asyncio.run(bridge.send_auto("u1"))
    assert r["ok"], r
    assert push.calls and push.calls[0]["bytes"] > 0


def test_category_without_images_reports_empty() -> None:
    """指定分类为空时如实报错(不拿默认图冒充), 但整库为空仍能兜底出图。"""
    bridge = _bridge()
    r = asyncio.run(bridge.send_photo("u1", category="不存在的分类"))
    assert not r["ok"] and r.get("error") == "category_empty"
    assert asyncio.run(bridge.send_photo("u1"))["ok"]


# ── 发图频率闸门(LLM 想发就发, 插件封顶) ────────────────
class _Store:
    def __init__(self):
        self.data: Dict[str, Any] = {}

    async def store_get_user(self, gid, uid, default=None):
        return self.data.get(uid, default)

    async def store_save_user(self, gid, uid, data):
        self.data[uid] = data


class _GamePlugin(StubPlugin):
    def __init__(self, storage: Path):
        super().__init__(storage)
        self.store = _Store()

    async def store_get_user(self, gid, uid, default=None):
        return await self.store.store_get_user(gid, uid, default)

    async def store_save_user(self, gid, uid, data):
        await self.store.store_save_user(gid, uid, data)


def _photo_game(tmp: Path, config: Dict[str, Any] | None = None):
    from plugin.plugins.neko_arcade.games.neko_photo.game import NekoPhotoGame
    plugin = _GamePlugin(_storage(tmp))
    game = NekoPhotoGame(plugin)
    game._config = config or {"send_min_interval": 90, "send_max_per_hour": 6,
                              "max_photos_per_day": 5}
    game._help, game._keywords, game._emotion_templates = {}, [], {}
    game.bind_services(push=StubPush(), photo=PhotoBridge(plugin, push=StubPush()))
    return game


def test_send_gate_blocks_too_frequent() -> None:
    game = _photo_game(_fresh())

    async def run():
        first = await game._check_send_gate("u1")
        assert first["ok"]
        await game._mark_sent("u1")
        return await game._check_send_gate("u1")

    second = asyncio.run(run())
    assert not second["ok"], "刚发过就该被拦"
    assert second["retry_after"] > 0 and "秒后" in second["summary"]


def test_send_gate_hourly_cap() -> None:
    game = _photo_game(_fresh(), {"send_min_interval": 0, "send_max_per_hour": 2,
                                  "max_photos_per_day": 50})

    async def run():
        for _ in range(2):
            assert (await game._check_send_gate("u1"))["ok"]
            await game._mark_sent("u1")
        return await game._check_send_gate("u1")

    third = asyncio.run(run())
    assert not third["ok"] and "小时" in third["summary"]


def test_send_gate_daily_cap() -> None:
    game = _photo_game(_fresh(), {"send_min_interval": 0, "send_max_per_hour": 99,
                                  "max_photos_per_day": 1})

    async def run():
        import time as _t
        data = game._new_data()
        data["day"] = _t.strftime("%Y-%m-%d")
        data["today_count"] = 1
        data["last_send_ts"] = 0
        await game.save_user_data("u1", data)
        return await game._check_send_gate("u1")

    capped = asyncio.run(run())
    assert not capped["ok"] and "今天" in capped["summary"]


def test_zero_interval_means_unlimited() -> None:
    """0 是合法值(不限冷却), 不能被 `cfg or default` 当成缺省。"""
    game = _photo_game(_fresh(), {"send_min_interval": 0, "send_max_per_hour": 99,
                                  "max_photos_per_day": 99})

    async def run():
        await game._mark_sent("u1")
        return await game._check_send_gate("u1")

    assert asyncio.run(run())["ok"]


def test_default_config_is_llm_driven() -> None:
    """默认开定时随机发图(用户点名"随机发图"), 闸门参数要有默认值。"""
    cfg = json.loads((REPO / "data" / "config" / "neko_photo" / "config.json")
                     .read_text(encoding="utf-8"))
    assert cfg.get("auto_send_enabled") is True
    assert cfg.get("send_max_per_hour", 0) > 0
    assert cfg.get("send_min_interval", 0) > 0
    assert cfg.get("auto_min_interval", 0) > 0


@pytest.mark.parametrize("bad", ["../evil", "a/b", "a\\b", ".."])
def test_category_is_sanitized(bad: str) -> None:
    bridge = _bridge()
    r = asyncio.run(bridge.upload_photo("u1", "x.png", data_bytes=b"\x89PNG" + b"0" * 8,
                                        category=bad))
    assert r["ok"], r
    saved = list(GALLERY.rglob("x.png"))
    assert saved and ".." not in str(saved[0])
