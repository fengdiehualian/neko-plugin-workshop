"""Read-only REAL OCR replay, separate from mocked contract tests.

Only use user-authorized existing images. No images, raw OCR, account fields,
or chat messages are saved. A replay is NOT a live switching/chat acceptance.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.runtime import CompanionRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--expected", default="", help="Comma-separated canonical printed names")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    catalog = ExternalKnowledgeCatalog(registry=json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8")))
    adapter = LocalVisionAdapter(catalog)
    expected = set(filter(None, (name.strip() for name in args.expected.split(","))))
    failed = False
    try:
        for path in args.images:
            started = time.monotonic()
            scenes = []
            result = adapter.analyze_live(path.read_bytes(), cancelled=lambda: False,
                                          on_scene=lambda p: scenes.append({"page": p["page"]["page_type"],
                                                                           "seconds": round(time.monotonic() - started, 3)}))
            # New runtime for each sample: previous identities cannot help it.
            runtime = CompanionRuntime()
            runtime.ingest(result)
            read = {c["name"] for c in result.get("characters") or []}
            confirmed = {c["name"] for c in result.get("characters") or []
                         if c["entity_id"] in runtime.stable_character_ids}
            missing = expected - read
            unexpected = read - expected if expected else set()
            failed |= not result.get("ok") or bool(missing) or bool(unexpected)
            print(json.dumps({
                "sample": path.name, "kind": "offline_real_ocr_not_live_acceptance",
                "source_size": (result.get("recognition") or {}).get("source_size"),
                "names_read": sorted(read), "names_confirmed_without_second_full_scan": sorted(confirmed),
                "missing": sorted(missing), "unexpected": sorted(unexpected),
                "scene_previews": scenes, "scene": result["scene"]["primary_state"],
                "seconds": round(time.monotonic() - started, 3),
                "text_reader": (result.get("recognition") or {}).get("text_reader"),
            }, ensure_ascii=False), flush=True)
    finally:
        adapter.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
