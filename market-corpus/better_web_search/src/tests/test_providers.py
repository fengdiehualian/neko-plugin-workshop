"""Backend behavior tests: unwrapping, block detection, envelope parsing."""

from __future__ import annotations

import json
from collections.abc import Iterator

import conftest

providers = conftest.load("_providers")
parsing = conftest.load("_parsing")


def provider(name: str):
    return providers.PROVIDERS[name]


# --- link unwrapping ------------------------------------------------------


def test_unwrap_duckduckgo_reads_the_uddg_parameter() -> None:
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fproject-neko.cn%2F&rut=abc"
    assert providers.unwrap_ddg(href) == "https://project-neko.cn/"


def test_unwrap_duckduckgo_keeps_a_literal_percent_in_the_target() -> None:
    # parse_qs already percent-decodes, so the destination is decoded exactly
    # once: %252F becomes %2F, not a bare "/".
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%252Fb"
    assert providers.unwrap_ddg(href) == "https://example.com/a%2Fb"


def test_unwrap_duckduckgo_leaves_foreign_links_alone() -> None:
    assert providers.unwrap_ddg("https://example.com/x") == "https://example.com/x"


def test_unwrap_bing_decodes_the_a1_base64_target() -> None:
    href = "https://www.bing.com/ck/a?!&p=deadbeef&u=a1aHR0cHM6Ly9wcm9qZWN0LW5la28uY24v&ntb=1"
    assert providers.unwrap_bing(href) == "https://project-neko.cn/"


def test_unwrap_bing_passes_through_plain_links() -> None:
    assert providers.unwrap_bing("/search?q=x") == "https://www.bing.com/search?q=x"


def test_unwrap_sogou_resolves_root_relative_and_drops_non_http() -> None:
    assert providers.unwrap_sogou("/link?url=abc").startswith("https://www.sogou.com/link")
    assert providers.unwrap_sogou("javascript:alert(1)") == ""


# --- engine self-links ----------------------------------------------------


def test_engine_results_pages_are_recognised_but_content_pages_are_not() -> None:
    baidu = provider("baidu")
    assert providers._is_engine_results_page(baidu, "https://www.baidu.com/s?wd=x")
    assert providers._is_engine_results_page(baidu, "https://www.baidu.com/s?rtt=1&word=x")
    assert providers._is_engine_results_page(baidu, "https://www.baidu.com/")
    assert not providers._is_engine_results_page(baidu, "https://baike.baidu.com/item/n/1915")
    assert not providers._is_engine_results_page(baidu, "https://zhuanlan.zhihu.com/p/1")
    bing = provider("bing")
    assert providers._is_engine_results_page(bing, "https://www.bing.com/search?q=x")
    assert not providers._is_engine_results_page(bing, "https://github.com/a/b")
    assert not providers._is_engine_results_page(bing, "https://example.com/")
    duck = provider("duckduckgo")
    assert providers._is_engine_results_page(duck, "https://duckduckgo.com/html/?q=x")


def test_unresolved_redirect_wrappers_are_detected() -> None:
    """Only wrappers that cannot be opened are dropped.

    Baidu's ``/link?url=`` is measured to answer ``302`` with a real ``Location``
    once a BAIDUID cookie exists, and ``_net`` follows redirects -- dropping it
    discarded every baidu card. The JS-shell case is caught by ``_check_block``
    instead, so the two must not be conflated here.
    """
    assert not providers._is_unresolved_redirect("https://www.baidu.com/link?url=abc")
    assert providers._is_unresolved_redirect("https://cn.bing.com/ck/a?u=a1")
    assert providers._is_unresolved_redirect("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fx.cn")
    assert providers._is_unresolved_redirect("https://www.sogou.com/link?url=abc")
    assert not providers._is_unresolved_redirect("https://example.com/page")


# --- block / empty classification ----------------------------------------


def test_challenge_page_raises_blocked_instead_of_empty_success() -> None:
    import pytest

    with pytest.raises(providers.BlockedError):
        providers._check_block(provider("baidu"), "百度安全验证", 200)
    with pytest.raises(providers.BlockedError):
        providers._check_block(provider("duckduckgo"), "<form id='anomaly-modal'>", 200)


def test_rate_limit_statuses_raise_blocked() -> None:
    import pytest

    for status in (403, 429, 202):
        with pytest.raises(providers.BlockedError):
            providers._check_block(provider("bing"), "<html>ok</html>", status)


def test_redirect_shell_raises_blocked_not_empty() -> None:
    import pytest

    with pytest.raises(providers.BlockedError):
        providers._check_block(provider("baidu"), conftest.fixture("baidu_redirect_shell.html"), 200)


def test_a_normal_page_neither_blocks_nor_raises() -> None:
    providers._check_block(provider("bing"), conftest.fixture("bing_html.html"), 200)


def test_block_word_inside_a_real_result_does_not_trigger() -> None:
    # A snippet may legitimately contain the word 验证.
    providers._check_block(provider("baidu"), "<div>" + "验证码登录" + "</div>" + "x" * 9000, 200)


# --- hosted API envelopes ------------------------------------------------


def test_anysearch_keyed_401_is_a_verdict_on_the_key(monkeypatch) -> None:
    """With a key, 401/403 says the credential is bad; anonymously it says nothing."""
    import pytest

    def refuse(status: int):
        def post(url, **kwargs):
            raise providers._net.HttpStatusCodeError("denied", status)
        return post

    monkeypatch.setattr(providers._net, "post", refuse(401))
    with pytest.raises(providers.ApiKeyRejectedError):
        providers.search_anysearch("q", 3, timeout=5, policy="none", proxy_url="",
                                   api_key="as-secret")
    with pytest.raises(providers.SearchProviderError) as info:
        providers.search_anysearch("q", 3, timeout=5, policy="none", proxy_url="", api_key="")
    assert not isinstance(info.value, providers.ApiKeyRejectedError)
    assert "as-secret" not in str(info.value)


def test_anysearch_429_stays_a_throttle_and_keeps_the_wait_time(monkeypatch) -> None:
    import pytest

    def post(url, **kwargs):
        raise providers._net.HttpStatusCodeError("slow down", 429, 7.5)

    monkeypatch.setattr(providers._net, "post", post)
    with pytest.raises(providers.BlockedError) as info:
        providers.search_anysearch("q", 3, timeout=5, policy="none", proxy_url="",
                                   api_key="as-secret")
    assert info.value.retry_after_seconds == 7.5


def test_anysearch_envelope_is_parsed_and_invalid_shapes_rejected() -> None:
    payload = {"results": [
        {"title": "猫娘计划", "url": "https://project-neko.cn/", "snippet": "开源 AI 伙伴",
         "content": "完整正文"},
        {"title": "坏数据", "url": "javascript:alert(1)", "snippet": "x"},
    ]}
    results = providers._json_results(payload, 5)
    assert len(results) == 1, results
    assert results[0]["url"] == "https://project-neko.cn/"
    # snippet stays short for the summary; content keeps the full page text.
    assert results[0]["content"] == "完整正文"
    assert results[0]["snippet"] == "开源 AI 伙伴"


def test_exa_advanced_json_envelope_is_parsed() -> None:
    blob = json.dumps({"requestId": "x", "results": [
        {"id": "https://project-neko.cn/", "url": "https://project-neko.cn/",
         "title": "猫娘计划 Project N.E.K.O.", "text": "听见你的心情\n看见你的世界",
         "publishedDate": "2026-09-17T03:09:21.000Z"},
    ]})
    results = providers._json_results(json.loads(blob), 5)
    assert results[0]["title"] == "猫娘计划 Project N.E.K.O."
    assert results[0]["published"].startswith("2026-09-17")
    assert "听见你的心情" in results[0]["content"]


def test_exa_text_fallback_splits_on_title_blocks() -> None:
    blob = (
        "Title: 猫娘计划 Project N.E.K.O.\nURL: https://project-neko.cn/\n"
        "Published: N/A\nAuthor: N/A\nHighlights:\n猫娘计划 是开源 AI 伙伴\n...\n"
        "更多正文\n\nTitle: Second\nURL: https://example.com/\nHighlights:\n第二结果\n"
    )
    results = providers._text_results(blob, 5)
    assert len(results) == 2, results
    assert results[0]["url"] == "https://project-neko.cn/"
    assert "开源 AI 伙伴" in results[0]["snippet"]
    assert results[1]["url"] == "https://example.com/"


def test_text_fallback_ignores_blocks_without_a_real_url() -> None:
    assert providers._text_results("Title: x\nURL: /relative\nHighlights: y", 5) == []


def test_mcp_sse_payload_is_extracted(monkeypatch) -> None:
    body = (
        ": hi\r\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,"
        "\"result\":{\"content\":[{\"type\":\"text\",\"text\":\"{\\\"results\\\":[]}\"}]}}\r\n"
    ).encode("utf-8")

    class Fake:
        status = 200
        url = "https://mcp.exa.ai/mcp"
        headers: dict[str, str] = {}
        retry_after_seconds = None
        content_type = ""
        def __init__(self, payload: bytes) -> None:
            self.body = payload
        def header(self, name: str) -> str:
            return ""

    monkeypatch.setattr(providers._net, "post", lambda *a, **k: Fake(body))
    result = providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                                 policy="none", proxy_url="")
    assert result["content"][0]["type"] == "text"


def test_mcp_bad_key_is_classified_and_sent_only_in_a_header(monkeypatch) -> None:
    import pytest

    body = (
        'data: {"result":{"content":[{"type":"text","text":"'
        'web_search_exa error (401): Invalid API key\\nTimestamp: now"}],'
        '"isError":true}}\n'
    ).encode("utf-8")
    captured_url = ""
    captured_headers: dict[str, str] = {}

    class Fake:
        status = 200
        retry_after_seconds = None

        def __init__(self, payload: bytes) -> None:
            self.body = payload

    def post(url, **kwargs):
        nonlocal captured_url, captured_headers
        captured_url = url
        captured_headers = kwargs["headers"]
        return Fake(body)

    monkeypatch.setattr(providers._net, "post", post)
    with pytest.raises(providers.ApiKeyRejectedError, match="Invalid API key"):
        providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                            policy="none", proxy_url="", api_key="bad-secret")
    assert captured_headers["x-api-key"] == "bad-secret"
    assert "bad-secret" not in captured_url
    assert "exaApiKey" not in captured_url


def test_mcp_rate_limit_becomes_blocked(monkeypatch) -> None:
    import pytest

    class Fake:
        status = 429
        url = "u"
        headers: dict[str, str] = {}
        body = b"too many"
        retry_after_seconds = None
        content_type = ""
        def header(self, name: str) -> str:
            return ""

    monkeypatch.setattr(providers._net, "post", lambda *a, **k: Fake())
    with pytest.raises(providers.BlockedError):
        providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                            policy="none", proxy_url="")


def test_mcp_http_402_becomes_quota_exhausted(monkeypatch) -> None:
    import pytest

    class Fake:
        status = 402
        body = b"payment required"
        retry_after_seconds = 18.0

    monkeypatch.setattr(providers._net, "post", lambda *a, **k: Fake())
    with pytest.raises(providers.QuotaExhaustedError) as caught:
        providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                            policy="none", proxy_url="", api_key="key")
    assert caught.value.retry_after_seconds == 18.0


def test_mcp_keyed_rate_limit_stays_a_throttle_not_a_spent_key(monkeypatch) -> None:
    """429 means "ask slower"; only 402 may ever mark a key as out of credits.

    A keyed 429 that raised ``QuotaExhaustedError`` would let one double-click
    report a healthy key as spent, which is how key rotation mis-parks keys.
    """
    import pytest

    class Fake:
        status = 429
        body = b"too many"
        retry_after_seconds = 4.0

    monkeypatch.setattr(providers._net, "post", lambda *a, **k: Fake())
    with pytest.raises(providers.BlockedError) as caught:
        providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                            policy="none", proxy_url="", api_key="key")
    assert caught.value.retry_after_seconds == 4.0
    assert not isinstance(caught.value, providers.QuotaExhaustedError)


def test_mcp_keyed_credit_exhaustion_is_still_quota(monkeypatch) -> None:
    """The 402 half of the pair: this one *is* the key being out of credits."""
    import pytest

    class Fake:
        status = 402
        body = b"payment required"
        retry_after_seconds = None

    monkeypatch.setattr(providers._net, "post", lambda *a, **k: Fake())
    with pytest.raises(providers.QuotaExhaustedError):
        providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                            policy="none", proxy_url="", api_key="key")


def test_mcp_generic_error_text_is_not_misread_as_a_bad_key(monkeypatch) -> None:
    """Digits inside server text must not frame a server fault as the user's key.

    Classifying an unrelated failure as ``ApiKeyRejectedError`` makes the panel
    tell a user their key is invalid when it is fine, which is the one mistake
    that sends them back into signup instead of telling them to retry.
    """
    import pytest

    def sse(text: str) -> bytes:
        return (
            f'data: {{"result":{{"content":[{{"type":"text","text":"{text}"}}],'
            f'"isError":true}}}}\n'
        ).encode("utf-8")

    class Fake:
        def __init__(self, payload: bytes) -> None:
            self.body = payload
            self.status = 200
            self.retry_after_seconds = None

    monkeypatch.setattr(providers._net, "post",
                        lambda *a, **k: Fake(sse("upstream returned 1401 shards")))
    with pytest.raises(providers.SearchProviderError) as caught:
        providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                            policy="none", proxy_url="", api_key="good-key")
    assert not isinstance(caught.value, providers.ApiKeyRejectedError)
    assert not isinstance(caught.value, providers.QuotaExhaustedError)

    # ... while the real wording still classifies correctly.
    monkeypatch.setattr(providers._net, "post",
                        lambda *a, **k: Fake(sse("web_search_exa error (401): Invalid API key")))
    with pytest.raises(providers.ApiKeyRejectedError):
        providers._mcp_call("web_search_exa", {"query": "x"}, timeout=5,
                            policy="none", proxy_url="", api_key="bad-key")


def test_search_exa_auto_uses_the_fast_path_even_with_a_key(monkeypatch) -> None:
    calls: list[str] = []
    advanced_text = json.dumps({"results": [{"url": "https://example.com/a",
                                               "title": "A", "text": "body"}]})

    def fake_mcp(tool, arguments, **kwargs):
        calls.append(tool)
        if tool == "web_search_advanced_exa":
            return {"content": [{"type": "text", "text": advanced_text}]}
        return {"content": [{"type": "text", "text":
                              "Title: Simple\nURL: https://example.com/s\nSummary"}]}

    monkeypatch.setattr(providers, "_mcp_call", fake_mcp)
    simple = providers.search_exa("x", 1, timeout=5, policy="none", proxy_url="")
    keyed = providers.search_exa("x", 1, timeout=5, policy="none", proxy_url="", api_key="key")
    deep = providers.search_exa("x", 1, timeout=5, policy="none", proxy_url="", tool="advanced")
    assert simple[0]["url"] == "https://example.com/s"
    assert keyed[0]["url"] == "https://example.com/s"
    assert deep[0]["url"] == "https://example.com/a"
    assert calls == ["web_search_exa", "web_search_exa", "web_search_advanced_exa"]


def test_fetch_exa_passes_the_optional_key_to_mcp(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_mcp(tool, arguments, **kwargs):
        seen["tool"] = tool
        seen["key"] = kwargs["api_key"]
        return {"content": [{"type": "text", "text": "# Title\n正文内容"}]}

    monkeypatch.setattr(providers, "_mcp_call", fake_mcp)
    result = providers.fetch_exa("https://example.com", timeout=5, policy="none",
                                proxy_url="", max_chars=100, api_key="key")
    assert seen == {"tool": "web_fetch_exa", "key": "key"}
    assert result["title"] == "Title"


def test_advanced_fallback_does_not_swallow_key_rejection(monkeypatch) -> None:
    import pytest

    calls: list[str] = []

    def fake_mcp(tool, arguments, **kwargs):
        calls.append(tool)
        raise providers.ApiKeyRejectedError("Invalid API key")

    monkeypatch.setattr(providers, "_mcp_call", fake_mcp)
    with pytest.raises(providers.ApiKeyRejectedError):
        providers.search_exa("x", 1, timeout=5, policy="none", proxy_url="",
                             api_key="key", tool="advanced")
    assert calls == ["web_search_advanced_exa"]


# --- the full scrape pipeline, offline -----------------------------------


class _Resp:
    def __init__(self, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.body = body
        self.status = status
        self.url = "https://example.invalid"
        self.headers = {"Content-Type": ctype}
        self.retry_after_seconds = None
        self.content_type = "text/html"

    def header(self, name: str) -> str:
        return self.headers.get(name, "")


def test_search_html_unwraps_filters_ads_and_keeps_real_results(monkeypatch) -> None:
    duck = provider("duckduckgo")
    # The fixture's sponsored block carries result--ad; the provider must also
    # catch an unmarked one purely from the y.js target.
    markup = conftest.fixture("ddg_html.html").replace('class="result result--ad"', 'class="result"')
    monkeypatch.setattr(providers._net, "request", lambda *a, **k: _Resp(markup.encode("utf-8")))
    results = providers.search_html(duck, "猫娘计划", 5, timeout=5, policy="none",
                                    proxy_url="", method="GET")
    urls = [item["url"] for item in results]
    assert any("project-neko.cn" in url for url in urls), urls
    assert any("github.com/Project-N-E-K-O" in url for url in urls), urls
    assert not any("y.js" in url for url in urls), urls
    assert all(not url.startswith("//") for url in urls), urls


def test_search_html_drops_baidu_nav_and_keeps_the_card(monkeypatch) -> None:
    baidu = provider("baidu")
    markup = conftest.fixture("baidu_html.html")
    monkeypatch.setattr(providers._net, "request", lambda *a, **k: _Resp(markup.encode("utf-8")))
    monkeypatch.setattr(providers._net, "get", lambda *a, **k: _Resp(b""))
    results = providers.search_html(baidu, "猫娘计划", 5, timeout=5, policy="none", proxy_url="")
    urls = [item["url"] for item in results]
    titles = [item["title"] for item in results]
    assert not any("广告推广位" in t for t in titles), titles
    assert not any("相关搜索" in t for t in titles), titles
    assert any("百度百科" in t for t in titles), titles
    # Baidu wraps its cards in /link?url=, which resolves by redirect. Dropping it
    # used to make the whole backend report "no parseable results".
    assert any("/link?url=" in u for u in urls), urls


def test_search_html_reports_block_rather_than_empty(monkeypatch) -> None:
    import pytest

    baidu = provider("baidu")
    monkeypatch.setattr(providers._net, "request",
                        lambda *a, **k: _Resp("<title>百度安全验证</title>".encode("utf-8")))
    monkeypatch.setattr(providers._net, "get", lambda *a, **k: _Resp(b""))
    with pytest.raises(providers.BlockedError):
        providers.search_html(baidu, "x", 5, timeout=5, policy="none", proxy_url="")


def test_baidu_warmup_runs_once_until_baiduid_is_in_the_jar(monkeypatch) -> None:
    baidu = provider("baidu")
    class FakeJar:
        def __init__(self) -> None:
            self.cookies: list[object] = []

        def __iter__(self) -> Iterator[object]:
            return iter(self.cookies)

    jar = FakeJar()
    home_calls: list[str] = []
    search_calls: list[str] = []

    def fake_get(url, **kwargs):
        home_calls.append(url)
        jar.cookies.append(type("Cookie", (), {"name": "BAIDUID"})())
        return _Resp(b"home")

    def fake_request(method, url, **kwargs):
        search_calls.append(url)
        return _Resp(conftest.fixture("baidu_html.html").encode("utf-8"))

    monkeypatch.setattr(providers, "_jar_for", lambda name: jar)
    monkeypatch.setattr(providers._net, "get", fake_get)
    monkeypatch.setattr(providers._net, "request", fake_request)
    providers.search_html(baidu, "x", 5, timeout=5, policy="none", proxy_url="")
    providers.search_html(baidu, "x", 5, timeout=5, policy="none", proxy_url="")
    assert home_calls == [providers.BAIDU_HOME]
    assert len(search_calls) == 2


# --- SSRF guard -----------------------------------------------------------
def test_guard_allows_public_urls_and_adds_https() -> None:
    guard = conftest.load("_guard")
    assert guard.normalize_http_url("https://example.com/a?b=1") == "https://example.com/a?b=1"
    assert guard.normalize_http_url("example.com").startswith("https://")


def test_guard_blocks_local_metadata_and_bad_schemes() -> None:
    guard = conftest.load("_guard")
    for bad in ("http://127.0.0.1:8000/x", "http://localhost/x", "http://0.0.0.0/",
                "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/",
                "http://192.168.1.1/", "http://[::1]/", "http://2130706433/",
                "http://0x7f000001/", "http://admin.local/", "javascript:alert(1)",
                "ftp://example.com/", "http://user:pw@example.com/", "", "http://"):
        try:
            guard.normalize_http_url(bad)
        except guard.UnsafeUrlError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")


def test_guard_rejects_hostname_resolving_to_loopback() -> None:
    guard = conftest.load("_guard")
    try:
        guard.normalize_http_url("http://this-host-does-not-exist.invalid/")
    except guard.UnsafeUrlError:
        return  # DNS failure is a fine, safe answer
    raise AssertionError("unresolvable host should not be fetchable")
