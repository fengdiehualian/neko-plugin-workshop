"""Coordinator tests: caching, coalescing, throttling, and failure backoff."""

from __future__ import annotations

import asyncio

import conftest

resilience = conftest.load("_resilience")


def make(**overrides):
    options = {
        "ttl_seconds": 60.0,
        "stale_seconds": 120.0,
        "max_entries": 8,
        "min_interval_seconds": 0.0,
        "cooldown_seconds": 0.5,
        "max_cooldown_seconds": 1.0,
        "queue_wait_seconds": 0.2,
    }
    options.update(overrides)
    return resilience.SearchCoordinator(**options).bind("under-test")


RESULTS = [{"title": "猫娘计划", "url": "https://project-neko.cn/", "snippet": "x",
            "content": "", "published": ""}]


def run(coro):
    return asyncio.run(coro)


def test_repeated_query_is_served_from_cache() -> None:
    coordinator = make()
    calls = []

    async def scenario() -> None:
        async def fetch():
            calls.append(1)
            return list(RESULTS)

        first = await coordinator.execute("猫娘 计划", 5, fetch)
        second = await coordinator.execute("  猫娘   计划 ", 5, fetch)
        assert first["cached"] is False
        assert second["cached"] is True
        assert len(calls) == 1, "whitespace/case must normalise to one cache key"

    run(scenario())


def test_concurrent_identical_queries_collapse_to_one_fetch() -> None:
    coordinator = make()
    calls = []

    async def scenario() -> None:
        async def fetch():
            calls.append(1)
            await asyncio.sleep(0.05)
            return list(RESULTS)

        outcomes = await asyncio.gather(*(coordinator.execute("q", 5, fetch) for _ in range(20)))
        assert len(calls) == 1
        assert all(outcome["results"] for outcome in outcomes)

    run(scenario())


def test_returned_results_are_defensive_copies() -> None:
    coordinator = make()

    async def scenario() -> None:
        async def fetch():
            return [dict(item) for item in RESULTS]

        first = await coordinator.execute("q", 5, fetch)
        first["results"][0]["title"] = "MUTATED"
        second = await coordinator.execute("q", 5, fetch)
        assert second["results"][0]["title"] == "猫娘计划"

    run(scenario())


def test_empty_results_are_not_cached() -> None:
    coordinator = make()
    calls = []

    async def scenario() -> None:
        async def fetch():
            calls.append(1)
            return []

        await coordinator.execute("q", 5, fetch)
        await coordinator.execute("q", 5, fetch)
        assert len(calls) == 2

    run(scenario())


def test_block_triggers_cooldown_and_refuses_the_next_call() -> None:
    coordinator = make(cooldown_seconds=5.0, max_cooldown_seconds=10.0)

    async def scenario() -> None:
        async def blocked():
            raise resilience.BlockedError("429", retry_after_seconds=5.0)

        try:
            await coordinator.execute("q", 5, blocked)
        except resilience.BlockedError:
            pass
        else:  # pragma: no cover
            raise AssertionError("first call must surface the block")

        async def never():  # pragma: no cover - must not run
            raise AssertionError("cooldown must prevent a second upstream call")

        try:
            await coordinator.execute("q2", 5, never)
        except resilience.CooldownError:
            return
        raise AssertionError("expected CooldownError")

    run(scenario())


def test_stale_cache_is_served_when_the_backend_starts_failing() -> None:
    coordinator = make(ttl_seconds=0.0, stale_seconds=60.0, cooldown_seconds=5.0)

    async def scenario() -> None:
        async def good():
            return list(RESULTS)

        await coordinator.execute("q", 5, good)

        async def bad():
            raise resilience.SearchProviderError("boom")

        served = await coordinator.execute("q", 5, bad)
        assert served["cached"] is True
        assert served["results"]

    run(scenario())


def test_a_cold_failure_is_not_masked_as_success() -> None:
    coordinator = make(cooldown_seconds=0.01)

    async def scenario() -> None:
        async def bad():
            raise resilience.SearchProviderError("boom")

        try:
            await coordinator.execute("q", 5, bad)
        except resilience.SearchProviderError:
            return
        raise AssertionError("cold failure must surface")

    run(scenario())


def test_provider_errors_are_flagged_for_fallback_but_blocks_are_not() -> None:
    assert resilience.SearchProviderError("x").is_search_provider_error
    assert resilience.BlockedError("x").is_search_block


def test_exa_key_and_quota_errors_are_provider_errors() -> None:
    rejected = resilience.ApiKeyRejectedError("Invalid API key")
    exhausted = resilience.QuotaExhaustedError("402", retry_after_seconds=12.5)
    assert isinstance(rejected, resilience.SearchProviderError)
    assert isinstance(exhausted, resilience.SearchProviderError)
    assert exhausted.retry_after_seconds == 12.5


def test_result_limit_is_enforced() -> None:
    coordinator = make()
    many = [{**RESULTS[0], "url": f"https://e{i}.test/"} for i in range(10)]

    async def scenario() -> None:
        async def fetch():
            return list(many)

        outcome = await coordinator.execute("q", 3, fetch)
        assert len(outcome["results"]) == 3

    run(scenario())
