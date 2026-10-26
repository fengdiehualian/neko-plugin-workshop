import pytest
from kards_companion.knowledge.deck_code import DeckCodeError, encode_deck_code, parse_deck_code


def test_parse_expands_copies_by_group() -> None:
    deck = parse_deck_code("%%45|aabb;ccdd;ee")
    assert deck.main_nation == "Soviet"
    assert deck.ally_nation == "USA"
    assert deck.import_ids == ("aa", "bb", "cc", "cc", "dd", "dd", "ee", "ee", "ee")
    assert deck.counts()["dd"] == 2


def test_parse_tolerates_prefix_and_case() -> None:
    assert parse_deck_code("42|ABab").normalized_code == "%%42|ABab"
    assert parse_deck_code("%%31|aa".upper()).ally_nation == "Germany"


def test_rejects_bad_shapes() -> None:
    with pytest.raises(DeckCodeError):
        parse_deck_code("not a code")
    with pytest.raises(DeckCodeError):
        parse_deck_code("%%99|aa")          # 主国只能 1-5
    with pytest.raises(DeckCodeError):
        parse_deck_code("%%44|aa")          # 主副国相同
    with pytest.raises(DeckCodeError):
        parse_deck_code("%%45|aa;;;" + "bb" * 5)  # 单卡超 4 张（第3组 bb×3 → 6 张）


def test_encode_roundtrip() -> None:
    counts = {"aa": 1, "bb": 2, "cc": 4}
    code = encode_deck_code("4", "5", counts)
    deck = parse_deck_code(code)
    assert deck.counts() == counts
    assert set(deck.nations) == {"Soviet", "USA"}
