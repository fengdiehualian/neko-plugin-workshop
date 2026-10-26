"""Posthoc score must fail partials, stale items and changed test binaries."""
import copy
import pytest

from plugin.plugins.hsr_companion.scripts.label_contract_acceptance import TRUTH, score_batch


def events():
    result = []
    for phase, (name, slot, main, subs) in enumerate(TRUTH, 1):
        for sample in range(1, 4):
            result.append({"kind": "formal_tool", "phase": phase, "sample": sample, "seconds": 1,
                "answer": {"state": "ready"}, "timing": {"refresh_wait_seconds": 1},
                "card": {"name": name, "slot": slot, "level": "+15", "state": "confirmed", "complete": True,
                    "main_stat": dict(zip(("label", "value"), main)),
                    "sub_stats": [dict(zip(("label", "value"), s)) for s in subs]}})
    return result


ENV = {"source_hashes_unchanged": True, "helper_pair_verified": True}


def test_exact_fixed_batch_passes_only_posthoc():
    before = events()
    result = score_batch(before, ENV)
    assert result["passed"] and result["passed_calls"] == 6
    assert before == events()


@pytest.mark.parametrize("failure", ["missing", "wrong", "stale", "partial", "timeout", "duplicate", "code_changed", "trace_error"])
def test_fixed_batch_failures_are_not_hidden_by_a_later_success(failure):
    calls, env = events(), copy.deepcopy(ENV)
    if failure == "missing":
        calls[0]["card"]["sub_stats"].pop()
    elif failure == "wrong":
        calls[0]["card"]["main_stat"]["value"] = "250"
    elif failure == "stale":
        calls[3]["card"] = calls[0]["card"]
    elif failure == "partial":
        calls[0]["answer"]["state"] = "partial"
    elif failure == "timeout":
        calls[0]["timing"]["refresh_wait_seconds"] = 9
    elif failure == "duplicate":
        calls[1] = copy.deepcopy(calls[0])
    elif failure == "code_changed":
        env["source_hashes_unchanged"] = False
    assert not score_batch(calls, env, trace_errors=int(failure == "trace_error"))["passed"]
