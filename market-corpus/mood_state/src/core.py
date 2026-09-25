"""mood_state 纯逻辑层（无 SDK 依赖，可单测）。

移植自 Open-LLM-VTuber 的心情/精力状态系统：
- 10 分一档的提示词选择与渲染（state_prompt.py）
- 基于关键词规则的启发式评估（filler.py 情景分组思路）
- 向基线的缓慢漂移（替代原启动器的定期 LLM 评估）
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DIMENSIONS = ("mood", "energy")

DEFAULT_STATE: Dict = {"enabled": False, "mood": 50, "energy": 50}


def clamp(value: object) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 50
    return min(max(v, 0), 100)


def clamp_delta(value: object) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return min(max(v, -100), 100)


def tier(score: float) -> str:
    """0~100 分 → 档位 key（'0','10',...,'90'），与提示词 JSON 的 key 对应。"""
    return str(min(max(int(score), 0), 99) // 10 * 10)


def pick(prompts: Dict, dimension: str, score: float) -> str:
    table = prompts.get(dimension) or {}
    text = table.get(tier(score))
    return str(text).strip() if text else ""


def render_state_block(state: Dict, prompts: Dict) -> str:
    """生成注入对话上下文的状态块；未启用时返回空串。"""
    if not state or not state.get("enabled"):
        return ""
    mood = state.get("mood", 50)
    energy = state.get("energy", 50)
    lines = [f"【当前状态】心情 {mood}/100,精力 {energy}/100(由状态系统实时驱动)"]
    mood_text = pick(prompts, "mood", mood)
    energy_text = pick(prompts, "energy", energy)
    if mood_text:
        lines.append(f"- 心情状态:{mood_text}")
    if energy_text:
        lines.append(f"- 精力状态:{energy_text}")
    if len(lines) == 1:
        lines.append("请让语气与上述状态值保持一致。")
    return "\n".join(lines)


STATE_MARKER = "【当前状态】"


def refresh_state_entry(chat: List[Dict], block: str) -> List[Dict]:
    """把状态块伪装成最新的系统记忆：剔除旧状态块，新块追加到末尾。

    兼容两种条目形状：磁盘形 {"type":..,"data":{"content":..}} 与
    API 形 {"role":..,"text":..}。返回 API save 需要的 [{role,text}]。
    block 为空串时只做剔除（用于停用清理）。
    """
    out: List[Dict] = []
    for e in chat:
        if not isinstance(e, dict):
            continue
        role = str(e.get("role") or e.get("type") or "")
        data = e.get("data")
        if isinstance(data, dict):
            text = str(data.get("content") or data.get("text") or "")
        else:
            text = str(e.get("text") or e.get("content") or "")
        if role == "system" and text.startswith(STATE_MARKER):
            continue
        out.append({"role": role, "text": text})
    if block:
        out.append({"role": "system", "text": block})
    return out


def normalize_state(data: Optional[Dict], defaults: Optional[Dict] = None) -> Dict:
    """补全并夹取状态字段；None/损坏数据 → 默认状态。"""
    d = defaults or {}
    base = {
        "enabled": bool(d.get("enabled", False)),
        "mood": clamp(d.get("mood", 50)),
        "energy": clamp(d.get("energy", 50)),
    }
    if isinstance(data, dict):
        base["enabled"] = bool(data.get("enabled", base["enabled"]))
        base["mood"] = clamp(data.get("mood", base["mood"]))
        base["energy"] = clamp(data.get("energy", base["energy"]))
        if data.get("updated_at") is not None:
            base["updated_at"] = data["updated_at"]
        if data.get("eval_note"):
            base["eval_note"] = data["eval_note"]
    return base


def load_state_file(path: Path) -> Optional[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_state_file(path: Path, state: Dict) -> Dict:
    state = normalize_state(state)
    state["updated_at"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    return state


def load_prompts_file(path: Path) -> Dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def drift_toward(
    state: Dict,
    baseline_mood: float,
    baseline_energy: float,
    drift_per_hour: float,
    now: Optional[float] = None,
) -> Tuple[Dict, bool]:
    """按距上次更新的时长向基线回归；返回 (新状态, 是否变化)。"""
    if drift_per_hour <= 0:
        return state, False
    now = now if now is not None else time.time()
    try:
        elapsed_h = max(0.0, (now - float(state.get("updated_at", now))) / 3600.0)
    except (TypeError, ValueError):
        return state, False
    budget = drift_per_hour * elapsed_h
    if budget < 1:
        return state, False

    def step(v: int, target: float) -> int:
        diff = target - v
        if abs(diff) <= budget:
            return int(round(target))
        return v + (1 if diff > 0 else -1) * int(budget)

    new = dict(state)
    new["mood"] = clamp(step(state.get("mood", 50), baseline_mood))
    new["energy"] = clamp(step(state.get("energy", 50), baseline_energy))
    changed = new["mood"] != state.get("mood") or new["energy"] != state.get("energy")
    return new, changed


# ---------- 启发式评估（关键词规则，移植自 filler.py 情景分组） ----------

_NEG_PATTERN = re.compile(
    r"难过|伤心|好累|累了|烦|挂科|考砸|挂了|没考好|没及格|失败|分手|输了|想哭|难受|生病|加班|崩溃|emo|烦死"
)
_POS_PATTERN = re.compile(
    r"哈哈|好笑|开心|太棒|棒了|厉害|谢谢|喜欢你|爱你|好玩|有意思|加油|太好了|赢了|考好|升职"
)
_HIGH_ENERGY_PATTERN = re.compile(r"哇塞|卧槽|不会吧|真的假的|居然|竟然|吓|惊喜|期待")


def heuristic_eval(texts: List[str], state: Dict) -> Tuple[int, int, str]:
    """从最近对话文本估算 (mood_delta, energy_delta, 说明)。

    规则（保守幅度，单次评估总变化 ≤ ±8）：
    - 用户负面情绪 → 共情，心情小幅下降
    - 用户正面情绪 → 被感染，心情上升
    - 惊讶/兴奋 → 精力小幅上升；长文本多 → 精力小幅消耗
    """
    neg = pos = hype = 0
    long_msgs = 0
    for t in texts:
        s = str(t or "")
        if not s.strip():
            continue
        neg += len(_NEG_PATTERN.findall(s))
        pos += len(_POS_PATTERN.findall(s))
        hype += len(_HIGH_ENERGY_PATTERN.findall(s))
        if len(s) > 200:
            long_msgs += 1

    mood_delta = min(pos, 2) * 3 - min(neg, 2) * 3
    energy_delta = min(hype, 2) * 2 - min(long_msgs, 2)
    parts = []
    if pos:
        parts.append(f"正面情绪x{pos}")
    if neg:
        parts.append(f"负面情绪x{neg}")
    if hype:
        parts.append(f"兴奋点x{hype}")
    if long_msgs:
        parts.append(f"长消息x{long_msgs}")
    note = "启发式评估:" + (",".join(parts) if parts else "无明显情绪信号")
    return mood_delta, energy_delta, note
