"""Pure-function parsing layer for the Luogu plugin.

Luogu's V2 SSR pages embed their data as a JSON object directly in a
``<script>`` block, opening with ``{"instance": ..., "template": ..., "data":
{...}}``. Older pages (or some endpoints) wrap that JSON in
``decodeURIComponent('...')``. ``extract_decoded_json`` handles both shapes.

This module is deliberately free of SDK, network, and ``_client`` so it can be
unit-tested against HTML fixtures.

Robustness contract
-------------------
* Every ``parse_*`` function tolerates a missing field by skipping it or
  falling back to a default. A single drifted key must never fail the whole
  parse. Only a missing JSON block raises ``LuoguParseError``.
* Tags are Luogu numeric ids (e.g. ``[1, 42, ...]``); we keep them as decimal
  strings (``"1"``). The page payload carries neither the names nor a complete
  table, so ``_TAG_NAMES`` seeds a static snapshot and
  ``merge_tag_names(parse_tag_dictionary(...))`` grows it from the live
  ``/_lfe/tags`` dictionary. Ids in neither render as ``#id``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from ._models import ContestItem, LuoguProblem, SubmissionRecord

_DECODED_RE = re.compile(r"decodeURIComponent\(\s*['\"]([^'\"]*)['\"]\s*\)", re.S)
_SCRIPT_RE = re.compile(r"<script[^>]*>([^<]+)</script>", re.S)
_TAG_ID_RE = re.compile(r"^\d+$")


class LuoguParseError(RuntimeError):
    """The page contained no usable JSON block."""


class LuoguAuthRequiredError(RuntimeError):
    """A login-required endpoint was hit without valid auth."""


# ---------------------------------------------------------------------------
# Generic extraction helpers
# ---------------------------------------------------------------------------

def _pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


def _pick_int(data: dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _pick_list(data: dict[str, Any], *keys: str) -> tuple[Any, ...]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return tuple(value)
    return ()


def _try_loads_script(text: str) -> dict[str, Any] | None:
    """Attempt to parse a ``<script>`` inner text as a full JSON object."""
    text = text.strip()
    if not text.startswith("{"):
        return None
    candidates = (text, text[: text.rfind("}") + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_decoded_json(html: str) -> dict[str, Any]:
    """Return the first JSON object embedded in the page.

    Prefers the direct-inline ``<script>`` JSON (current V2 shape), then falls
    back to ``decodeURIComponent('...')`` wrappers. Raises ``LuoguParseError``
    if nothing decodes to a dict.
    """
    for match in _SCRIPT_RE.finditer(html):
        parsed = _try_loads_script(match.group(1))
        if parsed is not None:
            return parsed
    for match in _DECODED_RE.finditer(html):
        try:
            decoded = unquote(match.group(1))
            parsed = json.loads(decoded)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LuoguParseError("no JSON block found")


def _data_root(data: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the standard ``{"instance": ..., "data": {...}}`` envelope."""
    candidate = data.get("data")
    if isinstance(candidate, dict):
        return candidate
    return data


def is_challenge_html(html: str) -> bool:
    return bool(re.search(r'window\[[^\]]*\]\.cookie="C3VK=', html)) or "C3VK=" in html


def is_not_logged_in(html: str) -> bool:
    if re.search(r"auth/login|login\?redirect", html, re.I):
        return True
    if "当前未登录" in html or "请先登录" in html:
        return True
    # Luogu's login-required endpoints respond 200 with a WebAuthn login form
    # (``data.webauthn`` in the SSR JSON) instead of a redirect; without this the
    # auth-required state slips through as an empty record list / parse error.
    if "webauthn" in html.lower():
        return True
    return False


# ---------------------------------------------------------------------------
# Tag handling
# ---------------------------------------------------------------------------

def tag_id_string(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(int(value))
    if isinstance(value, str):
        return value if _TAG_ID_RE.fullmatch(value.strip()) else value.strip()
    return str(value)


# Luogu problem tags are numeric ids in the page payload; the names are only in
# the rendered `<a href="/problem/list?tag=N">name</a>` anchors. Share a static
# map (gathered from Luogu) and grow it at runtime from any HTML we parse.
_TAG_NAMES: dict[str, str] = {
    '1': '模拟',
    '2': '字符串',
    '3': '动态规划 DP',
    '4': '搜索',
    '5': '数学',
    '6': '图论',
    '7': '贪心',
    '8': '计算几何',
    '10': '高精度',
    '11': '树形数据结构',
    '12': '递推',
    '13': '博弈论',
    '43': '倍增',
    '44': '线性数据结构',
    '45': '二分',
    '47': '并查集',
    '50': '平衡树',
    '51': '堆',
    '53': '树状数组',
    '54': '递归',
    '56': '单调队列',
    '72': '数论',
    '78': '离散化',
    '79': '网络流',
    '104': '提交答案',
    '111': '枚举',
    '112': '分治',
    '113': '排序',
    '126': '广度优先搜索 BFS',
    '127': '深度优先搜索 DFS',
    '128': '剪枝',
    '129': '记忆化搜索',
    '130': '启发式搜索',
    '131': '迭代加深搜索',
    '133': 'Dancing Links',
    '139': '背包 DP',
    '141': '数位 DP',
    '144': '区间 DP',
    '146': '动态规划优化',
    '148': '优先队列',
    '149': '矩阵加速',
    '152': '树形 DP',
    '155': '图论建模',
    '159': '拓扑排序',
    '160': '最短路',
    '166': '生成树',
    '175': '连通块',
    '179': '强连通分量',
    '180': 'Tarjan',
    '181': '双连通分量',
    '182': '欧拉回路',
    '187': '二分图',
    '204': '费用流',
    '213': '树的直径',
    '228': '树链剖分',
    '229': '动态树 LCT',
    '235': '哈希 hashing',
    '239': '素数判断',
    '241': '最大公约数 gcd',
    '242': '扩展欧几里德算法',
    '243': '不定方程',
    '244': '进制',
    '252': '组合数学',
    '253': '排列组合',
    '254': '前缀和',
    '260': 'Fibonacci 数列',
    '261': 'Catalan 数',
    '273': '线性递推',
    '274': '高斯消元',
    '276': '逆元',
    '287': '栈',
    '288': '队列',
    '290': 'ST 表',
    '303': '后缀数组 SA',
    '314': '位运算',
    '318': '构造',
    '330': '差分',
    '345': '双指针 two-pointer',
    '360': '链表',
    '364': 'Dilworth 定理',
    '376': '分类讨论',
    '380': '折半搜索 meet in the middle',
    '385': '单调栈',
    '411': '调和级数',
    '443': '动态 DP',
    '444': '线性 DP',
    '447': '离线处理',
    '464': '状压 DP',
    '472': '全局平衡二叉树',
    '476': 'Floyd 算法',
    '504': 'STL',
    '523': '模板题',
    '524': '反悔贪心',
    '46': 'USACO',
    '77': 'NOI',
    '82': 'NOIP 普及组',
    '83': 'NOIP 提高组',
    '335': 'ICPC',
    '502': 'CERC',
    '478': 'COI（克罗地亚）',
    '81': '洛谷原创',
    '337': '洛谷月赛',
    '434': '高校校赛',
    '88': '浙江',
    '91': '江苏',
    '93': '湖南',
    '94': '北京',
    '15': '1998',
    '16': '1999',
    '17': '2000',
    '18': '2001',
    '19': '2002',
    '20': '2003',
    '21': '2004',
    '22': '2005',
    '23': '2006',
    '24': '2007',
    '25': '2008',
    '26': '2009',
    '27': '2010',
    '28': '2011',
    '29': '2012',
    '33': '2016',
    '344': '1996',
    '107': 'Special Judge',
    '108': 'O2优化',
}

_TAG_LINK_RE = re.compile(r"tag=(\d{1,4})\">([^<]+)<")
# Rendered for ids that are in no known dictionary; also the marker
# ``usable_tag_names`` uses to reject a name as a search keyword.
_UNKNOWN_TAG_PREFIX = "#"


def extract_tag_names(html: str) -> None:
    """Grow the shared id→name map from rendered ``tag=N">名字<`` anchors."""
    if "tag=" not in html:
        return
    for sid, name in _TAG_LINK_RE.findall(html):
        if sid and name:
            _TAG_NAMES.setdefault(sid, name)


def tag_name(tag_id: str) -> str:
    """Human-readable name for a numeric Luogu tag id (or ``#id`` if unknown)."""
    return _TAG_NAMES.get(str(tag_id), f"{_UNKNOWN_TAG_PREFIX}{tag_id}")


def tag_names(ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(tag_name(tag_id) for tag_id in ids)


def parse_tag_dictionary(text: str) -> dict[str, str]:
    """Parse Luogu's ``/_lfe/tags`` JSON into ``{tag_id: name}``.

    Live shape (measured 2026-09, anonymous-accessible, 505 entries):
    ``{"tags": [{"id": 1, "name": "模拟", "type": 2, "parent": 110}, ...]}``.
    Anything unexpected yields ``{}`` so the caller keeps the static seed.
    """
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("tags")
    if isinstance(raw, dict):  # tolerate a plain {"1": "模拟"} mapping
        return {
            tag_id_string(key): str(value).strip()
            for key, value in raw.items()
            if str(value).strip()
        }
    if not isinstance(raw, list):
        return {}
    names: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        ident = item.get("id")
        label = item.get("name")
        if ident is None or label is None:
            continue
        text_label = str(label).strip()
        if text_label:
            names[tag_id_string(ident)] = text_label
    return names


def merge_tag_names(mapping: Mapping[str, str]) -> int:
    """Merge live ``{id: name}`` pairs into the shared map; returns added count.

    Live values win over the static seed: the seed is a point-in-time snapshot
    and Luogu both adds tags and renames them. Only previously-unknown ids count
    as "added", so the return value stays meaningful for logging.
    """
    added = 0
    for tag_id, name in mapping.items():
        key = str(tag_id).strip()
        label = str(name).strip()
        if not key or not label:
            continue
        if key not in _TAG_NAMES:
            added += 1
        _TAG_NAMES[key] = label
    return added


def usable_tag_names(ids: Iterable[str]) -> list[str]:
    """Names among ``ids`` that are meaningful as ``/problem/list`` keywords.

    ``/problem/list?keyword=`` matches visible problem text, not tag ids:
    ``keyword=42`` returns nothing and ``keyword=3`` returns every problem whose
    *title* contains a "3" (measured 2026-09). Unknown ids render as ``#<id>``,
    which is equally useless, so they are dropped here and the caller falls back
    to topical default keywords.
    """
    names: list[str] = []
    for tag_id in ids:
        label = tag_name(tag_id)
        if label and not label.startswith(_UNKNOWN_TAG_PREFIX) and label not in names:
            names.append(label)
    return names


def _tags_to_ids(values: tuple[Any, ...]) -> tuple[str, ...]:
    tags: list[str] = []
    for value in values:
        ident = tag_id_string(value)
        if ident and ident not in tags:
            tags.append(ident)
    return tuple(tags)


# ---------------------------------------------------------------------------
# Problem detail / list
# ---------------------------------------------------------------------------

def _difficulty(data: dict[str, Any]) -> int:
    return _pick_int(data, "difficulty", "difficultyValue", "level")


def _statement_text(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    parts: list[str] = []
    for key in ("background", "description", "inputFormat", "outputFormat"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n\n".join(parts)


def parse_problem_detail(html: str) -> LuoguProblem | None:
    extract_tag_names(html)
    data = _data_root(extract_decoded_json(html))
    problem = data.get("problem") if isinstance(data.get("problem"), dict) else data
    pid = _pick(problem, "pid", "problemId", "id", default="")
    if not pid:
        return None
    return LuoguProblem(
        pid=str(pid),
        title=str(_pick(problem, "name", "title", default="")),
        difficulty=_difficulty(problem),
        tags=_tags_to_ids(_pick_list(problem, "tags", "tagList")),
        accepted=_pick_int(problem, "totalAccepted", "accepted", "acceptedCount"),
        submitted=_pick_int(problem, "totalSubmit", "submitted", "submitCount"),
        solutions=_pick_int(problem, "solutionCount", "solutions"),
        statement=_statement_text(problem.get("content")),
        url=f"https://www.luogu.com.cn/problem/{pid}",
    )


def parse_problem_list(html: str, *, limit: int = 20) -> list[LuoguProblem]:
    extract_tag_names(html)
    data = _data_root(extract_decoded_json(html))
    records = _problem_records_from(data)
    problems: list[LuoguProblem] = []
    for record in records:
        pid = record.get("pid") or record.get("problemId")
        if not pid:
            continue
        problems.append(
            LuoguProblem(
                pid=str(pid),
                title=str(_pick(record, "name", "title", default="")),
                difficulty=_difficulty(record),
                tags=_tags_to_ids(_pick_list(record, "tags", "tagList")),
                accepted=_pick_int(record, "totalAccepted", "accepted", "acceptedCount"),
                submitted=_pick_int(record, "totalSubmit", "submitted", "submitCount"),
                url=f"https://www.luogu.com.cn/problem/{pid}",
            )
        )
        if len(problems) >= limit:
            break
    return problems


def _problem_records_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    # Current shape: {"problems": {"perPage":..., "count":..., "result": [...]}}
    container = data.get("problems")
    if isinstance(container, dict):
        inner = container.get("result")
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
    # Flattened variants.
    for key in ("result", "problems", "list", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------------------
# Contests
# ---------------------------------------------------------------------------

def parse_contest_list(html: str) -> list[ContestItem]:
    extract_tag_names(html)
    data = _data_root(extract_decoded_json(html))
    records: list[dict[str, Any]] = []
    for key in ("contests", "result", "list", "items"):
        container = data.get(key)
        if isinstance(container, dict):
            for sub in ("result", "list", "items"):
                if isinstance(container.get(sub), list):
                    container = container[sub]
                    break
        if isinstance(container, list):
            records = [item for item in container if isinstance(item, dict)]
            break
    contests: list[ContestItem] = []
    for record in records:
        name = _pick(record, "name", "title", default="")
        if not name:
            continue
        cid = _pick(record, "id", "contestId", default="")
        contests.append(
            ContestItem(
                name=str(name),
                start_time=str(_pick(record, "startTime", "start_time", default="")),
                duration=str(_pick(record, "duration", "endTime", default="")),
                link=f"https://www.luogu.com.cn/contest/{cid}" if cid else "",
            )
        )
    return contests


# ---------------------------------------------------------------------------
# User profile / submissions
# ---------------------------------------------------------------------------

def parse_user_profile(html: str) -> dict[str, Any]:
    data = _data_root(extract_decoded_json(html))
    user = data.get("user") if isinstance(data.get("user"), dict) else data
    # Current profile JSON uses passedProblemCount / submittedProblemCount and
    # keeps rating under the nested `gu` object; keep the old names as fallbacks.
    gu = data.get("gu") if isinstance(data.get("gu"), dict) else {}
    rating = (
        _pick_int(user, "rating", "eloValue")
        or _pick_int(gu, "rating")
        or _pick_int(user, "ranking")
    )
    return {
        "uid": str(_pick(user, "uid", "id", default="")),
        "name": str(_pick(user, "name", "username", default="")),
        "ac_count": _pick_int(user, "passedProblemCount", "acCount", "acceptedCount", "totalAccepted"),
        "submitted": _pick_int(user, "submittedProblemCount", "submitted", "submitCount", "totalSubmit"),
        "rating": rating,
        "registered_at": str(_pick(user, "registerTime", "register_time", default="")),
    }


# Luogu's record-list verdict codes (the `status` field is an int, not "AC").
# Only 12 == Accepted matters for `solved`; the rest are just labels.
_VERDICT_TEXT = {
    0: "Waiting",
    1: "Judging",
    2: "Compile Error",
    3: "Runtime Error",
    6: "Time Limit Exceeded",
    7: "Memory Limit Exceeded",
    8: "Output Limit Exceeded",
    10: "Wrong Answer",
    12: "Accepted",
    14: "Wrong Answer",
}
_AC_CODE = 12


def _verdict_text(status: object) -> str:
    if isinstance(status, bool):
        return str(status)
    if isinstance(status, int):
        return _VERDICT_TEXT.get(status, str(status))
    return str(status)


def _verdict_is_ac(status: object) -> bool:
    if isinstance(status, bool):
        return False
    if isinstance(status, int):
        return status == _AC_CODE
    return str(status).upper() == "AC"


# --- Submission timestamps ---------------------------------------------------
# Luogu's `submitTime` is a **10-digit seconds** Unix timestamp. Reading it as
# milliseconds is a known trap (every submission lands in 1970), so the length
# decides the unit and anything implausible is dropped rather than trusted.
_EPOCH_SECONDS_RE = re.compile(r"^\d{10}$")
_EPOCH_MILLIS_RE = re.compile(r"^\d{13}$")
_ISO_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")
# Luogu opened in 2013; a timestamp before that (or far in the future) is junk.
_MIN_YEAR = 2013
_MAX_YEAR = 2100


def normalize_submit_time(value: Any) -> str:
    """Normalise ``submitTime`` into an ISO-8601 UTC string (``""`` if unusable).

    Seconds in, ``2026-09-27T05:31:32Z`` out — so date/week/trend maths and
    string ordering both work, and the value no longer depends on the raw unit
    Luogu happened to send. Submissions are bucketed into local days later, by
    the caller that knows the configured timezone.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if _EPOCH_MILLIS_RE.match(text):
        seconds = int(text) / 1000
    elif _EPOCH_SECONDS_RE.match(text):
        seconds = int(text)
    elif _ISO_LIKE_RE.match(text):
        return text
    else:
        return ""
    try:
        stamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    if not _MIN_YEAR <= stamp.year <= _MAX_YEAR:
        return ""
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_solution_page(html: str) -> dict[str, Any]:
    """Parse a problem solution page: return the problem name + solution list."""
    extract_tag_names(html)
    data = _data_root(extract_decoded_json(html))
    problem = data.get("problem") if isinstance(data.get("problem"), dict) else {}
    container = data.get("solutions") if isinstance(data.get("solutions"), dict) else {}
    result = container.get("result") if isinstance(container, dict) else []
    solutions: list[dict[str, Any]] = []
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            solutions.append(
                {
                    "lid": str(item.get("lid") or ""),
                    "title": str(item.get("title") or ""),
                    "content": str(item.get("content") or ""),
                    "upvote": _pick_int(item, "upvote"),
                }
            )
    return {
        "name": str(_pick(problem, "name", "title", default="")),
        "difficulty": _pick_int(problem, "difficulty"),
        "solutions": solutions,
    }


def parse_submissions(html: str) -> list[SubmissionRecord]:
    extract_tag_names(html)
    if is_not_logged_in(html):
        raise LuoguAuthRequiredError("login required to read submissions")
    data = _data_root(extract_decoded_json(html))
    records = _submission_records_from(data)
    submissions: list[SubmissionRecord] = []
    for record in records:
        # Current V2 shape nests pid/title/difficulty under `problem`.
        problem = record.get("problem") if isinstance(record.get("problem"), dict) else {}
        pid = record.get("pid") or record.get("problemId") or problem.get("pid")
        if not pid:
            continue
        status = _pick(record, "status", "result", "verdict", default="")
        title = _pick(record, "title", "name", default="") or _pick(problem, "title", "name", default="")
        difficulty = _difficulty(problem) or _difficulty(record)
        tags = _pick_list(record, "tags", "tagList") or _pick_list(problem, "tags", "tagList")
        submissions.append(
            SubmissionRecord(
                pid=str(pid),
                title=str(title),
                status=_verdict_text(status),
                difficulty=difficulty,
                tags=_tags_to_ids(tags),
                solved=_verdict_is_ac(status),
                attempt_count=max(1, _pick_int(record, "attemptCount", "count")),
                submitted_at=normalize_submit_time(_pick(record, "submitTime", "submit_time", default="")),
            )
        )
    return submissions


def record_list_count(html: str) -> int:
    """Total number of submissions the logged-in user has (from /record/list)."""
    try:
        data = _data_root(extract_decoded_json(html))
    except LuoguParseError:
        return 0
    for container in (data.get("currentData"), data):
        if not isinstance(container, dict):
            continue
        records = container.get("records")
        if isinstance(records, dict):
            count = _pick_int(records, "count", "total", "totalCount")
            if count:
                return count
    return 0


def _submission_records_from(data: dict[str, Any]) -> list[dict[str, Any]]:
    # Current V2 shape: {"currentData": {"records": {"result": [...]}}}
    current_data = data.get("currentData")
    if isinstance(current_data, dict):
        for key in ("records", "submissions", "result", "list", "items"):
            container = current_data.get(key)
            if isinstance(container, dict):
                for sub in ("result", "list", "items", "records"):
                    if isinstance(container.get(sub), list):
                        container = container[sub]
                        break
            if isinstance(container, list):
                return [item for item in container if isinstance(item, dict)]
    # Legacy shapes.
    for key in ("records", "submissions", "result", "list", "items"):
        container = data.get(key)
        if isinstance(container, dict):
            for sub in ("result", "list", "items", "records"):
                if isinstance(container.get(sub), list):
                    container = container[sub]
                    break
        if isinstance(container, list):
            return [item for item in container if isinstance(item, dict)]
    return []


__all__ = [
    "LuoguAuthRequiredError",
    "LuoguParseError",
    "extract_decoded_json",
    "is_challenge_html",
    "is_not_logged_in",
    "extract_tag_names",
    "merge_tag_names",
    "normalize_submit_time",
    "parse_contest_list",
    "parse_problem_detail",
    "parse_problem_list",
    "parse_solution_page",
    "parse_submissions",
    "parse_tag_dictionary",
    "parse_user_profile",
    "record_list_count",
    "tag_id_string",
    "tag_name",
    "tag_names",
    "usable_tag_names",
]
