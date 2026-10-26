"""Offline formal-entry replay; NOT game/OCR/model/chat acceptance.

Runs the real plugin tool and receiving message formatter in one isolated
owner. Only resolve_query is replaced with previously confirmed field shapes.
No active plugin, player store, helper, socket or host message is used.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from plugin.plugins.hsr_companion.runtime.answer_message import render_followup
from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
from plugin.plugins.hsr_companion.runtime.service_contract import clear_discussion
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import received_text, shoe
from plugin.plugins.hsr_companion.tests.test_d1_player_equipment_reply import partial
from plugin.plugins.hsr_companion.tests.test_session_message_handoff import head


async def replay(root):
    owner, values, sent = plugin_fixture(root)
    owner._catalog = cases.catalog.__wrapped__(root)
    owner._guidance_book = cases.book.__wrapped__()
    current = head(serial=1)
    profile = copy.deepcopy(values["player_context"])
    demands, trace = [], []
    owner._observation_epoch = 1
    owner._analysis_game_pid = 7

    def select(value, serial):
        nonlocal current
        current = copy.deepcopy(value)
        current["screen_facts"]["observation_serial"] = serial
        owner._observation_serial = serial
        owner._committed_observation = copy.deepcopy(current)

    async def resolve(plugin, demand, **kwargs):
        demands.append(demand)
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=demand.fields),
                                {"outcome": "offline_replay", "stages": []}, 0, kwargs["generation"])

    async def call(label, **arguments):
        before = len(demands)
        result = await owner.hsr_companion_prepare(**arguments)
        if result.get("is_error"):
            raise AssertionError((label, result.get("error")))
        plan = demands[-1] if len(demands) > before else None
        card = result.get("equipment_answer") or {}
        guidance = result.get("guidance_answer") or {}
        trace.append({"step": label, "intent": arguments.get("intent", "conversation"),
                      "plan": result.get("query_plan"), "item": card.get("name"),
                      "snapshot": card.get("snapshot_id"), "main": card.get("main_stat"),
                      "sub_stats": card.get("sub_stats"),
                      "subjects": [g["name"] for g in guidance.get("guides", [])],
                      "consultation_kind": guidance.get("kind"),
                      "followup_pending": (result.get("followup") or {}).get("pending", False),
                      "source": (result.get("response_contract") or {}).get("source"),
                      "request_id": (result.get("response_contract") or {}).get("request_id")})
        return result, plan

    select(current, 1)
    module = importlib.import_module(type(owner).__module__)
    with patch.object(module, "resolve_query", resolve):
        for text in ("我打开的是啥页面", "这是哪一页", "这个页面是干嘛的", "这是什么页面，写了什么"):
            result, _ = await call("legacy_wording:" + text, query=text)
            expected = {"page", "visible_text"} if "干嘛" in text or "写了什么" in text else {"page"}
            assert set(result["query_plan"]["fields"]) == expected

        first, _ = await call("first_relic", query="这件给真珠合适吗", intent="assess_relic")
        assert first["equipment_answer"]["complete"]
        select(shoe(), 2)
        swapped, demand = await call("changed_item_followup", query="接着瞅瞅它", intent="continue_consultation",
                                    response_detail="full", discussion_character="")
        card = swapped["equipment_answer"]
        assert card["name"] == "卜者的飞腾云履" and card["main_stat"]["value"] == "25"
        assert card["snapshot_id"] != first["equipment_answer"]["snapshot_id"]
        assert swapped["guidance_answer"]["guides"][0]["name"] == "真珠"
        text, projection = render_followup(swapped, plan=demand, ready=True)
        delivered = received_text({"parts": [{"type": "text", "text": text}], "ai_behavior": "respond", "visibility": []})
        assert projection["complete"] and delivered == text
        assert all(r["label"] + r["value"] in delivered for r in [card["main_stat"], *card["sub_stats"]])
        assert "攻击力57" not in delivered
        trace[-1]["formatted_full_rows"] = 5

        advice, _ = await call("advice_without_pixels", query="我有专武，遗器怎么选", intent="equipment_guide")
        assert not advice["query_plan"]["screen"] and not advice["followup"]["pending"]
        focus, _ = await call("knowledge_followup", query="重点呢", intent="build_priority")
        assert not focus["query_plan"]["screen"] and focus["guidance_answer"]["guides"][0]["name"] == "真珠"
        stage, _ = await call("character_to_challenge", query="眼前这个挑战怎么安排", intent="challenge_guide")
        assert stage["guidance_answer"]["kind"] == "challenge"
        assert stage["query_plan"]["fields"] == ["visible_text"]
        assert not stage["guidance_answer"]["assessment"]["subject_ids"]

        # A fresh incomplete item stays incomplete. A bound player number is
        # a statement, not OCR proof, and must not trigger another frame read.
        select(partial(), 3)
        missing, _ = await call("partial_current_item", query="这件给真珠完整看看", intent="assess_relic",
                                response_detail="full")
        assert missing["player_reply_contract"]["state"] == "awaiting_player_value"
        count = len(demands)
        supplemented, _ = await call("player_missing_value", query="76", intent="continue_consultation", player_reply="76")
        assert len(demands) == count
        assert supplemented["player_equipment_statement"]["row"]["value"] == "76"
        assert not supplemented["player_equipment_statement"]["visually_confirmed"]
        assert not supplemented["current_visual_evidence"]["fresh"]

        clear_discussion(owner)
        cleared, _ = await call("cleared_discussion", query="接着看它", intent="continue_consultation")
        assert not cleared["guidance_answer"]["guides"] and not cleared["query_plan"]["screen"]

    assert values["player_context"] == profile and not sent
    return {"build_revision": BUILD_REVISION, "status": "passed", "steps": trace,
            "profile_unchanged": True, "host_messages_sent": 0,
            "scope": "isolated formal entry + real message formatter; confirmed field-shape replay",
            "not_tested": ["LLM intent selection", "new OCR/capture", "live cat speech", "actual desktop reload"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(replay(args.workdir))
    plugin_root = Path(__file__).resolve().parents[1]
    changed = ("__init__.py", "runtime/query_plan.py", "runtime/semantic_guidance.py", "runtime/guidance.py",
               "runtime/diagnostics.py", "runtime/service_contract.py", "runtime/build_info.py",
               "tests/test_semantic_routing.py", "scripts/replay_semantic_entry.py", "DESIGN.md")
    result["source_sha256"] = {name: hashlib.sha256((plugin_root / name).read_bytes()).hexdigest() for name in changed}
    prefix = "payload/plugins/hsr_companion/"
    with zipfile.ZipFile(plugin_root / "dist/hsr_companion-0.9.6-demo-session-handoff-20261019.1.neko-plugin") as baseline:
        unchanged = [n for n in baseline.namelist() if n.startswith(prefix) and not n.endswith("/")
                     and (n[len(prefix):].startswith(("adapters/", "detectors/", "assistant_runtime/"))
                          or n[len(prefix):] in {"runtime/screen_facts.py", "runtime/followup.py", "runtime/equipment.py",
                                                "runtime/observation_store.py", "runtime/query_reuse.py", "runtime/answer_message.py"})]
        assert unchanged and all((plugin_root / n[len(prefix):]).read_bytes() == baseline.read(n) for n in unchanged)
        result["unchanged_capture_ocr_evidence_cache_followup_files"] = len(unchanged)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
