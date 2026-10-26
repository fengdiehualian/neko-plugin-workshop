"""Offline self-check tests: injected fake probes, zero network access.

Everything here is deterministic except two deliberately tiny latency measurements,
which only assert ordering / a wall-clock upper bound. The whole file must stay well
under the 2 second budget a real self-check needs a proxy for.
"""

from __future__ import annotations

import socket
import threading
import time

import conftest
import pytest

diagnose = conftest.load("_diagnose")
net = conftest.load("_net")
resilience = conftest.load("_resilience")
entries = conftest.load("__init__")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Hard proof that W6 is offline: any real socket anywhere fails the test."""

    def tripwire(*_args, **_kwargs):
        raise AssertionError("_diagnose must never open a network connection")

    monkeypatch.setattr(socket, "socket", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    yield

HIT = [{"title": "猫娘计划", "url": "https://project-neko.example/", "snippet": "x",
        "content": "", "published": ""}]


def works(delay: float = 0.0):
    def probe():
        if delay:
            time.sleep(delay)
        return [dict(item) for item in HIT]

    return probe


def raises(error: BaseException):
    def probe():
        raise error

    return probe


# --------------------------------------------------------------- classify_error

CASES = [
    # (exception, expected kind) — plan §0 says these three faults must not be conflated.
    (resilience.BlockedError("baidu 返回了人机验证页"), "blocked"),
    (resilience.BlockedError("Exa 免配额已用完（429）"), "blocked"),
    # What the key ring raises when Exa never answered: cooling the backend needs
    # BlockedError, but telling the user 反爬 about a dead socket sends them off to
    # change backends for a network problem.
    (resilience.BlockedError(entries._MSG_EXA_UNREACHABLE), "timeout"),
    (resilience.CooldownError("baidu 后端处于失败冷却期"), "blocked"),
    (resilience.QuotaExhaustedError("402 Payment Required"), "quota"),
    (resilience.QuotaExhaustedError("exa 429", retry_after_seconds=30.0), "quota"),
    (resilience.BusyError("exa 后端请求过于频繁"), "quota"),
    (resilience.ApiKeyRejectedError("web_search_exa error (401): Invalid API key"), "key"),
    (resilience.SearchProviderError("AnySearch API Key 无效或已失效"), "key"),
    (net.HttpStatusCodeError("HTTP 429", 429), "quota"),
    (net.HttpStatusCodeError("HTTP 402", 402), "quota"),
    (net.HttpStatusCodeError("HTTP 401", 401), "key"),
    (net.HttpStatusCodeError("HTTP 403", 403), "blocked"),
    (net.HttpStatusCodeError("HTTP 503", 503), "network"),
    (net.NetworkError("网络不可达: timed out"), "timeout"),
    (net.NetworkError("请求超时"), "timeout"),
    (TimeoutError(), "timeout"),
    (net.NetworkError("网络不可达: [Errno 11001] getaddrinfo failed"), "network"),
    (net.NetworkError("网络错误: SSLError"), "network"),
    (net.NetworkError("网络不可达: [WinError 10061] 拒绝连接"), "network"),
    (resilience.SearchProviderError("Exa 未返回可解析结果"), "parse"),
    (resilience.SearchProviderError("sogou 未返回可解析结果"), "parse"),
    (resilience.SearchProviderError("AnySearch 返回了无效 JSON"), "parse"),
    (resilience.SearchProviderError("bing 没有匹配的结果"), "empty"),
    (resilience.SearchProviderError("boom"), "empty"),
    (RuntimeError("boom"), "network"),
]


def test_every_documented_failure_lands_in_its_own_bucket() -> None:
    kinds = {kind for kind, _advice in (diagnose.classify_error(error) for error, _ in CASES)}
    assert kinds == {"blocked", "quota", "key", "network", "timeout", "parse", "empty"}


def test_classify_error_cases() -> None:
    for error, expected in CASES:
        kind, advice = diagnose.classify_error(error)
        assert kind == expected, f"{type(error).__name__}({error}) -> {kind}, want {expected}"
        assert advice and any("\u4e00" <= char <= "\u9fff" for char in advice)
        assert "Error" not in advice and "http" not in advice.lower()


def test_classify_error_accepts_none_and_reports_ok() -> None:
    assert diagnose.classify_error(None)[0] == "ok"


def test_cooldown_and_throttle_keep_their_own_wording() -> None:
    """Same bucket, different sentence: "wait a moment" must not read as "we were blocked"."""
    kind, cooldown = diagnose.classify_error(resilience.CooldownError("baidu 后端处于失败冷却期"))
    busy_kind, busy = diagnose.classify_error(resilience.BusyError("exa 后端请求过于频繁"))
    assert (kind, busy_kind) == ("blocked", "quota")
    assert cooldown == diagnose.classify_error(resilience.CooldownError("anything"))[1]
    assert "退避" in cooldown
    assert "稍等几秒" in busy


def test_a_throttled_block_is_not_reported_as_an_anti_scrape_page() -> None:
    """``BlockedError`` covers both; only the captcha one deserves 反爬 wording.

    Exa's keyed 429 arrives as ``BlockedError`` so the penalty box backs off. The
    self-check must not then tell the user the backend blocks bots and to stop
    trusting it -- the fix is to wait seconds.
    """
    throttle_kind, throttle = diagnose.classify_error(
        resilience.BlockedError("Exa 请求过于频繁（429）"))
    block_kind, block = diagnose.classify_error(
        resilience.BlockedError("bing 返回了人机验证页"))
    assert (throttle_kind, block_kind) == ("quota", "blocked")
    assert "稍等几秒" in throttle
    assert "验证页" in block


def test_text_markers_still_classify_when_the_exception_class_is_unrelated() -> None:
    """Guards the offline contract: W5 may hand us an error class from a second copy
    of the plugin (same name, different module identity), so the wording must still
    land in the right bucket."""

    class Alien(RuntimeError):
        pass

    cases = {
        "baidu 后端处于失败冷却期": ("blocked", "退避"),
        "exa 后端请求过于频繁": ("quota", "稍等几秒"),
        "bing 返回了人机验证页": ("blocked", "验证页"),
        "Exa 未返回可解析结果": ("parse", "不可靠"),
        "web_search_exa error (401): Invalid API key": ("key", "密钥"),
        "额度已用尽": ("quota", "稍后再试"),
    }
    for message, (expected_kind, fragment) in cases.items():
        kind, advice = diagnose.classify_error(Alien(message))
        assert kind == expected_kind, message
        assert fragment in advice, (message, advice)


def test_usable_results_separates_no_results_from_unreadable_results() -> None:
    assert diagnose.usable_results(HIT) == (True, "ok")
    assert diagnose.usable_results([]) == (False, "empty")
    assert diagnose.usable_results([{}]) == (False, "parse")
    assert diagnose.usable_results("nonsense") == (False, "parse")
    assert diagnose.usable_results(None) == (False, "empty")
    assert diagnose.usable_results([{"title": "  "}]) == (False, "parse")


def test_dns_pollution_and_block_get_different_advice() -> None:
    """The whole point of the module: "can't connect" and "blocked" are not the same."""
    _k1, dns = diagnose.classify_error(
        net.NetworkError("网络不可达: [Errno 11001] getaddrinfo failed"))
    _k2, blocked = diagnose.classify_error(
        resilience.BlockedError("baidu 返回反自动化验证页（HTTP 202）"))
    _k3, quota = diagnose.classify_error(net.HttpStatusCodeError("HTTP 429", 429))
    _k4, key = diagnose.classify_error(resilience.ApiKeyRejectedError("Invalid API key"))
    assert "代理" in dns and "代理" not in blocked
    assert "验证页" in blocked
    assert "稍后再试" in quota
    assert "密钥" in key and "面板" in key


# ---------------------------------------------------------------------- recommend


def test_recommend_sorts_fastest_first() -> None:
    chain = diagnose.recommend([("bing", 0.6), ("exa", 1.3), ("anysearch", 1.2)],
                              proxy_detected=False)
    assert chain == ["bing", "anysearch", "exa"]


def test_recommend_drops_proxy_only_backends_without_a_proxy() -> None:
    detected = [("duckduckgo", 0.4), ("bing", 0.6), ("exa", 1.3)]
    assert diagnose.recommend(detected, proxy_detected=False) == ["bing", "exa"]
    assert diagnose.recommend(detected, proxy_detected=True) == ["duckduckgo", "bing", "exa"]


def test_recommend_tolerates_garbage_and_unknown_latency() -> None:
    chain = diagnose.recommend([("exa", 1.0), ("bad", "n/a"), "", None, ("sogou", 0),
                                ("exa", 0.2), ("bing", 9.0)], proxy_detected=False)
    assert chain == ["exa", "bing", "bad", "sogou"], \
        "duplicate keeps the best ms; unmeasured entries keep input order at the tail"
    assert "" not in chain


def test_recommend_is_pure_and_never_mutates_input() -> None:
    detected = [("exa", 2.0), ("bing", 1.0)]
    snapshot = list(detected)
    assert diagnose.recommend(detected, proxy_detected=True) == ["bing", "exa"]
    assert detected == snapshot


# ---------------------------------------------------------------------------- run

def test_rows_keep_probe_order_and_field_shape() -> None:
    report = diagnose.run(
        [
            ("exa", works()),
            ("anysearch", works()),
            ("bing", works()),
            ("baidu", raises(resilience.BlockedError("baidu 返回了人机验证页"))),
            ("sogou", lambda: []),
            ("duckduckgo", raises(net.NetworkError("网络不可达: timed out"))),
        ],
        allow_proxy=False,
        proxy_detected=False,
    )

    assert set(report) == {"rows", "proxy_detected", "recommended_chain", "summary"}
    assert [row["backend"] for row in report["rows"]] == \
        ["exa", "anysearch", "bing", "baidu", "sogou", "duckduckgo"]
    assert [row["direct"] for row in report["rows"]] == \
        ["ok", "ok", "ok", "blocked", "empty", "timeout"]
    for row in report["rows"]:
        assert list(row) == ["backend", "direct", "proxied", "ms", "note"]
        assert row["proxied"] == "", "allow_proxy=False must not fill the proxy column"
    assert report["recommended_chain"] == ["exa", "anysearch", "bing"]
    assert report["proxy_detected"] is False
    # Sub-millisecond fake probes all sit on MEASURED_FLOOR, so ranking ties and falls
    # back to configured order; assert the membership, not the tie-break.
    assert sorted(report["recommended_chain"]) == ["anysearch", "bing", "exa"]


def test_one_raising_probe_cannot_damage_its_neighbours() -> None:
    def explode():
        raise ZeroDivisionError("divide by zero in _providers")

    report = diagnose.run([("a", works()), ("b", explode), ("c", works())],
                          allow_proxy=False, proxy_detected=False)
    assert [row["direct"] for row in report["rows"]] == ["ok", "network", "ok"]
    assert sorted(report["recommended_chain"]) == ["a", "c"]


def test_proxy_column_is_merged_into_the_same_row_and_never_calls_unauthorised_probes() -> None:
    called = []

    def proxied():
        called.append("duckduckgo")
        return [dict(HIT[0])]

    report = diagnose.run(
        [("bing", works()), ("duckduckgo", raises(net.NetworkError("请求超时")))],
        allow_proxy=False,
        proxy_detected=True,
        proxied_probes={"duckduckgo": proxied},
    )
    assert called == [], "allow_proxy=False must not run any proxy path"
    assert all(row["proxied"] == "" for row in report["rows"])
    assert "duckduckgo" not in report["recommended_chain"] or report["proxy_detected"]

    allowed = diagnose.run(
        [("bing", works()), ("duckduckgo", raises(net.NetworkError("请求超时")))],
        allow_proxy=True,
        proxy_detected=True,
        proxied_probes=[("duckduckgo", proxied)],
    )
    assert called == ["duckduckgo"]
    ddg = allowed["rows"][1]
    assert ddg["backend"] == "duckduckgo"
    assert ddg["direct"] == "timeout" and ddg["proxied"] == "ok"
    assert len(allowed["rows"]) == 2, "two paths of one backend are one row"
    assert ddg["note"] == "直连不通，走代理可用"
    assert sorted(allowed["recommended_chain"]) == ["bing", "duckduckgo"]


def test_proxy_only_backend_needs_a_detected_proxy_to_be_recommended() -> None:
    probes = [("duckduckgo", works())]
    without = diagnose.run(probes, allow_proxy=True, proxy_detected=False)
    assert without["rows"][0]["direct"] == "ok"
    assert without["recommended_chain"] == [], "measured OK but no proxy -> do not recommend ddg"


def test_empty_probe_list_still_returns_a_usable_report() -> None:
    report = diagnose.run([], allow_proxy=False, proxy_detected=False)
    assert report["rows"] == [] and report["recommended_chain"] == []
    assert "自检" in report["summary"]


def test_per_probe_timeout_bounds_a_hanging_backend_without_stalling_the_table() -> None:
    started = time.perf_counter()
    report = diagnose.run(
        [("slow", lambda: time.sleep(0.3)), ("fast", works()), ("faster", works())],
        allow_proxy=False,
        proxy_detected=False,
        per_probe_timeout=0.05,
    )
    elapsed = time.perf_counter() - started
    rows = {row["backend"]: row for row in report["rows"]}
    assert rows["slow"]["direct"] == "timeout"
    assert rows["fast"]["direct"] == rows["faster"]["direct"] == "ok"
    assert elapsed < 1.0, f"backstop must not wait for the probe, took {elapsed:.2f}s"


def test_probes_run_concurrently_and_the_pool_is_capped_at_four() -> None:
    barrier = threading.Barrier(4, timeout=1.0)
    lock = threading.Lock()

    def guarded():
        barrier.wait()  # only passes if four probes are in flight at once
        return [dict(HIT[0])]

    for asked_workers in (diagnose.MAX_WORKERS, 16, 99):
        peak = []
        live = {"n": 0}

        def counted():
            with lock:
                live["n"] += 1
                peak.append(live["n"])
            try:
                return guarded()
            finally:
                with lock:
                    live["n"] -= 1

        started = time.perf_counter()
        report = diagnose.run([(f"b{i}", counted) for i in range(8)],
                              allow_proxy=False, proxy_detected=False,
                              per_probe_timeout=5.0, max_workers=asked_workers)
        elapsed = time.perf_counter() - started
        assert all(row["direct"] == "ok" for row in report["rows"]), asked_workers
        assert max(peak) == diagnose.MAX_WORKERS, f"thread ceiling breached: {peak}"
        assert elapsed < 0.9, f"8 probes should cost ~two rounds, took {elapsed:.2f}s"


def test_chain_order_follows_measured_latency() -> None:
    report = diagnose.run(
        [("slow", works(0.16)), ("mid", works(0.08)), ("quick", works(0.01))],
        allow_proxy=False,
        proxy_detected=False,
    )
    assert report["recommended_chain"] == ["quick", "mid", "slow"]
    assert [row["ms"] for row in report["rows"]] == \
        sorted([row["ms"] for row in report["rows"]], reverse=True)


def test_a_probe_too_fast_for_the_clock_is_not_treated_as_unmeasured() -> None:
    """Regression: monotonic() ticks at 15.6ms on Windows, so a cached backend used to
    measure 0.0 and fall to the tail of the chain. Every measured attempt now has a
    floor, so ranking is by real speed and ties keep the configured order.
    """
    report = diagnose.run([("exa", works()), ("bing", works())],
                          allow_proxy=False, proxy_detected=False)
    assert [row["direct"] for row in report["rows"]] == ["ok", "ok"]
    assert all(row["ms"] >= diagnose.MEASURED_FLOOR for row in report["rows"])
    assert report["recommended_chain"] == ["exa", "bing"]


def test_proxy_detection_is_consulted_only_when_not_supplied(monkeypatch) -> None:
    seen = []

    def fake_present():
        seen.append(1)
        return True

    monkeypatch.setattr(net, "system_proxy_present", fake_present)
    report = diagnose.run([("bing", works())], allow_proxy=False)
    assert report["proxy_detected"] is True and seen == [1]

    diagnose.run([("bing", works())], allow_proxy=False, proxy_detected=False)
    assert seen == [1], "an explicit answer must not trigger detection"
    assert diagnose.proxy_available() is True


def test_a_probe_that_is_not_callable_reports_a_row_instead_of_crashing() -> None:
    report = diagnose.run([("exa", None), ("bing", works())],
                          allow_proxy=False, proxy_detected=False)
    assert report["rows"][0]["direct"] == "parse"
    assert report["rows"][0]["note"] == "自检项无效：这个后端没有被真正测到"
    assert report["recommended_chain"] == ["bing"]


def test_summary_does_not_overclaim_a_majority_when_causes_are_mixed() -> None:
    """One blocked backend must not be reported as "most backends are blocked" while a
    timeout is the failure the user actually has to fix."""
    report = diagnose.run(
        [("baidu", raises(resilience.BlockedError("baidu 返回了人机验证页"))),
         ("duckduckgo", raises(net.NetworkError("网络不可达: timed out")))],
        allow_proxy=False,
        proxy_detected=False,
    )
    summary = report["summary"]
    assert "多数" not in summary
    assert "反爬拦截" in summary and "连接超时" in summary
    assert "代理" in summary

    single = diagnose.run([("baidu", raises(resilience.BlockedError("安全验证")))],
                          allow_proxy=False, proxy_detected=False)
    assert "反爬验证页" in single["summary"]


def test_summary_says_proxy_is_broken_when_a_detected_proxy_still_fails() -> None:
    report = diagnose.run(
        [("duckduckgo", raises(net.NetworkError("网络不可达: timed out")))],
        allow_proxy=True, proxy_detected=True,
        proxied_probes=[("duckduckgo", raises(net.NetworkError("网络错误: SSLError")))],
    )
    assert report["rows"][0]["proxied"] == "network"
    assert "节点" in report["summary"]


def test_summary_survives_a_large_chain_without_leaking_internals() -> None:
    probes = [(f"backend-{index}", raises(resilience.QuotaExhaustedError("402")))
              for index in range(24)]
    report = diagnose.run(probes, allow_proxy=False, proxy_detected=False, per_probe_timeout=2.0)
    assert len(report["rows"]) == 24
    assert len(report["summary"]) <= diagnose.SUMMARY_MAX_CHARS
    assert "Error" not in report["summary"] and "402" not in report["summary"]


# ----------------------------------------------------------------------- summary

def test_summary_is_plain_chinese_copy_for_every_shape() -> None:
    reports = [
        diagnose.run([("exa", works()), ("bing", works(0.05))],
                     allow_proxy=False, proxy_detected=False),
        diagnose.run([("baidu", raises(resilience.BlockedError("baidu 返回了人机验证页")))],
                     allow_proxy=False, proxy_detected=False),
        diagnose.run([("duckduckgo", raises(net.NetworkError("请求超时")))],
                     allow_proxy=True, proxy_detected=False,
                     proxied_probes=[("duckduckgo", works())]),
        diagnose.run([("exa", raises(resilience.ApiKeyRejectedError("Invalid API key")))],
                     allow_proxy=False, proxy_detected=False),
        diagnose.run([("exa", raises(resilience.QuotaExhaustedError("402")))],
                     allow_proxy=False, proxy_detected=True),
        diagnose.run([], allow_proxy=False, proxy_detected=False),
    ]
    for report in reports:
        summary = report["summary"]
        assert summary and len(summary) <= diagnose.SUMMARY_MAX_CHARS
        for forbidden in ("Error", "Traceback", "http://", "https://", "ZeroDivision", "File \""):
            assert forbidden not in summary, f"{forbidden!r} leaked into {summary!r}"
        assert any("\u4e00" <= char <= "\u9fff" for char in summary)


def test_summary_tells_the_user_whether_to_open_a_proxy() -> None:
    usable = diagnose.run([("bing", works())], allow_proxy=False, proxy_detected=False)
    assert "不开代理就能用" in usable["summary"]
    assert "下一步" in usable["summary"] or "建议" in usable["summary"]

    needs_proxy = diagnose.run(
        [("duckduckgo", raises(net.NetworkError("网络不可达: timed out")))],
        allow_proxy=False, proxy_detected=False,
    )
    assert "代理" in needs_proxy["summary"]
    assert needs_proxy["recommended_chain"] == []


def test_summary_scrubs_urls_and_status_noise_from_a_hostile_backend_name() -> None:
    report = diagnose.run(
        [("https://mcp.exa.ai/mcp 429", raises(RuntimeError("Traceback (most recent call last)")))],
        allow_proxy=False, proxy_detected=False,
    )
    summary = report["summary"]
    assert "http" not in summary.lower() and "Traceback" not in summary
    assert summary == summary.strip() and len(summary) <= diagnose.SUMMARY_MAX_CHARS


def test_note_fields_are_never_empty_and_reuse_the_classification_advice() -> None:
    report = diagnose.run(
        [("good", works()), ("bad", raises(resilience.BlockedError("安全验证")))],
        allow_proxy=False, proxy_detected=False,
    )
    good, bad = report["rows"]
    assert good["note"] and bad["note"]
    assert bad["note"] == diagnose.classify_error(resilience.BlockedError("x"))[1]
    assert "验证" in bad["note"]


def test_since_converts_perf_counter_seconds_to_milliseconds(monkeypatch) -> None:
    monkeypatch.setattr(diagnose, "_now", lambda: 1_001.5)
    assert diagnose._since(1_000.0) == 1_500.0
    assert diagnose.MEASURED_FLOOR == 1.0, "floor is 1 ms, not 1 s or 1/1000 s"


def test_ms_field_is_really_milliseconds() -> None:
    """``rows[].ms`` must hold **milliseconds** -- the panel renders it as ms.

    ``ui/panel.tsx`` formats ``<1000`` as "N ms" and ``>=1000`` as "N.N s". A unit
    slip here is silent and wrong in the same breath: a 1.2s probe would read
    "1 ms" and a 12s timeout would read "12 ms", telling users the opposite of
    what the network did. Deliberately does *not* stub the clock, so this is the
    same measurement path the real self-check uses.
    """
    def slow():
        time.sleep(0.02)
        return [{"url": "https://x.cn/a", "title": "t", "snippet": "s"}]

    report = diagnose.run([("slow", slow)], allow_proxy=False, proxy_detected=False)
    assert report["rows"][0]["ms"] >= 20.0
    assert diagnose._seconds_text(report["rows"][0]["ms"]) != "不到 0.1" or \
        report["rows"][0]["ms"] < 100.0


def test_summary_renders_milliseconds_as_seconds() -> None:
    assert diagnose._seconds_text(2_500.0) == "2.5"
    assert diagnose._seconds_text(50.0) == "不到 0.1"
