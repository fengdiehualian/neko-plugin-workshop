"""KARDS 卡组代码离线解码。

格式知识参考 PJ1059/KARDS-Deck-Tracker（GPL-3.0, Copyright (C) 2026 PJ1059）的
公开格式说明与解析行为；本实现为独立重写，随宿主插件按 AGPL-3.0 分发
（AGPLv3 §13 允许与 GPLv3 材料合并）。

代码形如 ``%%45|<一组一费复制的卡ID>;<二张组>;<三张组>;<四张组>``：
  · 第 1 位 = 主国（1-5，大国），第 2 位 = 盟友国（1-9,a）
  · 卡 ID 为两字符 import id（兼容个别遗留一字符尾部），直接拼接
  · 分号分出的第 N 组里每张卡 = N 张复制
"""

import re
from dataclasses import dataclass

NATION_BY_CODE = {
    "1": "Germany",
    "2": "Britain",
    "3": "Japan",
    "4": "Soviet",
    "5": "USA",
    "6": "France",
    "7": "Italy",
    "8": "Poland",
    "9": "Finland",
    "a": "Anzac",
}

MAJOR_NATIONS = {"Germany", "Britain", "Japan", "Soviet", "USA"}

_CODE_RE = re.compile(r"(?:%%)?(?P<main>[1-5])(?P<ally>[1-9aA])\|(?P<cards>[0-9a-zA-Z;]*)", re.ASCII)


class DeckCodeError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedDeck:
    normalized_code: str
    main_nation: str
    ally_nation: str
    import_ids: tuple[str, ...]  # 含重复，按复制数展开（共 40 张）

    @property
    def nations(self) -> frozenset[str]:
        return frozenset({self.main_nation, self.ally_nation})

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for card_id in self.import_ids:
            result[card_id] = result.get(card_id, 0) + 1
        return result


def parse_deck_code(input_code: str) -> ParsedDeck:
    text = (input_code or "").strip()
    match = _CODE_RE.search(text)
    if not match:
        raise DeckCodeError("没有识别到有效的 KARDS 卡组代码（应形如 %%45|xxxx;yy;…）。")
    main_code = match.group("main")
    ally_code = match.group("ally").lower()
    main_nation = NATION_BY_CODE.get(main_code)
    ally_nation = NATION_BY_CODE.get(ally_code)
    if main_nation not in MAJOR_NATIONS or ally_nation is None:
        raise DeckCodeError("卡组代码中的国家编号无法识别。")
    if ally_nation == main_nation:
        raise DeckCodeError("主国与盟友国不能相同。")

    cards_raw = match.group("cards")
    import_ids: list[str] = []
    for group_index, group in enumerate(cards_raw.split(";"), start=1):
        if not group:
            continue
        # 两字符 import id；末尾可能剩一个字符（官方解析器同样接受遗留 ID）
        tokens = re.findall(r"[0-9a-zA-Z]{1,2}", group)
        for token in tokens:
            import_ids.extend([token] * group_index)

    deck = ParsedDeck(
        normalized_code=f"%%{main_code}{ally_code}|{cards_raw}",
        main_nation=main_nation,
        ally_nation=ally_nation,
        import_ids=tuple(import_ids),
    )
    _validate_counts(deck)
    return deck


def _validate_counts(deck: ParsedDeck) -> None:
    counts = deck.counts()
    for card_id, copies in counts.items():
        if copies > 4:
            raise DeckCodeError(f"卡牌 {card_id!r} 出现 {copies} 张，超过 4 张上限，代码可能损坏。")
    if len(deck.import_ids) > 60:
        raise DeckCodeError("卡牌总数异常（>60），代码可能损坏。")


def encode_deck_code(main_code: str, ally_code: str, counts: dict[str, int]) -> str:
    """反向编码（测试与面板导出用）。counts: import_id -> 1..4 张。"""
    if main_code not in "12345" or ally_code not in NATION_BY_CODE:
        raise DeckCodeError("国家编号无效。")
    groups: dict[int, list[str]] = {1: [], 2: [], 3: [], 4: []}
    for card_id, copies in sorted(counts.items()):
        if not 1 <= copies <= 4:
            raise DeckCodeError(f"{card_id} 复制数 {copies} 无效")
        groups[copies].append(card_id)
    body = ";".join("".join(groups[n]) for n in (1, 2, 3, 4))
    return f"%%{main_code}{ally_code.lower()}|{body}"
