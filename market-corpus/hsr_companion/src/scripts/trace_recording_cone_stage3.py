"""One prepared cone page, real OCR/formal entry, isolated store and speech.

Reuse the pause/restore harness and the unchanged installed helper executable.
No screenshots, host-cat messages, external network, archive writes or install.
This is one prepared-page sample, NOT proof of all cross-page transitions.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import socket
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import verify_page_activity_candidate as harness
from .field_pipeline_probe import FieldPipelineProbe, summary
from .verify_roundtrip_c_acceptance import loopback_only


async def cone_phase(owner, args, result, emit):
    from plugin.sdk.plugin import NekoPluginBase
    from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION

    submissions = []
    owner.ctx = SimpleNamespace(bus=NekoPluginBase(SimpleNamespace(plugin_id="isolated-stage3-cone", metadata={},
        bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: [])))).bus)
    owner._target_character = lambda: "hsr_isolated_test"
    owner.push_message = lambda **kw: submissions.append(kw) or {"submitted": True}
    result.update(build_revision=BUILD_REVISION, test_scope="one_prepared_current_cone_real_evidence_chain",
                  host_cat_submissions=0, archive_writes=0, whole_route_retest=False,
                  prequery=summary(getattr(owner, "_committed_observation", None)))
    if result["prequery"].get("page") != "character_detail":
        result["sample_status"] = "setup_not_current_cone_page_not_a_product_failure"
        emit("cone_precondition_failed", observed_page=result["prequery"].get("page"), query_started=False)
        return
    if args.resident_seconds:
        emit("resident_interval", seconds=args.resident_seconds,
             hypothesis="original_failed_cache_was_about_56_seconds_old_not_a_fresh_cache_hit")
        until = time.monotonic() + args.resident_seconds
        while time.monotonic() < until:
            await asyncio.sleep(min(1, until - time.monotonic()))
        result["resident_prequery"] = summary(getattr(owner, "_committed_observation", None))
    started = time.monotonic()
    packet = await owner.hsr_companion_prepare(query="这把光锥给真珠用怎么样，有什么要注意的？",
        intent="assess_cone", consultation={"scope": "current_cone", "subject_state": "specified", "character": "真珠"})
    result["formal_results"].append(packet)
    result["direct_seconds"] = round(time.monotonic() - started, 3)
    emit("cone_direct", seconds=result["direct_seconds"], answer=packet.get("query_answer"),
         completion=(packet.get("guidance_answer") or {}).get("completion"),
         observation=summary(packet.get("current_visual_evidence")))
    if (packet.get("guidance_answer") or {}).get("completion", {}).get("evaluation_complete"):
        # Actual admitted live packet -> actual receiving formatter. This is
        # a projection dry run, NOT a naturally triggered supplement or speech.
        from plugin.plugins.hsr_companion.runtime import followup
        from plugin.plugins.hsr_companion.runtime.answer_message import render_followup
        from plugin.plugins.hsr_companion.runtime.guidance import plan_guidance
        from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
        query = "这把光锥给真珠用怎么样，有什么要注意的？"
        base = build_query_plan(query, "assess_cone")
        guidance_plan = plan_guidance(query, base, catalog=owner._catalog, book=owner._guidance_book,
            consultation={"scope": "current_cone", "subject_state": "specified", "character": "真珠"})
        request = followup.AnswerRequest("projection-only", query, "isolated", time.time(), time.monotonic(),
            time.monotonic() + 1, "character_detail", False, False, True, False,
            plan=base, guidance_plan=guidance_plan, guidance_profile={})
        data = followup.reply_data(packet["current_visual_evidence"], request, catalog=owner._catalog)
        data["guidance_answer"] = packet["guidance_answer"]
        text, receipt = await asyncio.to_thread(render_followup, data, plan=base, ready=True)
        result["live_packet_projection_dry_run"] = {"text": text, "receipt": receipt, "submitted": False}
    pending = getattr(owner, "_followup_task", None)
    if pending is not None and not pending.done():
        # Production deadline/grace unchanged. No repeated query until green.
        await pending
    result["total_seconds"] = round(time.monotonic() - started, 3)
    result["followup_status"] = getattr(owner, "_followup_status", {})
    result["captured_followups"] = [item.get("metadata") for item in submissions
        if (item.get("metadata") or {}).get("kind") == "query_followup"]
    result["final_observation"] = summary(getattr(owner, "_committed_observation", None))
    emit("cone_complete", seconds=result["total_seconds"], followup=result["followup_status"])


async def run(args):
    from plugin.plugins.hsr_companion.tests import test_resident_observer as fixtures

    root = Path(__file__).resolve().parents[1]
    names = ("runtime/consultation_contract.py", "runtime/semantic_guidance.py", "runtime/guidance.py",
             "runtime/saved_cone.py", "runtime/service_contract.py", "runtime/live_refresh.py",
             "runtime/query_reuse.py", "runtime/screen_facts.py", "runtime/answer_message.py")
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    probes = []
    original = fixtures.configure_loop
    with ExitStack() as stack:
        original_action = harness.ui_action
        setup_checks = []

        def action(base, name):
            try:
                return original_action(base, name)
            except TimeoutError:
                # A receipt timeout is not operation failure. Reuse the
                # established isolation prerequisite: stopped observer AND
                # disconnected old helper after its native worker drains.
                from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
                context = harness.ui_context(base)
                connected = bool(VisionAssistantClient().health().get("connected"))
                enabled = (context.get("observer") or {}).get("enabled")
                proved = (enabled is False and not connected if name == "stop_auto_observation" else
                          enabled is True and connected if name == "start_auto_observation" else False)
                setup_checks.append({"action": name, "receipt_timeout": True,
                                     "enabled": enabled, "helper_connected": connected, "readback_proved": proved})
                if not proved:
                    raise
                return {"operation_confirmed_by_readback": True}

        def configure(owner, client):
            original(owner, client)
            probe = stack.enter_context(FieldPipelineProbe(owner))
            probe.phase = "current_cone"
            probes.append(probe)

        def collect(result):
            result.update(stage3_source_hashes=hashes, stage3_source_unchanged=hashes == {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names})
            result["environment_receipt_checks"] = setup_checks
            if probes:
                result.update(pipeline_trace=probes[0].events, probe_errors=probes[0].errors,
                              probe_dropped=probes[0].dropped)

        args.collect_diagnostic_report = collect
        args.signals = "one_current_cone_no_transition_markers"
        args.current_item = args.three_fields_signals = args.three_fields_current = args.pending_protocol = False
        stack.enter_context(patch.object(fixtures, "configure_loop", configure))
        stack.enter_context(patch.object(harness, "ui_action", action))
        stack.enter_context(patch.object(harness, "delivery_phases", cone_phase))
        stack.enter_context(patch.object(socket.socket, "connect", loopback_only(socket.socket.connect)))
        await harness.verify(args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--port", type=int, default=8477)
    parser.add_argument("--resident-seconds", type=int, default=0, choices=range(91))
    parser.add_argument("--host", default="http://127.0.0.1:48916", choices=["http://127.0.0.1:48916"])
    asyncio.run(run(parser.parse_args()))
