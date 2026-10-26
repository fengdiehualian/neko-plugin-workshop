"""Resident production adapter -> real guards/commit -> formal tool; isolated host only.

No installations, helper launches, game input, host messages or saved images.
First-entry/switch timing starts at the first recognized new-object input frame,
not the human click or cat speech. In-memory ablation runs AFTER the observer
drains so it cannot contaminate the formal query's scheduling.
"""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import time


async def audit(args):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime
    from plugin.plugins.hsr_companion.runtime import live_refresh
    from plugin.plugins.hsr_companion.runtime.delivery_scope import project_evidence
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    from plugin.plugins.hsr_companion.runtime.fact_lifecycle import candidate_evidence
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_resident_observer import configure_loop

    root = Path(__file__).resolve().parents[1]
    report = Path(args.report).resolve()
    if not report.is_relative_to(root / "dist") or report.exists():
        raise ValueError("Use a new plugin dist report, never replace a frozen run")
    source_names = ("runtime/screen_facts.py", "adapters/relic_values.py", "adapters/text_verification.py",
                    "runtime/live_refresh.py", "runtime/query_reuse.py", "detectors/relic_geometry.py",
                    "detectors/layout.py", "detectors/scene_preview.py", "adapters/local_vision.py",
                    "scripts/audit_relic_repair_live.py")
    hashes = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
        root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
    adapter, client = LocalVisionAdapter(catalog, avatar_pack=avatars), VisionAssistantClient(args.assistant_url)
    owner, values, sent = plugin_fixture(root / ".isolated-relic-audit-unused")
    owner._catalog, owner._vision = catalog, adapter
    owner._target_character = lambda: ""
    owner.logger = logging.getLogger("hsr-isolated-relic-repair")
    values["observer_settings"] = {"enabled": True, "proactive_enabled": False, "interval_seconds": 2}
    configure_loop(owner, client)
    events, archive, memory_samples = [], {}, []
    phase = {"name": "baseline", "started": time.time(), "first_capture": None, "first_complete": None,
             "serials": set(), "previous_object": None}

    def emit(value):
        events.append(value)
        print(json.dumps(value, ensure_ascii=True), flush=True)

    def object_key(card):
        return (card.get("name"), card.get("slot"), json.dumps(card.get("main_stat"), sort_keys=True))

    original_analyze = adapter.analyze_live
    def analyze(payload, **kw):
        capture_at = getattr(owner, "_analysis_capture_at", "")
        result = original_analyze(payload, **kw)
        card = equipment_card({**result, "fresh": True})
        key = object_key(card)
        if (phase["name"] in {"entry", "switch"} and result.get("page", {}).get("page_type") == "relic_detail"
                and (phase["name"] == "entry" or key != phase["previous_object"])):
            if phase["first_capture"] is None and capture_at:
                phase["first_capture"] = datetime.fromisoformat(capture_at).timestamp()
        if len(memory_samples) < 2:
            fact = next((f for f in result.get("screen_facts", {}).get("facts", [])
                         if f.get("kind") == "sub_stat" and f.get("label") == "副词条·速度"), None)
            if fact and phase["name"] not in {s[0] for s in memory_samples}:
                memory_samples.append((phase["name"], payload, copy.deepcopy(fact)))
        emit({"kind": "raw_read", "phase": phase["name"], "captured_at": capture_at,
              "page": result.get("page", {}).get("page_type"), "raw_complete": card["complete"],
              "name": card["name"], "slot": card["slot"], "main_stat": card["main_stat"],
              "sub_stats": card["sub_stats"]})
        return result
    adapter.analyze_live = analyze
    original_apply = owner._apply_local_analysis
    async def apply(*a, **kw):
        returned = await original_apply(*a, **kw)
        raw = getattr(owner, "_committed_observation", None) or {}
        visual = candidate_evidence(raw)
        serial = raw.get("observation_serial", 0)
        if serial:
            archive[serial] = copy.deepcopy(visual)
            while len(archive) > 16:
                del archive[min(archive)]
        card = equipment_card(visual)
        is_new = phase["name"] != "switch" or object_key(card) != phase["previous_object"]
        if raw.get("page", {}).get("page_type") == ("exploration_hud" if phase["name"] in {"baseline", "explore"} else "relic_detail"):
            if is_new:
                phase["serials"].add(serial)
            if is_new and card["complete"] and phase["first_complete"] is None:
                phase["first_complete"] = time.time()
        emit({"kind": "committed", "phase": phase["name"], "serial": serial,
              "page": raw.get("page", {}).get("page_type"), "complete": card["complete"],
              "name": card["name"], "slot": card["slot"], "main_stat": card["main_stat"],
              "sub_stats": card["sub_stats"], "digest": card["content_digest"]})
        return returned
    owner._apply_local_analysis = apply

    async def query(text, label):
        started = time.monotonic()
        result = await owner.hsr_companion_prepare(query=text)
        visual = result.get("current_visual_evidence") or {}
        card = equipment_card(visual)
        committed = archive.get(card["observation_serial"])
        expected = equipment_card(project_evidence(committed, build_query_plan(text))) if committed else None
        projection = await owner._current_visual_evidence()
        emit({"kind": "formal_tool", "phase": phase["name"], "label": label,
              "seconds": round(time.monotonic() - started, 3), "page": visual.get("page_type"),
              "answer": result.get("query_answer"), "equipment": card,
              "selected_serial_found_in_commits": committed is not None,
              "same_selected_commit_digest": expected["content_digest"] == card["content_digest"] if expected else None,
              "panel_serial_at_return": projection.get("screen_facts", {}).get("observation_serial"),
              "panel_fresh_at_return": projection.get("fresh"),
              "timing": result.get("query_timing"), "delivery": result.get("delivery_receipt")})
        return card

    try:
        health = await asyncio.to_thread(client.health)
        if not health.get("connected") or not health.get("game", {}).get("running"):
            emit({"kind": "precondition_failed", "reason": "helper_or_game_unavailable"})
            return
        await asyncio.to_thread(adapter._get_backend)
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        deadline = time.monotonic() + 90
        while not phase["serials"] and time.monotonic() < deadline:
            await asyncio.sleep(.25)
        if not phase["serials"]:
            emit({"kind": "precondition_failed", "reason": "no_exploration_baseline_commit",
                  "state": owner._observer_status})
            return
        previous = None
        for name in ("entry", "switch", "explore"):
            phase.update(name=name, started=time.time(), first_capture=None, first_complete=None,
                         serials=set(), previous_object=previous)
            print(json.dumps({"kind": "awaiting_user_switch", "phase": name,
                              "resident_observer_keeps_running": True}), flush=True)
            if (await asyncio.to_thread(input)).strip() == "quit":
                break
            text = "现在在哪，接下来这个任务要做什么？" if name == "explore" else "这件遗器的主副词条是什么？"
            await query(text, "immediate_after_user_confirmation")
            deadline = time.monotonic() + 30
            while len(phase["serials"]) < 3 and time.monotonic() < deadline:
                await asyncio.sleep(.25)
            card = await query(text, "after_bounded_resident_observation")
            if name != "explore" and card.get("name"):
                previous = object_key(card)
            elapsed = (phase["first_complete"] - phase["first_capture"]
                       if phase["first_complete"] and phase["first_capture"] else None)
            emit({"kind": "phase_summary", "phase": name, "new_page_commit_count": len(phase["serials"]),
                  "first_complete_after_first_recognized_input_seconds": round(elapsed, 3) if elapsed is not None else None,
                  "actual_human_click_or_host_speech_latency": False, "correctness_requires_user_confirmation": True})
    finally:
        owner._observer_stop_requested = True
        task = getattr(owner, "_observer_task", None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await live_refresh.drain_analysis_worker(owner)
        # Same-image diagnostic only, after production scheduling has stopped.
        for name, payload, fact in memory_samples:
            proof = next((p for p in fact.get("evidence", []) if p.get("text") == fact["value"]), None)
            row = next((p for p in fact.get("evidence", []) if p.get("text") == "速度"), None)
            table = next((v.get("search_crop") for v in fact.get("value_verification", []) if v.get("search_crop")), None)
            if table is None and row:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(payload)) as image:
                    width, height = image.size
                bounds = next((v.get("crop") for v in fact.get("value_verification", [])
                               if v.get("method") == "same_frame_badge_masked_table_ocr"), None)
                if bounds:
                    r = row["region"]
                    rh = (r["bottom"] - r["top"]) * height
                    table = [max(r["right"] * width + rh, bounds[0] + (bounds[2]-bounds[0])*.55),
                             r["top"] * height - rh*.15, bounds[2], r["bottom"] * height + rh*.15]
            if proof and table:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(payload)) as image:
                    width, height = image.size
                wide = {**proof, "region": dict(zip(("left", "top", "right", "bottom"),
                    (table[0]/width, table[1]/height, table[2]/width, table[3]/height)))}
                for label, selected in (("tight", proof), ("wide", wide)):
                    trace = []
                    confirmed = await asyncio.to_thread(adapter.verify_text_regions, payload, [selected], trace=trace)
                    emit({"kind": "same_image_numeric_ablation", "phase": name, "arm": label,
                          "value": fact["value"], "region": selected["region"], "confirmed": bool(confirmed),
                          "trace": trace, "input_sha256": hashlib.sha256(payload).hexdigest(), "image_stored": False})
        adapter.close()
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as output:
            json.dump({"experiment": "relic_repair_resident_formal_v1", "source_hashes": hashes,
                       "image_stored": False, "host_messages": False, "installed_plugin_changed": False,
                       "fake_host_store_only": True, "results": events}, output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assistant-url", default="http://127.0.0.1:8219")
    parser.add_argument("--report", required=True)
    asyncio.run(audit(parser.parse_args()))


if __name__ == "__main__":
    main()
