"""Read-only resident source audit: item A -> real item B -> light cone.

Uses the SAME installed helper executable on a temporary port (no download,
replacement, game input or host chat). Pauses/restores only the HSR observer.
Every question uses the formal entry and original budgets; failed calls stay.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from .audit_field_handoff_isolated import stop_owned, ui_action, ui_context
from .field_pipeline_probe import FieldPipelineProbe, summary


async def audit(args):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime
    from plugin.plugins.hsr_companion.runtime import live_refresh
    from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_resident_observer import configure_loop

    root = Path(__file__).resolve().parents[1]
    report = Path(args.report).resolve()
    if not report.is_relative_to(root / "dist") or report.exists():
        raise ValueError("New plugin-owned report required")
    app = Path("C:/Users/USER/AppData/Local/N.E.K.O")
    helper_root = app / "data-packs/hsr_companion/assistant"
    pointer = helper_root / "current.json"
    executable = (helper_root / json.loads(pointer.read_text(encoding="utf-8"))["executable"]).resolve(strict=True)
    if not executable.is_relative_to(helper_root) or args.port == 8219:
        raise ValueError("Do not replace an installed helper")
    with socket.socket() as check:
        check.bind(("127.0.0.1", args.port))

    def hashes(directory):
        return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*") if p.is_file() and p.suffix != ".pyc" and "__pycache__" not in p.parts}

    installed = app / ".neko-plugin-installations/plugins/hsr_companion"
    installed_before = hashes(installed)
    helper_before = (hashlib.sha256(pointer.read_bytes()).hexdigest(), hashlib.sha256(executable.read_bytes()).hexdigest())
    source_names = ["__init__.py", "runtime/build_info.py", "runtime/live_refresh.py", "runtime/query_reuse.py",
                    "runtime/delivery_scope.py", "runtime/query_coordinator.py", "runtime/followup.py",
                    "adapters/local_vision.py", "adapters/text_reader.py", "adapters/text_verification.py",
                    "adapters/relic_values.py", "detectors/layout.py", "scripts/field_pipeline_probe.py"]
    source_before = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}
    events, transfers = [], []
    owner = adapter = process = probe = None
    enabled, pause_attempted = False, False

    def emit(kind, **data):
        event = {"kind": kind, "at": time.time(), **data}
        events.append(event)
        if kind == "formal_result":
            shown = {"kind": kind, "phase": data["phase"], "seconds": data["seconds"],
                     "card": {k: data["card"].get(k) for k in ("name", "slot", "main_stat", "sub_stats", "complete")},
                     "answer_state": data["answer"].get("state"), "timing": data.get("timing"),
                     "followup": data.get("followup")}
        elif kind in {"resident_ready", "phase_completed"}:
            raw = data.get("observation") or {}
            shown = {"kind": kind, "phase": data.get("phase"), "page": raw.get("page"),
                     "serial": raw.get("serial"), "followup": data.get("followup")}
        else:
            shown = event
        print(json.dumps(shown, ensure_ascii=True), flush=True)

    try:
        initial = await asyncio.to_thread(ui_context, args.host)
        enabled = initial.get("observer", {}).get("enabled")
        if not isinstance(enabled, bool):
            raise RuntimeError("Unknown installed observer state")
        if enabled:
            pause_attempted = True
            await asyncio.to_thread(ui_action, args.host, "stop_auto_observation")
        paused = await asyncio.to_thread(ui_context, args.host)
        if paused.get("observer", {}).get("enabled") is not False:
            raise RuntimeError("Do not run competing recognizers")
        process = subprocess.Popen([str(executable), "--port", str(args.port), "--parent-pid", str(os.getpid())],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)

        class Client(VisionAssistantClient):
            def fetch_frame(self, since_frame=0):
                start = time.monotonic()
                frame = super().fetch_frame(since_frame)
                transfers.append({"seconds": round(time.monotonic() - start, 4),
                                  "seq": frame.frame_seq if frame else None,
                                  "age": round(time.time() - frame.timestamp, 4) if frame else None})
                return frame

        client = Client(f"http://127.0.0.1:{args.port}")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            health = await asyncio.to_thread(client.health)
            if health.get("connected") and health.get("game", {}).get("running"):
                break
            await asyncio.sleep(.25)
        else:
            raise RuntimeError("Same installed helper/game unavailable")
        emit("helper_ready", version=health.get("service_version"), helper_sha256=helper_before[1])
        registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
        catalog = ExternalKnowledgeCatalog(registry=registry)
        avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
                                     root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
        adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
        if args.ablation:
            from plugin.plugins.hsr_companion.adapters.text_verification import proof_key, verify_text_regions
            backend = await asyncio.to_thread(adapter._get_backend)
            frame = await asyncio.to_thread(client.fetch_frame, 0)
            if frame is None:
                raise RuntimeError("No live frame for same-image comparison")
            raw = await asyncio.to_thread(adapter.analyze, frame.image_bytes)
            controls = raw.get("recognition", {}).get("scene_regions", [])
            detection_only = SimpleNamespace(extract_text_with_boxes=backend.extract_text_with_boxes)
            for control in controls:
                comparisons = []
                for name, reader in (("line", backend), ("detect", detection_only)):
                    trace = []
                    keys = await asyncio.to_thread(verify_text_regions, reader, frame.image_bytes, [control], trace=trace)
                    comparisons.append({"method": name, "confirmed": proof_key(control) in keys, "trace": trace})
                emit("same_image_control_comparison", control=control, comparisons=comparisons)
            return
        owner, values, sent = plugin_fixture(root / ".equipment-turns-unused")
        owner._vision, owner._catalog = adapter, catalog
        owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **_: [])))
        owner._target_character = lambda: "isolated-audit-target"  # Fake bus/sender only.
        owner.logger = logging.getLogger("hsr-equipment-turns-audit")
        values["observer_settings"] = {"enabled": True, "proactive_enabled": False, "interval_seconds": 2}
        configure_loop(owner, client)
        await asyncio.to_thread(adapter._get_backend)
        probe = FieldPipelineProbe(owner)
        probe.__enter__()
        wrapped_verify = adapter.verify_text_regions

        def timed_verify(payload, proofs, **kwargs):
            start = time.monotonic()
            try:
                return wrapped_verify(payload, proofs, **kwargs)
            finally:
                probe.emit("review_duration", seconds=round(time.monotonic() - start, 4), count=len(proofs))

        adapter.verify_text_regions = timed_verify
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        emit("resident_started", build=BUILD_REVISION, helper_updated=False, installed=False)
        # Real observer runs BEFORE any query; no pre-read answer injection.
        await asyncio.sleep(args.resident_seconds)
        emit("resident_ready", observation=summary(getattr(owner, "_committed_observation", None)))
        phases = args.phases or (("cone", "item_a", "item_b") if args.cone_first else ("item_a", "item_b", "cone"))
        for phase_index, phase in enumerate(phases):
            emit("awaiting_user", phase=phase)
            command = "go" if args.start_current and phase_index == 0 else (await asyncio.to_thread(input)).strip().lower()
            if command == "quit":
                break
            probe.phase = phase
            query = "这个光锥适合绯英吗？" if phase == "cone" else "这件遗器给真珠用怎么样？"
            emit("phase_started", phase=phase, current_serial=getattr(owner, "_observation_serial", 0))
            start = time.monotonic()
            result = await owner.hsr_companion_prepare(query=query)
            emit("formal_result", phase=phase, seconds=round(time.monotonic() - start, 3),
                 producer_role=getattr(owner, "_vision_schedule_role", "idle"),
                 card=equipment_card(result.get("current_visual_evidence") or {}),
                 answer=result.get("query_answer"), timing=result.get("query_timing"),
                 guidance=result.get("guidance_answer"), followup=result.get("followup"))
            pending = getattr(owner, "_followup_task", None)
            if pending is not None and not pending.done():
                try:
                    await asyncio.wait_for(asyncio.shield(pending), timeout=40)
                except TimeoutError:
                    emit("followup_unfinished", phase=phase)
            emit("phase_completed", phase=phase, observation=summary(getattr(owner, "_committed_observation", None)),
                 followup=getattr(owner, "_followup_status", {}), deliveries=getattr(owner, "_answer_deliveries", [])[-2:])
        emit("host_output_isolated", messages_captured=len(sent), host_messages_sent=0)
    except Exception as exc:
        emit("audit_failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if owner is not None:
            owner._observer_stop_requested = True
            for name in ("_followup_task", "_observer_task"):
                task = getattr(owner, name, None)
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await live_refresh.drain_analysis_worker(owner)
        if probe:
            probe.__exit__(None, None, None)
        if adapter:
            adapter.close()
        await asyncio.to_thread(stop_owned, process)
        if pause_attempted and enabled:
            try:
                current = await asyncio.to_thread(ui_context, args.host)
                if current.get("observer", {}).get("enabled") is False:
                    await asyncio.to_thread(ui_action, args.host, "start_auto_observation")
                restored = await asyncio.to_thread(ui_context, args.host)
                emit("installed_observer_restored", enabled=restored.get("observer", {}).get("enabled"))
            except Exception as exc:
                emit("restore_failed", error_type=type(exc).__name__)
        preservation = {"installed_unchanged": installed_before == hashes(installed),
                        "helper_unchanged": helper_before == (hashlib.sha256(pointer.read_bytes()).hexdigest(),
                                                            hashlib.sha256(executable.read_bytes()).hexdigest()),
                        "source_unchanged_during_run": source_before == {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}}
        with report.open("x", encoding="utf-8") as out:
            json.dump({"build_revision": BUILD_REVISION, "events": events, "trace": probe.events if probe else [],
                       "trace_errors": probe.errors if probe else None, "trace_dropped": probe.dropped if probe else None,
                       "source_hashes": source_before, "transfers": transfers, "preservation": preservation,
                       "evidence_scope": "real resident OCR/formal tool; isolated host/store, no actual cat speech"},
                      out, ensure_ascii=False, indent=2)
        print(json.dumps({"report": str(report), **preservation}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report")
    parser.add_argument("--restore-only", action="store_true")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--cone-first", action="store_true")
    parser.add_argument("--phases", nargs="+", choices=("cone", "item_a", "item_b"))
    parser.add_argument("--start-current", action="store_true")
    parser.add_argument("--host", choices=("http://127.0.0.1:48916",), default="http://127.0.0.1:48916")
    parser.add_argument("--port", type=int, choices=(8222,), default=8222)
    parser.add_argument("--resident-seconds", type=int, choices=(20, 45), default=20)
    args = parser.parse_args()
    if args.restore_only:
        state = ui_context(args.host).get("observer", {})
        if state.get("enabled") is False:
            ui_action(args.host, "start_auto_observation")
            state = ui_context(args.host).get("observer", {})
        print(json.dumps({"restore_checked": True, "observer": {k: state.get(k) for k in ("enabled", "state")}}, ensure_ascii=True))
        if state.get("enabled") is not True:
            raise RuntimeError("Installed observation not confirmed restored")
    elif args.report:
        asyncio.run(audit(args))
    else:
        parser.error("--report is required unless --restore-only")


if __name__ == "__main__":
    main()
