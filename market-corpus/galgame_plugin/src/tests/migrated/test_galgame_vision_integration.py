from __future__ import annotations

from importlib import import_module
from pathlib import Path

from PIL import Image
from tests.support.plugin_runtime_stubs import install_plugin_runtime_stubs

install_plugin_runtime_stubs()

models_module = import_module("market_plugins.galgame_plugin.models")
ocr_bridge_writer_module = import_module(
    "market_plugins.galgame_plugin.ocr_bridge_writer"
)
ocr_reader_module = import_module("market_plugins.galgame_plugin.ocr_reader")
ocr_runtime_types_module = import_module(
    "market_plugins.galgame_plugin.ocr_runtime_types"
)
reader_module = import_module("market_plugins.galgame_plugin.reader")
screen_classifier_module = import_module(
    "market_plugins.galgame_plugin.screen_classifier"
)
service_module = import_module("market_plugins.galgame_plugin.service")
text_module = import_module("market_plugins.galgame_plugin.ocr_manager_text")

OCR_CAPTURE_PROFILE_STAGE_DIALOGUE = (
    models_module.OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
)
OCR_CAPTURE_PROFILE_STAGE_TITLE = models_module.OCR_CAPTURE_PROFILE_STAGE_TITLE
DetectedGameWindow = ocr_reader_module.DetectedGameWindow
OcrExtractionResult = ocr_runtime_types_module.OcrExtractionResult
OcrReaderBridgeWriter = ocr_bridge_writer_module.OcrReaderBridgeWriter
OcrReaderManager = ocr_reader_module.OcrReaderManager
ScreenClassification = screen_classifier_module.ScreenClassification
build_config = service_module.build_config
tail_events_jsonl = reader_module.tail_events_jsonl


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None

    def debug(self, *_args, **_kwargs) -> None:
        return None

    def exception(self, *_args, **_kwargs) -> None:
        return None


class _FakeCaptureBackend:
    def is_available(self) -> bool:
        return True

    def describe_target(self, target: object) -> str:
        return str(target)

    def capture_frame(self, *_args, **_kwargs) -> str:
        return "frame"


class _FakeOcrBackend:
    def is_available(self) -> bool:
        return True

    def extract_text(self, _image: object) -> str:
        return ""


def _make_config(bridge_root: Path) -> object:
    return build_config(
        {
            "galgame": {"bridge_root": str(bridge_root)},
            "ocr_reader": {
                "enabled": True,
                "poll_interval_seconds": 999.0,
            },
        }
    )


def _read_events(events_path: Path) -> list[dict[str, object]]:
    return tail_events_jsonl(events_path, offset=0, line_buffer=b"").events


def _window() -> list[object]:
    return [
        DetectedGameWindow(
            hwnd=101,
            title="Demo Window",
            process_name="DemoGame.exe",
            pid=4242,
        )
    ]


class _FakeVisionClassifier:
    def __init__(self, result: dict[str, object] | None) -> None:
        self.result = result
        self.calls = 0

    def classify(self, image):
        assert image is not None
        self.calls += 1
        return self.result


def _manager(tmp_path, vision_result: dict[str, object] | None) -> OcrReaderManager:
    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    writer = OcrReaderBridgeWriter(bridge_root=bridge_root, time_fn=lambda: 100.0)
    writer.start_session(_window()[0])
    manager = OcrReaderManager(
        logger=_Logger(),
        config=_make_config(bridge_root),
        time_fn=lambda: 100.0,
        platform_fn=lambda: True,
        window_scanner=_window,
        capture_backend=_FakeCaptureBackend(),
        ocr_backend=_FakeOcrBackend(),
        writer=writer,
    )
    manager.vision_classifier = _FakeVisionClassifier(vision_result)
    manager._config.vision_classifier_enabled = True
    manager._config.vision_classifier_threshold = 0.75
    return manager


def test_cnn_high_confidence_skips_ocr_rule_fallback(tmp_path, monkeypatch) -> None:
    manager = _manager(
        tmp_path,
        {
            "label": "dialogue",
            "screen_type": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "confidence": 0.93,
            "all_scores": {"dialogue": 0.93},
            "latency_ms": 1.0,
        },
    )
    manager._writer.emit_screen_classified(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.9,
        ui_elements=[],
        raw_ocr_text=["Start"],
        screen_debug={"reason": "test"},
        ts="2026-05-22T00:00:00Z",
    )
    monkeypatch.setattr(
        text_module,
        "classify_screen_from_ocr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback should not run")),
    )
    calls = {"stability": 0}

    def _stability(classification):
        calls["stability"] += 1
        return classification

    def _update_capture_profile(*_args, **_kwargs):
        calls["capture_profile"] = calls.get("capture_profile", 0) + 1

    def _collect_sample(*_args, **_kwargs):
        calls["sample"] = calls.get("sample", 0) + 1

    monkeypatch.setattr(manager, "_apply_screen_classification_stability", _stability)
    monkeypatch.setattr(manager, "_update_capture_profile_recommendation", _update_capture_profile)
    monkeypatch.setattr(manager, "_collect_screen_awareness_sample", _collect_sample)

    classification, emitted = manager._emit_screen_classification_from_extraction(
        OcrExtractionResult(text=""),
        target=_window()[0],
        now=101.0,
        image=Image.new("RGB", (320, 180), "black"),
    )

    assert classification.screen_type == OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
    assert classification.confidence == 0.93
    assert classification.debug["source"] == "cnn_primary"
    assert classification.debug["label"] == "dialogue"
    assert emitted is True
    assert calls["stability"] == 1
    assert calls["capture_profile"] == 1
    assert calls["sample"] == 1
    assert manager._screen_awareness_model_detail == "skipped_cnn_primary"
    assert manager.vision_classifier.calls == 1


def test_cnn_low_confidence_falls_back_to_ocr_rules(tmp_path, monkeypatch) -> None:
    manager = _manager(
        tmp_path,
        {
            "label": "dialogue",
            "screen_type": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "confidence": 0.42,
            "all_scores": {"dialogue": 0.42},
            "latency_ms": 1.0,
        },
    )
    fallback = ScreenClassification(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.88,
        raw_ocr_text=["Start"],
        debug={"reason": "test_fallback"},
    )
    monkeypatch.setattr(text_module, "classify_screen_from_ocr", lambda *_args, **_kwargs: fallback)

    classification, emitted = manager._emit_screen_classification_from_extraction(
        OcrExtractionResult(text="Start"),
        target=_window()[0],
        now=101.0,
        image=Image.new("RGB", (320, 180), "black"),
    )

    events = _read_events(tmp_path / "bridge" / manager._writer.game_id / "events.jsonl")

    assert classification.screen_type == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert classification.debug["reason"] == "test_fallback"
    assert emitted is True
    assert events[-1]["payload"]["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_TITLE


def test_cnn_non_finite_confidence_falls_back_to_ocr_rules(tmp_path, monkeypatch) -> None:
    manager = _manager(
        tmp_path,
        {
            "label": "dialogue",
            "screen_type": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "confidence": float("nan"),
            "all_scores": {"dialogue": float("nan")},
            "latency_ms": 1.0,
        },
    )
    fallback = ScreenClassification(
        screen_type=OCR_CAPTURE_PROFILE_STAGE_TITLE,
        confidence=0.88,
        raw_ocr_text=["Start"],
        debug={"reason": "test_fallback"},
    )
    monkeypatch.setattr(text_module, "classify_screen_from_ocr", lambda *_args, **_kwargs: fallback)

    classification, emitted = manager._emit_screen_classification_from_extraction(
        OcrExtractionResult(text="Start"),
        target=_window()[0],
        now=101.0,
        image=Image.new("RGB", (320, 180), "black"),
    )

    assert classification.screen_type == OCR_CAPTURE_PROFILE_STAGE_TITLE
    assert classification.debug["reason"] == "test_fallback"
    assert emitted is True
    assert manager._vision_classifier_detail == "invalid_confidence"


def test_cnn_skipped_interval_preserves_last_successful_status(tmp_path) -> None:
    manager = _manager(
        tmp_path,
        {
            "label": "dialogue",
            "screen_type": OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
            "confidence": 0.93,
            "all_scores": {"dialogue": 0.93},
            "latency_ms": 1.0,
        },
    )
    manager._config.vision_classifier_tick_interval = 2
    manager._vision_classifier_tick_count = 1
    manager._vision_classifier_last_label = "dialogue"
    manager._vision_classifier_last_confidence = 0.93
    manager._vision_classifier_last_latency_ms = 1.0

    classification = manager._classify_screen_with_vision(
        OcrExtractionResult(text=""),
        image=Image.new("RGB", (320, 180), "black"),
    )

    assert classification is None
    assert manager._vision_classifier_detail == "skipped_interval"
    assert manager._vision_classifier_last_label == "dialogue"
    assert manager._vision_classifier_last_confidence == 0.93
    assert manager._vision_classifier_last_latency_ms == 1.0
    assert manager.vision_classifier.calls == 0
