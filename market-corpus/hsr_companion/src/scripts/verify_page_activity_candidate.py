"""One approved resident source run; pause/restore HSR only, no installation.

No game input, saved screenshots, installed helper replacement or host chat submission.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from .audit_field_handoff_isolated import stop_owned, ui_action, ui_context


async def delivery_phases(owner, args, result, emit):
    """Resident first, then two human-operated transitions, no refresh action."""
    root = Path(__file__).resolve().parents[1]
    signal_root = Path(args.signals).resolve()
    if not signal_root.is_relative_to(root / "dist") or signal_root.exists():
        raise ValueError("A new plugin-owned signal directory is required")
    signal_root.mkdir()
    if ((getattr(owner, "_committed_observation", None) or {}).get("page") or {}).get("page_type") != "exploration_hud":
        raise RuntimeError("Exploration baseline was not confirmed; do not count this run")

    async def formal(phase, query, intent, fields):
        start = time.monotonic()
        packet = await owner.hsr_companion_prepare(query=query, intent=intent, screen_fields=fields)
        visual = packet.get("current_visual_evidence") or {}
        screen = visual.get("screen_facts") or {}
        row = {"phase": phase, "intent": intent, "seconds": round(time.monotonic() - start, 3),
               "page": visual.get("page_type"), "answer": packet.get("query_answer"),
               "receipt": packet.get("delivery_receipt"), "timing": packet.get("query_timing"),
               "facts": screen.get("facts"), "equipment": packet.get("equipment_answer"),
               "source_serial": getattr(owner, "_observation_serial", 0)}
        result["formal_results"].append(row)
        emit("formal_entry", **row)

    # The observer is already running. First query must not start its producer.
    await formal("exploration_resident", "现在在哪，任务目标是什么？", "read_screen", ["location", "quest"])
    emit("resident_update_interval", seconds=35)
    await asyncio.sleep(35)
    await formal("exploration_updated", "这个页面是干嘛的，写了什么？", "explain_page", ["page", "visible_text"])
    for phase in ("first_relic", "swapped_relic"):
        emit("awaiting_user_transition", phase=phase, marker=str(signal_root / phase))
        deadline = time.monotonic() + 300
        while not (signal_root / phase).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Human transition not received: " + phase)
            await asyncio.sleep(.25)
        # No pre-read warmup here: the SAME resident loop sees the transition.
        await formal(phase, "完整报出这件遗器主词条和四项副词条", "read_screen", ["page", "equipment"])
        await asyncio.sleep(8)
        await formal(phase + "_continued", "这件主副词条分别是多少？", "read_screen", ["equipment"])


async def verify(args):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime
    from plugin.plugins.hsr_companion.runtime import live_refresh
    from plugin.plugins.hsr_companion.runtime.page_control_trace import public_control_trace
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_resident_observer import configure_loop

    root = Path(__file__).resolve().parents[1]
    report = Path(args.report).resolve()
    if not report.is_relative_to(root / "dist") or report.exists():
        raise ValueError("A new plugin-owned report is required")
    app = Path("C:/Users/USER/AppData/Local/N.E.K.O")
    installed = app / ".neko-plugin-installations/plugins/hsr_companion"
    pointer = app / "data-packs/hsr_companion/assistant/current.json"
    helper_root = pointer.parent
    executable = (helper_root / json.loads(pointer.read_text(encoding="utf-8"))["executable"]).resolve(strict=True)
    if not executable.is_relative_to(helper_root) or args.port == 8219:
        raise ValueError("Never replace the installed assistant")
    with socket.socket() as check:
        check.bind(("127.0.0.1", args.port))
    executable_before = hashlib.sha256(executable.read_bytes()).hexdigest()
    def hashes(directory):
        return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*") if p.is_file() and p.suffix != ".pyc" and "__pycache__" not in p.parts}
    installed_before, pointer_before = hashes(installed), hashlib.sha256(pointer.read_bytes()).hexdigest()
    source_names = ["__init__.py", "runtime/followup.py", "runtime/chat_activity.py", "runtime/page_control_trace.py",
        "runtime/live_refresh.py", "runtime/query_reuse.py", "runtime/review_policy.py", "runtime/delivery_scope.py",
        "adapters/local_vision.py", "detectors/exploration.py", "runtime/build_info.py",
        "runtime/delivery_boundary.py", "runtime/observation_store.py", "runtime/query_coordinator.py",
        "runtime/service_contract.py", "runtime/diagnostics.py", "runtime/revocation_trace.py", "runtime/pending_reply.py",
        "runtime/guidance.py"]
    source_names += ["adapters/text_reader.py", "adapters/ocr_work_queue.py", "runtime/page_handoff.py",
                     "scripts/trace_reader_budget.py", "scripts/trace_character_handoff.py",
                     "scripts/field_pipeline_probe.py", "scripts/verify_page_activity_candidate.py"]
    source_before = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}
    result = {"source_hashes": source_before, "image_stored": False, "installed_changed": False,
              "helper_pointer_changed": False, "events": [], "formal_results": [], "restored": False}
    owner = adapter = process = None
    pause_attempted = False
    enabled = False
    def emit(kind, **value):
        row = {"kind": kind, "at": time.time(), **value}
        result["events"].append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    try:
        client = VisionAssistantClient()
        health = await asyncio.to_thread(client.health)
        if not health.get("connected") or not health.get("game", {}).get("running"):
            emit("precondition_failed", reason="game_or_helper_unavailable")
            return
        initial = await asyncio.to_thread(ui_context, args.host)
        enabled = (initial.get("observer") or {}).get("enabled")
        if type(enabled) is not bool:
            raise RuntimeError("Installed observer state unavailable")
        if enabled:
            pause_attempted = True
            await asyncio.to_thread(ui_action, args.host, "stop_auto_observation")
        paused = await asyncio.to_thread(ui_context, args.host)
        if (paused.get("observer") or {}).get("enabled") is not False:
            raise RuntimeError("Do not run competing recognizers")
        emit("installed_observer_paused", helper_restarted=False)
        # stop_auto_observation stops the installed helper as well. Supply
        # frames from the SAME frozen executable, isolated to this run.
        process = subprocess.Popen([str(executable), "--port", str(args.port), "--parent-pid", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        client = VisionAssistantClient(f"http://127.0.0.1:{args.port}")
        for _ in range(40):
            candidate_health = await asyncio.to_thread(client.health)
            if candidate_health.get("connected") and candidate_health.get("game", {}).get("running"):
                break
            await asyncio.sleep(.25)
        else:
            raise RuntimeError("Isolated unchanged helper did not become ready")
        emit("isolated_helper_ready", service_version=client.service_version,
             executable_sha256=executable_before)
        registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
        catalog = ExternalKnowledgeCatalog(registry=registry)
        avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
            root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
        adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
        owner, values, local_submissions = plugin_fixture(root / ".page-activity-isolated-unused")
        owner._catalog, owner._vision = catalog, adapter
        owner._target_character = lambda: ""  # Never arms a real host followup.
        simulated_events = []
        if args.pending_protocol:
            from plugin.sdk.plugin import NekoPluginBase
            # The public SDK facade, but with a local fake transport. It NEVER
            # reads/writes a real cat's conversation or sends host messages.
            owner.ctx = SimpleNamespace(bus=NekoPluginBase(SimpleNamespace(plugin_id="isolated-live-page-protocol",
                metadata={}, bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: list(simulated_events))))).bus)
            owner._target_character = lambda: "hsr_isolated_test"
            result["pending_protocol"] = []
        values["observer_settings"] = {"enabled": True, "proactive_enabled": False, "interval_seconds": 2}
        configure_loop(owner, client)
        owner._assistant_process = SimpleNamespace(snapshot=lambda: {}, start_if_due=lambda: False)
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        seen = set()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            serial = getattr(owner, "_observation_serial", 0)
            raw = getattr(owner, "_committed_observation", None) or {}
            if serial and serial not in seen:
                seen.add(serial)
                emit("resident_commit", serial=serial, page=raw.get("page", {}).get("page_type"),
                     controls=public_control_trace(owner))
                if len(seen) >= 2:
                    break
            await asyncio.sleep(.2)
        if args.signals:
            await delivery_phases(owner, args, result, emit)
        if args.three_fields_signals:
            signals = Path(args.three_fields_signals).resolve()
            if not signals.is_relative_to(root / "dist") or signals.exists():
                raise ValueError("Use a new plugin-owned marker directory")
            signals.mkdir()
        if args.signals:
            queries = []
        elif args.three_fields_signals or args.three_fields_current:
            queries = [("我打开的是啥页面？", "read_screen", ["page", "quest", "visible_text"])] * 3
        elif args.current_item:
            queries = [("完整报出这件遗器主词条和四项副词条", "read_screen", ["page", "equipment"])] * 2
        else:
            queries = [("我打开的是啥页面？", "read_screen", ["page"]),
                       ("这个页面是干嘛的？页面上写了什么？", "explain_page", ["page", "visible_text"])]
        for index, (query, intent, fields) in enumerate(queries):
            if args.three_fields_signals and index == 1:
                emit("awaiting_camera_zoom", marker=str(signals / "zoom"))
                deadline = time.monotonic() + 240
                while not (signals / "zoom").exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Camera marker not received")
                    await asyncio.sleep(.25)
            if (args.three_fields_signals or args.three_fields_current) and index == 2:
                await asyncio.sleep(8)
            start = time.monotonic()
            packet = await owner.hsr_companion_prepare(query=query, intent=intent, screen_fields=fields)
            visual = packet.get("current_visual_evidence") or {}
            row = {"intent": intent, "seconds": round(time.monotonic() - start, 3),
                "page": visual.get("page_type"), "plan": packet.get("query_plan"),
                "answer": packet.get("query_answer"), "timing": packet.get("query_timing"),
                "receipt": packet.get("delivery_receipt"), "equipment": packet.get("equipment_answer"),
                "row_count": len((visual.get("screen_facts") or {}).get("visible_text") or []),
                "controls": public_control_trace(owner)}
            if args.three_fields_signals or args.three_fields_current:
                row.update(phase=(["resident", "camera_zoom", "continued"] if args.three_fields_signals
                                  else ["resident", "continued_1", "continued_2"])[index],
                    revocations=getattr(owner, "_revocation_trace", []),
                    audit=(values.get("tool_audit") or [{}])[-1])
            result["formal_results"].append(row)
            emit("formal_entry", **row)
            pending = getattr(owner, "_pending_answer", None)
            if args.pending_protocol and pending is not None:
                expires, generation = pending.expires_at, owner._answer_query_generation
                simulated_events.append({"type": "user_message", "lanlan": "hsr_isolated_test",
                    "content": "好", "_ts": time.time()})
                reply = await owner.hsr_companion_prepare(query="好", pending_reply={"request_id": pending.request_id,
                    "relation": "confirm", "player_message": "好"})
                event = {"phase": row.get("phase", str(index)), "accepted": reply["pending_reply"]["accepted"],
                    "original_deadline_unchanged": pending.expires_at == expires,
                    "generation_unchanged": owner._answer_query_generation == generation,
                    "simulated_user_event": True, "request_id": pending.request_id}
                result["pending_protocol"].append(event)
                emit("pending_confirmation", **event)
            if args.current_item:
                await asyncio.sleep(8)
        if args.pending_protocol:
            pending_task = getattr(owner, "_followup_task", None)
            if pending_task is not None and not pending_task.done():
                # Original production grace/deadline, no artificial renewal.
                await pending_task
            result["followup_status"] = getattr(owner, "_followup_status", {})
            result["captured_followups"] = [s.get("metadata") for s in local_submissions
                if s.get("metadata", {}).get("kind") == "query_followup"]
        result["isolated_source_host_submissions"] = 0  # fixture push_message only appends to local_submissions
        result["captured_targeted_submissions"] = sum(bool(s.get("target_lanlan")) for s in local_submissions)
    except Exception as exc:
        result["failure"] = {"type": type(exc).__name__, "error": str(exc)[:180]}
        emit("failure", error_type=type(exc).__name__)
    finally:
        if owner is not None:
            from plugin.plugins.hsr_companion.runtime.followup import cancel_followup
            cancel_followup(owner, "test_cleanup")
            owner._observer_stop_requested = True
            task = getattr(owner, "_observer_task", None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await live_refresh.drain_analysis_worker(owner)
        if adapter is not None:
            adapter.close()
        await asyncio.to_thread(stop_owned, process)
        if pause_attempted and enabled:
            try:
                await asyncio.to_thread(ui_action, args.host, "start_auto_observation")
                restored = await asyncio.to_thread(ui_context, args.host)
                result["restored"] = (restored.get("observer") or {}).get("enabled") is True
                emit("installed_observer_restored", success=result["restored"])
            except Exception as exc:
                result["restore_error"] = type(exc).__name__
                emit("restore_failed", error_type=type(exc).__name__)
        else:
            result["restored"] = not pause_attempted
        result["installed_changed"] = hashes(installed) != installed_before
        result["helper_pointer_changed"] = hashlib.sha256(pointer.read_bytes()).hexdigest() != pointer_before
        result["helper_executable_changed"] = hashlib.sha256(executable.read_bytes()).hexdigest() != executable_before
        result["source_hashes_unchanged"] = source_before == {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}
        collect = getattr(args, "collect_diagnostic_report", None)
        if callable(collect):
            collect(result)  # Includes precondition failures and drained worker exits.
        with report.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:48916", choices=["http://127.0.0.1:48916"])
    parser.add_argument("--report", required=True)
    parser.add_argument("--port", type=int, default=8439, choices=range(1024, 65536))
    parser.add_argument("--signals", help="New dist directory for first_relic and swapped_relic markers")
    parser.add_argument("--current-item", action="store_true", help="Two resident queries on the current relic; no human transitions")
    parser.add_argument("--three-fields-signals", help="Replay real page+quest+visible_text call, then one human camera zoom")
    parser.add_argument("--three-fields-current", action="store_true", help="Same three-field call without another human transition")
    parser.add_argument("--pending-protocol", action="store_true", help="Local SDK user-event fixture on real live OCR; no real cat messages/model")
    asyncio.run(verify(parser.parse_args()))
