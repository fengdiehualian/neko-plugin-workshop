from __future__ import annotations

import time
import unittest

import _loader
from nmb.models import BriefItem
from nmb.store import Store

_loader.setup()

def item(title: str, url: str | None = None) -> BriefItem:
    return BriefItem(source_id="s", title=title, url=url)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.db = _loader.db_path("store")
        self.store = Store(self.db)
        self.addCleanup(_loader.cleanup_db, self.db)
        self.addCleanup(self.store.close)

    def test_seen_window(self):
        items = [item("a"), item("b")]
        self.assertEqual(len(self.store.drop_seen(items)), 2)
        self.store.remember(items)
        self.assertEqual(self.store.drop_seen(items), [])
        # Same content, different object -> still known.
        self.assertEqual(self.store.drop_seen([item("a")]), [])

    def test_seen_expires(self):
        items = [item("old")]
        self.store.remember(items, now=time.time() - 8 * 86400)
        self.assertEqual(len(self.store.drop_seen(items)), 1)

    def test_cache_roundtrip(self):
        items = [item("cached", "https://x.test/1")]
        self.store.save_state("s", status="ok", items=items)
        cached = self.store.cached_items("s")
        assert cached is not None
        self.assertEqual(cached[0].title, "cached")
        self.assertEqual(cached[0].url, "https://x.test/1")

    def test_cache_expiry(self):
        self.store.save_state("s", status="ok", items=[item("x")], fetched_at=time.time() - 30 * 3600)
        self.assertIsNone(self.store.cached_items("s"))

    def test_error_keeps_previous_cache(self):
        self.store.save_state("s", status="ok", items=[item("kept")])
        self.store.save_state("s", status="error", error_code="TIMEOUT")
        cached = self.store.cached_items("s")
        assert cached is not None
        self.assertEqual(cached[0].title, "kept")
        meta = self.store.source_meta("s")
        assert meta is not None
        self.assertEqual(meta["last_error"], "TIMEOUT")

    def test_run_log_and_has_run(self):
        self.assertFalse(self.store.has_run("2026-10-21", "08:00"))
        self.store.log_brief("2026-10-21", "08:00", trigger="schedule", ok_count=2)
        self.assertTrue(self.store.has_run("2026-10-21", "08:00"))
        self.assertFalse(self.store.has_run("2026-10-21", "22:30"))
        last = self.store.last_run()
        assert last is not None
        self.assertEqual(last["trigger"], "schedule")

    def test_kv_ttl(self):
        self.store.kv_set("k", "v")
        self.assertEqual(self.store.kv_get_ttl("k"), "v")
        self.store.kv_set("t", "fresh", ttl_seconds=60)
        self.assertEqual(self.store.kv_get_ttl("t"), "fresh")
        self.store.kv_set("t2", "stale", ttl_seconds=-1)
        self.assertIsNone(self.store.kv_get_ttl("t2"))

    def test_purge(self):
        self.store.remember([item("gone")], now=time.time() - 30 * 86400)
        self.assertGreaterEqual(self.store.purge_seen(), 1)

if __name__ == "__main__":
    unittest.main()
