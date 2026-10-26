"""检测后处理契约：类别表由外部注入（ui_classes.json 10 类），无旧七类路径。"""

import numpy as np
from kards_companion.perception.detector import (
    Detection,
    UnitTracker,
    assign_rows,
    nms,
)
from kards_companion.perception.models import postprocess_detect

# 与 knowledge/ui_classes.json v1 完全一致的 10 类表。
UI_CLASS_NAMES = (
    "unit_board", "card_hand", "card_back", "played_card", "hq",
    "frontline", "end_turn_button", "end_turn_button_active",
    "screen_victory", "screen_defeat",
)
CLASS_TO_ID = {name: index for index, name in enumerate(UI_CLASS_NAMES)}


def det(name: str, x1: float, y1: float, x2: float, y2: float, conf: float = 0.9) -> Detection:
    cid = CLASS_TO_ID[name]
    return Detection(cid, name, conf, x1, y1, x2, y2)


def make_yolo_output(boxes: list[tuple[str, float, float, float, float, float]],
                     n_cls: int | None = None) -> np.ndarray:
    """ultralytics 布局 (1, 4+n_cls, n_anchors)，xywh 为 640 letterbox 坐标。"""
    count = len(UI_CLASS_NAMES) if n_cls is None else n_cls
    out = np.zeros((1, 4 + count, len(boxes)), dtype=np.float32)
    for i, (name, cx, cy, w, h, score) in enumerate(boxes):
        out[0, 0, i], out[0, 1, i], out[0, 2, i], out[0, 3, i] = cx, cy, w, h
        out[0, 4 + CLASS_TO_ID[name], i] = score
    return out


def test_postprocess_maps_back_and_filters_conf() -> None:
    out = make_yolo_output([
        ("unit_board", 320, 200, 80, 100, 0.8),   # 通过
        ("hq", 600, 100, 60, 90, 0.10),           # 低于阈值
        ("unit_board", 322, 202, 80, 100, 0.7),   # 与第一个重叠 -> NMS
    ])
    result = postprocess_detect(out, UI_CLASS_NAMES, scale=1.0, pad_x=0, pad_y=0)
    assert len(result) == 1 and result[0].name == "unit_board"
    assert result[0].conf > 0.5 and abs(result[0].cx - 320) < 2


def test_postprocess_handles_new_ui_classes() -> None:
    out = make_yolo_output([
        ("end_turn_button_active", 1700, 900, 120, 60, 0.9),
        ("screen_victory", 960, 540, 400, 220, 0.75),
        ("card_hand", 200, 1000, 90, 120, 0.62),
    ])
    result = postprocess_detect(out, UI_CLASS_NAMES, scale=1.0, pad_x=0, pad_y=0)
    names = {item.name for item in result}
    assert names == {"end_turn_button_active", "screen_victory", "card_hand"}


def test_postprocess_rejects_channel_mismatch_without_truncation() -> None:
    # 千类式输出（4+1083 行）按 10 类表解读时维度不足 -> 空结果，
    # 绝不截断前若干列当作 10 类读。
    wide = np.zeros((1, 4 + 1083, 5), dtype=np.float32)
    assert postprocess_detect(wide, UI_CLASS_NAMES, scale=1.0, pad_x=0, pad_y=0) == []
    # 反过来：10 类输出配 1083 类表同样拒绝。
    ten = make_yolo_output([("unit_board", 320, 200, 80, 100, 0.9)])
    names_1083 = tuple(f"class_{i}" for i in range(1083))
    assert postprocess_detect(ten, names_1083, scale=1.0, pad_x=0, pad_y=0) == []


def test_postprocess_letterbox_scale_maps_to_original_coords() -> None:
    out = make_yolo_output([("frontline", 320, 320, 640, 40, 0.9)])
    # scale=0.5、pad=0：640 系坐标 × 2 回到原图。
    result = postprocess_detect(out, UI_CLASS_NAMES, scale=0.5, pad_x=0, pad_y=0)
    assert len(result) == 1
    box = result[0]
    assert abs(box.cx - 640) < 2.5 and abs(box.cy - 640) < 2.5


def test_nms_keeps_max_per_cluster() -> None:
    boxes = [det("unit_board", 0, 0, 100, 100, 0.9),
             det("unit_board", 5, 5, 105, 105, 0.5),
             det("unit_board", 300, 300, 400, 400, 0.8),
             det("card_hand", 0, 0, 100, 100, 0.7)]  # 跨类不抑制
    kept = nms(boxes)
    assert len(kept) == 3
    assert {(k.name, round(k.conf, 1)) for k in kept} == {
        ("unit_board", 0.9), ("unit_board", 0.8), ("card_hand", 0.7)}


def test_assign_rows_side_and_lane() -> None:
    units = [det("unit_board", 500, 200, 560, 280),   # 敌支援（线上方远处）
             det("unit_board", 600, 440, 660, 520),   # 敌前线（贴着线上缘）
             det("unit_board", 500, 520, 560, 600)]   # 我前线（贴着线下缘）
    fronts = [det("frontline", 200, 480, 1400, 540)]
    assignment = assign_rows(units + fronts, frame_h=1080)
    by_pos = {(round(d.cx), round(d.cy)): (side, lane) for d, side, lane in assignment.units}
    assert by_pos[(530, 240)] == ("enemy", "support")
    assert by_pos[(630, 480)] == ("enemy", "front")
    assert by_pos[(530, 560)] == ("mine", "front")
    assert assignment.source == "model"


def test_assign_rows_fallback_ratio() -> None:
    units = [det("unit_board", 500, 900, 560, 980)]
    assignment = assign_rows(units, frame_h=1080)
    assert assignment.units[0][1] == "mine"
    assert assignment.source == "fallback_ratio"


def test_unit_tracker_identity_and_expiry() -> None:
    tracker = UnitTracker()
    front = [det("frontline", 200, 480, 1400, 540)]

    def ids_at(y: float) -> list[tuple[int, float]]:
        rows = assign_rows(front + [det("unit_board", 500, y, 560, y + 80)], 1080)
        return [(t.instance_id, t.det.cy) for t in tracker.update(rows)]

    first = ids_at(300)
    assert len(first) == 1 and first[0][0] == 1
    moved = ids_at(310)  # 小幅移动 -> 同一 id
    assert moved[0][0] == 1
    gone = tracker.update(assign_rows(front, 1080))  # 漏检：宽限期保留
    assert len(gone) == 1
    for _ in range(3):
        gone = tracker.update(assign_rows(front, 1080))
    assert gone == []
    new = ids_at(300)  # 单位彻底消失后再出现 -> 新 id
    assert new[0][0] == 2
