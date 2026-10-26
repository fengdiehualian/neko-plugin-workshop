"""YOLO 改造移除契约：旧 kards_det/OCR/录像链路不得再被源码引用。

grep 断言 + card 任务接线（recognized_cards 区域观察 / 无 order / 无单位身份认领）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import test_api_contract as api
from kards_companion.state.model import E_MATCH_START, E_ORDER_CARD, SCENE_MATCH
from test_api_contract import (
    MAIN,
    FakeCapture,
    FakeClassifier,
    capture_result,
    trusted_health,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 受 D 所有的运行时源码（不含 tests/docs 与主智能体文件）。
SOURCE_FILES = [
    PLUGIN_ROOT / "__init__.py",
    *sorted((PLUGIN_ROOT / "perception").glob("*.py")),
    *sorted((PLUGIN_ROOT / "state").glob("*.py")),
    *sorted((PLUGIN_ROOT / "runtime").glob("*.py")),
    PLUGIN_ROOT / "scripts" / "bundle_manifest.py",
    PLUGIN_ROOT / "worker" / "package_check.py",
    PLUGIN_ROOT / "plugin.toml",
    PLUGIN_ROOT / "requirements-bundle.txt",
]

# 旧七类模型权重路径：任何运行时路径都不得再引用。
FORBIDDEN_PATTERNS = (
    "kards_det.onnx",
    "models/kards_det",
    "rapidocr",
    "RapidOCR",
    "hud_reader",
    "HudReader",
    "TemporaryFrameRecorder",
    "RecordingConfig",
    "RegionCalibrator",
    "FrameRing",
    "ocr_models/runtime.json",
    "ocr_runtime",
)


def test_runtime_sources_never_reference_removed_paths() -> None:
    for path in SOURCE_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in FORBIDDEN_PATTERNS:
            assert pattern not in text, f"{path.name} still references {pattern!r}"


def test_detector_module_has_no_builtin_seven_class_table() -> None:
    from kards_companion.perception import detector

    assert not hasattr(detector, "CLASS_NAMES")
    assert not hasattr(detector, "CLASS_TO_ID")
    assert not hasattr(detector, "YoloDetector")
    source = (PLUGIN_ROOT / "perception" / "detector.py").read_text(encoding="utf-8")
    assert "kards_det" not in source


def test_legacy_keys_are_declared_for_warning_only() -> None:
    # scheduling 声明旧键集合的唯一目的：读配置时忽略并记 warning。
    from kards_companion.perception.scheduling import LEGACY_CONFIG_KEYS

    assert "hud_fps" in LEGACY_CONFIG_KEYS
    assert "recording_enabled" in LEGACY_CONFIG_KEYS
    assert "roi_auto_calibrate" in LEGACY_CONFIG_KEYS


# ------------------------------------------------------- card 任务接线

class FakeCardResult:
    def __init__(self, card_id: str, name: str, conf: float, margin: float = 0.8):
        self.card_id = card_id
        self.name = name
        self.conf = conf
        self.margin = margin

    @property
    def known(self) -> bool:
        return self.card_id != "unknown"


class FakeCardClassifier:
    available = True

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls: list = []

    def classify(self, crop, appearance=None) -> object:
        # appearance：card_hand→hand_full / played_card→zoom_full（白名单外观键）。
        self.calls.append(crop)
        return self._results.pop(0) if self._results else FakeCardResult("unknown", "", 0.3)

    def status(self) -> dict:
        return {"role": "card_classify", "state": "loaded", "loaded": True,
                "nc": 1083, "class_map_version": 1}


@pytest.fixture
def card_plugin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    logger = api.FakeLogger()
    pushes: list[dict] = []
    base = MAIN.NekoPluginBase
    monkeypatch.setattr(base, "enable_file_logging",
                        lambda self, log_level=None: logger, raising=False)
    monkeypatch.setattr(base, "data_path",
                        lambda self, name: Path(tmp_path) / name, raising=False)
    monkeypatch.setattr(base, "push_message",
                        lambda self, **kwargs: (pushes.append(kwargs),
                                                {"submitted": True})[1],
                        raising=False)
    instance = api.PLUGIN_CLASS(ctx=object())
    instance.config = api.FakeConfig()
    instance._events_log = []
    instance.pushes = pushes
    instance.scheduler.due = lambda *args, **kwargs: True

    class _AlwaysDirty:
        def __get__(self, obj, objtype=None):
            return True

        def __set__(self, obj, value):
            pass

    monkeypatch.setattr(type(instance), "_change_dirty", _AlwaysDirty(),
                        raising=False)
    # 测试帧是 8x8：放宽最小裁图边长，聚焦接线而非尺寸门槛。
    monkeypatch.setattr(MAIN, "CARD_CROP_MIN_SIDE", 2)
    return instance


def _det(name: str, cid: int, box: tuple[float, float, float, float], conf: float = 0.9):
    # 用被测 MAIN 模块自己的 Detection 类，保证 isinstance 门通过
    # （API 契约加载器会生成独立的 perception.detector 模块实例）。
    import sys
    detection_cls = sys.modules[MAIN.__name__ + ".perception.detector"].Detection
    return detection_cls(cid, name, conf, *box)


def test_card_task_records_zoom_as_observation_only(card_plugin) -> None:
    plugin = card_plugin
    front = _det("frontline", 5, (0.0, 0.0, 8.0, 1.0))
    played = _det("played_card", 3, (1.0, 1.0, 6.0, 6.0))
    unit = _det("unit_board", 0, (2.0, 4.0, 5.0, 7.0))

    class ScriptedUi:
        def __init__(self) -> None:
            # 首帧空棋盘建 match 基线；第 2 帧打出特写；后两帧单位登场确认。
            self.frames = [[front], [front, played], [front, unit], [front, unit]]
            self.calls = 0

        def detect(self, _image):
            self.calls += 1
            return self.frames.pop(0) if self.frames else [front]

    class ScriptedScreen:
        def classify(self, detections):
            return SCENE_MATCH, True

    card = FakeCardClassifier([
        FakeCardResult("t34_1942", "T-34 1942型", 0.91),
    ])
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = ScriptedScreen()
    plugin.ui_detector = ScriptedUi()
    plugin.card_classifier = card
    plugin._capture = FakeCapture(
        [capture_result(i, (i * 10 % 250, i * 20 % 250, i * 30 % 250)) for i in range(1, 5)],
        [trusted_health(i) for i in range(1, 5)],
    )

    # tick1：空棋盘 -> match_start 基线（无卡牌事件）。
    plugin._perceive_once()
    assert [record["kind"] for record in plugin._events_log] == [E_MATCH_START]

    # tick2：played_card 只是放大/悬停区域观察，不代表已打出，也不带敌我。
    plugin._perceive_once()
    state = plugin._pipeline_state
    assert state.last_order_card is None
    assert E_ORDER_CARD not in [record["kind"] for record in plugin._events_log]
    recognized = plugin._recognized_cards_payload()
    assert len(recognized) == 1
    zoom = recognized[0]
    assert zoom["region"] == "played_card"
    assert zoom["card_id"] == "t34_1942"
    assert "instance_id" not in zoom
    assert str(zoom["observation_id"]).startswith("card:")
    assert int(zoom["frame_id"]) > 0

    # tick3：单位登场（第 1 帧观测）。
    plugin._perceive_once()
    assert plugin._pipeline_state.units

    # tick4：单位 2 帧确认 -> unit_played，但不得继承 zoom 卡牌身份。
    plugin._perceive_once()
    units = plugin._pipeline_state.units
    assert units
    for board_unit in units:
        assert board_unit.card_id is None
        assert board_unit.card_name is None
        assert board_unit.name_confidence == 0.0
        assert board_unit.attack is None
        assert board_unit.health is None
        assert board_unit.cost is None
    kinds = [record["kind"] for record in plugin._events_log]
    assert kinds == [E_MATCH_START, "unit_played"]
    assert not [item for item in plugin._recognized_cards_payload()
                if item.get("region") == "board"]


def test_card_task_skips_unknown_and_small_crops(card_plugin) -> None:
    plugin = card_plugin
    front = _det("frontline", 5, (0.0, 0.0, 8.0, 1.0))
    tiny = _det("card_hand", 1, (1.0, 1.0, 2.0, 2.0))   # 短边 1px < 2
    weak = _det("card_hand", 1, (1.0, 1.0, 6.0, 6.0))   # 分类结果 unknown
    good = _det("card_hand", 1, (1.5, 1.0, 6.5, 6.0))

    class ScriptedUi:
        def detect(self, _image):
            return [front, tiny, weak, good]

    card = FakeCardClassifier([
        FakeCardResult("unknown", "", 0.4),              # 低置信 -> unknown
        FakeCardResult("t70", "T-70", 0.95),
    ])
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = ScriptedUi()
    plugin.card_classifier = card
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0))], [trusted_health(1)])

    plugin._perceive_once()
    # 小裁图被跳过：只分类了两张 hand；unknown 结果不进 recognized_cards。
    assert len(card.calls) == 2
    recognized = plugin._recognized_cards_payload()
    assert len(recognized) == 1
    entry = recognized[0]
    assert entry["card_id"] == "t70"
    assert entry["name"] == "T-70"
    assert entry["region"] == "card_hand"
    assert entry["conf"] == pytest.approx(0.95)
    assert "instance_id" not in entry
    assert str(entry["observation_id"]).startswith("card:")
    assert entry["frame_id"] == 1
    assert plugin._pipeline_state.last_order_card is None


def test_card_task_same_frame_is_consumed_once(card_plugin) -> None:
    plugin = card_plugin
    front = _det("frontline", 5, (0.0, 0.0, 8.0, 1.0))
    hand = _det("card_hand", 1, (1.0, 1.0, 6.0, 6.0))

    class ScriptedUi:
        def detect(self, _image):
            return [front, hand]

    card = FakeCardClassifier([FakeCardResult("t70", "T-70", 0.9)])
    plugin._monitoring = True
    plugin._cfg = {"tick_ms": 333, "chatter_interval_seconds": 0}
    plugin._apply_config({"kards": plugin._cfg})
    plugin.screen_classifier = FakeClassifier()
    plugin.ui_detector = ScriptedUi()
    plugin.card_classifier = card
    # card 任务每 tick 到期，unit 任务只在第 1 帧到期：第 2 tick 的 card
    # 任务面对的是同一 pending 帧，不得重复分类。
    plugin.scheduler.due = (
        lambda task, frame_id=None, **_kw: True if task == "card"
        else int(frame_id or 0) <= 1)
    plugin._capture = FakeCapture(
        [capture_result(1, (0, 0, 0)), capture_result(2, (5, 5, 5))],
        [trusted_health(1), trusted_health(2)],
    )
    plugin._perceive_once()
    assert len(card.calls) == 1
    with plugin._card_lock:
        consumed = plugin._card_consumed_frame
    assert consumed == 1
    plugin._perceive_once()
    with plugin._card_lock:
        assert plugin._card_consumed_frame == 1
    assert len(card.calls) == 1


def test_status_models_and_fps_measured_keys(card_plugin) -> None:
    plugin = card_plugin
    payload = asyncio.run(plugin.monitor_status()).data
    assert payload["schema_version"] == "kards_status_v2"
    models = payload["models"]
    assert set(models) == {"ui_det", "card_cls", "class_map_versions",
                           "trusted_card_count"}
    assert models["ui_det"]["file"].endswith("ui_det.onnx")
    assert models["card_cls"]["file"].endswith("card_cls.onnx")
    assert json.dumps(payload, ensure_ascii=False)
