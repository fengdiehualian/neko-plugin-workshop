"""检测后处理 + 轨迹跟踪（Python 3.11；宿主 onnxruntime 仅 CPU）。

类别表不再内嵌：检测类别由 ui_classes.json（10 类 v1）在
``perception.models.UiDetector`` 加载时注入；本模块只提供与类别无关的
letterbox/NMS/轨迹逻辑，不存在任何按固定七类读取或截断输出通道的路径。
"""

import copy as _copy
import math
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from PIL import Image

from ..state.model import (
    EVIDENCE_FRAME_LIMIT,
    LANE_FRONT,
    LANE_SUPPORT,
    POSITION_HISTORY_LIMIT,
    SIDE_ENEMY,
    SIDE_MINE,
    SIDE_UNKNOWN,
    BoardUnit,
)

_IOU_NMS = 0.45
_IOU_TRACK = 0.30
_TRACK_GRACE = 2  # 单位框消失几帧内不清除（旧 HUD 补数字已移除，保留宽限避免闪烁）

# ---------------------------------------------------------------- 轨迹确认
# 已批准方案：登场 2 帧、合法移动 2 帧、缺失 3 帧只记 unit_lost 诊断。
TRACK_CONFIRM_FRAMES = 2
TRACK_MOVE_CONFIRM_FRAMES = 2
TRACK_MISS_LIMIT = 3
TRACK_HISTORY_LIMIT = POSITION_HISTORY_LIMIT
TRACK_EVIDENCE_FRAME_LIMIT = EVIDENCE_FRAME_LIMIT
# 侧别（敌我）必须来自可信的前线框；fallback ratio、低可信前线或贴线歧义
# 只能是 unknown。
TRACK_MIN_SIDE_CONFIDENCE = 0.6
TRACK_MIN_FRONTLINE_CONFIDENCE = 0.5
# 低于该置信度的框只用于“避免误判丢失”，不创建新单位、不推进确认。
TRACK_WEAK_CONFIDENCE = 0.35


@dataclass(frozen=True)
class Detection:
    cls_id: int
    name: str
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    def crop(self, image: Image.Image, pad: float = 0.0) -> Image.Image:
        w, h = image.size
        x1 = max(0, int(self.x1 - self.width() * pad))
        y1 = max(0, int(self.y1 - self.height() * pad))
        x2 = min(w, int(self.x2 + self.width() * pad))
        y2 = min(h, int(self.y2 + self.height() * pad))
        return image.crop((x1, y1, x2, y2))

    def width(self) -> float:
        return self.x2 - self.x1

    def height(self) -> float:
        return self.y2 - self.y1


@dataclass(frozen=True)
class DetectionFrame:
    """One detector observation with explicit health.

    ``detections is None`` means the model was unavailable (load/inference
    failure).  ``detections == ()`` means the model actually ran and found
    nothing.  These two cases must never be treated as the same absence
    evidence.

    ``observation_valid=False`` marks a successful but untrustworthy frame
    (capture truncated/occluded/degraded).  Such frames never advance the
    three-frame missing counter.
    """

    detections: tuple[Detection, ...] | None
    image_size: tuple[float, float] | None = None
    frame_id: int = 0
    observed_at: float = 0.0
    detector_available: bool = True
    observation_valid: bool = True
    status: str = "ok"
    # Capture-health context for absence evidence.  ``occlusion_state`` uses
    # capture.py values (clear/not_required/unknown/unverified/occluded/...).
    # Host WGC may legitimately have foreground=False; desktop backends may
    # not.  Only explicit occluded/capture-not-reading always invalidates.
    occlusion_state: str = ""
    foreground: bool | None = None
    backend: str = ""
    occluded: bool | None = None
    capture_reading: bool | None = None

    @classmethod
    def unavailable(
        cls,
        *,
        frame_id: int = 0,
        observed_at: float = 0.0,
        image_size: tuple[float, float] | None = None,
        status: str = "model_unavailable",
        occlusion_state: str = "unknown",
        foreground: bool | None = None,
        backend: str = "",
        occluded: bool | None = None,
    ) -> "DetectionFrame":
        return cls(
            detections=None,
            image_size=image_size,
            frame_id=frame_id,
            observed_at=observed_at,
            detector_available=False,
            observation_valid=False,
            status=status,
            occlusion_state=occlusion_state,
            foreground=foreground,
            backend=backend,
            occluded=occluded,
            capture_reading=False,
        )


@dataclass(frozen=True)
class UnitObservation:
    """One unit_board detection enriched with side/lane/slot/bbox."""

    det: Detection
    side: str
    lane: str
    side_confidence: float
    lane_confidence: float
    slot: int
    bbox: tuple[float, float, float, float]  # normalized xyxy, each in 0..1
    uncertain: bool = False

    def position_snapshot(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "lane": self.lane,
            "slot": int(self.slot),
            "bbox": [round(float(v), 4) for v in self.bbox],
        }


def _box_iou(a: Detection, b: Detection) -> float:
    inter_w = max(0.0, min(a.x2, b.x2) - max(a.x1, b.x1))
    inter_h = max(0.0, min(a.y2, b.y2) - max(a.y1, b.y1))
    inter = inter_w * inter_h
    union = a.width() * a.height() + b.width() * b.height() - inter
    return inter / union if union > 0 else 0.0


def _is_host_wgc(backend: Any) -> bool:
    """Host window-graphics capture can produce valid frames when backgrounded."""
    text = str(backend or "").strip().lower()
    return "wgc" in text


def _observation_gate_reason(
    *,
    occlusion_state: str | None,
    foreground: bool | None,
    backend: str | None,
    occluded: bool | None,
    capture_reading: bool | None,
) -> str:
    """Return a reason to reject an absence observation, or ``""``.

    Full occlusion / unknown foreground makes ``[]`` unusable as missing
    evidence.  Host WGC is the explicit exception: its window surface can be
    captured while the window is backgrounded/obscured, so a trusted WGC
    reading is not invalidated by ``foreground=False`` or an unknown
    occlusion probe.  An explicit ``occluded=True`` or non-reading health
    still fails closed.
    """
    if capture_reading is False:
        return "capture_not_reading"
    if occluded is True:
        return "occluded"
    state = str(occlusion_state or "").strip().lower()
    host_wgc = _is_host_wgc(backend)
    if state == "occluded":
        return "occluded"
    if not host_wgc and state in {"unknown", "unverified", "foreground_required"}:
        return "occlusion_unverified"
    if not host_wgc and foreground is False:
        return "not_foreground"
    return ""


def letterbox(image: Image.Image, size: int = 640) -> tuple[np.ndarray, float, int, int]:
    w, h = image.size
    scale = min(size / w, size / h)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = image.convert("RGB").resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    left, top = (size - new_w) // 2, (size - new_h) // 2
    canvas.paste(resized, (left, top))
    arr = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return arr[None], scale, left, top


def nms(detections: list[Detection], threshold: float = _IOU_NMS) -> list[Detection]:
    """按类别的 numpy 版非极大值抑制。"""
    if not detections:
        return []
    by_class: dict[int, list[Detection]] = {}
    for det in detections:
        by_class.setdefault(det.cls_id, []).append(det)
    kept: list[Detection] = []
    for items in by_class.values():
        boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in items], dtype=np.float32)
        scores = np.array([d.conf for d in items], dtype=np.float32)
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size:
            head = int(order[0])
            keep.append(head)
            rest = order[1:]
            if rest.size == 0:
                break
            xx1 = np.maximum(boxes[head, 0], boxes[rest, 0])
            yy1 = np.maximum(boxes[head, 1], boxes[rest, 1])
            xx2 = np.minimum(boxes[head, 2], boxes[rest, 2])
            yy2 = np.minimum(boxes[head, 3], boxes[rest, 3])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            iou = inter / (areas[head] + areas[rest] - inter + 1e-9)
            order = rest[iou <= threshold]
        kept.extend(items[i] for i in keep)
    kept.sort(key=lambda d: -d.conf)
    return kept


# 模型加载与 ultralytics 检测张量后处理见 perception/models.py 的
# UiDetector/postprocess_detect（类别表由 ui_classes.json 注入）。旧
# 七类 YoloDetector 加载点已随 OCR/录像链路一并移除。


# ---------------------------------------------------------------- 分行归属

@dataclass
class RowAssignment:
    units: list[tuple[Detection, str, str]]  # (检测框, side, lane)
    frontline_y: float
    source: str  # "model" | "fallback_ratio"


def assign_rows(dets: list[Detection], frame_h: float,
                fallback_ratio: float = 0.465) -> RowAssignment:
    """frontline 框定线；单位按中心 y 分上下半带 → 敌我；靠近线 → 前线。"""
    fronts = [d for d in dets if d.name == "frontline"]
    if fronts:
        line_y = float(np.mean([d.cy for d in fronts]))
        source = "model"
    else:
        line_y = frame_h * fallback_ratio
        source = "fallback_ratio"
    band = frame_h * 0.06
    rows: list[tuple[Detection, str, str]] = []
    for det in dets:
        if det.name != "unit_board":
            continue
        side = "enemy" if det.cy < line_y else "mine"
        near = abs(det.cy - line_y) <= band * 1.6
        rows.append((det, side, "front" if near else "support"))
    return RowAssignment(rows, line_y, source)


# ------------------------------------------------------- 归一化 / 分行槽位

def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _image_size(value: Any) -> tuple[float, float] | None:
    """Accept ``(w, h)``, ``image.size`` or an object with width/height."""
    if value is None:
        return None
    if hasattr(value, "size"):
        try:
            value = value.size
        except Exception:
            return None
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        width = getattr(value, "width", None)
        height = getattr(value, "height", None)
        if width is None or height is None:
            return None
        value = (width, height)
    try:
        width, height = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(width) and math.isfinite(height)) or width <= 0 or height <= 0:
        return None
    return width, height


def _normalized_bbox(det: Detection, width: float, height: float
                     ) -> tuple[float, float, float, float] | None:
    """Clamp a pixel xyxy box into normalized 0..1 coordinates."""
    x1, y1, x2, y2 = det.x1, det.y1, det.x2, det.y2
    if not all(_finite(v) for v in (x1, y1, x2, y2)):
        return None
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    left = min(max(left, 0.0), width)
    right = min(max(right, 0.0), width)
    top = min(max(top, 0.0), height)
    bottom = min(max(bottom, 0.0), height)
    if right - left <= 1e-6 or bottom - top <= 1e-6:
        return None
    return (
        round(left / width, 6),
        round(top / height, 6),
        round(right / width, 6),
        round(bottom / height, 6),
    )


def _bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
    return ((float(bbox[0]) + float(bbox[2])) / 2.0,
            (float(bbox[1]) + float(bbox[3])) / 2.0)


def _bbox_iou_norm(a: Sequence[float], b: Sequence[float]) -> float:
    left = max(float(a[0]), float(b[0]))
    top = max(float(a[1]), float(b[1]))
    right = min(float(a[2]), float(b[2]))
    bottom = min(float(a[3]), float(b[3]))
    inter = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, float(a[2]) - float(a[0])) * max(0.0, float(a[3]) - float(a[1]))
    area_b = max(0.0, float(b[2]) - float(b[0])) * max(0.0, float(b[3]) - float(b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _shift_bbox(bbox: Sequence[float], dx: float, dy: float
                ) -> tuple[float, float, float, float]:
    return (
        max(0.0, min(1.0, float(bbox[0]) + dx)),
        max(0.0, min(1.0, float(bbox[1]) + dy)),
        max(0.0, min(1.0, float(bbox[2]) + dx)),
        max(0.0, min(1.0, float(bbox[3]) + dy)),
    )


def _bounded_frame_ids(values: Sequence[Any],
                       limit: int = TRACK_EVIDENCE_FRAME_LIMIT) -> list[int]:
    out: list[int] = []
    for value in values:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out[-max(1, int(limit)):]


def _position_copy(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    bbox = value.get("bbox")
    bbox_copy = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            bbox_copy = [round(float(v), 4) for v in bbox]
        except (TypeError, ValueError):
            bbox_copy = None
    slot = value.get("slot")
    try:
        slot_copy = None if slot is None else int(slot)
    except (TypeError, ValueError):
        slot_copy = None
    return {
        "side": str(value.get("side") or SIDE_UNKNOWN),
        "lane": str(value.get("lane") or LANE_SUPPORT),
        "slot": slot_copy,
        "bbox": bbox_copy,
    }


def _evidence(before: dict[str, Any] | None, after: dict[str, Any] | None,
              frame_ids: Sequence[Any], *, first_seen: int | None,
              last_seen: int | None, confirm_count: int,
              confidences: Sequence[float],
              frame_limit: int = TRACK_EVIDENCE_FRAME_LIMIT) -> dict[str, Any]:
    """Bounded structured evidence: no images, crops or OCR text."""
    frames = _bounded_frame_ids(frame_ids, frame_limit)
    conf = 0.0
    if confidences:
        try:
            conf = min(float(v) for v in confidences)
        except (TypeError, ValueError):
            conf = 0.0
    return {
        "before": _position_copy(before),
        "after": _position_copy(after),
        "frame_ids": frames,
        "first_seen": None if first_seen is None else int(first_seen),
        "last_seen": None if last_seen is None else int(last_seen),
        "confirm_count": max(0, int(confirm_count)),
        "confidence": round(max(0.0, min(1.0, conf)), 3),
    }


def build_unit_observations(
    detections: Sequence[Detection],
    image_size: Any,
    *,
    fallback_ratio: float = 0.465,
    min_side_confidence: float = TRACK_MIN_SIDE_CONFIDENCE,
) -> list[UnitObservation]:
    """Detections -> side/lane/slot + normalized bbox.

    Only ``unit_board`` detections become units; ``card_hand``, ``hq`` and
    other boxes are never converted into unit identities (and no OCR text is
    used).  Side is only accepted from a model frontline box with a clear
    side.  A fallback-ratio line or a box sitting on the line is recorded as
    ``SIDE_UNKNOWN``; temporal smoothing is deliberately not allowed to guess
    it.  Slots are the x-order rank inside each (side, lane) group.
    """
    size = _image_size(image_size)
    if size is None:
        return []
    width, height = size
    dets = [d for d in detections if isinstance(d, Detection)]
    assignment = assign_rows(dets, height, fallback_ratio)
    front_conf = min(
        (float(d.conf) for d in dets if d.name == "frontline"),
        default=0.0,
    )

    provisional: list[dict[str, Any]] = []
    for det, side, lane in assignment.units:
        bbox = _normalized_bbox(det, width, height)
        if bbox is None:
            continue
        if (assignment.source == "model"
                and front_conf >= TRACK_MIN_FRONTLINE_CONFIDENCE):
            # A box exactly on the line is geometrically ambiguous.
            side_confidence = 0.9
            if abs(float(det.cy) - assignment.frontline_y) <= height * 0.004:
                side_confidence = 0.3
        else:
            # Fallback ratio / weak frontline is a layout guess, not an
            # observed side.
            side_confidence = 0.45
        side_out = side if side_confidence >= min_side_confidence else SIDE_UNKNOWN
        lane_confidence = 0.9 if lane == LANE_SUPPORT else 0.85
        provisional.append({
            "det": det,
            "side": side_out,
            "lane": lane,
            "side_confidence": side_confidence,
            "lane_confidence": lane_confidence,
            "bbox": bbox,
            "uncertain": float(det.conf) < TRACK_WEAK_CONFIDENCE,
        })

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in provisional:
        groups.setdefault((item["side"], item["lane"]), []).append(item)
    for items in groups.values():
        items.sort(key=lambda item: (float(item["det"].cx), float(item["det"].cy)))
        for slot, item in enumerate(items):
            item["slot"] = slot

    observations = [
        UnitObservation(
            det=item["det"],
            side=item["side"],
            lane=item["lane"],
            side_confidence=item["side_confidence"],
            lane_confidence=item["lane_confidence"],
            slot=int(item["slot"]),
            bbox=item["bbox"],
            uncertain=bool(item["uncertain"]),
        )
        for item in provisional
    ]
    side_rank = {SIDE_MINE: 0, SIDE_ENEMY: 1, SIDE_UNKNOWN: 2}
    lane_rank = {LANE_SUPPORT: 0, LANE_FRONT: 1}
    observations.sort(key=lambda obs: (
        side_rank.get(obs.side, 3),
        lane_rank.get(obs.lane, 2),
        int(obs.slot),
        float(obs.det.cx),
    ))
    return observations


# ---------------------------------------------------------------- IoU 跟踪

@dataclass
class TrackedUnit:
    instance_id: int
    det: Detection
    side: str
    lane: str
    misses: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Track:
    """Internal trajectory state; never exposed directly."""

    instance_id: int
    side: str
    lane: str
    slot: int
    bbox: tuple[float, float, float, float]
    det_conf: float
    side_conf: float
    lane_conf: float
    first_seen_frame: int
    last_seen_frame: int
    first_seen_at: float
    last_seen_at: float
    confirm_count: int = 1
    confirmed: bool = False
    miss_count: int = 0
    weak_count: int = 0
    velocity: tuple[float, float] = (0.0, 0.0)
    history: list[dict[str, Any]] = field(default_factory=list)
    enter_evidence: dict[str, Any] | None = None
    move_evidence: dict[str, Any] | None = None
    pending_move: dict[str, Any] | None = None
    lane_bbox: tuple[float, float, float, float] | None = None
    observed: bool = True


class UnitTracker:
    """相邻帧贪心 IoU 匹配，给 unit_board 分配稳定 instance_id。

    旧接口 :meth:`update` 保持原样，供现有 ``assign_rows`` 调用方使用。

    新接口 :meth:`update_observations` 实现已批准的轨迹事件方案：

    * ``detections is None``（模型缺失/推理失败）与 ``[]``（真实空检测）严格
      区分，前者不推进丢失计数；
    * 单位出现连续 2 帧确认，合法跨 lane 移动连续 2 帧确认；
    * 缺失 3 个可信观测帧只在 diagnostics/ledger 记 ``unit_lost``，不生成
      任何“阵亡”事件；
    * 侧别只接受可信前线框；fallback/贴线歧义记 ``SIDE_UNKNOWN``，不做
      跨帧平滑猜敌我；
    * 每个单位保留最近 20 条 ``position_history``，证据只有
      before/after/frame_ids/first_seen/last_seen/confirm_count/confidence，
      不含图片、裁剪或 OCR 原文。

    Main integration contract (``_apply_detector`` call site)::

        units, diagnostics = unit_tracker.update_observations(
            detections_or_none,
            image.size,
            frame_id=frame_id_or_seq,          # > 0, unique per capture frame
            observed_at=result.captured_at,   # or state.detected_at
            detector_available=detections_or_none is not None,
            occlusion_state=occlusion_state,   # capture.py value
            foreground=health.foreground,
            backend=health.backend,
            occluded=health.occluded,
            capture_reading=health.reading,
        )
        state.units = units                    # always, even observed=False
        state.unit_observation_valid = True    # only on a unit-due tick
        state.unit_observation_frame = frame_id_or_seq

    On non-unit-due ticks keep the last verified units but set
    ``state.unit_observation_valid = False``; MatchTracker then updates
    HQ/turn/order without advancing unit entry/move/miss gates.  On model or
    inference failure pass ``None`` (not ``[]``): the returned units are the
    last known ones with ``observed=False`` and no miss is counted.  Full
    occlusion / non-reading / non-WGC background frames also fail closed, while
    a trusted host-WGC reading may keep ``foreground=False``.  ``frame_id <= 0``
    is treated as missing and replaced with a unique generated id.
    On capture pause/resume call :meth:`rebase` together with
    ``MatchTracker.rebase``; on match/session reset call
    :meth:`reset_match`/:meth:`reset_session` together with the corresponding
    MatchTracker reset.
    """

    def __init__(
        self,
        *,
        confirm_frames: int = TRACK_CONFIRM_FRAMES,
        move_confirm_frames: int = TRACK_MOVE_CONFIRM_FRAMES,
        miss_limit: int = TRACK_MISS_LIMIT,
        history_limit: int = TRACK_HISTORY_LIMIT,
        evidence_frame_limit: int = TRACK_EVIDENCE_FRAME_LIMIT,
    ) -> None:
        self._next_id = 1
        self._alive: dict[int, TrackedUnit] = {}
        self._frame_ts = 0.0

        self._confirm_frames = max(1, int(confirm_frames))
        self._move_confirm_frames = max(1, int(move_confirm_frames))
        self._miss_limit = max(1, int(miss_limit))
        self._history_limit = max(1, int(history_limit))
        self._evidence_frame_limit = max(1, int(evidence_frame_limit))
        self._obs_alive: dict[int, _Track] = {}
        self._obs_lost: list[dict[str, Any]] = []
        self._obs_loss_keys: set[tuple[Any, ...]] = set()
        self._obs_ledger: list[dict[str, Any]] = []
        self._obs_frame_seq = 0
        self._obs_last_diagnostics: dict[str, Any] = {}
        self._obs_rebase_pending = False

    # ------------------------------------------------------------- lifecycle

    def reset(self) -> None:
        self._alive.clear()
        self._obs_alive.clear()
        self._obs_lost.clear()
        self._obs_loss_keys.clear()
        self._obs_ledger.clear()
        self._obs_last_diagnostics = {}
        self._obs_frame_seq = 0
        self._obs_rebase_pending = False
        self._next_id = 1

    def reset_session(self) -> None:
        """Full identity reset; a new session never matches old tracks."""
        self.reset()

    def reset_match(self) -> None:
        """Current-match reset keeps the same object and clears identities."""
        self.reset()

    def rebase(self) -> None:
        """Treat the next valid observation as a baseline after a pause.

        Unmatched tracks are kept (not counted missing) and returned as
        ``observed=False`` so a caller rebasing MatchTracker in the same tick
        does not announce them as freshly played.
        """
        self._obs_rebase_pending = True

    # ------------------------------------------------------- legacy interface

    def update(self, assignment: RowAssignment, *, now: float | None = None) -> list[TrackedUnit]:
        self._frame_ts = time.monotonic() if now is None else float(now)
        candidates = [TrackedUnit(0, det, side, lane) for det, side, lane in assignment.units]
        pairs: list[tuple[float, int, int]] = []
        alive_ids = list(self._alive)
        for ai, alive_key in enumerate(alive_ids):
            old = self._alive[alive_key]
            for ci, cand in enumerate(candidates):
                iou = _box_iou(old.det, cand.det)
                if iou >= _IOU_TRACK:
                    pairs.append((iou, ai, ci))
        pairs.sort(key=lambda p: -p[0])
        used_old: set[int] = set()
        used_new: set[int] = set()
        links: dict[int, int] = {}  # alive index -> candidate index
        for _iou, ai, ci in pairs:
            if ai in used_old or ci in used_new:
                continue
            used_old.add(ai)
            used_new.add(ci)
            links[ai] = ci

        result: list[TrackedUnit] = []
        for ai, old_key in enumerate(alive_ids):
            old = self._alive[old_key]
            if ai in links:
                cand = candidates[links[ai]]
                cand.instance_id = old.instance_id
                cand.meta = old.meta
                self._alive[old_key] = cand
                result.append(cand)
            else:
                old.misses += 1
                if old.misses <= _TRACK_GRACE:
                    result.append(old)
                else:
                    del self._alive[old_key]
        for ci, cand in enumerate(candidates):
            if ci in used_new:
                continue
            cand.instance_id = self._next_id
            self._next_id += 1
            self._alive[cand.instance_id] = cand
            result.append(cand)
        return result

    # ---------------------------------------------------- trajectory interface

    @property
    def last_diagnostics(self) -> dict[str, Any]:
        return _copy.deepcopy(self._obs_last_diagnostics)

    def recent_ledger(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent structured enter/move/lost records; never images/OCR."""
        count = max(1, int(limit))
        return _copy.deepcopy(self._obs_ledger[-count:])

    def recent_losses(self, limit: int = 20) -> list[dict[str, Any]]:
        count = max(1, int(limit))
        return _copy.deepcopy(self._obs_lost[-count:])

    def snapshot(self) -> dict[str, Any]:
        return {
            "alive": len(self._obs_alive),
            "confirmed": sum(1 for track in self._obs_alive.values() if track.confirmed),
            "lost": len(self._obs_lost),
            "frame_id": self._obs_frame_seq,
            "ledger": self.recent_ledger(20),
        }

    def update_observations(
        self,
        detections: Sequence[Detection] | DetectionFrame | None,
        image_size: Any = None,
        frame_id: int | None = None,
        observed_at: float | None = None,
        *,
        detector_available: bool | None = None,
        observation_valid: bool | None = None,
        status: str = "",
        fallback_ratio: float = 0.465,
        occlusion_state: str | None = None,
        foreground: bool | None = None,
        backend: str = "",
        occluded: bool | None = None,
        capture_reading: bool | None = None,
    ) -> tuple[list[BoardUnit], dict[str, Any]]:
        """Consume one detector observation and return (units, diagnostics).

        Parameters are deliberately explicit so the main integration can pass
        capture/detector health without ever deciding "empty == model failed".

        Returns only units observed in a valid frame.  On an invalid /
        unavailable frame the last known units are returned unchanged (with
        ``observed=False``) so callers never feed a false empty board into
        :class:`~kards_companion.state.tracker.MatchTracker`.
        """
        frame = detections if isinstance(detections, DetectionFrame) else None
        if frame is not None:
            detections = frame.detections
            if image_size is None:
                image_size = frame.image_size
            if frame_id is None and frame.frame_id:
                frame_id = frame.frame_id
            if observed_at is None:
                observed_at = frame.observed_at
            if detector_available is None:
                detector_available = frame.detector_available
            if observation_valid is None:
                observation_valid = frame.observation_valid
            if not status:
                status = frame.status
            if occlusion_state is None and frame.occlusion_state:
                occlusion_state = frame.occlusion_state
            if foreground is None:
                foreground = frame.foreground
            if not backend:
                backend = frame.backend
            if occluded is None:
                occluded = frame.occluded
            if capture_reading is None:
                capture_reading = frame.capture_reading

        try:
            at = time.monotonic() if observed_at is None else float(observed_at)
        except (TypeError, ValueError):
            at = time.monotonic()
        size = _image_size(image_size)
        if detector_available is None:
            # None means the model did not produce an answer; an empty list is
            # a real, successful "nothing on board" observation.
            detector_available = detections is not None
        if observation_valid is None:
            observation_valid = bool(detector_available)

        if frame_id is None:
            self._obs_frame_seq += 1
            fid = self._obs_frame_seq
        else:
            try:
                candidate = int(frame_id)
            except (TypeError, ValueError):
                candidate = 0
            if candidate <= 0:
                # Frame id 0 is a "missing capture id" sentinel, not a real
                # observation.  Generate a unique monotonic id so repeated
                # zero fallbacks cannot masquerade as the same frame.
                self._obs_frame_seq += 1
                fid = self._obs_frame_seq
            else:
                fid = candidate
                self._obs_frame_seq = max(self._obs_frame_seq, fid)

        raw_detections = detections if detections is not None else ()
        if isinstance(raw_detections, Detection):
            raw_detections = (raw_detections,)
        dets = [d for d in raw_detections if isinstance(d, Detection)]

        if detections is None:
            detector_available = False
            observation_valid = False
            if status in ("", "ok"):
                status = "model_unavailable"
        if not detector_available:
            observation_valid = False
            if status in ("", "ok"):
                status = "model_unavailable"
        elif size is None:
            observation_valid = False
            if status in ("", "ok"):
                status = "image_invalid"
        else:
            gate_reason = _observation_gate_reason(
                occlusion_state=occlusion_state,
                foreground=foreground,
                backend=backend,
                occluded=occluded,
                capture_reading=capture_reading,
            )
            if gate_reason:
                observation_valid = False
                if status in ("", "ok"):
                    status = gate_reason
            if not observation_valid:
                if status in ("", "ok"):
                    status = "observation_invalid"
            elif not status:
                status = "ok"

        diagnostics: dict[str, Any] = {
            "detector_available": bool(detector_available),
            "observation_valid": bool(observation_valid),
            "status": str(status),
            "frame_id": int(fid),
            "observed_at": round(at, 3),
            "image_size": None if size is None else [round(size[0], 2), round(size[1], 2)],
            "occlusion_state": str(occlusion_state or ""),
            "foreground": foreground,
            "backend": str(backend or ""),
            "occluded": occluded,
            "capture_reading": capture_reading,
            "candidates": 0,
            "weak_candidates": 0,
            "matched": 0,
            "weak_matched": 0,
            "created": [],
            "entered": [],
            "moved": [],
            "lost": [],
            "expired_unconfirmed": [],
            "miss_advanced": {},
            "unknown_side_candidates": 0,
            "visible": 0,
            "retained": 0,
            "rebased": False,
        }

        if observation_valid:
            rebasing = bool(self._obs_rebase_pending)
            self._obs_rebase_pending = False
            if rebasing:
                for track in self._obs_alive.values():
                    track.pending_move = None
                    track.velocity = (0.0, 0.0)
                    track.miss_count = 0
            for track in self._obs_alive.values():
                track.observed = False
            candidates = build_unit_observations(
                dets, size, fallback_ratio=fallback_ratio)
            diagnostics["candidates"] = sum(
                1 for cand in candidates if not cand.uncertain)
            diagnostics["weak_candidates"] = sum(
                1 for cand in candidates if cand.uncertain)
            diagnostics["unknown_side_candidates"] = sum(
                1 for cand in candidates if cand.side == SIDE_UNKNOWN)
            self._apply_observations(
                candidates, fid, at, diagnostics,
                advance_misses=not rebasing)
            if rebasing:
                # Baseline only already-confirmed units.  A unit first seen on
                # the resume frame must still earn its 2-frame entry event.
                selected = [track for track in self._obs_alive.values()
                            if track.confirmed]
            else:
                selected = [track for track in self._obs_alive.values()
                            if track.observed]
            selected.sort(key=self._unit_sort_key)
            units = [self._as_board_unit(track) for track in selected]
            diagnostics["visible"] = sum(1 for track in selected if track.observed)
            diagnostics["retained"] = len(selected) - diagnostics["visible"]
            diagnostics["rebased"] = rebasing
        else:
            # Occluded / low-trust / unavailable frame: never advance miss.
            # Only confirmed identities are carried over; unconfirmed ghosts
            # must not pass MatchTracker's own entry gate on a bad frame.
            tracks = sorted(
                (track for track in self._obs_alive.values() if track.confirmed),
                key=self._unit_sort_key)
            units = [self._as_board_unit(track, observed=False) for track in tracks]
            diagnostics["retained"] = len(units)

        diagnostics["alive"] = len(self._obs_alive)
        diagnostics["confirmed"] = sum(
            1 for track in self._obs_alive.values() if track.confirmed)
        self._obs_last_diagnostics = _copy.deepcopy(diagnostics)
        return units, diagnostics

    # --------------------------------------------------------- observation math

    def _apply_observations(
        self,
        candidates: list[UnitObservation],
        frame_id: int,
        observed_at: float,
        diagnostics: dict[str, Any],
        *,
        advance_misses: bool = True,
    ) -> None:
        solid = [cand for cand in candidates if not cand.uncertain]
        weak = [cand for cand in candidates if cand.uncertain]
        alive = list(self._obs_alive.values())

        solid_pairs: list[tuple[float, int, int]] = []
        for ti, track in enumerate(alive):
            for ci, cand in enumerate(solid):
                score = self._match_score(track, cand)
                if score is not None:
                    solid_pairs.append((score, ti, ci))
        solid_pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
        used_tracks: set[int] = set()
        used_solid: set[int] = set()
        for _score, ti, ci in solid_pairs:
            if ti in used_tracks or ci in used_solid:
                continue
            used_tracks.add(ti)
            used_solid.add(ci)
            self._apply_solid(alive[ti], solid[ci], frame_id, observed_at, diagnostics)

        # Weak boxes only protect an existing track from being counted as
        # missing; they never create a unit and never move it.
        weak_pairs: list[tuple[float, int, int]] = []
        for ti, track in enumerate(alive):
            if ti in used_tracks:
                continue
            for ci, cand in enumerate(weak):
                score = self._match_score(track, cand)
                if score is not None:
                    weak_pairs.append((score, ti, ci))
        weak_pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
        used_weak: set[int] = set()
        for _score, ti, ci in weak_pairs:
            if ti in used_tracks or ci in used_weak:
                continue
            used_tracks.add(ti)
            used_weak.add(ci)
            track = alive[ti]
            track.observed = True
            track.weak_count += 1
            diagnostics["weak_matched"] += 1

        unmatched_weak = [cand for ci, cand in enumerate(weak) if ci not in used_weak]
        for ti, track in enumerate(alive):
            if ti in used_tracks:
                continue
            if any(self._near_track(track, cand) for cand in unmatched_weak):
                # A low-confidence box sits where this unit should be; do not
                # turn occlusion into a missing frame.
                track.observed = True
                continue
            if not advance_misses:
                # Rebase frame: keep identity without a loss verdict.  The
                # caller receives it with observed=False and MatchTracker
                # baselines it in the same tick.
                continue
            if not track.confirmed:
                self._obs_alive.pop(track.instance_id, None)
                diagnostics["expired_unconfirmed"].append(track.instance_id)
                continue
            # A confirmed track's movement must be observed on consecutive
            # valid frames; a missing frame restarts the move gate.
            track.pending_move = None
            track.velocity = (0.0, 0.0)
            track.miss_count += 1
            diagnostics["miss_advanced"][str(track.instance_id)] = track.miss_count
            if track.miss_count >= self._miss_limit:
                self._lose_track(track, frame_id, observed_at, diagnostics)

        for ci, cand in enumerate(solid):
            if ci in used_solid:
                continue
            track = self._new_track(cand, frame_id, observed_at)
            diagnostics["created"].append(track.instance_id)

    @staticmethod
    def _unit_sort_key(track: _Track) -> tuple[Any, ...]:
        side_rank = {SIDE_MINE: 0, SIDE_ENEMY: 1, SIDE_UNKNOWN: 2}
        lane_rank = {LANE_SUPPORT: 0, LANE_FRONT: 1}
        return (
            side_rank.get(track.side, 3),
            lane_rank.get(track.lane, 2),
            999 if track.slot is None else int(track.slot),
            int(track.instance_id),
        )

    def _match_score(self, track: _Track, cand: UnitObservation) -> float | None:
        if (track.side != SIDE_UNKNOWN and cand.side != SIDE_UNKNOWN
                and track.side != cand.side):
            # Side is never inferred from motion/smoothing.
            return None
        predicted = _shift_bbox(track.bbox, track.velocity[0], track.velocity[1])
        iou = _bbox_iou_norm(predicted, cand.bbox)
        old_cx, old_cy = _bbox_center(predicted)
        new_cx, new_cy = _bbox_center(cand.bbox)
        dx, dy = abs(old_cx - new_cx), abs(old_cy - new_cy)
        distance = math.hypot(dx, dy)
        lane_same = track.lane == cand.lane
        try:
            slot_delta = abs(int(track.slot) - int(cand.slot))
        except (TypeError, ValueError):
            slot_delta = 99

        if iou >= 0.20:
            score = 1.0 + iou
        elif distance <= 0.12:
            score = 0.8 - distance
        elif lane_same and slot_delta <= 2 and dx <= 0.16:
            # Adjacent-slot motion keeps the same identity.
            score = 0.5 - 0.05 * slot_delta - dx
        elif not lane_same and dx <= 0.08:
            # support <-> front column move.
            score = 0.4 - dx
        else:
            return None
        if lane_same:
            score += 0.08
        if cand.uncertain:
            score -= 0.02
        return score

    @staticmethod
    def _near_track(track: _Track, cand: UnitObservation) -> bool:
        predicted = _shift_bbox(track.bbox, track.velocity[0], track.velocity[1])
        if _bbox_iou_norm(predicted, cand.bbox) >= 0.10:
            return True
        old_cx, old_cy = _bbox_center(predicted)
        new_cx, new_cy = _bbox_center(cand.bbox)
        dx, dy = abs(old_cx - new_cx), abs(old_cy - new_cy)
        if math.hypot(dx, dy) <= 0.08:
            return True
        return track.lane == cand.lane and dx <= 0.10 and dy <= 0.20

    def _new_track(self, cand: UnitObservation, frame_id: int,
                   observed_at: float) -> _Track:
        track = _Track(
            instance_id=self._next_id,
            side=cand.side,
            lane=cand.lane,
            slot=int(cand.slot),
            bbox=cand.bbox,
            det_conf=float(cand.det.conf),
            side_conf=float(cand.side_confidence),
            lane_conf=float(cand.lane_confidence),
            first_seen_frame=int(frame_id),
            last_seen_frame=int(frame_id),
            first_seen_at=float(observed_at),
            last_seen_at=float(observed_at),
            lane_bbox=cand.bbox,
        )
        self._next_id += 1
        track.history.append(self._history_record(cand, frame_id, observed_at))
        self._obs_alive[track.instance_id] = track
        return track

    @staticmethod
    def _history_record(cand: UnitObservation, frame_id: int,
                        observed_at: float) -> dict[str, Any]:
        return {
            "frame_id": int(frame_id),
            "observed_at": round(float(observed_at), 3),
            "side": cand.side,
            "lane": cand.lane,
            "slot": int(cand.slot),
            "bbox": [round(float(v), 4) for v in cand.bbox],
            "confidence": round(float(cand.det.conf), 3),
        }

    def _apply_solid(self, track: _Track, cand: UnitObservation, frame_id: int,
                     observed_at: float, diagnostics: dict[str, Any]) -> None:
        old_cx, old_cy = _bbox_center(track.bbox)
        new_cx, new_cy = _bbox_center(cand.bbox)
        dx, dy = new_cx - old_cx, new_cy - old_cy
        if abs(dx) <= 0.12 and abs(dy) <= 0.18:
            track.velocity = (0.5 * track.velocity[0] + 0.5 * dx,
                              0.5 * track.velocity[1] + 0.5 * dy)
        else:
            track.velocity = (0.0, 0.0)

        track.miss_count = 0
        track.observed = True
        track.last_seen_frame = int(frame_id)
        track.last_seen_at = float(observed_at)
        track.det_conf = float(cand.det.conf)
        track.lane_conf = float(cand.lane_confidence)
        track.confirm_count = min(track.confirm_count + 1, 1_000_000)

        # Side: only a current trustworthy observation updates it.  A weak
        # frame may leave the last confirmed side in place, but its confidence
        # drops so no side-specific event can be emitted from a guess.
        if cand.side == SIDE_UNKNOWN:
            track.side_conf = float(cand.side_confidence)
        else:
            if track.side in (SIDE_UNKNOWN, cand.side):
                track.side = cand.side
            track.side_conf = float(cand.side_confidence)

        if cand.lane == track.lane:
            track.pending_move = None
            track.slot = int(cand.slot)
            track.bbox = cand.bbox
            track.lane_bbox = cand.bbox
        else:
            pending = track.pending_move
            if pending is not None and pending.get("lane") == cand.lane:
                pending["count"] = int(pending["count"]) + 1
                pending["last_frame"] = int(frame_id)
                pending["last_at"] = float(observed_at)
                pending["after"] = cand.position_snapshot()
                pending["frame_ids"].append(int(frame_id))
                pending["confidences"].append(float(cand.det.conf))
            else:
                pending = {
                    "lane": cand.lane,
                    "count": 1,
                    "before": self._committed_position(track),
                    "after": cand.position_snapshot(),
                    "frame_ids": [int(frame_id)],
                    "confidences": [float(cand.det.conf)],
                }
                track.pending_move = pending
            # Visual position follows the observation; committed lane/slot and
            # lane_bbox stay on the old cell until two-frame confirmation.
            track.bbox = cand.bbox
            if int(pending["count"]) >= self._move_confirm_frames:
                before = pending["before"]
                track.lane = cand.lane
                track.slot = int(cand.slot)
                track.lane_bbox = cand.bbox
                track.bbox = cand.bbox
                after = self._committed_position(track)
                evidence = _evidence(
                    before, after, pending["frame_ids"],
                    first_seen=track.first_seen_frame,
                    last_seen=track.last_seen_frame,
                    confirm_count=int(pending["count"]),
                    confidences=pending["confidences"],
                    frame_limit=self._evidence_frame_limit,
                )
                track.move_evidence = evidence
                track.pending_move = None
                diagnostics["moved"].append({
                    "instance_id": track.instance_id,
                    "side": track.side,
                    "before": _position_copy(before),
                    "after": _position_copy(after),
                    "evidence": _copy.deepcopy(evidence),
                })
                self._record_ledger(
                    "unit_moved", track, frame_id, observed_at,
                    before=before, after=after, evidence=evidence)

        track.history.append(self._history_record(cand, frame_id, observed_at))
        if len(track.history) > self._history_limit:
            del track.history[:-self._history_limit]

        if not track.confirmed and track.confirm_count >= self._confirm_frames:
            track.confirmed = True
            frame_ids = [item["frame_id"] for item in track.history]
            confidences = [float(item["confidence"]) for item in track.history]
            after = self._committed_position(track)
            evidence = _evidence(
                None, after, frame_ids,
                first_seen=track.first_seen_frame,
                last_seen=track.last_seen_frame,
                confirm_count=track.confirm_count,
                confidences=confidences,
                frame_limit=self._evidence_frame_limit,
            )
            track.enter_evidence = evidence
            diagnostics["entered"].append(track.instance_id)
            self._record_ledger(
                "unit_entered", track, frame_id, observed_at,
                after=after, evidence=evidence)

        diagnostics["matched"] += 1

    @staticmethod
    def _committed_position(track: _Track) -> dict[str, Any]:
        bbox = track.lane_bbox if track.lane_bbox is not None else track.bbox
        return {
            "side": track.side,
            "lane": track.lane,
            "slot": track.slot,
            "bbox": [round(float(v), 4) for v in bbox],
        }

    def _lose_track(self, track: _Track, frame_id: int, observed_at: float,
                    diagnostics: dict[str, Any]) -> None:
        """Record an observation loss only; never claim the unit died."""
        before = self._committed_position(track)
        frame_ids = [item["frame_id"] for item in track.history]
        confidences = [float(item["confidence"]) for item in track.history]
        evidence = _evidence(
            before, None, frame_ids,
            first_seen=track.first_seen_frame,
            last_seen=track.last_seen_frame,
            confirm_count=track.confirm_count,
            confidences=confidences,
            frame_limit=self._evidence_frame_limit,
        )
        record = {
            "kind": "unit_lost",
            "instance_id": track.instance_id,
            "side": track.side,
            "lane": track.lane,
            "slot": track.slot,
            "frame_id": int(frame_id),
            "observed_at": round(float(observed_at), 3),
            "miss_count": int(track.miss_count),
            "reason": "missing_frames",
            "evidence": _copy.deepcopy(evidence),
        }
        key = (track.instance_id, track.first_seen_frame,
               track.last_seen_frame, int(track.miss_count))
        if key not in self._obs_loss_keys:
            self._obs_loss_keys.add(key)
            self._obs_lost.append(record)
            if len(self._obs_lost) > 20:
                del self._obs_lost[:-20]
            diagnostics["lost"].append(_copy.deepcopy(record))
            self._record_ledger(
                "unit_lost", track, frame_id, observed_at,
                before=before, after=None, evidence=evidence)
        self._obs_alive.pop(track.instance_id, None)

    def _record_ledger(self, kind: str, track: _Track, frame_id: int,
                       observed_at: float, *, before: dict[str, Any] | None = None,
                       after: dict[str, Any] | None = None,
                       evidence: dict[str, Any] | None = None) -> None:
        record: dict[str, Any] = {
            "kind": kind,
            "instance_id": track.instance_id,
            "frame_id": int(frame_id),
            "observed_at": round(float(observed_at), 3),
            "side": track.side,
            "lane": track.lane,
            "slot": track.slot,
            "confidence": round(float(track.det_conf), 3),
        }
        if before is not None:
            record["before"] = _position_copy(before)
        if after is not None:
            record["after"] = _position_copy(after)
        if evidence is not None:
            record["evidence"] = _copy.deepcopy(evidence)
        self._obs_ledger.append(record)
        if len(self._obs_ledger) > 20:
            del self._obs_ledger[:-20]

    @staticmethod
    def _as_board_unit(track: _Track, *, observed: bool | None = None) -> BoardUnit:
        return BoardUnit(
            instance_id=track.instance_id,
            side=track.side or SIDE_UNKNOWN,
            lane=track.lane,
            slot=track.slot,
            bbox=track.bbox,
            side_confidence=round(float(track.side_conf), 3),
            lane_confidence=round(float(track.lane_conf), 3),
            detection_confidence=round(float(track.det_conf), 3),
            first_seen_frame=track.first_seen_frame,
            last_seen_frame=track.last_seen_frame,
            confirm_count=track.confirm_count,
            miss_count=track.miss_count,
            observed=track.observed if observed is None else bool(observed),
            trajectory=True,
            track_confirmed=track.confirmed,
            enter_evidence=_copy.deepcopy(track.enter_evidence),
            move_evidence=_copy.deepcopy(track.move_evidence),
            position_history=_copy.deepcopy(track.history[-20:]),
            observed_at=track.last_seen_at,
        )
