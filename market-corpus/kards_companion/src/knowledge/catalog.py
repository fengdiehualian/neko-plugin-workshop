"""KARDS 卡牌目录同步（Python 3.11，仅标准库 urllib）。

数据源是社区维护的公开 GraphQL 端点（KARDS 官方无公开 API），响应形态已用
2026-10 实包核对：``cards(language:"zh", first, offset){ pageInfo{count
hasNextPage} edges{node{id cardId importId json imageUrl thumbUrl}} }``，
其中 ``json`` 是内嵌字符串：``title``/``text`` 为语言键字典（简体中文在
``zh-Hans``，兜底 ``en-EN``），费用字段叫 ``kredits``，阵营叫 ``faction``
（单个国名字符串），``imageUrl`` 是相对路径、须拼官网域名。解析层同时兼容
扁平 name/cost/text 的历史形态。直连失败时走 CORS 中转兜底。

卡库以**内置快照**（knowledge/cards_builtin.json，同步管线生成的 v2 全量
数据）为基础：插件一加载即可用，离线也能报效果文本；网络同步只是把它刷新
到最新。插件不内置也不分发任何卡图，卡图仅按需缓存到用户本地 cache 目录。

安全：用户可配置数据源，故所有出站请求统一过 SSRF 校验——仅 http/https、
解析后的 IP 不得为私网/环回/链路本地/保留地址、禁用重定向。
"""

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OFFICIAL_ENDPOINT = "https://herokuapi.kards.com/graphql"
IMAGE_HOST = "https://www.kards.com"
RELAY_PREFIX = "https://api.allorigins.win/raw?url="
PAGE_SIZE = 500
CACHE_VERSION = 2  # v2：语言键字典解析上线，v1 缓存没有效果文本，必须作废
_UA = "n.e.k.o_plugin_kards_companion/0.1 (personal-companion plugin)"

# image 是带参字段：别名成 imageUrl/thumbUrl 让解析层保持扁平契约
_GRAPHQL_QUERY = """
query getCards($offset: Int!) {
  cards(language: "zh", first: %d, offset: $offset,
        showSpawnables: true, showExiles: true, showReserved: false) {
    pageInfo { count hasNextPage }
    edges { node { id cardId importId json
      imageUrl: image(language: "zh")
      thumbUrl: image(type: thumb, language: "zh")
    } }
  }
}
""" % PAGE_SIZE


class CatalogError(RuntimeError):
    pass


# ---------------------------------------------------------------- SSRF 防线

def _ip_blocked(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # 解析不了的地址一律拒绝
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
            or (ip.version == 6 and ip.is_site_local))


def validate_public_url(url: str) -> str:
    """校验为可公开访问的 http(s) URL，解析主机并阻断私网/环回/元数据地址。"""
    clean = (url or "").strip()
    try:
        parts = urllib.parse.urlsplit(clean)
    except ValueError as exc:
        raise CatalogError("无效的卡库数据源 URL") from exc
    if parts.scheme not in ("https", "http"):
        raise CatalogError("卡库数据源必须是 http/https URL")
    host = parts.hostname
    if not host:
        raise CatalogError("卡库数据源缺少主机名")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise CatalogError(f"无法解析卡库数据源主机：{host}") from exc
    for info in infos:
        if _ip_blocked(str(info[4][0])):
            raise CatalogError("卡库数据源不允许指向内网/环回/链路本地地址")
    return urllib.parse.urlunsplit(parts)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """任何 3xx 都直接失败：防止校验后跳转到内网。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post_graphql(endpoint: str, variables: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps({"query": _GRAPHQL_QUERY, "variables": variables}).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "user-agent": _UA,
        "origin": "https://www.kards.com",
        "referer": "https://www.kards.com/",
    }

    def _request(url: str) -> dict[str, Any]:
        validate_public_url(url)
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with _OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    try:
        return _request(endpoint)
    except urllib.error.HTTPError:
        raise
    except Exception:
        relayed = RELAY_PREFIX + urllib.parse.quote(endpoint, safe="")
        return _request(relayed)


# ---------------------------------------------------------------- 数据模型

@dataclass
class Card:
    card_id: str
    import_id: str
    name: str
    name_en: str = ""
    cost: int | None = None
    attack: int | None = None
    defense: int | None = None
    countries: tuple[str, ...] = ()
    card_type: str = ""
    text: str = ""
    image_url: str = ""
    thumb_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id, "import_id": self.import_id, "name": self.name,
            "name_en": self.name_en, "cost": self.cost, "attack": self.attack,
            "defense": self.defense, "countries": list(self.countries),
            "card_type": self.card_type, "text": self.text,
            "image_url": self.image_url, "thumb_url": self.thumb_url,
        }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# 实测 zh 端点的 title/text 均为语言键字典；简体优先、英文兜底
_ZH_LANGS = ("zh-Hans", "zh-CN", "zh-Hant", "zh")
_EN_LANGS = ("en-EN", "en-US", "en")


def _lang_text(value: Any, langs: tuple[str, ...]) -> str:
    """从语言键字典取首个非空文本；纯字符串原样透传（兼容扁平形态）。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for lang in langs:
            got = value.get(lang)
            if isinstance(got, str) and got.strip():
                return got.strip()
    return ""


def _abs_image(path: Any) -> str:
    text = str(path or "").strip()
    if text.startswith("http"):
        return text
    if text.startswith("/"):
        return IMAGE_HOST + text
    return ""


def parse_node(node: dict[str, Any]) -> Card | None:
    """解析单个 GraphQL node；字段缺失容错，无 card id 返回 None。"""
    raw_json = node.get("json")
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError:
            raw_json = {}
    if not isinstance(raw_json, dict):
        raw_json = {}
    card_id = str(node.get("cardId") or raw_json.get("id") or node.get("id") or "").strip()
    if not card_id:
        return None
    title = raw_json.get("title")
    text_raw = raw_json.get("text")
    countries_raw = (raw_json.get("faction") or raw_json.get("country")
                     or raw_json.get("countries") or [])
    if isinstance(countries_raw, str):
        countries_raw = [countries_raw]
    countries = tuple(str(c).strip().upper() for c in countries_raw if c)
    cost = raw_json.get("kredits")
    if cost is None:
        cost = raw_json.get("cost")
    attack = raw_json.get("attack")
    if attack is None:
        attack = raw_json.get("power")
    defense = raw_json.get("defense")
    if defense is None:
        defense = raw_json.get("health")
    return Card(
        card_id=card_id,
        import_id=str(node.get("importId") or raw_json.get("import_id") or "").strip(),
        name=(_lang_text(title, _ZH_LANGS) or _lang_text(title, _EN_LANGS)
              or str(raw_json.get("name") or "").strip()),
        name_en=_lang_text(title, _EN_LANGS) or str(raw_json.get("nameEn") or "").strip(),
        cost=_as_int(cost),
        attack=_as_int(attack),
        defense=_as_int(defense),
        countries=countries,
        card_type=str(raw_json.get("type") or raw_json.get("cardType") or ""),
        text=_lang_text(text_raw, _ZH_LANGS) or _lang_text(text_raw, _EN_LANGS),
        image_url=_abs_image(node.get("imageUrl")),
        thumb_url=_abs_image(node.get("thumbUrl")),
    )


def parse_graphql_response(payload: dict[str, Any]) -> tuple[list[Card], bool]:
    """返回 (cards, has_next_page)。errors / 结构异常抛 CatalogError。"""
    if not isinstance(payload, dict) or payload.get("errors"):
        errors = (payload or {}).get("errors") or [{}]
        message = "; ".join(str(e.get("message", e)) for e in errors)
        raise CatalogError(f"卡库接口返回错误：{message}")
    cards_section = (payload.get("data") or {}).get("cards") or {}
    page_info = cards_section.get("pageInfo") or {}
    result: list[Card] = []
    for edge in cards_section.get("edges") or []:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        card = parse_node(node)
        if card is not None:
            result.append(card)
    return result, bool(page_info.get("hasNextPage"))


# ---------------------------------------------------------------- 目录

BUILTIN_FILE = Path(__file__).with_name("cards_builtin.json")


class CardCatalog:
    """卡牌目录：内置快照打底（离线即用），网络同步负责刷新。"""

    def __init__(self, cache_file: Path, *, endpoint: str = "", timeout: float = 20.0) -> None:
        self._cache_file = Path(cache_file)
        self._endpoint = validate_public_url(endpoint) if (endpoint or "").strip() else OFFICIAL_ENDPOINT
        self._timeout = float(timeout)
        self._cards: dict[str, Card] = {}
        self._by_import: dict[str, Card] = {}
        self._by_name: dict[str, Card] = {}
        self.fetched_at: float = 0.0
        self.last_error: str = ""
        self.builtin = False   # 当前数据来自内置快照（未成功联网刷新）
        if not self.load_cache():
            self.load_builtin()

    # ---------------------------------------------------------- persistence

    def load_builtin(self) -> bool:
        try:
            data = json.loads(BUILTIN_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if int(data.get("version") or 1) < CACHE_VERSION:
            return False
        self._clear()
        self._ingest(data)
        self.builtin = bool(self._cards)
        return self.builtin

    def _clear(self) -> None:
        self._cards.clear()
        self._by_import.clear()
        self._by_name.clear()

    def _ingest(self, data: dict[str, Any]) -> None:
        self.fetched_at = float(data.get("fetched_at") or 0.0)
        for item in data.get("cards") or []:
            if not isinstance(item, dict):
                continue
            try:
                card = Card(**item)
            except TypeError:
                continue
            self._index(card)

    def load_cache(self) -> bool:
        try:
            data = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if int(data.get("version") or 1) < CACHE_VERSION:
            return False  # 旧版解析没有效果文本，作废重拉
        self._ingest(data)
        return bool(self._cards)

    def save_cache(self) -> None:
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": CACHE_VERSION, "fetched_at": self.fetched_at,
                   "source": self._endpoint,
                   "cards": [c.to_dict() for c in self._cards.values()]}
        tmp = self._cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._cache_file)

    def _index(self, card: Card) -> None:
        self._cards[card.card_id] = card
        if card.import_id:
            self._by_import[card.import_id.lower()] = card
        if card.name:
            self._by_name[card.name.replace(" ", "").lower()] = card
        if card.name_en:
            self._by_name.setdefault(card.name_en.replace(" ", "").lower(), card)

    # ---------------------------------------------------------- sync

    def sync(self, *, force: bool = False, max_age_hours: float = 24.0) -> bool:
        """全量分页同步。返回是否实际更新了数据。"""
        if not force and self._cards and time.time() - self.fetched_at < max_age_hours * 3600:
            return False
        collected: list[Card] = []
        offset = 0
        try:
            while True:
                payload = _post_graphql(self._endpoint, {"offset": offset}, self._timeout)
                page, has_next = parse_graphql_response(payload)
                collected.extend(page)
                if not has_next or not page:
                    break
                offset += PAGE_SIZE
                time.sleep(0.4)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self.last_error = ""
        if not collected:
            self.last_error = "empty_response"
            return False
        self._clear()
        for card in collected:
            self._index(card)
        self.fetched_at = time.time()
        self.builtin = False
        try:
            self.save_cache()
        except OSError:
            pass
        return True

    # ---------------------------------------------------------- queries

    @property
    def ready(self) -> bool:
        return bool(self._cards)

    def size(self) -> int:
        return len(self._cards)

    def by_import_id(self, import_id: str) -> Card | None:
        return self._by_import.get((import_id or "").lower())

    def by_card_id(self, card_id: str) -> Card | None:
        return self._cards.get(card_id)

    def by_name(self, name: str) -> Card | None:
        return self._by_name.get((name or "").replace(" ", "").lower())

    def search(self, query: str, limit: int = 8) -> list[Card]:
        needle = (query or "").strip().lower()
        if not needle:
            return []
        hits: list[tuple[int, Card]] = []
        for card in self._cards.values():
            if needle in (card.name.replace(" ", "").lower(), card.name_en.lower()):
                hits.append((0, card))
            elif needle in card.name.lower() or needle in card.name_en.lower():
                hits.append((1, card))
            elif needle in card.text.lower():
                hits.append((2, card))
        hits.sort(key=lambda p: p[0])
        seen: set[str] = set()
        unique: list[Card] = []
        for _, card in hits:
            if card.card_id not in seen:
                seen.add(card.card_id)
                unique.append(card)
        return unique[:limit]

    def deck_pool(self, import_ids: tuple[str, ...]) -> list[Card]:
        return [c for c in (self.by_import_id(i) for i in import_ids) if c is not None]

    def nation_pool(self, nations: set[str], limit: int = 4000) -> list[Card]:
        wanted = {n.upper() for n in nations}
        out: list[Card] = []
        for card in self._cards.values():
            if wanted & set(card.countries):
                out.append(card)
                if len(out) >= limit:
                    break
        return out
