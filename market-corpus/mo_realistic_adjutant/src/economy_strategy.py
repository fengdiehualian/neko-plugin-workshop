from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


PERSONALITY_REFRESH_SECONDS = 60.0


@dataclass(frozen=True)
class EconomicPersonality:
    mode: str
    display_name: str
    reserve_floor: int
    target_cash: int
    batch_limit: int
    score: float
    source: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EconomicBudget:
    credits: int
    reserve_floor: int
    pending_cost: int
    spendable: int
    target_cash: int
    excess_cash: int
    income_per_minute: float | None
    spending_per_minute: float | None
    net_per_minute: float | None
    flow_confidence: str
    emergency: bool
    mode: str
    reason: str

    def affordable_quantity(self, unit_cost: int, requested: int) -> int:
        requested = max(0, int(requested))
        unit_cost = max(0, int(unit_cost))
        if requested == 0:
            return 0
        if unit_cost == 0:
            return requested
        return min(requested, self.spendable // unit_cost)

    def can_spend(self, cost: int) -> bool:
        return max(0, int(cost)) <= self.spendable

    def public(self) -> dict[str, Any]:
        return asdict(self)


def balanced_personality(*, source: str = "default") -> EconomicPersonality:
    return EconomicPersonality(
        mode="balanced",
        display_name="平衡运营",
        reserve_floor=2500,
        target_cash=4000,
        batch_limit=5,
        score=0.0,
        source=source,
    )


def infer_economic_personality(character: dict[str, Any] | None) -> EconomicPersonality:
    if not isinstance(character, dict):
        return balanced_personality()

    explicit_keys = ("经济偏好", "资金偏好", "消费习惯", "理财风格")
    explicit = " ".join(
        str(character.get(key) or "").strip() for key in explicit_keys
    ).strip()
    corpus = " ".join(
        str(value)
        for key, value in character.items()
        if key != "_reserved" and isinstance(value, (str, int, float))
    )
    text = f"{explicit} {corpus}".casefold()

    saver_terms = {
        "爱钱": 2.5, "财迷": 3.0, "守财": 3.0, "精打细算": 3.0,
        "节俭": 2.5, "省钱": 2.0, "理财": 1.5, "谨慎消费": 2.5,
        "资金安全": 2.0, "存钱": 2.0, "抠门": 2.0,
    }
    spender_terms = {
        "大手大脚": 3.0, "花钱": 1.5, "不在乎钱": 3.0, "不感冒": 2.5,
        "豪爽": 1.5, "冲动": 1.5, "及时行乐": 2.0, "挥霍": 3.0,
        "有钱就花": 3.0, "火力优先": 1.5, "战斗力优先": 1.5,
    }
    score = sum(weight for term, weight in saver_terms.items() if term in text)
    score -= sum(weight for term, weight in spender_terms.items() if term in text)
    normalized = max(-1.0, min(1.0, score / 4.0))
    source = "character_explicit" if explicit else "character_inferred"

    if normalized >= 0.35:
        return EconomicPersonality(
            mode="frugal", display_name="精打细算", reserve_floor=5000,
            target_cash=6500, batch_limit=3, score=round(normalized, 3), source=source,
        )
    if normalized <= -0.35:
        return EconomicPersonality(
            mode="spendthrift", display_name="火力消费", reserve_floor=500,
            target_cash=1500, batch_limit=10, score=round(normalized, 3), source=source,
        )
    return EconomicPersonality(
        mode="balanced", display_name="平衡运营", reserve_floor=2500,
        target_cash=4000, batch_limit=5, score=round(normalized, 3), source=source,
    )


def load_current_character(
    base_url: str, *, timeout: float = 1.0
) -> tuple[str, dict[str, Any]] | None:
    endpoint = urljoin(str(base_url).rstrip("/") + "/", "api/characters")
    request = Request(endpoint, headers={"Accept": "application/json"})
    with urlopen(request, timeout=max(0.1, float(timeout))) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("当前猫娘") or "").strip()
    catgirls = payload.get("猫娘")
    character = catgirls.get(name) if isinstance(catgirls, dict) else None
    if not name or not isinstance(character, dict):
        return None
    return name, character


def build_economic_budget(
    credits: int | None,
    flow_report: dict[str, Any] | None,
    personality: EconomicPersonality,
    *,
    pending_cost: int = 0,
    reserve_override: int | None = None,
    emergency: bool = False,
) -> EconomicBudget:
    current = max(0, int(credits or 0))
    pending = max(0, int(pending_cost))
    report = flow_report if isinstance(flow_report, dict) else {}
    income, spending, net, confidence = _flow_rates(report)

    floor = personality.reserve_floor
    if reserve_override is not None:
        floor = max(0, int(reserve_override))
    if emergency:
        emergency_floors = {"spendthrift": 0, "balanced": 750, "frugal": 1500}
        floor = min(floor, emergency_floors.get(personality.mode, 750))
    elif net is not None and net < -60:
        multiplier = 0.15 if personality.mode == "spendthrift" else 0.35
        floor += min(2500, int(abs(net) * multiplier))

    spendable = max(0, current - floor - pending)
    target = max(floor, personality.target_cash)
    excess = max(0, current - target - pending)
    reason = (
        f"{personality.display_name}：保留 {floor}，待支付 {pending}，"
        f"当前可支配 {spendable}"
    )
    if emergency:
        reason += "；紧急状态已降低资金底线"
    elif net is not None and net < -60:
        reason += "；资金负增长，增加缓冲"
    elif excess > 0:
        reason += f"；闲置资金 {excess} 应转化为战斗力"

    return EconomicBudget(
        credits=current,
        reserve_floor=floor,
        pending_cost=pending,
        spendable=spendable,
        target_cash=target,
        excess_cash=excess,
        income_per_minute=income,
        spending_per_minute=spending,
        net_per_minute=net,
        flow_confidence=confidence,
        emergency=bool(emergency),
        mode=personality.mode,
        reason=reason,
    )


def _flow_rates(
    report: dict[str, Any]
) -> tuple[float | None, float | None, float | None, str]:
    windows = report.get("windows")
    candidates = (
        [item for item in windows if isinstance(item, dict)]
        if isinstance(windows, list) else []
    )
    useful = [item for item in candidates if _number(item.get("net_per_minute")) is not None]
    selected = max(
        useful,
        key=lambda item: (
            {"high": 3, "medium": 2, "low": 1}.get(str(item.get("confidence")), 0),
            float(item.get("observed_seconds") or 0),
        ),
        default=None,
    )
    if selected is None:
        return (
            _number(report.get("income_per_minute")),
            _number(report.get("spending_per_minute")),
            _number(report.get("net_per_minute")),
            str(report.get("flow_confidence") or "low"),
        )
    return (
        _number(selected.get("income_per_minute")),
        _number(selected.get("spending_per_minute")),
        _number(selected.get("net_per_minute")),
        str(selected.get("confidence") or "low"),
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None
