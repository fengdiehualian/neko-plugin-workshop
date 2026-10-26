"""Kards 自动对局布局标定 — 界面元素坐标（相对比例 0..1，乘窗口客户区尺寸得绝对坐标）。

Kards（Unity UI）在标准 1920×1080 下布局稳定，但窗口缩放/分辨率变化时
绝对坐标会漂移。所有元素一律用「相对比例 + 窗口客户区尺寸」计算，并支持
calibrate 模式：kards_calibrate 工具截取关键区域让 YUI/玩家确认后写入
kards_layout.json（存在插件目录），重启自动加载。

本文件默认值是基于 Kards 标准界面（1920×1080，100% 缩放）的初始估算，
**必须以实机截图校准后为准**；数值识别模板（数字 0-9）由 calibrate 从真实
画面裁剪生成，存入 layout 目录 templates/。
"""
from __future__ import annotations

import json
import pathlib

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parent
_LAYOUT_FILE = _PLUGIN_DIR / "kards_layout.json"
_TEMPLATE_DIR = _PLUGIN_DIR / "templates" / "kards"

# ── 初始估算布局（相对坐标；校准后覆盖） ──
DEFAULT_LAYOUT = {
    # 指挥点数字（黄色，左上角）
    "kredits": {"x": 0.030, "y": 0.042, "w": 0.040, "h": 0.045},
    # 我方总部防御（指挥点下方）
    "my_hq": {"x": 0.030, "y": 0.095, "w": 0.030, "h": 0.035},
    # 敌方总部防御（右上角）
    "opp_hq": {"x": 0.935, "y": 0.042, "w": 0.030, "h": 0.035},
    # 我方手牌区（底部；卡牌中心按张数扇形计算）
    "hand": {"left": 0.06, "right": 0.94, "y": 0.880, "card_h": 0.11},
    # 战线槽位（从左到右 5 槽；x 按槽位均分）
    "opp_support_line": {"y": 0.335, "slots": 5, "slot_w": 0.085, "slot_h": 0.110},
    "front_line": {"y": 0.520, "slots": 5, "slot_w": 0.085, "slot_h": 0.110},
    "my_support_line": {"y": 0.705, "slots": 5, "slot_w": 0.085, "slot_h": 0.110},
    # 结束回合按钮（右下角；Kards 标准界面默认位）
    "end_turn": {"x": 0.925, "y": 0.860},
    # 攻击确认/目标选择时出现的「攻击」按钮（估算位）
    "attack_confirm": {"x": 0.500, "y": 0.600},
    # 回合提示（对手回合等待检测用区域：屏幕中央横幅）
    "turn_banner": {"x": 0.35, "y": 0.10, "w": 0.30, "h": 0.06},
}


def _default_deepcopy() -> dict:
    return json.loads(json.dumps(DEFAULT_LAYOUT))


def load_layout() -> dict:
    """加载标定布局；无标定文件时返回默认估算。"""
    if _LAYOUT_FILE.is_file():
        try:
            data = json.loads(_LAYOUT_FILE.read_text(encoding="utf-8"))
            merged = _default_deepcopy()
            merged.update(data or {})
            return merged
        except Exception:  # noqa: BLE001 - 标定文件损坏则回退默认
            pass
    return _default_deepcopy()


def save_layout(layout: dict) -> pathlib.Path:
    """保存标定布局到插件目录（供复现与升级保留）。"""
    _LAYOUT_FILE.write_text(
        json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _LAYOUT_FILE


def template_dir() -> pathlib.Path:
    _TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    return _TEMPLATE_DIR


def hand_card_centers(n: int, layout: dict, win_w: int, win_h: int) -> list[dict]:
    """按手牌张数 n 计算每张牌中心点（横排扇形）。返回 [{x, y, index}] 绝对坐标。"""
    n = max(1, min(int(n), 9))
    hand = layout.get("hand", DEFAULT_LAYOUT["hand"])
    left = hand["left"] * win_w
    right = hand["right"] * win_w
    y = hand["y"] * win_h
    if n == 1:
        xs = [(left + right) / 2]
    else:
        step = (right - left) / (n + 1)
        xs = [left + step * (i + 1) for i in range(n)]
    return [{"index": i, "x": round(xs[i]), "y": round(y), "slot": i} for i in range(n)]


def line_slots(line_key: str, layout: dict, win_w: int, win_h: int) -> list[dict]:
    """某战线 5 个槽位的中心绝对坐标（含「空位可用」标记位）。"""
    spec = layout.get(line_key, DEFAULT_LAYOUT[line_key])
    slots = int(spec.get("slots", 5))
    y = spec["y"] * win_h
    slot_w = spec.get("slot_w", 0.085) * win_w
    # 战线水平居中排布
    total = slot_w * slots
    start_x = (win_w - total) / 2 + slot_w / 2
    out = []
    for i in range(slots):
        out.append({"index": i, "slot": i, "x": round(start_x + i * slot_w), "y": round(y)})
    return out


def region_pixels(name: str, layout: dict, win_w: int, win_h: int) -> tuple[int, int, int, int]:
    """命名的矩形区域 → (left, top, right, bottom) 绝对像素。"""
    spec = layout.get(name)
    if spec is None:
        raise KeyError(f"layout 中无区域 '{name}'")
    if "left" in spec and "right" in spec:  # 横条（手牌行）：left/right/y/card_h
        left = int(spec["left"] * win_w)
        right = int(spec["right"] * win_w)
        y = int(spec["y"] * win_h)
        card_h = int(spec.get("card_h", 0.11) * win_h)
        return left, max(0, y - int(card_h * 0.6)), right, min(win_h, y + int(card_h * 0.4))
    if "w" in spec:  # 矩形：x/y/w/h
        left = int(spec["x"] * win_w)
        top = int(spec["y"] * win_h)
        return left, top, left + max(2, int(spec["w"] * win_w)), top + max(2, int(spec["h"] * win_h))
    # 点（按钮）：x/y，取 40×28 点击盒
    cx, cy = int(spec["x"] * win_w), int(spec["y"] * win_h)
    return cx - 20, cy - 14, cx + 20, cy + 14


def point(name: str, layout: dict, win_w: int, win_h: int) -> tuple[int, int]:
    """命名点（按钮中心）→ 绝对坐标。"""
    spec = layout.get(name)
    if spec is None:
        raise KeyError(f"layout 中无点 '{name}'")
    return int(spec["x"] * win_w), int(spec["y"] * win_h)
