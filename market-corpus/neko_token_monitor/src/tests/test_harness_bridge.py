import json
import os
import sys
import time
from unittest.mock import patch

from test_providers import load

bridge, core, fleet, topup = [load(x) for x in ("harness_bridge", "core", "fleet", "topup")]


def payload():
    return {
        "protocol": 1,
        "provider": "deepseek_harness",
        "observed_at": time.time(),
        "wallets": {
            "normal": [{"currency": "CNY", "balance": "9.123456789"}, {"currency": "USD", "balance": "1.25"}],
            "bonus": [{"currency": "CNY", "balance": "0.50"}],
        },
    }


def test_wallet_precision_currencies_and_safe_projection():
    raw = payload()
    raw["token"] = "must-not-leak"
    raw["wallets"]["normal"][0]["email"] = "must-not-leak"
    parsed = bridge.parse_balance(raw)
    assert parsed["balance_infos"] == [
        {"currency": "CNY", "total_balance": "9.623456789"},
        {"currency": "USD", "total_balance": "1.25"},
    ]
    assert parsed["wallet_breakdown"]["CNY"] == {"normal": "9.123456789", "bonus": "0.50"}
    assert "must-not-leak" not in json.dumps(parsed)
    raw["wallets"]["normal"] = [{"currency": "CNY", "balance": "-1"}]
    raw["wallets"]["bonus"] = []
    assert bridge.parse_balance(raw)["is_available"] is False


def test_invalid_or_old_balance_never_becomes_zero():
    for bad in (True, "NaN", "Infinity", "1e1000000", None):
        value = payload()
        value["wallets"]["normal"][0]["balance"] = bad
        try:
            bridge.parse_balance(value)
        except ValueError:
            pass
        else:
            raise AssertionError("bad amount accepted")
    for stamp in (True, 0, time.time() - 121, time.time() + 1):
        value = payload()
        value["observed_at"] = stamp
        try:
            bridge.parse_balance(value)
        except ValueError:
            pass
        else:
            raise AssertionError("stale data accepted")


def test_connection_is_private_loopback_only(tmp_path):
    p = tmp_path / "connection.json"

    def write(value, mode=0o600):
        p.write_text(json.dumps(value))
        os.chmod(p, mode)

    valid = {"protocol": 1, "url": "http://127.0.0.1:48929", "token": "a" * 64}
    write(valid)
    assert bridge.connection(p) == {"url": valid["url"], "token": valid["token"]}
    for url in ("https://evil.invalid", "http://localhost:48929", "http://127.0.0.1:48929/x", "http://127.0.0.1:99999"):
        write(dict(valid, url=url))
        try:
            bridge.connection(p)
        except ValueError:
            pass
        else:
            raise AssertionError("untrusted endpoint accepted")
    write(valid, 0o644)
    try:
        bridge.connection(p)
    except ValueError:
        pass
    else:
        raise AssertionError("public token file accepted")
    link = tmp_path / "link.json"
    link.symlink_to(p)
    try:
        bridge.connection(link)
    except ValueError:
        pass
    else:
        raise AssertionError("symlink accepted")


def test_real_monitor_contract_persists_failure_and_needs_no_key(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.configure({"profile": "deepseek_harness", "currency": "CNY", "balance_total": 100})
    module = sys.modules[fleet.Monitor.__module__]
    with patch.object(module, "read_harness_balance", return_value=bridge.parse_balance(payload())):
        state = f.refresh()
    assert state["configured"] and state["selected"]["balance"] == "9.623456789"
    assert state["wallet_breakdown"]["CNY"]["bonus"] == "0.50" and not state["stale"]
    monitor = f._monitor("deepseek_harness")
    monitor.last_attempt = 0
    with patch.object(module, "read_harness_balance", side_effect=ValueError("桥接断开")):
        state = f.refresh()
    assert state["stale"] and state["selected"]["balance"] == "9.623456789"
    assert fleet.Fleet(tmp_path).snapshot()["error"] == "桥接断开"
    assert not f._monitor("deepseek_harness").config["api_key"]


def test_harness_native_destination_uses_bridge_only():
    app = topup.app_topup
    with (
        patch.object(app.sys, "platform", "darwin"),
        patch.object(app, "open_topup", return_value={"ok": True, "stage": "credits"}) as opened,
    ):
        result = topup.open_official("deepseek_harness", "application")
        assert result["page_confirmed"] and result["payment_confirmed"] is False
        assert opened.call_count == 1 and "Harness" in result["message"]
