"""套图包（v0.2.0）：导出/导入的 manifest 协议与 zip 安全门——全部纯函数。

为什么要有"套图包"：主人从别处（自己攒的目录、别人的收藏）拿到一批表情时，
一张张重传会把 v0.1.2 辛苦攒下的描述/标签/分组全部丢掉。包 = **图 + 台账的快照**：
`manifest.json`（条目元数据）+ `stickers/<file>`（图片本体），打成 zip 就是可迁移的套图。

刻意**不兼容** 外部系统的 `memes_data.json` 格式（用户拍板）：
那是别人仓演进中的形状，钉死它 = 把我们的导入面绑在别人版本号上。

zip-slip 纪律（`safe_member_name`）：包内条目名是**不可信输入**——
`../../evil.png` 或绝对路径在解包时会写到库目录外面去。这里只允许"纯文件名"
（不含目录分隔、不含 `..`、不以点开头），图片扩展名由魔数嗅探把关（core.catalog），
manifest 里的 `file` 字段与 zip 条目名要**一致**（同一把安全尺量两遍）。

frozen：本模块零 IO、零 SDK——zip 的读与写在 `services/library.py`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .catalog import (
    CAPTION_MAX_CHARS,
    GROUP_DESC_MAX_CHARS,
    VISIBLE_TEXT_MAX_CHARS,
    Sticker,
    desc_from_filename,
    normalize_group,
    normalize_optional_text,
    normalize_tags,
    validate_desc,
)

# v2（v0.3.0）：条目新增 caption/visible_text。v3（轮 F）：顶层新增 groups（组名→一句话说明，
# “分类=描述”随包迁移）。导入侧从不按版本号硬拒
# （旧 reader 遇新键会自然忽略，新 reader 遇旧包缺键回空），升号只为诚实。
PACK_MANIFEST_VERSION = 3
PACK_MANIFEST_FILENAME = "manifest.json"
# zip 内图片条目的目录前缀。导入时剥掉；导出时必须用同一个。
PACK_DIR_PREFIX = "stickers/"
# 官方包的内容版本键（v0.13.0 J-3）：随包携带，库记已应用值，包更新时刷官方区标签。
# 只有官方包写它——`export_pack` 的产物不带（带了就等于拿主人的库当官方库覆写）。
PACK_VERSION_KEY = "pack_version"

# 单个套图包最多收多少张（防一个"手滑打了整盘截图"的 zip 把库淹了；
# 与面板批量通道 MAX_BATCH_FILES 同量级但更宽——包里带描述，逐张确认成本低）。
PACK_MAX_ENTRIES = 512


@dataclass(frozen=True)
class PackEntry:
    """manifest 里的一张表情（id 不跨包——导入时重新发号）。"""

    file: str
    desc: str
    tags: list[str] = field(default_factory=list)
    group: str = ""
    sha256: str = ""
    caption: str = ""
    visible_text: str = ""
    legacy_sha256: list[str] = field(default_factory=list)


def safe_member_name(name: Any) -> str:
    """把 zip 条目名/manifest 里的 file 字段收敛成**安全的纯文件名**；不合法给空串。

    拦的三类（zip-slip 的经典三面）：目录分隔（`/` 或 `\\`）、上跳（`..`）、
    隐藏/盘符（点开头或含冒号）。留下的就是 `abc12.png` 这种一个词的形状。
    """
    if not isinstance(name, str):
        return ""
    raw = name.strip().replace("\\", "/")
    if not raw or ":" in raw:
        return ""
    base = raw.split("/")[-1]
    if not base or base == ".." or base.startswith(".") or "/" in base:
        return ""
    # 再兜一层：Windows 保留名与空白形状不做文件名。
    if base.rstrip() != base or base.rstrip(" ") != base:
        return ""
    return base[:96]


def pack_entry_from_raw(raw: Any) -> PackEntry | None:
    """宽松还原一条 manifest 条目；救不了的回 None（不炸整个包）。

    描述在这里**不设死线**：坏描述回退到文件名清洗（core.catalog.desc_from_filename
    同规则），导入的目标是"图先进来、描述可以后改"，不是复读 add 入口的校验。
    """
    if not isinstance(raw, Mapping):
        return None
    member = safe_member_name(raw.get("file"))
    if not member:
        return None
    desc, desc_error = validate_desc(raw.get("desc"))
    if desc_error:
        desc = desc_from_filename(member)
    digest = raw.get(
        "sha256"
    )  # 局部变量收窄：三元里双调 raw.get(...) 静态侧钉不住类型（同 Sticker.from_dict 的 sha 处理）
    return PackEntry(
        file=member,
        desc=desc,
        tags=normalize_tags(raw.get("tags")),
        group=normalize_group(raw.get("group")),
        sha256=digest if isinstance(digest, str) else "",
        caption=normalize_optional_text(raw.get("caption"), limit=CAPTION_MAX_CHARS),
        visible_text=normalize_optional_text(raw.get("visible_text"), limit=VISIBLE_TEXT_MAX_CHARS),
        legacy_sha256=parse_legacy_digests(raw.get("legacy_sha256")),
    )



def parse_legacy_digests(raw: Any) -> list[str]:
    """Read explicit previous image fingerprints for official asset upgrades."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            continue
        digest = value.lower()
        if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest) and digest not in out:
            out.append(digest)
    return out

def parse_manifest(raw: Any, *, limit: int | None = PACK_MAX_ENTRIES) -> list[PackEntry]:
    """manifest.json 的顶层解析：认不出形状给空列表，能救的按原顺序救。"""
    if not isinstance(raw, Mapping):
        return []
    entries = raw.get("stickers")
    if not isinstance(entries, Iterable) or isinstance(entries, (str, bytes)):
        return []
    out: list[PackEntry] = []
    for item in entries:
        entry = pack_entry_from_raw(item)
        if entry is not None:
            out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return out


def parse_pack_version(raw: Any) -> int:
    """宽松读顶层 `pack_version`（v0.13.0 J-3 官方包标签下发尺）：非正整数一律 0。

    0 的含义是"这包不带版本尺"——旧包、主人自打的包、`export_pack` 的产物都是 0，
    刷标签的尺据此**什么都不做**，不会把主人的库当成官方库来覆写。
    """
    if not isinstance(raw, Mapping):
        return 0
    value = raw.get(PACK_VERSION_KEY)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return 0
    return value


def parse_manifest_groups(raw: Any) -> dict[str, str]:
    """宽松读顶层 groups（轮 F，manifest v3）：支持 [{name,desc}] 或 {name: desc} 两种形状。

    与条目同纪律：只救不拒——坏名字/坏说明跳过；说明硬截到 GROUP_DESC_MAX_CHARS。
    旧包（v2/v1）无此键 → 空字典，不炸。
    """
    if not isinstance(raw, Mapping):
        return {}
    listed = raw.get("groups")
    out: dict[str, str] = {}
    if isinstance(listed, Mapping):
        pairs = list(listed.items())
    elif isinstance(listed, Iterable) and not isinstance(listed, (str, bytes)):
        pairs = [(item.get("name"), item.get("desc")) for item in listed if isinstance(item, Mapping)]
    else:
        return {}
    for name, desc in pairs:
        group = normalize_group(name)
        if not group or group in out:
            continue
        out[group] = normalize_optional_text(desc, limit=GROUP_DESC_MAX_CHARS)
    return out


def sticker_to_manifest_entry(sticker: Sticker) -> dict[str, Any]:
    """导出面的一条：只带迁移有意义的字段。

    刻意不带 id/added_at/use_count——id 每库自分配，台账是"这个收藏间自己的经历"，
    换间房不该继承（否则导入方的一批假时间会污染"最近爱用"排序）。
    """
    return {
        "file": sticker.file,
        "desc": sticker.desc,
        "tags": list(sticker.tags),
        "group": sticker.group,
        "sha256": sticker.sha256,
        "caption": sticker.caption,
        "visible_text": sticker.visible_text,
    }


def build_manifest(stickers: Iterable[Sticker], groups: Mapping[str, str] | None = None) -> dict[str, Any]:
    """导出包的 manifest.json 形状（轮 F 起随包携带分组说明）。"""
    payload: dict[str, Any] = {
        "version": PACK_MANIFEST_VERSION,
        "app": "sticker_manager",
        "stickers": [sticker_to_manifest_entry(s) for s in stickers],
    }
    if groups:
        payload["groups"] = [{"name": name, "desc": desc} for name, desc in sorted(groups.items()) if desc]
    return payload
