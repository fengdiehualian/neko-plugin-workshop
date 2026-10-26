"""One resident OCR + candidate helper; pauses/restores only the installed HSR observer.

No installation, game input, saved images, real-host test messages or core edits.
This script is diagnostic: all production results and review decisions remain unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import logging
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from .field_pipeline_probe import FieldPipelineProbe, summary


def ui_context(base):
    with urllib.request.urlopen(base + "/plugin/hsr_companion/hosted-ui/context?kind=panel&id=demo", timeout=20) as response:
        return json.load(response).get("state") or {}


def ui_action(base, action):
    if action not in {"stop_auto_observation", "start_auto_observation"}:
        raise ValueError("Only HSR observer pause/restore is allowed")
    request = urllib.request.Request(base + "/plugin/hsr_companion/hosted-ui/action/" + action,
        data=json.dumps({"args": {}, "kind": "panel", "surface_id": "demo"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def stop_owned(process):
    import psutil
    if process is None:
        return
    try:
        owned = psutil.Process(process.pid)
        tree = owned.children(recursive=True) + [owned]
    except psutil.NoSuchProcess:
        tree = []
    for child in tree:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(tree, timeout=3)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    process.wait(timeout=5)


async def audit(args, *, activity_bus=None, activity_target=""):
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
    report, executable = Path(args.report).resolve(), Path(args.executable).resolve(strict=True)
    if not report.is_relative_to(root / "dist") or report.exists() or not executable.is_relative_to(root / "dist"):
        raise ValueError("Use a new plugin dist report and a plugin-owned dist executable")
    if args.port == 8219 or not 1024 <= args.port <= 65535 or args.host_url != "http://127.0.0.1:48916":
        raise ValueError("Only an isolated helper port and the known local HSR host are permitted")
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", args.port))
    source_names = ("runtime/build_info.py", "runtime/live_refresh.py", "runtime/query_reuse.py",
        "runtime/observation_store.py", "runtime/delivery_scope.py", "runtime/screen_facts.py", "runtime/equipment.py",
        "adapters/local_vision.py", "adapters/relic_values.py", "adapters/text_verification.py",
        "detectors/relic_geometry.py", "detectors/relic_labels.py", "scripts/field_pipeline_probe.py", "scripts/numeric_review_ablation.py",
        "scripts/label_contract_acceptance.py",
        "scripts/audit_field_handoff_isolated.py", "runtime/guidance.py", "runtime/service_contract.py",
        "runtime/followup.py", "runtime/fact_lifecycle.py", "runtime/chat_activity.py", "__init__.py", "resources/guidance.cn-4.6.json")
    source_hashes = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}
    installed_root = Path(os.environ["LOCALAPPDATA"]) / "N.E.K.O/.neko-plugin-installations/plugins/hsr_companion"
    def installed_hashes():
        return {p.relative_to(installed_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in installed_root.rglob("*") if p.is_file() and p.suffix != ".pyc" and "__pycache__" not in p.parts}
    installed_before = installed_hashes()
    events, transfers = [], []
    owner = adapter = probe = process = None
    original_enabled, pause_attempted = None, False
    environment = {"build_revision": BUILD_REVISION, "helper_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "helper_path": str(executable), "helper_port": args.port, "host_store_isolated": True,
        "installed_plugin_changed": False, "images_stored": False, "test_messages_to_host": False,
        "existing_helper_not_replaced": True, "producer_activity_probe": "_vision_schedule_role",
        "real_sdk_activity_enabled": activity_bus is not None,
        "activity_probe_scope": "public SDK on temporary context; not installed child process" if activity_bus is not None else "none"}
    if args.expected_build and BUILD_REVISION != args.expected_build:
        raise ValueError("Source build differs from frozen candidate")
    if args.expected_helper_sha256 and environment["helper_sha256"] != args.expected_helper_sha256:
        raise ValueError("Helper executable differs from frozen candidate")
    if args.label_contract_batch and (args.phases != 2 or args.samples != 3 or args.no_wait
                                     or args.initial_page != "exploration_hud"):
        raise ValueError("Frozen label batch requires exploration then two items, three calls each")

    def emit(kind, **data):
        event = {"kind": kind, "at": time.time(), **data}
        events.append(event)
        output = event
        if getattr(args, "compact_output", False) and kind in {"formal_tool", "resident_baseline", "resident_warm_completed"}:
            card = event.get("card") or {}
            output = {"kind": kind, "phase": event.get("phase"), "sample": event.get("sample"),
                "seconds": event.get("seconds"), "producer_active_before_query": event.get("producer_active_before_query"),
                "page": (event.get("observation") or {}).get("page"),
                "answer_state": (event.get("answer") or {}).get("state"),
                "name": card.get("name"), "slot": card.get("slot"), "main": card.get("main_stat"),
                "subs": card.get("sub_stats"), "complete": card.get("complete"),
                "followup_pending": (event.get("followup") or {}).get("pending")}
        print(json.dumps(output, ensure_ascii=True), flush=True)

    try:
        original = await asyncio.to_thread(ui_context, args.host_url)
        original_enabled = (original.get("observer") or {}).get("enabled")
        if not isinstance(original_enabled, bool):
            raise RuntimeError("Cannot determine original installed observer state")
        environment["original_observer"] = {k: (original.get("observer") or {}).get(k)
                                             for k in ("enabled", "state", "proactive_enabled")}
        if original_enabled:
            pause_attempted = True  # Restore even if an HTTP reply is lost after the action executed.
            await asyncio.to_thread(ui_action, args.host_url, "stop_auto_observation")
        paused = await asyncio.to_thread(ui_context, args.host_url)
        if (paused.get("observer") or {}).get("enabled") is not False:
            raise RuntimeError("Installed observer is not confirmed paused; do not start a second OCR")
        emit("old_observer_paused", originally_enabled=original_enabled)
        checked = await asyncio.to_thread(subprocess.run, [str(executable), "--self-test"],
            capture_output=True, text=True, timeout=45, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        environment["helper_self_test"] = json.loads(checked.stdout)
        process = subprocess.Popen([str(executable), "--port", str(args.port), "--parent-pid", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        environment["owned_helper_launcher_pid"] = process.pid

        class Client(VisionAssistantClient):
            def fetch_frame(self, since_frame=0):
                started = time.monotonic()
                frame = super().fetch_frame(since_frame)
                transfers.append({"at": time.time(), "seconds": round(time.monotonic() - started, 4),
                    "seq": frame.frame_seq if frame else None,
                    "age_seconds": round(time.time() - frame.timestamp, 4) if frame else None})
                return frame
        client = Client(f"http://127.0.0.1:{args.port}")
        deadline = time.monotonic() + 30
        health = {}
        while time.monotonic() < deadline and process.poll() is None:
            health = await asyncio.to_thread(client.health)
            if health.get("connected") and health.get("game", {}).get("running"):
                break
            await asyncio.sleep(.25)
        else:
            raise RuntimeError("Candidate helper/game did not become ready")
        if health.get("service_version") != "0.9.3":
            raise RuntimeError("Unexpected helper version; do not mix test pairs")
        environment["helper_pair_verified"] = bool(args.expected_build and args.expected_helper_sha256)
        environment["helper_health"] = health
        emit("candidate_helper_ready", version=health.get("service_version"), game=health.get("game"),
             capture=health.get("capture"))
        registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
        catalog = ExternalKnowledgeCatalog(registry=registry)
        avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
            root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
        adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
        owner, values, sent = plugin_fixture(root / ".isolated-field-audit-unused")
        owner._vision, owner._catalog = adapter, catalog
        owner._target_character = lambda: activity_target if activity_bus is not None else ""
        isolated_sender = owner.push_message  # Fixture output only; never bind a host sender.
        if activity_bus is not None:
            if not activity_target:
                raise ValueError("A real activity probe requires an exact target")
            async def activity_get(**kwargs):
                started = time.monotonic()
                try:
                    value = activity_bus.memory.get(**kwargs)
                    value = await value if inspect.isawaitable(value) else value
                    emit("public_activity_read", seconds=round(time.monotonic() - started, 4),
                         outcome="returned", content_retained=False)
                    return value
                except Exception as exc:
                    emit("public_activity_read", seconds=round(time.monotonic() - started, 4),
                         outcome="failed", error_type=type(exc).__name__, content_retained=False)
                    raise
            owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=activity_get)),
                                        _current_lanlan=activity_target)
        owner.logger = logging.getLogger("hsr-isolated-field-audit")
        values["observer_settings"] = {"enabled": True, "proactive_enabled": False, "interval_seconds": 2}
        configure_loop(owner, client)
        await asyncio.to_thread(adapter._get_backend)
        environment["backend"] = {"class": type(adapter._backend).__name__,
                                  "model": getattr(adapter._backend, "model", "")}
        probe = FieldPipelineProbe(owner, keep_failed_numeric=True)
        probe.__enter__()
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            raw = getattr(owner, "_committed_observation", None) or {}
            scope_ready = any(f.get("scope") == args.initial_scope for f in raw.get("screen_facts", {}).get("facts", [])) if args.initial_scope else False
            if scope_ready or not args.initial_scope and raw.get("page", {}).get("page_type") == args.initial_page:
                break
            await asyncio.sleep(.25)
        else:
            raise RuntimeError("No initial-page commit within fixed setup budget")
        emit("resident_baseline", observation=summary(raw))
        resident_seconds = getattr(args, "resident_seconds", 0)
        if resident_seconds:
            emit("resident_warm_started", seconds=resident_seconds, observer_restarted=False)
            await asyncio.sleep(resident_seconds)
            emit("resident_warm_completed", observation=summary(getattr(owner, "_committed_observation", None) or {}))
        if not args.no_wait:
            emit("awaiting_relic", resident_observer_keeps_running=True)
            if (await asyncio.to_thread(input)).strip() == "quit":
                return
        for phase in range(args.phases):
            emit("equipment_phase_started", phase=phase + 1, observer_restarted=False)
            for index in range(args.samples):
                installed = await asyncio.to_thread(ui_context, args.host_url)
                if (installed.get("observer") or {}).get("enabled") is not False:
                    raise RuntimeError("Installed observer resumed during isolated test")
                # Naturally running resident worker; no restart or pre-reading.
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if getattr(owner, "_vision_schedule_role", "idle") == "producer":
                        break
                    await asyncio.sleep(.1)
                active = getattr(owner, "_vision_schedule_role", "idle") == "producer"
                if activity_bus is not None:
                    from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
                    read_started = time.monotonic()
                    state, timestamp = await latest_user_activity(owner.bus, target=activity_target, limit=8)
                    emit("busy_producer_activity_guard", state=state, has_recent_user=timestamp is not None,
                         seconds=round(time.monotonic() - read_started, 4),
                         producer_active=getattr(owner, "_vision_schedule_role", "idle") == "producer")
                    active = getattr(owner, "_vision_schedule_role", "idle") == "producer"
                probe.phase = f"item_{phase + 1}_query_{index + 1}"
                started = time.monotonic()
                result = await owner.hsr_companion_prepare(query=args.query)
                visual = result.get("current_visual_evidence") or {}
                card = equipment_card(visual)
                probe.emit("formal_tool_return", visual=summary(visual), card=card,
                    answer=result.get("query_answer"), timing=result.get("query_timing"), delivery=result.get("delivery_receipt"))
                emit("formal_tool", phase=phase + 1, sample=index + 1, seconds=round(time.monotonic() - started, 3),
                    producer_active_before_query=active, card=card, answer=result.get("query_answer"),
                    timing=result.get("query_timing"), assessment=result.get("guidance_answer", {}).get("assessment"),
                    followup=result.get("followup"),
                    identities=[{"name": c.get("name"), "scope": c.get("identity_scope")} for c in visual.get("characters", [])])
                if index + 1 < args.samples:
                    await asyncio.sleep(4)
            if phase + 1 < args.phases:
                probe.phase = "awaiting_item_switch"
                emit("awaiting_next_relic", next_phase=phase + 2, resident_observer_keeps_running=True)
                if (await asyncio.to_thread(input)).strip() == "quit":
                    return
        assert owner.push_message is isolated_sender, "Diagnostic sender must remain isolated"
        pending = getattr(owner, "_followup_task", None)
        if pending is not None and not pending.done():
            await asyncio.wait_for(asyncio.shield(pending), timeout=40)
        emit("isolated_followup_result", status=getattr(owner, "_followup_status", {}),
             captured_count=len(getattr(owner, "_answer_deliveries", [])),
             deliveries=getattr(owner, "_answer_deliveries", []),
             followup_messages=[{"parts": message.get("parts"), "metadata": message.get("metadata")}
                                for message in sent
                                if (message.get("metadata") or {}).get("kind") == "query_followup"],
             submitted_to_real_host=False)
    except Exception as exc:
        emit("audit_error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if owner is not None:
            owner._observer_stop_requested = True
            task = getattr(owner, "_observer_task", None)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await live_refresh.drain_analysis_worker(owner)
        if probe is not None and adapter is not None:
            from .numeric_review_ablation import compare
            for payload, proof in probe.numeric_failures:
                try:
                    result = await asyncio.to_thread(compare, adapter, payload, proof)
                    probe.emit("numeric_same_image_ablation", **result)
                    emit("numeric_ablation_done", expected=proof["text"], input_sha256=result["input_sha256"])
                except Exception as exc:
                    probe.emit("numeric_ablation_error", error=f"{type(exc).__name__}: {exc}")
            probe.numeric_failures.clear()
        if probe is not None:
            probe.__exit__(None, None, None)
        if adapter is not None:
            adapter.close()
        await asyncio.to_thread(stop_owned, process)
        if owner is not None:
            environment["binary_frame_transport_negotiated"] = bool(getattr(owner._assistant_client, "_binary_frames", False))
        if pause_attempted and original_enabled:
            try:
                current = await asyncio.to_thread(ui_context, args.host_url)
                if (current.get("observer") or {}).get("enabled") is False:
                    await asyncio.to_thread(ui_action, args.host_url, "start_auto_observation")
                restored = await asyncio.to_thread(ui_context, args.host_url)
                environment["original_observer_restored"] = (restored.get("observer") or {}).get("enabled") is True
                emit("old_observer_restored", success=environment["original_observer_restored"])
            except Exception as exc:
                environment["restore_error"] = f"{type(exc).__name__}: {exc}"
                emit("old_observer_restore_failed", error=environment["restore_error"])
        source_hashes_end = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}
        environment["source_hashes_unchanged"] = source_hashes_end == source_hashes
        environment["installed_plugin_changed"] = installed_hashes() != installed_before
        acceptance = None
        if args.label_contract_batch:
            # Truth becomes accessible only after recognition and all queries end.
            from .label_contract_acceptance import score_batch
            acceptance = score_batch(events, environment, trace_errors=probe.errors if probe else 1,
                                     trace_dropped=probe.dropped if probe else 0)
            emit("batch_acceptance", **acceptance)
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as output:
            json.dump({"environment": environment, "source_hashes": source_hashes, "events": events,
                "source_hashes_end": source_hashes_end, "acceptance": acceptance,
                "transfers": transfers, "trace": probe.events if probe else [],
                "trace_dropped": probe.dropped if probe else 0, "trace_errors": probe.errors if probe else 0},
                output, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--port", type=int, default=8222)
    parser.add_argument("--host-url", default="http://127.0.0.1:48916")
    parser.add_argument("--initial-page", choices=("exploration_hud", "relic_detail"), default="exploration_hud")
    parser.add_argument("--initial-scope", choices=("attributes",))
    parser.add_argument("--query", default="这件遗器的主副词条是什么？")
    parser.add_argument("--samples", type=int, choices=(1, 2, 3, 4), default=3)
    parser.add_argument("--phases", type=int, choices=(1, 2), default=1)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--expected-build")
    parser.add_argument("--expected-helper-sha256")
    parser.add_argument("--label-contract-batch", action="store_true")
    parser.add_argument("--resident-seconds", type=int, choices=(0, 45, 90), default=0)
    parser.add_argument("--compact-output", action="store_true")
    asyncio.run(audit(parser.parse_args()))


if __name__ == "__main__":
    main()
