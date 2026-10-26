"""Explainable OCR scene classifier.

It intentionally returns ``unknown`` when evidence is weak.  The classifier is
not allowed to infer a scene from a character portrait or background alone.
"""

from __future__ import annotations

from typing import Any

from ..data import normalize_lookup_text

SCENE_TERMS: dict[str, tuple[str, ...]] = {
    "warp": ("跃迁", "星轨专票", "跃迁记录", "角色活动跃迁", "光锥活动跃迁"),
    "reward": ("挑战成功", "获得物品", "战利品", "领取奖励", "通关奖励"),
    "team_setup": ("队伍编成", "开始挑战", "快速编队", "支援角色", "出战"),
    "menu": ("角色详情", "光锥", "遗器", "行迹", "星魂", "背包", "合成", "队伍配置"),
    "combat": ("战斗开始", "弱点击破", "我方回合", "敌方回合", "终结技", "战技点", "我方行动中", "敌方行动中", "剩余行动值"),
    "story": ("自动播放", "跳过剧情", "对话记录", "点击继续", "选择你的回答"),
    "loading": ("正在加载", "点击进入", "列车跃迁中", "数据加载"),
    "exploration": ("任务追踪", "导航", "距离目标", "开启导航", "当前任务"),
}

SCENE_PRIORITY = (
    "reward",
    "warp",
    "team_setup",
    "combat",
    "story",
    "menu",
    "loading",
    "exploration",
)


def detect_scene(text: str) -> dict[str, Any]:
    compact = normalize_lookup_text(text)
    hits: dict[str, list[str]] = {}
    for scene, terms in SCENE_TERMS.items():
        matched = [term for term in terms if normalize_lookup_text(term) in compact]
        if matched:
            hits[scene] = matched

    if not hits:
        return {
            "primary_state": "unknown",
            "confidence": "low",
            "evidence": [],
            "classifier": "local_ocr_rules_v1",
        }

    ranked = sorted(
        hits,
        key=lambda scene: (-len(hits[scene]), SCENE_PRIORITY.index(scene)),
    )
    best = ranked[0]
    evidence = [f"OCR：{term}" for term in hits[best]][:6]
    count = len(hits[best])
    return {
        "primary_state": best,
        "confidence": "high" if count >= 2 else "mixed",
        "evidence": evidence,
        "classifier": "local_ocr_rules_v1",
        "alternatives": [{"state": scene, "matched_terms": hits[scene]} for scene in ranked[1:3]],
    }


__all__ = ["SCENE_TERMS", "detect_scene"]
