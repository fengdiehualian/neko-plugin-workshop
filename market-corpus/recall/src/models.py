"""Recall domain logic.

The first release intentionally contains only a tiny, auditable vocabulary
catalog.  Knowledge-source adapters and card handoff contracts live at this
boundary so a later N.E.K.O community/local source does not leak into the SRS.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class VocabularyItem:
    id: str
    word: str
    phonetic: str
    meaning: str
    example: str
    example_zh: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class KnowledgeSourceInfo:
    id: str
    name: str
    kind: str
    language: str
    description: str
    available: bool
    item_count: int
    notice: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VocabularySource(Protocol):
    """Stable adapter seam for official, community, local and external data."""

    @property
    def info(self) -> KnowledgeSourceInfo: ...

    def items(self) -> list[VocabularyItem]: ...


class BuiltinVocabularySource:
    def __init__(self, info: KnowledgeSourceInfo, items: list[VocabularyItem]):
        self._info = info
        self._items = list(items)

    @property
    def info(self) -> KnowledgeSourceInfo:
        return self._info

    def items(self) -> list[VocabularyItem]:
        return list(self._items)


DEMO_ITEMS = [
    VocabularyItem("cet4.abandon", "abandon", "/əˈbændən/", "v. 放弃；抛弃", "They had to abandon the plan.", "他们不得不放弃这个计划。"),
    VocabularyItem("cet4.ability", "ability", "/əˈbɪləti/", "n. 能力；才能", "Practice improves your ability to recall words.", "练习会提高你回忆单词的能力。"),
    VocabularyItem("cet4.absorb", "absorb", "/əbˈzɔːb/", "v. 吸收；理解", "Plants absorb light from the sun.", "植物吸收太阳光。"),
    VocabularyItem("cet4.accurate", "accurate", "/ˈækjərət/", "adj. 准确的；精确的", "The report gives an accurate description.", "这份报告给出了准确的描述。"),
    VocabularyItem("cet4.adapt", "adapt", "/əˈdæpt/", "v. 适应；改编", "It takes time to adapt to a new routine.", "适应新的日常安排需要时间。"),
    VocabularyItem("cet4.approach", "approach", "/əˈprəʊtʃ/", "n./v. 方法；接近", "We need a different approach to the problem.", "我们需要用不同的方法处理这个问题。"),
    VocabularyItem("cet4.available", "available", "/əˈveɪləbl/", "adj. 可获得的；有空的", "The book is available online.", "这本书可以在线获取。"),
    VocabularyItem("cet4.aware", "aware", "/əˈweə(r)/", "adj. 意识到的；知晓的", "She was aware of the risk.", "她意识到了风险。"),
    VocabularyItem("cet4.benefit", "benefit", "/ˈbenɪfɪt/", "n./v. 益处；受益", "Regular review benefits long-term memory.", "定期复习有益于长期记忆。"),
    VocabularyItem("cet4.challenge", "challenge", "/ˈtʃælɪndʒ/", "n./v. 挑战", "Learning a language is a rewarding challenge.", "学习语言是一项值得的挑战。"),
    VocabularyItem("cet4.concentrate", "concentrate", "/ˈkɒnsntreɪt/", "v. 集中；专心", "Try to concentrate on one word at a time.", "试着一次专注于一个单词。"),
    VocabularyItem("cet4.consequence", "consequence", "/ˈkɒnsɪkwəns/", "n. 结果；后果", "Every choice has a consequence.", "每个选择都有结果。"),
]


def build_source_registry() -> dict[str, VocabularySource]:
    source_id = "official.cet4.core.v1"
    data_path = Path(__file__).with_name("static") / "data" / "cet4-core.v1.json"
    items: list[VocabularyItem] = []
    seen: set[str] = set()
    enrichment = {item.id: item for item in DEMO_ITEMS}
    attribution = "Recall 精选体验词"
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8-sig"))
        for raw in payload.get("items") or []:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("id") or "").strip()
            word = str(raw.get("word") or "").strip()
            meaning = str(raw.get("meaning") or "").strip()
            if not item_id or item_id in seen or not word or not meaning:
                continue
            curated = enrichment.get(item_id)
            items.append(
                VocabularyItem(
                    id=item_id,
                    word=word,
                    phonetic=str(raw.get("phonetic") or "").strip(),
                    meaning=meaning,
                    example=(curated.example if curated else str(raw.get("example") or "").strip()),
                    example_zh=(curated.example_zh if curated else str(raw.get("example_zh") or "").strip()),
                )
            )
            seen.add(item_id)
        attribution = f"ECDICT · {payload.get('upstream_commit', '')[:8]}"
    except (OSError, ValueError, TypeError):
        # A missing optional data bundle must not make the Recall plugin fail to
        # start; the curated cards remain a complete fallback source.
        items = list(DEMO_ITEMS)
    source = BuiltinVocabularySource(
        KnowledgeSourceInfo(
            id=source_id,
            name="CET-4 核心词汇",
            kind="official",
            language="en-CN",
            description="按当代语料词频排列的离线 CET-4 词库。",
            available=True,
            item_count=len(items),
            notice=f"离线可用 · {len(items)} 词 · {attribution}",
        ),
        items,
    )
    # The old id is retained as a read-only alias so existing learning records
    # and already-open UI tabs continue without migration loss.
    return {source_id: source, "official.cet4.demo": source}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def initial_state() -> dict[str, Any]:
    return {
        "version": 1,
        "selected_source": "official.cet4.demo",
        "familiarity": "unset",
        "items": {},
        "history": [],
    }


def dashboard(state: dict[str, Any], source: VocabularySource, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    item_states = state.get("items") if isinstance(state.get("items"), dict) else {}
    due = 0
    mastered = 0
    for record in item_states.values():
        if not isinstance(record, dict):
            continue
        due_at = parse_time(record.get("due_at"))
        if due_at is not None and due_at <= now:
            due += 1
        if float(record.get("interval_days") or 0) >= 21:
            mastered += 1
    learned = len([value for value in item_states.values() if isinstance(value, dict)])
    return {
        "source": source.info.to_dict(),
        "due_count": due,
        "learned_count": learned,
        "mastered_count": mastered,
        "total_count": source.info.item_count,
        "new_count": max(0, source.info.item_count - learned),
        "familiarity": str(state.get("familiarity") or "unset"),
        "memory_boundary": "学习进度只保存在 Recall，不会写入或接管 N.E.K.O 长期记忆。",
    }


def build_session(
    state: dict[str, Any],
    source: VocabularySource,
    *,
    now: datetime | None = None,
    new_limit: int = 5,
    review_limit: int = 12,
) -> list[dict[str, Any]]:
    now = now or utc_now()
    item_states = state.get("items") if isinstance(state.get("items"), dict) else {}
    all_items = source.items()
    due_items: list[tuple[datetime, VocabularyItem]] = []
    new_items: list[VocabularyItem] = []
    for item in all_items:
        record = item_states.get(item.id)
        if not isinstance(record, dict):
            new_items.append(item)
            continue
        due_at = parse_time(record.get("due_at"))
        if due_at is not None and due_at <= now:
            due_items.append((due_at, item))
    due_items.sort(key=lambda row: row[0])
    selected = [item for _, item in due_items[:review_limit]]
    remaining = max(0, review_limit - len(selected))
    if remaining:
        selected.extend(new_items[: min(new_limit, remaining)])
    return [
        {
            **item.to_dict(),
            "is_new": item.id not in item_states,
            "progress": item_states.get(item.id, {}),
        }
        for item in selected
    ]


def schedule_review(
    state: dict[str, Any],
    *,
    item_id: str,
    grade: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Small, replaceable scheduler for the MVP (forgot / hard / good)."""
    if grade not in {"forgot", "hard", "good"}:
        raise ValueError("grade must be forgot, hard or good")
    now = now or utc_now()
    items = state.setdefault("items", {})
    previous = items.get(item_id) if isinstance(items.get(item_id), dict) else {}
    repetitions = int(previous.get("repetitions") or 0)
    lapses = int(previous.get("lapses") or 0)
    old_interval = float(previous.get("interval_days") or 0)

    if grade == "forgot":
        lapses += 1
        interval_days = 10 / (24 * 60)
        status = "relearning"
    elif grade == "hard":
        repetitions += 1
        interval_days = 0.5 if old_interval < 0.5 else max(1.0, old_interval * 1.35)
        status = "learning" if repetitions < 2 else "review"
    else:
        repetitions += 1
        interval_days = 1.0 if old_interval < 1 else max(2.0, old_interval * 2.5)
        status = "learning" if repetitions < 2 else "review"

    record = {
        "status": status,
        "repetitions": repetitions,
        "lapses": lapses,
        "interval_days": round(interval_days, 4),
        "last_grade": grade,
        "last_reviewed_at": now.isoformat(),
        "due_at": (now + timedelta(days=interval_days)).isoformat(),
    }
    items[item_id] = record
    history = state.setdefault("history", [])
    history.append({"item_id": item_id, "grade": grade, "at": now.isoformat()})
    del history[:-200]
    return record
