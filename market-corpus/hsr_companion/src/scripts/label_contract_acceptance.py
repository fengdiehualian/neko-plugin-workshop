"""POSTHOC scoring only. Never import truth into recognition or selection."""
TRUTH = (
    ("救世主的拓荒长靴", "脚部", ("速度", "25"),
     (("攻击力", "35"), ("防御力", "33"), ("暴击率", "8.7%"), ("暴击伤害", "5.8%"))),
    ("启航地的搁浅列车", "位面球", ("火属性伤害提高", "38.8%"),
     (("攻击力", "35"), ("防御力", "4.8%"), ("效果命中", "3.8%"), ("防御力", "69"))),
)


def score_batch(events, environment, *, trace_errors=0, trace_dropped=0):
    results = []
    calls = [e for e in events if e["kind"] == "formal_tool"]
    for call in calls:
        name, slot, main, subs = TRUTH[call["phase"] - 1]
        card = call["card"]
        row = lambda v: (v.get("label"), v.get("value"))
        conditions = {
            "ready": call.get("answer", {}).get("state") == "ready",
            "complete": card.get("complete") is True and card.get("state") == "confirmed",
            "object": card.get("name") == name and card.get("slot") == slot and card.get("level") == "+15",
            "main_exact": row(card.get("main_stat") or {}) == main,
            "four_subs_exact": sorted(map(row, card.get("sub_stats") or [])) == sorted(subs),
            "refresh_budget": 0 <= call.get("timing", {}).get("refresh_wait_seconds", 999) <= 8.1,
        }
        results.append({"phase": call["phase"], "sample": call["sample"], "seconds": call["seconds"],
                        "passed": all(conditions.values()), "conditions": conditions})
    environment_ok = (environment.get("source_hashes_unchanged") is True
                      and environment.get("helper_pair_verified") is True
                      and not trace_errors and not trace_dropped
                      and not any(e["kind"] == "audit_error" for e in events))
    unique_calls = {(r["phase"], r["sample"]) for r in results}
    return {"scope": "source_formal_tool_not_host_speech", "expected_calls": 6,
            "passed_calls": sum(r["passed"] for r in results), "calls": results,
            "environment_ok": environment_ok,
            "passed": environment_ok and len(results) == 6 and unique_calls == {(p, s) for p in (1, 2) for s in (1, 2, 3)}
                      and all(r["passed"] for r in results)}
