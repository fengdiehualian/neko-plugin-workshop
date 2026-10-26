"""Real resident observer first, question second; isolated storage and output.

Runs the production _observer_loop, capture/OCR/validation/commit/query code.
Never installs, launches a helper, saves images or sends messages to N.E.K.O.
An installed observer may also be running: this is source diagnostics under
additional load, NOT an installed-chat latency benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


async def run(args):
    from plugin.plugins.hsr_companion import HsrCompanionPlugin
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.core import CharacterRegistry
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime
    from plugin.plugins.hsr_companion.runtime import CompanionRuntime
    from plugin.plugins.hsr_companion.runtime.live_refresh import (
        drain_analysis_worker,
        refresh_for_query,
        validated_delivery,
    )
    from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.sdk.plugin import Ok

    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
        root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
    adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
    client = VisionAssistantClient()
    health = await asyncio.to_thread(client.health)
    if not health.get("connected") or not (health.get("game") or {}).get("running"):
        raise RuntimeError("Existing helper/game required; this diagnostic never launches them")

    owner = object.__new__(HsrCompanionPlugin)
    owner._catalog, owner._characters = catalog, CharacterRegistry(registry)
    owner._runtime = CompanionRuntime(global_rate_seconds=0)
    owner._observer_status = {"enabled": True, "state": "observing", "proactive_enabled": False}
    owner._assistant_client, owner._assistant_frame_seq = client, 0
    owner._observer_last_signature, owner._observer_last_push_monotonic = None, 0
    owner._observer_wake_event = asyncio.Event()
    owner._announced_game_pid = (health.get("game") or {}).get("pid")
    owner.logger = logging.getLogger("hsr-resident-readonly-diagnostic")
    def no_launch():
        raise RuntimeError("Diagnostic will not launch or restart a helper")
    owner._assistant_process = SimpleNamespace(snapshot=lambda: {}, start_if_due=no_launch)
    owner.push_message = lambda **kwargs: {"submitted": False, "reason": "isolated_diagnostic"}
    values = {}
    async def get(key):
        return Ok(values.get(key))
    async def set_value(key, value):
        values[key] = value
        return Ok(value)
    owner.store = SimpleNamespace(get=get, set=set_value)
    started = asyncio.Queue()
    commits, calls = [], []
    loop = asyncio.get_running_loop()

    def full(payload, **kwargs):
        item = {"index": len(calls) + 1, "started": time.monotonic(),
                "waiters_at_start": getattr(owner, "_visual_query_waiters", 0)}
        calls.append(item)
        loop.call_soon_threadsafe(started.put_nowait, item)
        try:
            result = adapter.analyze_live(payload, **kwargs)
            item.update(processing_seconds=(result.get("recognition") or {}).get("processing_seconds"),
                        page=(result.get("page") or {}).get("page_type"))
            return result
        finally:
            item["finished"] = time.monotonic()

    owner._vision = SimpleNamespace(analyze_live=full, analyze_scene=adapter.analyze_scene,
        analyze_navigation=adapter.analyze_navigation, verify_text_regions=adapter.verify_text_regions)
    verification_attempts = []
    if args.verification_details:
        def verify(payload, proofs, **kwargs):
            from plugin.plugins.hsr_companion.runtime.screen_facts import safe_text
            trace = []
            try:
                confirmed = adapter.verify_text_regions(payload, proofs, trace=trace, **kwargs)
                return confirmed
            finally:
                verification_attempts.append({"expected": [safe_text(p.get("text"), 72) for p in proofs],
                    "rows": [{key: safe_text(row[key], 72) if key in {"expected", "actual"} else row.get(key)
                              for key in ("expected", "actual", "valid", "scale", "scores") if key in row}
                             for row in trace][-24:]})
                del verification_attempts[:-8]
        owner._vision.verify_text_regions = verify
    apply = owner._apply_local_analysis
    async def commit(*pos, **kwargs):
        result = await apply(*pos, **kwargs, deliver_event=False)
        commits.append({"at": time.monotonic(), "serial": getattr(owner, "_observation_serial", 0)})
        return result
    owner._apply_local_analysis = commit
    owner._observer_task = asyncio.create_task(owner._observer_loop())
    try:
        for sample in range(1, args.samples + 1):
            while True:
                item = await asyncio.wait_for(started.get(), timeout=55)
                if len(commits) < args.warm_commits or item["waiters_at_start"] != 0 or item.get("finished"):
                    continue
                # The real background full worker is ALREADY RUNNING. Never
                # arm the question first or sleep inside a fake producer.
                await asyncio.sleep(args.delay)
                if not item.get("finished"):
                    break
            began = time.monotonic()
            plan = build_query_plan(args.query)
            answer = await refresh_for_query(owner, timeout=8, query_plan=plan, **plan.refresh_args())
            emit({"kind": "resident_source_real_capture_NOT_installed_or_chat", "sample": sample,
                  "question_after_worker_seconds": round(began - item["started"], 3),
                  "background_full_index": item["index"], "waiters_at_worker_start": item["waiters_at_start"],
                  "worker_running_at_query": not bool(item.get("finished") and item["finished"] <= began),
                  "commits_before_query": sum(c["at"] < began for c in commits),
                  "elapsed": round(time.monotonic() - began, 3),
                  "page": (answer or {}).get("page_type"), "fresh": bool((answer or {}).get("fresh")),
                  "fields": [{"kind": f.get("kind"), "value": f.get("value")}
                             for f in ((answer or {}).get("screen_facts") or {}).get("facts") or []],
                  "observer_state": owner._observer_status.get("state"),
                  "resident_pages": [c.get("page") for c in calls],
                  "query_plan": plan.public(),
                  "refresh": owner._last_refresh_diagnostics})
            if args.settle_seconds and answer is None:
                # Continue the SAME resident producer after the direct budget.
                # Test delayed eligibility, not an actual host response/push.
                end = time.monotonic() + args.settle_seconds
                late = None
                owner._visual_query_waiters = getattr(owner, "_visual_query_waiters", 0) + 1
                settled_trace = {"started": time.monotonic(), "stages": []}
                owner._active_refresh_trace = settled_trace
                try:
                    while time.monotonic() < end:
                        visual = await owner._current_visual_evidence()
                        if getattr(owner, "_observation_pending_reason", "").startswith("verification_pending"):
                            from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
                            recovered = await try_reuse(owner, navigation=plan.navigation,
                                full=not (plan.navigation or plan.scene_only), plan=plan)
                            if recovered is not None:
                                visual = recovered
                        if plan.fields <= satisfied_fields(plan, visual):
                            try:
                                late = await asyncio.wait_for(validated_delivery(owner, visual, plan=plan), timeout=2.5)
                            except TimeoutError:
                                late = None
                            if late is not None:
                                break
                        await asyncio.sleep(.25)
                finally:
                    owner._visual_query_waiters -= 1
                    owner._active_refresh_trace = None
                emit({"kind": "resident_source_delayed_handoff_NOT_chat_followup", "sample": sample,
                      "elapsed_since_question": round(time.monotonic() - began, 3), "ready": late is not None,
                      "page": (late or {}).get("page_type"), "commits": len(commits),
                      "worker_processing": [c.get("processing_seconds") for c in calls],
                      "pending_reason": getattr(owner, "_observation_pending_reason", ""),
                      "late_stages": settled_trace["stages"],
                      "confirmed_kinds": [f.get("kind") for f in ((late or {}).get("screen_facts") or {}).get("facts") or []]})
            if args.verification_details:
                emit({"kind": "opt_in_local_ocr_verification_NOT_chat", "attempts": verification_attempts})
            if args.pause_between and sample < args.samples:
                emit({"waiting_for_page_switch": True, "observer_keeps_running": True,
                      "resume": "Press Enter after the user changes page; no game input is generated"})
                await asyncio.to_thread(input)
    finally:
        owner._observer_stop_requested = True
        owner._observer_task.cancel()
        try:
            await owner._observer_task
        except asyncio.CancelledError:
            pass
        await drain_analysis_worker(owner)
        await asyncio.to_thread(adapter.close)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--warm-commits", type=int, choices=range(0, 4), default=2)
    parser.add_argument("--delay", type=float, default=.5)
    parser.add_argument("--query", default="现在在哪，接下来这个任务要做什么？")
    parser.add_argument("--settle-seconds", type=float, default=0)
    parser.add_argument("--pause-between", action="store_true", help="Keep observer running; stdin Enter starts next query sample")
    parser.add_argument("--verification-details", action="store_true", help="Print bounded local game text verification only; never save frames or send chat")
    args = parser.parse_args()
    if not 0 <= args.delay <= 3:
        parser.error("delay must be in [0, 3]")
    if not 0 <= args.settle_seconds <= 35:
        parser.error("settle-seconds must be in [0, 35]")
    asyncio.run(asyncio.wait_for(run(args), timeout=120))


if __name__ == "__main__":
    main()
