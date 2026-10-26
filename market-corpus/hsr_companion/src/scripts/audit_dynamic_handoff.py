"""Real resident producer + formal prepare, in isolated memory, no game input.

Never installs, launches helpers, persists frames or sends host messages.
Reads existing local resources. Report contains counts/states, not raw OCR.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import logging
import time
from pathlib import Path


async def audit(args):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
    from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
    from plugin.plugins.hsr_companion.data.avatar_pack import AvatarTemplatePack
    from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime
    from plugin.plugins.hsr_companion.runtime import live_refresh
    from plugin.plugins.hsr_companion.runtime.navigation import navigation_answer
    from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp, regions_changed
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_resident_observer import configure_loop

    root = Path(__file__).resolve().parents[1]
    report = Path(args.report).resolve() if args.report else None
    if report and (not report.is_relative_to(root / "dist") or report.exists()):
        raise ValueError("Use a new plugin dist report; never overwrite a frozen run")
    source_names = ("runtime/live_refresh.py", "runtime/query_reuse.py", "runtime/build_info.py",
                    "runtime/screen_facts.py", "adapters/relic_values.py", "adapters/text_verification.py",
                    "detectors/relic_geometry.py", "detectors/layout.py", "detectors/scene_preview.py",
                    "scripts/audit_dynamic_handoff.py")
    source_hashes = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in source_names}
    events = []
    registry = json.loads((root / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    avatars = AvatarTemplatePack(json.loads((root / "resources/avatar_templates.json").read_text(encoding="utf-8")),
        root=catalog.root, runtime=VisionMatchRuntime(root=catalog.root))
    adapter = LocalVisionAdapter(catalog, avatar_pack=avatars)
    transfers = []
    loop_lags = []
    class AuditedClient(VisionAssistantClient):
        def fetch_frame(self, since_frame=0):
            started = time.monotonic()
            frame = super().fetch_frame(since_frame)
            transfers.append({"seconds": round(time.monotonic() - started, 4),
                "age_at_thread_return": round(time.time() - frame.timestamp, 4) if frame else None,
                "seq": frame.frame_seq if frame else None})
            del transfers[:-32]
            return frame
    client = AuditedClient(args.assistant_url)
    health = await asyncio.to_thread(client.health)
    if not health.get("connected") or not health.get("game", {}).get("running"):
        print(json.dumps({"precondition": "game_or_helper_unavailable"}), flush=True)
        adapter.close()
        return
    owner, values, sent = plugin_fixture(root / ".isolated-audit-unused")
    owner._catalog = catalog
    owner._vision = adapter
    owner._target_character = lambda: ""  # Cannot register real host follow-up/events.
    values["observer_settings"] = {"enabled": True, "proactive_enabled": False, "interval_seconds": 2}
    configure_loop(owner, client)
    owner.logger = logging.getLogger("hsr-isolated-dynamic-audit")
    owner._active_refresh_trace = {"started": time.monotonic(), "stages": []}
    producer_checks = []
    verification_checks = []
    original_verify = adapter.verify_text_regions

    def audited_verify(payload, proofs, **kwargs):
        from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
        trace = []
        started = time.monotonic()
        confirmed = original_verify(payload, proofs, **{**kwargs, "trace": trace})
        verification_checks.append({"requested": len(proofs), "confirmed": len(confirmed),
            "seconds": round(time.monotonic() - started, 4),
            "rejected_controls": sum(p.get("control") is True and proof_key(p) not in confirmed for p in proofs),
            "rejected_identities": sum(p.get("identity_literal") is True and proof_key(p) not in confirmed for p in proofs),
            "valid_mismatches": sum(t.get("valid", False) and t.get("expected") != t.get("actual") for t in trace),
            "weak_reads": sum(not t["valid"] for t in trace if "valid" in t),
            **({"proofs": proofs, "trace": trace} if args.detailed_proofs else {})})
        return confirmed

    adapter.verify_text_regions = audited_verify
    original = live_refresh.guarded_analysis

    async def observed(target, payload, **kwargs):
        result = await original(target, payload, **kwargs)
        if result is None:
            producer_checks.append({"discarded": True,
                "trace": (getattr(owner, "_active_refresh_trace", None) or {}).get("stages", [])[-12:]})
        if result is not None:
            actors = result.get("characters") or []
            latest = None if args.no_independent_reread else await asyncio.to_thread(client.fetch_frame, 0)
            changed = None
            reread = None
            literal_count = 0
            if latest is not None and actors:
                before, after = region_stamp(payload, actors), region_stamp(latest.image_bytes, actors)
                changed = sum(regions_changed((a,), (b,)) for a, b in zip(before, after))
                proofs = [{"region": a["region"], "text": a["raw_label"]} for a in actors
                          if a.get("region") and a.get("raw_label") and a.get("identity_scope") != "hud_portrait"]
                literal_count = len(proofs)
                if proofs and not args.no_independent_reread:
                    # Independent diagnosis, after producer completion, not used to authorize its output.
                    verified = await asyncio.to_thread(adapter.verify_text_regions, latest.image_bytes, proofs)
                    reread = len(verified)
            producer_checks.append({"page": result.get("page", {}).get("page_type"), "actor_count": len(actors),
                "reader_calls": result.get("recognition", {}).get("text_reader", {}).get("backend_calls", []),
                "page_controls": len(result.get("recognition", {}).get("scene_regions", [])),
                "fact_kinds": [f.get("kind") for f in result.get("screen_facts", {}).get("facts", [])],
                "identity_guarded": getattr(owner, "_next_frame_identity_guarded", False),
                "changed_actor_regions": changed, "literal_actor_proofs": literal_count,
                "independent_literal_reread_matches": reread,
                "identity_validation": result.get("recognition", {}).get("identity_validation")})
        return result

    # Observe producer decisions without substituting their result or changing guards.
    live_refresh.guarded_analysis = observed
    entry_module = __import__(type(owner).__module__, fromlist=["guarded_analysis"])
    entry_original = entry_module.guarded_analysis
    entry_module.guarded_analysis = observed
    owner._observer_task = asyncio.create_task(owner._observer_loop())
    async def monitor_loop():
        while True:
            started = time.monotonic()
            await asyncio.sleep(.1)
            lag = time.monotonic() - started - .1
            if lag > .15:
                loop_lags.append(round(lag, 4))
                del loop_lags[:-32]
    lag_task = asyncio.create_task(monitor_loop())
    def show(value):
        if args.compact:
            nav = value.get("navigation") or {}
            value["navigation"] = {k: nav.get(k, {}).get("value") for k in ("current_location", "objective", "quest_title")}
            answer = value.get("answer") or {}
            value["answer"] = {k: answer.get(k) for k in ("state", "confirmed_fields", "missing_fields")}
        events.append(value)
        print(json.dumps(value, ensure_ascii=True), flush=True)
    try:
        deadline = time.monotonic() + args.setup_seconds
        resident_commits = set()
        while len(resident_commits) < args.resident_commits and time.monotonic() < deadline:
            serial = getattr(owner, "_last_full_commit_serial", 0)
            actual_page = (getattr(owner, "_committed_observation", None) or {}).get("page", {}).get("page_type")
            if serial and (not args.expected_page or actual_page == args.expected_page):
                resident_commits.add(serial)
            await asyncio.sleep(.2)
        # Reconcile a commit completed during the final polling sleep before
        # deciding setup failed. This changes no timestamp or answer policy.
        serial = getattr(owner, "_last_full_commit_serial", 0)
        actual_page = (getattr(owner, "_committed_observation", None) or {}).get("page", {}).get("page_type")
        if serial and (not args.expected_page or actual_page == args.expected_page):
            resident_commits.add(serial)
        if len(resident_commits) < args.resident_commits:
            print(json.dumps({"precondition": "resident_commit_target_not_reached", "checks": producer_checks[-3:],
                              "resident_commit_count": len(resident_commits), "requested_commits": args.resident_commits,
                              "trace": (getattr(owner, "_active_refresh_trace", None) or {}).get("stages"),
                              "state": owner._observer_status.get("state"),
                              "worker_done": owner._observer_task.done(),
                              "transfers": transfers[-12:], "loop_lag_seconds": loop_lags}), flush=True)
            return
        resident_started = time.monotonic()
        while time.monotonic() - resident_started < args.resident_seconds:
            current = await owner._current_visual_evidence()
            print(json.dumps({"kind": "resident_soak_progress", "seconds": round(time.monotonic()-resident_started, 1),
                "serial": getattr(owner, "_observation_serial", 0), "state": owner._observer_status.get("state"),
                "projection_fresh": current.get("fresh"), "page": current.get("page_type"),
                "fact_count": len(current.get("screen_facts", {}).get("facts", [])),
                "raw_observation_present": bool(getattr(owner, "_committed_observation", None))}), flush=True)
            await asyncio.sleep(min(30, max(0, args.resident_seconds - (time.monotonic()-resident_started))))
        for i in range(args.samples):
            query = args.query
            if i and args.interactive:
                print(json.dumps({"kind": "awaiting_next_round", "next_sample": i + 1,
                                  "producer_stays_running": True}), flush=True)
                query = (await asyncio.to_thread(input)).strip() or query
            if i and args.sample_interval:
                await asyncio.sleep(args.sample_interval)
            worker = getattr(owner, "_analysis_worker", None)
            worker_active = bool(worker and not worker.done()) or getattr(owner, "_vision_schedule_role", "idle") == "producer"
            started = time.monotonic()
            result = await owner.hsr_companion_prepare(query=query)
            v = result.get("current_visual_evidence", {})
            from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
            show({"kind": "isolated_resident_formal_entry_not_host_chat", "sample": i+1,
                "seconds": round(time.monotonic()-started, 3), "page": v.get("page_type"),
                "resident_elapsed_seconds": round(time.monotonic()-resident_started, 3),
                "observed_at": v.get("observed_at"), "last_verified_at": v.get("last_verified_at"),
                "equipment": {k: equipment_card(v).get(k) for k in ("snapshot_id", "complete", "slot", "main_stat", "sub_stats", "exact_text")},
                "resident_commits_before_query": len(resident_commits),
                "worker_active_before_query": worker_active,
                "independent_diagnostic_reread": not args.no_independent_reread,
                "resident_stable_identity_count": len((getattr(owner, "_committed_observation", None) or {}).get("runtime", {}).get("stable_character_ids", [])),
                "names": [c["name"] for c in v.get("characters", [])], "answer": result.get("query_answer"),
                "navigation": navigation_answer(v, requested=True) if any(f in result.get("query_plan", {}).get("fields", [])
                    for f in ("location", "quest")) else {"requested": False},
                "timing": result.get("query_timing"), "delivery": result.get("delivery_receipt"),
                "producer_checks": producer_checks[-3:], "verification_checks": verification_checks[-8:],
                "transfers": transfers[-12:], "loop_lag_seconds": loop_lags})
    finally:
        owner._observer_stop_requested = True
        lag_task.cancel()
        await asyncio.gather(lag_task, return_exceptions=True)
        owner._observer_task.cancel()
        await asyncio.gather(owner._observer_task, return_exceptions=True)
        await live_refresh.drain_analysis_worker(owner)
        live_refresh.guarded_analysis = original
        entry_module.guarded_analysis = entry_original
        adapter.close()
        assert not any(s.get("target_lanlan") for s in sent), "Unexpected host target"
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            with report.open("x", encoding="utf-8") as output:
                json.dump({"source_hashes": source_hashes, "image_stored": False, "host_messages": False,
                           "installed_plugin_changed": False, "results": events, "producer_checks": producer_checks,
                           "verification_checks": verification_checks, "transfers": transfers}, output,
                          ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assistant-url", default="http://127.0.0.1:8219",
        help="Validated loopback endpoint, including an isolated candidate helper; does not launch or replace it.")
    parser.add_argument("--samples", type=int, default=2, choices=range(1, 7))
    parser.add_argument("--sample-interval", type=float, default=0)
    parser.add_argument("--resident-seconds", type=float, default=0,
        help="Real wall-clock resident soak before asking; never rewrites timestamps or suspends the producer.")
    parser.add_argument("--setup-seconds", type=float, default=60,
        help="Explicit first-commit setup budget; failures remain failures, not silent restarts.")
    parser.add_argument("--expected-page", default="",
        help="Optional explicit initial-page precondition, never count a different menu as an exploration soak.")
    parser.add_argument("--interactive", action="store_true",
        help="Keep the SAME observer alive; stdin newline starts each next formal-entry round.")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--query", default="现在是什么页面？都有谁？")
    parser.add_argument("--resident-commits", type=int, default=1, choices=(1, 2, 3),
        help="1 preserves the original cold resident test; 2/3 additionally test settled resident identity.")
    parser.add_argument("--no-independent-reread", action="store_true",
        help="Disable diagnostic-only OCR after producer validation for a less intrusive runtime check.")
    parser.add_argument("--report", default="", help="New JSON report under this plugin's dist; never saves images.")
    parser.add_argument("--detailed-proofs", action="store_true", help="Record per-proof decisions, not only counts.")
    asyncio.run(audit(parser.parse_args()))
