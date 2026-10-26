import copy
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from test_providers import load

engine, fleet, core, server, care, commands = [
    load(n) for n in ("allowance", "fleet", "core", "server", "companion", "chat_commands")
]
FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "replays/allowance-v1.json").read_text())
SCOPE = {"id": "isolated-account", "kind": "credential_slot", "verified": False, "label": "隔离档案"}


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_replay(case):
    assert FIXTURE["isolated"] and not FIXTURE["contains_real_account_data"]
    r = engine.normalize(case["state"], case["profile"], SCOPE, now=case["now"])
    chosen = engine.choose([r], now=case["now"])
    for k, v in case["expect"].items():
        assert chosen[k] == v


def money(now=2000000000, value="5", **extra):
    return {
        "config": {"source": "deepseek", "currency": "CNY", "interval": 300},
        "selected": {"balance": value},
        "updated_at": now,
        **extra,
    }


def test_selection_never_blends_unverified_accounts_or_promotes_a_page_after_api_failure():
    api = engine.normalize(money(error="offline"), "deepseek", SCOPE, now=2000000001)
    page_scope = dict(SCOPE, id="different-browser-login")
    page = engine.normalize(money(value="500"), "deepseek", page_scope, now=2000000001, page=True)
    chosen = engine.choose([api, page], preferred_scope=SCOPE["id"], now=2000000001)
    assert chosen["value"] == "5" and chosen["status"] == "stale" and not chosen["can_alert"]
    assert chosen["alternatives"][0]["decision"] == "separate_account_scope"


def test_comparable_conflict_disables_alerts_but_different_currency_time_or_account_is_not_a_conflict():
    verified = dict(SCOPE, verified=True)
    a = engine.normalize(money(), "deepseek", verified, now=2000000001)
    b = engine.normalize(money(value="8"), "deepseek", verified, now=2000000001)
    chosen = engine.choose([a, b], now=2000000001)
    assert chosen["status"] == "conflict" and not chosen["can_alert"] and chosen["value"] == "5"
    for key, value in [
        ("unit", "USD"),
        ("observed_at", 1999000000),
        ("metric", "usage_total"),
        ("precision", "page_display"),
        ("account_scope", dict(verified, id="another")),
    ]:
        other = dict(b, **{key: value})
        assert engine.choose([a, other], now=2000000001)["status"] == "current"


def test_expiry_does_not_reset_quota_to_zero_and_keeps_other_active_window():
    case = copy.deepcopy(next(c for c in FIXTURE["cases"] if c["name"] == "codex_weakest_active_window"))
    r = engine.normalize(case["state"], "codex", SCOPE, now=2000000000)
    assert engine.choose([r], now=2000604801)["value"] is None
    stale = engine.choose([r], now=2000000700)
    assert stale["status"] == "stale" and stale["percent"] == 25 and not stale["can_alert"]


def test_audit_dedup_restart_privacy_and_new_key_isolation(tmp_path):
    ledger = engine.AllowanceLedger(tmp_path, clock=lambda: 2000000001)
    config = dict(money()["config"], api_key="synthetic-private-key", endpoint="https://example.test/?secret=private")
    first = ledger.observe(money(), "deepseek", config)
    for _ in range(10):
        ledger.select(ledger.observe(money(), "deepseek", config))
    assert len(ledger.data["observations"]) == 1 and len(ledger.data["decisions"]) == 2
    history = ledger.history(first)
    assert "synthetic-private-key" not in json.dumps(history) and "example.test" not in json.dumps(history)
    assert os.stat(ledger.path).st_mode & 0o777 == 0o600
    restarted = engine.AllowanceLedger(tmp_path, clock=lambda: 2000000001)
    assert restarted.scope("deepseek", config) == first["account_scope"]
    other = restarted.observe(money(value="9"), "deepseek", dict(config, api_key="another-private-key"))
    assert len(restarted.history(other)["observations"]) == 1
    assert restarted.history(other)["observations"][0]["value"] == "9"


def test_invalid_observation_preserves_last_valid_only_in_same_context(tmp_path):
    ledger = engine.AllowanceLedger(tmp_path, clock=lambda: 2000000001)
    config = dict(money()["config"], api_key="synthetic-key")
    ledger.observe(money(), "deepseek", config)
    bad = money(value="NaN")
    result = ledger.observe(bad, "deepseek", config)
    assert result["value"] == "5" and result["observed_at"] == 2000000000 and result["status"] == "stale"
    assert not result["can_alert"]
    assert ledger.observe(bad, "deepseek", dict(config, api_key="different-key"))["value"] is None


def test_corruption_is_not_overwritten_and_failed_write_can_retry(tmp_path):
    path = tmp_path / "allowance-ledger.json"
    path.write_text("{broken")
    ledger = engine.AllowanceLedger(tmp_path, clock=lambda: 2000000001)
    assert not ledger.observe(money(), "deepseek", {})["audit_saved"] and path.read_text() == "{broken"
    path.unlink()
    ledger = engine.AllowanceLedger(tmp_path, clock=lambda: 2000000001)
    with patch.object(engine.os, "replace", side_effect=OSError("synthetic disk failure")):
        assert not ledger.observe(money(), "deepseek", {})["audit_saved"]
    assert ledger.observe(money(), "deepseek", {})["audit_saved"]


def test_money_precision_and_exponent_bounds():
    assert engine.number(True) is None and engine.number("NaN") is None
    assert engine.number("1e-999999") is None
    r = engine.normalize(money(value="0.000000000000000001"), "deepseek", SCOPE, now=2000000001)
    assert r["value"] == "0.000000000000000001"


def test_manual_entry_is_not_refreshed_by_polling_and_reaffirmation_records_new_entry(tmp_path):
    m = core.Monitor(tmp_path)
    m.configure({"source": "manual_quota", "quota_remaining": 25, "quota_total": 100})
    original = m.snapshot()["updated_at"]
    m.refresh()
    m.refresh()
    assert m.snapshot()["updated_at"] == original
    time.sleep(0.001)
    m.configure({"quota_remaining": 25})
    assert m.snapshot()["updated_at"] > original


def test_fleet_api_key_never_uses_browser_balance_even_when_api_is_unavailable(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.configure({"profile": "deepseek", "api_key": "synthetic-key"})
    now = time.time()
    f.monitors["deepseek"].state = {
        "balances": [{"balance": "4", "currency": "CNY"}],
        "updated_at": now,
        "error": "offline",
    }
    f.follow_foreground(
        {
            "profile": "deepseek",
            "status": "page",
            "page_wallet": {"currency": "CNY", "normal": "99", "bonus": "1", "balance": "100", "observed_at": now},
        }
    )
    s = f.snapshot()
    assert s["allowance"]["value"] == "4" and s["allowance"]["status"] == "stale"
    assert s["allowance"]["alternatives"][0]["decision"] == "separate_account_scope"
    q = care.quota_snapshot(s)
    assert q["stale"] and care.alert_level(s, q) == 0
    assert "上次记录" in commands.balance_answer(s)


def test_1000_isolated_failure_and_restart_cycles_do_not_invent_balance_or_cross_credentials(tmp_path):
    clock = [2000000001]
    ledger = engine.AllowanceLedger(tmp_path, clock=lambda: clock[0])
    for i in range(1000):
        config = dict(money()["config"], api_key="synthetic-slot-" + str(i % 3))
        raw = money(value=str(i % 100), error="offline" if i % 4 == 0 else None)
        if i % 7 == 0:
            raw["selected"] = None
        chosen = ledger.observe(raw, "deepseek", config)
        assert (chosen["value"] is None) == (raw["selected"] is None)
        assert chosen["can_alert"] == bool(raw["selected"] is not None and not raw["error"])
        if i % 100 == 0:
            ledger = engine.AllowanceLedger(tmp_path, clock=lambda: clock[0])
    assert len(ledger.data["observations"]) <= 1000 and len(ledger.data["decisions"]) <= 1000
    assert "synthetic-slot-" not in ledger.path.read_text()


def test_audit_http_scope_validation_origin_and_read_only_history(tmp_path):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    f = fleet.Fleet(tmp_path)
    f.configure({"profile": "qwen", "quota_remaining": 500, "quota_total": 1000, "quota_unit": "token"})
    httpd, thread = server.start_server(f, port=0)
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        payload = json.load(urlopen(base + "/api/allowance?profile=qwen"))
        assert payload["reading"]["status"] == "manual" and payload["reading"]["percent"] == 50
        assert payload["observations"][0]["value"] == "500"
        raw = json.dumps(payload)
        assert "api_key" not in raw and "salt" not in raw and 'config"' not in raw
        for path, headers, code in [
            ("/api/allowance?profile=bad", {}, 400),
            ("/api/allowance?profile=qwen&profile=codex", {}, 400),
            ("/api/allowance?secret=x", {}, 400),
            ("/api/allowance", {"Origin": "https://example.test"}, 403),
        ]:
            with pytest.raises(HTTPError) as exc:
                urlopen(Request(base + path, headers=headers))
            assert exc.value.code == code
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


def test_same_account_different_quota_windows_are_not_conflicts():
    state = copy.deepcopy(next(c for c in FIXTURE["cases"] if c["name"] == "codex_weakest_active_window")["state"])
    verified = dict(SCOPE, verified=True)
    a = engine.normalize(state, "codex", verified, now=2000000000)
    state["codex_quota"]["windows"] = [state["codex_quota"]["windows"][0]]
    b = engine.normalize(state, "codex", verified, now=2000000000)
    assert engine.choose([a, b], now=2000000000)["status"] == "current"


def test_reported_usage_is_audited_from_the_global_ledger_without_becoming_balance(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.record_usage(
        {
            "provider": "jev",
            "request_id": "isolated-request",
            "model": "test-model",
            "input_tokens": 100,
            "output_tokens": 20,
        }
    )
    f.configure_tracking({"mode": "fixed", "profile": "jev"})
    s = f.snapshot()
    assert s["allowance"]["metric"] == "usage_total" and s["allowance"]["value"] == "120"
    assert not s["allowance"]["can_alert"] and care.quota_snapshot(s) is None
    assert f.allowance_history("jev")["observations"][0]["value"] == "120"


def test_semantically_corrupt_ledger_is_preserved(tmp_path):
    path = tmp_path / "allowance-ledger.json"
    path.write_text(json.dumps({"version": 1, "salt": "a" * 64, "observations": ["invalid-row"], "decisions": []}))
    before = path.read_bytes()
    ledger = engine.AllowanceLedger(tmp_path, clock=lambda: 2000000001)
    assert not ledger.observe(money(), "deepseek", {})["audit_saved"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("case", FIXTURE["source_cases"], ids=lambda c: c["name"])
def test_source_response_replay(case):
    assert load("replay_allowance").source_case_matches(case)
