import json

import pytest
from kards_companion.knowledge.catalog import (
    CardCatalog,
    CatalogError,
    parse_graphql_response,
    parse_node,
    validate_public_url,
)


def node(card_id: str, import_id: str, name: str, **attrs) -> dict:
    payload = {"name": name, **attrs}
    return {"id": card_id, "cardId": card_id, "importId": import_id,
            "json": json.dumps(payload, ensure_ascii=False),
            "imageUrl": f"https://cdn.example/{card_id}.png", "thumbUrl": ""}


def zh_node(card_id: str, import_id: str, title_zh: str, title_en: str,
            text_zh: str = "", **attrs) -> dict:
    """按 2026-10 实测的 zh 端点形态造 node：语言键字典 + kredits + faction + 相对图片路径。"""
    payload = {
        "id": card_id, "import_id": import_id,
        "title": {"en-EN": title_en, "zh-Hans": title_zh},
        "text": {"en-EN": text_zh and "flash", "zh-Hans": text_zh},
        **attrs,
    }
    return {"id": "944" + card_id, "cardId": card_id, "importId": import_id,
            "json": payload,
            "imageUrl": f"/images/card/v53/zh-Hans/{card_id}.avif",
            "thumbUrl": f"/images/card/v53/zh-Hans/thumb/{card_id}.avif"}


def test_parse_node_real_zh_shape() -> None:
    card = parse_node(zh_node("13e_dragons", "rV", "13e龙骑队", "13E DRAGONS",
                              text_zh="若本回合部署过单位，无法被部署。",
                              kredits=2, attack=1, defense=2, faction="France",
                              type="infantry", attributes=["blitz"]))
    assert card is not None
    assert card.name == "13e龙骑队" and card.name_en == "13E DRAGONS"
    assert (card.cost, card.attack, card.defense) == (2, 1, 2)
    assert card.countries == ("FRANCE",) and card.card_type == "infantry"
    assert card.text == "若本回合部署过单位，无法被部署。"
    assert card.image_url == "https://www.kards.com/images/card/v53/zh-Hans/13e_dragons.avif"
    assert card.thumb_url.endswith("/thumb/13e_dragons.avif") and card.thumb_url.startswith("https://")


def test_parse_node_text_language_and_empty_fallback() -> None:
    only_en = zh_node("x1", "a1", "", "SPARK", kredits=3)
    only_en["json"]["title"] = {"en-EN": "SPARK"}
    only_en["json"]["text"] = {"en-EN": "Deal 2 damage."}
    card = parse_node(only_en)
    assert card is not None
    assert card.name == "SPARK" and card.text == "Deal 2 damage."
    empty = parse_node(zh_node("x2", "a2", "无效果", "NO TEXT", text_zh=""))
    assert empty is not None and empty.text == "" and empty.name == "无效果"


def test_parse_node_zh_json_string() -> None:
    card = parse_node(node("k1", "aa", "喀秋莎", cost=2, attack=1, defense=2,
                           country=["SOVIET"], type="UNIT", text="闪击"))
    assert card is not None
    assert (card.name, card.cost, card.attack, card.defense) == ("喀秋莎", 2, 1, 2)
    assert card.countries == ("SOVIET",) and card.text == "闪击"


def test_parse_node_tolerates_missing_and_dict_json() -> None:
    card = parse_node({"cardId": "k2", "importId": "", "json": {"nameEn": "Blitz"}})
    assert card is not None and card.name == "" and card.name_en == "Blitz"
    assert parse_node({"json": "{}"}) is None
    assert parse_node({"cardId": "k3", "json": "<not json>"}) is not None


def test_parse_response_and_pagination() -> None:
    payload = {"data": {"cards": {
        "pageInfo": {"count": 2, "hasNextPage": True},
        "edges": [{"node": node("a", "aa", "A")}, {"node": node("b", "bb", "B", bogus=1)}],
    }}}
    cards, has_next = parse_graphql_response(payload)
    assert [c.card_id for c in cards] == ["a", "b"] and has_next
    with pytest.raises(CatalogError):
        parse_graphql_response({"errors": [{"message": "boom"}]})


def test_catalog_index_and_search(tmp_path) -> None:
    catalog = CardCatalog(tmp_path / "cards.json")
    catalog._clear()  # 隔离内置快照，本用例只验证索引/查询逻辑
    for card in (parse_node(node("a", "aa", "虎式", cost=6, attack=8, defense=8,
                                 country=["GERMANY"], type="UNIT", text="重坦克")),
                 parse_node(node("b", "bb", "闪电战", cost=3, country=["GERMANY"],
                                 type="COMMAND", text="所有单位可攻击"))):
        catalog._index(card)
    assert catalog.by_import_id("AA").card_id == "a"
    assert catalog.by_name(" 虎 式 ").card_id == "a"
    assert [c.card_id for c in catalog.search("闪电")] == ["b"]
    assert {c.card_id for c in catalog.nation_pool({"germany"})} == {"a", "b"}
    catalog.save_cache()
    reloaded = CardCatalog(tmp_path / "cards.json")
    assert reloaded.ready and reloaded.by_card_id("a").name == "虎式"


def test_builtin_snapshot_is_offline_ready(tmp_path, monkeypatch) -> None:
    def reject_network(*args, **kwargs):
        pytest.fail("加载内置卡牌库不应访问网络")

    monkeypatch.setattr("kards_companion.knowledge.catalog._post_graphql", reject_network)
    monkeypatch.setattr("kards_companion.knowledge.catalog.socket.getaddrinfo", reject_network)
    catalog = CardCatalog(tmp_path / "missing.json")
    assert catalog.ready and catalog.builtin and catalog.size() >= 1000
    hit = catalog.by_name("闪电战")
    assert hit is not None and "攻击力" in hit.text


def test_cache_version_invalidates_stale_rows(tmp_path) -> None:
    cache = tmp_path / "cards.json"
    cache.write_text(json.dumps({"fetched_at": 4000000000.0, "cards": [
        {"card_id": "x", "import_id": "aa", "name": "旧卡", "text": ""}]}), encoding="utf-8")
    stale = CardCatalog(cache)
    # 旧 v1 行被拒收，但内置快照兜底 → 数据来源必须翻到 builtin
    assert stale.by_card_id("x") is None and stale.builtin
    # 当前版本写入的缓存要能原样重载（此时 builtin 标记应清掉）
    stale._clear()
    stale._index(parse_node(node("y", "bb", "新卡", cost=1, text="生效")))
    stale.builtin = False
    stale.fetched_at = 4000000000.0
    stale.save_cache()
    reloaded = CardCatalog(cache)
    assert reloaded.by_card_id("y").text == "生效" and not reloaded.builtin


@pytest.mark.parametrize("url", [
    "ftp://example.com/x",
    "http://localhost:8080/graphql",
    "http://127.0.0.1/gql",
    "http://192.168.1.10/gql",
    "http://10.0.0.5/gql",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/gql",
])
def test_validate_rejects_unsafe(url: str) -> None:
    with pytest.raises(CatalogError):
        validate_public_url(url)


def test_catalog_rejects_bad_endpoint(tmp_path) -> None:
    with pytest.raises(CatalogError):
        CardCatalog(tmp_path / "c.json", endpoint="http://localhost/gql")
