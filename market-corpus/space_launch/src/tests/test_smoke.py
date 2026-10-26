# 太空发射插件测试 —— Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是太空发射插件的一部分，以 GNU General Public License v3.0 许可发布。
# 完整条款见仓库根目录的 LICENSE 文件。

from datetime import datetime, timezone
from pathlib import Path

import pytest
from plugin.plugins.space_launch import (
    _coerce_limit,
    _describe_launch,
    _describe_ll2_entity,
    _describe_ntrs_document,
    _describe_sat_wiki_item,
    _describe_sat_wiki_list,
    _filter_relevant_web_results,
    _humanize_delta,
    _is_still_upcoming,
    _join_names,
    _looks_like_cloudflare_challenge,
    _no_result_payload,
    _parse_bing_rss,
    _parse_iso,
    _strip_wiki_snippet,
    _summarize_baike_lemma,
    _summarize_launch,
    _summarize_ll2_entity,
    _summarize_ntrs_document,
    _summarize_sat_wiki,
)
from plugin.sdk.plugin import SdkError


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "space_launch"' in text
    assert 'entry = "plugin.plugins.space_launch:SpaceLaunchPlugin"' in text


def test_parse_iso_handles_z_suffix() -> None:
    parsed = _parse_iso("2026-09-17T01:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc).hour == 1


def test_parse_iso_rejects_garbage() -> None:
    assert _parse_iso("") is None
    assert _parse_iso(None) is None
    assert _parse_iso("not-a-date") is None


def test_humanize_delta_future_and_past() -> None:
    assert _humanize_delta(90).startswith("还有")
    assert "1 分钟" in _humanize_delta(90)

    past = _humanize_delta(-7200)
    assert past.startswith("已过去")
    assert "2 小时" in past


def test_coerce_limit_bounds() -> None:
    assert _coerce_limit(None, 5) == 5
    assert _coerce_limit("", 5) == 5
    assert _coerce_limit(3, 5) == 3
    assert _coerce_limit("3", 5) == 3
    assert _coerce_limit(0, 5) == 1
    assert _coerce_limit(-1, 5) == 1
    assert _coerce_limit(999, 5) == 20

    with pytest.raises(SdkError):
        _coerce_limit("abc", 5)


def test_is_still_upcoming_filters_finished_and_past() -> None:
    assert _is_still_upcoming({"status_abbrev": "Go", "countdown_seconds": 3600})
    assert _is_still_upcoming({"status_abbrev": "TBD", "countdown_seconds": None})
    assert _is_still_upcoming({"status_abbrev": "In Flight", "countdown_seconds": -30})

    # 已经结束的任务，无论时间如何都要剔除
    assert not _is_still_upcoming({"status_abbrev": "Success", "countdown_seconds": -3600})
    assert not _is_still_upcoming({"status_abbrev": "Failure", "countdown_seconds": 60})

    # net 早已过去但状态没刷新的条目也要剔除
    assert not _is_still_upcoming({"status_abbrev": "Go", "countdown_seconds": -7200})


_SAMPLE = {
    "name": "Falcon 9 Block 5 | USSF-259",
    "net": "2099-09-17T01:00:00Z",
    "status": {"name": "Go for Launch", "abbrev": "Go"},
    "launch_service_provider": {"name": "SpaceX", "abbrev": "SpX"},
    "rocket": {"configuration": {"full_name": "Falcon 9 Block 5", "name": "Falcon 9"}},
    "mission": {
        "name": "USSF-259",
        "type": "Government/Top Secret",
        "description": "A classified mission.",
        "orbit": {"name": "Polar Orbit"},
    },
    "pad": {
        "name": "Space Launch Complex 4E",
        "location": {"name": "Vandenberg SFB, CA, USA"},
        "country": {"name": "United States of America"},
    },
    "net_precision": {"name": "Minute"},
    "webcast_live": False,
    "url": "https://ll.thespacedevs.com/2.3.0/launches/example/",
    "image": {"image_url": "https://example.invalid/rocket.jpg"},
}


def test_summarize_launch_extracts_fields() -> None:
    now = datetime(2099, 9, 17, 0, 0, 0, tzinfo=timezone.utc)
    item = _summarize_launch(_SAMPLE, include_description=True, now=now)

    assert item["name"] == "Falcon 9 Block 5 | USSF-259"
    assert item["provider"] == "SpaceX"
    assert item["rocket"] == "Falcon 9 Block 5"
    assert item["location"] == "Vandenberg SFB, CA, USA"
    assert item["orbit"] == "Polar Orbit"
    assert item["description"] == "A classified mission."
    assert item["countdown_seconds"] == 3600
    assert item["countdown"] == "还有 1 小时"
    assert item["net_local"]


def test_summarize_launch_without_description() -> None:
    now = datetime(2099, 9, 17, 0, 0, 0, tzinfo=timezone.utc)
    item = _summarize_launch(_SAMPLE, include_description=False, now=now)
    assert "description" not in item


def test_summarize_launch_tolerates_missing_net() -> None:
    now = datetime(2099, 9, 17, 0, 0, 0, tzinfo=timezone.utc)
    item = _summarize_launch({"name": "TBD"}, include_description=True, now=now)
    assert item["net_local"] == ""
    assert item["countdown"] == ""
    assert item["countdown_seconds"] is None


def test_describe_launch_mentions_key_facts() -> None:
    now = datetime(2099, 9, 17, 0, 0, 0, tzinfo=timezone.utc)
    item = _summarize_launch(_SAMPLE, include_description=False, now=now)
    text = _describe_launch(item)

    assert "Falcon 9 Block 5 | USSF-259" in text
    assert "SpaceX" in text
    assert "Vandenberg SFB, CA, USA" in text
    assert "Go for Launch" in text


# ---------------------------------------------------------------------------
# LL2 实体检索
# ---------------------------------------------------------------------------

_SPACECRAFT_SAMPLE = {
    "name": "Cargo Dragon 2",
    "type": {"name": "Capsule"},
    "agency": {"name": "SpaceX"},
    "in_use": True,
    "family": [{"name": "Dragon", "maiden_flight": "2010-12-08"}],
    "image": {"image_url": "https://example.invalid/dragon.jpg"},
    "url": "https://ll.thespacedevs.com/2.3.0/spacecraft_configurations/7/",
}


def test_join_names_builds_enumeration() -> None:
    assert _join_names([{"name": "NASA"}, {"name": "ESA"}]) == "NASA、ESA"
    assert _join_names([{"name": "NASA"}, {"other": 1}]) == "NASA"
    assert _join_names(None) == ""
    assert _join_names("not-a-list") == ""


def test_summarize_ll2_spacecraft() -> None:
    item = _summarize_ll2_entity(_SPACECRAFT_SAMPLE, "spacecraft")

    assert item["category"] == "spacecraft"
    assert item["category_label"] == "航天器"
    assert item["name"] == "Cargo Dragon 2"
    assert item["type"] == "Capsule"
    assert item["agency"] == "SpaceX"
    assert item["family"] == "Dragon"
    assert item["maiden_flight"] == "2010-12-08"
    assert item["in_use"] is True
    assert item["image"].endswith("dragon.jpg")


def test_summarize_ll2_agency() -> None:
    raw = {
        "name": "National Aeronautics and Space Administration",
        "abbrev": "NASA",
        "type": {"name": "Government"},
        "country": [{"name": "United States of America"}],
        "founding_year": 1958,
        "administrator": "Administrator: Example",
    }
    item = _summarize_ll2_entity(raw, "agency")

    assert item["category_label"] == "航天机构"
    assert item["abbrev"] == "NASA"
    assert item["type"] == "Government"
    assert item["country"] == "United States of America"
    assert item["founding_year"] == "1958"
    assert item["administrator"] == "Administrator: Example"


def test_summarize_ll2_entity_tolerates_missing_fields() -> None:
    item = _summarize_ll2_entity({}, "launcher")

    assert item["name"] == ""
    assert item["image"] == ""
    assert item["category"] == "launcher"
    assert item["category_label"] == "火箭型号"


def test_describe_ll2_entity_mentions_details() -> None:
    raw = dict(_SPACECRAFT_SAMPLE)
    raw["description"] = "A reusable cargo spacecraft."
    text = _describe_ll2_entity(_summarize_ll2_entity(raw, "spacecraft"))

    assert "航天器「Cargo Dragon 2」" in text
    assert "Capsule" in text
    assert "SpaceX" in text
    assert "A reusable cargo spacecraft." in text


# ---------------------------------------------------------------------------
# NASA NTRS 文献检索
# ---------------------------------------------------------------------------

_NTRS_SAMPLE = {
    "id": 20150017756,
    "title": "Seasonal Variations of the JWST Orbital Dynamics",
    "abstract": "We investigate the variability of the observatory trajectory.",
    "authorAffiliations": [
        {"meta": {"author": {"name": "Brown, Jonathan"}}},
        {"meta": {"author": {"name": "Petersen, Jeremy"}}},
    ],
    "center": {"name": "Marshall Space Flight Center"},
    "stiTypeDetails": "Conference Paper",
    "keywords": ["launch windows", 123],
    "downloads": [
        {"links": {"fulltext": "/api/citations/20150017756/downloads/x.txt"}}
    ],
}


def test_summarize_ntrs_document() -> None:
    item = _summarize_ntrs_document(_NTRS_SAMPLE)

    assert item["title"] == "Seasonal Variations of the JWST Orbital Dynamics"
    assert item["authors"] == ["Brown, Jonathan", "Petersen, Jeremy"]
    assert item["center"] == "Marshall Space Flight Center"
    assert item["document_type"] == "Conference Paper"
    assert item["keywords"] == ["launch windows"]
    assert item["document_id"] == 20150017756
    assert item["url"] == "https://ntrs.nasa.gov/citations/20150017756"
    assert item["full_text_url"].startswith("https://ntrs.nasa.gov/api/")


def test_summarize_ntrs_document_keeps_absolute_links() -> None:
    raw = {
        "id": 1,
        "downloads": [{"links": {"pdf": "https://example.invalid/paper.pdf"}}],
    }
    item = _summarize_ntrs_document(raw)
    assert item["full_text_url"] == "https://example.invalid/paper.pdf"


def test_summarize_ntrs_document_tolerates_missing_fields() -> None:
    item = _summarize_ntrs_document({})

    assert item["title"] == ""
    assert item["authors"] == []
    assert item["url"] == ""
    assert item["full_text_url"] == ""


def test_describe_ntrs_document_truncates_abstract() -> None:
    item = {
        "title": "A Long Paper",
        "authors": ["A", "B", "C", "D"],
        "center": "JPL",
        "document_type": "Conference Paper",
        "abstract": "x" * 500,
    }
    text = _describe_ntrs_document(item)

    assert "A Long Paper" in text
    assert "A、B、C 等" in text
    assert "JPL" in text
    assert "Conference Paper" in text
    assert "…" in text
    assert len(text) < 500


# ---------------------------------------------------------------------------
# 防幻觉：无结果时必须给出明确指引
# ---------------------------------------------------------------------------


def test_no_result_payload_forbids_fabrication() -> None:
    """没有数据时必须写明“不要编造”，否则 LLM 会用记忆补全细节。"""
    payload = _no_result_payload(query="UR-700A", label="火箭型号")

    assert payload["found"] is False
    assert payload["count"] == 0
    assert payload["results"] == []
    assert "不要凭猜测" in payload["summary"]
    assert "绝对不要" in payload["guidance"]
    assert payload["next_steps"]


def test_no_result_payload_includes_suggestions() -> None:
    payload = _no_result_payload(
        query="UR-700A",
        label="火箭型号",
        suggestions=["UR-700", "N1"],
    )

    assert payload["suggestions"] == ["UR-700", "N1"]
    assert any("UR-700" in step for step in payload["next_steps"])


# ---------------------------------------------------------------------------
# 百度百科解析
# ---------------------------------------------------------------------------


def test_summarize_baike_lemma_hit() -> None:
    raw = {
        "title": "长征五号",
        "abstract": "长征五号是中国研制的大型运载火箭。",
        "url": "http://baike.baidu.com/subview/658465/658465.htm",
        "image": "//example.invalid/x.jpg",
    }
    item = _summarize_baike_lemma(raw, "长征五号")

    assert item is not None
    assert item["title"] == "长征五号"
    assert item["abstract"].startswith("长征五号是")
    assert item["url"].startswith("http://baike")
    assert item["image"].startswith("https://")


def test_summarize_baike_lemma_miss_returns_none() -> None:
    """百度百科未收录时返回空对象，必须识别为“没查到”。"""
    assert _summarize_baike_lemma({}, "UR-700A") is None


# ---------------------------------------------------------------------------
# 网页结果相关性过滤
# ---------------------------------------------------------------------------


def test_filter_drops_irrelevant_commercial_results() -> None:
    """实测 UR-700A 会搜到同名服装品牌，这类结果必须被丢弃。"""
    items = [
        {
            "title": "URBAN REVIVO (UR) 官方商城-PLAY FASHION",
            "snippet": "UR 官方商城，提供最新时尚服饰和配饰",
            "url": "https://ur.cn/",
        },
        {
            "title": "UR官方旗舰店 - 淘宝网",
            "snippet": "欢迎光临 UR 官方旗舰店，提供最新商品价格折扣",
            "url": "https://taobao.example/",
        },
    ]
    assert _filter_relevant_web_results(items, "UR-700A") == []


def test_filter_keeps_aerospace_results() -> None:
    items = [
        {
            "title": "UR-700A 苏联登月火箭方案",
            "snippet": "这是一种运载火箭，计划用于载人登月任务",
            "url": "https://example.invalid/ur700a",
        },
    ]
    kept = _filter_relevant_web_results(items, "UR-700A")

    assert len(kept) == 1
    assert kept[0]["title"].startswith("UR-700A")


def test_filter_keeps_generic_aerospace_snippet() -> None:
    items = [
        {"title": "某型火箭资料", "snippet": "运载火箭的发射与轨道设计", "url": "u"},
    ]
    assert len(_filter_relevant_web_results(items, "UR-700A")) == 1


def test_filter_handles_empty_and_tokenless_query() -> None:
    assert _filter_relevant_web_results([], "UR-700A") == []
    items = [{"title": "任意标题", "snippet": "无关内容", "url": "u"}]
    assert _filter_relevant_web_results(items, "") == []


# ---------------------------------------------------------------------------
# Bing RSS 解析
# ---------------------------------------------------------------------------


def test_parse_bing_rss() -> None:
    xml = """<?xml version="1.0" encoding="utf-8" ?>
    <rss version="2.0"><channel>
      <title>必应：测试</title>
      <item>
        <title>长征五号 - 维基百科</title>
        <link>https://example.invalid/cz5</link>
        <description>长征五号是中国研制的大型运载火箭</description>
      </item>
      <item>
        <title></title>
        <link></link>
      </item>
    </channel></rss>"""

    items = _parse_bing_rss(xml)

    assert len(items) == 1
    assert items[0]["title"] == "长征五号 - 维基百科"
    assert items[0]["url"] == "https://example.invalid/cz5"


def test_parse_bing_rss_tolerates_invalid_xml() -> None:
    assert _parse_bing_rss("not xml at all") == []
    assert _parse_bing_rss("") == []


# ---------------------------------------------------------------------------
# 卫星百科（灰机wiki）检索
# ---------------------------------------------------------------------------


def test_strip_wiki_snippet_removes_tags_and_entities() -> None:
    raw = '航天科技 <span class="searchmatch">长征</span>一号 | 1970&apos;年发射'
    cleaned = _strip_wiki_snippet(raw)
    assert "<span" not in cleaned
    assert "searchmatch" not in cleaned
    assert "长征" in cleaned
    assert "1970'年发射" in cleaned


def test_strip_wiki_snippet_handles_empty() -> None:
    assert _strip_wiki_snippet("") == ""
    assert _strip_wiki_snippet(None) == ""  # type: ignore[arg-type]


def test_summarize_sat_wiki_builds_url_and_strips_snippet() -> None:
    raw = {
        "ns": 0,
        "title": "长征一号",
        "pageid": 3625,
        "size": 26214,
        "wordcount": 3602,
        "snippet": '航天科技 <span class="searchmatch">长征</span>一号',
        "timestamp": "2026-10-14T17:28:50Z",
    }
    item = _summarize_sat_wiki(raw, "https://sat.huijiwiki.com/")

    assert item["title"] == "长征一号"
    assert item["pageid"] == 3625
    assert item["source"] == "卫星百科（灰机wiki）"
    # 摘要应去掉高亮标签
    assert item["snippet"] == "航天科技 长征一号"
    # 规范链接：wiki/<URL 编码标题>
    assert item["url"].startswith("https://sat.huijiwiki.com/wiki/")
    assert "长征一号" not in item["url"]  # 中文已被编码
    assert "%E9%95%BF%E5%BE%81%E4%B8%80%E5%8F%B7" in item["url"]


def test_summarize_sat_wiki_handles_missing_snippet() -> None:
    item = _summarize_sat_wiki({"title": "风云四号"}, "https://sat.huijiwiki.com/")
    assert item["title"] == "风云四号"
    assert item["snippet"] == ""
    assert item["url"].endswith("/wiki/%E9%A3%8E%E4%BA%91%E5%9B%9B%E5%8F%B7")


def test_describe_sat_wiki_item_mentions_facts() -> None:
    item = {
        "title": "长征一号",
        "snippet": "我国第一款成功入轨的运载火箭",
        "url": "https://sat.huijiwiki.com/wiki/%E9%95%BF%E5%BE%81%E4%B8%80%E5%8F%B7",
    }
    text = _describe_sat_wiki_item(item)

    assert "卫星百科（灰机wiki）条目「长征一号」" in text
    assert "我国第一款成功入轨的运载火箭" in text
    assert "https://sat.huijiwiki.com/wiki/" in text


def test_describe_sat_wiki_list_formatting() -> None:
    items = [
        {"title": "长征一号", "snippet": "第一款入轨火箭"},
        {"title": "长征二号丙", "snippet": "衍生型号"},
    ]
    text = _describe_sat_wiki_list(items, query="长征")

    assert "与「长征」相关的 2 个条目" in text
    assert "1. 长征一号" in text
    assert "2. 长征二号丙" in text
    assert "第一款入轨火箭" in text


def test_describe_sat_wiki_list_empty() -> None:
    assert "没有找到" in _describe_sat_wiki_list([], query="UR-700A")


# ---------------------------------------------------------------------------
# 卫星百科的 Cloudflare 风控识别
# ---------------------------------------------------------------------------


class _FakeResponse:
    """只保留风控识别需要的两个属性。"""

    def __init__(self, status_code: int = 200, headers=None, text: str = "") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


def test_detects_cloudflare_challenge_via_header() -> None:
    """实测灰机wiki 被拦截时返回 403 + cf-mitigated: challenge。"""
    response = _FakeResponse(
        status_code=403,
        headers={"cf-mitigated": "challenge", "server": "cloudflare"},
        text="<!DOCTYPE html><html><head><title>Just a moment...</title>",
    )
    assert _looks_like_cloudflare_challenge(response)


def test_detects_cloudflare_challenge_via_body() -> None:
    response = _FakeResponse(
        status_code=403,
        headers={"server": "cloudflare"},
        text="... challenges.cloudflare.com ...",
    )
    assert _looks_like_cloudflare_challenge(response)


def test_plain_403_is_not_a_challenge() -> None:
    """普通 403（例如接口权限问题）不能被误判成风控。"""
    response = _FakeResponse(
        status_code=403,
        headers={"server": "nginx"},
        text='{"error":"forbidden"}',
    )
    assert not _looks_like_cloudflare_challenge(response)


def test_normal_json_response_is_not_a_challenge() -> None:
    response = _FakeResponse(
        status_code=200,
        headers={"server": "cloudflare"},
        text='{"query":{"search":[]}}',
    )
    assert not _looks_like_cloudflare_challenge(response)

