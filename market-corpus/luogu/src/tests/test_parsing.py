"""Luogu plugin parsing-layer tests.

The parser is pure and must tolerate drifted/missing fields in Luogu's embedded
JSON. Fixtures feed fake HTML with a ``decodeURIComponent(...)`` wrapper, as it
actually appears on Luogu pages (URL-encoded JSON).
"""

from __future__ import annotations

import json
import urllib.parse

import pytest
from plugin.plugins.luogu import _parsing as p

pytestmark = pytest.mark.plugin_unit


def _wrap(payload: dict) -> str:
    encoded = urllib.parse.quote(json.dumps(payload, ensure_ascii=False), safe="")
    return f"<script>var x = decodeURIComponent('{encoded}');</script>"


def test_extract_decoded_json() -> None:
    html = _wrap({"data": {"pid": "P1001", "title": "A+B Problem"}})
    data = p.extract_decoded_json(html)
    assert data["data"]["pid"] == "P1001"


def test_extract_raises_when_no_block() -> None:
    with pytest.raises(p.LuoguParseError):
        p.extract_decoded_json("<html><body>no data here</body></html>")


def test_parse_problem_detail_full() -> None:
    html = _wrap({
        "data": {
            "pid": "P1001",
            "title": "A+B Problem",
            "difficulty": 1,
            "tags": ["模拟", "入门"],
            "accepted": 500000,
            "submitted": 800000,
            "solutions": 120,
        }
    })
    problem = p.parse_problem_detail(html)
    assert problem is not None
    assert problem.pid == "P1001"
    assert problem.difficulty == 1
    assert problem.tags == ("模拟", "入门")
    assert problem.accepted == 500000
    assert problem.url == "https://www.luogu.com.cn/problem/P1001"


def test_parse_problem_detail_tolerates_missing_fields() -> None:
    # Only pid present: everything else falls back without crashing.
    html = _wrap({"data": {"pid": "P1002"}})
    problem = p.parse_problem_detail(html)
    assert problem is not None
    assert problem.pid == "P1002"
    assert problem.title == ""
    assert problem.difficulty == 0
    assert problem.tags == ()


def test_parse_problem_detail_returns_none_without_pid() -> None:
    html = _wrap({"data": {"title": "no pid"}})
    assert p.parse_problem_detail(html) is None


def test_parse_problem_list_tags_as_numeric_ids() -> None:
    html = _wrap({
        "data": {
            "problems": [
                {"pid": "P1001", "name": "A", "difficulty": 2, "tags": [42, 108]},
                {"pid": "P1002", "name": "B", "difficulty": 3, "tags": [1]},
            ]
        }
    })
    problems = p.parse_problem_list(html, limit=10)
    assert len(problems) == 2
    assert problems[0].tags == ("42", "108")
    assert problems[1].pid == "P1002"
    # title comes from the V2 `name` field.
    assert problems[0].title == "A"


def test_parse_problem_list_limit() -> None:
    html = _wrap({"data": {"problems": [{"pid": f"P{i}"} for i in range(5)]}})
    assert len(p.parse_problem_list(html, limit=3)) == 3


def test_is_challenge_html() -> None:
    assert p.is_challenge_html('<script>window["document"].cookie="C3VK=620eb1;max-age=300"</script>')
    assert p.is_challenge_html("C3VK=abc123")
    assert not p.is_challenge_html("<html>normal page</html>")


def test_is_not_logged_in() -> None:
    assert p.is_not_logged_in('<a href="https://www.luogu.com.cn/auth/login">登录</a>')
    assert p.is_not_logged_in("当前未登录")
    assert not p.is_not_logged_in('<html>private data here</html>')


def test_parse_submissions_auth_required() -> None:
    with pytest.raises(p.LuoguAuthRequiredError):
        p.parse_submissions("请先登录后查看")


def test_parse_submissions_basic() -> None:
    html = _wrap({
        "data": {
            "records": [
                {"pid": "P1001", "status": "AC", "difficulty": 1, "tags": ["入门"]},
                {"pid": "P1002", "status": "WA", "difficulty": 3, "tags": ["动态规划"]},
            ]
        }
    })
    records = p.parse_submissions(html)
    assert len(records) == 2
    assert records[0].solved is True
    assert records[1].solved is False
    assert records[1].status == "WA"
