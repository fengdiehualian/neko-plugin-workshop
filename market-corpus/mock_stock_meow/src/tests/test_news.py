"""新闻系统单测。"""

from __future__ import annotations

import pytest
from engine.config import NewsConfig
from engine.news import generate_news, is_reversal_day, plan_reversal_days
from engine.rng import derive_rng

CONFIG = NewsConfig()
SYMBOLS = [f"FAKE-{index:03d}" for index in range(1, 6)]


def test_plan_reversal_days_count_in_range():
    for seed in range(40):
        days = plan_reversal_days(seed, days=30, min_days=1, max_days=3)
        assert 1 <= len(days) <= 3
        assert all(1 <= day <= 30 for day in days)


def test_plan_reversal_days_is_deterministic():
    first = plan_reversal_days(7, days=30)
    second = plan_reversal_days(7, days=30)
    assert first == second


def test_plan_reversal_days_handles_short_ranges():
    assert plan_reversal_days(1, days=0) == set()
    days = plan_reversal_days(1, days=2)
    assert 1 <= len(days) <= 2


def test_generate_news_is_deterministic():
    first = generate_news(7, 3, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG)
    second = generate_news(7, 3, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG)
    assert first == second


def test_generate_news_differs_between_seeds():
    first = generate_news(7, 3, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG)
    second = generate_news(8, 3, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG)
    assert first != second


def test_all_five_news_types_can_appear():
    seen = set()
    for seed in range(60):
        for phase in ("honeymoon", "trap", "slaughter"):
            items = generate_news(
                seed, 1, phase=phase, symbols=SYMBOLS, reversal_days=set(), config=CONFIG
            )
            seen.update(item.type for item in items)
    assert {"true", "fake", "vague", "thunder", "noise"} <= seen


def test_trap_phase_is_half_fake_news():
    total = 0
    fake = 0
    for seed in range(120):
        items = generate_news(
            seed, 2, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG
        )
        total += len(items)
        fake += sum(1 for item in items if item.type == "fake")
    ratio = fake / total
    assert 0.40 <= ratio <= 0.60, ratio


def test_honeymoon_true_news_accuracy_is_configured():
    items = generate_news(
        3, 1, phase="honeymoon", symbols=SYMBOLS, reversal_days=set(), config=CONFIG
    )
    true_items = [item for item in items if item.type == "true"]
    assert true_items, "蜜月期应该发得出真新闻"
    for item in true_items:
        assert item.accuracy == pytest.approx(CONFIG.truth_ratio_honeymoon)


def test_planned_reversal_day_is_always_reversal():
    planned = plan_reversal_days(11, days=30)
    for day in planned:
        assert is_reversal_day(11, day, reversal_days=planned, config=CONFIG) is True


def test_reversal_day_flips_news_direction():
    day = 5
    normal = generate_news(
        9, day, phase="honeymoon", symbols=SYMBOLS, reversal_days=set(), config=CONFIG
    )
    reversed_news = generate_news(
        9, day, phase="honeymoon", symbols=SYMBOLS, reversal_days={day}, config=CONFIG
    )
    assert [item.type for item in normal] == [item.type for item in reversed_news]
    assert [item.content for item in normal] != [item.content for item in reversed_news]


def test_news_ids_are_unique_per_day():
    items = generate_news(
        5, 4, phase="trap", symbols=SYMBOLS, reversal_days=set(), config=CONFIG
    )
    ids = [item.id for item in items]
    assert len(set(ids)) == len(ids)
    assert all(item.day == 4 for item in items)


def test_generate_news_accepts_empty_symbols():
    items = generate_news(
        5, 4, phase="trap", symbols=[], reversal_days=set(), config=CONFIG
    )
    assert all(item.stock_id is None for item in items)


def test_derive_rng_is_stable_across_calls():
    assert derive_rng(7, 1, 2).random() == derive_rng(7, 1, 2).random()
    assert derive_rng(7, 1, 2).random() != derive_rng(7, 1, 3).random()
