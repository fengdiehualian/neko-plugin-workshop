import importlib.util
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("_catfood_test")
pkg.__path__ = [str(ROOT)]
sys.modules["_catfood_test"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location("_catfood_test." + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


providers = load("providers")
core = load("core")


def test_face_reference_total_persists_without_clearing_balance():
    with tempfile.TemporaryDirectory() as d:
        monitor = core.Monitor(d)
        monitor.state = {"balances": [{"currency": "CNY", "balance": "5"}]}
        monitor.configure({"balance_total": "20"})
        assert monitor.snapshot()["selected"]["balance"] == "5"
        assert core.Monitor(d).config["balance_total"] == 20
        monitor.configure({"balance_total": ""})
        assert core.Monitor(d).config["balance_total"] is None


def test_face_reference_total_rejects_invalid_numbers():
    with tempfile.TemporaryDirectory() as d:
        monitor = core.Monitor(d)
        for value in ("nan", "inf", "-1", "1000000000001"):
            try:
                monitor.configure({"balance_total": value})
            except ValueError:
                pass
            else:
                raise AssertionError("invalid total accepted")
        assert monitor.config["balance_total"] is None


def test_openai_pagination_and_usd():
    pages = [
        {
            "data": [{"results": [{"amount": {"value": "1.25", "currency": "usd"}}]}],
            "has_more": True,
            "next_page": "p2",
        },
        {"data": [{"results": [{"amount": {"value": "2.00", "currency": "usd"}}]}], "has_more": False},
    ]
    calls = []

    def request(url, headers):
        calls.append(url)
        assert headers["Authorization"] == "Bearer fake"
        return pages.pop(0)

    result = providers.query(
        dict(source="openai", budget=10, budget_start="2026-01-01", currency="USD", api_key="fake"), request
    )
    assert result["balance_infos"][0]["total_balance"] == "6.75"
    assert "page=p2" in calls[1]
    assert "非平台余额" in result["basis"]


def test_claude_cents_to_dollars():
    result = providers.query(
        dict(source="anthropic", budget=10, budget_start="2026-01-01", currency="USD", api_key="fake"),
        lambda u, h: {"data": [{"results": [{"amount": "125", "currency": "USD"}]}], "has_more": False},
    )
    assert result["balance_infos"][0]["total_balance"] == "8.75"


def test_custom_nested_path():
    cfg = dict(
        source="custom",
        endpoint="https://example.com/balance",
        value_path="data.accounts.0.balance",
        currency="CNY",
        api_key="fake",
    )
    result = providers.query(cfg, lambda u, h: {"data": {"accounts": [{"balance": "12.50"}]}})
    assert result["balance_infos"][0]["total_balance"] == "12.50"


def test_switch_does_not_send_old_key():
    with tempfile.TemporaryDirectory() as d:
        monitor = core.Monitor(d)
        monitor.configure({"api_key": "fake-test-secret"})
        monitor.configure({"source": "custom", "endpoint": "https://example.com/balance"})
        assert monitor.config["api_key"] == ""
        assert not monitor.snapshot()["configured"]
        monitor.configure({"source": "manual", "budget": 12, "model": "any model"})
        state = monitor.refresh()
        assert state["selected"]["balance"] == "12.0"
        assert state["basis"] == "手动填写 · 不自动扣减"
        assert "api_key" not in state["config"]


def test_reject_non_https_and_nonfinite():
    for url in ["http://example.com", "https://user:secret@example.com", "https://example.com?k=secret"]:
        try:
            providers.validate_endpoint(url)
        except ValueError:
            pass
        else:
            raise AssertionError(url)
    for value in ["NaN", "Infinity", True]:
        try:
            providers.money(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)


def test_jev_counter_deduplicates_and_rejects_invalid():
    with tempfile.TemporaryDirectory() as d:
        monitor = core.Monitor(d)
        item = dict(provider="jev", request_id="test1", model="jev-test", input_tokens=321, output_tokens=31)
        assert monitor.record_usage(item)["requests"] == 1
        assert monitor.record_usage(item)["requests"] == 1
        assert monitor.snapshot()["reported_usage"]["input_tokens"] == 321
        monitor.configure({"source": "jev"})
        assert monitor.refresh()["selected"] is None
        assert monitor.snapshot()["estimated_tokens"] is None
        item["input_tokens"] = -1
        try:
            monitor.record_usage(item)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid usage accepted")
        assert core.Monitor(d).snapshot()["reported_usage"]["requests"] == 1


def test_codex_quota_missing_is_not_zero():
    quota = load("codex_quota")
    result = quota.normalize(
        {
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": 22, "windowDurationMins": 10080, "resetsAt": 1790724867},
                    "secondary": None,
                }
            }
        }
    )
    assert result["windows"][0]["remaining"] == 78
    assert len(result["windows"]) == 1
    try:
        quota.normalize({"rateLimitsByLimitId": {"codex": {"primary": None}}})
    except ValueError:
        pass
    else:
        raise AssertionError("Unavailable quota became zero")


def test_failed_refresh_stays_stale_after_restart(tmp_path):
    from unittest.mock import patch
    for source in ("deepseek", "codex", "openai"):
        monitor = core.Monitor(tmp_path / source)
        monitor.configure({"source": source, "api_key": "synthetic"})
        monitor.state = {"balances": [{"currency": monitor.config["currency"], "balance": "1"}],
                         "updated_at": __import__("time").time()}
        monitor.write("state.json", monitor.state)
        def fail(*_):
            raise ValueError("synthetic query failure")
        monitor.fetch = fail
        with patch.object(core, "query", fail), patch.object(core, "read_quota", fail):
            monitor.refresh()
        restarted = core.Monitor(tmp_path / source).snapshot()
        assert restarted["stale"] and restarted["error"] == "synthetic query failure"
        assert restarted["selected"]["balance"] == "1"
        assert restarted["estimated_tokens"] is None


def test_boolean_balance_is_not_money():
    for value in (True, False):
        try:
            core.parse_balance({"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": value}]})
        except ValueError:
            pass
        else:
            raise AssertionError("boolean interpreted as an account balance")


def test_official_shared_balances_and_destinations():
    for source, expected_url, payload, expected in [
        (
            "openrouter",
            "https://openrouter.ai/api/v1/credits",
            {"data": {"total_credits": "20", "total_usage": "3.25"}},
            "16.75",
        ),
        (
            "siliconflow",
            "https://api.siliconflow.cn/v1/user/info",
            {"status": True, "data": {"balance": "2", "chargeBalance": "4", "totalBalance": "6"}},
            "6",
        ),
    ]:

        def request(url, headers):
            assert url == expected_url
            assert headers == {"Authorization": "Bearer test-secret"}
            return payload

        result = providers.query(dict(source=source, api_key="test-secret", currency="USD"), request)
        assert result["balance_infos"][0]["total_balance"] == expected
        assert "共享" in result["basis"]


def test_fleet_migration_keys_isolated_and_persistent(tmp_path):
    fleet_module = load("fleet")
    old = core.Monitor(tmp_path)
    old.configure({"api_key": "deepseek-test-secret"})
    fleet = fleet_module.Fleet(tmp_path)
    assert fleet.monitors["deepseek"].config["api_key"] == "deepseek-test-secret"
    fleet.configure({"profile": "openrouter", "api_key": "router-test-secret"})
    fleet.configure({"profile": "deepseek", "api_key": ""})
    assert fleet.monitors["deepseek"].config["api_key"] == "deepseek-test-secret"
    assert fleet.monitors["openrouter"].config["api_key"] == "router-test-secret"
    state = fleet.snapshot()
    assert "test-secret" not in str(state)
    restarted = fleet_module.Fleet(tmp_path)
    assert restarted.selected == "deepseek"
    assert restarted.enabled == {"deepseek", "openrouter"}
    assert restarted.monitors["openrouter"].config["api_key"] == "router-test-secret"


def test_manual_quota_independent_and_not_refreshed_by_polling(tmp_path):
    fleet = load("fleet").Fleet(tmp_path)
    fleet.configure({"profile": "gemini", "quota_remaining": 25, "quota_total": 100})
    first = fleet.refresh()
    fleet.configure({"profile": "claude", "quota_remaining": 60, "quota_unit": "%"})
    fleet.refresh()
    fleet.select("gemini")
    assert fleet.refresh()["updated_at"] == first["updated_at"]
    assert fleet.snapshot()["config"]["quota_remaining"] == 25
    assert fleet.snapshot()["selected"] is None
    assert fleet.snapshot()["estimated_tokens"] is None
    try:
        fleet.configure({"profile": "gemini", "quota_remaining": 101})
    except ValueError:
        pass
    else:
        raise AssertionError("remaining greater than total accepted")
    assert fleet.snapshot()["config"]["quota_remaining"] == 25


def test_fleet_jev_ledger_survives_source_switch(tmp_path):
    fleet = load("fleet").Fleet(tmp_path)
    fleet.record_usage(dict(provider="jev", request_id="test-new", model="jev", input_tokens=7, output_tokens=3))
    fleet.configure({"profile": "jev"})
    assert fleet.snapshot()["reported_usage"]["input_tokens"] == 7
    fleet.configure({"profile": "manual", "budget": 12})
    assert fleet.snapshot()["reported_usage"]["output_tokens"] == 3
