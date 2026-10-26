"""Counterbalanced same-image PoC; no installs, game input, host messages/images saved.

Fixed image-pair proof retention is NOT a live delivery or host chat receipt.
"""
import argparse
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import statistics
import time
from datetime import datetime, timezone

from plugin.plugins.hsr_companion.experiments.regional_reader import RegionalReader


def arm_order(index):
    return ("full", "regional") if index % 2 == 0 else ("regional", "full")


def read_pair(readers, payload, index, meter, page):
    arms = {}
    for name in arm_order(index):
        meter.calls.clear()
        started = time.perf_counter()
        value = readers[name](payload)
        arms[name] = {"value": focused(value, page), "read_seconds": round(time.perf_counter() - started, 4),
            "model_calls": list(meter.calls), "page": value.get("page", {}).get("page_type"),
            "scene_confirmed": value.get("scene", {}).get("verified", False)}
    return arms


class Meter:
    def __init__(self, backend):
        self.backend, self.calls = backend, []
    def __getattr__(self, name):
        value = getattr(self.backend, name)
        if name not in {"extract_text_with_boxes", "recognize_line"} or not callable(value):
            return value
        def measured(image, *args, **kwargs):
            started = time.perf_counter()
            try:
                return value(image, *args, **kwargs)
            finally:
                self.calls.append({"method": name, "size": list(image.size),
                                   "seconds": round(time.perf_counter() - started, 4)})
        return measured


def focused(value, page):
    value = copy.deepcopy(value)
    scopes = {"location", "quest"} if page == "exploration_hud" else {"equipment"}
    screen = value.get("screen_facts") or {}
    controls = value.get("recognition", {}).get("scene_regions", [])
    def key(item):
        return tuple(round(float(item.get("region", {}).get(k, -1)), 5) for k in ("left", "top", "right", "bottom"))
    wanted = {key(c) for c in controls}
    value["screen_facts"] = {**screen, "facts": [f for f in screen.get("facts", []) if f.get("scope") in scopes],
                            "visible_text": [r for r in screen.get("visible_text", []) if key(r) in wanted]}
    value["characters"] = []
    return value


def fields(value):
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    screen = value.get("screen_facts") or {}
    rows = [{k: f.get(k) for k in ("kind", "label", "value")} for f in screen.get("facts", [])]
    equipment = equipment_card({**value, "fresh": True})
    return {"rows": rows, "count": len(rows), "equipment": {
        k: equipment.get(k) for k in ("slot", "level", "main_stat", "sub_stats", "complete")}}


def main():
    from PIL import Image
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime
    from plugin.plugins.hsr_companion.runtime.live_refresh import stable_screen_content
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", choices=("exploration_hud", "relic_detail"), required=True)
    parser.add_argument("--samples", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--assistant-url", default="http://127.0.0.1:8220")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        "adapters/local_vision.py", "experiments/regional_reader.py", "runtime/screen_facts.py",
        "runtime/live_refresh.py", "scripts/benchmark_regional_poc.py")}
    report = Path(args.report).resolve()
    if not report.is_relative_to(root / "dist") or report.exists():
        raise ValueError("Use a new plugin dist report path; never overwrite an earlier experiment")
    catalog = ExternalKnowledgeCatalog(registry=json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8")))
    avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
        root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
    adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
    client = VisionAssistantClient(args.assistant_url)
    results = []
    def emit(value):
        results.append(value)
        print(json.dumps(value, ensure_ascii=True), flush=True)
    try:
        health = client.health()
        if not health.get("connected") or not health.get("game", {}).get("running"):
            emit({"kind": "precondition_failed", "reason": "helper_or_game_unavailable"})
            return
        backend = adapter._get_backend()
        meter = Meter(backend)
        adapter._backend = meter  # This isolated adapter only, never host/installed state.
        regional = RegionalReader(meter, catalog)
        readers = {"full": lambda p: adapter.analyze_live(p, cancelled=lambda: False, on_scene=lambda value: None),
                   "regional": regional.analyze}
        warm = client.fetch_frame(0)
        if warm is None:
            raise RuntimeError("No input frame")
        # Explicit warm-up for BOTH arms; no scene/text/answer injected.
        warm_pages = {name: readers[name](warm.image_bytes).get("page", {}).get("page_type") for name in arm_order(0)}
        emit({"kind": "warmup_excluded", "pages": warm_pages, "model": getattr(backend, "model", type(backend).__name__)})
        if warm_pages["full"] != args.page:
            emit({"kind": "precondition_failed", "reason": "actual_page_differs", "expected": args.page})
            return
        for index in range(args.samples):
            frame = client.fetch_frame(0)
            if frame is None:
                raise RuntimeError("Capture unavailable during fixed batch")
            payload = frame.image_bytes
            digest = hashlib.sha256(payload).hexdigest()
            with Image.open(BytesIO(payload)) as image:
                size = image.size
            arms = read_pair(readers, payload, index, meter, args.page)
            validation = client.fetch_frame(0)
            if validation is None:
                raise RuntimeError("No subsequent frame for shared proof retention")
            observations = {}
            for name in arm_order(index):
                arm = arms[name]
                meter.calls.clear()
                started = time.perf_counter()
                checked = stable_screen_content(arm["value"], payload, validation.image_bytes,
                    verifier=adapter.verify_text_regions)
                preserved = fields(checked)
                controls = bool(checked.get("recognition", {}).get("page_controls_revalidated"))
                observations[name] = {k: arm[k] for k in ("read_seconds", "page", "scene_confirmed", "model_calls")}
                observations[name].update(raw=fields(arm["value"]), after_shared_proof=preserved,
                    proof_seconds=round(time.perf_counter() - started, 4),
                    proof_model_calls=list(meter.calls), page_controls_preserved=controls,
                    proof_retained_field_count=preserved["count"] if controls else 0,
                    requested_native_pixels=sum(c["size"][0] * c["size"][1] for c in arm["model_calls"]),
                    maximum_model_call_seconds=max((c["seconds"] for c in arm["model_calls"]), default=0))
            emit({"kind": "same_image_offline_paired_read_and_proof_NOT_live_delivery", "pair": index + 1,
                  "expected_page": args.page, "order": arm_order(index), "input_sha256": digest,
                  "input_seq": frame.frame_seq, "input_timestamp": frame.timestamp, "native_size": size,
                  "proof_sha256": hashlib.sha256(validation.image_bytes).hexdigest(), "proof_seq": validation.frame_seq,
                  "proof_timestamp": validation.timestamp, "image_stored": False, "arms": observations})
        pairs = [r for r in results if "arms" in r]
        emit({"kind": "summary_small_sample_not_accuracy_or_host_acceptance", "pairs": len(pairs),
            "arms": {name: {"median_read_seconds": statistics.median(p["arms"][name]["read_seconds"] for p in pairs),
                "raw_field_counts": [p["arms"][name]["raw"]["count"] for p in pairs],
                "retained_field_counts": [p["arms"][name]["proof_retained_field_count"] for p in pairs]}
                for name in ("full", "regional")}})
    finally:
        adapter.close()
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as output:
            json.dump({"schema_version": 1, "experiment": "regional_poc_v1", "checked_at": datetime.now(timezone.utc).isoformat(),
                       "source_hashes_at_start": source_hashes,
                       "same_model": True, "same_input_within_pair": True, "image_stored": False,
                       "host_messages": False, "installation_changed": False, "results": results}, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
