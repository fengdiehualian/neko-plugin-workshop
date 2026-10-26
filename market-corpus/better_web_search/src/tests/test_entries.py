"""Offline tests for the W5 integration layer (``__init__.py``).

No host is started and no socket is ever opened: the plugin class is loaded via
``conftest.load`` (the venv can import ``plugin.sdk`` because N.E.K.O is on the
editable path), and every instance is built with ``object.__new__`` plus the few
attributes the tested code touches. Provider I/O is monkeypatched.
"""

from __future__ import annotations

import asyncio
import copy
import json

import conftest
import pytest

entries = conftest.load("__init__")
net = conftest.load("_net")
providers = conftest.load("_providers")
resilience = conftest.load("_resilience")

BetterWebSearchPlugin = entries.BetterWebSearchPlugin
ApiKeyRejectedError = resilience.ApiKeyRejectedError
QuotaExhaustedError = resilience.QuotaExhaustedError
SearchProviderError = resilience.SearchProviderError

# A realistic-shaped "secret": anything the plugin echoes must never contain it.
SECRET = "sk-exa-live-0123456789abcdef9b2c"
TAIL = SECRET[-4:]


class FakeLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def _record(self, message, *args) -> None:
        try:
            self.lines.append(str(message).format(*args))
        except Exception:
            self.lines.append(str(message))

    def info(self, message, *args, **_kw) -> None:
        self._record(message, *args)

    debug = warning = error = exception = log = info

    def blob(self) -> str:
        return "\n".join(self.lines)


class FakeConfig:
    """In-memory stand-in for ``self.config`` (host deep-merge + atomic write)."""

    def __init__(self, data: dict) -> None:
        self.data = data

    async def dump(self, *, timeout: float = 5.0) -> dict:
        return copy.deepcopy(self.data)

    async def update(self, patch, *, timeout: float = 5.0) -> dict:
        for section, values in dict(patch).items():
            if isinstance(values, dict):
                table = self.data.setdefault(section, {})
                table.update(values)
        return copy.deepcopy(self.data)


class FakeCtx:
    def __init__(self) -> None:
        self.statuses: list[dict] = []

    def update_status(self, status: dict) -> None:
        self.statuses.append(status)


class FakeStore:
    """The smallest thing [plugin.store] needs to look like: async get/set."""

    def __init__(self, *, enabled: bool = True, fail: bool = False) -> None:
        self.enabled = enabled
        self.fail = fail
        self.values: dict[str, object] = {}
        self.writes = 0

    async def get(self, key, default=None):
        if self.fail:
            raise OSError("store down")
        return entries.Ok(self.values.get(key, default))

    async def set(self, key, value):
        self.writes += 1
        if self.fail:
            raise OSError("store down")
        self.values[key] = value
        return entries.Ok(None)


def make_plugin(search: dict | None = None, *, net_: dict | None = None,
                ui_: dict | None = None, data: dict | None = None) -> BetterWebSearchPlugin:
    """A plugin instance with config sections set, no host, no real __init__."""
    if data is None:
        data = {
            "search": dict(search or {}),
            "net": dict(net_ or {}),
            "ui": dict(ui_ or {}),
        }
    plugin = object.__new__(BetterWebSearchPlugin)
    plugin.ctx = FakeCtx()
    plugin.logger = FakeLogger()
    plugin._cfg = {}
    plugin._retired_chain_noted = False
    plugin._sections = {name: {} for name in entries.CONFIG_SECTIONS}
    plugin._coordinators = {}
    plugin._key_state = "unknown"
    plugin._quota_state = ""
    plugin._any_key_state = "unknown"
    plugin._exa_last_error = ""
    plugin._key_health = {}
    plugin._key_at = ""
    plugin._key_at_saved = ""
    plugin.store = FakeStore()
    plugin._last_search = None
    plugin.config = FakeConfig(data)
    sections = plugin._sections
    for name in entries.CONFIG_SECTIONS:
        sections[name] = dict(data.get(name) or {})
    plugin._cfg = sections["search"]
    return plugin


def exa_results() -> list[dict]:
    return [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]


# ---------------------------------------------------------------------------
# C. effective chain (proxy-aware trimming)
# ---------------------------------------------------------------------------

def test_default_chain_matches_plan() -> None:
    plugin = make_plugin()
    assert plugin._chain() == ["anysearch", "exa", "bing", "baidu"]
    assert list(entries.DEFAULT_CHAIN) == ["anysearch", "exa", "bing", "baidu"]


def test_a_pinned_retired_default_chain_reads_as_unset() -> None:
    """The host copies config.example.toml once at install and never rewrites an existing
    file (config_paths.py:148-153), so an upgrader keeps pinning the order that was
    default on their install day and no changed default ever reaches them."""
    plugin = make_plugin({"backend_chain": list(entries.RETIRED_DEFAULT_CHAIN)})
    assert plugin._chain() == list(entries.DEFAULT_CHAIN)
    assert plugin._ordered_chain() == ["anysearch", "exa", "bing", "baidu"]
    plugin._chain()
    plugin._chain()
    assert sum("retired default" in line for line in plugin.logger.lines) == 1


def test_a_hand_written_order_stays_authoritative() -> None:
    # Only the exact retired default is discarded. Anything else -- including a
    # deliberately exa-first list -- is the user's own and must keep winning.
    plugin = make_plugin({"backend_chain": ["exa", "bing", "baidu"]})
    assert plugin._chain() == ["exa", "bing", "baidu"]


def test_the_backend_preference_outranks_the_retired_chain_alias() -> None:
    """Wanting Exa first has a first-class knob; un-pinning must not take it away."""
    plugin = make_plugin({"backend_chain": list(entries.RETIRED_DEFAULT_CHAIN),
                          "backend": "exa"})
    assert plugin._ordered_chain()[0] == "exa"


def test_only_an_unpaired_exa_pool_takes_the_head() -> None:
    """AnySearch leads unless the user registered Exa keys and has no AnySearch key."""
    assert make_plugin()._ordered_chain() == ["anysearch", "exa", "bing", "baidu"]
    assert make_plugin({"anysearch_api_key": "as-key"})._ordered_chain() == \
        ["anysearch", "exa", "bing", "baidu"]
    assert make_plugin({"exa_api_keys": [SECRET],
                        "anysearch_api_key": "as-key"})._ordered_chain() == \
        ["anysearch", "exa", "bing", "baidu"]
    assert make_plugin({"exa_api_keys": [SECRET]})._ordered_chain() == \
        ["exa", "anysearch", "bing", "baidu"]


def test_explicit_backend_preference_outranks_the_key_rule() -> None:
    """A hand-picked engine still leads even when an Exa pool would have promoted Exa."""
    plugin = make_plugin({"exa_api_keys": [SECRET], "backend": "bing"})
    assert plugin._ordered_chain() == ["bing", "anysearch", "exa", "baidu"]


def test_keyed_exa_is_actually_tried_first(monkeypatch) -> None:
    """The reported head and the engine the runtime knocks on must be the same one."""
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    attempted: list[str] = []
    plugin = make_plugin({"exa_api_keys": [SECRET]})
    _stub_backends(plugin, monkeypatch, {"exa"}, attempted)
    outcome = asyncio.run(plugin._search_with_fallback("猫娘计划", 3, "auto"))
    assert attempted == ["exa", "anysearch"]
    assert outcome["backend"] == "anysearch"


def test_duckduckgo_trimmed_without_any_proxy(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["exa", "duckduckgo", "anysearch", "bing"]})
    assert plugin._chain() == ["exa", "duckduckgo", "anysearch", "bing"]
    assert plugin._effective_chain() == ["exa", "anysearch", "bing"]


def test_duckduckgo_joins_with_system_proxy(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: True)
    plugin = make_plugin({"backend_chain": ["exa", "duckduckgo", "anysearch", "bing"]})
    assert plugin._effective_chain() == ["exa", "duckduckgo", "anysearch", "bing"]


def test_duckduckgo_joins_with_explicit_proxy_url(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["exa", "duckduckgo"],
                          "proxy_url": "http://127.0.0.1:7897"})
    assert plugin._effective_chain() == ["exa", "duckduckgo"]


def test_proxy_mode_holding_a_url_counts_as_proxy(monkeypatch) -> None:
    # plan §4.2: "手动设 proxy=<url> 后出现"
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["exa", "duckduckgo"],
                          "proxy": "http://127.0.0.1:7897"})
    assert plugin._effective_chain() == ["exa", "duckduckgo"]


def test_duckduckgo_needs_proxy_off_keeps_it(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["exa", "duckduckgo"],
                          "duckduckgo_needs_proxy": False})
    assert plugin._effective_chain() == ["exa", "duckduckgo"]


def test_searxng_still_requires_base_url() -> None:
    plugin = make_plugin({"backend_chain": ["exa", "searxng"]})
    assert plugin._effective_chain() == ["exa"]
    plugin = make_plugin({"backend_chain": ["exa", "searxng"],
                          "searxng_base_url": "http://127.0.0.1:8888"})
    assert plugin._effective_chain() == ["exa", "searxng"]


def test_forced_duckduckgo_without_proxy_explains_itself(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["exa", "duckduckgo"]})
    with pytest.raises(resilience.SearchProviderError) as info:
        asyncio.run(plugin._search_with_fallback("某个关键词", 3, "duckduckgo"))
    assert "代理" in str(info.value)


# ---------------------------------------------------------------------------
# C2. [search] backend is a preference; the per-call argument is the lock
# ---------------------------------------------------------------------------

def _stub_backends(plugin, monkeypatch, failing: set[str], attempted: list[str]) -> None:
    async def fake(name: str, query: str, limit: int, budget: float) -> dict:
        attempted.append(name)
        if name in failing:
            raise resilience.BlockedError("验证页")
        return {"results": exa_results(), "backend": name}

    monkeypatch.setattr(plugin, "_search_once", fake)


def test_configured_backend_is_promoted_in_the_reported_chain(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    # Deliberately not RETIRED_DEFAULT_CHAIN: that exact list now reads as unset, and
    # this test is about the [search] backend preference promotion, not about the alias.
    plugin = make_plugin({"backend": "bing",
                          "backend_chain": ["exa", "bing", "baidu", "anysearch"]})
    assert plugin._ordered_chain() == ["bing", "exa", "baidu", "anysearch"]
    assert plugin._effective_chain() == ["bing", "exa", "baidu", "anysearch"]


def test_configured_backend_keeps_falling_back(monkeypatch) -> None:
    """The status card may not promise an order the runtime does not run."""
    attempted: list[str] = []
    plugin = make_plugin({"backend": "bing", "backend_chain": ["bing", "exa"]})
    _stub_backends(plugin, monkeypatch, {"bing"}, attempted)
    outcome = asyncio.run(plugin._search_with_fallback("猫娘计划", 3, "auto"))
    assert attempted == ["bing", "exa"]
    assert outcome["backend"] == "exa"
    assert outcome["attempted"] == ["bing", "exa"]


def test_per_call_backend_still_locks_and_does_not_fall_back(monkeypatch) -> None:
    attempted: list[str] = []
    plugin = make_plugin({"backend": "auto", "backend_chain": ["bing", "exa"]})
    _stub_backends(plugin, monkeypatch, {"bing"}, attempted)
    with pytest.raises(resilience.BlockedError):
        asyncio.run(plugin._search_with_fallback("猫娘计划", 3, "bing"))
    assert attempted == ["bing"]


def test_startup_report_matches_the_order_the_runtime_uses(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend": "baidu", "backend_chain": ["exa", "anysearch", "baidu"]})
    outcome = asyncio.run(plugin.startup())
    payload = outcome.value if outcome.is_ok() else {}
    assert payload["chain"] == ["baidu", "exa", "anysearch"]
    assert payload["effective_chain"] == ["baidu", "exa", "anysearch"]


# ---------------------------------------------------------------------------
# C3. [search] max_results is the default the entry falls back to
# ---------------------------------------------------------------------------

def _capture_limit(plugin, monkeypatch) -> dict:
    seen: dict = {}

    async def fake(query: str, limit: int, backend: str) -> dict:
        seen["limit"] = limit
        return {"results": exa_results(), "backend": "exa"}

    monkeypatch.setattr(plugin, "_search_with_fallback", fake)
    return seen


def test_max_results_config_drives_the_default_limit(monkeypatch) -> None:
    plugin = make_plugin({"max_results": 9, "backend_chain": ["exa"]})
    seen = _capture_limit(plugin, monkeypatch)
    assert asyncio.run(plugin.search(query="猫娘计划")).is_ok()
    assert seen["limit"] == 9


def test_explicit_max_results_beats_the_configured_default(monkeypatch) -> None:
    plugin = make_plugin({"max_results": 9, "backend_chain": ["exa"]})
    seen = _capture_limit(plugin, monkeypatch)
    asyncio.run(plugin.search(query="猫娘计划", max_results=2))
    assert seen["limit"] == 2


def test_configured_max_results_is_clamped_like_the_argument(monkeypatch) -> None:
    plugin = make_plugin({"max_results": 999, "backend_chain": ["exa"]})
    seen = _capture_limit(plugin, monkeypatch)
    asyncio.run(plugin.search(query="猫娘计划"))
    assert seen["limit"] == 15


# ---------------------------------------------------------------------------
# C4. diagnose_network: the dual-path the panel asks for must really run
# ---------------------------------------------------------------------------

def _row_by_backend(rows: list[dict]) -> dict:
    return {str(row.get("backend")): row for row in rows}


def _stub_probes(plugin, monkeypatch, reachable: set[tuple[str, bool]]) -> list[tuple[str, bool]]:
    built: list[tuple[str, bool]] = []

    def fake_probe(name: str, query: str, limit: int, timeout: float, *,
                   force_proxy: bool):
        built.append((name, force_proxy))

        def probe() -> list[dict]:
            if (name, force_proxy) not in reachable:
                raise net.NetworkError("no route")
            return [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]

        return probe

    monkeypatch.setattr(plugin, "_probe", fake_probe)
    return built


def test_single_path_self_check_marks_the_proxy_column_as_not_attempted(monkeypatch) -> None:
    plugin = make_plugin({"backend_chain": ["exa", "bing"]})
    built = _stub_probes(plugin, monkeypatch, {("exa", False), ("bing", False)})
    outcome = asyncio.run(plugin.diagnose_network())
    assert outcome.is_ok()
    assert all(force is False for _name, force in built)
    rows = _row_by_backend(outcome.value["rows"])
    assert rows["exa"]["direct"] == "ok" and rows["exa"]["proxied"] == ""
    # duckduckgo is always probed: "why not DDG?" is the first thing users ask.
    assert "duckduckgo" in rows


def test_dual_path_self_check_probes_both_ways_and_names_the_proxy_only_engine(monkeypatch) -> None:
    plugin = make_plugin({"backend_chain": ["exa", "bing"]})
    built = _stub_probes(plugin, monkeypatch, {("exa", False), ("bing", True)})
    outcome = asyncio.run(plugin.diagnose_network(with_proxy=True))
    assert sorted(built) == [("bing", False), ("bing", True),
                             ("duckduckgo", False), ("duckduckgo", True),
                             ("exa", False), ("exa", True)]
    rows = _row_by_backend(outcome.value["rows"])
    assert rows["bing"]["direct"] != "ok" and rows["bing"]["proxied"] == "ok"
    assert "代理" in rows["bing"]["note"]
    assert "bing" in outcome.value["recommended_chain"]
    # Without a proxy path duckduckgo fails both ways, so it stays out of the advice.
    assert "duckduckgo" not in outcome.value["recommended_chain"]


def test_dual_path_self_check_doubles_the_probe_pool(monkeypatch) -> None:
    """8 backends x 2 paths at 7 s each cannot drain through 4 workers in 25 s."""
    seen: dict = {}

    def fake_run(probes, **kwargs) -> dict:
        seen.update(kwargs)
        return {"rows": [], "summary": "ok", "recommended_chain": []}

    monkeypatch.setattr(entries._diagnose, "run", fake_run)
    plugin = make_plugin({"backend_chain": ["exa", "bing", "baidu"]})
    monkeypatch.setattr(plugin, "_probe", lambda *a, **k: (lambda: []))

    asyncio.run(plugin.diagnose_network(with_proxy=True))
    assert seen["allow_proxy"] is True
    assert seen["max_workers"] == entries._diagnose.MAX_WORKERS * 2
    # chain + duckduckgo, each with its own proxy-path closure
    assert len(seen["proxied_probes"]) == 4

    asyncio.run(plugin.diagnose_network())
    assert seen["allow_proxy"] is False
    assert seen["max_workers"] == entries._diagnose.MAX_WORKERS
    assert seen["proxied_probes"] is None


# ---------------------------------------------------------------------------
# D. masking + key fallback (retry exactly once)
# ---------------------------------------------------------------------------

def test_mask_key_shapes() -> None:
    assert BetterWebSearchPlugin._mask_key(SECRET) == f"exa****{TAIL}"
    assert BetterWebSearchPlugin._mask_key("") == ""
    assert BetterWebSearchPlugin._mask_key("   ") == ""
    # A key this short must not be echoed even partially.
    assert BetterWebSearchPlugin._mask_key("abcd") == "exa****"
    # The prefix is what tells the two backends' keys apart on the panel.
    assert BetterWebSearchPlugin._mask_key(SECRET, "any") == f"any****{TAIL}"


def _install_key_error(monkeypatch, error, anonymous_ok=True):
    calls: list[str] = []

    def fake_search_exa(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
                        api_key="", tool="auto"):
        calls.append(api_key)
        if api_key:
            raise error
        if anonymous_ok:
            return exa_results()
        raise resilience.BlockedError("anonymous tier challenged")

    monkeypatch.setattr(providers, "search_exa", fake_search_exa)
    return calls


def test_invalid_key_retries_anonymous_exactly_once(monkeypatch) -> None:
    calls = _install_key_error(monkeypatch, ApiKeyRejectedError("web_search_exa error (401): Invalid API key"))
    plugin = make_plugin({"exa_api_keys": [SECRET], "exa_key_fallback_anonymous": True})
    results = plugin._fetcher("exa", "q", 3, 5.0)()
    assert results == exa_results()
    assert calls == [SECRET, ""]            # once keyed, once anonymous -- no more
    assert plugin._key_state == "invalid"
    assert SECRET not in plugin._exa_last_error
    assert SECRET not in plugin.logger.blob()


def test_quota_error_marks_quota_and_degrades(monkeypatch) -> None:
    calls = _install_key_error(monkeypatch, QuotaExhaustedError("402 Payment Required", 30.0))
    plugin = make_plugin({"exa_api_keys": [SECRET], "exa_key_fallback_anonymous": True})
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert calls == [SECRET, ""]
    assert plugin._quota_state == "exhausted"


def test_anonymous_retry_happens_only_once(monkeypatch) -> None:
    # Anonymous tier failing must NOT trigger a second retry -- it propagates
    # so the chain fallback moves to the next backend.
    calls = _install_key_error(monkeypatch, ApiKeyRejectedError("401"), anonymous_ok=False)
    plugin = make_plugin({"exa_api_keys": [SECRET], "exa_key_fallback_anonymous": True})
    with pytest.raises(resilience.BlockedError):
        plugin._fetcher("exa", "q", 3, 5.0)()
    assert len(calls) == 2


def test_valid_key_marks_state_and_no_retry(monkeypatch) -> None:
    calls = _install_key_error(monkeypatch, None or RuntimeError("never"))

    def good(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
             api_key="", tool="auto"):
        calls.append(api_key)
        assert api_key == SECRET and tool == "auto"
        return exa_results()

    monkeypatch.setattr(providers, "search_exa", good)
    plugin = make_plugin({"exa_api_keys": [SECRET]})
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert calls == [SECRET]
    assert plugin._key_state == "valid"


def _error_code_for(error: BaseException) -> str:
    return entries._error_code(error)


# ---------------------------------------------------------------------------
# D2. the Exa key ring: one spent account must not end the search
# ---------------------------------------------------------------------------

KEY_A = "sk-exa-pool-aaaa-1111"
KEY_B = "sk-exa-pool-bbbb-2222"
KEY_C = "sk-exa-pool-cccc-3333"

POOL = {"exa_api_keys": [KEY_A, KEY_B, KEY_C]}


def _finger(plugin, key: str) -> str:
    return BetterWebSearchPlugin._key_fingerprint(key)


def _install_pool(monkeypatch, failures: dict[str, Exception]):
    """Fake Exa that fails only for the keys named in ``failures``."""
    seen: list[str] = []

    def fake(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
             api_key="", tool="auto"):
        seen.append(api_key)
        error = failures.get(api_key)
        if error is not None:
            raise error
        return exa_results()

    monkeypatch.setattr(providers, "search_exa", fake)
    return seen


def test_spent_key_yields_to_the_next_one(monkeypatch) -> None:
    seen = _install_pool(monkeypatch, {KEY_A: QuotaExhaustedError("402 Payment Required")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert seen == [KEY_A, KEY_B]                   # B answered; the 402 never surfaced
    assert plugin._key_health[_finger(plugin, KEY_A)] == "exhausted"
    assert plugin._key_health[_finger(plugin, KEY_B)] == "ok"


def test_the_next_search_starts_where_the_last_one_succeeded(monkeypatch) -> None:
    """This is the whole point of the ring: nobody re-tries a key every single time."""
    seen = _install_pool(monkeypatch, {KEY_A: QuotaExhaustedError("402")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    plugin._fetcher("exa", "q", 3, 5.0)()
    seen.clear()
    assert plugin._fetcher("exa", "q2", 3, 5.0)() == exa_results()
    assert seen == [KEY_B]


def test_the_ring_wraps_back_to_the_first_key(monkeypatch) -> None:
    """No timers: going all the way round is the retry schedule."""
    seen = _install_pool(monkeypatch, {})
    plugin = make_plugin(POOL)
    plugin._key_at = _finger(plugin, KEY_C)         # C answered last time
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert seen == [KEY_C]                          # the pass starts at C and stops there
    plugin._fetcher("exa", "q2", 3, 5.0)()
    assert seen == [KEY_C, KEY_C]                   # still C: nothing forced it sideways


def test_a_key_that_failed_once_is_reached_again_after_a_full_pass(monkeypatch) -> None:
    seen = _install_pool(monkeypatch, {KEY_A: QuotaExhaustedError("402")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    for index in range(4):
        seen.clear()
        assert plugin._fetcher("exa", f"q{index}", 3, 5.0)() == exa_results()
    assert seen == [KEY_B]                          # B is still the only one that works
    assert plugin._key_at == _finger(plugin, KEY_B)


def test_keyed_429_rotates_without_blaming_the_key(monkeypatch) -> None:
    """Exa throttles per account, so the next account is a real way out.

    A 429 must not leave an "out of credits" verdict: one fast double-click would
    otherwise report a healthy account as spent.
    """
    seen = _install_pool(monkeypatch, {KEY_A: resilience.BlockedError("Exa 请求过于频繁（429）")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert seen == [KEY_A, KEY_B]
    assert _finger(plugin, KEY_A) not in plugin._key_health   # a throttle is no verdict
    assert plugin._key_health[_finger(plugin, KEY_B)] == "ok"
    assert plugin._quota_state == ""


def test_a_garbled_answer_rotates_without_blaming_the_key(monkeypatch) -> None:
    """Anything that answered is worth a step sideways -- 5xx included.

    The ring used to rotate only on 401/402/429, so one server-side hiccup ended
    Exa for that search while healthy accounts sat untried. A wrong answer is not
    a verdict on the key either: only 401/402 may leave a mark.
    """
    seen = _install_pool(monkeypatch, {
        KEY_A: SearchProviderError("Exa 返回了无法解析的响应")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert seen == [KEY_A, KEY_B]
    assert _finger(plugin, KEY_A) not in plugin._key_health


def test_a_hang_rotates_nothing_and_hands_exa_to_the_cooldown(monkeypatch) -> None:
    """No answer at all is nobody's key's fault, and stepping sideways cannot help.

    Each hang costs the per-backend timeout, and the first backend in the chain
    holds the entire 25 s budget: rotating would burn the pool and still leave
    nothing for anysearch / bing behind it. So one hang exits the pass as the one
    error class the coordinator cools down, and no key gets blamed or spent.
    """
    seen = _install_pool(monkeypatch, {KEY_A: net.NetworkError("请求超时")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B, KEY_C]})
    plugin._key_at = _finger(plugin, KEY_A)
    with pytest.raises(resilience.BlockedError) as info:
        plugin._fetcher("exa", "q", 3, 5.0)()
    assert str(info.value) == entries._MSG_EXA_UNREACHABLE
    assert seen == [KEY_A]                            # B and C never billed
    assert plugin._key_at == _finger(plugin, KEY_A)   # a hang moves nothing
    assert not plugin._key_health


def test_a_keyless_hang_cools_exa_down_too(monkeypatch) -> None:
    """A fresh install pays that timeout once, not once per search."""
    def time_out(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
                 api_key="", tool="auto"):
        raise net.NetworkError("网络不可达: timed out")

    monkeypatch.setattr(providers, "search_exa", time_out)
    plugin = make_plugin({})
    with pytest.raises(resilience.BlockedError):
        plugin._fetcher("exa", "q", 3, 5.0)()


def test_a_key_removed_outside_the_panel_loses_its_verdict(monkeypatch) -> None:
    """Hand-editing exa_api_keys skips the remove button's cleanup.

    The cards are rebuilt from the pool so the panel never showed the ghost, but
    the stale fingerprint stayed in memory for the life of the plugin.
    """
    _install_pool(monkeypatch, {})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    plugin._fetcher("exa", "q", 3, 5.0)()
    assert plugin._key_health == {_finger(plugin, KEY_A): "ok"}
    plugin._sections["search"]["exa_api_keys"] = [KEY_B]
    cards = plugin._build_panel_context({"exists": True})["exa_keys"]
    assert [card["fingerprint"] for card in cards] == [_finger(plugin, KEY_B)]
    assert cards[0]["state"] == "unknown"
    assert plugin._key_health == {}


def test_whole_ring_failed_falls_back_to_anonymous_only_when_told_to(monkeypatch) -> None:
    spent = {key: QuotaExhaustedError("402") for key in (KEY_A, KEY_B)}
    seen = _install_pool(monkeypatch, spent)
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B], "exa_key_fallback_anonymous": True})
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert seen == [KEY_A, KEY_B, ""]               # every key had its turn, then anonymous
    context = plugin._build_panel_context({"exists": True, "running": False})
    assert [card["state"] for card in context["exa_keys"]] == ["exhausted", "exhausted"]
    assert context["exa_key_state"] == "exhausted"


def test_ring_failed_with_the_switch_off_hands_exa_to_the_penalty_box(monkeypatch) -> None:
    """Default behaviour: a spent pool goes to the next backend, not to anonymous.

    It must arrive as ``BlockedError`` -- that class is what puts the backend in
    the coordinator cooldown, which is how "do not re-hammer a dead pool" holds.
    """
    spent = {key: QuotaExhaustedError("402") for key in (KEY_A, KEY_B)}
    seen = _install_pool(monkeypatch, spent)
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    with pytest.raises(resilience.BlockedError) as info:
        plugin._fetcher("exa", "q", 3, 5.0)()
    assert seen == [KEY_A, KEY_B]                   # no anonymous attempt at all
    assert "限流" in str(info.value)
    assert _error_code_for(info.value) == "BETTER_WEB_SEARCH_BLOCKED"


def test_a_single_spent_key_without_fallback_does_not_search_anonymously(monkeypatch) -> None:
    seen = _install_pool(monkeypatch, {KEY_A: ApiKeyRejectedError("401 Invalid API key")})
    plugin = make_plugin({"exa_api_keys": [KEY_A]})
    with pytest.raises(resilience.BlockedError):
        plugin._fetcher("exa", "q", 3, 5.0)()
    assert seen == [KEY_A]
    assert plugin._key_state == "invalid"
    assert SECRET not in plugin._exa_last_error     # fixed copy, never upstream text


def test_anonymous_tier_failing_still_propagates(monkeypatch) -> None:
    seen = _install_pool(monkeypatch, {KEY_A: QuotaExhaustedError("402"),
                                        "": resilience.BlockedError("Exa 免配额已用完（429）")})
    plugin = make_plugin({"exa_api_keys": [KEY_A], "exa_key_fallback_anonymous": True})
    with pytest.raises(resilience.BlockedError):
        plugin._fetcher("exa", "q", 3, 5.0)()
    assert seen == [KEY_A, ""]                      # exactly one anonymous try


def test_a_fresh_install_still_searchs_anonymously(monkeypatch) -> None:
    seen = _install_pool(monkeypatch, {})
    plugin = make_plugin()
    assert plugin._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert seen == [""]


def test_the_log_says_which_key_answered_without_naming_it(monkeypatch) -> None:
    """"Did the new key get used?" must be answerable from the logs.

    The host does not log payloads, and a pool of two looks identical to a pool of
    one from the outside. The fingerprint is what gets printed -- a hash, not the
    secret -- so "restart and check the ring starts where it stopped" is testable.
    """
    _install_pool(monkeypatch, {})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    plugin._fetcher("exa", "q", 3, 5.0)()
    blob = plugin.logger.blob()
    assert _finger(plugin, KEY_A) in blob
    assert KEY_A not in blob and KEY_B not in blob


def test_pool_never_leaks_a_key_into_panel_context_or_logs(monkeypatch) -> None:
    _install_pool(monkeypatch, {KEY_A: QuotaExhaustedError("402 Payment Required")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    plugin._fetcher("exa", "q", 3, 5.0)()
    context = plugin._build_panel_context({"exists": True, "running": False})
    dumped = json.dumps(context, ensure_ascii=False)
    for key in (KEY_A, KEY_B):
        assert key not in dumped
        assert key not in plugin.logger.blob()
    # The point of the pool -- which account answered -- is still visible.
    assert context["exa_keys"][0]["masked"].endswith(KEY_A[-4:])


def test_the_fallback_switch_is_a_panel_button_that_persists(monkeypatch) -> None:
    """The user asked for a control, not a config key they have to type."""
    data = {"search": {"exa_key_fallback_anonymous": False}, "net": {}, "ui": {}}
    plugin = make_plugin(data=data)
    result = asyncio.run(plugin.set_key_fallback(enabled=True))
    assert result.is_ok()
    assert data["search"]["exa_key_fallback_anonymous"] is True
    assert result.value["enabled"] is True
    assert plugin._build_panel_context({"exists": False})["key_fallback"] is True
    asyncio.run(plugin.set_key_fallback(enabled=False))
    assert data["search"]["exa_key_fallback_anonymous"] is False
    assert plugin._build_panel_context({"exists": False})["key_fallback"] is False


async def _search_once(plugin, query: str = "猫娘 官网"):
    """One real trip through the backend dispatch (coordinator included)."""
    outcome = await plugin._search_once("exa", query, 3, 20.0)
    return outcome["results"]


def test_the_ring_position_survives_a_reload(monkeypatch) -> None:
    """The reason the position is stored at all: a restart must not reset the ring."""
    seen = _install_pool(monkeypatch, {KEY_A: QuotaExhaustedError("402 Payment Required")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    store = plugin.store
    assert asyncio.run(_search_once(plugin)) == exa_results()
    assert seen == [KEY_A, KEY_B]
    assert store.values["exa_key_ring"] == {"at": _finger(plugin, KEY_B)}

    # A fresh instance (restart) restores it and never touches the spent key again.
    seen.clear()
    revived = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    revived.store = store
    asyncio.run(revived._load_key_ring())
    assert revived._key_at == _finger(plugin, KEY_B)
    assert revived._fetcher("exa", "q", 3, 5.0)() == exa_results()
    assert seen == [KEY_B]


def test_the_position_is_written_only_when_the_ring_actually_moves(monkeypatch) -> None:
    _install_pool(monkeypatch, {})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    for index in range(3):
        assert asyncio.run(_search_once(plugin, f"猫娘 官网 {index}")) == exa_results()
    assert plugin.store.writes == 1              # one key, three searches


def test_a_pooled_ring_moves_the_pointer_once_per_spent_key(monkeypatch) -> None:
    seen = _install_pool(monkeypatch, {KEY_A: QuotaExhaustedError("402"),
                                        KEY_B: QuotaExhaustedError("402")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B, KEY_C]})
    assert asyncio.run(_search_once(plugin)) == exa_results()
    assert seen == [KEY_A, KEY_B, KEY_C]
    assert plugin.store.values["exa_key_ring"] == {"at": _finger(plugin, KEY_C)}
    assert plugin.store.writes == 1


def test_a_missing_store_never_breaks_a_search(monkeypatch) -> None:
    """The KV layer is the host's; Exa must still answer when it is not there."""
    seen = _install_pool(monkeypatch, {})
    plugin = make_plugin({"exa_api_keys": [KEY_A]})
    plugin.store = FakeStore(enabled=False)
    assert asyncio.run(_search_once(plugin)) == exa_results()
    asyncio.run(plugin._save_key_ring())
    asyncio.run(plugin._load_key_ring())
    assert seen == [KEY_A]
    assert plugin.store.writes == 0


def test_a_failing_store_is_logged_and_ignored(monkeypatch) -> None:
    _install_pool(monkeypatch, {})
    plugin = make_plugin({"exa_api_keys": [KEY_A]})
    plugin.store = FakeStore(fail=True)
    assert asyncio.run(_search_once(plugin)) == exa_results()
    assert plugin._key_at == _finger(plugin, KEY_A)     # in-memory ring still advanced


def test_a_stored_key_that_was_removed_does_not_move_the_ring(monkeypatch) -> None:
    _install_pool(monkeypatch, {})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    plugin.store.values["exa_key_ring"] = {"at": "deadbeef"}
    asyncio.run(plugin._load_key_ring())
    assert plugin._key_at == ""                          # falls back to the first key
    assert plugin._key_start(plugin._exa_keys()) == 0


def test_removing_the_current_key_clears_the_pointer(monkeypatch) -> None:
    _install_pool(monkeypatch, {})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    plugin._key_at = plugin._key_at_saved = _finger(plugin, KEY_A)   # as a save would leave it
    plugin.store.values["exa_key_ring"] = {"at": _finger(plugin, KEY_A)}
    asyncio.run(plugin.remove_exa_key(fingerprint=_finger(plugin, KEY_A)))
    assert plugin._key_at == ""
    assert plugin.store.values["exa_key_ring"] == {"at": ""}


def test_a_verified_key_becomes_the_starting_point(monkeypatch) -> None:
    def good(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
             api_key="", tool="auto"):
        return exa_results()

    monkeypatch.setattr(providers, "search_exa", good)
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    plugin._key_at = _finger(plugin, KEY_A)
    asyncio.run(plugin.save_exa_key(api_key=KEY_C))
    assert plugin._key_at == _finger(plugin, KEY_C)
    assert plugin.store.values["exa_key_ring"] == {"at": _finger(plugin, KEY_C)}


def test_fingerprint_is_a_short_stable_hash_not_the_key() -> None:
    finger = BetterWebSearchPlugin._key_fingerprint(KEY_A)
    assert len(finger) == 8 and KEY_A not in finger
    assert finger == BetterWebSearchPlugin._key_fingerprint(KEY_A)
    assert finger != BetterWebSearchPlugin._key_fingerprint(KEY_B)


# ---------------------------------------------------------------------------
# E. panel context structure + no plaintext key anywhere
# ---------------------------------------------------------------------------

CONTEXT_KEYS = {
    "exa_keys", "exa_key_masked", "exa_key_source", "exa_key_state",
    "exa_last_error", "anysearch_key_masked", "anysearch_key_state",
    "chain", "effective_chain", "proxy_mode", "proxy_detected",
    "host_search", "ssrf_fake_ip", "key_fallback", "quota_note", "last_search",
}

KEY_CARD_KEYS = {"fingerprint", "masked", "state", "current"}


def test_panel_context_shape_and_mask(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin(
        {"exa_api_keys": [SECRET], "backend_chain": ["exa", "duckduckgo", "anysearch", "bing"]},
    )
    context = plugin._build_panel_context({"exists": True, "running": True})
    assert set(context) == CONTEXT_KEYS
    assert context["exa_key_masked"] == f"exa****{TAIL}"
    assert context["exa_key_source"] == "config"
    # Nothing has been tried yet, so the honest answer is "unknown", not "valid".
    assert context["exa_key_state"] == "unknown"
    assert [set(card) for card in context["exa_keys"]] == [KEY_CARD_KEYS]
    assert context["exa_keys"][0]["state"] == "unknown"
    assert context["exa_keys"][0]["current"] is True
    assert context["chain"] == ["exa", "duckduckgo", "anysearch", "bing"]
    assert context["effective_chain"] == ["exa", "anysearch", "bing"]
    assert context["proxy_mode"] == "auto"
    assert context["proxy_detected"] is False
    assert context["host_search"] == {"exists": True, "running": True}
    assert context["ssrf_fake_ip"] is True
    assert "$10" in context["quota_note"]
    assert SECRET not in json.dumps(context, ensure_ascii=False)


def test_panel_context_defaults_without_key(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin()
    context = plugin._build_panel_context({"exists": False, "running": False})
    assert context["exa_key_masked"] == ""
    assert context["exa_key_source"] == "none"
    assert context["chain"] == ["anysearch", "exa", "bing", "baidu"]
    assert context["last_search"] == {}


# ---------------------------------------------------------------------------
# E1. the panel's "last search" card: what the log line already knew
# ---------------------------------------------------------------------------

QUERY = "猫娘计划 第一集 什么时候 播出"


def _stub_chain(plugin, monkeypatch, outcome=None, error=None):
    async def fake(query: str, limit: int, backend: str):
        if error is not None:
            raise error
        return outcome

    monkeypatch.setattr(plugin, "_search_with_fallback", fake)


class _BoomControl:
    """Constructing it fails, exactly like a host whose API is down."""

    def __init__(self, *args, **kwargs):
        raise OSError("host api down")


def test_search_records_who_answered_and_how_long(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["bing", "exa"]})
    _stub_chain(plugin, monkeypatch, {
        "results": exa_results(), "backend": "exa", "attempted": ["bing", "exa"],
    })
    assert plugin._last_search is None
    assert asyncio.run(plugin.search(query=QUERY)).is_ok()

    record = plugin._last_search
    assert record["ok"] is True
    assert record["backend"] == "exa"
    assert record["count"] == 1
    assert record["requested"] == 6
    assert record["attempted"] == ["bing", "exa"]
    assert record["query_len"] == len(QUERY)
    assert record["ms"] >= 0
    assert record["at"].count(":") == 2                # a clock, not a timestamp blob
    assert record["message"] == "" and record["code"] == ""


def test_last_search_card_data_carries_no_query_text(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    monkeypatch.setattr(entries._host, "HostPluginControl", _BoomControl)
    plugin = make_plugin({"backend_chain": ["exa"]})
    _stub_chain(plugin, monkeypatch, {
        "results": exa_results(), "backend": "exa", "attempted": ["exa"],
    })
    asyncio.run(plugin.search(query=QUERY))
    context = asyncio.run(plugin.panel_context())
    assert context["last_search"]["backend"] == "exa"
    assert context["last_search"]["count"] == 1
    # The context goes through the host, and the host logs what it receives.
    assert QUERY not in json.dumps(context, ensure_ascii=False)
    assert "猫娘" not in json.dumps(context, ensure_ascii=False)


def test_blocked_backend_failure_is_recorded_with_the_copy_the_chat_got(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["bing"]})
    _stub_chain(plugin, monkeypatch, error=resilience.BlockedError("验证页"))
    outcome = asyncio.run(plugin.search(query=QUERY))
    assert outcome.is_err()

    record = plugin._last_search
    assert record["ok"] is False and record["count"] == 0
    assert "验证页" in record["message"]
    assert record["message"] == str(outcome.error)      # exactly what the chat was told
    assert record["code"] == entries._ERROR_CODES["blocked"]
    assert record["attempted"] == []                    # the chain never reported back
    assert QUERY not in json.dumps(record, ensure_ascii=False)


def test_empty_answers_keep_the_backends_that_were_walked(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["bing", "exa"]})
    _stub_chain(plugin, monkeypatch, {
        "results": [{"title": "T", "url": "not-a-url", "snippet": "s"}],
        "backend": "exa", "attempted": ["bing", "exa"],
    })
    outcome = asyncio.run(plugin.search(query=QUERY))
    assert outcome.is_err() and outcome.error.code == "BETTER_WEB_SEARCH_EMPTY"

    record = plugin._last_search
    assert record["ok"] is False
    assert record["attempted"] == ["bing", "exa"]       # "why nothing?" needs this line
    assert "没有搜索到结果" in record["message"]


def test_a_too_short_query_does_not_overwrite_the_last_real_search(monkeypatch) -> None:
    monkeypatch.setattr(net, "system_proxy_present", lambda: False)
    plugin = make_plugin({"backend_chain": ["exa"]})
    _stub_chain(plugin, monkeypatch, {
        "results": exa_results(), "backend": "exa", "attempted": ["exa"],
    })
    asyncio.run(plugin.search(query=QUERY))
    before = dict(plugin._last_search)
    assert asyncio.run(plugin.search(query="喵")).is_err()
    assert plugin._last_search == before



def test_panel_context_entry_returns_plain_dict_and_survives_host_failure(monkeypatch) -> None:
    # _host.HostPluginControl raising must not sink the context: it degrades to
    # an "unknown" host_search block (both contract keys still present).
    class Boom:
        def __init__(self, *args, **kwargs):
            raise OSError("host api down")

    monkeypatch.setattr(entries._host, "HostPluginControl", Boom)
    plugin = make_plugin({"exa_api_keys": [SECRET]})
    context = asyncio.run(plugin.panel_context())
    assert isinstance(context, dict)                      # context, not Ok()
    assert context["host_search"] == {"exists": False, "running": False}
    assert SECRET not in json.dumps(context, ensure_ascii=False)


def test_no_plaintext_key_in_any_return_or_log(monkeypatch) -> None:
    # Full search entry run: keyed exa dies on a 401-style error, anonymous
    # tier answers. Neither the Ok payload nor any logged line may contain the
    # key (the host logs plugin status/results verbatim).
    calls = _install_key_error(monkeypatch, ApiKeyRejectedError("web_search_exa error (401): Invalid API key"))
    plugin = make_plugin({"exa_api_keys": [SECRET], "backend_chain": ["exa"],
                          "exa_key_fallback_anonymous": True})
    outcome = asyncio.run(plugin.search(query="猫娘计划 官网", max_results=3))
    assert outcome.is_ok() and calls == [SECRET, ""]
    blob = json.dumps(outcome.value, ensure_ascii=False, default=str) + plugin.logger.blob()
    assert SECRET not in blob
    assert SECRET[:-4] not in blob                        # even a long prefix
    report = plugin.ctx.statuses
    assert all(SECRET not in json.dumps(item, default=str) for item in report)


# ---------------------------------------------------------------------------
# D/E. save/clear/test key round-trip (persist + self reload)
# ---------------------------------------------------------------------------

class _LostAckConfig(FakeConfig):
    """Host behaviour captured on a Steam install: the file lands, the ack does not.

    ``projectneko_server`` wrote the runtime config in 3 ms and then raised
    ``Config persistence response timed out; final persistence status is unknown``
    ~4.5 s later, which the plugin used to report as "配置目录不可写".
    """

    def __init__(self, data: dict, *, land: bool) -> None:
        super().__init__(data)
        self._land = land

    async def update(self, patch, *, timeout: float = 5.0) -> dict:
        if self._land:
            await super().update(patch)
        raise entries.TransportError(
            "failed to update runtime config: Config persistence response timed out; "
            "final persistence status is unknown")


def _lost_ack_plugin(*, land: bool) -> BetterWebSearchPlugin:
    data = {"search": {"backend_chain": ["exa"], "exa_api_keys": []},
            "net": {}, "ui": {}}
    plugin = make_plugin()
    plugin.config = _LostAckConfig(data, land=land)
    return plugin


def test_persist_accepts_a_write_whose_acknowledgement_was_lost() -> None:
    plugin = _lost_ack_plugin(land=True)
    assert asyncio.run(plugin._persist({"search": {"exa_api_keys": [SECRET]}})) is True
    assert plugin._list_in("search", "exa_api_keys", ()) == [SECRET]


def test_persist_still_fails_when_the_value_never_lands() -> None:
    plugin = _lost_ack_plugin(land=False)
    assert asyncio.run(plugin._persist({"search": {"exa_api_keys": [SECRET]}})) is False
    assert plugin._exa_keys() == []


def test_save_exa_key_reports_success_when_only_the_ack_is_lost(monkeypatch) -> None:
    plugin = _lost_ack_plugin(land=True)

    async def fake_verify(key: str):
        assert key == SECRET
        return True, 120, 3, "密钥可用：120 ms 返回 3 条结果", ""

    monkeypatch.setattr(plugin, "_verify_exa_key", fake_verify)
    outcome = asyncio.run(plugin.save_exa_key(api_key=SECRET))
    assert outcome.is_ok() and outcome.value["ok"] is True
    assert "没能保存" not in outcome.value["message"]
    assert SECRET not in json.dumps(outcome.value, ensure_ascii=False)


def test_save_exa_key_still_says_no_when_the_write_really_failed(monkeypatch) -> None:
    plugin = _lost_ack_plugin(land=False)

    async def fake_verify(key: str):
        return True, 120, 3, "密钥可用", ""

    monkeypatch.setattr(plugin, "_verify_exa_key", fake_verify)
    outcome = asyncio.run(plugin.save_exa_key(api_key=SECRET))
    assert outcome.is_ok()                        # the search itself still works
    assert outcome.value["ok"] is False           # but the key is not stored
    assert "没能保存" in outcome.value["message"]


def test_save_exa_key_persists_reloads_and_reports_masked(monkeypatch) -> None:
    def good(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
             api_key="", tool="auto"):
        assert api_key == "sk-new-key-abcd"
        assert tool in {"auto", "simple", "advanced"}
        return exa_results()

    monkeypatch.setattr(providers, "search_exa", good)
    data = {"search": {}, "net": {}, "ui": {}}
    plugin = make_plugin(data=data)
    result = asyncio.run(plugin.save_exa_key(api_key="sk-new-key-abcd"))
    assert result.is_ok()
    payload = result.value
    assert payload["ok"] is True
    assert payload["masked"] == "exa****abcd"
    assert payload["count"] == 1
    assert isinstance(payload["latency_ms"], int)
    assert data["search"]["exa_api_keys"] == ["sk-new-key-abcd"]   # written to disk
    assert plugin._cfg["exa_api_keys"] == ["sk-new-key-abcd"]      # and reloaded in-process
    assert plugin._key_state == "valid"
    assert "sk-new-key-abcd" not in json.dumps(payload)


def test_save_exa_key_bad_key_saves_but_marks_invalid(monkeypatch) -> None:
    def reject(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
               api_key="", tool="auto"):
        raise ApiKeyRejectedError("401 Invalid API key")

    monkeypatch.setattr(providers, "search_exa", reject)
    data = {"search": {}, "net": {}, "ui": {}}
    plugin = make_plugin(data=data)
    result = asyncio.run(plugin.save_exa_key(api_key=SECRET))
    payload = result.value
    assert payload["ok"] is False
    assert payload["key_state"] == "invalid"
    assert data["search"]["exa_api_keys"] == [SECRET]              # saved anyway (contract)
    assert SECRET not in json.dumps(payload)
    assert payload["masked"] == f"exa****{TAIL}"


def test_clear_and_test_exa_key_without_key(monkeypatch) -> None:
    monkeypatch.setattr(providers, "search_exa",
                        lambda *a, **k: pytest.fail("must not search without a key"))
    data = {"search": {"exa_api_keys": [SECRET]}, "net": {}, "ui": {}}
    plugin = make_plugin(data=data)
    cleared = asyncio.run(plugin.clear_exa_key())
    assert cleared.is_ok() and data["search"]["exa_api_keys"] == []
    assert plugin._cfg["exa_api_keys"] == [] and plugin._key_state == "unknown"
    tested = asyncio.run(plugin.test_exa_key())
    assert tested.is_ok() and tested.value["ok"] is False and tested.value["count"] == 0


def test_save_exa_key_rejects_empty(monkeypatch) -> None:
    plugin = make_plugin()
    assert asyncio.run(plugin.save_exa_key(api_key="  ")).is_err()


# ---------------------------------------------------------------------------
# D2. the single optional AnySearch key: same contract, no ring
# ---------------------------------------------------------------------------

ANY_SECRET = "as-live-0123456789abcdef9b2c"
ANY_TAIL = ANY_SECRET[-4:]


def _install_any_error(monkeypatch, error) -> list[str]:
    calls: list[str] = []

    def fake(query, limit, *, timeout, policy, proxy_url, api_key="", zone=""):
        calls.append(api_key)
        if api_key:
            raise error
        return exa_results()

    monkeypatch.setattr(providers, "search_anysearch", fake)
    return calls


def test_set_anysearch_key_saves_verifies_and_masks(monkeypatch) -> None:
    seen: list[str] = []

    def good(query, limit, *, timeout, policy, proxy_url, api_key="", zone=""):
        seen.append(api_key)
        return exa_results()

    monkeypatch.setattr(providers, "search_anysearch", good)
    data = {"search": {}, "net": {}, "ui": {}, "host": {}}
    plugin = make_plugin(data=data)
    payload = asyncio.run(plugin.set_anysearch_key(api_key=f"  {ANY_SECRET}  ")).value
    assert payload["ok"] is True
    assert seen == [ANY_SECRET]                              # trimmed before it is used
    assert data["search"]["anysearch_api_key"] == ANY_SECRET  # written to disk
    assert plugin._cfg["anysearch_api_key"] == ANY_SECRET     # and reloaded in-process
    assert payload["masked"] == f"any****{ANY_TAIL}"
    assert payload["key_state"] == "valid"
    assert ANY_SECRET not in json.dumps(payload, ensure_ascii=False)
    context = plugin._build_panel_context({})
    assert context["anysearch_key_masked"] == f"any****{ANY_TAIL}"
    assert context["anysearch_key_state"] == "valid"
    assert ANY_SECRET not in json.dumps(context, ensure_ascii=False)
    # The search itself must use what was just saved, not a second copy of nothing.
    assert plugin._anysearch_key() == ANY_SECRET


def test_set_anysearch_key_rejects_empty_and_keeps_the_old_one(monkeypatch) -> None:
    plugin = make_plugin({"anysearch_api_key": ANY_SECRET})
    monkeypatch.setattr(providers, "search_anysearch",
                        lambda *a, **k: pytest.fail("an empty draft must not be tested"))
    assert asyncio.run(plugin.set_anysearch_key(api_key="   ")).is_err()
    assert plugin._anysearch_key() == ANY_SECRET


def test_bad_anysearch_key_saves_but_marks_invalid(monkeypatch) -> None:
    _install_any_error(monkeypatch, ApiKeyRejectedError("AnySearch API Key 无效或已失效"))
    data = {"search": {}, "net": {}, "ui": {}, "host": {}}
    plugin = make_plugin(data=data)
    payload = asyncio.run(plugin.set_anysearch_key(api_key=ANY_SECRET)).value
    assert payload["ok"] is False
    assert payload["key_state"] == "invalid"
    assert data["search"]["anysearch_api_key"] == ANY_SECRET   # saved anyway (contract)
    assert ANY_SECRET not in json.dumps(payload, ensure_ascii=False)


def test_anysearch_rate_limit_never_blames_the_key(monkeypatch) -> None:
    """429 says the window is busy; a wrong badge would have users re-paste a good key."""
    _install_any_error(monkeypatch, resilience.BlockedError("AnySearch 请求受限（429）", 5))
    plugin = make_plugin({"anysearch_api_key": ANY_SECRET})
    plugin._any_key_state = "valid"
    payload = asyncio.run(plugin.test_anysearch_key()).value
    assert payload["ok"] is False
    assert plugin._any_key_state == "valid"
    assert "限流" in payload["message"]
    assert ANY_SECRET not in json.dumps(payload, ensure_ascii=False)


def test_anysearch_network_failure_leaves_the_verdict_alone(monkeypatch) -> None:
    _install_any_error(monkeypatch, net.NetworkError("网络不可达: timed out"))
    plugin = make_plugin({"anysearch_api_key": ANY_SECRET})
    payload = asyncio.run(plugin.test_anysearch_key()).value
    assert payload["ok"] is False
    assert plugin._any_key_state == "unknown"                  # never adjudicated
    assert "网络" in payload["message"] or "测试未完成" in payload["message"]


def test_anysearch_search_sends_the_saved_key(monkeypatch) -> None:
    seen: list[str] = []

    def good(query, limit, *, timeout, policy, proxy_url, api_key="", zone=""):
        seen.append(api_key)
        return exa_results()

    monkeypatch.setattr(providers, "search_anysearch", good)
    plugin = make_plugin({"anysearch_api_key": ANY_SECRET, "anysearch_zone": "cn"})
    results = plugin._fetcher("anysearch", "q", 3, 5.0)()
    assert results == exa_results()
    assert seen == [ANY_SECRET]


def test_clear_anysearch_key_returns_to_the_anonymous_tier(monkeypatch) -> None:
    data = {"search": {"anysearch_api_key": ANY_SECRET}, "net": {}, "ui": {}, "host": {}}
    plugin = make_plugin(data=data)
    plugin._any_key_state = "valid"
    result = asyncio.run(plugin.clear_anysearch_key())
    assert result.is_ok()
    assert data["search"]["anysearch_api_key"] == ""
    assert plugin._cfg["anysearch_api_key"] == ""
    assert plugin._any_key_state == "unknown"
    assert plugin._build_panel_context({})["anysearch_key_state"] == "none"


def test_test_anysearch_key_without_a_key_says_so(monkeypatch) -> None:
    monkeypatch.setattr(providers, "search_anysearch",
                        lambda *a, **k: pytest.fail("must not search without a key"))
    payload = asyncio.run(make_plugin().test_anysearch_key()).value
    assert payload["ok"] is False
    assert payload["key_state"] == "none"
    assert payload["masked"] == ""


def test_adding_a_key_that_is_already_there_does_not_double_it(monkeypatch) -> None:
    def good(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
             api_key="", tool="auto"):
        return exa_results()

    monkeypatch.setattr(providers, "search_exa", good)
    data = {"search": {"exa_api_keys": [SECRET]}, "net": {}, "ui": {}}
    plugin = make_plugin(data=data)
    result = asyncio.run(plugin.save_exa_key(api_key=SECRET))
    assert result.is_ok()
    assert data["search"]["exa_api_keys"] == [SECRET]
    assert result.value["key_count"] == 1


def test_remove_exa_key_drops_only_that_one(monkeypatch) -> None:
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    finger = plugin._key_fingerprint(KEY_A)
    plugin._key_health[finger] = "exhausted"
    result = asyncio.run(plugin.remove_exa_key(fingerprint=finger))
    assert result.is_ok()
    assert plugin._exa_keys() == [KEY_B]
    assert not plugin._key_health                     # its record went with it
    assert result.value["key_count"] == 1


def test_remove_exa_key_rejects_an_unknown_fingerprint(monkeypatch) -> None:
    plugin = make_plugin({"exa_api_keys": [KEY_A]})
    assert asyncio.run(plugin.remove_exa_key(fingerprint="deadbeef")).is_err()
    assert plugin._exa_keys() == [KEY_A]


def test_test_exa_key_reports_each_key_separately(monkeypatch) -> None:
    _install_pool(monkeypatch, {KEY_B: QuotaExhaustedError("402 Payment Required")})
    plugin = make_plugin({"exa_api_keys": [KEY_A, KEY_B]})
    result = asyncio.run(plugin.test_exa_key())
    payload = result.value
    assert payload["ok"] is True and payload["usable"] == 1 and payload["key_count"] == 2
    assert [row["kind"] for row in payload["keys"]] == ["", "quota"]
    assert [row["masked"] for row in payload["keys"]] == [
        plugin._mask_key(KEY_A), plugin._mask_key(KEY_B)]
    assert KEY_A not in json.dumps(payload, ensure_ascii=False)
    assert KEY_B not in json.dumps(payload, ensure_ascii=False)
    cards = plugin._build_panel_context({"exists": True})["exa_keys"]
    assert [card["state"] for card in cards] == ["ok", "exhausted"]


def test_a_network_failure_during_retest_leaves_the_key_verdict_alone(monkeypatch) -> None:
    def time_out(query, limit, *, timeout, policy, proxy_url, live_crawl=False,
                 api_key="", tool="auto"):
        raise net.NetworkError("连接超时")

    monkeypatch.setattr(providers, "search_exa", time_out)
    plugin = make_plugin({"exa_api_keys": [KEY_A]})
    asyncio.run(plugin.test_exa_key())
    assert not plugin._key_health            # unreachable Exa says nothing about the key
    assert plugin._exa_key_cards()[0]["state"] == "unknown"


# ---------------------------------------------------------------------------
# F. host search status read (read-only: this plugin never switches it)
# ---------------------------------------------------------------------------

class FakeControl:
    calls: list[tuple] = []

    def __init__(self, base_url: str = "", timeout: float = 4.0):
        self.timeout = timeout

    def status(self, plugin_id: str):
        FakeControl.calls.append(("status", plugin_id))
        return entries._host.HostPluginState(plugin_id, True, True, {})


def test_the_write_path_stays_deleted() -> None:
    """Nothing here may start or stop a host plugin -- that is the user's click.

    Pressing the host's toggle from the panel meant POSTing
    ``/plugin/web_search/stop``, which a host reloading its plugin list answers
    8+ seconds late -- later than our own client waited, so a change that had
    already landed came back as "未能连接宿主管理接口". Nineteen starts on a real
    install (v0.2.0 through v0.9.6) logged exactly that; not one was a real
    failure. Re-checking it in a background tick then had to disarm itself once
    the stop was confirmed, which left a built-in that came back later never
    re-stopped. Removing the write removes both failure modes.
    """
    assert not hasattr(entries.BetterWebSearchPlugin, "set_host_search")
    assert not hasattr(entries.BetterWebSearchPlugin, "watch_host_takeover")
    assert not hasattr(entries.BetterWebSearchPlugin, "_host_set_enabled_sync")
    assert not hasattr(entries._host.HostPluginControl, "set_enabled")
    assert "host" not in entries.CONFIG_SECTIONS


def test_startup_never_calls_the_host_management_api(monkeypatch) -> None:
    """Boot must not spend the host's 10 s startup budget on a management call."""
    def never(*_args, **_kw):
        raise AssertionError("startup must not talk to the host management API")

    monkeypatch.setattr(entries._host, "HostPluginControl", never)
    plugin = make_plugin()
    assert asyncio.run(plugin.startup()).is_ok()      # boots anyway, searches anyway


def test_get_host_search_only_reads(monkeypatch) -> None:
    FakeControl.calls = []
    monkeypatch.setattr(entries._host, "HostPluginControl", FakeControl)
    plugin = make_plugin()
    got = asyncio.run(plugin.get_host_search())
    assert got.is_ok() and got.value["running"] is True and got.value["exists"] is True
    assert FakeControl.calls == [("status", entries._host.BUILTIN_SEARCH_PLUGIN_ID)]
    assert "toggleable" not in got.value


def test_a_dead_management_api_degrades_the_read_instead_of_raising(monkeypatch) -> None:
    class Dead:
        def __init__(self, *a, **k):
            raise OSError("connection refused")

    monkeypatch.setattr(entries._host, "HostPluginControl", Dead)
    plugin = make_plugin()
    result = asyncio.run(plugin.get_host_search())
    assert result.is_ok() and result.value["ok"] is False
    assert "插件中心" in result.value["message"]


def test_panel_context_caches_the_host_read_so_refreshes_stop_hanging(monkeypatch) -> None:
    """Every action ends with a context refresh; it must not re-pay the round trip."""
    reads = []

    def fake_sync(self, timeout=4.0):
        reads.append(timeout)
        return {"exists": True, "running": False}

    monkeypatch.setattr(entries.BetterWebSearchPlugin, "_host_search_state_sync", fake_sync)
    plugin = make_plugin()
    asyncio.run(plugin.panel_context())
    asyncio.run(plugin.panel_context())
    asyncio.run(plugin.panel_context())
    assert len(reads) == 1, f"live reads per refresh: {reads}"

    # A failed read must never age into "truth".
    plugin._host_state_cache = None
    monkeypatch.setattr(entries.BetterWebSearchPlugin, "_host_search_state_sync",
                        lambda self, timeout=4.0: {"exists": False, "running": False,
                                                   "error": "boom"})
    asyncio.run(plugin.panel_context())
    assert len(reads) == 1



def test_search_logs_which_backend_answered(monkeypatch) -> None:
    """The host does not log tool payloads, so the plugin has to remember itself."""
    plugin = make_plugin({"backend_chain": ["exa"]})

    async def fake(query, limit, backend):
        return {"results": exa_results(), "backend": "exa", "attempted": ["bing", "exa"]}

    monkeypatch.setattr(plugin, "_search_with_fallback", fake)
    asyncio.run(plugin.search(query="守望先锋 最可爱角色"))
    blob = plugin.logger.blob()
    assert "search answered: backend=exa count=1" in blob
    assert "attempted=['bing', 'exa']" in blob



# ---------------------------------------------------------------------------
# G. first-run notice
# ---------------------------------------------------------------------------

def test_first_run_notice_pushes_once_and_marks_sent() -> None:
    data = {"search": {}, "net": {}, "ui": {}}
    plugin = make_plugin(data=data)
    pushes: list[dict] = []
    plugin.push_message = lambda **kw: pushes.append(kw) or {"submitted": True}

    first = asyncio.run(plugin.startup())
    assert first.is_ok()
    assert len(pushes) == 1
    push = pushes[0]
    assert push["ai_behavior"] == "respond" and push["visibility"] == []
    assert push["parts"][0]["type"] == "text"
    assert data["ui"]["first_run_notice_sent"] is True

    second = asyncio.run(plugin.startup())            # config_change also re-runs startup
    assert second.is_ok() and len(pushes) == 1        # never nags twice
    # Ok payload exposes chain vs effective_chain and never the raw key material.
    assert set(second.value) >= {"chain", "effective_chain"}


def test_first_run_notice_survives_a_failing_push() -> None:
    def boom(**_kw):
        raise RuntimeError("bus down")

    plugin = make_plugin(data={"search": {}, "net": {}, "ui": {}})
    plugin.push_message = boom
    result = asyncio.run(plugin.startup())
    assert result.is_ok()                             # push failure must not sink startup
    assert plugin._sections["ui"].get("first_run_notice_sent") is not True  # retry next boot


# ---------------------------------------------------------------------------
# A. multi-section config tolerance
# ---------------------------------------------------------------------------

def test_missing_sections_fall_back_to_defaults() -> None:
    plugin = object.__new__(BetterWebSearchPlugin)
    plugin.logger = FakeLogger()
    plugin._cfg = {}
    plugin._sections = {name: {} for name in entries.CONFIG_SECTIONS}
    plugin._coordinators = {}
    # Host may hand us anything; the getters must not raise on absent keys.
    assert plugin._flag_in("net", "nope", True) is True
    assert plugin._text_in("ui", "no_such_key") == ""
    assert plugin._list_in("net", "ssrf_allow_ranges", entries.DEFAULT_SSRF_ALLOW_RANGES) == ["198.18.0.0/15"]
    assert plugin._ssrf_ranges() == ["198.18.0.0/15"]
    plugin._sections["net"] = {"ssrf_allow_ranges": []}
    assert plugin._ssrf_ranges() == []              # explicit empty = opt out


def test_tolerant_bool_and_text_parsers() -> None:
    plugin = make_plugin(search={})
    plugin._sections["ui"] = {"a": "true", "b": 0, "c": "off"}
    assert plugin._flag_in("ui", "a", False) is True
    assert plugin._flag_in("ui", "b", True) is False
    assert plugin._flag_in("ui", "c", True) is False
    assert plugin._flag_in("ui", "missing", True) is True
    assert plugin._text_in("ui", "b", "x") == "x"   # non-str -> default


def test_set_ssrf_guard_switch_round_trips() -> None:
    """The panel's one-click switch must actually change what fetch allows."""
    data = {"search": {}, "net": {}, "ui": {}}
    plugin = make_plugin(data=data)

    off = asyncio.run(plugin.set_ssrf_guard(enabled=False))
    assert off.is_ok() and off.value["enabled"] is False
    assert data["net"]["ssrf_allow_ranges"] == []
    assert plugin._ssrf_ranges() == []                       # reloaded, not stale
    assert asyncio.run(plugin.panel_context())["ssrf_fake_ip"] is False

    on = asyncio.run(plugin.set_ssrf_guard(enabled=True))
    assert on.is_ok() and on.value["ranges"] == list(entries.DEFAULT_SSRF_ALLOW_RANGES)
    assert asyncio.run(plugin.panel_context())["ssrf_fake_ip"] is True


def test_fetch_passes_configured_allow_ranges_to_guard(monkeypatch) -> None:
    """Wiring proof: an empty `[net].ssrf_allow_ranges` has to reach `_guard`.

    A switch that writes config but is never handed to the SSRF check is the same
    as no switch at all, and it fails silently for exactly the TUN-mode users it
    was built for. Providers are stubbed: this test must not touch the network.
    """
    seen: list[object] = []

    def fake_normalize(url: str, *, allow_ranges=()):
        seen.append(list(allow_ranges))
        return url

    def fake_fetch_direct(url: str, **_kwargs):
        return {"title": "Example", "content": "hello body", "final_url": url, "mode": "direct"}

    monkeypatch.setattr(entries._guard, "normalize_http_url", fake_normalize)
    monkeypatch.setattr(entries._providers, "fetch_direct", fake_fetch_direct)

    plugin = make_plugin(net_={"ssrf_allow_ranges": ["198.18.0.0/15"]})
    result = asyncio.run(plugin.fetch("https://example.com/", mode="direct"))

    assert result.is_ok(), result
    assert seen == [["198.18.0.0/15"]], seen
