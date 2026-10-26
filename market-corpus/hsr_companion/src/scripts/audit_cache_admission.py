"""Repeat the failing cache fixture with transparent candidate tracing.

No host, game capture, installed files or model calls. This is diagnosis, not
recognition acceptance. The unmodified candidate selector supplies the result.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import tempfile
import time
from pathlib import Path

from ..runtime import fact_lifecycle
from ..runtime.delivery_scope import scoped_guard
from ..runtime.query_coordinator import answer_status
from ..runtime.query_plan import build_query_plan
from ..runtime.review_policy import checked_receipt
from ..tests.test_chain_audit_20261017 import audit_owner, commit_raw, raw_page
from ..tests.test_current_observation import ROLE
from ..tests.test_delivery_fix_20261012 import CONTROL
from ..tests.test_review_policy_20261017 import QUERY, resident_owner, select


async def audit(iterations, receipt_iterations=500):
    original = fact_lifecycle.candidate_evidence
    events = []

    def traced(raw):
        before = time.time()
        value = original(raw)
        after = time.time()
        events.append({"serial": raw.get("observation_serial"),
            "captured": fact_lifecycle.timestamp(raw.get("observed_at")),
            "before": before, "after": after,
            "raw_characters": [a.get("name") for a in raw.get("characters", [])],
            "stable_ids": (raw.get("runtime") or {}).get("stable_character_ids"),
            "fresh": value.get("fresh"), "status": value.get("status"),
            "characters": [a.get("name") for a in value.get("characters", [])]})
        return value

    failures = []
    fact_lifecycle.candidate_evidence = traced
    try:
        with tempfile.TemporaryDirectory(prefix="cache-audit-", dir=Path(__file__).resolve().parents[1]) as folder:
            for index in range(iterations):
                start = len(events)
                owner, _, _ = audit_owner(Path(folder))
                raw = raw_page("team_setup")
                raw["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
                raw["screen_facts"]["facts"] = []
                await commit_raw(owner, raw)
                await commit_raw(owner, raw)
                cached = copy.deepcopy(owner._reusable_observations.get("full", {}))
                evidence = cached.get("evidence", {})
                if not evidence.get("characters"):
                    plan = build_query_plan("画面上是谁")
                    selected = scoped_guard(owner, evidence, plan, [cached.get("guard")], proof_source=cached)
                    failures.append({"iteration": index, "events": events[start:],
                        "committed_serial": owner._observation_serial,
                        "cached_serial": (evidence.get("screen_facts") or {}).get("observation_serial"),
                        "counts": owner._runtime._identity_counts,
                        "runtime": owner._runtime.snapshot(),
                        "scoped_guard_selected": selected is not None,
                        "answer_status": answer_status(plan, selected[0] if selected else evidence, owner._observer_status)})
    finally:
        fact_lifecycle.candidate_evidence = original
    receipt_failures = []
    with tempfile.TemporaryDirectory(prefix="receipt-audit-", dir=Path(__file__).resolve().parents[1]) as folder:
        for index in range(receipt_iterations):
            owner, _, _, _ = await resident_owner(Path(folder))
            value = await select(owner, "full")
            if value is None:
                receipt_failures.append({"iteration": index, "stage": "selection", "trace": owner._active_refresh_trace})
                continue
            if not checked_receipt(owner, value, plan=build_query_plan(QUERY)):
                receipt_failures.append({"iteration": index, "stage": "receipt_consumption",
                    "validated_at": value["reuse"]["validated_at"], "now": time.time(),
                    "age_seconds": time.time() - fact_lifecycle.timestamp(value["reuse"]["validated_at"]),
                    "answer_state": answer_status(build_query_plan(QUERY), value, owner._observer_status)})
    return {"iterations": iterations, "failure_count": len(failures), "failures": failures,
            "candidate_calls": len(events),
            "nonfresh_calls": [e for e in events if not e["fresh"]],
            "receipt_iterations": receipt_iterations, "receipt_failure_count": len(receipt_failures),
            "receipt_failures": receipt_failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--receipt-iterations", type=int, default=500)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(audit(args.iterations, args.receipt_iterations))
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"failures", "nonfresh_calls", "receipt_failures"}}))


if __name__ == "__main__":
    main()
