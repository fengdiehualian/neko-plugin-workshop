"""better_web_search - keyless web search and page reading for N.E.K.O.

Design constraints, in order of why they matter:

1. **No API key required.** The default chain uses Exa's public MCP gateway and
   AnySearch's anonymous tier, both verified to work without credentials, plus
   direct HTML scraping of Bing/Baidu. Keys are optional upgrades, never
   prerequisites: a bad or spent key silently degrades to the anonymous tier for
   that call instead of failing the user's search.
2. **Zero third-party dependencies.** The host's plugin packaging rule is: any
   Python dependency declared in ``pyproject.toml`` must be vendored into
   ``vendor/``. This plugin uses only the standard library so the distributed
   ``.neko-plugin`` package stays tiny and cannot break on a version clash.
3. **Proxy-aware.** Search engines that are unreachable from a given network are
   useless no matter how good the parser is, so each route carries its own proxy
   policy (``auto`` / ``direct`` / ``proxy``) resolved against the OS and
   environment proxy settings, and DuckDuckGo is trimmed from the effective
   chain entirely when no proxy exists (measured: polluted DNS -> timeout).
4. **Read, not just find.** ``search`` alone gives the model titles and
   snippets; ``fetch`` gives it the actual page, which is what makes an answer
   trustworthy.
5. **The panel owns the secrets.** ``exa_api_keys`` lives only in the plugin
   config, and per-key health (which key is out of credits) is derived from what
   Exa actually answered -- never from a stored balance, because Exa's usage API
   reports spend only and is gated per team. Every surface that leaves this
   process (logs, ``report_status``, entry results, panel context) carries the
   masked form ``exa****tail4`` at most, because the host's status payload is
   logged verbatim at INFO (``core/context.py:703``) and the log redaction list
   is dead code (``plugin/logging_config.py:174-185``).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any, Callable, Dict, List, Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    ui,
)

from . import _diagnose, _guard, _host, _net, _parsing, _providers
from ._resilience import (
    ApiKeyRejectedError,
    BlockedError,
    BusyError,
    CooldownError,
    QuotaExhaustedError,
    SearchCoordinator,
    SearchProviderError,
)

# Default chain, measured on a real mainland desktop network with the proxy off
# (plan-v0.2 §0; keep README's table in sync with these numbers):
#   anysearch  keyless anonymous tier, direct 1.2-5.0s                            -> primary
#   exa        public MCP gateway, keyless, direct 1.6-3.5s, returns page text;
#              the keyless tier is one shared pool, so it leads only when the user
#              registers keys (_exa_leads) -- otherwise it stays second
#   bing       direct 0.6s, best Chinese + English HTML results                   -> default now
#   baidu      direct answers "百度安全验证" without a BAIDUID warm-up cookie;
#              with warmup it is usable but IP-burst sensitive                   -> last
#   duckduckgo direct times out (DNS pollution), TLS EOF even via some proxies;
#              it is NOT in the default chain and joins the *effective* chain
#              only when a proxy is actually available (duckduckgo_needs_proxy).
#   sogou      no parseable results on the test network: implemented, never default.
DEFAULT_CHAIN = ("anysearch", "exa", "bing", "baidu")

# The host copies config.example.toml into the plugin's runtime config **once**, at first
# install, and never touches an existing file again (config_paths.py:148-153). So every
# install keeps the chain order that was current when it was installed, and changing the
# default reaches nobody who already installed: a 0.9.6-era user pinned
# RETIRED_DEFAULT_CHAIN in their own file and kept leading with keyless Exa forever.
# Treating exactly that list as "never chosen" un-pins them. Any other order is a hand
# edit and stays authoritative; wanting Exa first has a first-class knob anyway
# ([search] backend = "exa"), which _ordered_chain() still honours over this.
RETIRED_DEFAULT_CHAIN = ("exa", "anysearch", "bing", "baidu")

# Config sections the plugin reads; missing sections fall back to defaults so a
# v0.1 install (which only has [search]) keeps working untouched.
CONFIG_SECTIONS = ("search", "net", "ui")

# fake-ip range trusted for DNS *results* of fetch targets (TUN proxy mode).
# Literal-IP targets, metadata addresses, *.local etc. are still refused by
# _guard -- this list only excuses what getaddrinfo answers (plan §1.4).
DEFAULT_SSRF_ALLOW_RANGES = ("198.18.0.0/15",)

# How long a successful host-status read stays good for. Every panel action ends
# with a context refresh, and each refresh used to pay a live loopback round trip
# (capped at 2.5 s) -- that is what made the switches feel like they hung.
HOST_STATE_TTL_SECONDS = 20.0

# Where the Exa key ring currently starts, kept in the plugin's own KV store
# ([plugin.store]) rather than the config: a config write rebuilds the
# coordinators and drops every search cache with it.
KEY_RING_STORE_KEY = "exa_key_ring"

_ERROR_CODES = {
    "blocked": "BETTER_WEB_SEARCH_BLOCKED",
    "busy": "BETTER_WEB_SEARCH_BUSY",
    "cooldown": "BETTER_WEB_SEARCH_COOLDOWN",
    "key_invalid": "BETTER_WEB_SEARCH_KEY_INVALID",
    "quota": "BETTER_WEB_SEARCH_QUOTA",
}

# User-facing copy. Never interpolate raw upstream text or the key here.
_MSG_KEY_INVALID = "Exa 密钥没验通过：这把已跳过，请到面板重新填写或加一把"
_MSG_QUOTA = "Exa 密钥额度已用尽：这把已跳过，池里还有别的钥匙就接着用，额度每月自动刷新"
# Raised once the whole ring failed: wording lands in the "限流" bucket of the
# self-check, which is what a dead pool actually looks like from the outside.
_MSG_POOL_SPENT = "Exa 密钥池全部限流或额度用尽：本次改用其它搜索来源，稍后会再试"
# A pass that could not reach Exa at all. Kept out of the ring on purpose: see
# _call_with_exa_keys. The wording must carry 超时/连不上 so the self-check still
# reports it as a network problem rather than 反爬.
_MSG_EXA_UNREACHABLE = "Exa 这次连不上（超时或网络不可达）：本次改用其它搜索来源，稍后会再试"
_MSG_NO_HOST = "未能读取宿主插件状态，请到插件中心确认『网络搜索』是否在运行"
_QUOTA_NOTE = "每个账号每月刷新 $10 ≈ 1400 次；多填几把会自动轮流用，不填也能搜，只是匿名档慢且限额低"
_ONBOARDING_HINT = (
    "主人，『更好的网络搜索』装好啦。请到宿主「插件中心」把自带的『网络搜索』停用，"
    "对话里的联网搜索就交给本插件——这一步要你亲手点，插件不替你按宿主的开关。"
    "不填任何密钥也能直接搜；想搜得更多更稳，再去面板「进阶」里填 Exa 或 AnySearch 的免费密钥。"
)


def _error_code(error: BaseException) -> str:
    # isinstance first: ApiKeyRejectedError/QuotaExhaustedError derive from
    # SearchProviderError and their upstream text must not be trusted for
    # classification (a server-side message could contain the word "blocked").
    if isinstance(error, ApiKeyRejectedError):
        return _ERROR_CODES["key_invalid"]
    if isinstance(error, QuotaExhaustedError):
        return _ERROR_CODES["quota"]
    text = f"{type(error).__name__} {error}".casefold()
    if "cooldown" in text or isinstance(error, CooldownError):
        return _ERROR_CODES["cooldown"]
    if "busy" in text or isinstance(error, BusyError):
        return _ERROR_CODES["busy"]
    if "blocked" in text or isinstance(error, BlockedError):
        return _ERROR_CODES["blocked"]
    if "apikeyrejected" in text or "invalid api key" in text:
        return _ERROR_CODES["key_invalid"]
    return ""


async def _in_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(func, *args, **kwargs)


@neko_plugin
class BetterWebSearchPlugin(NekoPluginBase):
    """Free, keyless web search + page reading, with a setup panel."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        # [search] view; _num/_int/_text keep reading it (many call sites).
        self._cfg: Dict[str, Any] = {}
        # One line per reload, not one per search: the retired-default alias is a
        # fact about this config file, and a search-path log line would repeat it
        # every query forever.
        self._retired_chain_noted = False
        # All four sections, tolerant of missing ones (v0.1 configs, partial
        # user overrides). Written as a whole by _load_sections so a reload can
        # never leave a half-updated view.
        self._sections: Dict[str, Dict[str, Any]] = {name: {} for name in CONFIG_SECTIONS}
        self._coordinators: Dict[str, SearchCoordinator] = {}
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        # Exa key health shown on the panel. These strings are fixed copy;
        # nothing here (or anywhere the key touches) ever stores the key itself.
        self._key_state: str = "unknown"  # unknown|valid|invalid
        self._quota_state: str = ""       # ""|exhausted
        self._exa_last_error: str = ""
        # Per-key verdicts, keyed by fingerprint: what Exa last said about this
        # key ("ok" | "exhausted" | "invalid"). Pure display -- the ring position,
        # not a verdict, decides who gets tried next.
        self._key_health: Dict[str, str] = {}
        # The key the next search starts from, and what is already in the store.
        # Restored at startup so a reload does not restart the rotation.
        self._key_at: str = ""
        self._key_at_saved: str = ""
        # Same idea for the single optional AnySearch key: what AnySearch last said
        # about it ("unknown"|"valid"|"invalid"). Display only -- the search works
        # with or without this key, so nothing in the chain reads it.
        self._any_key_state: str = "unknown"
        # Last good host-status read: (monotonic stamp, state dict). The panel
        # refreshes after every action; re-asking the host each time added seconds.
        self._host_state_cache: Optional[tuple] = None
        # The one search the user just asked about, shaped for the panel. Only
        # counts, timings and backend names -- never the query text.
        self._last_search: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------

    async def _load_sections(self) -> None:
        """Read [search] [net] [ui] from the host's merged config."""
        # 8s, not the SDK's 5.0 default: this read is also the read-back that
        # settles whether a lost-acknowledgement write actually landed, so it is
        # correctness-critical and must outlive a slow host.
        cfg = await self.config.dump(timeout=8.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        sections: Dict[str, Dict[str, Any]] = {}
        for name in CONFIG_SECTIONS:
            table = cfg.get(name)
            sections[name] = dict(table) if isinstance(table, dict) else {}
        self._sections = sections
        self._cfg = sections["search"]

    def _section(self, name: str) -> Dict[str, Any]:
        table = self._sections.get(name)
        return table if isinstance(table, dict) else {}

    def _raw(self, section: str, key: str, default: Any = None) -> Any:
        return self._section(section).get(key, default)

    def _text_in(self, section: str, key: str, default: str = "") -> str:
        raw = self._raw(section, key, default)
        return raw.strip() if isinstance(raw, str) else default

    def _flag_in(self, section: str, key: str, default: bool) -> bool:
        """Tolerant bool: TOML bool, 0/1, or true/false/yes/no/on/off strings."""
        raw = self._raw(section, key, default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            lowered = raw.strip().casefold()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off", ""}:
                return False
            return default
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return bool(raw)
        return default

    def _list_in(self, section: str, key: str, default: tuple) -> List[str]:
        """List getter that distinguishes *absent* (-> default) from *empty*.

        ``ssrf_allow_ranges = []`` must mean "allow nothing", not "default".
        """
        raw = self._raw(section, key, None)
        if raw is None:
            return [str(item) for item in default]
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return [str(item) for item in default]
        return [str(item).strip() for item in raw if str(item).strip()]

    # -- [search] helpers (names/defaults frozen from v0.1, do not change) --

    def _num(self, key: str, default: float, low: float, high: float) -> float:
        raw = self._cfg.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = float(default)
        return max(low, min(value, high))

    def _int(self, key: str, default: int, low: int, high: int) -> int:
        raw = self._cfg.get(key, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = int(default)
        return max(low, min(value, high))

    def _text(self, key: str, default: str = "") -> str:
        raw = self._cfg.get(key, default)
        return raw.strip() if isinstance(raw, str) else default

    def _chain(self) -> List[str]:
        raw = self._cfg.get("backend_chain")
        names: List[str] = []
        if isinstance(raw, list):
            names = [str(item).strip().lower() for item in raw if str(item).strip()]
        if names == list(RETIRED_DEFAULT_CHAIN):
            names = []
            if not self._retired_chain_noted:
                self._retired_chain_noted = True
                self.logger.info(
                    "backend_chain is the retired default {}, reading it as unset and "
                    "using {}; delete that key, or set [search] backend, to decide by hand",
                    list(RETIRED_DEFAULT_CHAIN), list(DEFAULT_CHAIN),
                )
        if not names:
            names = list(DEFAULT_CHAIN)
        valid = {"exa", "anysearch", "bing", "sogou", "baidu", "duckduckgo", "searxng"}
        ordered: List[str] = []
        for name in names:
            if name in valid and name not in ordered:
                ordered.append(name)
        return ordered or list(DEFAULT_CHAIN)

    def _exa_leads(self) -> bool:
        """True when Exa should take the first slot: its own keys, and no AnySearch key.

        Keyless Exa answers out of one pool every anonymous user shares (that is what
        the "Exa 免配额已用完（429）" branch is), so it should not be the first thing a
        search tries. A registered key spends the user's own allowance instead, which
        is the only reason to put it in front.
        """
        if self._text("anysearch_api_key"):
            return False
        return bool(self._exa_keys())

    def _ordered_chain(self) -> List[str]:
        """Configured chain with the head backend promoted to first place.

        The head is the explicit ``[search] backend`` preference when there is one,
        else Exa when _exa_leads() holds, else the configured order as written (which
        leads with anysearch). ``[search] backend`` is a *preference*, not a lock: a
        user who picks bing still gets a result when bing is rate-limited. Locking to
        one engine is the per-call ``backend`` argument's job (see
        _search_with_fallback).
        """
        chain = self._chain()
        preferred = self._text("backend", "auto").lower()
        head = preferred if preferred in chain else ""
        if not head and "exa" in chain and self._exa_leads():
            head = "exa"
        if not head:
            return chain
        return [head] + [name for name in chain if name != head]

    def _route_policy(self, route: str) -> tuple[str, str]:
        """Return ``(policy, proxy_url)`` for one backend route.

        ``proxied`` matters for routes such as DuckDuckGo that are simply
        unreachable from some networks without a proxy; ``direct`` keeps
        mainland-reachable engines off the proxy.
        """
        mode = self._text("proxy", "auto").lower()
        explicit = self._text("proxy_url")
        if mode == "off":
            return _net.POLICY_NONE, ""
        if mode in {"on", "proxy"}:
            return _net.POLICY_SYSTEM, explicit or "system"
        if mode.startswith("http://") or mode.startswith("socks"):
            return _net.POLICY_SYSTEM, mode
        policy = _net.POLICY_AUTO if mode == "auto" else _net.POLICY_NONE
        want_proxy = self._text(f"{route}_proxy", "auto").lower()
        if want_proxy == "proxy":
            return _net.POLICY_SYSTEM, explicit or "system"
        if want_proxy == "direct":
            return _net.POLICY_NONE, ""
        return policy, explicit

    def _proxy_available(self) -> bool:
        """True when a proxy really exists for this run: system/env or explicit URL.

        Covers the three ways a proxy can show up: OS/environment settings
        (``_net.system_proxy_present``), ``proxy_url = "..."``, or ``proxy``
        itself holding a URL (``http://...``/``socks5://...``).
        """
        if self._text("proxy_url"):
            return True
        mode = self._text("proxy", "auto").lower()
        if mode.startswith("http://") or mode.startswith("https://") or mode.startswith("socks"):
            return True
        return bool(_net.system_proxy_present())

    def _ssrf_ranges(self) -> List[str]:
        return self._list_in("net", "ssrf_allow_ranges", DEFAULT_SSRF_ALLOW_RANGES)

    def _exa_tool(self) -> str:
        tool = self._text("exa_tool", "auto").lower()
        return tool if tool in {"auto", "advanced", "simple"} else "auto"

    def _anysearch_zone(self) -> str:
        """AnySearch's zone argument, emptied for anything we do not send."""
        zone = self._text("anysearch_zone").lower()
        return zone if zone in {"cn", "intl"} else ""

    def _coordinator(self, name: str) -> SearchCoordinator:
        coordinator = self._coordinators.get(name)
        if coordinator is None:
            common = {
                "ttl_seconds": self._num("cache_ttl_seconds", 300, 15, 7200),
                "stale_seconds": self._num("stale_ttl_seconds", 1800, 60, 86400),
                "max_entries": self._int("cache_entries", 128, 8, 4096),
                "queue_wait_seconds": self._num("queue_wait_seconds", 2, 0.2, 10),
            }
            spacing = {
                "exa": self._num("exa_min_interval_seconds", 0.5, 0, 30),
                "anysearch": self._num("anysearch_min_interval_seconds", 0.75, 0, 30),
                "bing": self._num("bing_min_interval_seconds", 2, 0, 60),
                "sogou": self._num("sogou_min_interval_seconds", 3, 0, 60),
                "baidu": self._num("baidu_min_interval_seconds", 10, 0, 120),
                "duckduckgo": self._num("duckduckgo_min_interval_seconds", 3, 0, 120),
                "searxng": self._num("searxng_min_interval_seconds", 0.5, 0, 30),
            }.get(name, 1.0)
            penalty = {
                "baidu": self._num("baidu_cooldown_seconds", 120, 5, 7200),
                "duckduckgo": self._num("duckduckgo_cooldown_seconds", 300, 5, 7200),
            }.get(name, self._num("cooldown_seconds", 60, 5, 7200))
            coordinator = SearchCoordinator(
                **common,
                min_interval_seconds=spacing,
                cooldown_seconds=penalty,
                max_cooldown_seconds=min(7200.0, penalty * 8),
            ).bind(name)
            self._coordinators[name] = coordinator
        return coordinator

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        await self._load_sections()
        self._coordinators.clear()
        await self._load_key_ring()

        order = self._ordered_chain()
        effective = self._effective_chain()

        proxies_present = _net.system_proxy_present()
        # Bools only: never log the Exa key itself, its presence is what matters
        # and the host logs plugin status verbatim.
        self.logger.info(
            "better_web_search ready: chain={} effective={} proxy_mode={} system_proxy={} "
            "anysearch_key={} exa_keys={}",
            order, effective, self._text("proxy", "auto"), proxies_present,
            bool(self._text("anysearch_api_key")), len(self._exa_keys()),
        )
        await self._maybe_send_first_run_notice()
        payload = {
            "status": "running",
            "chain": order,
            "effective_chain": effective,
            "proxy_mode": self._text("proxy", "auto"),
            "system_proxy_detected": proxies_present,
            "proxy_usable": self._proxy_available(),
            "anysearch_api_key_configured": bool(self._text("anysearch_api_key")),
            "exa_key_count": len(self._exa_keys()),
            "exa_key_state": self._key_state,
        }
        self._safe_report_status(payload)
        return Ok(payload)

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        return await self.startup()

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self._coordinators.clear()
        return Ok({"status": "stopped"})

    # ------------------------------------------------------------------
    # first-run onboarding (must never fail startup)
    # ------------------------------------------------------------------

    def _safe_report_status(self, payload: Dict[str, Any]) -> None:
        try:
            self.report_status(payload)
        except Exception:  # status reporting is best-effort, never fatal
            self.logger.exception("report_status failed (ignored)")

    async def _maybe_send_first_run_notice(self) -> None:
        """Let the cat-girl introduce the panel, exactly once per install.

        ``push_message`` v2 contract (sdk/plugin/base.py:185-196 +
        shared/core/push_message_schema.py): parts are text dicts,
        ``visibility=[]`` keeps the raw cue out of the chat channel and
        ``ai_behavior="respond"`` makes the AI actually say it. The in-memory
        flag flips *before* the write: ``config.update`` does not fire
        ``config_change``, and even if a reload happens we never re-push.
        """
        try:
            if self._flag_in("ui", "first_run_notice_sent", False):
                return
            try:
                self.push_message(
                    parts=[{"type": "text", "text": _ONBOARDING_HINT}],
                    visibility=[],
                    ai_behavior="respond",
                    source="better_web_search",
                    metadata={"description": "better_web_search 首次使用引导"},
                )
            except Exception as error:
                # Keep the flag false: a user who never saw the hint should get
                # it on the next startup instead of silently losing onboarding.
                self.logger.info("first-run notice push failed: {}:{}", type(error).__name__, error)
                return
            self._sections.setdefault("ui", {})["first_run_notice_sent"] = True
            await self._persist({"ui": {"first_run_notice_sent": True}})
        except Exception:  # onboarding must NEVER turn startup into an Err
            self.logger.exception("first-run notice failed (ignored)")

    # ------------------------------------------------------------------
    # config writes (panel entries land here; config.update does not notify us)
    # ------------------------------------------------------------------

    async def _persist(self, payload: Dict[str, Any]) -> bool:
        """Deep-merge into the plugin config, then reload our own view.

        ``config.update`` is atomic on the host but fires no ``config_change``
        (plan §0), so every writer reloads itself -- same pattern the host's
        lifekit plugin uses (lifekit/__init__.py:531-532).
        """
        try:
            await self.config.update(payload)
        except Exception as error:
            # Seen in the wild on Windows: the host writes the file within a few
            # milliseconds and then loses the acknowledgement, raising
            # "Config persistence response timed out; final persistence status is
            # unknown". The raise therefore cannot mean "not written" -- reading
            # the value back is the only way to tell the two cases apart.
            self.logger.info("config update raised {}:{}", type(error).__name__, error)
            # _verify_persisted reloads our own view, so skip the reload below:
            # stacking two 5 s round trips is what made the panel feel stuck.
            if not await self._verify_persisted(payload):
                return False
            self.logger.info("config did persist; only the acknowledgement was lost")
            return True
        try:
            await self._load_sections()
            self._coordinators.clear()
        except Exception:
            self.logger.exception("config reload failed")
        return True

    async def _verify_persisted(self, payload: Dict[str, Any]) -> bool:
        """True when every key in ``payload`` now reads back from the host."""
        patched: List[tuple[str, str, Any]] = []
        for section, values in (payload or {}).items():
            if isinstance(values, dict):
                patched.extend((str(section), str(key), value) for key, value in values.items())
        if not patched:
            return False
        try:
            await self._load_sections()
        except Exception as error:
            self.logger.info("config read-back failed: {}:{}", type(error).__name__, error)
            return False
        for section, key, expected in patched:
            table = self._section(section)
            if key not in table or table[key] != expected:
                return False
        self._coordinators.clear()
        return True

    # ------------------------------------------------------------------
    # Exa key pool: several accounts, one $10 monthly credit each
    # ------------------------------------------------------------------

    @staticmethod
    def _key_fingerprint(key: str) -> str:
        """Short stable id for one key. The key never becomes a dict key itself."""
        return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()[:8]

    def _exa_keys(self) -> List[str]:
        """Configured keys, de-duplicated, in config order (the ring order)."""
        pool: List[str] = []
        for key in self._list_in("search", "exa_api_keys", ()):
            if key not in pool:
                pool.append(key)
        return pool

    def _call_with_exa_keys(self, attempt: Callable[[str], Any]) -> Any:
        """One ring pass over the pool, ending early when Exa itself is unreachable.

        ``_resilience`` only cools a backend down for ``BlockedError``, and a dead
        socket is no key's fault: stepping sideways would hang once per key, on the
        same broken route, until the whole search budget is gone -- which also
        leaves nothing for the backends behind Exa. One pass is enough to know.
        """
        try:
            return self._exa_ring_pass(attempt)
        except _net.NetworkError as error:
            raise BlockedError(_MSG_EXA_UNREACHABLE) from error

    def _exa_ring_pass(self, attempt: Callable[[str], Any]) -> Any:
        """Round-robin the pool: a key that answers badly yields to the next.

        Deliberately no per-key timers. The ring starts at the key that last
        answered and only steps forward when that one fails, so a spent key is
        not touched again until every other key has had its turn -- which is also
        roughly how long it takes the user to work through the rest of the pool.
        When the whole ring fails, the single ``BlockedError`` below hands Exa to
        the coordinator's penalty box, so the next search does not re-run it.
        """
        pool = self._exa_keys()
        if not pool:
            return attempt("")

        can_degrade = self._flag_in("search", "exa_key_fallback_anonymous", False)
        start = self._key_start(pool)
        for offset in range(len(pool)):
            index = (start + offset) % len(pool)
            key = pool[index]
            finger = self._key_fingerprint(key)
            try:
                results = attempt(key)
            except SearchProviderError as error:
                # Anything that answered -- wrongly, expensively, with a 5xx body
                # -- is worth a step sideways, because the next account is a
                # different identity with its own quota and rate limit. Only
                # 401/402 leave a verdict on *this* key: a 429 says nothing about
                # it, and one fast double-click must not mark a healthy pool dead.
                self._note_exa_key_error(error)
                if isinstance(error, ApiKeyRejectedError):
                    self._key_health[finger] = "invalid"
                elif isinstance(error, QuotaExhaustedError):
                    self._key_health[finger] = "exhausted"
                self.logger.info("exa key {} failed ({})", finger, type(error).__name__)
                continue
            self._key_at = finger
            self._key_health[finger] = "ok"
            self._key_state = "valid"
            self._quota_state = ""
            self._exa_last_error = ""
            # Which of the pool answered is otherwise unanswerable from the logs,
            # and "did my new key get used?" is the first thing to ask after a
            # rotation change. The fingerprint is a hash, not the secret.
            self.logger.info("exa served by key {}", finger)
            return results

        if can_degrade:
            # Returns or raises: an anonymous failure must propagate untouched so
            # the backend chain moves on, and a key verdict set above must not be
            # painted over by this attempt's success.
            self.logger.info("exa degraded to anonymous tier (no usable key left)")
            return attempt("")
        raise BlockedError(_MSG_POOL_SPENT)

    def _key_start(self, pool: List[str]) -> int:
        """Where the ring begins this time: the position of the key that last answered.

        Held as a fingerprint rather than an index, so reordering or hand-editing
        ``exa_api_keys`` cannot silently point the ring at a different account.
        """
        if self._key_at:
            for index, key in enumerate(pool):
                if self._key_fingerprint(key) == self._key_at:
                    return index
        return 0

    async def _save_key_ring(self) -> None:
        """Persist the ring position, so a reload does not restart the rotation.

        Written only when the position actually moved -- that is once per spent
        key, not once per search. Best effort: a store that is unavailable must
        never turn a working search into an error.
        """
        store = getattr(self, "store", None)
        if store is None or not getattr(store, "enabled", False):
            return
        if self._key_at == self._key_at_saved:
            return
        try:
            outcome = await store.set(KEY_RING_STORE_KEY, {"at": self._key_at})
        except Exception as error:
            self.logger.info("key ring position not saved: {}:{}", type(error).__name__, error)
            return
        if outcome.is_ok():
            self._key_at_saved = self._key_at

    async def _load_key_ring(self) -> None:
        """Restore the ring position; a stale or missing record means key #1."""
        store = getattr(self, "store", None)
        if store is None or not getattr(store, "enabled", False):
            return
        try:
            outcome = await store.get(KEY_RING_STORE_KEY, None)
        except Exception as error:
            self.logger.info("key ring position not read: {}:{}", type(error).__name__, error)
            return
        if not outcome.is_ok():
            return
        value = outcome.value
        saved = str(value.get("at") or "") if isinstance(value, dict) else ""
        # A key the user has since removed must not drag the ring onto its
        # neighbour: fall back to the start of the list instead.
        if saved and any(self._key_fingerprint(key) == saved for key in self._exa_keys()):
            self._key_at = saved
        self._key_at_saved = self._key_at

    # ------------------------------------------------------------------
    # backend dispatch
    # ------------------------------------------------------------------

    def _fetcher(self, name: str, query: str, limit: int, timeout: float, *,
                 policy: Optional[str] = None,
                 proxy_url: Optional[str] = None) -> Callable[[], Any]:
        if policy is None or proxy_url is None:
            policy, proxy_url = self._route_policy(name)
        if name == "exa":
            tool = self._exa_tool()
            live = self._cfg.get("exa_live_crawl") is True  # pi-lens-ignore: no-identity-operator-on-literals

            def call_exa() -> Any:
                return self._call_with_exa_keys(lambda key: _providers.search_exa(
                    query, limit, timeout=timeout, policy=policy, proxy_url=proxy_url,
                    live_crawl=live, api_key=key, tool=tool))

            return call_exa
        if name == "anysearch":
            return lambda: _providers.search_anysearch(
                query, limit, timeout=timeout, policy=policy, proxy_url=proxy_url,
                api_key=self._anysearch_key(), zone=self._anysearch_zone())
        if name == "searxng":
            return lambda: _providers.search_searxng(
                query, limit, base_url=self._text("searxng_base_url"),
                timeout=timeout, policy=policy, proxy_url=proxy_url)
        provider = _providers.PROVIDERS[name]
        method = self._text(f"{name}_method", "POST" if name == "duckduckgo" else "GET")
        warmup = self._flag_in("search", "baidu_warmup", True)
        return lambda: _providers.search_html(
            provider, query, limit, timeout=timeout, policy=policy,
            proxy_url=proxy_url, method=method, warmup=warmup)

    def _note_exa_key_error(self, error: BaseException) -> None:
        """Record panel state from fixed copy -- never from upstream text."""
        if isinstance(error, ApiKeyRejectedError):
            self._key_state = "invalid"
            self._exa_last_error = _MSG_KEY_INVALID
        elif isinstance(error, QuotaExhaustedError):
            self._quota_state = "exhausted"
            self._exa_last_error = _MSG_QUOTA

    async def _search_once(self, name: str, query: str, limit: int,
                           budget: float) -> Dict[str, Any]:
        timeout = min(self._num("timeout_seconds", 12, 2, 30), budget)
        coordinator = self._coordinator(name)

        async def fetch() -> List[Dict[str, str]]:
            results = await _in_thread(self._fetcher(name, query, limit, timeout))
            return [dict(item) for item in results if isinstance(item, dict)]

        async with asyncio.timeout(max(1.0, budget)):
            outcome = await coordinator.execute(query, limit, fetch)
        outcome.setdefault("backend", name)
        if name == "exa":
            # The ring may have stepped to another key to answer this one; a
            # reload should start there, not back at the spent first key.
            await self._save_key_ring()
        return outcome

    _KNOWN_BACKENDS = frozenset({"exa", "anysearch", "searxng"}) | frozenset(
        _providers.PROVIDERS
    )

    def _available(self, name: str) -> bool:
        if name == "searxng":
            return bool(self._text("searxng_base_url"))
        if name == "duckduckgo" and self._flag_in("search", "duckduckgo_needs_proxy", True):
            # Measured: direct queries die on polluted DNS; without any proxy
            # this slot is 12 dead seconds of the budget, so trim it.
            return self._proxy_available()
        return True

    def _effective_chain(self) -> List[str]:
        return [name for name in self._ordered_chain() if self._available(name)]

    def _unconfigured_message(self, name: str) -> str:
        if name == "searxng":
            return "searxng 需要在插件设置里填写 searxng_base_url 才能使用"
        return f"{name} 当前不可用"

    async def _search_with_fallback(self, query: str, limit: int,
                                    backend: str) -> Dict[str, Any]:
        chain = self._effective_chain()
        # Only the per-call argument locks an engine. ``[search] backend`` is a
        # preference and is already folded into ``chain`` by _ordered_chain();
        # reading it again here would turn "prefer bing" into "bing or nothing".
        forced = (backend or "").strip().lower()
        if forced in {"auto", ""}:
            order = chain
            allow_fallback = True
        elif forced in self._KNOWN_BACKENDS:
            # An explicitly requested engine must not silently answer as another
            # one: reporting exa results while the user asked for searxng would
            # make every downstream citation a lie.
            if not self._available(forced):
                if forced == "duckduckgo":
                    raise SearchProviderError(
                        "duckduckgo 需要先检测到系统代理（或在设置里填 proxy_url）才会启用")
                raise SearchProviderError(self._unconfigured_message(forced))
            order = [forced]
            allow_fallback = False
        else:
            order = chain
            allow_fallback = True
        if not order:
            raise SearchProviderError("没有可用的搜索后端，请检查插件设置")

        total = self._num("total_timeout_seconds", 25, 5, 28)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total
        last_error: Optional[BaseException] = None
        attempted: List[str] = []
        best: Optional[Dict[str, Any]] = None

        for index, name in enumerate(order):
            remaining = deadline - loop.time()
            if remaining <= 1.0:
                break
            attempted.append(name)
            share = remaining if index == 0 else max(1.5, remaining / max(1, len(order) - index))
            try:
                outcome = await self._search_once(name, query, limit, min(share, remaining))
            except (BlockedError, BusyError, CooldownError, SearchProviderError,
                    _net.NetworkError, TimeoutError, asyncio.TimeoutError) as error:
                last_error = error
                self.logger.info("backend {} failed: {}: {}", name, type(error).__name__, error)
                if not allow_fallback:
                    break
                continue
            except Exception as error:  # unexpected: keep it local, try the next one
                last_error = error
                self.logger.exception("backend {} raised unexpectedly", name)
                if not allow_fallback:
                    break
                continue
            if outcome.get("results"):
                outcome["attempted"] = attempted
                return outcome
            best = best or outcome

        if best is not None and best.get("results"):
            best["attempted"] = attempted
            return best
        if last_error is not None:
            raise last_error
        return {"results": [], "backend": attempted[0] if attempted else "", "attempted": attempted}

    # ------------------------------------------------------------------
    # entries: conversation-facing
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(query: str, results: List[Dict[str, str]], backend: str) -> str:
        lines = [f'搜索: "{query}" (共 {len(results)} 条结果，来源 {backend})\n']
        for index, item in enumerate(results, 1):
            lines.append(f"{index}. {item.get('title', '')}")
            if item.get("snippet"):
                lines.append(f"   {item['snippet']}")
            lines.append(f"   {item.get('url', '')}")
            lines.append("")
        return "\n".join(lines)

    @plugin_entry(
        id="search",
        name="更好的网络搜索",
        description="免 API Key 的联网搜索，开箱即用。重要：query 保留用户原始语言（中文问题就用中文搜），"
                    "不要翻译成英文。需要看具体内容时，用返回结果里的 url 再调用 fetch 读取正文。",
        llm_result_fields=["summary"],
        timeout=30.0,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "搜索关键词（保留用户原始语言，不要翻译）"},
                "max_results": {"type": "integer",
                               "description": "返回条数；不填则用插件设置里的 max_results（默认 6），最多 15"},
                "backend": {"type": "string",
                            "enum": ["auto", "exa", "anysearch", "bing", "sogou", "baidu",
                                     "duckduckgo", "searxng"],
                            "description": "指定搜索后端；auto 按免 key 链路自动回退",
                            "default": "auto"},
            },
            "required": ["query"],
        },
    )
    async def search(self, query: str, max_results: Optional[int] = None,
                     backend: str = "auto", **_):
        text = _parsing.collapse(query)
        if len(text) < 2:
            return Err(SdkError("搜索关键词太短"))
        # The host passes through whatever the model supplied, so an omitted
        # optional argument arrives as None and the user's configured default
        # applies; the schema deliberately carries no literal default for that
        # reason.
        wanted = max_results if max_results is not None else self._int("max_results", 6, 1, 15)
        # The host's entry wrapper maps a bad-argument ValueError to an entry
        # error, same contract as the accepted v0.1 code.
        limit = max(1, min(int(wanted or 6), 15))  # pi-lens-ignore: unchecked-throwing-call-python
        self.logger.info("search: query_len={} limit={} backend={}", len(text), limit,
                         backend or "auto")
        started = time.monotonic()
        result, attempted = await self._run_search(text, limit, str(backend or "auto"))
        self._record_search(result, attempted, text=text, limit=limit, started=started)
        return result

    async def _run_search(self, text: str, limit: int, backend: str):
        """One search attempt, returning ``(result, backends actually tried)``.

        ``attempted`` comes back beside the result instead of inside it: the
        empty-answers case fails the same way it succeeded as far as the payload
        is concerned, and that is the case where "which engines did we walk
        through" is the only useful answer.
        """
        try:
            outcome = await self._search_with_fallback(text, limit, backend)
        except (BlockedError, BusyError, CooldownError) as error:  # pi-lens-ignore: no-boolean-in-except
            return Err(SdkError(str(error), code=_error_code(error) or _ERROR_CODES["blocked"])), []
        except (SearchProviderError, TimeoutError, asyncio.TimeoutError) as error:
            code = _error_code(error)
            if code == _ERROR_CODES["key_invalid"]:
                # Distinguishable, actionable copy (plan §1.3): the panel is
                # where the key is fixed, and the text never contains the key.
                return Err(SdkError(_MSG_KEY_INVALID, code=code)), []
            if code == _ERROR_CODES["quota"]:
                return Err(SdkError(_MSG_QUOTA, code=code)), []
            return Err(SdkError(f"搜索失败: {type(error).__name__}", code=code)), []
        except Exception as error:
            # Exception text can carry the full request URL (and thus the query),
            # so only the type name goes back to the conversation.
            self.logger.exception("search failed")
            return Err(SdkError(f"搜索失败: {type(error).__name__}")), []

        attempted = [str(name) for name in outcome.get("attempted") or []]
        results = [{
            "title": _parsing.sanitize_text(item.get("title"), _parsing.MAX_TITLE_LEN),
            "url": str(item.get("url") or ""),
            "snippet": _parsing.sanitize_text(item.get("snippet"), _parsing.MAX_SNIPPET_LEN),
        } for item in outcome.get("results", []) if str(item.get("url") or "").startswith("http")]
        if not results:
            return Err(SdkError("没有搜索到结果，可稍后重试或在插件设置里换后端",
                                code="BETTER_WEB_SEARCH_EMPTY")), attempted
        # Which backend actually answered is otherwise invisible: the host does not
        # log tool payloads, so "did my key serve this search?" could not be asked
        # after the fact.
        self.logger.info("search answered: backend={} count={} attempted={}",
                         outcome.get("backend") or "", len(results), attempted)
        return Ok({
            "query": text,
            "count": len(results),
            "backend": outcome.get("backend", ""),
            "attempted": attempted,
            "summary": self._build_summary(text, results, outcome.get("backend", "")),
            "results": results,
        }), attempted

    def _record_search(self, result, attempted: List[str], *, text: str, limit: int,
                       started: float) -> None:
        """Remember the latest conversation-facing search so the panel can show it.

        Everything the user could want to know after a search -- which backend
        answered, whether the chain had to fall back, how long it took -- existed
        only in our own log file, because the host does not log plugin tool
        payloads. The query itself is stored as its length: this dict travels into
        the panel context, and status payloads reach host logs verbatim.
        """
        payload = result.value if result.is_ok() else {}
        error = None if result.is_ok() else result.error
        self._last_search = {
            "ok": bool(payload.get("results")),
            "backend": str(payload.get("backend") or ""),
            "count": int(payload.get("count") or 0),
            "requested": int(limit),
            "attempted": [str(name) for name in attempted][:8],
            "query_len": len(text),
            "ms": int((time.monotonic() - started) * 1000),
            "at": time.strftime("%H:%M:%S"),
            "message": str(error) if error is not None else "",
            "code": str(getattr(error, "code", "") or "") if error is not None else "",
        }

    @plugin_entry(
        id="fetch",
        name="读取网页正文",
        description="读取一个网页并返回纯文本正文，免 API Key。适合在 search 之后打开最相关的 1-2 条结果再回答。"
                    "输入必须是完整 http(s) 链接。",
        llm_result_fields=["summary"],
        timeout=30.0,
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要读取的完整网页链接"},
                "max_chars": {"type": "integer",
                              "description": "最多返回多少字符，默认 4000，上限 20000",
                              "default": 4000},
                "mode": {"type": "string", "enum": ["auto", "direct", "reader"],
                         "description": "auto 先直连抓本地正文，失败再走远端阅读器",
                         "default": "auto"},
            },
            "required": ["url"],
        },
    )
    async def fetch(self, url: str, max_chars: int = 4000, mode: str = "auto", **_):
        try:
            target = _guard.normalize_http_url(url, allow_ranges=self._ssrf_ranges())
        except _guard.UnsafeUrlError as error:
            return Err(SdkError(f"链接不可访问: {error}", code="BETTER_WEB_SEARCH_UNSAFE_URL"))

        budget = max_chars if max_chars > 0 else self._int("max_content_chars", 4000, 200, 20000)
        budget = max(200, min(int(budget), 20000))  # pi-lens-ignore: unchecked-throwing-call-python
        requested = (mode or "auto").strip().lower()
        if requested not in {"auto", "direct", "reader"}:
            requested = "auto"
        routes: List[tuple[str, Callable[[float, str, str], dict]]] = []
        policy, proxy_url = self._route_policy("fetch")

        if requested in {"auto", "direct"}:
            routes.append(("direct", lambda timeout, pol, pxy: _providers.fetch_direct(
                target, timeout=timeout, policy=pol, proxy_url=pxy, max_chars=budget)))
        if requested in {"auto", "reader"}:
            # Same pool as search: reading a page must not keep hammering the key
            # that just answered 402 a second ago. Only the ring start moves here
            # though -- `_search_once` is the sole writer on this path, so a reload
            # after fetch-only can step back one key.
            routes.append(("exa", lambda timeout, pol, pxy: self._call_with_exa_keys(
                lambda key: _providers.fetch_exa(
                    target, timeout=timeout, policy=pol, proxy_url=pxy,
                    max_chars=budget, api_key=key))))

        total = self._num("fetch_total_timeout_seconds", 24, 5, 28)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total
        last_error: Optional[BaseException] = None
        attempted: List[str] = []

        for name, call in routes:
            remaining = deadline - loop.time()
            if remaining <= 1.0:
                break
            attempted.append(name)
            timeout = min(self._num("timeout_seconds", 12, 2, 30), remaining)
            try:
                async with asyncio.timeout(max(1.0, remaining)):
                    page = await _in_thread(call, timeout, policy, proxy_url)
            except Exception as error:
                last_error = error
                self.logger.info("fetch via {} failed: {}: {}", name, type(error).__name__, error)
                continue
            content = str(page.get("content") or "")
            if not content.strip():
                last_error = SearchProviderError(f"{name} 返回了空正文")
                continue
            return Ok({
                "url": target,
                "final_url": page.get("final_url") or target,
                "title": page.get("title") or "",
                "mode": page.get("mode") or name,
                "chars": len(content),
                "content": content,
                "summary": f"《{page.get('title') or target}》\n{content}",
            })

        code = _error_code(last_error) if last_error else ""
        if code == _ERROR_CODES["key_invalid"]:
            return Err(SdkError(_MSG_KEY_INVALID, code=code))
        if code == _ERROR_CODES["quota"]:
            return Err(SdkError(_MSG_QUOTA, code=code))
        message = str(last_error) if last_error else "无法读取该网页"
        return Err(SdkError(message[:200], code=code or "BETTER_WEB_SEARCH_FETCH_FAILED"))

    # ------------------------------------------------------------------
    # entries: panel (W4's ui/panel.tsx calls these action ids verbatim)
    # ------------------------------------------------------------------

    @staticmethod
    def _mask_key(key: str, prefix: str = "exa") -> str:
        """``前缀****尾4位``; short keys keep nothing, empty keys mask to empty."""
        text = str(key or "").strip()
        if not text:
            return ""
        if len(text) <= 4:
            return f"{prefix}****"
        return f"{prefix}****{text[-4:]}"

    def _exa_key_cards(self) -> List[Dict[str, Any]]:
        """One masked row per configured key: its last verdict, and who is next.

        ``state`` is ``unknown`` (not tried since the plugin started), ``ok``,
        ``exhausted`` or ``invalid``; ``current`` marks the key the next search
        starts from -- the one position that survives a reload.
        """
        pool = self._exa_keys()
        current = self._key_start(pool)
        # A key dropped by hand-editing exa_api_keys never passes through the
        # panel's remove button, so this is where its verdict dies.
        live = {self._key_fingerprint(key) for key in pool}
        for finger in [held for held in self._key_health if held not in live]:
            self._key_health.pop(finger, None)
        cards: List[Dict[str, Any]] = []
        for index, key in enumerate(pool):
            finger = self._key_fingerprint(key)
            cards.append({
                "fingerprint": finger,
                "masked": self._mask_key(key),
                "state": self._key_health.get(finger, "unknown"),
                "current": index == current,
            })
        return cards

    def _exa_pool_state(self, cards: List[Dict[str, Any]]) -> str:
        """The one badge the onboarding card shows for the whole pool."""
        if not cards:
            return "none"
        states = {str(card.get("state")) for card in cards}
        if states == {"invalid"}:
            return "invalid"
        if states <= {"exhausted", "invalid"}:
            return "exhausted"
        if "ok" in states:
            return "valid"
        return "unknown"

    def _build_panel_context(self, host_search: Dict[str, Any]) -> Dict[str, Any]:
        """Pure context builder (structure frozen by plan §3, keys masked)."""
        chain = self._ordered_chain()
        cards = self._exa_key_cards()
        any_key = self._anysearch_key()
        return {
            "exa_keys": cards,
            "exa_key_masked": str(cards[0]["masked"]) if cards else "",
            "exa_key_source": "config" if cards else "none",
            "exa_key_state": self._exa_pool_state(cards),
            "exa_last_error": self._exa_last_error,
            # One key, no ring: AnySearch takes a single credential.
            "anysearch_key_masked": self._mask_key(any_key, "any"),
            "anysearch_key_state": self._any_key_state if any_key else "none",
            "chain": chain,
            "effective_chain": self._effective_chain(),
            "proxy_mode": self._text("proxy", "auto"),
            "proxy_detected": self._proxy_available(),
            "host_search": {
                "exists": bool(host_search.get("exists")),
                "running": bool(host_search.get("running")),
            },
            "ssrf_fake_ip": bool(self._ssrf_ranges()),
            "key_fallback": self._flag_in("search", "exa_key_fallback_anonymous", False),
            "quota_note": _QUOTA_NOTE,
            "last_search": dict(getattr(self, "_last_search", None) or {}),
        }

    def _host_control(self, timeout: float = 4.0) -> _host.HostPluginControl:
        return _host.HostPluginControl(timeout=timeout)

    def _host_search_state_sync(self, timeout: float = 4.0) -> Dict[str, Any]:
        """Synchronous status read; every failure becomes a printable state dict."""
        try:
            control = self._host_control(timeout)
            return control.status(_host.BUILTIN_SEARCH_PLUGIN_ID).as_context_dict()
        except Exception:
            return {"exists": False, "running": False, "error": _MSG_NO_HOST}

    def _cached_host_state(self) -> Optional[Dict[str, Any]]:
        """The last good host-status read, while it is still fresh."""
        cached = getattr(self, "_host_state_cache", None)
        if not cached:
            return None
        stamp, state = cached
        if time.monotonic() - stamp > HOST_STATE_TTL_SECONDS:
            return None
        return state

    def _remember_host_state(self, state: Dict[str, Any]) -> None:
        """Keep only trustworthy reads; a failed one must not age into "truth"."""
        if isinstance(state, dict) and not state.get("error"):
            self._host_state_cache = (time.monotonic(), dict(state))

    @ui.context(id="main", title="联网搜索")
    @ui.action(label="面板数据", icon="📊", group="state", order=0, refresh_context=False)
    @plugin_entry(
        id="panel_context",
        name="面板数据",
        description="供『联网搜索』面板读取的当前状态；密钥一律以掩码形式出现。",
        timeout=10.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def panel_context(self, **_):
        # Cached first: the panel refreshes its context after *every* action, and a
        # live loopback read used to stall each one. A fresh answer is still fetched
        # whenever the user presses 「重新检查」 (get_host_search).
        cached = self._cached_host_state()
        if cached is not None:
            return self._build_panel_context(cached)
        # The host gives context providers a ~5s budget (core/host.py:147-181),
        # so this loopback status read is capped at 2.5s.
        host_search = await asyncio.to_thread(self._host_search_state_sync, 2.5)
        self._remember_host_state(host_search)
        # Serve the last trustworthy read when this one failed, rather than the
        # bare error blob: the panel shows a stale-but-real state plus its own hint.
        stored = getattr(self, "_host_state_cache", None)
        return self._build_panel_context(stored[1] if stored else host_search)

    async def _verify_exa_key(self, key: str) -> tuple[bool, int, int, str, str]:
        """One real search with ``key``. Returns (ok, ms, count, 中文文案, kind).

        ``kind`` is "" | "key" | "quota" | "network"; it drives panel state.
        The key never appears in any returned text.
        """
        policy, proxy_url = self._route_policy("exa")
        tool = self._exa_tool()
        timeout = self._num("timeout_seconds", 12, 2, 30)
        started = time.perf_counter()
        try:
            results = await _in_thread(
                _providers.search_exa, "N.E.K.O 插件", 3,
                timeout=timeout, policy=policy, proxy_url=proxy_url,
                api_key=key, tool=tool)
            ms = int((time.perf_counter() - started) * 1000)
            count = len([item for item in results if isinstance(item, dict)])
            if count:
                return True, ms, count, f"密钥可用：{ms} ms 返回 {count} 条结果", ""
            return False, ms, 0, "密钥未被拒绝，但这次没搜到结果：可再试一次", "network"
        except ApiKeyRejectedError:
            ms = int((time.perf_counter() - started) * 1000)
            return False, ms, 0, "Exa 判定这个密钥无效：请回面板重新粘贴（不填密钥也能匿名搜索）", "key"
        except QuotaExhaustedError:
            ms = int((time.perf_counter() - started) * 1000)
            return False, ms, 0, "密钥有效但额度已用尽：每月自动刷新，先用匿名档也能搜", "quota"
        except Exception as error:
            ms = int((time.perf_counter() - started) * 1000)
            return (False, ms, 0,
                    f"测试未完成（{type(error).__name__}）：密钥已保存，网络恢复后可再点测试",
                    "network")

    def _apply_exa_verify_state(self, kind: str) -> None:
        if kind == "":
            self._key_state = "valid"
            self._quota_state = ""
            self._exa_last_error = ""
        elif kind == "key":
            self._key_state = "invalid"
            self._exa_last_error = _MSG_KEY_INVALID
        elif kind == "quota":
            # Key itself is fine; only the balance is spent.
            self._key_state = "valid"
            self._quota_state = "exhausted"
            self._exa_last_error = _MSG_QUOTA
        # "network": leave the previous state, the key was not adjudicated.

    async def _set_exa_pool(self, keys: List[str]) -> bool:
        return await self._persist({"search": {"exa_api_keys": list(keys)}})

    def _apply_key_verdict(self, key: str, kind: str) -> None:
        """Record one verify result as this key's verdict.

        ``network`` deliberately records nothing: an unreachable Exa says nothing
        about the key, and a wrong verdict would take a good account out of the
        ring.
        """
        self._apply_exa_verify_state(kind)
        verdict = {"": "ok", "key": "invalid", "quota": "exhausted"}.get(kind)
        if not verdict:
            return
        finger = self._key_fingerprint(key)
        self._key_health[finger] = verdict
        if verdict == "ok":
            # A key that just answered is the best "start here" evidence there
            # is -- and after a replace, the ring must not sit on the old one.
            self._key_at = finger

    def _anysearch_key(self) -> str:
        return self._text("anysearch_api_key")

    async def _set_anysearch_key(self, key: str) -> bool:
        return await self._persist({"search": {"anysearch_api_key": key}})

    async def _verify_anysearch_key(self, key: str) -> tuple[bool, int, int, str, str]:
        """One real search with ``key``. Returns (ok, ms, count, 中文文案, kind).

        Same shape as _verify_exa_key: ``kind`` is "" | "key" | "limited" |
        "network", and only "key" is a statement about the credential. The key
        never appears in any returned text.
        """
        policy, proxy_url = self._route_policy("anysearch")
        timeout = self._num("timeout_seconds", 12, 2, 30)
        started = time.perf_counter()

        def spent_ms() -> int:
            return int((time.perf_counter() - started) * 1000)

        try:
            results = await _in_thread(
                _providers.search_anysearch, "N.E.K.O 插件", 3,
                timeout=timeout, policy=policy, proxy_url=proxy_url,
                api_key=key, zone=self._anysearch_zone())
        except ApiKeyRejectedError:
            return False, spent_ms(), 0, "AnySearch 判定这个密钥无效：请重新粘贴（不填也能搜）", "key"
        except BlockedError:
            return False, spent_ms(), 0, "密钥没被判无效，但这次被限流：稍等几秒再测一次", "limited"
        except Exception as error:
            return (False, spent_ms(), 0,
                    f"测试未完成（{type(error).__name__}）：密钥已保存，网络恢复后可再点测试",
                    "network")
        count = len([item for item in results if isinstance(item, dict)])
        if not count:
            return False, spent_ms(), 0, "密钥未被拒绝，但这次没搜到结果：可再试一次", "network"
        return True, spent_ms(), count, f"密钥可用：{spent_ms()} ms 返回 {count} 条结果", ""

    def _apply_any_verify_state(self, kind: str) -> None:
        """Only what AnySearch itself decides may move this badge.

        A busy window or a dead socket says nothing about the credential; a wrong
        "没验通过" would have the user re-pasting a key that works.
        """
        if kind == "":
            self._any_key_state = "valid"
        elif kind == "key":
            self._any_key_state = "invalid"

    @ui.action(label="添加密钥", icon="🔑", tone="success", group="exa", order=10)
    @plugin_entry(
        id="save_exa_key",
        name="添加 Exa 密钥",
        description="仅供面板调用：把一把 Exa API Key 加进密钥池，并立即用一次真实搜索验证。"
                    "验证失败也会保存，但那把钥匙会被暂时跳过。",
        timeout=28.0,
        input_schema={
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "从 https://dashboard.exa.ai/api-keys 复制的密钥"},
            },
            "required": ["api_key"],
        },
    )
    async def save_exa_key(self, api_key: str = "", **_):
        text = str(api_key or "").strip()
        if not text:
            return Err(SdkError("请先在 Exa 控制台复制密钥，再粘贴到这里"))
        pool = self._exa_keys()
        already = any(self._key_fingerprint(item) == self._key_fingerprint(text) for item in pool)
        # Re-pasting the key that is already in the pool means "check this one
        # again", not "give me two copies of it".
        saved = True if already else await self._set_exa_pool(pool + [text])
        # Validate with the *candidate* key directly, before any fallback logic
        # could mask a broken key with an anonymous success.
        ok, ms, count, message, kind = await self._verify_exa_key(text)
        if saved:
            self._apply_key_verdict(text, kind)
            await self._save_key_ring()
        else:
            message = "密钥没能保存：宿主没有确认这次配置写入，请再点一次添加（不填密钥也能搜索）"
        return Ok({
            "ok": bool(ok and saved),
            "masked": self._mask_key(text),
            "key_count": len(self._exa_keys()),
            "message": message,
            "latency_ms": ms,
            "count": count,
            "key_state": "invalid" if (saved and kind == "key") else self._key_state,
        })

    @ui.action(label="移除这把", icon="🗑", tone="danger", group="exa", order=20)
    @plugin_entry(
        id="remove_exa_key",
        name="移除一把 Exa 密钥",
        description="仅供面板调用：从密钥池里去掉一把钥匙（按面板上的指纹），不碰 Exa 账号本身。",
        timeout=15.0,
        input_schema={
            "type": "object",
            "properties": {
                "fingerprint": {"type": "string", "description": "面板上那把密钥的 8 位指纹"},
            },
            "required": ["fingerprint"],
        },
    )
    async def remove_exa_key(self, fingerprint: str = "", **_):
        target = str(fingerprint or "").strip().casefold()
        pool = self._exa_keys()
        kept = [key for key in pool if self._key_fingerprint(key) != target]
        if len(kept) == len(pool):
            return Err(SdkError("池里没有这把密钥：面板可能过期了，先刷新一次"))
        for key in pool:
            if key not in kept:
                self._key_health.pop(self._key_fingerprint(key), None)
        if not await self._set_exa_pool(kept):
            return Err(SdkError("移除失败：宿主没有确认这次配置写入，请重试"))
        if self._key_at and self._key_at not in {self._key_fingerprint(key) for key in kept}:
            self._key_at = ""          # the ring was sitting on the one just removed
        await self._save_key_ring()
        return Ok({"ok": True, "key_count": len(kept),
                   "message": f"已移除一把，池里还剩 {len(kept)} 把"})

    @ui.action(label="清空密钥池", icon="🧹", tone="danger", group="exa", order=30, confirm=True)
    @plugin_entry(
        id="clear_exa_key",
        name="清除全部 Exa 密钥",
        description="仅供面板调用：清空密钥池并回到匿名档。",
        timeout=15.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def clear_exa_key(self, **_):
        if not await self._set_exa_pool([]):
            return Err(SdkError("清除失败：配置写入未成功，请重试"))
        self._key_health.clear()
        self._key_at = ""
        self._key_state = "unknown"
        self._quota_state = ""
        self._exa_last_error = ""
        await self._save_key_ring()
        return Ok({"ok": True, "masked": "", "key_count": 0,
                   "message": "已清空密钥池，回到匿名档（也能搜索，只是更慢更低额）"})

    @ui.action(label="重测全部密钥", icon="🧪", group="exa", order=40)
    @plugin_entry(
        id="test_exa_key",
        name="重测 Exa 密钥池",
        description="仅供面板调用：拿每把已保存的密钥真实搜索一次，刷新它们各自的状态；不改配置。",
        timeout=28.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def test_exa_key(self, **_):
        pool = self._exa_keys()
        if not pool:
            return Ok({"ok": False, "key_count": 0, "keys": [],
                       "message": "还没有填写密钥；不填也能用，匿名档稍慢限额低",
                       "latency_ms": 0, "count": 0})
        rows: List[Dict[str, Any]] = []
        usable = 0
        last_count = 0
        started = time.perf_counter()
        for key in pool:
            # One search per key is real quota and the entry dies at 28s, so stop
            # early and say so rather than losing every remaining answer to the
            # host watchdog.
            if time.perf_counter() - started > 20.0:
                rows.append({"masked": self._mask_key(key), "ok": False, "kind": "skipped",
                             "message": "未测：本次时间不够，再点一次接着往后测"})
                continue
            ok, ms, count, message, kind = await self._verify_exa_key(key)
            self._apply_key_verdict(key, kind)
            usable += 1 if ok else 0
            last_count = count
            rows.append({"fingerprint": self._key_fingerprint(key),
                         "masked": self._mask_key(key), "ok": bool(ok),
                         "kind": kind, "message": message, "count": count})
        tested = len([row for row in rows if row.get("kind") != "skipped"])
        ms_sum = int((time.perf_counter() - started) * 1000)
        await self._save_key_ring()
        return Ok({
            "ok": usable > 0,
            "key_count": len(pool),
            "usable": usable,
            "keys": rows,
            "latency_ms": ms_sum,
            "count": last_count,
            "message": (f"{usable}/{tested} 把可用" if tested else "太网不好：一把都没测成")
                       + ("" if tested == len(pool) else f"（另有 {len(pool) - tested} 把未测）"),
        })

    @ui.action(label="保存 anysearch 密钥", icon="🔑", tone="success", group="any", order=10)
    @plugin_entry(
        id="set_anysearch_key",
        name="保存 anysearch 密钥",
        description="仅供面板调用：保存一把 AnySearch API Key 并立刻用一次真实搜索验证。"
                    "验证没通过也会保存，只是那把会被标成没验通过；不填密钥也能匿名搜。",
        timeout=20.0,
        input_schema={
            "type": "object",
            "properties": {
                "api_key": {"type": "string", "description": "从 AnySearch 控制台复制的密钥"},
            },
            "required": ["api_key"],
        },
    )
    async def set_anysearch_key(self, api_key: str = "", **_):
        text = str(api_key or "").strip()
        if not text:
            return Err(SdkError("请先在 AnySearch 控制台复制密钥，再粘贴到这里"))
        saved = await self._set_anysearch_key(text)
        ok, ms, count, message, kind = await self._verify_anysearch_key(text)
        if saved:
            self._apply_any_verify_state(kind)
        else:
            message = "密钥没能保存：宿主没有确认这次配置写入，请再点一次（不填密钥也能搜）"
        return Ok({
            "ok": bool(ok and saved),
            "masked": self._mask_key(text, "any"),
            "message": message,
            "latency_ms": ms,
            "count": count,
            "key_state": self._any_key_state if saved else "unknown",
        })

    @ui.action(label="测这把密钥", icon="🧪", group="any", order=20)
    @plugin_entry(
        id="test_anysearch_key",
        name="测试 anysearch 密钥",
        description="仅供面板调用：拿已保存的 AnySearch 密钥真实搜索一次，刷新它的状态；不改配置。",
        timeout=20.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def test_anysearch_key(self, **_):
        key = self._anysearch_key()
        if not key:
            return Ok({"ok": False, "masked": "", "key_state": "none",
                       "latency_ms": 0, "count": 0,
                       "message": "还没有填写 anysearch 密钥；不填也能搜，走的是匿名档"})
        ok, ms, count, message, kind = await self._verify_anysearch_key(key)
        self._apply_any_verify_state(kind)
        return Ok({"ok": bool(ok), "masked": self._mask_key(key, "any"),
                   "key_state": self._any_key_state,
                   "message": message, "latency_ms": ms, "count": count})

    @ui.action(label="移除这把", icon="🗑", tone="danger", group="any", order=30, confirm=True)
    @plugin_entry(
        id="clear_anysearch_key",
        name="移除 anysearch 密钥",
        description="仅供面板调用：清空配置里的 AnySearch 密钥并回到匿名档，不碰 AnySearch 账号本身。",
        timeout=15.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def clear_anysearch_key(self, **_):
        if not await self._set_anysearch_key(""):
            return Err(SdkError("移除失败：宿主没有确认这次配置写入，请重试"))
        self._any_key_state = "unknown"
        return Ok({"ok": True, "masked": "", "key_state": "none",
                   "message": "已移除 anysearch 密钥，回到匿名档（不填也能搜）"})

    @ui.action(label="查询内置搜索状态", icon="🩺", group="host", order=20, refresh_context=False)
    @plugin_entry(
        id="get_host_search",
        name="查询宿主内置搜索状态",
        description="仅供面板调用：读取宿主内置 web_search 插件是否存在/在跑。"
                    "本插件不代替用户开关它，停用请去宿主的插件中心。",
        timeout=10.0,
        input_schema={"type": "object", "properties": {}},
    )
    async def get_host_search(self, **_):
        state = await asyncio.to_thread(self._host_search_state_sync, 4.0)
        # The user asked for the truth; let the panel reuse it for a while instead
        # of paying another live read on the next refresh.
        self._remember_host_state(state)
        error = str(state.get("error") or "")
        running = bool(state.get("running"))
        return Ok({
            "ok": not bool(error),
            "exists": bool(state.get("exists")),
            "running": running,
            "message": error or ("内置『网络搜索』正在运行" if running
                                 else "内置『网络搜索』未在运行"),
        })

    @ui.action(label="开关代理软件兼容", icon="🛡", group="tools", order=20, refresh_context=True)
    @plugin_entry(
        id="set_ssrf_guard",
        name="切换代理软件兼容",
        description="仅供面板调用：打开/关闭 Clash·miaomiao 等代理软件 TUN 模式兼容。"
                    "打开后域名解析到 198.18.0.0/15 也允许抓取；内网地址、元数据地址照旧拒绝。",
        timeout=10.0,
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean",
                            "description": "true=允许 fake-ip 段；false=全部按内网拒绝"},
            },
            "required": ["enabled"],
        },
    )
    async def set_ssrf_guard(self, enabled: bool = True, **_):
        """Flip the fake-ip exception without asking the user to edit CIDRs.

        Plan §1.4 promised a one-click switch for a non-technical audience; a
        ``[net].ssrf_allow_ranges`` string list is not something they can type.
        """
        want = bool(enabled)
        ranges = list(DEFAULT_SSRF_ALLOW_RANGES) if want else []
        if not await self._persist({"net": {"ssrf_allow_ranges": ranges}}):
            return Err(SdkError("写入失败：配置未能保存，请重试"))
        return Ok({
            "ok": True,
            "enabled": want,
            "ranges": ranges,
            "message": ("已开启代理软件兼容：用 Clash 之类的 TUN 模式也能正常打开网页"
                        if want else "已关闭：只允许访问普通公网网址，更严但可能与 TUN 代理冲突"),
        })

    @ui.action(label="整池失败时降级无 Key", icon="🪂", group="exa", order=50)
    @plugin_entry(
        id="set_key_fallback",
        name="切换无 Key 降级",
        description="仅供面板调用：打开后，密钥池里每一把都报错时再试一次免密钥匿名档；"
                    "关闭（默认）则直接改用其它搜索来源，把时间留给它们。",
        timeout=10.0,
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean",
                            "description": "true=池子全败后再降级匿名档；false=直接换后端"},
            },
            "required": ["enabled"],
        },
    )
    async def set_key_fallback(self, enabled: bool = False, **_):
        """What to do when the whole ring failed is a judgement call: the user's.

        Default off, because if every account the user owns answered 402, the
        shared anonymous tier has usually lost the same traffic too -- and the
        seconds spent proving that are missing from the next backend's budget.
        """
        want = bool(enabled)
        if not await self._persist({"search": {"exa_key_fallback_anonymous": want}}):
            return Err(SdkError("写入失败：配置未能保存，请重试"))
        return Ok({
            "ok": True, "enabled": want,
            "message": ("已开启：密钥全部失效时再试一次免密钥匿名档"
                        if want else "已关闭（默认）：密钥全部失效时直接改用其它搜索来源"),
        })

    def _probe(self, name: str, query: str, limit: int, timeout: float, *,
               force_proxy: bool) -> Callable[[], Any]:
        if force_proxy:
            policy, proxy_url = _net.POLICY_SYSTEM, (self._text("proxy_url") or "system")
        else:
            policy, proxy_url = self._route_policy(name)
        call = self._fetcher(name, query, limit, timeout, policy=policy, proxy_url=proxy_url)
        return lambda: call()

    @ui.action(label="网络自检", icon="📡", group="tools", order=10, refresh_context=True)
    @plugin_entry(
        id="diagnose_network",
        name="网络自检",
        description="仅供面板调用、用户显式点击：逐个后端做真实搜索探测（消耗额度），"
                    "返回直连/代理可用性表格与建议链路。",
        timeout=28.0,
        input_schema={
            "type": "object",
            "properties": {
                "with_proxy": {"type": "boolean",
                               "description": "true=同时用代理再测一遍（需要已配置代理）",
                               "default": False},
            },
        },
    )
    async def diagnose_network(self, with_proxy: bool = False, **_):
        # Probe the configured chain plus duckduckgo (users ask "why not DDG?"),
        # minus searxng when it has no base URL. Closures own all I/O; W6's
        # run() stays pure and offline-testable.
        names = list(dict.fromkeys([*self._chain(), "duckduckgo"]))
        probe_names = [name for name in names if name != "searxng" or self._text("searxng_base_url")]
        timeout = min(self._num("timeout_seconds", 12, 2, 30), 6.0)
        query = self._text("diagnose_query", "N.E.K.O 猫娘 插件") or "N.E.K.O 猫娘 插件"
        probes = [(name, self._probe(name, query, 2, timeout, force_proxy=False))
                  for name in probe_names]
        proxied = None
        if with_proxy:
            proxied = {name: self._probe(name, query, 2, timeout, force_proxy=True)
                       for name in probe_names}
        try:
            # run() spawns its own worker pool: to_thread keeps it off our loop.
            # Dual-path mode doubles the probe count (up to 8 backends x 2), which
            # 4 workers cannot drain inside the 25 s envelope -- 7 s x 4 waves
            # already hits it -- so widen the pool to keep pace with the request.
            workers = _diagnose.MAX_WORKERS * (2 if proxied else 1)
            report = await asyncio.wait_for(
                asyncio.to_thread(
                    _diagnose.run,
                    probes,
                    allow_proxy=bool(with_proxy),
                    per_probe_timeout=7.0,
                    max_workers=workers,
                    proxy_detected=self._proxy_available(),
                    proxied_probes=proxied,
                ),
                timeout=25.0,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return Err(SdkError("网络自检超时：网络可能太慢，请减少后端数量后再试",
                                code="BETTER_WEB_SEARCH_BUSY"))
        except Exception as error:
            self.logger.info("diagnose failed: {}:{}", type(error).__name__, error)
            return Err(SdkError("网络自检失败，请稍后重试", code="BETTER_WEB_SEARCH_BLOCKED"))
        return Ok({
            "summary": str(report.get("summary") or ""),
            "rows": list(report.get("rows") or []),
            "recommended_chain": list(report.get("recommended_chain") or []),
        })
