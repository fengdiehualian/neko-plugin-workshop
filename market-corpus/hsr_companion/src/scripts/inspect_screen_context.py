"""Read-only live check of frame -> facts -> knowledge, with no saved images.

Only run after the player agrees to a capture. This is not a host-chat test.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="这个技能是什么意思")
    parser.add_argument("--details", action="store_true", help="Include redacted field evidence, not images")
    parser.add_argument("--scene-only", action="store_true", help="Use the single-call scene/location path, not character analysis")
    args = parser.parse_args()
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient, VisionAssistantProcess
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.runtime import CompanionRuntime
    from plugin.plugins.hsr_companion.runtime.grounding import build_grounding
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    from plugin.plugins.hsr_companion.runtime.screen_facts import safe_text
    from plugin.plugins.hsr_companion.session import utc_now_iso

    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    adapter = LocalVisionAdapter(catalog)
    runtime = CompanionRuntime(global_rate_seconds=0)
    manager = VisionAssistantProcess(root)
    client = VisionAssistantClient()
    started_helper = False
    last_sequence = 0
    try:
        if not client.health().get("connected"):
            manager.start()
            started_helper = True
        for serial in (1, 2):
            deadline = time.monotonic() + 15
            frame = None
            while time.monotonic() < deadline and frame is None:
                try:
                    frame = client.fetch_frame(last_sequence)
                except RuntimeError:
                    frame = None  # Packaged helper cold-starts asynchronously.
                if frame is None:
                    time.sleep(.2)
            if frame is None:
                raise RuntimeError("独立助手未提供游戏画面：" + str(client.health().get("capture") or {}))
            last_sequence = frame.frame_seq
            from PIL import Image
            with Image.open(BytesIO(frame.image_bytes)) as image:
                size = list(image.size)
            started = time.monotonic()
            diagnostic_boxes = []
            if args.scene_only:
                result = adapter.analyze_scene(frame.image_bytes)
            elif args.details:
                from unittest.mock import patch

                from plugin.plugins.hsr_companion.adapters import local_vision
                original_reader = local_vision.read_native_text

                def inspect_reader(*reader_args, **reader_kwargs):
                    output = original_reader(*reader_args, **reader_kwargs)
                    diagnostic_boxes.extend(output[1])
                    return output

                with patch.object(local_vision, "read_native_text", inspect_reader):
                    result = adapter.analyze(frame.image_bytes)
            else:
                result = adapter.analyze(frame.image_bytes)
            runtime.ingest(result)
            result.update(source="assistant_local_ocr", observed_at=datetime.fromtimestamp(frame.timestamp, timezone.utc).isoformat()
                          if frame.timestamp else utc_now_iso(), observation_serial=serial, runtime=runtime.snapshot())
            evidence = public_visual_evidence(result, {"state": "observing", "enabled": True})
            grounded = build_grounding(args.query, evidence, catalog)
            print(json.dumps({
                "kind": "live_source_check_not_host_chat", "sample": serial,
                "source_size": size, "page": result.get("page"),
                "scene_only": args.scene_only,
                "seconds": round(time.monotonic() - started, 3), "ok": result.get("ok"),
                "numeric_recheck": result.get("recognition", {}).get("text_reader", {}).get("relic_value_recheck"),
                "table_recheck": result.get("recognition", {}).get("text_reader", {}).get("relic_table_recheck"),
                "scene_confirmed": evidence["scene"]["verified"], "characters": evidence["characters"],
                "screen_facts": evidence["screen_facts"] if args.details else [
                    {k: f[k] for k in ("kind", "label", "value", "entity_id", "subject_entity_id") if k in f}
                    for f in evidence["screen_facts"]["facts"]],
                "grounding": {k: grounded[k] for k in ("topics", "target_entity_ids", "needs_target_clarification", "missing_information")},
                "knowledge": [{"id": r["id"], "type": r["entity_type"], "name": r["name"],
                               "source_count": len(r.get("sources") or [])} for r in grounded["knowledge_results"]],
                "image_stored": False,
                **({"ocr_boxes": [{**{k: b.get(k) for k in ("left", "top", "right", "bottom", "score")},
                                    "text": safe_text(b.get("text"))} for b in diagnostic_boxes
                                   if "已隐藏]" not in safe_text(b.get("text"))]} if args.details else {}),
            }, ensure_ascii=True), flush=True)
    finally:
        adapter.close()
        if started_helper:
            manager.stop()


if __name__ == "__main__":
    main()
