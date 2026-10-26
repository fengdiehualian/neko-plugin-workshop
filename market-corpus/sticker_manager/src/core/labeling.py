"""官方包标签刷新的判定尺（v0.13.0 J-3，纯函数、零 IO）。

为什么单独一层：刷标签这件事的**判断**（谁的话算数、哪些字段算变了、哪些条目不动）
和它的**IO**（读 zip、写 catalog）得分开——判断要能被逐条钉死测试，IO 只需一条端到端。
`services/library.py` 已经 1200 行，把判断塞进去只会让它更难读，也把这轮的门埋进大文件里。

三条尺（都指向同一件事：包不消主人和她的东西）：
1. 只按**内容指纹**配对（`sha256`），文件名不算——文件在库里早就是 `<id>.<ext>`；
2. 主人手改过的**字段**让位（`owner_edited` 记的是字段名列表）——措辞（desc/caption）
   绑成一整块让位，分类与标签照刷；整条跳过会让这张永远拿不到新分类；
3. 值真变了才进补丁：同值不算刷新（否则每次启动都写一遍盘、日志全是假动作）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .catalog import Sticker
from .pack import PackEntry

# 刷标签能动的文本字段。刻意不含 file/disabled/zone/id/台账——包只供给"这张图是什么"，
# 启停、搬家、使用记录都是主人和她在这间收藏间里长出来的经历。
REFRESHABLE_FIELDS: tuple[str, ...] = ("desc", "caption", "visible_text", "group", "tags")

# 措辞是一个整体（v0.13.0 定尺）：目录行正文走 caption > desc 的回落尺，
# 主人碰过 desc 却照样刷上包的 caption，她的话就只是"没被覆盖但永远不露面"——
# 那还是消音。所以碰过两者之一，两者都归主人；分类和标签照刷（不然这张永远没分类）。
WORD_FIELDS: frozenset[str] = frozenset({"desc", "caption"})


def protected_fields(sticker: Sticker) -> set[str]:
    """这张图上包不许动的字段。"""
    touched = set(sticker.owner_edited)
    if touched & WORD_FIELDS:
        touched |= WORD_FIELDS
    return touched


@dataclass
class LabelRefresh:
    """一次刷新的计划：补丁 + 要补的分类说明 + 计数。"""

    patches: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    group_descs: dict[str, str] = field(default_factory=dict)
    new_groups: list[str] = field(default_factory=list)
    skipped_edited: int = 0
    unmatched: int = 0

    @property
    def refreshed(self) -> int:
        return len(self.patches)


def index_by_digest(entries: Iterable[PackEntry]) -> dict[str, PackEntry]:
    """按内容指纹给包里的条目建索引；无指纹的条目进不来（配对靠它，不能靠名字）。

    同指纹重复出现时**第一条赢**——官方包由工坊打出来不该有重复，但宽松层不拒。
    """
    out: dict[str, PackEntry] = {}
    rows = list(entries)
    for entry in rows:
        if entry.sha256 and entry.sha256 not in out:
            out[entry.sha256] = entry
    aliases: dict[str, PackEntry] = {}
    ambiguous: set[str] = set()
    for entry in rows:
        if not entry.sha256:
            continue
        for digest in entry.legacy_sha256:
            if digest in aliases and aliases[digest].sha256 != entry.sha256:
                ambiguous.add(digest)
            else:
                aliases[digest] = entry
    for digest, entry in aliases.items():
        if digest not in ambiguous:
            out.setdefault(digest, entry)
    return out


def plan_label_refresh(
    stickers: Iterable[Sticker],
    entries: Iterable[PackEntry],
    known_group_descs: Mapping[str, str] | None = None,
    pack_group_descs: Mapping[str, str] | None = None,
) -> LabelRefresh:
    """算出"把这份包的标签刷到这批条目上"要动什么。

    `stickers` 由调用方**筛过**（只给官方区的条目——区的真身是 builtin 位，陷阱 24）；
    `known_group_descs` 是库里已有的分类说明，只补缺不覆盖（与 `import_pack` 同一条尺）。
    """
    known = known_group_descs or {}
    pack_groups = pack_group_descs or {}
    index = index_by_digest(entries)
    plan = LabelRefresh()
    for sticker in stickers:
        entry = index.get(sticker.sha256)
        if entry is None:
            plan.unmatched += 1
            continue
        keep = protected_fields(sticker)
        if keep:
            plan.skipped_edited += 1
        patch: dict[str, Any] = {}
        for name in REFRESHABLE_FIELDS:
            if name in keep:
                continue
            want: Any = getattr(entry, name)
            if name == "tags":
                want = list(want)
            if getattr(sticker, name) != want:
                patch[name] = want
        if patch:
            plan.patches.append((sticker.id, patch))
        if entry.group and entry.group not in known and entry.group not in plan.new_groups:
            plan.new_groups.append(entry.group)
    for name, desc in pack_groups.items():
        if desc and not known.get(name):
            plan.group_descs[name] = desc
    return plan
