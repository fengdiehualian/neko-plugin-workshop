import json
import urllib.error
import urllib.request
from unittest.mock import patch
from urllib.parse import urlsplit

from test_providers import load

topup, fleet, server = [load(x) for x in ("topup", "fleet", "server")]


def test_default_routes_match_accounts_and_supported_platforms():
    with patch.object(topup.app_topup.sys, "platform", "darwin"):
        assert topup.info("trae")["routing"]["target"] == "application"
        for key, *_ in fleet.CATALOG:
            data = topup.info(key)
            route = data["routing"]
            if route["kind"] == "website":
                assert route["target"] in [x["id"] for x in data["entries"]]
            elif route["kind"] == "application":
                assert data["application"] and route["target"] == data["application"]["id"]
            else:
                assert route["target"] is None
        assert topup.info("codex")["routing"]["target"] == "usage"
        assert topup.info("deepseek")["routing"]["target"] == "balance"
        assert topup.info("workbuddy")["routing"]["target"] == "plan"
        assert "头像 → 设置 → 套餐与积分" in topup.info("workbuddy")["routing"]["guide"]
        assert topup.info("workbuddy")["application"] is not None
        assert all(topup.info(k)["routing"]["kind"] == "choice" for k in ("qwen", "doubao"))
        route = topup.info("codex")["routing"]
        route["target"] = "billing"
        assert topup.info("codex")["routing"]["target"] == "usage"
    with patch.object(topup.app_topup.sys, "platform", "win32"):
        route = topup.info("trae")["routing"]
        assert route["kind"] == "website" and route["target"] == "credits"


def test_all_profiles_have_explicit_official_or_unknown_entry():
    missing = []
    for key, *_ in fleet.CATALOG:
        data = topup.info(key)
        if not data["entries"]:
            missing.append(key)
        for row in data["entries"]:
            url = urlsplit(row["url"])
            assert url.scheme == "https" and url.hostname == row["domain"]
            assert not url.username and not url.password and not url.query and not url.fragment
            assert topup.destination(key, row["id"]) == row
    assert set(missing) == {"jev", "manual", "custom", "deepseek_chat"}
    assert topup.info("deepseek_chat")["routing"]["kind"] == "unavailable"
    assert len(topup.LINKS) == 19
    assert topup.info("custom")["entries"] == []


def test_api_subscription_and_regions_never_share_wrong_billing_page():
    destinations = {key: {x["url"] for x in topup.info(key)["entries"]} for key in topup.LINKS}
    for a, b in [
        ("codex", "openai"),
        ("chatgpt", "openai"),
        ("claude", "anthropic"),
        ("kimi", "kimi_api_cn"),
        ("kimi_api_cn", "kimi_api_global"),
    ]:
        assert not destinations[a] & destinations[b]
    assert all("platform.kimi.com" in x for x in destinations["kimi_api_cn"])
    assert all("platform.kimi.ai" in x for x in destinations["kimi_api_global"])
    assert all("usercenter2.aliyun.com" in x for x in destinations["qwen_api"])
    assert len(destinations["qwen"]) == 3 and len(destinations["doubao"]) == 2


def test_opening_official_is_not_a_payment_and_cannot_change_urls():
    calls = []
    for profile, entries in topup.LINKS.items():
        for row in entries:
            result = topup.open_official(profile, row["id"], lambda url: calls.append(url) or True)
            assert result["requested"] and result["payment_confirmed"] is False
            assert calls[-1] == row["url"]
    for a, b in [
        ("custom", "https://evil.invalid"),
        ("deepseek", "javascript:alert(1)"),
        ("workbuddy", "balance"),
        (None, "usage"),
    ]:
        try:
            topup.open_official(a, b, lambda _: (_ for _ in ()).throw(AssertionError("must not open")))
        except ValueError:
            pass
        else:
            raise AssertionError("unknown target allowed")
    data = topup.info("deepseek")
    data["entries"][0]["url"] = "https://evil.invalid"
    assert topup.destination("deepseek", "balance")["url"] == "https://platform.deepseek.com/top_up"
    result = topup.open_official("deepseek", "balance", lambda _: False)
    assert not result["requested"] and not result["payment_confirmed"]


def test_topup_http_csrf_fixed_targets_and_no_quota_mutation(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.configure(
        {"profile": "trae", "quota_mode": "manual", "quota_remaining": 9, "quota_total": 100, "quota_unit": "积分"}
    )
    before = f.snapshot()
    opened = []
    httpd, thread = server.start_server(f, port=0, official_opener=lambda url: opened.append(url) or True)
    base = f"http://127.0.0.1:{httpd.server_port}"

    def request(method, path, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(
                urllib.request.Request(base + path, data=raw, method=method, headers=headers or {}), timeout=3
            ) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    try:
        code, data = request("GET", "/api/status")
        assert code == 200
        headers = {"X-Neko-CSRF": data["csrf"], "Content-Type": "application/json"}
        assert request("POST", "/api/topup", {"profile": "trae", "target": "credits"})[0] == 403
        assert (
            request(
                "POST",
                "/api/topup",
                {"profile": "trae", "target": "credits"},
                dict(headers, Origin="https://evil.invalid"),
            )[0]
            == 403
        )
        assert (
            request(
                "POST", "/api/topup", {"profile": "trae", "target": "credits", "url": "https://evil.invalid"}, headers
            )[0]
            == 400
        )
        assert request("POST", "/api/topup", {"profile": "custom", "target": "credits"}, headers)[0] == 400
        assert not opened
        code, data = request("POST", "/api/topup", {"profile": "trae", "target": "credits"}, headers)
        assert code == 200 and data["requested"] and data["payment_confirmed"] is False
        assert opened == ["https://www.trae.cn/pricing"]
        assert f.snapshot()["config"] == before["config"] and f.snapshot()["account_quota"] == before["account_quota"]
        assert "api_key" not in json.dumps(data)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
