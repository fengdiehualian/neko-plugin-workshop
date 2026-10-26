"""Opt-in condition trace. Real captures/OCR; no host messages, installation or saved images.

Keep one warmed engine between entry and item switch. Each regional read is
fresh (no answer cache); a full read of that same input is a diagnostic control,
not a fair live latency comparison. Proof retention is a fixed-image-pair check,
NOT the formal live admission/host speech path.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from plugin.plugins.hsr_companion.experiments.regional_reader import RegionalReader
from plugin.plugins.hsr_companion.scripts.benchmark_regional_poc import focused, fields, Meter


def complete_stats(value):
    rows = (value.get("screen_facts") or {}).get("facts") or []
    return (sum(r.get("kind") == "main_stat" for r in rows) == 1
            and sum(r.get("kind") == "sub_stat" for r in rows) == 4
            and not any(r.get("kind") in {"equipment_binding_missing", "unreadable_stat"} for r in rows))


def binding_summary(trace):
    events = [e for e in trace if e.get("stage") == "relic_binding"]
    if not events:
        return {"binding_event_present": False}
    event = events[-1]
    return {"binding_event_present": True, "accepted": event["binding_accepted"],
            "failed_conditions": [k for k, v in event["conditions"].items() if not v],
            "row_count": len(event["rows"]), "upgrade_count": len(event["upgrades"]),
            "level_gap": event["level_to_first_row_gap"]}


def main():
    from PIL import Image
    from plugin.plugins.hsr_companion.adapters import local_vision
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime
    from plugin.plugins.hsr_companion.runtime.live_refresh import stable_screen_content
    from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assistant-url", default="http://127.0.0.1:8219")
    parser.add_argument("--report", required=True)
    parser.add_argument("--samples", type=int, choices=(2, 3, 4), default=3)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = Path(args.report).resolve()
    if not report.is_relative_to(root / "dist") or report.exists():
        raise ValueError("New plugin dist report only; never overwrite previous results")
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
                                root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
    adapter = local_vision.LocalVisionAdapter(catalog, avatar_pack=avatars)
    client = VisionAssistantClient(args.assistant_url)
    results = []
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        "runtime/screen_facts.py", "runtime/live_refresh.py", "adapters/text_verification.py",
        "adapters/local_vision.py", "experiments/regional_reader.py", "scripts/audit_relic_conditions.py")}

    def emit(event):
        results.append(event)
        # Full diagnostic text stays in the explicit local report, not the terminal.
        print(json.dumps({k: v for k, v in event.items() if k not in {"trace", "verification_trace", "proof_trace"}},
                         ensure_ascii=True), flush=True)

    def capture(since=0, seconds=4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            frame = client.fetch_frame(since)
            if (frame is not None and frame.frame_seq > since and 0 <= time.time() - frame.timestamp <= 2
                    and frame.game.get("running") and not frame.game.get("minimized")
                    and frame.capture.get("state") == "capturing"
                    and frame.capture.get("isolated_window") is True):
                return frame
            time.sleep(.15)
        return None

    active_trace = []
    original_extract = local_vision.extract_screen_facts
    def traced_extract(*a, **kw):
        return extract_screen_facts(*a, **kw, trace=active_trace)

    try:
        health = client.health()
        if not health.get("connected") or not health.get("game", {}).get("running"):
            emit({"kind": "precondition_failed", "health": health})
            return
        meter = Meter(adapter._get_backend())
        adapter._backend = meter
        # Initialize inference without using a relic as a warm answer.
        meter.extract_text_with_boxes(Image.new("RGB", (320, 160), (24, 24, 24)))
        regional = RegionalReader(meter, catalog)
        baseline = capture()
        if baseline is None:
            emit({"kind": "precondition_failed", "reason": "no_fresh_baseline_frame"})
            return
        base = regional.analyze(baseline.image_bytes)
        emit({"kind": "baseline", "page": base.get("page", {}).get("page_type"), "image_stored": False})
        local_vision.extract_screen_facts = traced_extract  # This isolated process only.
        print('{"kind":"ready","commands":"entry / switch / quit"}', flush=True)
        previous_identity = None
        for phase_number in range(2):
            command = input().strip()
            if command == "quit":
                break
            if command not in {"entry", "switch"}:
                raise ValueError("Expected entry or switch")
            phase_start = time.monotonic()
            first_frame_at = None
            first_complete_after_capture = None
            phase_identity = None
            controls = []  # At most four in-memory pairs, released after this phase.
            def inspect(arm, original, frame, validation, trace, read_seconds, sample):
                verification_trace, proof_trace = [], []
                def verify(payload, proofs, *, cancelled=None):
                    return adapter.verify_text_regions(payload, proofs, cancelled=cancelled, trace=verification_trace)
                checked = (stable_screen_content(original, frame.image_bytes, validation.image_bytes,
                           verifier=verify, trace=proof_trace) if validation else None)
                ready = bool(checked and complete_stats(checked)
                             and checked.get("recognition", {}).get("page_controls_revalidated"))
                emit({"kind": "condition_sample", "phase": command, "phase_number": phase_number + 1,
                      "sample": sample + 1, "arm": arm, "page": original.get("page", {}).get("page_type"),
                      "raw": fields(original), "after_proof": fields(checked) if checked else None,
                      "binding": binding_summary(trace), "complete_after_proof": ready,
                      "read_seconds": round(read_seconds, 3), "phase_elapsed": round(time.monotonic() - phase_start, 3),
                      "input_sha256": hashlib.sha256(frame.image_bytes).hexdigest(),
                      "input_seq": frame.frame_seq, "input_timestamp": frame.timestamp,
                      "proof_sha256": hashlib.sha256(validation.image_bytes).hexdigest() if validation else None,
                      "proof_seq": validation.frame_seq if validation else None,
                      "proof_timestamp": validation.timestamp if validation else None,
                      "same_input_for_both_arms": True, "trace": copy.deepcopy(trace),
                      "verification_trace": verification_trace, "proof_trace": proof_trace})
                return ready
            for sample in range(args.samples):
                frame = capture()
                if frame is None:
                    emit({"kind": "capture_failed", "phase": command, "sample": sample + 1})
                    continue
                if first_frame_at is None:
                    first_frame_at = frame.timestamp
                trace = []
                meter.calls.clear()
                started = time.monotonic()
                value = regional.analyze(frame.image_bytes, trace=trace)
                read_seconds = time.monotonic() - started
                original = focused(value, "relic_detail")
                validation = capture(frame.frame_seq)
                ready = inspect("regional", original, frame, validation, trace, read_seconds, sample)
                identity = tuple((r.get("kind"), r.get("value"))
                    for r in original.get("screen_facts", {}).get("facts", [])
                    if r.get("kind") in {"equipment_name", "equipment_slot", "equipment_level"})
                phase_identity = identity or phase_identity
                if ready and first_complete_after_capture is None:
                    first_complete_after_capture = round(time.time() - first_frame_at, 3)
                controls.append((sample, frame, validation))
            emit({"kind": "phase_summary", "phase": command, "identity": phase_identity,
                  "different_from_previous_identity": phase_identity != previous_identity if previous_identity else None,
                  "first_complete_after_first_sample_capture_seconds": first_complete_after_capture,
                  "actual_user_switch_time_measured": False, "correctness_requires_user_confirmation": True,
                  "no_candidate_cache_or_cross_frame_merge": True})
            # Diagnostic full-screen controls cannot delay production of the
            # regional first complete candidate. Reuse the exact image pairs.
            for sample, frame, validation in controls:
                active_trace.clear()
                started = time.monotonic()
                original = focused(adapter.analyze_live(frame.image_bytes,
                    cancelled=lambda: False, on_scene=lambda _: None), "relic_detail")
                inspect("full", original, frame, validation, active_trace, time.monotonic() - started, sample)
            controls.clear()
            previous_identity = phase_identity
            print('{"kind":"awaiting_next_phase"}', flush=True)
    finally:
        local_vision.extract_screen_facts = original_extract
        adapter.close()
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as output:
            json.dump({"experiment": "relic_condition_trace_v1", "checked_at": datetime.now(timezone.utc).isoformat(),
                       "source_hashes": hashes, "image_stored": False, "host_messages": False,
                       "installed_plugin_changed": False, "results": results}, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
