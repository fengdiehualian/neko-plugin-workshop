from __future__ import annotations

import asyncio
import time
import unittest

import _loader
from nmb.models import BriefItem, SourceConfig, SourceReport
from nmb.pipeline import collect, rank_items, select_spoken
from nmb.sources.base import BaseSource
from nmb.sources.http import FetchError
from nmb.store import Store

_loader.setup()

class FakeSource(BaseSource):
    type = "fake"

    def __init__(self, behavior):
        super().__init__(None)  # type: ignore[arg-type]
        self.behavior = behavior

    async def fetch(self, cfg):
        if callable(self.behavior):
            return await self.behavior(cfg)
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return list(self.behavior)

def src(source_id: str = "s1", **kwargs) -> SourceConfig:
    base = dict(id=source_id, type="fake", label=source_id.upper())
    base.update(kwargs)
    return SourceConfig(**base)

def make_item(title: str, source_id: str = "s1", ts: float | None = None, weight: int = 3):
    return BriefItem(source_id=source_id, title=title, ts=ts, weight=weight)

class CollectTests(unittest.TestCase):
    def setUp(self):
        self.db = _loader.db_path("pipeline")
        self.store = Store(self.db)
        self.addCleanup(_loader.cleanup_db, self.db)
        self.addCleanup(self.store.close)

    def run_collect(self, cfgs, sources, **kwargs):
        return asyncio.run(
            collect(cfgs, sources, self.store, default_timeout=2.0, **kwargs)
        )

    def test_ok(self):
        reports = self.run_collect([src()], {"fake": FakeSource([make_item("a")])})
        self.assertEqual(reports[0].status, "ok")
        self.assertEqual(reports[0].items[0].title, "a")

    def test_dedupe_across_runs(self):
        sources = {"fake": FakeSource([make_item("a")])}
        self.run_collect([src()], sources)
        reports = self.run_collect([src()], sources)
        self.assertEqual(reports[0].status, "empty")
        self.assertEqual(reports[0].items, [])

    def test_snapshot_source_repeats(self):
        sources = {"fake": FakeSource([make_item("15~21℃ 小雨")])}
        first = self.run_collect([src(dedupe=False)], sources)
        second = self.run_collect([src(dedupe=False)], sources)
        self.assertEqual(first[0].status, "ok")
        self.assertEqual(second[0].status, "ok")
        self.assertEqual(len(second[0].items), 1)

    def test_snapshot_item_bypasses_dedupe(self):
        snapshot = make_item("BTC 512300")
        snapshot.snapshot = True
        feed_item = make_item("news")
        sources = {"fake": FakeSource([snapshot, feed_item])}
        self.run_collect([src()], sources)
        second = self.run_collect([src()], sources)
        self.assertEqual(second[0].status, "ok")
        self.assertEqual([item.title for item in second[0].items], ["BTC 512300"])

    def test_http_error_degrades_to_error(self):
        reports = self.run_collect(
            [src()], {"fake": FakeSource(FetchError("HTTP_500"))}
        )
        self.assertEqual(reports[0].status, "error")
        self.assertEqual(reports[0].error_code, "HTTP_500")

    def test_http_error_uses_cache(self):
        self.store.save_state("s1", status="ok", items=[make_item("cached")])
        reports = self.run_collect(
            [src()], {"fake": FakeSource(FetchError("TIMEOUT"))}
        )
        self.assertEqual(reports[0].status, "cached")
        self.assertEqual(reports[0].items[0].title, "cached")

    def test_timeout(self):
        async def slow(cfg):
            await asyncio.sleep(5)
            return []

        reports = self.run_collect([src(timeout=1)], {"fake": FakeSource(slow)})
        self.assertEqual(reports[0].status, "error")
        self.assertEqual(reports[0].error_code, "TIMEOUT")

    def test_unknown_type(self):
        reports = self.run_collect([src(type="mystery")], {})
        self.assertEqual(reports[0].status, "error")
        self.assertEqual(reports[0].error_code, "CONFIG_INVALID")

    def test_max_items(self):
        items = [make_item(f"i{n}") for n in range(5)]
        reports = self.run_collect([src(max_items=2)], {"fake": FakeSource(items)})
        self.assertEqual(len(reports[0].items), 2)

    def test_disabled_skipped(self):
        reports = self.run_collect(
            [src(enabled=False)], {"fake": FakeSource([make_item("a")])}
        )
        self.assertEqual(reports, [])

    def test_one_bad_source_does_not_break_others(self):
        cfgs = [src("bad"), src("good")]
        # Swap behaviour per cfg so "good" returns items.
        async def fetch(cfg):
            if cfg.id == "bad":
                raise FetchError("HTTP_500")
            return [make_item("ok", cfg.id)]

        reports = self.run_collect(cfgs, {"fake": FakeSource(fetch)})
        self.assertEqual([r.status for r in reports], ["error", "ok"])

class RankTests(unittest.TestCase):
    def test_weight_and_recency(self):
        now = time.time()
        fresh = make_item("fresh", ts=now - 60, weight=3)
        old = make_item("old", ts=now - 10 * 86400, weight=3)
        heavy = make_item("heavy", ts=now - 60, weight=8)
        ranked = rank_items([old, fresh, heavy], now=now)
        self.assertEqual([i.title for i in ranked], ["heavy", "fresh", "old"])

    def test_unknown_ts_not_last(self):
        now = time.time()
        unknown = make_item("unknown", ts=None, weight=5)
        old = make_item("old", ts=now - 10 * 86400, weight=3)
        ranked = rank_items([old, unknown], now=now)
        self.assertEqual(ranked[0].title, "unknown")

    def test_select_spoken_caps_per_source_and_total(self):
        now = time.time()
        reports = [
            SourceReport(
                "a",
                "A",
                "ok",
                [make_item(f"a{n}", "a", ts=now - n * 60, weight=5) for n in range(4)],
            ),
            SourceReport(
                "b",
                "B",
                "ok",
                [make_item(f"b{n}", "b", ts=now - n * 60, weight=4) for n in range(4)],
            ),
        ]
        picked = select_spoken(reports, max_items=4, per_source=2, now=now)
        self.assertEqual(len(picked), 4)
        self.assertEqual(sum(1 for i in picked if i.source_id == "a"), 2)

    def test_select_spoken_dedupes_url(self):
        now = time.time()
        a = make_item("x", "a", ts=now)
        a.url = "https://same.test/1"
        b = make_item("y", "b", ts=now)
        b.url = "https://same.test/1"
        reports = [SourceReport("a", "A", "ok", [a]), SourceReport("b", "B", "ok", [b])]
        picked = select_spoken(reports, max_items=5, now=now)
        self.assertEqual(len(picked), 1)

if __name__ == "__main__":
    unittest.main()
