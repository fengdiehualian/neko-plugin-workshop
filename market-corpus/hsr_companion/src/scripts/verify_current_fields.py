"""Compare old pixel-only and new text verification on IDENTICAL in-memory inputs.

Read-only local helper access. No game control, screenshots on disk, installation,
model download, player storage writes or messages to N.E.K.O. This is not chat QA.
"""
import json
import time
from pathlib import Path

from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.adapters.text_verification import verify_text_regions
from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.runtime.live_refresh import stable_screen_content


def main():
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    adapter = LocalVisionAdapter(ExternalKnowledgeCatalog(registry=registry))
    client = VisionAssistantClient()
    try:
        original = client.fetch_frame(0)
        if original is None:
            raise RuntimeError("No game frame; this check never starts or repairs the helper")
        started = time.monotonic()
        value = adapter.analyze_navigation(original.image_bytes)
        latest = client.fetch_frame(0)
        if latest is None or latest.game.get("pid") != original.game.get("pid") or latest.capture.get("state") != "capturing":
            raise RuntimeError("Capture changed/unavailable; no comparison authorized")
        before = stable_screen_content(value, original.image_bytes, latest.image_bytes)
        trace = []
        def verify(payload, proofs, **kwargs):
            return verify_text_regions(adapter._get_backend(), payload, proofs, trace=trace, **kwargs)
        after = stable_screen_content(value, original.image_bytes, latest.image_bytes, verifier=verify)
        def fields(v):
            return [{k: f.get(k) for k in ("kind", "value")} for f in v.get("screen_facts", {}).get("facts") or []]
        print(json.dumps({"kind": "same_two_frames_NOT_chat_or_accuracy_rate", "image_stored": False,
            "frame_sequences": [original.frame_seq, latest.frame_seq], "page": value.get("page", {}).get("page_type"),
            "elapsed_seconds": round(time.monotonic() - started, 3), "read_fields": fields(value),
            "pixel_only_fields": fields(before), "candidate_fields": fields(after),
            "candidate_checks": after.get("screen_facts", {}).get("field_checks"),
            "reread_attempted": after.get("recognition", {}).get("text_revalidation_attempted"),
            "reread_confirmed": after.get("recognition", {}).get("text_revalidation_confirmed"),
            "reread_trace": trace}, ensure_ascii=True), flush=True)
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
