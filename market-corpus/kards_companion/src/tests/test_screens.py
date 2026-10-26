"""无 OCR 场景分类器：UI 检测结果 → (scene, my_turn)。"""

from kards_companion.perception.detector import Detection
from kards_companion.perception.screens import ScreenClassifier
from kards_companion.state.model import (
    SCENE_DEFEAT,
    SCENE_MATCH,
    SCENE_UNKNOWN,
    SCENE_VICTORY,
)

NAMES = (
    "unit_board", "card_hand", "card_back", "played_card", "hq",
    "frontline", "end_turn_button", "end_turn_button_active",
    "screen_victory", "screen_defeat",
)
IDS = {name: index for index, name in enumerate(NAMES)}


def det(name: str) -> Detection:
    return Detection(IDS[name], name, 0.9, 0, 0, 10, 10)


def test_screen_needs_stable_frames_to_confirm_match() -> None:
    classifier = ScreenClassifier(confirm_frames=2)
    frame = [det("frontline")]
    # 首帧只建立候选，第二帧确认。
    assert classifier.classify(frame) == (SCENE_UNKNOWN, None)
    assert classifier.classify(frame) == (SCENE_MATCH, None)
    assert classifier.scene == SCENE_MATCH


def test_screen_end_turn_button_variants_confirm_match_without_turn_claim() -> None:
    classifier = ScreenClassifier(confirm_frames=2)
    inactive = [det("end_turn_button")]
    active = [det("end_turn_button_active")]  # unsupported 零样本类
    classifier.classify(inactive)
    scene, my_turn = classifier.classify(inactive)
    assert scene == SCENE_MATCH and my_turn is None
    # active 当前 unsupported：即使检测框出现也不得声称 True。
    _scene, my_turn = classifier.classify(active)
    assert my_turn is None
    _scene, my_turn = classifier.classify(active)
    assert my_turn is None
    assert classifier.my_turn is None


def test_screen_victory_and_defeat_take_priority() -> None:
    classifier = ScreenClassifier(confirm_frames=2)
    victory_frame = [det("frontline"), det("screen_victory")]  # 残留前线框
    assert classifier.classify(victory_frame)[0] == SCENE_UNKNOWN
    assert classifier.classify(victory_frame)[0] == SCENE_VICTORY
    assert classifier.my_turn is None

    classifier.invalidate()
    defeat_frame = [det("screen_defeat"), det("end_turn_button")]
    classifier.classify(defeat_frame)
    assert classifier.classify(defeat_frame)[0] == SCENE_DEFEAT


def test_screen_unknown_without_signal_and_none_input_keeps_state() -> None:
    classifier = ScreenClassifier(confirm_frames=2)
    assert classifier.classify([det("card_back")])[0] == SCENE_UNKNOWN
    # None 表示没有有效检测：不确认新场景，也不清空已确认场景。
    classifier.classify([det("frontline")])
    classifier.classify([det("frontline")])
    assert classifier.scene == SCENE_MATCH
    assert classifier.classify(None) == (SCENE_MATCH, None)


def test_screen_streak_resets_on_scene_change() -> None:
    classifier = ScreenClassifier(confirm_frames=3)
    for _ in range(2):
        classifier.classify([det("frontline")])   # 未到 3 帧，仍是 unknown
    assert classifier.scene == SCENE_UNKNOWN
    for _ in range(3):
        classifier.classify([det("screen_victory")])  # 场景切换重置计数
    assert classifier.scene == SCENE_VICTORY


def test_screen_invalidate_resets_baseline() -> None:
    classifier = ScreenClassifier(confirm_frames=2)
    classifier.classify([det("frontline")])
    classifier.classify([det("frontline")])
    assert classifier.scene == SCENE_MATCH
    classifier.invalidate()
    assert classifier.scene == SCENE_UNKNOWN
    assert classifier.my_turn is None
