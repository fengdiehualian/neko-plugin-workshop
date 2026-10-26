"""表情库的持久层：`data/catalog.json` + `data/stickers/<id>.<ext>` + `data/usage.json`。

为什么用 JSON 文件而不是 PluginStore 键值：
1. 图片本体就是文件，目录用 JSON 与它同根（`data_path`），备份/排障时一眼看全；
2. 避开"[plugin.store].enabled=false 时静默失效"的坑（our_life 陷阱 §4）——
   文件通道的失败是**响亮的**（IOError 会被捕获并转成稳定错误码）。

线程模型：入口和定时器可能运行在不同线程。实例级可重入锁覆盖状态修改、
写盘及失败回滚，也保护读快照；没有跨进程共享。
"""

from __future__ import annotations

import json
import threading
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Concatenate, Iterator, ParamSpec, TypeVar

from ..core.catalog import (
    DEFAULT_ZONE_NAME,
    GROUP_DESC_MAX_CHARS,
    MAX_STICKER_BYTES,
    OFFICIAL_ZONE_NAME,
    UPLOAD_CHUNK_BYTES,
    UPLOAD_MAX_TOTAL_BYTES,
    Sticker,
    content_sha256,
    desc_from_filename,
    detect_image_format,
    new_sticker_id,
    new_zone_id,
    normalize_group,
    normalize_zone_name,
)
from ..core.labeling import index_by_digest, plan_label_refresh
from ..core.pack import (
    PACK_DIR_PREFIX,
    PACK_MANIFEST_FILENAME,
    PACK_MAX_ENTRIES,
    build_manifest,
    parse_manifest,
    parse_manifest_groups,
    parse_pack_version,
    safe_member_name,
)
from ..core.thumbs import THUMB_CACHE_VERSION, build_thumb, thumb_filename
from .bundled_pack import open_pack
from .official_assets import validate_official_assets
from .pack_io import ManifestLimitError, read_pack_manifest

# v2（v0.11.0 J-1）：顶层新增 zones / active_zone / group_zone 三键（区→分类→图）。
# 读侧宽松兼容 v1（无这三键 = 旧平铺库，load() 里一次性迁进默认区）；写侧永远按 v2 写。
# v3（v0.13.0 J-3）：顶层新增 `official_pack_version`（已应用的官方包内容版本），
# 条目新增 `owner_edited`（主人改过文本的位）。两键都是**纯插入**：缺席即 0/False，
# 旧库读得动、盘形不变；写侧从此按 v3 写。
CATALOG_VERSION = 3
CATALOG_FILENAME = "catalog.json"
USAGE_FILENAME = "usage.json"
STICKER_DIRNAME = "stickers"
# 收件箱：用户把一堆图片文件（或套图 zip 包）丢这里，面板点"导入收件箱"逐张收进库。
# 选目录而不是浏览器多选当唯一批量通道：免 base64 膨胀、免逐次往返、
# 成百张也不怕；点不到的文件（隐藏/子目录）一律不碰不删。
INBOX_DIRNAME = "inbox"
# 缩略图缓存（v0.17.2）：表情墙一格一图，以前每格拉的是**原图字节**——实机账是
# 官方区 190 张 ≈34.7MB（base64 后 ≈46MB）走控制通道、并发 2，于是"加载图片超时"
# 与"日志刷屏"（当天 363 行里 313 行是 preview）是同一个根因的两面。
# 缓存按内容指纹命名 ⇒ 没有失效逻辑，内容变了自然重算；整目录删掉也不会坏（下次重生成）。
THUMB_DIRNAME = "thumbs"
# 直传暂存（v0.6.0 面板选择文件导入）：分块落盘在这里，完成即导入、随即删。
# 与 inbox 的分工：inbox 是"主人自己找到了目录"的旁路，uploads 是面板会话的
# 临时尸体——两者的文件都不该长期住下，但只有 inbox 参与"点导入"扫描。
UPLOADS_DIRNAME = "uploads"
# 导出物（v0.2.0）：套图 zip 落这里。面板拿不到文件句柄（iframe），
# 只能显示路径让主人自己取——与 inbox 是一对镜像（进/出都走文件系统）。
EXPORTS_DIRNAME = "exports"

# 稳定错误码（面板与模型各自翻译/理解，见 DESIGN.md 的错误码契约）
ERR_IO = "library_io_error"
ERR_NOT_FOUND = "sticker_not_found"
ERR_EMPTY = "library_empty"
ERR_DUPLICATE = "duplicate_image"
ERR_PACK_UNREADABLE = "pack_unreadable"

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _serialized(
    method: Callable[Concatenate["Library", _P], _R],
) -> Callable[Concatenate["Library", _P], _R]:
    @wraps(method)
    def guarded(self: Library, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with self.locked():
            return method(self, *args, **kwargs)

    return guarded


@dataclass
class LibraryResult:
    ok: bool
    code: str = ""
    detail: str = ""

    @classmethod
    def failure(cls, code: str, detail: str = "") -> "LibraryResult":
        return cls(ok=False, code=code, detail=detail)


class Library:
    """表情库。`root` 是插件 data 目录（生产由 `plugin.data_path()` 提供）。

    `on_saved` 在**每次写盘成功**后被调一次（不传=不通知）。它是"库变了"这件事的唯一
    收口：任何新增入口只要走了 `save()` 就自动带上，不用在调用点各记一遍。回调里只做
    打标（便宜、同步），真正的重活（往宿主重挂工具描述）由调用方挪到自己的拍子上做——
    批量导入会连着 save() 上百次，在回调里直接发 IPC 就是把会话刷爆。
    回调抛了不许把一次成功的写盘说成失败，所以这里吞掉异常只记日志。
    """

    def __init__(self, root: Path, *, logger: Any = None, on_saved: Any = None):
        self._state_lock = threading.RLock()
        self._root = Path(root)
        self._logger = logger
        self._on_saved = on_saved
        self._stickers: dict[str, Sticker] = {}
        # 分组说明（轮 F：“分类=描述”挂在组上，不挂在图上）；catalog.json 顶层 groups。
        self._groups: dict[str, str] = {}
        # 区（v0.11.0 J-1）：`_zones` 按插入序就是面板 tab 序；`_group_zone` 是分类→区的归属；
        # `_active_zone` 是她看世界的唯一窗口（非激活区的分类/图对她整体隐形，陷阱 21 的推广版）。
        # meta 值形如 {name, desc, builtin?}（J-2 起 builtin 为真才写，旧库形状不变）。
        self._zones: dict[str, dict[str, Any]] = {}
        self._group_zone: dict[str, str] = {}
        self._active_zone: str = ""
        # 官方播种台账（J-2 P2A，拍板 P3：只播一次 + 恢复按钮）：catalog 顶层 `official_seeded`。
        self._official_seeded = False
        # 已应用的官方包内容版本（v0.13.0 J-3）：0 = 从没刷过标签。
        self._official_pack_version = 0
        self._loaded = False
        self._read_failed = False
        self._catalog_seen = False
        self._io_dirty = False
        # zip 直传会话（仅本进程，sid -> {h, path, seq, size, name, at}）：
        # 会话变更与文件写入共用图库锁；进程重启 = 会话作废，
        # 重选文件即可——断点续传不值得为这种短生命周期交互复杂度。
        self._uploads: dict[str, dict] = {}

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Protect a synchronous compound operation; never hold this across await."""
        with self._state_lock:
            yield

    # ------------------------------------------------------------------
    # 路径与加载
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def stickers_dir(self) -> Path:
        return self._root / STICKER_DIRNAME

    @property
    def inbox_dir(self) -> Path:
        return self._root / INBOX_DIRNAME

    @property
    def thumbs_dir(self) -> Path:
        """缩略图缓存目录（v0.17.2）。全部由我们生成，按内容指纹命名，可整目录删。"""
        return self._root / THUMB_DIRNAME

    def inbox_files(self) -> list[Path]:
        """收件箱里可见的候选文件（排序稳定；不递归、不碰隐藏项）。"""
        try:
            if not self.inbox_dir.is_dir():
                return []
            return sorted(p for p in self.inbox_dir.iterdir() if p.is_file() and not p.name.startswith("."))
        except Exception:
            self._log("inbox scan failed")
            return []

    @property
    def catalog_path(self) -> Path:
        return self._root / CATALOG_FILENAME

    def _log(self, message: str) -> None:
        # logger 缺席（测试直接构造 Library）时安静。
        if self._logger is not None:
            try:
                self._logger.info(message)
            except Exception:
                pass

    @_serialized
    def load(self, *, force: bool = False) -> LibraryResult:
        """Read a valid catalog; reject unreadable roots without discarding the last good snapshot.

        区的不变量在此兜住：**zones 永远非空、active_zone 永远在册**；
        旧库（无 zones 键）在这里一次性迁进默认区（当场补写一次盘，失败不阻断读）。
        """
        if self._loaded and not self._read_failed and not force:
            return LibraryResult(ok=True)
        try:
            raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("stickers"), list):
                raise ValueError("invalid catalog structure")
        except FileNotFoundError:
            try:
                images_remain = self.stickers_dir.is_dir() and any(self.stickers_dir.iterdir())
            except OSError:
                images_remain = True
            if self._catalog_seen or self._read_failed or images_remain:
                self._read_failed = self._io_dirty = True
                return LibraryResult.failure(ERR_IO, "catalog_unreadable")
            raw = {"stickers": [], "zones": []}
        except Exception:
            # Keep the last good snapshot, but block writes until a successful read.
            self._read_failed = self._io_dirty = True
            return LibraryResult.failure(ERR_IO, "catalog_unreadable")
        else:
            self._catalog_seen = True
        self._read_failed = False
        self._stickers = {}
        self._groups = {}
        self._zones = {}
        self._group_zone = {}
        self._active_zone = ""
        self._official_seeded = False
        self._official_pack_version = 0
        legacy_shape = True
        entries = raw.get("stickers") if isinstance(raw, dict) else None
        if isinstance(entries, list):
            for item in entries:
                sticker = Sticker.from_dict(item)
                if sticker is not None:
                    self._stickers[sticker.id] = sticker
        # 分组说明宽松读（轮 F）：坏键/坏值跳过；旧库无此键 = 空字典。
        # 轮 I 语义改一寸：**空说明也保留键**——`{名: ""}` 的意思是
        # “这个分类在册（主人建过/图还住着），只是暂时没写说明”。
        # 分类的生死从此只由 create_group/remove_group 决定，写空说明不再顺手杀分类。
        groups_raw = raw.get("groups") if isinstance(raw, dict) else None
        if isinstance(groups_raw, dict):
            for key, value in groups_raw.items():
                group = normalize_group(key)
                if not group or not isinstance(value, str):
                    continue
                self._groups[group] = value.strip()[:GROUP_DESC_MAX_CHARS]
        # --- 区（v0.11.0 J-1）：宽松读三键，坏项逐项丢 ---
        zones_raw = raw.get("zones") if isinstance(raw, dict) else None
        if isinstance(zones_raw, list) and zones_raw:
            legacy_shape = False
            for item in zones_raw:
                if not isinstance(item, dict):
                    continue
                zone_id = item.get("id")
                zone_name = normalize_zone_name(item.get("name"))
                if not isinstance(zone_id, str) or not zone_id or not zone_name:
                    continue
                zone_desc = item.get("desc")
                meta: dict[str, Any] = {
                    "name": zone_name,
                    "desc": zone_desc.strip()[:GROUP_DESC_MAX_CHARS] if isinstance(zone_desc, str) else "",
                }
                # J-2：builtin 位宽松读（旧库无此键 = 非官方区）；只存真值，假值不占键位。
                if item.get("builtin"):
                    meta["builtin"] = True
                self._zones[zone_id] = meta
            gz_raw = raw.get("group_zone") if isinstance(raw, dict) else None
            if isinstance(gz_raw, dict):
                for group_name, zone_id in gz_raw.items():
                    group = normalize_group(group_name)
                    if group and isinstance(zone_id, str) and zone_id in self._zones:
                        self._group_zone[group] = zone_id
            active_raw = raw.get("active_zone")
            if isinstance(active_raw, str) and active_raw in self._zones:
                self._active_zone = active_raw
        # 播种台账宽松读（J-2）：只认布尔真（非布尔一律当未播——宁可下拍重试，不可假装封过）。
        seeded_raw = raw.get("official_seeded") if isinstance(raw, dict) else None
        self._official_seeded = isinstance(seeded_raw, bool) and seeded_raw
        # 已应用的官方包版本宽松读（J-3）：只认正整数，其余（缺键/坏值/负数）一律当 0 = 没刷过。
        version_raw = raw.get("official_pack_version") if isinstance(raw, dict) else None
        self._official_pack_version = (
            version_raw if isinstance(version_raw, int) and not isinstance(version_raw, bool) and version_raw > 0 else 0
        )
        self._loaded = True
        self._io_dirty = False
        self._ensure_zone_invariant(migrated=legacy_shape and self._catalog_seen)
        return LibraryResult(ok=True)

    def _ensure_zone_invariant(self, *, migrated: bool = False) -> None:
        """区的地板：没有区就造一个默认区；激活位、分类归属、图上 zone 全部补齐。

        旧库（migrated=True 或整库无区）全部进默认区（第一个区）；
        新库里指不到区的分类/图也兜到默认区——宽松读的铁律是**永不因脏数据拒绝整本库**。
        """
        if not self._zones:
            self._zones = {new_zone_id(set()): {"name": DEFAULT_ZONE_NAME, "desc": ""}}
        first = next(iter(self._zones))
        if self._active_zone not in self._zones:
            self._active_zone = first
        for group in self._groups:
            self._group_zone.setdefault(group, first)
        for sticker_id, sticker in self._stickers.items():
            mapped = self._group_zone.get(sticker.group) if sticker.group else None
            want = mapped or (sticker.zone if sticker.zone in self._zones else first)
            if sticker.zone != want:
                self._stickers[sticker_id] = replace(sticker, zone=want)
                if sticker.group:
                    self._group_zone.setdefault(sticker.group, want)
        if migrated:
            # 一次性迁移写盘：失败只是下次再迁一遍（幂等），不把读拖死。
            self.save()
            self._log("catalog migrated to zones v2")

    @_serialized
    def save(self) -> LibraryResult:
        if self._read_failed:
            return LibraryResult.failure(ERR_IO, "catalog_unreadable")
        if not self._loaded:
            loaded = self.load()
            if not loaded.ok:
                return loaded
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": CATALOG_VERSION,
                "updated_at": time.time(),
                "stickers": [s.as_dict() for s in self._stickers.values()],
                "groups": dict(self._groups),
                "zones": [
                    {
                        **({"builtin": True} if meta.get("builtin") else {}),
                        "id": zone_id,
                        "name": meta["name"],
                        "desc": meta["desc"],
                    }
                    for zone_id, meta in self._zones.items()
                ],
                "active_zone": self._active_zone,
                "group_zone": dict(self._group_zone),
            }
            # J-2：播种台账只在真时写键（纯插入，旧库/未播种库的盘形一字不变）。
            if self._official_seeded:
                payload["official_seeded"] = True
            # J-3：已应用的官方包版本只在非零时写键，同一条纯插入纪律。
            if self._official_pack_version:
                payload["official_pack_version"] = self._official_pack_version
            tmp = self.catalog_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.catalog_path)
            self._catalog_seen = True
        except Exception:
            self._io_dirty = True
            return LibraryResult.failure(ERR_IO, "catalog_write_failed")
        self._io_dirty = False
        self._notify_saved()
        return LibraryResult(ok=True)

    def _notify_saved(self) -> None:
        """写盘成功的收口通知（v0.17.0：库变了 → 她的工具描述该重挂）。

        吞异常但不吞痕迹：库已经落盘了，不许被下游的打标动作反咬成"保存失败"，
        可静默吞掉又会造出"盘面变了、她看到的目录没变"这种查不到的账——所以必须
        warning 带栈（debug 不进日志文件，同 `services/turns.py` 那条纪律）。
        """
        hook = self._on_saved
        if not callable(hook):
            return
        try:
            hook()
        except Exception:  # noqa: BLE001 - 通知失败不改写"存下来了"这个事实
            if self._logger is not None:
                try:
                    self._logger.warning("library on_saved failed", exc_info=True)
                except Exception:
                    pass

    @property
    @_serialized
    def io_dirty(self) -> bool:
        """上一次读/写是否走过 IO 异常。面板横幅用它。"""
        return self._io_dirty

    # ------------------------------------------------------------------
    # 区（v0.11.0 J-1：区→分类→图的上层；她只感知激活区，其余整体隐形）
    # ------------------------------------------------------------------

    @_serialized
    def zones(self) -> list[dict[str, Any]]:
        """按 tab 序（插入序）列区：id/名/说明/是否激活/区内张数（含未分组）。"""
        counts: dict[str, int] = {}
        for sticker in self._stickers.values():
            zone_id = self.zone_of_sticker(sticker)
            counts[zone_id] = counts.get(zone_id, 0) + 1
        return [
            {
                "id": zone_id,
                "name": meta["name"],
                "desc": meta["desc"],
                "builtin": bool(meta.get("builtin")),
                "active": zone_id == self._active_zone,
                "total": counts.get(zone_id, 0),
            }
            for zone_id, meta in self._zones.items()
        ]

    @_serialized
    def zone_ids(self) -> set[str]:
        return set(self._zones)

    @_serialized
    def active_zone(self) -> str:
        return self._active_zone

    @_serialized
    def zone_of_group(self, group: str) -> str:
        return self._group_zone.get(group, "")

    @_serialized
    def zone_of_sticker(self, sticker: Sticker) -> str:
        """图在哪个区：分类住哪区它就在哪区（分类归属优先），未分组看自己的 zone。"""
        if sticker.group:
            mapped = self._group_zone.get(sticker.group)
            if mapped:
                return mapped
        return sticker.zone if sticker.zone in self._zones else self._active_zone

    @_serialized
    def zone_group_names(self, zone_id: str) -> set[str]:
        """一个区里的分类名（显式 ∪ 区内图在住的隐式）。跨区查名请走 `group_names()`。"""
        names = {g for g, z in self._group_zone.items() if z == zone_id and g in self._groups}
        names |= {s.group for s in self._stickers.values() if s.group and self.zone_of_sticker(s) == zone_id}
        return names

    @_serialized
    def create_zone(self, name: Any, desc: Any = "") -> tuple[str, str]:
        """新建一个区；回 (区 id, 错误码)。重名回 `zone_exists`（tab 撞名和分类撞名同理）。"""
        zone_name = normalize_zone_name(name)
        if not zone_name:
            return "", "zone_required"
        if any(meta["name"] == zone_name for meta in self._zones.values()):
            return "", "zone_exists"
        cleaned = desc.strip()[:GROUP_DESC_MAX_CHARS] if isinstance(desc, str) else ""
        zone_id = new_zone_id(set(self._zones))
        self._zones[zone_id] = {"name": zone_name, "desc": cleaned}
        saved = self.save()
        if not saved.ok:
            self._zones.pop(zone_id, None)
            return "", saved.code or ERR_IO
        self._log(f"zone created: id={zone_id}")
        return zone_id, ""

    @_serialized
    def rename_zone(self, zone_id: Any, name: Any) -> tuple[bool, str]:
        """改区名：内部 id 设计所以改名白送（分类改名的债不新埋）；回 (是否成功, 错误码)。"""
        if not isinstance(zone_id, str) or zone_id not in self._zones:
            return False, "zone_not_found"
        zone_name = normalize_zone_name(name)
        if not zone_name:
            return False, "zone_required"
        if any(meta["name"] == zone_name for zid, meta in self._zones.items() if zid != zone_id):
            return False, "zone_exists"
        previous = self._zones[zone_id]["name"]
        self._zones[zone_id] = {**self._zones[zone_id], "name": zone_name}
        saved = self.save()
        if not saved.ok:
            self._zones[zone_id] = {**self._zones[zone_id], "name": previous}
            return False, saved.code or ERR_IO
        return True, ""

    @_serialized
    def set_zone_desc(self, zone_id: Any, desc: Any) -> tuple[bool, str]:
        if not isinstance(zone_id, str) or zone_id not in self._zones:
            return False, "zone_not_found"
        cleaned = desc.strip()[:GROUP_DESC_MAX_CHARS] if isinstance(desc, str) else ""
        previous = self._zones[zone_id]["desc"]
        self._zones[zone_id] = {**self._zones[zone_id], "desc": cleaned}
        saved = self.save()
        if not saved.ok:
            self._zones[zone_id] = {**self._zones[zone_id], "desc": previous}
            return False, saved.code or ERR_IO
        return True, ""

    @_serialized
    def activate_zone(self, zone_id: Any) -> tuple[bool, str]:
        """切她的世界：激活区以外的分类/图从她目录、检索与发图候选里整体消失。"""
        if not isinstance(zone_id, str) or zone_id not in self._zones:
            return False, "zone_not_found"
        previous = self._active_zone
        self._active_zone = zone_id
        saved = self.save()
        if not saved.ok:
            self._active_zone = previous
            return False, saved.code or ERR_IO
        self._log(f"zone activated: id={zone_id}")
        return True, ""

    @_serialized
    def remove_zone(self, zone_id: Any) -> tuple[bool, str, int]:
        """拆区：**连带拆它全部分类与图**（与 remove_group 同一条裁决，放大一层）。

        回 (成功, 错误码, 删掉的张数)；最后一个区不许拆（`zone_last`，否则她的世界空了）；
        拆的是激活区时自动激活剩下的第一个。纪律照 `remove_group()`：先改内存、
        save() 成功再删文件，失败全量回滚，不留暗孤儿。
        """
        if not isinstance(zone_id, str) or zone_id not in self._zones:
            return False, "zone_not_found", 0
        if len(self._zones) <= 1:
            return False, "zone_last", 0
        victims = [(sid, s) for sid, s in self._stickers.items() if self.zone_of_sticker(s) == zone_id]
        gone_groups = [g for g, z in self._group_zone.items() if z == zone_id]
        snapshot_stickers = dict(self._stickers)
        snapshot_groups = dict(self._groups)
        snapshot_group_zone = dict(self._group_zone)
        snapshot_zone = self._zones.get(zone_id)
        snapshot_active = self._active_zone
        for sid, _ in victims:
            self._stickers.pop(sid)
        for group in gone_groups:
            self._groups.pop(group, None)
            self._group_zone.pop(group, None)
        self._zones.pop(zone_id)
        if self._active_zone == zone_id:
            self._active_zone = next(iter(self._zones))
        saved = self.save()
        if not saved.ok:
            self._stickers = snapshot_stickers
            self._groups = snapshot_groups
            self._group_zone = snapshot_group_zone
            if snapshot_zone is not None:
                self._zones[zone_id] = snapshot_zone
            self._active_zone = snapshot_active
            return False, saved.code or ERR_IO, 0
        for _, sticker in victims:
            try:
                self.image_path(sticker).unlink(missing_ok=True)
            except Exception:
                self._log(f"sticker file removal failed: id={sticker.id}")
        self._log(f"zone removed: id={zone_id} stickers={len(victims)}")
        return True, "", len(victims)

    # ------------------------------------------------------------------
    # 官方区播种（v0.12.0 J-2 P2A：完全内置 + 只播一次 + 恢复按钮）
    # ------------------------------------------------------------------

    @_serialized
    def official_seeded(self) -> bool:
        """播种台账（catalog 顶层 `official_seeded`）：封过盘就不再自动重播。"""
        return self._official_seeded

    @_serialized
    def official_zone(self) -> str:
        """官方区 id：`builtin` 位优先，其次同名收编（主人手建的「官方」区）；都没有回空串。

        真身尺是 builtin 位——收编那次就地补打；改名后靠位不靠名。
        """
        for zone_id, meta in self._zones.items():
            if meta.get("builtin"):
                return zone_id
        for zone_id, meta in self._zones.items():
            if meta["name"] == OFFICIAL_ZONE_NAME:
                return zone_id
        return ""

    @_serialized
    def seed_official(self, pack_path: Path, *, force: bool = False) -> dict[str, Any]:
        """把内置官方包收进官方区；回 {status, zone?, imported/duplicates/rejected/failed, refresh}。

        尺（拍板 P2A/P3，全部可重放）：
        - 只播一次：`official_seeded` 在册且非 force → status=already，**不再进图**；
          但仍然走一次标签刷新（J-3）——包更新的是"这张图是什么"，不是"图进没进来"；
        - force（恢复按钮）跳台账但照样吃指纹查重，不会重入重图；
        - 同名收编：有同名区就直接收编它（补打 builtin 位）而不是造重名区；
        - 不抢台：播种前全库有图 → 激活区不动；只有空库（新装态）才默认激活官方区；
        - 台账只在包干净（rejected+failed=0）时盖：半截/坏包下拍重试，幂等不重入；
        - 入库走 `import_pack` 全套尺（魔数/查重/原子写盘/zip-slip 免疫），不造第二条入库路。
        """
        loaded = self.load()
        if not loaded.ok:
            return {"status": "io", "error": loaded.code or ERR_IO}
        if self._official_seeded and not force:
            return {"status": "already", "refresh": self.refresh_official_labels(pack_path)}
        try:
            validate_official_assets(pack_path)
        except Exception:
            return {"status": "invalid_assets", "error": "official_pack_validation"}
        if force and self._official_pack_version:
            try:
                with open_pack(pack_path) as pack:
                    version = parse_pack_version(json.loads(read_pack_manifest(pack)))
            except Exception:
                version = 0
            if 0 < version < self._official_pack_version:
                return {"status": "invalid_assets", "error": "official_pack_older"}
        zone_id = self.official_zone()
        created = False
        if zone_id:
            if not self._zones[zone_id].get("builtin"):
                self._zones[zone_id] = {**self._zones[zone_id], "builtin": True}
                self.save()
        else:
            zone_id, error = self.create_zone(OFFICIAL_ZONE_NAME, "")
            if error:
                return {"status": "io", "error": error}
            created = True
            self._zones[zone_id] = {**self._zones[zone_id], "builtin": True}
            self.save()
        fresh_library = self.count() == 0
        upgraded = self.refresh_official_labels(pack_path) if force else None
        if upgraded and upgraded["status"] in {"io", "invalid_assets"}:
            return upgraded
        summary = self.import_pack(pack_path, zone=zone_id)
        if summary["rejected"] == 0 and summary["failed"] == 0:
            self._official_seeded = True
            self.save()
        if fresh_library and (summary["imported"] + summary["duplicates"]) > 0:
            # 新装语义：官方区就是她的默认世界；空库 force 恢复同样适用（合法的偏好默认）。
            self.activate_zone(zone_id)
        status = "restored" if force else "seeded"
        self._log(
            "official seed: status={} zone={} imported={} duplicates={} rejected={} failed={}".format(
                status, zone_id, summary["imported"], summary["duplicates"], summary["rejected"], summary["failed"]
            )
        )
        return {
            "status": status,
            "zone": zone_id,
            "created": created,
            "refresh": upgraded if upgraded and upgraded["status"] == "refreshed" else self.refresh_official_labels(pack_path),
            **summary,
        }

    @_serialized
    def official_pack_version(self) -> int:
        """已应用的官方包内容版本（catalog 顶层 `official_pack_version`）；0 = 从没刷过。"""
        return self._official_pack_version

    @_serialized
    def refresh_official_labels(self, pack_path: Path) -> dict[str, Any]:
        """官方包标签下发尺（v0.13.0 J-3）：包比库新时，按内容指纹把**官方区条目**的文本刷成包里的。

        补的是 J-2 留的洞：`import_pack` 对同指纹只计 duplicates 就跳过，`official_seeded`
        又是一次性台账——所以重打的官方包带着新分类/新梗义进来时，老装机一张也不会变。
        判断全在 `core/labeling.py`（纯函数，逐条钉死），这里只管 IO。

        只碰官方区现有条目，主人编辑过的字段让位、分类说明只补缺。包显式携带
        legacy_sha256 时可替换旧版图片；保持 id/启停/使用台账，不补回主动删除的图。
        图片先以新路径发布，catalog 原子保存成功后清旧图；失败回退内存与新文件。
        status：no_pack（没有官方区）/ unreadable（包读不出）/ no_version（包不带版本尺，
        主人自打的包与 export_pack 产物都算）/ current（不比库里新，什么都不做）/
        refreshed / io（写盘失败：版本不盖章，下次启动自然重试）。
        """
        zone_id = self.official_zone()
        if not zone_id or not self._zones[zone_id].get("builtin"):
            return {"status": "no_pack"}
        try:
            with open_pack(pack_path) as pack:
                raw = json.loads(read_pack_manifest(pack).decode("utf-8"))
        except Exception:
            return {"status": "unreadable"}
        version = parse_pack_version(raw)
        if version <= 0:
            return {"status": "no_version"}
        if version <= self._official_pack_version:
            return {"status": "current", "version": version}
        try:
            validate_official_assets(pack_path)
        except Exception:
            return {"status": "invalid_assets", "error": "official_pack_validation"}
        entries = parse_manifest(raw)
        official = [s for s in self._stickers.values() if self.zone_of_sticker(s) == zone_id]
        index = index_by_digest(entries)
        images: dict[str, tuple[str, str, bytes]] = {}
        try:
            with open_pack(pack_path) as pack:
                for sticker in official:
                    entry = index.get(sticker.sha256)
                    if entry is None or entry.sha256 == sticker.sha256:
                        continue
                    info = pack.getinfo(PACK_DIR_PREFIX + entry.file)
                    if info.file_size <= 0 or info.file_size > MAX_STICKER_BYTES:
                        return {"status": "invalid_assets", "error": "image_size"}
                    data = pack.read(info)
                    detected = detect_image_format(data)
                    if detected is None or content_sha256(data) != entry.sha256:
                        return {"status": "invalid_assets", "error": "image_digest_or_format"}
                    filename = f"{sticker.id}-{entry.sha256}.{detected[0]}"
                    if (self.stickers_dir / filename).resolve().parent != self.stickers_dir.resolve():
                        return {"status": "invalid_assets", "error": "image_path"}
                    images[sticker.id] = (filename, entry.sha256, data)
        except Exception:
            return {"status": "invalid_assets", "error": "image_unreadable"}
        plan = plan_label_refresh(
            official,
            entries,
            self._groups,
            parse_manifest_groups(raw),
        )
        old_stickers = dict(self._stickers)
        old_groups = dict(self._groups)
        old_group_zone = dict(self._group_zone)
        created: list[Path] = []
        # Publish different filenames first; the old catalog remains usable if interrupted.
        try:
            self.stickers_dir.mkdir(parents=True, exist_ok=True)
            for sticker_id, (filename, digest, data) in images.items():
                target = self.stickers_dir / filename
                if not target.exists():
                    created.append(target)
                temporary = target.with_suffix(target.suffix + ".tmp")
                try:
                    temporary.write_bytes(data)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                self._stickers[sticker_id] = replace(self._stickers[sticker_id], file=filename, sha256=digest)
        except Exception:
            self._stickers = old_stickers
            self._discard_upgrade_files(created)
            return {"status": "io", "error": ERR_IO}
        for sticker_id, patch in plan.patches:
            self._stickers[sticker_id] = replace(self._stickers[sticker_id], **patch)
        for group_name in plan.new_groups:
            # 包里的新分类登记进官方区（陷阱 23：分类不跨区搬家，已有归属的不改）。
            self._group_zone.setdefault(group_name, zone_id)
        self._groups.update(plan.group_descs)
        previous = self._official_pack_version
        self._official_pack_version = version
        saved = self.save()
        if not saved.ok:
            self._official_pack_version = previous
            self._stickers = old_stickers
            self._groups = old_groups
            self._group_zone = old_group_zone
            self._discard_upgrade_files(created)
            return {"status": "io", "error": saved.code}
        referenced = {s.file for s in self._stickers.values()}
        self._discard_upgrade_files([
            self.image_path(old_stickers[sticker_id])
            for sticker_id in images
            if old_stickers[sticker_id].file not in referenced
        ])
        self._log(
            "official labels refreshed: version={} refreshed={} skipped_edited={} unmatched={} groups={} images_updated={}".format(
                version, plan.refreshed, plan.skipped_edited, plan.unmatched, len(plan.group_descs), len(images)
            )
        )
        return {
            "status": "refreshed",
            "version": version,
            "refreshed": plan.refreshed,
            "images_updated": len(images),
            "skipped_edited": plan.skipped_edited,
            "unmatched": plan.unmatched,
            "groups": len(plan.group_descs),
        }

    # ------------------------------------------------------------------
    # 分组说明（v0.7.0 轮 F）
    # ------------------------------------------------------------------

    def _discard_upgrade_files(self, paths: list[Path]) -> None:
        for path in paths:
            try:
                if path.resolve().parent != self.stickers_dir.resolve():
                    continue
                path.unlink(missing_ok=True)
            except OSError:
                self._log(f"official asset cleanup deferred: {path.name}")

    @_serialized
    def group_descs(self) -> dict[str, str]:
        """组名 -> 一句话说明（副本）。"""
        return dict(self._groups)

    @_serialized
    def group_names(self) -> set[str]:
        """在册分类名（轮 I）：主人建过的（含零张的空分类）∪ 有图在住的（隐式分类）。

        一把尺量两处：入口层判“这个组存不存在”、面板层列可移入的分类，
        都从这里取，不再各自拼一遍 `sticker groups | group_descs`。
        """
        return {sticker.group for sticker in self._stickers.values() if sticker.group} | set(self._groups)

    @_serialized
    def set_group_desc(self, name: Any, desc: Any) -> tuple[bool, str]:
        """写/改/清一个分类的说明；回 (是否成功, 错误码)。

        合法性在入口层把关（group_required / group_desc_too_long），这里只兕宽钳位；
        **空说明 = 清空这句话，不是删掉这个分类**（轮 I：生死归 create/remove）。
        写盘失败回滚内存，不把假成功留给面板。
        """
        group = normalize_group(name)
        if not group:
            return False, "group_required"
        cleaned = desc.strip()[:GROUP_DESC_MAX_CHARS] if isinstance(desc, str) else ""
        existed = group in self._groups
        previous = self._groups.get(group, "")
        self._groups[group] = cleaned
        saved = self.save()
        if not saved.ok:
            if existed:
                self._groups[group] = previous
            else:
                self._groups.pop(group, None)
            return False, saved.code or ERR_IO
        return True, ""

    @_serialized
    def create_group(self, name: Any, desc: Any = "", zone: Any = "") -> tuple[bool, str]:
        """新建一个分类（轮 I：先立分类，再往分类里塞图；J-1：分类住在区里）；回 (是否成功, 错误码)。

        空分类是合法状态：她在目录里看不见它、也发不出它（`format_group_overview`
        与发图候选都只从有图的贴纸算）——分类是**管理概念**，不是发送目标。
        重名回 `group_exists`：**全局唯一**（跨区也算）——检索、目录、台账只认名字，
        允许两区同名会让她在激活区里发出另一区的图。区不存在回 `zone_not_found`。
        """
        group = normalize_group(name)
        if not group:
            return False, "group_required"
        if zone and (not isinstance(zone, str) or zone not in self._zones):
            return False, "zone_not_found"
        target_zone = zone if isinstance(zone, str) and zone else self._active_zone
        if group in self.group_names():
            return False, "group_exists"
        cleaned = desc.strip()[:GROUP_DESC_MAX_CHARS] if isinstance(desc, str) else ""
        self._groups[group] = cleaned
        self._group_zone[group] = target_zone
        saved = self.save()
        if not saved.ok:
            self._groups.pop(group, None)
            self._group_zone.pop(group, None)
            return False, saved.code or ERR_IO
        self._log(f"group created: {group} zone={target_zone}")
        return True, ""

    @_serialized
    def remove_group(self, name: Any) -> tuple[bool, str, int]:
        """删分类，**连带删掉这一组的图与文件**（主人拍板 1C）；回 (成功, 错误码, 删掉的张数)。

        纪律照 `remove()`：先改内存、`save()` 落盘成功再动文件（失败回滚内存，
        绝不留“目录里没这张、盘上还留着”的暗孤儿）；单个文件删不掉只记日志
        （孤儿文件由 `repair()` 收尾）。张数回给入口，面板确认后如实报数。
        """
        group = normalize_group(name)
        if not group:
            return False, "group_required", 0
        if group not in self.group_names():
            return False, "group_not_found", 0
        victims = [(sid, sticker) for sid, sticker in self._stickers.items() if sticker.group == group]
        for sid, _ in victims:
            self._stickers.pop(sid)
        existed = group in self._groups
        previous = self._groups.get(group, "")
        previous_zone = self._group_zone.pop(group, "")
        self._groups.pop(group, None)
        saved = self.save()
        if not saved.ok:
            for sid, sticker in victims:
                self._stickers[sid] = sticker
            if existed:
                self._groups[group] = previous
            if previous_zone:
                self._group_zone[group] = previous_zone
            return False, saved.code or ERR_IO, 0
        for sid, sticker in victims:
            try:
                self.image_path(sticker).unlink(missing_ok=True)
            except Exception:
                self._log(f"sticker file removal failed: id={sid}")
        self._log(f"group removed: {group} stickers={len(victims)}")
        return True, "", len(victims)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @_serialized
    def all(self) -> list[Sticker]:
        return sorted(
            self._stickers.values(),
            key=lambda s: -s.added_at,
        )

    @_serialized
    def active_pool(self) -> list[Sticker]:
        """她的全世界：激活区内的全部图（与 `all()` 同序）。

        J-1 的总闸：目录、检索、发图候选、awareness 一律从这里拿——
        非激活区对她整体隐形（陷阱 21 的推广版；新防回归门钉这条）。
        """
        return [s for s in self.all() if self.zone_of_sticker(s) == self._active_zone]

    @_serialized
    def get(self, sticker_id: str) -> Sticker | None:
        return self._stickers.get(sticker_id)

    @_serialized
    def count(self) -> int:
        return len(self._stickers)

    def image_path(self, sticker: Sticker) -> Path:
        # file 字段只在本类内部生成（<id>.<ext>），不接受外部输入拼接，
        # 所以这里不需要再做路径逃逸检查——检查在 add() 的入库口。
        return self.stickers_dir / sticker.file

    @_serialized
    def thumb_for(self, sticker: Sticker, *, build: Any = build_thumb) -> bytes | None:
        """取这张图的缩略图字节；盘上没有就现做一份并落盘。`None` = 拿不到，调用方**降级回原图**。

        缓存键是**内容指纹**不是 sticker id：内容变了自然重算，所以这里不需要任何失效逻辑，
        同图重建（换了 id）也自动共用一份。整目录被删也不坏——下次要时重生成。

        两条不许做错的尺：
        - **命中缓存时绝不读原图**（本轮省的就是那 34.7MB，顺手 `read_bytes()` 一下等于白做）；
        - 任何一步失败都只许"没有缩略图"，不许让格子空白——所以降级路径是回原图，不是报错。
        """
        digest = sticker.sha256
        if digest:
            cached = self.thumbs_dir / thumb_filename(digest)
            if cached.is_file():
                try:
                    return cached.read_bytes()
                except Exception:  # noqa: BLE001 - 缓存读不动就重做一份，别把图搞没
                    self._log("thumb cache unreadable; regenerating")
        try:
            data = self.image_path(sticker).read_bytes()
        except Exception:  # noqa: BLE001 - 原图都没了：preview 那条路会如实报 sticker_file_missing
            return None
        digest = digest or content_sha256(data)
        blob = build(data)
        if not blob:
            return None
        path = self.thumbs_dir / thumb_filename(digest)
        try:
            self.thumbs_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".jpg.tmp")
            tmp.write_bytes(blob)
            tmp.replace(path)
        except Exception:  # noqa: BLE001 - 写不下只是下次还要重做，这一次照样给得出图
            self._log("thumb cache write failed; serving it anyway")
        return blob

    # ------------------------------------------------------------------
    # 写路径
    # ------------------------------------------------------------------

    def _find_duplicate(self, digest: str) -> Sticker | None:
        """按内容指纹找同图。旧条目（v0.1.1 前入库、无指纹）现算并回填——
        回填是顺手的、幂等的；算不动（文件坏了）则跳过该条目，不阻断查重。
        """
        backfilled = False
        hit: Sticker | None = None
        for sticker in list(self._stickers.values()):
            sha = sticker.sha256
            if not sha:
                try:
                    sha = content_sha256(self.image_path(sticker).read_bytes())
                except Exception:
                    continue
                self._stickers[sticker.id] = sticker.with_sha256(sha)
                backfilled = True
            if sha == digest and hit is None:
                hit = sticker
        if backfilled:
            self.save()  # 回填失败不阻断入库（下次 repair/查重会再试）
        return hit

    @_serialized
    def add(
        self,
        *,
        data: bytes,
        desc: str,
        tags: list[str],
        now: float | None = None,
        group: str = "",
        zone: str = "",
        caption: str = "",
        visible_text: str = "",
    ) -> tuple[Sticker | None, str]:
        """入库一张图。返回 (条目, 错误码)；成功时错误码为空。

        错误码：invalid_image（不是受支持的图片格式）/ duplicate_image（库里已有同图）/ io_error。
        查重只认内容指纹，不认文件名（见 core/catalog 设计决定 5）。
        group/caption/visible_text 由入口层收敛后才进来（core 层负责合法性，这里只搬运）。
        区的尺（J-1）：分好类的图跟着分类走（分类住哪区就哪区）；未分组用 zone 参数，
        再缺省落激活区。
        """
        detected = detect_image_format(data or b"")
        loaded = self.load()
        if not loaded.ok:
            return None, loaded.code
        if detected is None:
            return None, "invalid_image"
        digest = content_sha256(data)
        if self._find_duplicate(digest) is not None:
            return None, ERR_DUPLICATE
        moment = time.time() if now is None else now
        try:
            sticker_id = new_sticker_id(self._stickers.keys())
        except RuntimeError:
            return None, ERR_IO
        ext, _mime = detected
        file_name = f"{sticker_id}.{ext}"
        try:
            self.stickers_dir.mkdir(parents=True, exist_ok=True)
            target = self.stickers_dir / file_name
            if target.exists():  # 16 次重采样仍撞名（几乎不可能）：换个 id 再来一次
                sticker_id = new_sticker_id({*self._stickers.keys(), file_name.split(".")[0]})
                file_name = f"{sticker_id}.{ext}"
                target = self.stickers_dir / file_name
            target.write_bytes(data)
        except Exception:
            return None, ERR_IO
        sticker = Sticker(
            id=sticker_id,
            file=file_name,
            desc=desc,
            tags=list(tags),
            added_at=moment,
            sha256=digest,
            group=group,
            zone=self._group_zone.get(group) or (zone if zone in self._zones else "") or self._active_zone,
            caption=caption,
            visible_text=visible_text,
        )
        self._stickers[sticker_id] = sticker
        saved = self.save()
        if not saved.ok:
            self._stickers.pop(sticker_id, None)
            return None, saved.code
        self._log(f"sticker added: id={sticker_id} bytes={len(data)}")
        return sticker, ""

    @_serialized
    def update(
        self,
        sticker_id: str,
        *,
        desc: str | None = None,
        tags: list[str] | None = None,
        disabled: bool | None = None,
        group: str | None = None,
        caption: str | None = None,
        visible_text: str | None = None,
    ) -> tuple[Sticker | None, str]:
        """改描述/标签/禁用态/分组/梗义/图内文字；None = 不改那一项（合法性由入口层把关）。

        v0.3.0 起用 dataclasses.replace 重建：sha256 回归（v0.1.x 每编辑一次丢一次指纹，
        当时靠手工补字段治的）的真病根是"手工枚举字段的拷贝重建"——每加一个字段
        就多一个漏写即丢数据的雷，replace 从构造上灭掉整类雷。
        """
        sticker = self._stickers.get(sticker_id)
        if sticker is None:
            return None, ERR_NOT_FOUND
        patch: dict[str, Any] = {}
        if desc is not None:
            patch["desc"] = desc
        if tags is not None:
            patch["tags"] = list(tags)
        if disabled is not None:
            patch["disabled"] = bool(disabled)
        if group is not None:
            patch["group"] = group
        if caption is not None:
            patch["caption"] = caption
        if visible_text is not None:
            patch["visible_text"] = visible_text
        touched = {"desc", "tags", "group", "caption", "visible_text"} & set(patch)
        if touched:
            # J-3：记下主人亲手改过哪些字段（只改启停不算）——官方包刷标签时按字段让位。
            patch["owner_edited"] = sorted(set(sticker.owner_edited) | touched)
        if "group" in patch and patch["group"]:
            # J-1：换分类 = 换住户；图跟着分类搬区（隐式新名字就地登记到目标区）。
            mapped = self._group_zone.get(patch["group"]) or sticker.zone or self._active_zone
            self._group_zone.setdefault(patch["group"], mapped)
            patch["zone"] = mapped
        updated = replace(sticker, **patch) if patch else sticker
        self._stickers[sticker_id] = updated
        saved = self.save()
        if not saved.ok:
            self._stickers[sticker_id] = sticker
            return None, saved.code
        return updated, ""

    @_serialized
    def touch_used(self, sticker_id: str, *, now: float) -> None:
        """记一次使用。失败不回滚发送——台账是弱一致的一刻。"""
        sticker = self._stickers.get(sticker_id)
        if sticker is None:
            return
        self._stickers[sticker_id] = sticker.with_touch(now=now)
        self.save()

    @_serialized
    def remove(self, sticker_id: str) -> str:
        """删除条目与文件。返回错误码（空 = 成功）。"""
        sticker = self._stickers.pop(sticker_id, None)
        if sticker is None:
            return ERR_NOT_FOUND
        saved = self.save()
        if not saved.ok:
            self._stickers[sticker_id] = sticker
            return saved.code
        try:
            self.image_path(sticker).unlink(missing_ok=True)
        except Exception:
            self._log(f"sticker file removal failed: id={sticker_id}")
        self._log(f"sticker removed: id={sticker_id}")
        return ""

    # ------------------------------------------------------------------
    # 自修复
    # ------------------------------------------------------------------

    @_serialized
    def repair(self) -> dict[str, int]:
        """库体检：清掉文件已丢失的条目、删掉没人引用的孤儿文件、回填旧条目指纹。

        返回计数字典（面板如实展示）：removed_entries / purged_files / backfilled_hashes。
        纪律：只在自己生成的 `stickers/` 目录里活动；条目表就是引用集，
        不在表里的文件视为孤儿（目录里的文件全部由 add() 生成，无用户自放位）。
        区卫生（J-1）同批：分类无人住且无说明→退册；图上 zone 记法统一成规范形。
        """
        removed_entries = 0
        purged_files = 0
        backfilled = 0
        if not self.load().ok:
            self._log("library repair blocked: catalog unreadable")
            return {"removed_entries": 0, "purged_files": 0, "backfilled_hashes": 0}
        referenced: set[str] = set()
        dirty = False
        for sticker_id in list(self._stickers.keys()):
            sticker = self._stickers[sticker_id]
            path = self.image_path(sticker)
            if not path.is_file():
                self._stickers.pop(sticker_id)
                removed_entries += 1
                dirty = True
                continue
            referenced.add(sticker.file)
            if not sticker.sha256:
                try:
                    digest = content_sha256(path.read_bytes())
                except Exception:
                    continue  # 读不动的图：不回填也不删条目，交给下次体检
                self._stickers[sticker_id] = sticker.with_sha256(digest)
                backfilled += 1
                dirty = True
        if dirty:
            self.save()
        # 区卫生（J-1，不进计数字典：面板三格形状不变）。
        live_groups = {sticker.group for sticker in self._stickers.values() if sticker.group}
        stale = [g for g, z in self._group_zone.items() if g not in live_groups and g not in self._groups]
        for group in stale:
            self._group_zone.pop(group, None)
        for sticker_id, sticker in list(self._stickers.items()):
            want = self.zone_of_sticker(sticker)
            if sticker.group:
                self._group_zone.setdefault(sticker.group, want)
            if sticker.zone != want:
                self._stickers[sticker_id] = replace(sticker, zone=want)
                dirty = True
        if dirty:
            self.save()
        try:
            if self.stickers_dir.is_dir():
                for candidate in self.stickers_dir.iterdir():
                    if candidate.is_file() and candidate.name not in referenced:
                        try:
                            candidate.unlink()
                            purged_files += 1
                        except Exception:
                            self._log(f"orphan purge failed: {candidate.name}")
        except Exception:
            self._log("orphan scan failed")
        # 缩略图缓存同批回收（v0.17.2）：目录里只认我们自己的命名（内容指纹 + 版本 + 尺寸），
        # 别的一律不碰——这个目录理论上全是生成的，但"顺手全删"不是体检该做的事。
        # 计数并进 purged_files（面板三格形状不动），分项数字进日志。
        purged_thumbs = 0
        wanted_thumbs = {thumb_filename(s.sha256) for s in self._stickers.values() if s.sha256}
        try:
            if self.thumbs_dir.is_dir():
                for candidate in self.thumbs_dir.iterdir():
                    if not candidate.is_file() or THUMB_CACHE_VERSION not in candidate.name:
                        continue
                    if candidate.name in wanted_thumbs:
                        continue
                    try:
                        candidate.unlink()
                        purged_thumbs += 1
                        purged_files += 1
                    except Exception:
                        self._log(f"thumb purge failed: {candidate.name}")
        except Exception:
            self._log("thumb scan failed")
        self._log(
            f"library repaired: entries_removed={removed_entries} "
            f"orphans_purged={purged_files} (thumbs={purged_thumbs}) "
            f"hashes_backfilled={backfilled}"
        )
        return {
            "removed_entries": removed_entries,
            "purged_files": purged_files,
            "backfilled_hashes": backfilled,
        }

    # ------------------------------------------------------------------
    # 套图包导出 / 导入（v0.2.0）
    # ------------------------------------------------------------------

    @property
    def exports_dir(self) -> Path:
        return self._root / EXPORTS_DIRNAME

    @_serialized
    def export_pack(self, *, now: float | None = None) -> tuple[dict[str, Any], str]:
        """把整本库打成套图 zip 写进 `exports/`。返回 (结果, 错误码)。

        结果是给面板的小回包：`{"file": 路径, "exported": n, "skipped": n}`——
        **绝不回包字节**（ZeroMQ 控制帧 4.56MiB 上限，见 DESIGN 陷阱 14；
        图字节只进磁盘，不进返回值）。
        """
        moment = time.time() if now is None else now
        stickers = self.all()
        if not stickers:
            return {}, ERR_EMPTY
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))
        try:
            self.exports_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return {}, ERR_IO
        target = self.exports_dir / f"stickers-{stamp}.zip"
        suffix = 0
        while target.exists():  # 同一秒连点两次导出：如实换名，不覆盖
            suffix += 1
            target = self.exports_dir / f"stickers-{stamp}-{suffix}.zip"
        exported = 0
        skipped = 0
        included: list[Sticker] = []
        for sticker in stickers:
            try:
                self.image_path(sticker).read_bytes()  # 只验可读，字节由 zip 自己写
            except Exception:
                skipped += 1
                continue
            included.append(sticker)
            exported += 1
        if exported > PACK_MAX_ENTRIES:
            return {}, "pack_too_many_entries"
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as pack:
                pack.writestr(
                    PACK_MANIFEST_FILENAME,
                    json.dumps(build_manifest(included, self.group_descs()), ensure_ascii=False, indent=2),
                )
                for sticker in included:
                    pack.write(self.image_path(sticker), arcname=f"{PACK_DIR_PREFIX}{sticker.file}")
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            return {}, ERR_IO
        self._log(f"pack exported: file={target.name} stickers={exported} skipped={skipped}")
        return {"file": str(target), "exported": exported, "skipped": skipped}, ""

    @_serialized
    def import_pack(
        self, path: Path, *, group: str = "", tags: list[str] | None = None, zone: str = ""
    ) -> dict[str, int]:
        """导入一个套图 zip（manifest 协议见 core/pack）。返回与收件箱同款四类计数。

        区的尺（J-1）：整包收进 `zone` 指定的区（缺省落激活区）；包内分类就地登记到那个区。

        纪律：
        - **zip-slip**：条目名过 `safe_member_name`（core 层唯一的门），目录/上跳/隐藏一律拒；
        - 单张字节上限先查 `ZipInfo.file_size` 再读（防 zip bomb 把解压撑进内存）；
        - 图字节仍走 add 的全套规则（魔数/查重/原子写盘），不造第二条入库路；
        - 有 manifest 认 manifest（描述/标签/分组随行就市），没有就按裸图集处理，
          描述取文件名清洗——与收件箱单文件通道同一形态；两种形态都吃整批 tags/group 兜底。
        """
        summary = {"imported": 0, "duplicates": 0, "rejected": 0, "failed": 0}
        if not self.load().ok or (zone and zone not in self._zones):
            summary["failed"] += 1
            return summary
        target_zone = zone or self._active_zone
        group = normalize_group(group)
        group_zones = {name: self.zone_of_group(name) or self._active_zone for name in self.group_names()}
        group_zones.update({
            sticker.group: self.zone_of_sticker(sticker)
            for sticker in self._stickers.values() if sticker.group
        })
        try:
            pack = open_pack(path)
        except Exception:
            self._log(f"pack unreadable: {path.name}")
            summary["failed"] += 1
            return summary
        with pack:
            try:
                manifest_raw = json.loads(read_pack_manifest(pack).decode("utf-8"))
            except ManifestLimitError:
                summary["failed"] += 1
                self._log("pack rejected: manifest size limit")
                return summary
            except KeyError:
                manifest_raw = None
            except Exception:
                # manifest 在但坏了：图仍可救（按裸包处理），如实记一笔日志。
                self._log(f"pack manifest broken, falling back to bare mode: {path.name}")
                manifest_raw = None
            # The manifest byte budget bounds parsing; count overflow before enforcing the image cap.
            parsed = parse_manifest(manifest_raw, limit=None) if manifest_raw is not None else []
            pack_groups = parse_manifest_groups(manifest_raw) if manifest_raw is not None else {}
            imported_groups: set[str] = set()
            if parsed:
                members = {f"{PACK_DIR_PREFIX}{entry.file}": entry for entry in parsed}
            else:
                members = {name: None for name in pack.namelist() if safe_member_name(name)}
            if len(members) > PACK_MAX_ENTRIES:
                # 超大包：收下前 PACK_MAX_ENTRIES 个，多出的整批计 rejected。
                summary["rejected"] += len(members) - PACK_MAX_ENTRIES
                members = dict(list(members.items())[:PACK_MAX_ENTRIES])
            for name, entry in members.items():
                try:
                    info = pack.getinfo(name)
                except KeyError:
                    summary["failed"] += 1  # manifest 报了名但包里没有：如实计
                    continue
                if info.is_dir() or info.file_size > MAX_STICKER_BYTES:
                    summary["rejected"] += 1
                    continue
                try:
                    data = pack.read(name)
                except Exception:
                    summary["failed"] += 1
                    continue
                file_name = safe_member_name(name)
                entry_group = entry.group if entry is not None else ""
                chosen_group = entry_group or group
                if chosen_group in group_zones and group_zones[chosen_group] != target_zone:
                    if self._find_duplicate(content_sha256(data)) is not None:
                        summary["duplicates"] += 1
                    else:
                        summary["rejected"] += 1
                        self._log("pack image rejected: category belongs to another zone")
                    continue
                entry_tags = list(entry.tags) if entry is not None and entry.tags else list(tags or [])
                sticker, error = self.add(
                    data=data,
                    desc=entry.desc if entry is not None else desc_from_filename(file_name),
                    tags=entry_tags,
                    group=chosen_group,
                    zone=target_zone,
                    caption=entry.caption if entry is not None else "",
                    visible_text=entry.visible_text if entry is not None else "",
                    now=time.time(),
                )
                if error == ERR_DUPLICATE:
                    summary["duplicates"] += 1
                elif error:
                    summary["rejected"] += 1
                else:
                    summary["imported"] += 1
                    if sticker is not None and sticker.group:
                        imported_groups.add(sticker.group)
            # 分组说明随包迁移（轮 F）：只补缺不覆盖——主人已写过的组话不被包/import 消音。
            # J-1：同时把包里的分类登记进目标区（已有归属的不改——分类不跨区搬家）。
            changed = False
            for group_name in imported_groups:
                if group_name not in self._group_zone:
                    self._group_zone[group_name] = target_zone
                    changed = True
                group_desc = pack_groups.get(group_name, "")
                if group_desc and not self._groups.get(group_name):
                    self._groups[group_name] = group_desc
                    changed = True
            if changed:
                self.save()
        self._log(
            "pack ingested: {} imported={imported} duplicates={duplicates} rejected={rejected} failed={failed}".format(
                path.name, **summary
            )
        )
        return summary

    # ------------------------------------------------------------------
    # 收件箱导入
    # ------------------------------------------------------------------

    @_serialized
    def ingest_inbox(
        self,
        *,
        tags: list[str],
        group: str = "",
        zone: str = "",
        max_bytes: int = MAX_STICKER_BYTES,
    ) -> dict[str, int]:
        """把收件箱里的图片逐张收进库（描述取自文件名，走 add 的全部规则：
        魔数、查重、原子写盘）。`.zip` 按套图包整批收（v0.2.0）。

        处置纪律：成功与重复的源文件删掉（重复件留着只会在下次体检里再报一遍）；
        超限/坏图/读不动的**保留原地**，让用户能改好后重试。
        zip 包的纪律是同一条尺的整包版：包内**有任何**未收下的（rejected/failed）
        就留包原地——已收下的图有指纹，重试整包时它们只会计"重复"，不重复入库。
        返回四类计数：imported / duplicates / rejected / failed（隐藏文件不计）。
        """
        summary = {"imported": 0, "duplicates": 0, "rejected": 0, "failed": 0}
        for path in self.inbox_files():
            if path.suffix.lower() == ".zip":
                pack_summary = self.import_pack(path, group=group, tags=tags, zone=zone)
                if not (pack_summary["rejected"] or pack_summary["failed"]):
                    self._discard_inbox_file(path)
                for key in summary:
                    summary[key] += pack_summary[key]
                continue
            try:
                data = path.read_bytes()
            except Exception:
                summary["failed"] += 1
                continue
            if len(data) > max_bytes:
                summary["rejected"] += 1
                continue
            sticker, error = self.add(
                data=data,
                desc=desc_from_filename(path.name),
                tags=tags,
                group=group,
                zone=zone,
                now=time.time(),
            )
            if error == ERR_DUPLICATE:
                summary["duplicates"] += 1
                self._discard_inbox_file(path)
            elif error:
                summary["rejected"] += 1  # invalid_image / io_error：文件留着
            else:
                summary["imported"] += 1
                self._discard_inbox_file(path)
        self._log(
            "inbox ingested: imported={imported} duplicates={duplicates} rejected={rejected} failed={failed}".format(
                **summary
            )
        )
        return summary

    def _discard_inbox_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            self._log(f"inbox file discard failed: {path.name}")

    # ------------------------------------------------------------------
    # zip 直传会话（v0.6.0：面板“选择文件”导入，不再依赖“放收件箱 + 点读取”）
    # ------------------------------------------------------------------
    # 为什么分块：面板→entry 的 args 与预览回包走同一条 ZeroMQ 控制通道，
    # 单帧硬上限 4,784,128 字节（见 core.catalog PREVIEW_CHUNK_BYTES 注释）——
    # 几 MiB 的套图包整块 base64 会被直接拒。方向相反：预览是服务端分段**回**，
    # 这里是面板分段**来**。会话只存本进程内存 + data/uploads/.sid.part：
    # 重启即作废，重选文件即可（断点续传不值得为这种短生命周期交互引入复杂度）。

    @property
    def uploads_dir(self) -> Path:
        return self._root / UPLOADS_DIRNAME

    @_serialized
    def upload_start(self, filename: Any, size: Any = None) -> tuple[str, str]:
        """开一个上传会话：只认 .zip 结尾（图走 add 通道，不重复造第二条入库路）。

        回 (session_id, 错误码)；错误码为空串 = 成功。可选的 size 只用来
        提前拒绝“一眼就知道装不下”的包（真正的总量上限在每块 append 时把关）。
        """
        name = safe_member_name(filename)
        # safe_member_name 对"../x.zip"是**剥成 x.zip**而非拒——目录形状在这里没有合法用途
        # （面板传来的就该是裸文件名），收到带路径的名宁可拒也不静默改写：诚实 > 宽容。
        if not isinstance(filename, str) or "/" in filename or "\\" in filename:
            return "", "upload_not_zip"
        if not name or not name.lower().endswith(".zip"):
            return "", "upload_not_zip"
        if isinstance(size, int) and not isinstance(size, bool) and size > UPLOAD_MAX_TOTAL_BYTES:
            return "", "upload_too_large"
        self._gc_uploads()
        sid = new_sticker_id(set(self._uploads))
        path = self.uploads_dir / f".{sid}.part"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("wb")
        except Exception:
            self._log("upload staging open failed")
            return "", "upload_write_failed"
        self._uploads[sid] = {"h": handle, "path": path, "seq": 0, "size": 0, "name": name, "at": time.time()}
        return sid, ""

    @_serialized
    def upload_append(self, sid: str, seq: Any, data: bytes) -> str:
        """按 seq 顺序追加一块；乱序/超限/写失败都**作废会话**（不留半截尸体让人重试错对象）。

        回错误码；空串 = 成功。seq 必须连续——面板循环保证；乱序只可能
        是网络重放/并发错乱，宁可作废重选也不能静默拼出一个坏 zip。
        """
        session = self._uploads.get(sid) if isinstance(sid, str) else None
        if session is None:
            return "upload_session_unknown"
        if seq != session["seq"]:
            self._drop_upload(sid)
            return "upload_seq_gap"
        if len(data) > UPLOAD_CHUNK_BYTES or session["size"] + len(data) > UPLOAD_MAX_TOTAL_BYTES:
            self._drop_upload(sid)
            return "upload_too_large"
        try:
            session["h"].write(data)
        except Exception:
            self._drop_upload(sid)
            self._log("upload chunk write failed")
            return "upload_write_failed"
        session["seq"] = session["seq"] + 1
        session["size"] += len(data)
        session["at"] = time.time()
        return ""

    @_serialized
    def upload_finish(
        self, sid: str, *, tags: list[str] | None = None, group: str = "", zone: str = ""
    ) -> tuple[dict[str, int], str]:
        """收尾：关流→同一把尺 import_pack→删暂存体。

        与 inbox “成功即删/失败留原地重试”不同：直传会话没有“原地”——面板会话
        是一次性的，无论导入结果如何都删暂存体（成败已进 summary 四类计数，
        重试 = 重选文件）。包内部分失败不拼掉整次上传：计数如实回。
        """
        session = self._uploads.pop(sid, None) if isinstance(sid, str) else None
        if session is None:
            return {}, "upload_session_unknown"
        try:
            session["h"].close()
        except Exception:
            pass
        if session["size"] == 0:
            self._drop_upload_file(session["path"])
            return {}, "upload_empty"
        summary = self.import_pack(session["path"], tags=tags, group=group, zone=zone)
        self._drop_upload_file(session["path"])
        return summary, ""

    def _drop_upload(self, sid: str) -> None:
        session = self._uploads.pop(sid, None)
        if session is not None:
            try:
                session["h"].close()
            except Exception:
                pass
            self._drop_upload_file(session["path"])

    @staticmethod
    def _drop_upload_file(path: Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    def _gc_uploads(self, *, older_than_sec: float = 24 * 3600) -> None:
        """新开会话前顺带扫死体：内存会话按 at 过期；盘上残留的 .*.part（上次崩溃/断电）
        按 mtime 过期。隐藏名开头 + 固定后缀，不伤及任何正经文件。"""
        now = time.time()
        for sid in list(self._uploads):
            if now - self._uploads[sid]["at"] > older_than_sec:
                self._drop_upload(sid)
        try:
            if self.uploads_dir.is_dir():
                for path in self.uploads_dir.iterdir():
                    if not path.name.startswith(".") or not path.name.endswith(".part"):
                        continue
                    try:
                        if path.is_file() and now - path.stat().st_mtime > older_than_sec:
                            path.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            self._log("uploads gc scan failed")

    # ------------------------------------------------------------------
    # 使用台账
    # ------------------------------------------------------------------

    def usage_path(self) -> Path:
        return self._root / USAGE_FILENAME

    @_serialized
    def read_usage(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.usage_path().read_text(encoding="utf-8"))
        except Exception:
            return []
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return []
        out: list[dict[str, Any]] = []
        for item in entries[-limit:]:
            if isinstance(item, dict):
                out.append(dict(item))
        out.reverse()  # 新的在前
        return out

    @_serialized
    def append_usage(self, record: dict[str, Any], *, keep: int) -> None:
        """追加一条使用记录（只放非隐私字段：时刻/表情 id/角色名/来源/成败）。

        落盘按时间正序（新的在尾部），`read_usage` 负责反转成"新的在前"；
        容量超过 keep 时从头部丢弃旧记录。
        """
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            entries: list[dict[str, Any]] = []
            try:
                raw = json.loads(self.usage_path().read_text(encoding="utf-8"))
                listed = raw.get("entries") if isinstance(raw, dict) else None
                if isinstance(listed, list):
                    entries = [dict(item) for item in listed if isinstance(item, dict)]
            except Exception:
                entries = []
            entries.append(dict(record))
            entries = entries[-max(20, keep) :]
            payload = {"version": 1, "entries": entries}
            tmp = self.usage_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.usage_path())
        except Exception:
            self._log("usage ledger write failed")
