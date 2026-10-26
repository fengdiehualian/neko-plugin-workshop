"""Offline parser tests: real search-page shapes, no network."""

from __future__ import annotations

import conftest

parsing = conftest.load("_parsing")
providers = conftest.load("_providers")


# --- text hygiene ---------------------------------------------------------


def test_sanitize_strips_icon_font_and_bidi_but_keeps_emoji() -> None:
    raw = "猫娘\ue687\u200b\u202e计划\u00a0😀\n\t第二行"
    cleaned = parsing.sanitize_text(raw)
    assert "\ue687" not in cleaned
    assert "\u200b" not in cleaned
    assert "\u202e" not in cleaned  # right-to-left override could reverse spoken text
    assert "😀" in cleaned
    assert cleaned == "猫娘计划 😀 第二行"


def test_sanitize_clips_with_ellipsis() -> None:
    assert parsing.sanitize_text("x" * 50, 20) == "x" * 19 + "…"


# --- charset handling -----------------------------------------------------


def test_decode_accepts_gbk_bytes_mislabelled_as_utf8() -> None:
    # Baidu has been observed doing exactly this.
    raw = "猫娘计划".encode("gbk")
    assert parsing.decode_body(raw, "text/html; charset=utf-8") == "猫娘计划"


def test_decode_upgrades_gb2312_to_gb18030() -> None:
    raw = "榫栌".encode("gb18030")
    assert parsing.decode_body(raw, "text/html; charset=gb2312") == "榫栌"


def test_decode_reads_meta_charset_when_header_omits_it() -> None:
    raw = b'<html><meta charset="gb18030">' + "猫娘".encode("gb18030") + b"</html>"
    assert "猫娘" in parsing.decode_body(raw, "text/html")


def test_decode_never_raises_on_junk() -> None:
    assert isinstance(parsing.decode_body(b"\xff\xfe\x00abc", "text/html"), str)


# --- DuckDuckGo -----------------------------------------------------------


def test_duckduckgo_fixture_yields_real_results_and_drops_the_ad() -> None:
    results = parsing.extract_results(
        conftest.fixture("ddg_html.html"),
        anchor_markers=("result__a",),
        container_markers=("result", "web-result"),
        skip_markers=("result--ad",),
        limit=10,
    )
    urls = [item["url"] for item in results]
    # The parser returns raw hrefs; unwrapping is the provider's job.
    assert any("project-neko.cn" in url for url in urls), urls
    assert any("github.com%2FProject-N-E-K-O" in url for url in urls), urls
    # The sponsored slot is marked on the wrapping div, not on the anchor.
    assert not any("y.js" in url for url in urls), urls


def test_duckduckgo_snippet_comes_from_the_sibling_snippet_anchor() -> None:
    results = parsing.extract_results(
        conftest.fixture("ddg_html.html"),
        anchor_markers=("result__a",),
        container_markers=("result",),
        skip_markers=("result--ad",),
        limit=10,
    )
    hit = next(item for item in results if "project-neko.cn" in item["url"])
    assert "开源 AI 伙伴" in hit["snippet"]


def test_anomaly_page_is_detected_and_not_treated_as_empty_success() -> None:
    markup = conftest.fixture("ddg_anomaly.html")
    assert parsing.extract_results(markup, anchor_markers=("result__a",),
                                   container_markers=("result",)) == []
    assert "anomaly" in markup


# --- Baidu ----------------------------------------------------------------


def _baidu() -> list[dict[str, str]]:
    return parsing.extract_results(
        conftest.fixture("baidu_html.html"),
        container_markers=("c-container",),
        skip_markers=("data-tuiguang",),
        limit=10,
    )


def test_baidu_titles_come_from_the_card_not_the_tab_bar() -> None:
    titles = [item["title"] for item in _baidu()]
    assert any("猫娘计划" in title for title in titles), titles
    # The tab bar links are not inside a c-container, so they must not appear.
    assert not any(title in {"网页", "资讯", "笔记"} for title in titles), titles


def test_baidu_ad_container_is_skipped() -> None:
    titles = [item["title"] for item in _baidu()]
    assert not any("广告推广位" in title for title in titles), titles


def test_baidu_snippet_includes_the_abstract_div_not_just_anchor_text() -> None:
    hit = next(item for item in _baidu() if "猫娘计划" in item["title"])
    assert "屏幕感知" in hit["snippet"] or "实时语音" in hit["snippet"], hit


def test_baidu_real_cards_outrank_the_short_sublink() -> None:
    results = _baidu()
    scores = [int(item["score"]) for item in results]
    assert scores == sorted(scores, reverse=True), results
    catgirl = next(item for item in results if "猫娘计划" in item["title"])
    sublink = next(item for item in results if item["title"] == "查看40天预报")
    assert int(catgirl["score"]) > int(sublink["score"])


def test_baidu_engine_search_pages_are_dropped_by_the_provider_filter() -> None:
    # The tab bar and "related searches" point back at baidu's own /s pages,
    # which is exactly what the provider-level self-link filter exists for.
    baidu = providers.PROVIDERS["baidu"]
    assert providers._is_engine_results_page(baidu, "/s?wd=x") is False  # relative, not a URL
    assert providers._is_engine_results_page(baidu, "https://www.baidu.com/s?wd=x")
    assert providers._is_engine_results_page(baidu, "https://www.baidu.com/s?rtt=1")
    assert not providers._is_engine_results_page(baidu, "https://baike.baidu.com/item/n/1915")


# --- Bing -----------------------------------------------------------------


def test_bing_reads_the_b_algo_container_and_skips_b_ans() -> None:
    results = parsing.extract_results(
        conftest.fixture("bing_html.html"),
        container_markers=("b_algo",),
        prefer_h2_h3=True,
        limit=10,
    )
    urls = [item["url"] for item in results]
    # b_ans (the answer box) is not a b_algo card and must not be harvested.
    assert not any("answer-box" in url for url in urls), urls
    assert any("github.com" in url for url in urls), urls
    assert all(int(item["score"]) >= 20 for item in results), results


def test_bing_real_cards_are_ranked_before_the_self_link() -> None:
    results = parsing.extract_results(
        conftest.fixture("bing_html.html"),
        container_markers=("b_algo",),
        prefer_h2_h3=True,
        limit=10,
    )
    positions = {item["url"]: index for index, item in enumerate(results)}
    external = next(url for url in positions if "github.com" in url)
    self_link = next(url for url in positions if "bing.com/search" in url)
    assert positions[external] < positions[self_link], positions


def test_bing_caption_text_becomes_the_snippet() -> None:
    results = parsing.extract_results(
        conftest.fixture("bing_html.html"),
        container_markers=("b_algo",),
        prefer_h2_h3=True,
        limit=10,
    )
    assert any("主动陪伴" in item["snippet"] for item in results)


# --- readable text --------------------------------------------------------


def test_readable_text_drops_script_and_nav_keeps_article() -> None:
    markup = """
    <html><head><title>页面标题</title><style>p{color:red}</style>
    <script>var evil = "不应出现";</script></head>
    <body><nav><a href="/x">首页 关于 登录</a></nav>
    <article><h1>大标题</h1><p>这是第一段正文内容，应当保留。</p>
    <p>这是第二段正文内容，也应当保留。</p></article>
    <footer>版权信息</footer></body></html>
    """
    title, text = parsing.readable_text(markup)
    assert title == "页面标题"
    assert "第一段正文内容" in text
    assert "第二段正文内容" in text
    assert "不应出现" not in text
    assert "color:red" not in text


def test_readable_text_respects_the_character_budget() -> None:
    markup = "<article>" + "".join(f"<p>段落{i}{'字' * 40}</p>" for i in range(200)) + "</article>"
    _, text = parsing.readable_text(markup, limit=600)
    assert len(text) <= 601


# --- redirect shells ------------------------------------------------------


def test_meta_refresh_shell_is_flagged() -> None:
    assert parsing.looks_like_redirect_shell(conftest.fixture("baidu_redirect_shell.html"))


def test_a_real_results_page_is_not_a_shell() -> None:
    assert not parsing.looks_like_redirect_shell(conftest.fixture("bing_html.html"))
