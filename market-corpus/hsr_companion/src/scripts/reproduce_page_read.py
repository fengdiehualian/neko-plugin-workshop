"""Read-only reproduction: identical in-memory frame or live public tool calls.

No game input, helper launch, installs, downloads or screenshot files. Public
tool mode may register the plugin's normal follow-up; it is not a chat test.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("same-frame", "tool", "source-query"), required=True)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--query", default="现在是什么页面？页面文字写了什么？")
    parser.add_argument("--page-only", action="store_true")
    parser.add_argument("--navigation", action="store_true", help="Read location/quest, not a full content/portrait pass")
    args = parser.parse_args()
    if args.page_only:
        args.query = "现在是什么页面？"
    if not 1 <= args.samples <= 5:
        parser.error("samples must be between 1 and 5")
    if args.mode == "tool":
        for sample in range(1, args.samples + 1):
            body = json.dumps({"name": "hsr_companion_prepare", "arguments": {"query": args.query},
                               "call_id": f"repro-20261012-{time.time_ns()}"}).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:48916/api/llm-tools/callback/hsr_companion/hsr_companion_prepare",
                data=body, headers={"Content-Type": "application/json"})
            started = time.monotonic()
            with urllib.request.urlopen(req, timeout=18) as response:
                envelope = json.load(response)
            result = envelope.get("output", {})
            if isinstance(result, str):
                result = json.loads(result)
            visual = result.get("current_visual_evidence") or {}
            screen = visual.get("screen_facts") or {}
            emit({"kind": "real_public_tool_not_chat", "sample": sample,
                  "elapsed": round(time.monotonic() - started, 3), "is_error": envelope.get("is_error"),
                  "page": visual.get("page_type"), "fresh": visual.get("fresh"),
                  "status": visual.get("status"), "scene": visual.get("scene"),
                  "scope": visual.get("read_scope"), "visible_text_count": len(screen.get("visible_text") or []),
                  "screen_content": result.get("screen_context_answer"),
                  "scene_answer": result.get("screen_scene_answer"), "followup": result.get("followup"),
                  "refresh": result.get("refresh_diagnostics")})
        return

    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime

    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
                                 root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
    adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
    frame = VisionAssistantClient().fetch_frame(0)
    if frame is None:
        raise RuntimeError("No frame; this diagnostic never launches or repairs the helper")
    emit({"kind": "same_frame_in_memory", "frame_seq": frame.frame_seq,
          "sha256": hashlib.sha256(frame.image_bytes).hexdigest(), "image_stored": False})
    try:
        if args.mode == "source-query":
            # Run current source through REAL capture/OCR/guards/commit/query,
            # but isolate storage and pushes. Never replace the installed build
            # or submit anything to the cat from this developer experiment.
            from plugin.plugins.hsr_companion import HsrCompanionPlugin
            from plugin.plugins.hsr_companion.runtime import CompanionRuntime
            from plugin.plugins.hsr_companion.runtime.live_refresh import guarded_analysis, refresh_for_query
            from plugin.sdk.plugin import Ok

            adapter.analyze_scene(frame.image_bytes)  # Explicit warm-up, not a measured query.
            async def run():
                for sample in range(1, args.samples + 1):
                    captured = await asyncio.to_thread(VisionAssistantClient().fetch_frame, 0)
                    if captured is None:
                        raise RuntimeError("Capture unavailable")
                    owner = object.__new__(HsrCompanionPlugin)
                    owner._catalog = catalog
                    owner._runtime = CompanionRuntime(global_rate_seconds=0)
                    owner._observer_status = {"enabled": True, "state": "observing"}
                    owner._assistant_client = VisionAssistantClient()
                    owner._analysis_game_pid = captured.game.get("pid")
                    values = {}
                    async def get(key):
                        return Ok(values.get(key))
                    async def set_value(key, value):
                        values[key] = value
                        return Ok(value)
                    owner.store = SimpleNamespace(get=get, set=set_value)
                    owner.push_message = lambda **kwargs: {"submitted": False}
                    producer_result = {}
                    def analyze_live(payload, **kwargs):
                        value = adapter.analyze_live(payload, **kwargs)
                        producer_result.update(analyzed_page=value.get("page", {}).get("page_type"),
                            processing_seconds=value.get("recognition", {}).get("processing_seconds"),
                            page_region_count=len(value.get("recognition", {}).get("scene_regions") or []))
                        return value
                    owner._vision = SimpleNamespace(analyze_live=analyze_live, analyze_scene=adapter.analyze_scene,
                        analyze_navigation=adapter.analyze_navigation, verify_text_regions=adapter.verify_text_regions)
                    async def produce():
                        await asyncio.sleep(.01)  # Query arms before producer starts.
                        value = await guarded_analysis(owner, captured.image_bytes, helper=True)
                        if value is not None:
                            producer_result.update(page=value.get("page", {}).get("page_type"),
                                processing_seconds=value.get("recognition", {}).get("processing_seconds"),
                                unstable_text_rows_removed=value.get("recognition", {}).get("unstable_text_rows_removed"),
                                text_revalidation_confirmed=value.get("recognition", {}).get("text_revalidation_confirmed"),
                                field_checks=value.get("screen_facts", {}).get("field_checks"))
                            await owner._apply_local_analysis(value, image_fingerprint="source-query-memory-only",
                                source="assistant_local_ocr", observed_at=datetime.fromtimestamp(captured.timestamp, timezone.utc).isoformat())
                    owner._observer_task = asyncio.create_task(produce())
                    started = time.monotonic()
                    selected = await refresh_for_query(owner, timeout=25, require_characters=not args.navigation,
                        require_content=not args.navigation, require_navigation=args.navigation)
                    elapsed = time.monotonic() - started
                    await owner._observer_task
                    emit({"kind": "current_source_real_capture_ocr_query_NOT_installed_or_chat", "sample": sample,
                          "elapsed": round(elapsed, 3), "frame_seq": captured.frame_seq,
                          "fresh": bool(selected and selected.get("fresh")), "page": (selected or {}).get("page_type"),
                          "visible_text_count": len(((selected or {}).get("screen_facts") or {}).get("visible_text") or []),
                          "fields": [{k: f.get(k) for k in ("kind", "value")}
                                     for f in ((selected or {}).get("screen_facts") or {}).get("facts") or []],
                          "producer": producer_result,
                          "refresh": owner._last_refresh_diagnostics})
            asyncio.run(run())
            return
        for sample in range(1, args.samples + 1):
            for scope, reader in (("scene", adapter.analyze_scene), ("full", adapter.analyze)):
                started = time.monotonic()
                value = reader(frame.image_bytes)
                screen = value.get("screen_facts") or {}
                emit({"sample": sample, "scope": scope, "elapsed": round(time.monotonic() - started, 3),
                      "ok": value.get("ok"), "page": value.get("page"), "scene": value.get("scene"),
                      "ocr_error": (value.get("ocr") or {}).get("error"),
                      "visible_text": screen.get("visible_text") or [],
                      "facts": [{k: f.get(k) for k in ("kind", "scope", "value")} for f in screen.get("facts") or []],
                      "character_count": len(value.get("characters") or [])})
    finally:
        adapter.close()


if __name__ == "__main__":
    main()
