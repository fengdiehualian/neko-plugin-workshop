"""Network self-check orchestration (work package W6).

Why this module exists
----------------------
The plugin ships to non-technical users with no developer nearby, and the single
most common support question is "can this machine search without a proxy?". Three
completely different failures hide behind that one sentence, and the advice differs
for each of them:

* unreachable route  (polluted DNS, timeout, broken TLS) -> open a proxy / switch backend
* reachable but refusing us (anti-bot challenge page)    -> stop trusting that backend
* working but spent    (quota exhausted, key rejected)   -> wait, or add an own API key

Measured on a mainland machine with a direct route and no system proxy wired in:
exa / anysearch / bing answer directly, baidu replies with a 百度安全验证 page,
sogou parses to nothing, duckduckgo times out on polluted DNS and fails TLS even
through a proxy. ``run()`` reproduces exactly that table on the user's machine.

Design constraints
------------------
* **No network I/O in this module.** Every probe is injected by the caller (W5) as a
  zero-argument closure "search once with backend X, return ``list[dict]``". That
  keeps W6 offline-testable and decoupled from W1/W5.
* Pure functions, state local to one ``run()`` call, no import of ``_host.py``.
* Probes are isolated: one raising (or hanging) probe must never affect the other rows.
"""

from __future__ import annotations

import math
import re
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import _net
from ._net import HttpStatusCodeError, NetworkError
from ._resilience import (
    ApiKeyRejectedError,
    BlockedError,
    BusyError,
    CooldownError,
    QuotaExhaustedError,
    SearchProviderError,
)

# --------------------------------------------------------------------------- knobs

MAX_WORKERS = 4
"""Hard ceiling on concurrent probes: a self-check must not look like an attack."""

DEFAULT_PROBE_TIMEOUT = 12.0
"""Backstop per probe. The closure owns its own budget; this only bounds a hang."""

SUMMARY_MAX_CHARS = 160
RESULT_FIELDS = ("url", "title", "snippet", "content")

#: Backends that are known to be unusable without a proxy, so they must never be
#: recommended to a user who has none (plan §1.1).
PROXY_ONLY_BACKENDS = frozenset({"duckduckgo"})

STATUS_DIRECT = "direct"
STATUS_PROXIED = "proxied"
STATUS_OK = "ok"
STATUS_SKIP = ""  # this column was not attempted
STATUS_INVALID = "parse"

#: Row columns, in contract order.
ROW_FIELDS = ("backend", "direct", "proxied", "ms", "note")

# ----------------------------------------------------------------------- user text
# Every string below is end-user copy: no URLs, no exception names, no stack traces.

_NOTE_OK = "可用"
_NOTE_INVALID = "自检项无效：这个后端没有被真正测到"
_NOTE_KEY = "密钥无效或已过期：请到面板重新填写密钥，暂时用免密钥档也能搜"
_NOTE_QUOTA = "额度或频率已用尽：稍后再试，或填入自己的密钥以提高额度"
_NOTE_BUSY = "同时请求过多被限流：稍等几秒再搜一次即可"
_NOTE_COOLDOWN = "该后端刚被拦截正在自动退避：先用其它后端，几分钟后自愈"
_NOTE_BLOCKED = "被反爬验证页挡住：别依赖这个后端，已自动改用其它可用后端"
_NOTE_TIMEOUT = "连接超时：多半需要代理，建议开启系统代理后重测或换后端"
_NOTE_DNS = "域名解析疑似被污染：建议开启代理后重测，或改用可直接访问的后端"
_NOTE_TLS = "加密连接被中断：代理节点不稳，建议更换节点或关掉代理再测"
_NOTE_NETWORK = "网络不通：建议开启系统代理后重测，或换用其它后端"
_NOTE_EMPTY = "没有搜到可用结果：换个说法再试，或改用其它后端"
_NOTE_PARSE = "返回的内容读不出结果：该后端不可靠，建议换后端"

#: classification -> user advice (contract: ``classify_error`` returns ``(kind, 中文建议)``).
ADVICE: dict[str, str] = {
    "ok": _NOTE_OK,
    "blocked": _NOTE_BLOCKED,
    "quota": _NOTE_QUOTA,
    "key": _NOTE_KEY,
    "network": _NOTE_NETWORK,
    "timeout": _NOTE_TIMEOUT,
    "empty": _NOTE_EMPTY,
    "parse": _NOTE_PARSE,
}

VALID_STATUSES = frozenset(ADVICE) | {STATUS_SKIP}

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_STATUS_WORD_RE = re.compile(r"\bhttp\s*\d{3}\b", re.IGNORECASE)

NAN = math.nan
INF = math.inf
Row = Mapping[str, Any]

#: Smallest latency reported for an attempt that really ran (see :func:`_now`).
# Floor is 1 millisecond: a cached probe must still read as "measured" (see
# :func:`_since`) without a real 12s probe being rounded down.
MEASURED_FLOOR = 1.0


def _norm(exc_text: Any) -> str:
    """Whitespace-collapsed, case-folded exception text used for marker matching."""
    return " ".join(str(exc_text).casefold().split())


def _pair(entry: Any) -> tuple[Any, Any]:
    """First two members of a loosely typed pair/mapping, or ``("", None)``.

    Callers hand us ``(backend, closure)`` and ``(backend, ms)``; a malformed entry
    must degrade to "skip this row", never raise across the whole report.
    """
    if isinstance(entry, Mapping):
        name = entry.get("backend") or entry.get("name") or ""
        value: Any = entry.get("probe") if "probe" in entry else entry.get("ms", entry.get("fn"))
        return name, value
    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
        return entry[0], entry[1]
    return "", None


def _as_float(value: Any, default: float = NAN) -> float:
    """Coerce loosely-typed caller data to float without ever raising."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _seconds(value: Any) -> float:
    """A latency usable for ranking: finite and positive, else ``INF`` (sorts last)."""
    number = _as_float(value, NAN)
    return number if (math.isfinite(number) and number > 0) else INF


def _has(text: str, *markers: str) -> bool:
    return any(marker in text for marker in markers)


# ------------------------------------------------------------------ error taxonomy


def _is_timeout_text(text: str) -> bool:
    return _has(text, "timed out", "timeout", "time out", "超时", "时限", "timeouterror")


def _is_dns_text(text: str) -> bool:
    return _has(
        text,
        "getaddrinfo", "name resolution", "nodename nor servname", "known",
        "11001", "11004", "dns", "域名解析", "解析域名", "无法解析主机", "getnameinfo",
    )


def _is_tls_text(text: str) -> bool:
    return _has(text, "ssl", "tls", "eof occurred", "handshake", "certificate", "证书", "握手")


def _is_unreachable_text(text: str) -> bool:
    return _has(
        text,
        "unreachable", "connection refused", "refused", "reset", "aborted",
        "broken pipe", "network is unreachable", "无法连接", "连不上", "网络不可达",
        "网络错误", "连接被重置", "拒绝连接",
    )


def _is_key_text(text: str) -> bool:
    return _has(
        text,
        "invalid api key", "api key", "apikey", "unauthorized", "authorization",
        "bearer", "token", "密钥", "无效密钥", "bad credentials", "forbidden key",
    ) or _has(text, "401")


def _is_busy_text(text: str) -> bool:
    """Our own throttle (or a plain 429) rather than a spent quota."""
    return _has(text, "过于频繁", "繁忙", "请求过多", "too many requests", "限流")


def _is_cooldown_text(text: str) -> bool:
    return _has(text, "冷却", "退避", "penalty", "backoff")


def _is_quota_text(text: str) -> bool:
    return _has(
        text,
        "402", "429", "payment required", "quota", "rate limit", "ratelimit",
        "too many requests", "额度", "配额", "用尽", "超出限制", "过于频繁", "限流", "超额",
    )


def _is_block_text(text: str) -> bool:
    return _has(
        text,
        "安全验证", "人机验证", "验证页", "验证码登录", "captcha", "antispider",
        "access denied", "wappass", "反自动化", "反爬", "滑块", "跳转壳页", "拦截",
        "机器人", "bot check",
    ) or _has(text, "202", "403")


def _is_empty_text(text: str) -> bool:
    return _has(
        text,
        "no results", "not found", "没有结果", "无结果", "未返回正文", "没有匹配",
        "未返回结果", "缺少 results", "非成功状态", "结果为空",
    )


def _is_parse_text(text: str) -> bool:
    return _has(
        text,
        "可解析", "无法解析", "解析失败", "无效 json", "不是 json", "json 解析",
        "structure", "结构无效", "解码", "decode", "unsupported", "不支持",
    )


def classify_error(exc: BaseException | None) -> tuple[str, str]:
    """Map one failure onto ``(kind, 中文建议)``.

    ``kind`` is one of ``ok | blocked | quota | key | network | timeout | empty | parse``.

    Ordering is deliberate: the *type* tells us what the backend layer already
    concluded, so it wins; the *text* only refines ambiguous cases. W1's
    ``ApiKeyRejectedError`` / ``QuotaExhaustedError`` both derive from
    ``SearchProviderError``, so they must be tested before the generic one.
    """
    if exc is None:
        return "ok", _NOTE_OK

    text = _norm(exc)

    # 1. Types from the plugin's own error model (most specific first).
    if isinstance(exc, ApiKeyRejectedError):
        return "key", _NOTE_KEY
    if isinstance(exc, QuotaExhaustedError):
        return "quota", _NOTE_QUOTA
    if isinstance(exc, BlockedError):
        # The key ring reports an Exa pass that never got an answer as
        # ``BlockedError`` too, because that is the one class the backend cooldown
        # punishes. Saying 反爬 there would send the user off to change backends
        # over a socket that is simply dead.
        if _is_timeout_text(text):
            return "timeout", _NOTE_TIMEOUT
        # A throttle and an anti-scrape page share the class but not the advice:
        # "quota" reads as 额度或频率受限, while "blocked" blames 反爬 and tells
        # the user to abandon a backend that only asked too fast.
        return ("quota", _NOTE_BUSY) if _is_busy_text(text) else ("blocked", _NOTE_BLOCKED)
    if isinstance(exc, CooldownError):
        return "blocked", _NOTE_COOLDOWN
    if isinstance(exc, BusyError):
        return "quota", _NOTE_BUSY

    # 2. A timeout is a timeout, whatever wrapped it (duckduckgo's DNS pollution
    #    surfaces as ``NetworkError("网络不可达: timed out")`` on the author's box).
    if isinstance(exc, (TimeoutError, socket.timeout)) or _is_timeout_text(text):
        return "timeout", _NOTE_TIMEOUT

    # 3. Plain HTTP status codes carry their own meaning.
    if isinstance(exc, HttpStatusCodeError):
        return _classify_status(_status_of(exc), text)

    # 4. Transport layer: DNS poisoning, broken TLS, refused connections.
    if isinstance(exc, NetworkError) or _is_unreachable_text(text):
        if _is_dns_text(text):
            return "network", _NOTE_DNS
        if _is_tls_text(text):
            return "network", _NOTE_TLS
        return "network", _NOTE_NETWORK

    # 5. Text refinement for provider errors and for anything unexpected. Ordering here
    #    mirrors the type branches above, so an error class from a second loaded copy of
    #    the plugin still gets the same bucket *and* the same sentence.
    if _is_key_text(text):
        return "key", _NOTE_KEY
    if _is_busy_text(text):
        return "quota", _NOTE_BUSY
    if _is_quota_text(text):
        return "quota", _NOTE_QUOTA
    if _is_cooldown_text(text):
        return "blocked", _NOTE_COOLDOWN
    if _is_block_text(text):
        return "blocked", _NOTE_BLOCKED
    if _is_parse_text(text):
        return "parse", _NOTE_PARSE
    if _is_empty_text(text):
        return "empty", _NOTE_EMPTY
    if _is_dns_text(text):
        return "network", _NOTE_DNS
    if _is_tls_text(text):
        return "network", _NOTE_TLS
    if isinstance(exc, (SearchProviderError, _net.ResponseTooLargeError)):
        # The backend answered nothing usable but gave no sharper reason.
        return "empty", _NOTE_EMPTY
    if isinstance(exc, (ValueError, TypeError, AttributeError, KeyError, ImportError)):
        return "parse", _NOTE_PARSE
    return "network", _NOTE_NETWORK


def _status_of(exc: BaseException) -> int:
    try:
        return int(getattr(exc, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _classify_status(status: int, text: str) -> tuple[str, str]:
    if status in (401, 407):
        return "key", _NOTE_KEY
    if status == 403:
        return ("key", _NOTE_KEY) if _is_key_text(text) else ("blocked", _NOTE_BLOCKED)
    if status in (402, 429, 451):
        return "quota", _NOTE_QUOTA
    if status in (204, 404, 405, 409, 410, 414, 418):
        return "parse", _NOTE_PARSE
    if status >= 500:
        return "network", _NOTE_NETWORK
    if status in (301, 302, 303, 307, 308):
        return "blocked", _NOTE_BLOCKED
    return "network", _NOTE_NETWORK


# --------------------------------------------------------------- result inspection


def usable_results(value: Any) -> tuple[bool, str]:
    """``(is_usable, status)`` for whatever a probe returned.

    ``[]`` means "answered but nothing came back" (``empty``); a non-list or a list
    of dicts without a single useful field means "we could not read it" (``parse``) —
    the sogou case from plan §0.
    """
    if value is None:
        return False, "empty"
    if not isinstance(value, (list, tuple)):
        return False, "parse"
    if not len(value):
        return False, "empty"
    dicts = [item for item in value if isinstance(item, dict)]
    if not dicts:
        return False, "parse"
    for item in dicts:
        for field in RESULT_FIELDS:
            if str(item.get(field) or "").strip():
                return True, STATUS_OK
    return False, "parse"


# ------------------------------------------------------------------ chain advice


def recommend(detected: Sequence[tuple[str, float]], *, proxy_detected: bool) -> list[str]:
    """Order usable backends fastest-first; never suggest a proxy-only backend to a
    machine with no proxy (plan §1.1).

    A missing, unparsable or non-positive latency means "nobody measured this one", so
    it keeps input order at the tail instead of pretending to be instant; duplicates
    collapse onto their best measurement.
    """
    scored: list[tuple[float, int, str]] = []
    seen: dict[str, float] = {}
    for index, entry in enumerate(detected or ()):
        name, seconds = _split_entry(entry)
        if not name:
            continue
        lowered = name.casefold()
        if lowered in PROXY_ONLY_BACKENDS and not proxy_detected:
            continue
        rank = _seconds(seconds)
        if lowered in seen:
            if rank >= seen[lowered]:
                continue
            scored = [item for item in scored if item[2].casefold() != lowered]
        seen[lowered] = rank
        scored.append((rank, index, name))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [name for _, _, name in scored]


def _split_entry(entry: Any) -> tuple[str, float]:
    raw_name, raw_ms = _pair(entry)
    return str(raw_name or "").strip(), _as_float(raw_ms)


def proxy_available() -> bool:
    """Local-only proxy advertisement check (registry/env); it never opens a connection."""
    advertised = True
    try:
        advertised = bool(_net.system_proxy_present())
    except Exception:  # pragma: no cover - a broken proxy stack is reported as "no proxy"
        advertised = False
    return advertised


# ------------------------------------------------------------------ orchestration


@dataclass(frozen=True)
class _Attempt:
    status: str
    ms: float
    note: str


ProbeEntry = tuple[str, Any]


def _normalise_probes(probes: Iterable[Any] | None) -> list[ProbeEntry]:
    out: list[ProbeEntry] = []
    for entry in probes or ():
        raw_name, raw_fn = _pair(entry)
        name = str(raw_name or "").strip() or "unknown"
        out.append((name, raw_fn if callable(raw_fn) else None))
    return out


def _normalise_proxy_probes(
    proxied_probes: Mapping[str, Callable[[], list[dict]]]
    | Sequence[tuple[str, Callable[[], list[dict]]]]
    | None,
) -> dict[str, Any]:
    """``{backend: closure}`` from either a mapping or a list of pairs."""
    out: dict[str, Any] = {}
    if not proxied_probes:
        return {}
    pairs: Iterable[Any]
    pairs = proxied_probes.items() if isinstance(proxied_probes, Mapping) else proxied_probes
    for entry in pairs:
        raw_name, raw_fn = _pair(entry)
        name = str(raw_name or "").strip()
        if name and name.casefold() not in out and callable(raw_fn):
            out[name.casefold()] = raw_fn
    return out


def _now() -> float:
    """Elapsed-time clock for latencies.

    ``time.monotonic()`` is ``GetTickCount64()`` on Windows with a **15.6ms** tick, so a
    cached or very fast probe measures exactly ``0.0`` -- which :func:`recommend` reads as
    "not measured" and would push to the tail of the chain. ``perf_counter()`` is
    ``QueryPerformanceCounter()`` (100ns) and is the documented elapsed-time clock.
    """
    return time.perf_counter()


def _since(marker: float) -> float:
    """Elapsed **milliseconds**, floored so a real measurement never looks like "no data".

    ``perf_counter`` deltas are seconds; the row field is named ``ms`` and the panel
    renders it as milliseconds, so the conversion belongs here rather than in every
    consumer.
    """
    return max(MEASURED_FLOOR, (_now() - marker) * 1000.0)


def _attempt(kind: str, ms: float, note: str) -> _Attempt:
    """Build an attempt, clamping anything outside :data:`VALID_STATUSES`.

    The panel renders one badge per status token, so an unknown token (a future W1
    error class we do not know yet) must degrade to "network", never to a blank cell.
    """
    if kind not in VALID_STATUSES:
        return _Attempt("network", max(0.0, ms), _NOTE_NETWORK)
    return _Attempt(kind, max(0.0, ms), note)


def _run_one(fn: Any) -> _Attempt:
    """Execute one probe closure and never let it escape as an exception."""
    if not callable(fn):
        return _attempt(STATUS_INVALID, 0.0, _NOTE_INVALID)
    started = _now()
    try:
        value = fn()
    except BaseException as exc:  # deliberate: one bad backend must not kill the table
        kind, note = classify_error(exc)
        return _attempt(kind, _since(started), note)
    elapsed = _since(started)
    ok, status = usable_results(value)
    return _attempt(STATUS_OK if ok else status, elapsed, ADVICE.get(status, _NOTE_NETWORK))


def _execute(
    jobs: Sequence[tuple[int, str, Any]],
    *,
    per_probe_timeout: float | None,
    max_workers: int,
) -> dict[tuple[int, str], _Attempt]:
    """Run every job concurrently, bounding each one by ``per_probe_timeout``.

    Each job is ``(row_index, mode, closure)``; the row index alone is not an
    identity because a backend has a direct *and* a proxied job. The pool is never
    joined after a backstop timeout, so a probe that ignores its own budget costs one
    worker slot and nothing else: wall time stays ~one round, not ``O(probes)``.
    """
    outcomes: dict[tuple[int, str], _Attempt] = {}
    if not jobs:
        return outcomes

    workers = max(1, min(MAX_WORKERS, _as_int(max_workers, MAX_WORKERS) or 1, len(jobs)))
    budget = _as_float(per_probe_timeout, NAN)
    budget = budget if math.isfinite(budget) and budget > 0 else None
    keys = [(index, mode) for index, mode, _fn in jobs]
    started_at: dict[tuple[int, str], float] = {}
    guard = threading.Lock()

    def wrap(key: tuple[int, str], fn: Any) -> _Attempt:
        with guard:
            started_at[key] = _now()
        return _run_one(fn)

    # A queued job may not get a worker for a while, so the hard stop allows for the
    # rounds the pool needs; anything still unfinished there is reported as a timeout.
    rounds = math.ceil(len(jobs) / workers)
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="netcheck")
    try:
        futures: dict[Future, tuple[int, str]] = {
            executor.submit(wrap, key, job[2]): key for key, job in zip(keys, jobs)
        }
        pending = set(futures)
        started = _now()
        hard_stop = started + budget * rounds + 0.5 if budget else INF
        while pending:
            now = _now()
            if budget:
                for future in list(pending):
                    key = futures[future]
                    with guard:
                        began = started_at.get(key)
                    if began is None:
                        # Never got a worker: still a timeout from the user's point of view.
                        began = now if now > hard_stop else None
                    if began is not None and now - began > budget:
                        outcomes[key] = _Attempt("timeout", _since(began), _NOTE_TIMEOUT)
                        pending.discard(future)
            if not pending:
                break
            slice_seconds = 0.02 if budget is None else max(0.001, min(0.02, hard_stop - now))
            done, _still = wait(list(pending), timeout=slice_seconds)
            for future in done:
                if future not in pending:
                    continue
                pending.discard(future)
                outcomes[futures[future]] = _collect(future)
            if not pending or _now() > hard_stop:
                if not pending:
                    break
                for future in list(pending):
                    outcomes[futures[future]] = _Attempt("timeout", 0.0, _NOTE_TIMEOUT)
                pending.clear()
    finally:
        # cancel_futures only: never join a probe that ignored its own timeout.
        executor.shutdown(wait=False, cancel_futures=True)
    return outcomes


def _collect(future: Future) -> _Attempt:
    try:
        result = future.result()
    except BaseException as exc:  # pragma: no cover - _run_one swallows probes
        kind, note = classify_error(exc)
        return _attempt(kind, 0.0, note)
    return result if isinstance(result, _Attempt) else _Attempt("network", 0.0, _NOTE_NETWORK)


def _round(value: float) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return 0.0


def _row(name: str, direct: _Attempt, proxied: _Attempt | None) -> dict[str, Any]:
    """Merge the two columns of one backend into the contract's row shape."""
    best = direct
    if direct.status != STATUS_OK and proxied is not None and proxied.status == STATUS_OK:
        best = proxied
    note = direct.note
    if direct.status == STATUS_OK:
        note = _NOTE_OK if proxied is None or proxied.status == STATUS_OK else "直连可用；走代理反而不通"
    elif proxied is not None and proxied.status == STATUS_OK:
        note = "直连不通，走代理可用"
    elif proxied is not None and proxied.status != STATUS_OK and direct.status == proxied.status:
        note = direct.note
    return {
        "backend": name,
        "direct": direct.status,
        "proxied": proxied.status if proxied is not None else STATUS_SKIP,
        "ms": _round(best.ms),
        "note": note,
    }


def run(
    probes: list[tuple[str, Callable[[], list[dict]]]],
    *,
    allow_proxy: bool,
    per_probe_timeout: float | None = DEFAULT_PROBE_TIMEOUT,
    max_workers: int = MAX_WORKERS,
    proxy_detected: bool | None = None,
    proxied_probes: Mapping[str, Callable[[], list[dict]]]
    | Sequence[tuple[str, Callable[[], list[dict]]]]
    | None = None,
) -> dict[str, Any]:
    """Self-check every backend and return the panel-ready report.

    ``probes`` are direct-path closures ``(backend, callable)``. ``proxied_probes``
    (optional, additive to the seam contract) maps the same backend names onto
    proxy-path closures; they are only built and run when ``allow_proxy`` is true,
    and each probe is isolated in its own worker thread.
    """
    entries = _normalise_probes(probes)
    proxy_closures = _normalise_proxy_probes(proxied_probes) if allow_proxy else {}

    jobs: list[tuple[int, str, Any]] = [
        (index, STATUS_DIRECT, fn) for index, (_name, fn) in enumerate(entries)
    ]
    for index, (name, _fn) in enumerate(entries):
        closure = proxy_closures.get(name.casefold())
        if closure is not None:
            jobs.append((index, STATUS_PROXIED, closure))

    outcomes = _execute(jobs, per_probe_timeout=per_probe_timeout, max_workers=max_workers)

    rows: list[dict[str, Any]] = []
    for index, (name, _fn) in enumerate(entries):
        direct = outcomes.get((index, STATUS_DIRECT)) or _Attempt("network", 0.0, _NOTE_NETWORK)
        proxied = outcomes.get((index, STATUS_PROXIED))
        rows.append(_row(name, direct, proxied))

    detected = bool(proxy_available() if proxy_detected is None else bool(proxy_detected))
    chain = recommend(
        [(str(row["backend"]), _as_float(row.get("ms"), 0.0)) for row in rows if _is_usable(row)],
        proxy_detected=detected,
    )
    return {
        "rows": rows,
        "proxy_detected": detected,
        "recommended_chain": chain,
        "summary": _scrub(_summarise(rows, chain, allow_proxy=allow_proxy,
                                      proxy_detected=detected)),
    }


def _is_usable(row: Row) -> bool:
    return row.get("direct") == STATUS_OK or row.get("proxied") == STATUS_OK


def _scrub(text: str) -> str:
    """Last line of defence before user-facing copy: no URLs, no status noise, one line."""
    cleaned = _URL_RE.sub("该地址", str(text or ""))
    cleaned = _STATUS_WORD_RE.sub("异常状态", cleaned)
    cleaned = " ".join(cleaned.splitlines()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > SUMMARY_MAX_CHARS:
        cleaned = cleaned[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return cleaned


def _counts(rows: Sequence[Row]) -> tuple[list[Row], list[Row], list[Row]]:
    direct_ok = [row for row in rows if row.get("direct") == STATUS_OK]
    proxy_only = [row for row in rows
                  if row.get("direct") != STATUS_OK and row.get("proxied") == STATUS_OK]
    failed = [row for row in rows if not _is_usable(row)]
    return direct_ok, proxy_only, failed


# Which failure the user must act on first: a machine that cannot reach anything
# needs a proxy decision, and everything else (quota, key, anti-bot) is secondary.
_CAUSE_PRIORITY = ("timeout", "network", "quota", "key", "blocked", "empty", "parse")

_CAUSE_LONG = {
    "blocked": "部分后端返回反爬验证页",
    "quota": "部分后端额度或频率受限",
    "key": "密钥被判无效",
    "network": "本机直连这些域名不通",
    "timeout": "连接超时（多半需要代理）",
    "empty": "后端没有返回可用结果",
    "parse": "后端返回的内容读不出结果",
    "ok": "",
}

_CAUSE_SHORT = {
    "blocked": "反爬拦截",
    "quota": "额度受限",
    "key": "密钥无效",
    "network": "连不上",
    "timeout": "连接超时",
    "empty": "没有结果",
    "parse": "结果读不出",
    "ok": "可用",
}

_NEXT_STEP = {
    "timeout": "先开启系统代理（或代理客户端的虚拟网卡模式），再点自检重测一次。",
    "network": "先开启系统代理（或代理客户端的虚拟网卡模式），再点自检重测一次。",
    "quota": "稍等几分钟再搜；或在面板里填入自己的密钥，额度会更高。",
    "key": "到插件面板重新填写密钥；不填也能用免密钥档，只是额度低一些。",
    "blocked": "不用折腾代理：这类后端本来就挡机器人，换用托管后端更稳。",
    "empty": "换个说法再搜一次，或在面板里调整后端链路。",
    "parse": "这个后端读不出结果，建议在面板里把它移出后端链路。",
}


def _tally(rows: Sequence[Row]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("direct") or "network")
        if status == STATUS_SKIP:
            status = str(row.get("proxied") or "network")
        if status and status != STATUS_OK:
            counts[status] = counts.get(status, 0) + 1
    return counts


def _dominant(rows: Sequence[Row]) -> str:
    """The most actionable failure among ``rows`` (see :data:`_CAUSE_PRIORITY`)."""
    counts = _tally(rows)
    for kind in _CAUSE_PRIORITY:
        if kind in counts:
            return kind
    return "network"


def _cause_phrase(rows: Sequence[Row]) -> str:
    """Describe the failures honestly: one cause gets a sentence, a mix gets a list.

    Saying "多数后端被反爬挡住" when only 1 of 3 rows was blocked is exactly the kind
    of wrong conclusion that sends a non-technical user down the wrong path.
    """
    counts = _tally(rows)
    kinds = [kind for kind in _CAUSE_PRIORITY if kind in counts]
    if not kinds:
        return "部分后端不可用"
    total = sum(counts.values())
    top = kinds[0]
    if counts[top] == total:
        return _CAUSE_LONG.get(top, "部分后端不可用")
    return "失败原因有" + "、".join(_CAUSE_SHORT.get(kind, "其它") for kind in kinds[:3])


def _seconds_text(value_ms: Any) -> str:
    """Render a millisecond latency as the Chinese "约 X 秒" phrase for the summary."""
    seconds = _as_float(value_ms, 0.0) / 1000.0
    if seconds < 0.1:
        return "不到 0.1"
    return f"{seconds:.1f}"


def _summarise(
    rows: Sequence[Row],
    chain: Sequence[str],
    *,
    allow_proxy: bool,
    proxy_detected: bool,
) -> str:
    if not rows:
        return "本次没有可测的后端，自检未执行：请先确认搜索链路配置。"

    direct_ok, proxy_only, failed = _counts(rows)
    parts: list[str] = []

    if direct_ok:
        fastest = min(direct_ok, key=lambda row: _seconds(row.get("ms")))
        parts.append(
            f"不开代理就能用：{len(direct_ok)} 个后端直连成功，"
            f"最快 {fastest.get('backend')} 约 {_seconds_text(fastest.get('ms'))} 秒。"
        )
        if proxy_only:
            parts.append(f"另有 {len(proxy_only)} 个需开代理才通。")
        parts.append(f"建议链路：{' → '.join(chain)}。")
        if failed:
            parts.append(f"{_cause_phrase(failed)}，已自动跳过。")
        return "".join(parts)

    if proxy_only:
        names = "、".join(str(row.get("backend")) for row in proxy_only[:4])
        parts.append(f"直连全部失败，但开代理后 {len(proxy_only)} 个后端可用（{names}）。")
        parts.append("下一步：开启系统代理或客户端的虚拟网卡模式，再搜一次。")
        parts.append(f"建议链路：{' → '.join(chain)}。")
        return "".join(parts)

    kind = _dominant(failed or rows)
    parts.append(f"暂时搜不到：{_cause_phrase(failed or rows)}。")
    if kind in ("timeout", "network") and proxy_detected:
        parts.append("下一步：代理已检测到但走不通，请检查节点是否可用或换一个节点。")
    else:
        parts.append("下一步：" + _NEXT_STEP.get(kind, "换用其它后端，或稍后再试一次。"))
    if chain:
        parts.append(f"建议链路：{' → '.join(chain)}。")
    return "".join(parts)
