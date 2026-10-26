"""双 YOLO 模型加载层契约：合成张量 + 假 ONNX 会话，不需要真实权重。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from kards_companion.perception import models as models_mod
from kards_companion.perception.models import (
    CARD_CLS_PATH,
    UI_DET_PATH,
    CardClassifier,
    UiDetector,
    load_card_class_map,
    load_trusted_card_count,
    load_ui_class_map,
    postprocess_detect,
)
from PIL import Image

UI_CLASSES = (
    "unit_board", "card_hand", "card_back", "played_card", "hq",
    "frontline", "end_turn_button", "end_turn_button_active",
    "screen_victory", "screen_defeat",
)
PRODUCTION_SUPPORTED = ("card_back", "end_turn_button")

FAKE_UI_WEIGHTS = b"fake-ui-weights-only"
FAKE_CARD_WEIGHTS = b"fake-card-classifier-weights-only"
_MISSING = object()


@dataclass
class FakeTensorMeta:
    name: str = "input"
    shape: list = field(default_factory=list)
    type: str = "tensor(float)"


class FakeSession:
    """onnxruntime.InferenceSession 的最小替身：静态形状 + 可编程输出。"""

    def __init__(self, input_shape: list, output_shape: list,
                 output: np.ndarray | None = None,
                 input_type: str = "tensor(float)"):
        self._inputs = [FakeTensorMeta("images", input_shape, input_type)]
        self._outputs = [FakeTensorMeta("output0", output_shape)]
        self._output = output
        self.calls: list[np.ndarray] = []

    def get_inputs(self):
        return self._inputs

    def get_outputs(self):
        return self._outputs

    def run(self, _targets, feeds):
        self.calls.append(feeds[self._inputs[0].name])
        assert self._output is not None, "FakeSession.run without programmed output"
        return [self._output]


# ---------------------------------------------------------------- 契约文件

def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _manifest_path(root: Path) -> Path:
    return root / "knowledge" / "models_manifest.json"


def _manifest_entry(root: Path, role: str) -> dict:
    data = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    for entry in data["models"]:
        if entry.get("role") == role:
            return entry
    raise AssertionError(f"manifest missing role {role}")


def _set_manifest_field(root: Path, role: str, key: str,
                        value: object = _MISSING) -> None:
    path = _manifest_path(root)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["models"]:
        if entry.get("role") != role:
            continue
        if value is _MISSING:
            entry.pop(key, None)
        else:
            entry[key] = value
        break
    else:
        raise AssertionError(f"manifest missing role {role}")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _rewrite_manifest_sha(root: Path, role: str, payload: bytes) -> None:
    _set_manifest_field(root, role, "sha256", _sha256(payload))


def _set_ui_capabilities(root: Path, capabilities: object = _MISSING) -> None:
    _set_manifest_field(root, "ui_detect", "capabilities", capabilities)


@pytest.fixture()
def contract_root(tmp_path: Path) -> Path:
    """复制真实契约 JSON 到临时插件根，并为**假模型**生成独立 sha256。

    production manifest 里的真实 sha 不会被复制到测试根：fixture 写入的
    payload 与各自条目重新计算指纹，严格校验仍然生效。默认 capability 保持
    production 值（card_back/end_turn_button），需要全类别用例自行覆盖。
    默认写入覆盖前三张非 conflict 卡的可信白名单（全外观）。
    """
    src = Path(models_mod.PLUGIN_ROOT) / "knowledge"
    dst = tmp_path / "knowledge"
    dst.mkdir(parents=True)
    card_data: dict = {}
    for name in ("models_manifest.json", "ui_classes.json", "card_classes.json"):
        data = json.loads((src / name).read_text(encoding="utf-8"))
        if name == "card_classes.json":
            card_data = data
        (dst / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    (models / "ui_det.onnx").write_bytes(FAKE_UI_WEIGHTS)
    (models / "card_cls.onnx").write_bytes(FAKE_CARD_WEIGHTS)
    _rewrite_manifest_sha(tmp_path, "ui_detect", FAKE_UI_WEIGHTS)
    _rewrite_manifest_sha(tmp_path, "card_classify", FAKE_CARD_WEIGHTS)
    trusted_ids = [c["card_id"] for c in card_data.get("classes", [])
                   if not c.get("conflict") and c.get("asset_status", "ok") == "ok"][:3]
    (dst / "trusted_cards.json").write_text(json.dumps({
        "schema": "kards.trusted-cards", "version": 1,
        "entries": [{"card_id": cid,
                     "appearance": {"hand_full": True, "zoom_full": True}}
                    for cid in trusted_ids],
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def make_det_session(output_channels: int, output: np.ndarray | None = None, *,
                     input_shape: list | None = None,
                     input_type: str = "tensor(float)") -> FakeSession:
    return FakeSession(input_shape or [1, 3, 640, 640],
                       [1, output_channels, 8400], output, input_type)


def make_cls_session(num_classes: int, output: np.ndarray | None = None, *,
                     input_shape: list | None = None,
                     input_type: str = "tensor(float)") -> FakeSession:
    return FakeSession(input_shape or [1, 3, 224, 224],
                       [1, num_classes], output, input_type)


def det_output(entries: list[tuple[int, float]], *, n: int = 10) -> np.ndarray:
    """构造 (1, 4+n, len(entries)) 检测张量；框位置互不重叠。"""
    out = np.zeros((1, 4 + n, len(entries)), dtype=np.float32)
    for column, (class_id, conf) in enumerate(entries):
        x = 40.0 + column * 120.0
        out[0, 0, column], out[0, 1, column] = x, 320.0
        out[0, 2, column], out[0, 3, column] = 60.0, 80.0
        out[0, 4 + class_id, column] = conf
    return out


# ---------------------------------------------------------------- UiDetector

def _ui_detector(root: Path, session: FakeSession | None = None) -> UiDetector:
    return UiDetector(root / "models" / "ui_det.onnx", root=root, session=session)


def test_ui_detector_missing_file_is_unavailable_not_exception(
        contract_root: Path) -> None:
    (contract_root / "models" / "ui_det.onnx").unlink()
    detector = UiDetector(contract_root / "models" / "ui_det.onnx",
                          root=contract_root)
    assert detector.available is False
    assert detector.state == "unavailable"
    assert "model_missing" in detector.diagnostics
    assert detector.detect(Image.new("RGB", (64, 64))) is None
    status = detector.status()
    assert status["state"] == "unavailable" and status["nc"] == 10
    assert status["supported_classes"] == list(PRODUCTION_SUPPORTED)


def test_ui_detector_valid_session_loads_with_truthful_capabilities(
        contract_root: Path) -> None:
    detector = _ui_detector(contract_root, make_det_session(4 + 10))
    assert detector.available is True
    assert detector.state == "loaded"
    assert detector.class_map is not None and detector.class_map.num_classes == 10
    assert detector.sha256 == _sha256(FAKE_UI_WEIGHTS)
    status = detector.status()
    assert status["loaded"] is True and status["nc"] == 10
    # loaded 只代表权重可用；能力仍只有 manifest 验收的两类。
    assert status["capability_state"] == "manifest"
    assert status["supported_classes"] == list(PRODUCTION_SUPPORTED)
    assert status["supported_class_count"] == 2
    assert "unit_board" in status["unsupported_classes"]
    assert "frontline" in status["unsupported_classes"]
    assert status["unsupported_class_count"] == 8
    assert status["capability_diagnostics"] == ""


def test_ui_detector_rejects_wrong_channel_count(contract_root: Path) -> None:
    # 旧七类模型：4+7=11 通道 != 4+10。
    detector = _ui_detector(contract_root, make_det_session(11))
    assert detector.available is False
    assert detector.state == "rejected"
    assert "output_shape=[1, 11, 8400]" in detector.diagnostics
    assert "expected=[1, 14, 8400]" in detector.diagnostics


def test_ui_detector_rejects_non_static_or_wrong_input(contract_root: Path) -> None:
    wrong_size = _ui_detector(
        contract_root, make_det_session(14, input_shape=[1, 3, 320, 320]))
    assert wrong_size.state == "rejected"
    assert "input_shape=[1, 3, 320, 320]" in wrong_size.diagnostics

    wrong_dtype = _ui_detector(
        contract_root, make_det_session(14, input_type="tensor(uint8)"))
    assert wrong_dtype.state == "rejected"
    assert "input_dtype" in wrong_dtype.diagnostics
    assert "expected=float32" in wrong_dtype.diagnostics


def test_ui_detector_rejects_task_mismatch_in_class_map(
        contract_root: Path) -> None:
    path = contract_root / "knowledge" / "ui_classes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["task"] = "classify"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    detector = _ui_detector(contract_root, make_det_session(14))
    assert detector.state == "rejected"
    assert "task_mismatch" in detector.diagnostics


@pytest.mark.parametrize(
    ("field", "value", "marker"),
    [
        ("task", "classify", "manifest_task_mismatch"),
        ("file", "models/other.onnx", "manifest_file_mismatch"),
        ("class_map", "knowledge/other.json", "manifest_class_map_mismatch"),
        ("num_classes", 11, "manifest_num_classes"),
        ("class_map_version", 2, "manifest_class_map_version"),
        ("sha256", _MISSING, "manifest_sha256_invalid"),
        ("sha256", "not-a-sha", "manifest_sha256_invalid"),
    ],
)
def test_ui_detector_manifest_contract_is_strict(
        contract_root: Path, field: str, value: object, marker: str) -> None:
    _set_manifest_field(contract_root, "ui_detect", field, value)
    detector = _ui_detector(contract_root, make_det_session(14))
    assert detector.state == "rejected"
    assert marker in detector.diagnostics


def test_ui_detector_rejects_wrong_sha(contract_root: Path) -> None:
    (contract_root / "models" / "ui_det.onnx").write_bytes(b"tampered")
    detector = _ui_detector(contract_root, make_det_session(14))
    assert detector.state == "rejected"
    assert detector.diagnostics == "sha256_mismatch"


def test_ui_detector_capability_hard_gate_filters_runtime_detections(
        contract_root: Path) -> None:
    _set_ui_capabilities(contract_root, ["card_back", "end_turn_button"])
    output = det_output([(0, 0.95), (6, 0.90), (4, 0.85), (2, 0.80)])
    detector = _ui_detector(contract_root, make_det_session(14, output=output))
    detections = detector.detect(Image.new("RGB", (1280, 1280)))
    assert detections is not None
    # unit_board / hq 不因模型训练过就被启用。
    assert {d.name for d in detections} == {"end_turn_button", "card_back"}
    assert detector.status()["suppressed_detections"] == 2
    assert detector.status()["suppressed_detections_total"] == 2


def test_ui_detector_empty_capabilities_are_valid_empty_not_failure(
        contract_root: Path) -> None:
    _set_ui_capabilities(contract_root, [])
    output = det_output([(2, 0.95), (6, 0.90)])
    detector = _ui_detector(contract_root, make_det_session(14, output=output))
    assert detector.state == "loaded"
    detections = detector.detect(Image.new("RGB", (1280, 1280)))
    assert detections == []          # 模型确实跑过：有效空结果，而非 None
    status = detector.status()
    assert status["capability_state"] == "empty"
    assert status["supported_classes"] == []
    assert status["unsupported_classes"] == list(UI_CLASSES)
    assert status["suppressed_detections"] == 2
    assert "capabilities_empty" in status["capability_diagnostics"]


def test_ui_detector_missing_capabilities_fail_closed(contract_root: Path) -> None:
    _set_ui_capabilities(contract_root)   # pop key
    output = det_output([(2, 0.95)])
    detector = _ui_detector(contract_root, make_det_session(14, output=output))
    assert detector.state == "loaded"
    detections = detector.detect(Image.new("RGB", (1280, 1280)))
    assert detections == []          # 不是 None：推理成功但全部被硬门压制
    status = detector.status()
    assert status["capability_state"] == "missing"
    assert status["supported_classes"] == []
    assert status["suppressed_detections"] == 1
    assert "capabilities_missing:fail_closed" in status["capability_diagnostics"]


def test_ui_detector_unknown_capability_name_is_rejected(
        contract_root: Path) -> None:
    _set_ui_capabilities(contract_root, ["card_back", "not_a_ui_class"])
    detector = _ui_detector(contract_root, make_det_session(14))
    assert detector.available is False
    assert detector.state == "rejected"
    assert "capabilities_unknown:not_a_ui_class" in detector.diagnostics
    assert "known=" in detector.diagnostics


def test_ui_detector_invalid_capabilities_shape_is_rejected(
        contract_root: Path) -> None:
    _set_ui_capabilities(contract_root, "card_back")
    detector = _ui_detector(contract_root, make_det_session(14))
    assert detector.state == "rejected"
    assert "capabilities_invalid" in detector.diagnostics


def test_ui_detector_all_verified_capabilities_can_be_enabled(
        contract_root: Path) -> None:
    _set_ui_capabilities(contract_root, list(UI_CLASSES))
    output = det_output([(0, 0.90)])
    detector = _ui_detector(contract_root, make_det_session(14, output=output))
    detections = detector.detect(Image.new("RGB", (1280, 1280)))
    assert detections is not None
    assert [d.name for d in detections] == ["unit_board"]
    assert detector.status()["supported_class_count"] == 10
    assert detector.status()["suppressed_detections"] == 0


def test_ui_detector_detect_end_to_end(contract_root: Path) -> None:
    output = det_output([(2, 0.90)])
    detector = _ui_detector(contract_root, make_det_session(14, output=output))
    detections = detector.detect(Image.new("RGB", (1280, 1280)))
    assert detections is not None
    assert [d.name for d in detections] == ["card_back"]
    # letterbox 1280→640：坐标翻倍回原图。
    assert abs(detections[0].cx - 80) < 3


def test_ui_detector_inference_failure_moves_to_error(
        contract_root: Path) -> None:
    class ExplodingSession(FakeSession):
        def run(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    detector = _ui_detector(
        contract_root, ExplodingSession([1, 3, 640, 640], [1, 14, 8400]))
    assert detector.available is True
    assert detector.detect(Image.new("RGB", (64, 64))) is None
    assert detector.state == "error"
    assert "inference_failed" in detector.diagnostics
    # 推理失败不是有效空盘：suppressed 计数不应被当作过滤结果。
    assert detector.status()["suppressed_detections"] == 0


# ------------------------------------------------------------- CardClassifier

def _card_classifier(root: Path, session: FakeSession) -> CardClassifier:
    return CardClassifier(root / "models" / "card_cls.onnx", root=root,
                          session=session)


def _classifier_with(root: Path, probs: np.ndarray) -> CardClassifier:
    return _card_classifier(
        root, make_cls_session(probs.size, output=probs.reshape(1, -1)))


def test_card_classifier_validates_output_class_count(contract_root: Path) -> None:
    card_map = models_mod.load_card_class_map(contract_root)[0]
    assert card_map is not None and card_map.num_classes == 1083
    good = _card_classifier(contract_root, make_cls_session(1083))
    assert good.available is True
    assert good.sha256 == _sha256(FAKE_CARD_WEIGHTS)
    status = good.status()
    assert status["nc"] == 1083
    assert good.min_conf == pytest.approx(0.55)
    assert good.min_margin == pytest.approx(0.15)
    # 单帧声明必须真实：manifest 声明 2 帧，本版没有多帧跟踪。
    assert status["decision_mode"] == "single_frame"
    assert status["frames_to_confirm"] == 1
    assert status["manifest_frames_to_confirm"] == 2
    assert status["multi_frame_confirmation"] is False
    assert status["trusted_whitelist_applied"] is True

    bad = _card_classifier(contract_root, make_cls_session(512))
    assert bad.available is False
    assert bad.state == "rejected"
    assert "output_shape=[1, 512]" in bad.diagnostics
    assert "expected=[1, 1083]" in bad.diagnostics


def test_card_classifier_manifest_and_sha_are_strict(contract_root: Path) -> None:
    _set_manifest_field(contract_root, "card_classify", "num_classes", 512)
    bad = _card_classifier(contract_root, make_cls_session(1083))
    assert bad.state == "rejected"
    assert "manifest_num_classes=512" in bad.diagnostics

    _set_manifest_field(contract_root, "card_classify", "num_classes", 1083)
    (contract_root / "models" / "card_cls.onnx").write_bytes(b"tampered")
    tampered = _card_classifier(contract_root, make_cls_session(1083))
    assert tampered.state == "rejected"
    assert tampered.diagnostics == "sha256_mismatch"


def test_card_classifier_thresholds_force_unknown(contract_root: Path) -> None:
    card_map = models_mod.load_card_class_map(contract_root)[0]
    assert card_map is not None
    n = card_map.num_classes

    # conf 充足但 margin 不足（两个强类几乎打平）。
    flat = np.full(n, 0.0004, dtype=np.float32)
    flat[0] = 0.56
    flat[1] = 0.44
    result = _classifier_with(contract_root, flat).classify(
        Image.new("RGB", (300, 420)))
    assert result is not None and result.card_id == "unknown"
    assert result.rejected is True and result.conf >= 0.55 and result.margin < 0.15
    assert result.reason == "below_threshold"

    # margin 充足但 conf 不足。
    low = np.zeros(n, dtype=np.float32)
    low[0] = 0.50
    result = _classifier_with(contract_root, low).classify(
        Image.new("RGB", (300, 420)))
    assert result is not None and result.card_id == "unknown"
    assert result.reason == "below_threshold"

    # 双阈值都满足 → 命中真实 card_id。
    fine = np.zeros(n, dtype=np.float32)
    fine[0] = 0.9
    fine[1] = 0.05
    result = _classifier_with(contract_root, fine).classify(
        Image.new("RGB", (300, 420)))
    assert result is not None and result.known
    assert result.card_id == card_map.classes[0].card_id
    assert result.name == card_map.classes[0].name
    assert result.conf >= 0.55 and result.margin >= 0.15


def test_card_classifier_blocked_raw_top_is_unknown_not_reclassified(
        contract_root: Path) -> None:
    """raw top1 被 conflict/blocked 时不得让次高顶替成高置信身份。"""
    card_map = models_mod.load_card_class_map(contract_root)[0]
    assert card_map is not None
    conflicts = [c for c in card_map.classes if c.conflict]
    assert conflicts, "card_classes.json 应包含 conflict 标记"
    top_conflict = conflicts[0]

    probs = np.zeros(card_map.num_classes, dtype=np.float32)
    probs[top_conflict.id] = 0.97
    probs[0] = 0.90
    result = _classifier_with(contract_root, probs).classify(
        Image.new("RGB", (300, 420)))
    assert result is not None
    assert result.card_id == "unknown"
    assert result.rejected is True
    assert result.reason == "blocked_top"
    assert result.raw_card_id == top_conflict.card_id
    # 不重归一化/不吞掉 raw 概率：conf 仍是原始 top1。
    assert result.conf == pytest.approx(0.97, abs=1e-4)
    assert result.margin == pytest.approx(0.07, abs=1e-4)
    assert result.card_id != card_map.classes[0].card_id

    # blocked 是唯一强类 → unknown，且如实报告 0.99 而非置 0。
    only = np.zeros(card_map.num_classes, dtype=np.float32)
    only[top_conflict.id] = 0.99
    result = _classifier_with(contract_root, only).classify(
        Image.new("RGB", (300, 420)))
    assert result is not None and result.card_id == "unknown"
    assert result.reason == "blocked_top"
    assert result.conf == pytest.approx(0.99, abs=1e-4)
    assert result.margin == pytest.approx(0.99, abs=1e-4)


def test_card_classifier_blocked_runner_up_does_not_inflate_margin(
        contract_root: Path) -> None:
    """次高是 blocked 类时 margin 用原始 top1-top2，不挤掉后放大置信度。"""
    card_map = models_mod.load_card_class_map(contract_root)[0]
    assert card_map is not None
    top_conflict = next(c for c in card_map.classes if c.conflict)
    probs = np.zeros(card_map.num_classes, dtype=np.float32)
    probs[0] = 0.90
    probs[top_conflict.id] = 0.85
    result = _classifier_with(contract_root, probs).classify(
        Image.new("RGB", (300, 420)))
    assert result is not None
    assert result.card_id == "unknown"
    assert result.reason == "below_threshold"
    assert result.margin == pytest.approx(0.05, abs=1e-4)
    assert result.raw_card_id == card_map.classes[0].card_id


def test_card_classifier_whitelist_gate(contract_root: Path) -> None:
    """白名单硬门：不在白名单 / 外观未验证 / 空白名单 → 一律 unknown。"""
    card_map = models_mod.load_card_class_map(contract_root)[0]
    assert card_map is not None

    def classifier() -> CardClassifier:
        fine = np.zeros(card_map.num_classes, dtype=np.float32)
        fine[0] = 0.9
        fine[1] = 0.05
        return _classifier_with(contract_root, fine)

    first = card_map.classes[0]
    # fixture 白名单含前三张非 conflict 卡：无外观参数（测试路径）允许。
    result = classifier().classify(Image.new("RGB", (300, 420)))
    assert result is not None and result.card_id == first.card_id

    # 外观已验证 → 命中；外观未验证（board_crop）→ unknown 但保留 raw 诊断。
    result = classifier().classify(Image.new("RGB", (300, 420)), "hand_full")
    assert result is not None and result.card_id == first.card_id
    result = classifier().classify(Image.new("RGB", (300, 420)), "board_crop")
    assert result is not None and result.card_id == "unknown"
    assert result.rejected is True and result.raw_card_id == first.card_id
    assert result.reason == "not_trusted"

    # 空白名单（文件删除）→ 全部 unknown；conf/margin 仍如实保留。
    (contract_root / "knowledge" / "trusted_cards.json").unlink()
    empty = classifier()
    assert empty.trusted_count == 0
    result = empty.classify(Image.new("RGB", (300, 420)), "hand_full")
    assert result is not None and result.card_id == "unknown"
    assert result.rejected is True and result.raw_card_id == first.card_id
    assert result.reason == "not_trusted"
    assert result.conf >= 0.55


def test_card_classifier_decide_rejects_wrong_vector_size(
        contract_root: Path) -> None:
    card_map = models_mod.load_card_class_map(contract_root)[0]
    assert card_map is not None
    classifier = _card_classifier(
        contract_root, make_cls_session(card_map.num_classes))
    short = classifier.decide(np.zeros(10, dtype=np.float32))
    assert short.card_id == "unknown" and short.rejected is True
    assert short.reason == "size_mismatch"


def test_card_classifier_preprocess_short_side_and_center_crop(
        contract_root: Path) -> None:
    # 300x420 → 短边 300 缩到 224（scale=224/300），再中心裁剪 224。
    blob = CardClassifier.preprocess(Image.new("RGB", (300, 420), (128, 64, 32)))
    assert blob.shape == (1, 3, 224, 224)
    assert blob.dtype == np.float32
    assert 0.0 <= float(blob.min()) <= float(blob.max()) <= 1.0


# ---------------------------------------------------------------- class maps

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _ui_map(classes: list[dict] | None = None, **overrides) -> dict:
    data = {
        "schema": "kards.ui-classes", "version": 1, "task": "detect",
        "classes": classes if classes is not None else [
            {"id": 0, "name": "unit_board"},
            {"id": 1, "name": "card_hand"},
            {"id": 2, "name": "card_back"},
        ],
    }
    data.update(overrides)
    return data


def test_load_ui_class_map_accepts_valid_map(tmp_path: Path) -> None:
    _write_json(tmp_path / "knowledge" / "ui_classes.json", _ui_map())
    class_map, error = load_ui_class_map(tmp_path)
    assert error == ""
    assert class_map is not None
    assert class_map.names == ("unit_board", "card_hand", "card_back")


@pytest.mark.parametrize(
    ("classes", "overrides", "marker"),
    [
        ([{"id": 1, "name": "a"}, {"id": 1, "name": "b"}], {},
         "class_map_id_mismatch:0"),
        ([{"id": False, "name": "a"}], {}, "class_map_id_mismatch:0"),
        ([{"id": "0", "name": "a"}], {}, "class_map_id_mismatch:0"),
        ([{"id": 0, "name": "a"}, {"id": 1, "name": " a "}], {},
         "class_map_duplicate_name:a"),
        ([{"id": 0, "name": ""}], {}, "class_map_bad_entry:0"),
        (None, {"version": 0}, "class_map_version_invalid"),
        (None, {"version": "1"}, "class_map_version_invalid"),
        (None, {"task": ""}, "class_map_task_invalid"),
    ],
)
def test_load_ui_class_map_rejects_invalid(
        tmp_path: Path, classes: list[dict] | None, overrides: dict,
        marker: str) -> None:
    _write_json(tmp_path / "knowledge" / "ui_classes.json",
                _ui_map(classes, **overrides))
    class_map, error = load_ui_class_map(tmp_path)
    assert class_map is None
    assert marker in error


def _card_map(classes: list[dict] | None = None, **overrides) -> dict:
    data = {
        "schema": "kards.card-classes", "version": 1, "task": "classify",
        "classes": classes if classes is not None else [
            {"id": 0, "card_id": "a", "name": "A", "conflict": False},
            {"id": 1, "card_id": "b", "name": "B", "conflict": False},
        ],
    }
    data.update(overrides)
    return data


def test_load_card_class_map_accepts_duplicate_display_names(tmp_path: Path) -> None:
    _write_json(tmp_path / "knowledge" / "card_classes.json",
                _card_map([
                    {"id": 0, "card_id": "a", "name": "同名"},
                    {"id": 1, "card_id": "b", "name": "同名"},
                ]))
    class_map, error = load_card_class_map(tmp_path)
    assert error == ""
    assert class_map is not None and class_map.num_classes == 2


@pytest.mark.parametrize(
    ("classes", "overrides", "marker"),
    [
        ([{"id": True, "card_id": "a"}], {}, "class_map_id_mismatch:0"),
        ([{"id": "0", "card_id": "a"}], {}, "class_map_id_mismatch:0"),
        ([{"id": 0, "card_id": "a"}, {"id": 1, "card_id": "a"}], {},
         "class_map_duplicate_card_id:a"),
        ([{"id": 0, "card_id": ""}], {}, "class_map_bad_entry:0"),
        ([{"id": 0, "card_id": "a", "conflict": "false"}], {},
         "class_map_conflict_invalid:0"),
        ([{"id": 0, "card_id": "a", "asset_status": ""}], {},
         "class_map_asset_status_invalid:0"),
        (None, {"version": -1}, "class_map_version_invalid"),
        (None, {"task": None}, "class_map_task_invalid"),
    ],
)
def test_load_card_class_map_rejects_invalid(
        tmp_path: Path, classes: list[dict] | None, overrides: dict,
        marker: str) -> None:
    _write_json(tmp_path / "knowledge" / "card_classes.json",
                _card_map(classes, **overrides))
    class_map, error = load_card_class_map(tmp_path)
    assert class_map is None
    assert marker in error


# ---------------------------------------------------------------- trusted cards

def test_trusted_card_count_missing_file_is_zero(tmp_path: Path) -> None:
    assert load_trusted_card_count(tmp_path) == 0
    data = {
        "schema": "kards.trusted-cards", "version": 1,
        "entries": [
            {"card_id": "t34_1942",
             "appearance": {"hand_full": True, "zoom_full": True, "board_crop": False},
             "evidence": {"validated_at": "2026-10-01"}},
            {"card_id": "panzer_iiij_late",
             "appearance": {"hand_full": True, "zoom_full": True, "board_crop": True},
             "evidence": {}},
        ],
    }
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "trusted_cards.json").write_text(
        json.dumps(data), encoding="utf-8")
    assert load_trusted_card_count(tmp_path) == 2


# ---------------------------------------------------------------- misc

def test_model_path_constants_point_at_new_models() -> None:
    assert UI_DET_PATH.name == "ui_det.onnx"
    assert CARD_CLS_PATH.name == "card_cls.onnx"
    assert "kards_det" not in str(UI_DET_PATH) and "kards_det" not in str(CARD_CLS_PATH)


def test_postprocess_detect_rejects_output_with_fewer_class_columns() -> None:
    # (1, 14, N) 是合法布局；缺类列的 (1, 8, N) 直接空结果，不截断。
    short = np.zeros((1, 8, 3), dtype=np.float32)
    assert postprocess_detect(short, UI_CLASSES, scale=1.0, pad_x=0, pad_y=0) == []
