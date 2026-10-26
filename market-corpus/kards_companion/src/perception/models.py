"""双 YOLO 模型加载层（ui_det 检测 + card_cls 分类，Python 3.11）。

契约来源（见 docs/contracts-yolo-v1.md，已冻结）：

* ``knowledge/models_manifest.json`` —— 模型清单（任务/输入输出/unknown 阈值）。
* ``knowledge/ui_classes.json``      —— 界面检测 10 类 v1。
* ``knowledge/card_classes.json``    —— 卡牌分类 1083 类（card_id 字典序，append-only，
  含 ``conflict`` 标记与 ``asset_status``）。

权重位于 ``models/ui_det.onnx`` 与 ``models/card_cls.onnx``（不进 git，部署时随
包）。文件缺失时进入显式 ``unavailable`` 状态，绝不抛异常；加载时校验 ONNX
输出通道/类别数与 class_map 的 ``task``/类别数一致，不匹配则拒绝加载并把诊断
暴露给状态面的 ``models`` 段。旧七类检测权重不再被任何路径加载。
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .detector import Detection, letterbox, nms

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELATIVE = "knowledge/models_manifest.json"
UI_CLASSES_RELATIVE = "knowledge/ui_classes.json"
CARD_CLASSES_RELATIVE = "knowledge/card_classes.json"
TRUSTED_CARDS_RELATIVE = "knowledge/trusted_cards.json"

# 双模型常量：旧七类检测权重加载点已删除，禁止再引用。
UI_DET_PATH = PLUGIN_ROOT / "models" / "ui_det.onnx"
CARD_CLS_PATH = PLUGIN_ROOT / "models" / "card_cls.onnx"

STATE_LOADED = "loaded"
STATE_UNAVAILABLE = "unavailable"   # 文件缺失或 onnxruntime 不可用
STATE_REJECTED = "rejected"         # 契约校验失败（通道数/任务/sha256）
STATE_ERROR = "error"               # 推理期异常（进入不可用，保留诊断）

_UNKNOWN_CARD_ID = "unknown"
_CARD_INPUT_SIZE = 224
_UI_INPUT_SIZE = 640
_UI_OUTPUT_ANCHORS = 8400
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

UI_DET_FILE = "models/ui_det.onnx"
CARD_CLS_FILE = "models/card_cls.onnx"
UI_CLASS_MAP_FILE = "knowledge/ui_classes.json"
CARD_CLASS_MAP_FILE = "knowledge/card_classes.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    """读取契约 JSON；缺失/损坏返回 None，由调用方生成诊断。"""
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


# ---------------------------------------------------------------- 契约文件

@dataclass(frozen=True)
class UiClassMap:
    """ui_classes.json 的运行时视图。"""

    version: int
    task: str
    names: tuple[str, ...]

    @property
    def num_classes(self) -> int:
        return len(self.names)


@dataclass(frozen=True)
class CardClassInfo:
    id: int
    card_id: str
    name: str
    conflict: bool = False
    asset_status: str = "ok"


@dataclass(frozen=True)
class CardClassMap:
    """card_classes.json 的运行时视图（1083 类，id 即输出通道下标）。"""

    version: int
    task: str
    classes: tuple[CardClassInfo, ...]

    @property
    def num_classes(self) -> int:
        return len(self.classes)

    def by_id(self, index: int) -> CardClassInfo | None:
        if 0 <= index < len(self.classes):
            return self.classes[index]
        return None


def _positive_int(value: Any) -> bool:
    """严格正整数：bool 是 int 子类，必须排除。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def load_ui_class_map(root: Path | None = None) -> tuple[UiClassMap | None, str]:
    """返回 (class_map, 诊断)。诊断为空串表示加载成功。

    硬校验：version 为正整数、task 非空、每项 ``id`` 为整数且等于数组下标、
    ``name`` 非空且全局唯一。任何一项不合规都返回 None + 明确诊断，绝不猜测
    缺失 id 或静默接受重复名。
    """
    base = Path(root) if root is not None else PLUGIN_ROOT
    data = _read_json(base / UI_CLASSES_RELATIVE)
    if data is None:
        return None, f"class_map_missing:{UI_CLASSES_RELATIVE}"
    raw = data.get("classes")
    if not isinstance(raw, list) or not raw:
        return None, "class_map_empty:ui_classes"
    version = data.get("version")
    if not _positive_int(version):
        return None, "class_map_version_invalid:ui_classes"
    task = data.get("task")
    if not isinstance(task, str) or not task.strip():
        return None, "class_map_task_invalid:ui_classes"
    names: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"class_map_bad_entry:{index}"
        item_id = item.get("id")
        if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id != index:
            return None, f"class_map_id_mismatch:{index}"
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, f"class_map_bad_entry:{index}"
        name = name.strip()
        if name in seen:
            return None, f"class_map_duplicate_name:{name}"
        seen.add(name)
        names.append(name)
    return UiClassMap(
        version=int(version),
        task=task.strip().lower(),
        names=tuple(names),
    ), ""


def load_card_class_map(root: Path | None = None) -> tuple[CardClassMap | None, str]:
    """返回 (class_map, 诊断)。类别 id 必须与数组下标一一对应。

    硬校验：version 为正整数；``id`` 必须是真实 int（拒绝 bool / 数字字符串），
    且等于下标；``card_id`` 非空且唯一；``conflict`` 必须是 bool、
    ``asset_status`` 是非空字符串。卡名允许重复（美术复用），不作为唯一键。
    """
    base = Path(root) if root is not None else PLUGIN_ROOT
    data = _read_json(base / CARD_CLASSES_RELATIVE)
    if data is None:
        return None, f"class_map_missing:{CARD_CLASSES_RELATIVE}"
    raw = data.get("classes")
    if not isinstance(raw, list) or not raw:
        return None, "class_map_empty:card_classes"
    version = data.get("version")
    if not _positive_int(version):
        return None, "class_map_version_invalid:card_classes"
    task = data.get("task")
    if not isinstance(task, str) or not task.strip():
        return None, "class_map_task_invalid:card_classes"
    classes: list[CardClassInfo] = []
    seen_cards: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"class_map_bad_entry:{index}"
        item_id = item.get("id")
        if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id != index:
            return None, f"class_map_id_mismatch:{index}"
        card_id = item.get("card_id")
        if not isinstance(card_id, str) or not card_id.strip():
            return None, f"class_map_bad_entry:{index}"
        card_id = card_id.strip()
        if card_id in seen_cards:
            return None, f"class_map_duplicate_card_id:{card_id}"
        seen_cards.add(card_id)
        conflict = item.get("conflict", False)
        if not isinstance(conflict, bool):
            return None, f"class_map_conflict_invalid:{index}"
        asset_status = item.get("asset_status", "ok")
        if not isinstance(asset_status, str) or not asset_status.strip():
            return None, f"class_map_asset_status_invalid:{index}"
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = card_id
        classes.append(CardClassInfo(
            id=index,
            card_id=card_id,
            name=name.strip(),
            conflict=conflict,
            asset_status=asset_status.strip(),
        ))
    return CardClassMap(
        version=int(version),
        task=task.strip().lower(),
        classes=tuple(classes),
    ), ""


def load_model_manifest(root: Path | None = None) -> dict[str, Any]:
    """读取模型清单；缺失时返回空 dict（诊断由各加载器给出）。"""
    base = Path(root) if root is not None else PLUGIN_ROOT
    data = _read_json(base / MANIFEST_RELATIVE)
    return dict(data) if data else {}


def _manifest_entry(manifest: dict[str, Any], role: str) -> dict[str, Any]:
    for item in manifest.get("models") or ():
        if isinstance(item, dict) and item.get("role") == role:
            return dict(item)
    return {}


def load_trusted_card_count(root: Path | None = None) -> int:
    """knowledge/trusted_cards.json 可能不存在；不存在则 0。"""
    return len(load_trusted_cards(root))


def load_trusted_cards(root: Path | None = None) -> dict[str, set[str]]:
    """可信卡白名单：card_id -> 允许的外观集合（hand_full/zoom_full/board_crop）。

    文件缺失、损坏或条目非法时返回空表——空白名单意味着任何卡都只能输出
    unknown（mission 规则：只有真实画面验证过的类别才允许上线）。
    """
    base = Path(root) if root is not None else PLUGIN_ROOT
    data = _read_json(base / TRUSTED_CARDS_RELATIVE)
    if not data:
        return {}
    entries = data.get("entries")
    if not isinstance(entries, list):
        return {}
    trusted: dict[str, set[str]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        card_id, appearance = item.get("card_id"), item.get("appearance")
        if not isinstance(card_id, str) or not card_id:
            continue
        allowed = set()
        if isinstance(appearance, dict):
            allowed = {key for key, value in appearance.items()
                       if isinstance(value, bool) and value}
        if allowed:
            trusted[card_id] = allowed
    return trusted


# ---------------------------------------------------------------- 会话探针

def _static_shape(node: Any, rank: int) -> list[int] | None:
    """读取静态张量形状；非 int/维度数不符时返回 None（拒绝动态猜测）。"""
    try:
        shape = list(node.shape)
    except Exception:
        return None
    if len(shape) != rank:
        return None
    out: list[int] = []
    for dim in shape:
        if not isinstance(dim, int) or isinstance(dim, bool):
            return None
        out.append(int(dim))
    return out


def _is_float32_node(node: Any) -> bool:
    """ONNX 输入类型必须是 float32；替身无 type 属性时视为不可验证但不误拒。"""
    type_name = getattr(node, "type", None)
    if not type_name:
        return True
    normalized = str(type_name).replace(" ", "").lower()
    return normalized in {"tensor(float)", "tensor(float32)", "float32"}


def _int_list(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    out: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            return None
        out.append(int(item))
    return out


def _same_relpath(value: Any, expected: str) -> bool:
    return (isinstance(value, str)
            and value.strip().replace("\\", "/") == expected)


def _manifest_contract_error(
        entry: dict[str, Any], *, role: str, expected_task: str,
        expected_file: str, expected_class_map: str,
        expected_num_classes: int, expected_map_version: int,
        base: Path, model_path: Path) -> str:
    """清单条目与实际类别表/模型文件的一致性硬校验。"""
    if not entry:
        return f"manifest_missing:{role}"
    task = entry.get("task")
    if not isinstance(task, str) or task.strip().lower() != expected_task:
        return f"manifest_task_mismatch:{task!r},expected={expected_task}"
    if not _same_relpath(entry.get("file"), expected_file):
        return (f"manifest_file_mismatch:{entry.get('file')!r},"
                f"expected={expected_file}")
    declared_path = (base / expected_file).resolve()
    if Path(model_path).resolve() != declared_path:
        return f"model_path_mismatch:{model_path},expected={declared_path}"
    if not _same_relpath(entry.get("class_map"), expected_class_map):
        return (f"manifest_class_map_mismatch:{entry.get('class_map')!r},"
                f"expected={expected_class_map}")
    num_classes = entry.get("num_classes")
    if not _positive_int(num_classes) or num_classes != expected_num_classes:
        return (f"manifest_num_classes={num_classes!r},"
                f"expected={expected_num_classes}")
    map_version = entry.get("class_map_version")
    if not _positive_int(map_version) or map_version != expected_map_version:
        return (f"manifest_class_map_version={map_version!r},"
                f"expected={expected_map_version}")
    digest = entry.get("sha256")
    if not isinstance(digest, str) or not _SHA256_RE.match(digest.strip()):
        return "manifest_sha256_invalid"
    return ""


def _manifest_tensor_error(entry: dict[str, Any], *, expected_input: list[int],
                           expected_output: list[int]) -> str:
    """清单 input/output 形状与 dtype 必须与实际导出一致。"""
    spec = entry.get("input")
    if not isinstance(spec, dict):
        return "manifest_input_missing"
    shape = _int_list(spec.get("shape"))
    if shape != expected_input:
        return f"manifest_input_shape={shape},expected={expected_input}"
    dtype = spec.get("dtype")
    if (not isinstance(dtype, str)
            or dtype.strip().lower() not in ("float32", "tensor(float)")):
        return f"manifest_input_dtype={dtype!r},expected=float32"
    out = entry.get("output")
    if not isinstance(out, dict):
        return "manifest_output_missing"
    out_shape = _int_list(out.get("shape"))
    if out_shape != expected_output:
        return f"manifest_output_shape={out_shape},expected={expected_output}"
    return ""


def _session_input_error(session: Any, *, size: int) -> str:
    """输入必须是静态 NCHW、batch=1、3 通道、size×size、float32。"""
    try:
        node = session.get_inputs()[0]
    except Exception as exc:
        return f"session_invalid:{type(exc).__name__}"
    expected = [1, 3, size, size]
    shape = _static_shape(node, 4)
    if shape != expected:
        return f"input_shape={shape},expected={expected}"
    if not _is_float32_node(node):
        return (f"input_dtype={getattr(node, 'type', None)!r},"
                "expected=float32")
    return ""


def _session_output_error(session: Any, expected: list[int]) -> str:
    try:
        node = session.get_outputs()[0]
    except Exception as exc:
        return f"session_invalid:{type(exc).__name__}"
    shape = _static_shape(node, len(expected))
    if shape != expected:
        return f"output_shape={shape},expected={expected}"
    return ""


@dataclass(frozen=True)
class CapabilityGate:
    """manifest capabilities 的硬门结果（loaded != 可用类别）。"""

    declared: tuple[str, ...]
    supported: tuple[str, ...]
    unsupported: tuple[str, ...]
    state: str          # manifest / empty / missing / invalid / rejected
    diagnostics: str

    @property
    def open(self) -> bool:
        return self.state in ("manifest", "empty", "missing")


def _capability_gate(entry: dict[str, Any],
                     names: tuple[str, ...]) -> CapabilityGate:
    """严格解析 capabilities；未知类别名直接拒绝，绝不静默忽略。"""
    if "capabilities" not in entry:
        return CapabilityGate((), (), names, "missing",
                              "capabilities_missing:fail_closed")
    raw = entry.get("capabilities")
    if (not isinstance(raw, (list, tuple))
            or any(not isinstance(item, str) for item in raw)):
        return CapabilityGate((), (), names, "invalid",
                              "capabilities_invalid:fail_closed")
    declared: list[str] = []
    for item in raw:
        value = item.strip()
        if value and value not in declared:
            declared.append(value)
    known = set(names)
    unknown = [item for item in declared if item not in known]
    if unknown:
        return CapabilityGate(
            tuple(declared), (), names, "rejected",
            "capabilities_unknown:" + ",".join(unknown)
            + ",known=" + ",".join(names))
    if not declared:
        return CapabilityGate((), (), names, "empty",
                              "capabilities_empty:all_classes_suppressed")
    declared_set = set(declared)
    supported = tuple(name for name in names if name in declared_set)
    unsupported = tuple(name for name in names if name not in declared_set)
    return CapabilityGate(tuple(declared), supported, unsupported,
                          "manifest", "")


def _make_session(onnx_path: Path) -> Any:
    import onnxruntime as ort  # 可选依赖；宿主仅 CPU

    return ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])


# ---------------------------------------------------------------- 界面检测

class UiDetector:
    """models/ui_det.onnx 包装：10 类检测 + 契约校验。

    状态机：``loaded`` 可推理；``unavailable`` 文件缺失或 onnxruntime 不可用；
    ``rejected`` 输出通道数 != 4+num_classes 或任务不是 detect（含 sha256 不匹配）。
    后处理沿用 detector.letterbox/nms（conf>=0.30、按类 NMS iou=0.45），类别表
    由 ui_classes.json 注入，不存在任何按旧七类截断的路径。
    """

    CONF_THRESHOLD = 0.30
    IOU_NMS = 0.45
    EXPECTED_TASK = "detect"

    def __init__(self, onnx_path: str | Path | None = None, *,
                 root: Path | None = None,
                 session: Any = None) -> None:
        self._root = Path(root) if root is not None else PLUGIN_ROOT
        self.path = Path(onnx_path) if onnx_path is not None else UI_DET_PATH
        self.state = STATE_UNAVAILABLE
        self.diagnostics = ""
        self.sha256 = ""
        self.last_ms = 0.0
        self.last_suppressed = 0
        self.suppressed_total = 0
        self._session: Any = None
        self._input_name = ""
        self._imgsz = _UI_INPUT_SIZE

        class_map, error = load_ui_class_map(root)
        manifest = load_model_manifest(root)
        entry = _manifest_entry(manifest, "ui_detect")
        self.class_map = class_map
        self.manifest_entry = entry
        self.class_map_version = class_map.version if class_map else 0
        self.capability_gate = (
            _capability_gate(entry, class_map.names) if class_map is not None
            else CapabilityGate((), (), (), "unavailable",
                                "capabilities_unavailable:no_class_map"))
        self._supported_names = frozenset(self.capability_gate.supported)
        if class_map is None:
            self.diagnostics = error
            return

        if not self.path.is_file():
            self.diagnostics = f"model_missing:{self.path.name}"
            return
        try:
            self._session = _make_session(self.path) if session is None else session
        except Exception as exc:  # onnxruntime 缺失或文件损坏
            self.diagnostics = f"runtime_unavailable:{type(exc).__name__}"
            return

        if not self._validate(class_map, entry):
            self._session = None
            return
        self.state = STATE_LOADED
        # missing/empty capabilities 仍保持 loaded，但 supported 为空且诊断可见；
        # loaded 只代表权重可用，绝不等于识别能力已验收。
        self.diagnostics = self.capability_gate.diagnostics

    # ------------------------------------------------------------- 校验

    def _validate(self, class_map: UiClassMap, entry: dict[str, Any]) -> bool:
        error = _manifest_contract_error(
            entry, role="ui_detect", expected_task=self.EXPECTED_TASK,
            expected_file=UI_DET_FILE, expected_class_map=UI_CLASS_MAP_FILE,
            expected_num_classes=class_map.num_classes,
            expected_map_version=class_map.version,
            base=self._root, model_path=self.path)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        if class_map.task != self.EXPECTED_TASK:
            self.state = STATE_REJECTED
            self.diagnostics = f"task_mismatch:class_map={class_map.task}"
            return False
        expected_output = [1, 4 + class_map.num_classes, _UI_OUTPUT_ANCHORS]
        error = _manifest_tensor_error(
            entry,
            expected_input=[1, 3, _UI_INPUT_SIZE, _UI_INPUT_SIZE],
            expected_output=expected_output)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        error = _session_input_error(self._session, size=_UI_INPUT_SIZE)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        error = _session_output_error(self._session, expected_output)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        self.sha256 = _file_sha256(self.path)
        declared = entry.get("sha256")
        if not self.sha256 or self.sha256 != str(declared).strip().lower():
            self.state = STATE_REJECTED
            self.diagnostics = "sha256_mismatch"
            return False
        if self.capability_gate.state in ("invalid", "rejected"):
            self.state = STATE_REJECTED
            self.diagnostics = self.capability_gate.diagnostics
            return False
        try:
            self._input_name = self._session.get_inputs()[0].name
        except Exception as exc:
            self.state = STATE_ERROR
            self.diagnostics = f"session_invalid:{type(exc).__name__}"
            return False
        return True

    # ------------------------------------------------------------- 推理

    @property
    def available(self) -> bool:
        return self.state == STATE_LOADED

    def detect(self, image: Image.Image) -> list[Detection] | None:
        """返回 capability 硬门后的检测列表。

        ``None`` = 模型不可用/推理失败（无效帧）；``[]`` = 模型真实运行但
        没有通过 capability 白名单的检测（有效空结果）。被压制的框只累计到
        ``suppressed_detections`` 供诊断，绝不进入场景/跟踪/裁图路径。
        """
        if not self.available or self.class_map is None:
            return None
        started = time.perf_counter()
        try:
            blob, scale, pad_x, pad_y = letterbox(image, self._imgsz)
            raw = self._session.run(None, {self._input_name: blob})[0]
        except Exception as exc:
            self.state = STATE_ERROR
            self.diagnostics = f"inference_failed:{type(exc).__name__}"
            self._session = None
            return None
        self.last_ms = (time.perf_counter() - started) * 1000.0
        detections = postprocess_detect(
            np.asarray(raw), self.class_map.names,
            scale=scale, pad_x=pad_x, pad_y=pad_y,
            conf_threshold=self.CONF_THRESHOLD, iou_threshold=self.IOU_NMS)
        supported: list[Detection] = []
        suppressed = 0
        for detection in detections:
            if detection.name in self._supported_names:
                supported.append(detection)
            else:
                suppressed += 1
        self.last_suppressed = suppressed
        self.suppressed_total += suppressed
        return supported

    def status(self) -> dict[str, Any]:
        return {
            "role": "ui_detect",
            "state": self.state,
            "loaded": self.available,
            "file": str(self.path),
            "sha256": self.sha256 or None,
            "nc": self.class_map.num_classes if self.class_map else None,
            "task": self.EXPECTED_TASK,
            "class_map_version": self.class_map_version or None,
            "capabilities": list(self.capability_gate.declared),
            "capability_state": self.capability_gate.state,
            "capability_diagnostics": self.capability_gate.diagnostics,
            "supported_classes": list(self.capability_gate.supported),
            "unsupported_classes": list(self.capability_gate.unsupported),
            "supported_class_count": len(self.capability_gate.supported),
            "unsupported_class_count": len(self.capability_gate.unsupported),
            "suppressed_detections": self.last_suppressed,
            "suppressed_detections_total": self.suppressed_total,
            "last_ms": round(self.last_ms, 3),
            "diagnostics": self.diagnostics,
        }


def postprocess_detect(out: np.ndarray, class_names: tuple[str, ...], *,
                       scale: float, pad_x: int, pad_y: int,
                       conf_threshold: float = 0.30,
                       iou_threshold: float = 0.45) -> list[Detection]:
    """ultralytics 原始检测张量 (1, 4+n_cls, N) → 原图坐标 Detection 列表。

    类别表完全来自入参（ui_classes.json 注入）；维度不足 4+n_cls 时返回空，
    绝不截断前若干列或按其他类别表解读。
    """
    if out.ndim == 3:
        out = out[0]
    if out.ndim != 2:
        return []
    expected = 4 + len(class_names)
    if out.shape[0] == expected and out.shape[1] != expected:
        out = out.T
    if out.shape[1] < expected:
        return []
    boxes_xywh = out[:, :4]
    class_scores = out[:, 4:4 + len(class_names)]
    confs = class_scores.max(axis=1)
    cls_ids = class_scores.argmax(axis=1)
    mask = confs >= conf_threshold
    detections: list[Detection] = []
    for i in np.nonzero(mask)[0]:
        cx, cy, w, h = boxes_xywh[i]
        x1 = (cx - w / 2 - pad_x) / scale
        y1 = (cy - h / 2 - pad_y) / scale
        x2 = (cx + w / 2 - pad_x) / scale
        y2 = (cy + h / 2 - pad_y) / scale
        cid = int(cls_ids[i])
        if cid >= len(class_names):
            continue
        detections.append(Detection(
            cid, class_names[cid], float(confs[i]), x1, y1, x2, y2))
    return nms(detections, threshold=iou_threshold)


# ---------------------------------------------------------------- 卡牌分类

@dataclass(frozen=True)
class CardClassification:
    """一次裁图分类结果；``card_id == "unknown"`` 表示低于可信门槛。"""

    card_id: str
    name: str
    conf: float
    margin: float
    rejected: bool = False          # True = 双阈值/conflict/白名单策略强制 unknown
    raw_card_id: str = ""           # 策略前的 top1（仅诊断，不可对外当身份）
    reason: str = ""                # blocked_top / below_threshold / not_trusted / size_mismatch

    @property
    def known(self) -> bool:
        return self.card_id != _UNKNOWN_CARD_ID


class CardClassifier:
    """models/card_cls.onnx 包装：1083 类分类 + unknown 双阈值策略。

    预处理与契约一致：短边等比缩放 224（双线性）→ 中心裁剪 224 → RGB/255；
    ImageNet 归一化与 softmax 已内嵌导出图。若**原始 top1** 是 conflict 或
    asset_status!=ok 的类别，直接输出 unknown，绝不让次高类别顶替成高置信
    身份；margin 使用原始 top1-top2，不对分数做重归一化。最终 conf<min_conf
    或 margin<min_margin → card_id=unknown。

    当前决策是**单帧**判定（``frames_to_confirm=1``）；manifest 里的
    ``frames_to_confirm=2`` 只是声明，本版未实现跨帧跟踪，status 明确标注，
    不得把它当成已有多帧确认。
    """

    EXPECTED_TASK = "classify"
    DEFAULT_MIN_CONF = 0.55
    DEFAULT_MIN_MARGIN = 0.15
    FRAMES_TO_CONFIRM = 1

    def __init__(self, onnx_path: str | Path | None = None, *,
                 root: Path | None = None,
                 session: Any = None) -> None:
        self._root = Path(root) if root is not None else PLUGIN_ROOT
        self.path = Path(onnx_path) if onnx_path is not None else CARD_CLS_PATH
        self.state = STATE_UNAVAILABLE
        self.diagnostics = ""
        self.sha256 = ""
        self.last_ms = 0.0
        self._session: Any = None
        self._input_name = ""
        self.min_conf = self.DEFAULT_MIN_CONF
        self.min_margin = self.DEFAULT_MIN_MARGIN
        self.manifest_frames_to_confirm: int | None = None
        # 可信卡白名单：不在白名单内（或该外观未验证）的卡一律输出 unknown。
        # 空白名单 = 全部 unknown；这是"只有真实画面验证过的类别才上线"的硬门。
        self.trusted: dict[str, set[str]] = load_trusted_cards(root)
        self.trusted_count = len(self.trusted)

        class_map, error = load_card_class_map(root)
        manifest = load_model_manifest(root)
        entry = _manifest_entry(manifest, "card_classify")
        self.class_map = class_map
        self.manifest_entry = entry
        self.class_map_version = class_map.version if class_map else 0
        policy = entry.get("unknown_policy") if isinstance(entry.get("unknown_policy"), dict) else {}
        try:
            self.min_conf = float(policy.get("min_conf", self.DEFAULT_MIN_CONF))
            self.min_margin = float(policy.get("min_margin", self.DEFAULT_MIN_MARGIN))
        except (TypeError, ValueError):
            self.min_conf, self.min_margin = self.DEFAULT_MIN_CONF, self.DEFAULT_MIN_MARGIN
        declared_frames = policy.get("frames_to_confirm")
        self.manifest_frames_to_confirm = (
            int(declared_frames) if _positive_int(declared_frames) else None)
        if class_map is None:
            self.diagnostics = error
            return

        if not self.path.is_file():
            self.diagnostics = f"model_missing:{self.path.name}"
            return
        try:
            self._session = _make_session(self.path) if session is None else session
        except Exception as exc:
            self.diagnostics = f"runtime_unavailable:{type(exc).__name__}"
            return

        if not self._validate(class_map, entry):
            self._session = None
            return
        self.state = STATE_LOADED
        self.diagnostics = ""

    def _validate(self, class_map: CardClassMap, entry: dict[str, Any]) -> bool:
        error = _manifest_contract_error(
            entry, role="card_classify", expected_task=self.EXPECTED_TASK,
            expected_file=CARD_CLS_FILE, expected_class_map=CARD_CLASS_MAP_FILE,
            expected_num_classes=class_map.num_classes,
            expected_map_version=class_map.version,
            base=self._root, model_path=self.path)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        if class_map.task != self.EXPECTED_TASK:
            self.state = STATE_REJECTED
            self.diagnostics = f"task_mismatch:class_map={class_map.task}"
            return False
        expected_output = [1, class_map.num_classes]
        error = _manifest_tensor_error(
            entry,
            expected_input=[1, 3, _CARD_INPUT_SIZE, _CARD_INPUT_SIZE],
            expected_output=expected_output)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        error = _session_input_error(self._session, size=_CARD_INPUT_SIZE)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        error = _session_output_error(self._session, expected_output)
        if error:
            self.state = STATE_REJECTED
            self.diagnostics = error
            return False
        self.sha256 = _file_sha256(self.path)
        declared = entry.get("sha256")
        if not self.sha256 or self.sha256 != str(declared).strip().lower():
            self.state = STATE_REJECTED
            self.diagnostics = "sha256_mismatch"
            return False
        try:
            self._input_name = self._session.get_inputs()[0].name
        except Exception as exc:
            self.state = STATE_ERROR
            self.diagnostics = f"session_invalid:{type(exc).__name__}"
            return False
        return True

    @property
    def available(self) -> bool:
        return self.state == STATE_LOADED

    # ------------------------------------------------------------- 预处理

    @staticmethod
    def preprocess(crop: Image.Image) -> np.ndarray:
        """短边等比缩放 224（双线性）+ 中心裁剪 224，RGB / 255。"""
        size = _CARD_INPUT_SIZE
        image = crop.convert("RGB")
        width, height = image.size
        scale = size / min(width, height)
        new_w = max(1, int(round(width * scale)))
        new_h = max(1, int(round(height * scale)))
        resized = image.resize((new_w, new_h), Image.BILINEAR)
        left = max(0, (new_w - size) // 2)
        top = max(0, (new_h - size) // 2)
        cropped = resized.crop((left, top, left + size, top + size))
        if cropped.size != (size, size):
            cropped = cropped.resize((size, size), Image.BILINEAR)
        arr = np.asarray(cropped, dtype=np.float32).transpose(2, 0, 1) / 255.0
        return arr[None]

    # ------------------------------------------------------------- 推理

    def classify(self, crop: Image.Image,
                 appearance: str | None = None) -> CardClassification | None:
        """模型不可用/推理失败返回 ``None``；否则返回含 unknown 策略的结果。

        ``appearance`` 为该裁图的外观键（hand_full / zoom_full）；通过双阈值的
        结果还必须命中可信白名单（card_id 已验证且该外观已验证），否则按
        unknown 返回（``raw_card_id`` 保留诊断信息，不可对外当身份）。
        """
        if not self.available or self.class_map is None:
            return None
        started = time.perf_counter()
        try:
            blob = self.preprocess(crop)
            raw = self._session.run(None, {self._input_name: blob})[0]
        except Exception as exc:
            self.state = STATE_ERROR
            self.diagnostics = f"inference_failed:{type(exc).__name__}"
            self._session = None
            return None
        self.last_ms = (time.perf_counter() - started) * 1000.0
        probs = np.asarray(raw, dtype=np.float32).reshape(-1)
        result = self.decide(probs)
        if result.known and not self._trusted_for(result.card_id, appearance):
            return CardClassification(
                _UNKNOWN_CARD_ID, "", result.conf, result.margin,
                rejected=True, raw_card_id=result.raw_card_id,
                reason="not_trusted")
        return result

    def _trusted_for(self, card_id: str, appearance: str | None) -> bool:
        allowed = self.trusted.get(card_id)
        if not allowed:
            return False
        if appearance is None:
            return True
        return appearance in allowed

    def decide(self, probs: np.ndarray) -> CardClassification:
        """概率向量（含模型端已内嵌的 softmax）→ 门槛判定结果。

        原始 top1 若被 conflict/asset_status 屏蔽，直接 unknown；不做概率置 0
        后让次高顶替，也不重归一化分数。margin 始终取原始 top1-top2，避免把
        屏蔽类挤掉后人为放大置信度。
        """
        assert self.class_map is not None
        vector = np.asarray(probs, dtype=np.float32).reshape(-1).copy()
        if vector.size != self.class_map.num_classes:
            return CardClassification(
                _UNKNOWN_CARD_ID, "", 0.0, 0.0, rejected=True,
                raw_card_id="", reason="size_mismatch")
        order = np.argsort(-vector)
        top1 = int(order[0])
        conf = float(vector[top1])
        top2_conf = float(vector[int(order[1])]) if vector.size > 1 else 0.0
        margin = max(0.0, conf - top2_conf)
        info = self.class_map.by_id(top1)
        if info is None:
            return CardClassification(
                _UNKNOWN_CARD_ID, "", round(conf, 4), round(margin, 4),
                rejected=True, raw_card_id="", reason="size_mismatch")
        raw_card_id = info.card_id
        if info.conflict or info.asset_status != "ok":
            return CardClassification(
                _UNKNOWN_CARD_ID, "", round(conf, 4), round(margin, 4),
                rejected=True, raw_card_id=raw_card_id, reason="blocked_top")
        if not (conf >= self.min_conf and margin >= self.min_margin):
            return CardClassification(
                _UNKNOWN_CARD_ID, "", round(conf, 4), round(margin, 4),
                rejected=True, raw_card_id=raw_card_id,
                reason="below_threshold")
        return CardClassification(
            info.card_id, info.name, round(conf, 4), round(margin, 4),
            False, raw_card_id=raw_card_id)

    def status(self) -> dict[str, Any]:
        return {
            "role": "card_classify",
            "state": self.state,
            "loaded": self.available,
            "file": str(self.path),
            "sha256": self.sha256 or None,
            "nc": self.class_map.num_classes if self.class_map else None,
            "task": self.EXPECTED_TASK,
            "class_map_version": self.class_map_version or None,
            "unknown_policy": {"min_conf": self.min_conf, "min_margin": self.min_margin},
            "decision_mode": "single_frame",
            "frames_to_confirm": self.FRAMES_TO_CONFIRM,
            "manifest_frames_to_confirm": self.manifest_frames_to_confirm,
            "multi_frame_confirmation": False,
            "trusted_whitelist_applied": True,
            "trusted_card_count": self.trusted_count,
            "last_ms": round(self.last_ms, 3),
            "diagnostics": self.diagnostics,
        }


@dataclass
class ModelStatusBundle:
    """状态面 ``models`` 段的聚合视图。"""

    ui_det: dict[str, Any] = field(default_factory=dict)
    card_cls: dict[str, Any] = field(default_factory=dict)
    class_map_versions: dict[str, int] = field(default_factory=dict)
    trusted_card_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ui_det": self.ui_det,
            "card_cls": self.card_cls,
            "class_map_versions": self.class_map_versions,
            "trusted_card_count": self.trusted_card_count,
        }
