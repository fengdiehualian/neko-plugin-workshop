import pytest
from plugin.plugins.from_the_heart.dialogue import _parse_candidate


def test_parse_candidate_accepts_fenced_json_and_strips_values():
    candidate = _parse_candidate(
        "```json\n"
        '{"intent_key":" wrong_food ","requested_extra_dish":"none",'
        '"lobster_stance":"neutral","social_key":"tease",'
        '"reply_text":"  不喜欢这个。 "}\n```'
    )
    assert candidate is not None
    assert candidate.intent_key == "wrong_food"
    assert candidate.reply_text == "不喜欢这个。"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        '{"intent_key": "wrong_food", "reply_text": 1}',
        "[1, 2, 3]",
    ],
)
def test_parse_candidate_returns_none_for_malformed_or_incomplete_model_output(raw):
    assert _parse_candidate(raw) is None

