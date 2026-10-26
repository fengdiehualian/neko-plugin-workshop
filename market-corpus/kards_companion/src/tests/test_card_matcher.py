from kards_companion.perception.card_matcher import CardMatcher
from PIL import Image, ImageDraw


def art(seed_colors: tuple[tuple[int, int, int], tuple[int, int, int]]) -> Image.Image:
    img = Image.new("RGB", (128, 128))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 128, 64), fill=seed_colors[0])
    draw.rectangle((0, 64, 128, 128), fill=seed_colors[1])
    return img


def test_exact_art_matches_highest() -> None:
    pool = art(((200, 30, 30), (30, 30, 200)))
    other = art(((20, 200, 20), (200, 200, 10)))
    matcher = CardMatcher()
    matcher.build([("tank_a", "虎式", pool), ("truck_b", "补给", other)])
    card_id, score = matcher.match(pool)
    assert card_id == "tank_a" and score > 0.9
    scaled = pool.resize((48, 60))
    assert matcher.match(scaled)[0] == "tank_a"


def test_below_threshold_returns_none() -> None:
    matcher = CardMatcher(min_score=0.95)
    matcher.build([("x", "X", art(((0, 0, 0), (255, 255, 255))))])
    card_id, score = matcher.match(art(((255, 0, 255), (0, 255, 0))))
    assert card_id is None and 0.0 <= score < 0.95


def test_empty_pool_and_name_confirm() -> None:
    matcher = CardMatcher()
    assert matcher.match(art(((1, 2, 3), (4, 5, 6)))) == (None, 0.0)
    assert CardMatcher.confirm_name("喀 秋 莎", "喀秋莎") > 0.9
    assert CardMatcher.confirm_name("M3A3", "Katyusha") < 0.3
    assert CardMatcher.confirm_name("", "X") == 0.0
