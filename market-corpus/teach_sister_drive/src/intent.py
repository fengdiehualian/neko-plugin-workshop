"""Parse one immediate command. Speed is km/h, steering is normalized [-1, 1].

Keep the browser's ui/intent.mjs equivalent; both use tests/intent_cases.json.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

ACTIONS = ("brake", "back", "left", "right", "center", "forward")
LESSON_ACTIONS = ("fasten_seatbelt", "start_engine", "shift_drive")
ALL_ACTIONS = ACTIONS + LESSON_ACTIONS
WORDS = {
    "brake": ("刹车", "停下", "停车", "刹住", "停止", "brake", "stop"),
    "back": ("倒车", "后退", "往后", "reverse", "back"),
    "left": ("向左", "往左", "左转", "左边", "left"),
    "right": ("向右", "往右", "右转", "右边", "right"),
    "center": ("回正", "摆正", "直行", "方向回中", "center"),
    "forward": (
        "前进",
        "往前",
        "加速",
        "油门",
        "向前",
        "forward",
        "出发",
        "起步",
        "踩下油门",
        "踩一下油门",
        "踩一脚油门",
    ),
    "fasten_seatbelt": (
        "系安全带",
        "系上安全带",
        "系好安全带",
        "扣上安全带",
        "扣好安全带",
        "安全带系上",
        "安全带系好",
        "安全带扣上",
        "安全带扣好",
        "系一下安全带",
        "系下安全带",
        "扣一下安全带",
    ),
    "start_engine": (
        "打火",
        "点火",
        "启动引擎",
        "启动发动机",
        "启动汽车",
        "发动汽车",
        "发动引擎",
        "发动发动机",
        "发动车子",
        "把车启动",
        "引擎启动",
        "发动机启动",
        "打一下火",
        "启动一下发动机",
        "启动一下引擎",
    ),
    "shift_drive": ("挂前进挡", "挂前进档", "挂d挡", "挂d档"),
}
NEGATION = r"不要|别|不能|不许|不准|禁止|不用|无需|莫|不|\b(?:not|never|don't|do not)\b"
DEFER = r"等(?:我|你|到|一下|一会|会儿)|待会|稍后|过一会|如果|假如|要是|当.+?时|\b(?:if|when|later|after)\b"
QUESTION = (
    r"为什么|怎么|是否|能否|要不要|有没有|我(?:刚才|之前|说过)|你(?:刚才|之前)|吗[？?。！!]*$|[？?]|\b(?:why|whether)\b"
)
SEQUENCE = r"然后|接着|随后|之后|以后|[再后](?=往|向|加|前|倒|左|右|开|刹|停|回|打|挂|系)|\b(?:then|next)\b"
NUMBER = r"(?:\d+(?:\.\d+)?|[零一二两三四五六七八九十百]+)"
SPEED = rf"({NUMBER})\s*(?:公里(?:每小时|每小時|/小时)?|千米(?:每小时|/小时)|km\s*/?\s*h|kph|迈)"
DURATION = rf"({NUMBER})\s*(?:秒(?:钟)?|seconds?|secs?)(?!后)"
CLAUSE = r"[，,。!！;；]|\.(?!\d)|(?:但是|不过|而是|但|而|\bbut\b)"
FILLER = re.compile(
    r"悠怡|妹妹|猫娘|请|麻烦|帮我|给我|我想让你|自己的|你的|现在|马上|立刻|赶紧|赶快|先|一下|一点|稍微|轻轻|慢慢|慢点|缓慢|低速|轻踩|保持|目标速度|速度|公里每小时|每小时|的速度|方向盘|方向|持续|行驶|前行|踩|油门踏板|转|开|把|车|你|以|到|为|和|并|且|着|好|上|点|吧|呀|啊|喵|～|~|\b(?:please|go|drive|turn|a little|a bit|slowly|slow|for|at|and|now)\b"
)


@dataclass(frozen=True)
class CommandPlan:
    commands: list[dict] = field(default_factory=list)
    error: str = ""
    note: str = ""


def _number(value: str) -> float:
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "百" in value:
        raise ValueError("unsupported number")
    if "十" in value:
        if not re.fullmatch(r"[一二两三四五六七八九]?十[一二三四五六七八九]?", value):
            raise ValueError("unsupported number")
        before, after = value.split("十", 1)
        return float((digits.get(before, 1) if before else 1) * 10 + digits.get(after, 0))
    if len(value) == 1 and value in digits:
        return float(digits[value])
    raise ValueError("unsupported number")


def _hits(clause: str) -> set[str]:
    found = set()
    # Full tutorial phrases contain driving words (挂前进挡 contains 前进).
    for action in LESSON_ACTIONS:
        for word in WORDS[action]:
            if word in clause:
                found.add(action)
                clause = clause.replace(word, "")
    return found | {
        action
        for action in ACTIONS
        if any(re.search(rf"\b{word}\b" if word.isascii() else word, clause) for word in WORDS[action])
    }


def _is_directive(text: str) -> bool:
    remainder = re.sub(SPEED, "", re.sub(DURATION, "", text))
    for word in sorted((word for words in WORDS.values() for word in words), key=len, reverse=True):
        remainder = re.sub(rf"\b{word}\b" if word.isascii() else word, "", remainder)
    remainder = FILLER.sub("", remainder)
    return not re.sub(r"[\s，,。.!！;；]+", "", remainder)


def parse_command(
    text: str | None,
    explicit: str | None = None,
    *,
    target_speed: float | None = None,
    steering: float | None = None,
    duration_ms: int | None = None,
) -> CommandPlan:
    """Original speech wins over LLM fields, including negation and parameters."""
    if len(text or "") > 160:
        return CommandPlan(error="指令过长，请一次说一个动作")
    raw = unicodedata.normalize("NFKC", text or "").strip().lower()
    raw = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", raw)
    raw = re.sub(r"(不要|不许|不准|别|不用)[，,]\s*", r"\1", raw)
    if not raw:
        action = (explicit or "").strip().lower()
        if action not in ALL_ACTIONS:
            return CommandPlan(error="没有明确的驾驶动作")
        return _build(action, target_speed, steering, duration_ms)
    if re.search(QUESTION, raw):
        return CommandPlan(error="疑问或转述不作为驾驶指令")
    if re.search(r"环顾|看看周围|看一下周围|观察四周", raw):
        return CommandPlan(error="环顾由游戏教程动画控制，不能单独执行")
    if re.search(r"停止(?:说话|讲话|播放|录音)|[-−负]\s*\d|(?<!\d)\.\d|\d[eE][+-]?\d", raw):
        return CommandPlan(error="不是明确的驾驶指令或数值格式不支持")
    cuts = [match.start() for pattern in (DEFER, SEQUENCE) if (match := re.search(pattern, raw))]
    if cuts:
        immediate = raw[: min(cuts)].strip(" ，,。;；")
        first = parse_command(immediate) if immediate else CommandPlan()
        if len(first.commands) == 1 and first.commands[0]["action"] == "brake":
            return CommandPlan(first.commands, note="只执行当前刹车；后续动作请到时单独下令")
        return CommandPlan(error="包含条件或先后步骤，请一次说一个立即动作")
    positive, found, negated = [], set(), False
    for clause in re.split(CLAUSE, raw):
        hits = _hits(clause)
        if re.search(NEGATION, clause):
            negated = negated or bool(hits or re.search(SPEED, clause))
        else:
            positive.append(clause)
            found.update(hits)
    positive_text = "，".join(positive)
    if not found and not re.search(SPEED, positive_text):
        return CommandPlan(error="否定指令不会执行" if negated else "没有明确的驾驶动作")
    if re.search(r"松(?:开)?油门|松脚|减速|减少速度|\bslow down\b", positive_text):
        return CommandPlan(error="请说目标速度或刹车，不能猜测减速幅度")
    if re.search(rf"{NUMBER}\s*度", positive_text):
        return CommandPlan(error="游戏方向量不是角度，请说向左/右转一点")
    if not _is_directive(positive_text):
        return CommandPlan(error="只接受明确口令，请直接说动作、速度或持续时间")
    lesson = found.intersection(LESSON_ACTIONS)
    if lesson:
        if len(found) != 1:
            return CommandPlan(error="请一次说一个教程动作")
        return _build(next(iter(lesson)), None, None, None)
    directions = found.intersection(("left", "right", "center"))
    pedals = found.intersection(("forward", "back", "brake"))
    if len(directions) > 1 or len(pedals) > 1:
        return CommandPlan(error="同一句有相互冲突的驾驶动作")
    speeds, durations = re.findall(SPEED, positive_text), re.findall(DURATION, positive_text)
    if len(speeds) > 1 or len(durations) > 1:
        return CommandPlan(error="请只指定一个速度和持续时间")
    try:
        speed = _number(speeds[0]) if speeds else None
        duration = math.floor(_number(durations[0]) * 1000 + 0.5) if durations else None
    except ValueError:
        return CommandPlan(error="无法确定数值")
    if directions and not pedals and "开" in positive_text:
        pedals.add("forward")
    if speed is not None and not pedals:
        pedals.add("brake" if speed == 0 else "forward")
    action = next(iter(pedals or directions))
    if action == "brake" and (directions or (speed is not None and speed != 0)):
        return CommandPlan(error="刹车与转向或非零速度请分开下令")
    if action == "back" and speed is not None:
        speed = -speed
    if speed is None and action == "forward" and re.search(r"慢|缓|低速|轻踩|\bslow(?:ly)?\b", positive_text):
        speed = 5.0
    steer = None
    if directions:
        amount = 0.12 if re.search(r"一点|一點|稍微|轻轻|\ba (?:little|bit)\b", positive_text) else 0.25
        steer = {"left": -amount, "right": amount, "center": 0.0}[next(iter(directions))]
    return _build(action, speed, steer, duration)


def _build(action: str, speed: float | None, steer: float | None, duration: int | None) -> CommandPlan:
    for value in (speed, steer, duration):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        ):
            return CommandPlan(error="驾驶参数必须是有限数值")
    if speed is not None and not -20 <= speed <= 60:
        return CommandPlan(error="目标速度必须在倒车20至前进60公里每小时之间")
    if steer is not None and not -1 <= steer <= 1:
        return CommandPlan(error="方向量必须在 -1 至 1 之间")
    if duration is not None and (int(duration) != duration or not 100 <= duration <= 10000):
        return CommandPlan(error="持续时间必须在0.1至10秒之间")
    if action in LESSON_ACTIONS:
        if any(value is not None for value in (speed, steer, duration)):
            return CommandPlan(error="教程步骤不接受速度或转向参数")
        return CommandPlan([{"action": action}])
    if action == "brake" and ((speed is not None and speed != 0) or steer is not None):
        return CommandPlan(error="刹车不接受转向或非零速度")
    if action == "forward" and speed is not None and speed <= 0:
        return CommandPlan(error="前进速度必须大于零")
    if action == "back" and speed is not None and speed >= 0:
        return CommandPlan(error="倒车速度必须小于零")
    if action in ("left", "right", "center") and speed is not None:
        return CommandPlan(error="同时给油请用前进或倒车并指定方向量")
    command: dict = {"action": action}
    if action in ("forward", "back") and speed is not None:
        command["target_speed"] = float(speed)
    elif action == "brake":
        command["target_speed"] = 0.0
    if steer is not None:
        if (
            (action == "left" and steer >= 0)
            or (action == "right" and steer <= 0)
            or (action == "center" and steer != 0)
        ):
            return CommandPlan(error="动作与方向量不一致")
        command["steering"] = float(steer)
    elif action in ("left", "right", "center"):
        command["steering"] = {"left": -0.25, "right": 0.25, "center": 0.0}[action]
    if duration is not None:
        command["duration_ms"] = int(duration)
    elif action in ("left", "right"):
        command["duration_ms"] = 800
    return CommandPlan([command])


def parse_actions(text: str | None, explicit: str | None = None) -> list[str]:
    """Compatibility projection for action labels, not an execution protocol."""
    plan = parse_command(text, explicit)
    if not plan.commands:
        return []
    command = plan.commands[0]
    action = command["action"]
    if action in ("forward", "back") and "steering" in command:
        steer = command["steering"]
        return ["left" if steer < 0 else "right" if steer > 0 else "center", action]
    return [action]
