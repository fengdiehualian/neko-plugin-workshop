"""Isolated resident audit with aggregate CPU/wall timings, no saved frames.

Wrappers preserve arguments/results. Nested timings overlap; not a flame graph.
"""
import asyncio
import functools
import json
import time
from collections import defaultdict
from types import SimpleNamespace

from plugin.plugins.hsr_companion.runtime import live_refresh, query_reuse
from plugin.plugins.hsr_companion.scripts.audit_dynamic_handoff import audit


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resident-seconds", type=float, default=120)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--query", default="现在在哪，接下来这个任务要做什么？")
    parser.add_argument("--expected-page", default="exploration_hud")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    totals = defaultdict(lambda: [0, 0., 0.])
    originals = []
    def instrument(module, name):
        original = getattr(module, name)
        @functools.wraps(original)
        def measured(*a, **kw):
            started = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                elapsed = time.perf_counter() - started
                item = totals[name]
                item[0] += 1
                item[1] += elapsed
                item[2] = max(item[2], elapsed)
        originals.append((module, name, original))
        setattr(module, name, measured)
    for module in (query_reuse, live_refresh):
        for name in ("region_stamp", "regions_changed"):
            instrument(module, name)
    instrument(live_refresh, "stable_screen_content")
    try:
        asyncio.run(audit(SimpleNamespace(assistant_url="http://127.0.0.1:8220", samples=args.samples,
            resident_seconds=args.resident_seconds, sample_interval=5, resident_commits=1, setup_seconds=150,
            expected_page=args.expected_page, interactive=args.interactive, compact=True,
            query=args.query, no_independent_reread=True)))
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)
        print(json.dumps({"kind": "blocking_profile_nested_wall_times",
            "functions": {k: {"calls": v[0], "total_seconds": round(v[1], 4), "max_seconds": round(v[2], 4)}
                          for k, v in totals.items()}}), flush=True)


if __name__ == "__main__":
    main()
