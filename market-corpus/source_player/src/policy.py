"""The playing policy: frame features -> input primitives.

This is deliberately a *reactive* policy, not a walkthrough bot.  It keeps the
character moving, looking, shooting and interacting between LLM turns, while the
dialog LLM (which can see pushed screenshots) supplies the high-level plan and
can override everything with :class:`Directive`.

Design rules learned from the ICEY plugin's field failures:

* never press keys on a black frame -- "blind" is its own state;
* "cannot see health" is not "health is zero" -- there is no health gate;
* every action carries a short Chinese ``intent`` so the character can narrate
  what she is doing instead of inventing a reason afterwards;
* a stuck ladder escalates only after evidence (scene hash unchanged and motion
  low), and resets as soon as the scene changes.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from . import catalog
from .hud import HudState
from .vision import FrameFeatures

__all__ = [
    "Command",
    "Decision",
    "Directive",
    "LookCalibration",
    "Op",
    "PlayPolicy",
    "PolicyState",
    "parse_instruction",
]


class PolicyState(str, Enum):
    WAITING = "waiting"
    MENU = "menu"
    BLIND = "blind"
    EXPLORE = "explore"
    COMBAT = "combat"
    PUZZLE = "puzzle"
    STUCK = "stuck"
    PAUSED = "paused"


@dataclass
class Op:
    """One input primitive; the service executes these sequentially."""

    kind: str                 # tap | hold | down | up | wait | look | click | mouse_down | mouse_up
    key: str = ""
    seconds: float = 0.05
    dx: float = 0.0
    dy: float = 0.0
    button: str = "left"

    def summary(self) -> str:
        if self.kind == "wait":
            return f"等 {self.seconds:.2f}s"
        if self.kind == "look":
            return f"转视角({self.dx:.0f},{self.dy:.0f})"
        if self.kind in ("click", "mouse_down", "mouse_up"):
            return f"{self.kind} {self.button}"
        if self.kind == "hold":
            return f"按住 {self.key} {self.seconds:.2f}s"
        if self.kind == "tap":
            return f"点 {self.key}"
        return f"{self.kind} {self.key}"


@dataclass
class LookCalibration:
    """Mouse counts needed for one degree of view rotation.

    Source computes yaw as ``dx * sensitivity * m_yaw``.  The defaults come from
    the reference machine's own configs: sensitivity 3, m_yaw 0.022, i.e.
    ``1 / (3 * 0.022) = 15.15`` counts/degree.  Closed-loop aiming tolerates a
    wrong value (it just takes another nudge), which is why this does not need
    to be perfect.
    """

    units_per_degree: float = 15.15
    horizontal_fov: float = 90.0
    vertical_fov: float = 59.0
    invert_y: bool = False

    def counts_for_delta(self, ndc_x: float, ndc_y: float) -> tuple[float, float]:
        """Convert a normalised screen offset from centre into mouse counts."""
        dx_deg = (float(ndc_x) - 0.5) * self.horizontal_fov
        dy_deg = (float(ndc_y) - 0.5) * self.vertical_fov
        dy = dy_deg * self.units_per_degree
        if self.invert_y:
            dy = -dy
        return dx_deg * self.units_per_degree, dy

    def turn_counts(self, degrees: float) -> float:
        return float(degrees) * self.units_per_degree


@dataclass
class Directive:
    """A time-boxed override from the LLM/user."""

    mode: str
    text: str = ""
    seconds: float = 0.0
    command: "Command | None" = None
    issued_at: float = field(default_factory=time.time)
    source: str = "user"

    def expired(self, now: float | None = None) -> bool:
        if self.seconds <= 0:
            return False
        return (now if now is not None else time.time()) - self.issued_at > self.seconds

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "text": self.text,
            "seconds": self.seconds,
            "source": self.source,
            "command": self.command.action if self.command else None,
        }


@dataclass
class Command:
    kind: str        # action | mode | stop | resume | wait | look_at | move
    action: str = ""
    seconds: float = 0.6
    text: str = ""
    recognized: bool = True

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "seconds": self.seconds,
            "text": self.text,
            "recognized": self.recognized,
        }


@dataclass
class Decision:
    state: PolicyState
    intent: str
    ops: list[Op] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def summary(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "intent": self.intent,
            "ops": [op.summary() for op in self.ops],
            "reasons": list(self.reasons),
            "confidence": round(self.confidence, 2),
        }


# ---------------------------------------------------------------------------
# Natural-language instruction parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|s|sec|seconds?)", re.I)

_STOP_WORDS = ("别玩", "不玩", "别打", "停手", "停下", "停止", "暂停", "我来", "我接手",
               "让我来", "关掉")
_RESUME_WORDS = ("继续", "接着玩", "你继续", "你来", "交给你", "接着打", "恢复")
_WAIT_WORDS = ("等一下", "等等", "停一下", "先别动", "别动", "站住", "待机", "不要动")
_PUZZLE_WORDS = ("解谜", "谜题", "puzzle", "传送门谜题", "找路", "想办法")
_FIGHT_WORDS = ("战斗", "清怪", "打架", "开打", "打怪", "干掉他们", "fight")
_EXPLORE_WORDS = ("探索", "找出口", "找路", "沿着走廊", "沿走廊", "探路")
_MOVE_WORDS = {
    "forward": ("前进", "往前走", "向前", "往前", "走", "冲", "跑", "follow", "跟着",
                "forward"),
    "back": ("后退", "往后", "退后", "撤", "躲", "back"),
    "left": ("往左", "向左", "左移", "左闪", "left"),
    "right": ("往右", "向右", "右移", "右闪", "right"),
    "jump": ("跳", "跳跃", "jump", "space"),
    "crouch": ("蹲", "蹲下", "crouch", "ctrl"),
    "sprint": ("冲刺", "加速", "sprint", "shift"),
    "use": ("按e", "使用", "开门", "按按钮", "按钮", "捡", "拿", "拾取", "互动",
            "use", "interact"),
    "reload": ("换弹", "装弹", "reload"),
    "flashlight": ("手电", "开灯", "flashlight"),
    "fire": ("开火", "射击", "攻击", "打", "射", "fire", "shoot", "attack"),
    "alt_fire": ("右键", "副攻击", "次要攻击", "alt fire", "altfire"),
    "portal_blue": ("蓝门", "蓝色传送门", "蓝传送门", "左键", "蓝色", "blue"),
    "portal_orange": ("橙门", "橙色传送门", "橙传送门", "右键", "橙色", "orange"),
    "gravity_gun": ("重力枪", "重力", "gravity"),
    "weapon_pistol": ("手枪", "pistol"),
    "weapon_shotgun": ("霰弹枪", "散弹枪", "shotgun"),
    "weapon_smg": ("冲锋枪", "smg"),
    "weapon_crowbar": ("撬棍", "crowbar"),
}


def _duration(text: str, default: float) -> float:
    match = _DURATION_RE.search(text)
    if not match:
        return default
    try:
        return max(0.08, min(8.0, float(match.group(1))))
    except ValueError:
        return default


def parse_instruction(text: str, *, seconds: float = 0.0) -> Command:
    """Map a free-form Chinese/English instruction onto one command.

    The mapping is intentionally broad: the dialog LLM is told to pass the
    user's *own wording* through, so missing a phrase is worse than accepting a
    short alias.  Unrecognised text comes back with ``recognized=False`` and the
    caller can ask the user to rephrase instead of silently doing nothing.
    """
    raw = str(text or "").strip()
    # Match against a space-free form so "按 E" and "按e" behave identically;
    # Chinese input methods routinely insert spaces around Latin keys.
    body = re.sub(r"\s+", "", raw.lower())
    if not body:
        return Command("action", "", 0.0, raw, recognized=False)

    duration = _duration(body, 0.0) or float(seconds or 0.0) or 0.6

    # Exact canonical action names are used by source_sequence and by an LLM
    # that prefers machine names over Chinese.  Handle them before substring
    # matching so e.g. "left" cannot accidentally match another word.
    exact = {
        "forward": ("move", "forward"), "back": ("move", "back"),
        "left": ("move", "left"), "right": ("move", "right"),
        "jump": ("action", "jump"), "crouch": ("action", "crouch"),
        "sprint": ("action", "sprint"), "use": ("action", "use"),
        "reload": ("action", "reload"), "flashlight": ("action", "flashlight"),
        "fire": ("action", "fire"), "shoot": ("action", "fire"),
        "altfire": ("action", "alt_fire"), "alt_fire": ("action", "alt_fire"),
        "portal_blue": ("action", "portal_blue"),
        "portal_orange": ("action", "portal_orange"),
        "gravity_gun": ("action", "gravity_gun"),
        "wait": ("wait", "wait"),
    }
    if body in exact:
        kind, action = exact[body]
        return Command(kind, action, max(duration, 0.08), raw)

    if any(word in body for word in _STOP_WORDS):
        return Command("stop", "stop", 0.0, raw)
    if any(word in body for word in _RESUME_WORDS):
        return Command("resume", "resume", 0.0, raw)
    if any(word in body for word in _WAIT_WORDS):
        return Command("wait", "wait", max(duration, 1.2), raw)

    if any(word in body for word in _PUZZLE_WORDS):
        return Command("mode", "puzzle", max(duration, 30.0), raw)
    if any(word in body for word in _FIGHT_WORDS) and not any(
            word in body for word in ("这个", "那只", "前面")):
        return Command("mode", "fight", max(duration, 20.0), raw)
    if any(word in body for word in _EXPLORE_WORDS) and not any(
            word in body for word in ("走", "跑", "冲")):
        return Command("mode", "explore", max(duration, 20.0), raw)

    # Look directions first: "往右看" is a turn, not a strafe.
    if "看" in body or "转" in body or "视角" in body:
        if "左" in body:
            return Command("look_at", "turn_left", max(duration, 0.35), raw)
        if "右" in body:
            return Command("look_at", "turn_right", max(duration, 0.35), raw)
        if "上" in body or "头顶" in body:
            return Command("look_at", "look_up", max(duration, 0.3), raw)
        if "下" in body:
            return Command("look_at", "look_down", max(duration, 0.3), raw)

    # Portal gun verbs before generic fire: "打蓝门" contains "打".
    if any(word in body for word in _MOVE_WORDS["portal_blue"]):
        return Command("action", "portal_blue", 0.0, raw)
    if any(word in body for word in _MOVE_WORDS["portal_orange"]):
        return Command("action", "portal_orange", 0.0, raw)
    if any(word in body for word in _MOVE_WORDS["gravity_gun"]):
        return Command("action", "gravity_gun", 0.2, raw)

    for action in ("weapon_pistol", "weapon_shotgun", "weapon_smg", "weapon_crowbar"):
        if any(word in body for word in _MOVE_WORDS[action]):
            return Command("action", action, 0.15, raw)

    # Movement / generic actions.  Longer phrases first so "往前走" wins over
    # the bare "走" and "蹲下" over "下".
    for action in ("jump", "crouch", "sprint", "use", "reload", "flashlight",
                   "alt_fire", "fire", "left", "right", "back", "forward"):
        if any(word in body for word in _MOVE_WORDS[action]):
            hold = 0.12 if action in ("fire", "alt_fire", "jump", "use", "reload") \
                else max(0.15, duration)
            return Command("move" if action in ("forward", "back", "left", "right") else "action",
                           action, hold, raw)

    return Command("action", "", 0.0, raw, recognized=False)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class PlayPolicy:
    """Small reactive state machine shared by all supported games."""

    def __init__(
        self,
        game: catalog.GameProfile,
        *,
        look: LookCalibration | None = None,
        key_overrides: Mapping[str, str] | None = None,
        seed: int = 7,
    ) -> None:
        self.game = game
        self.look = look or LookCalibration()
        self.keys = {action: game.key(action, key_overrides) for action in game.keys}
        if key_overrides:
            self.keys.update({k: v for k, v in key_overrides.items() if v})
        self._random = random.Random(seed)
        self.state = PolicyState.WAITING
        self.directive: Directive | None = None
        self._forced_until = 0.0
        self._forced_mode = ""
        self._last_hash = 0
        self._same_scene_since = time.time()
        self._last_motion_at = time.time()
        self._last_use_at = 0.0
        self._last_fire_at = 0.0
        self._last_look_at = 0.0
        self._stuck_level = 0
        self._stuck_since = 0.0
        self._portal_phase = 0
        self._explore_phase = 0
        self._explore_turn = 1
        self._last_intent = ""
        self.ticks = 0

    # -- configuration ---------------------------------------------------

    def set_game(self, game: catalog.GameProfile, *, key_overrides=None) -> None:
        self.game = game
        self.keys = {action: game.key(action, key_overrides) for action in game.keys}
        if key_overrides:
            self.keys.update({k: v for k, v in key_overrides.items() if v})
        self.reset()

    def reset(self) -> None:
        self.state = PolicyState.WAITING
        self.directive = None
        self._forced_until = 0.0
        self._forced_mode = ""
        self._last_hash = 0
        self._same_scene_since = time.time()
        self._last_motion_at = time.time()
        self._stuck_level = 0
        self._stuck_since = 0.0
        self._portal_phase = 0
        self._explore_phase = 0
        self.ticks = 0

    def set_directive(self, directive: Directive | None) -> None:
        self.directive = directive
        if directive is not None and directive.mode in ("puzzle", "fight", "explore", "vehicle"):
            self._forced_mode = directive.mode
            self._forced_until = time.time() + (directive.seconds or 30.0)

    def clear_directive(self) -> None:
        self.directive = None
        self._forced_mode = ""
        self._forced_until = 0.0

    # -- helpers ---------------------------------------------------------

    def _key(self, action: str) -> str:
        return self.keys.get(action, "") or ""

    def _look_ops(self, ndc_x: float, ndc_y: float) -> list[Op]:
        dx, dy = self.look.counts_for_delta(ndc_x, ndc_y)
        return [Op("look", dx=dx, dy=dy)]

    def _turn_ops(self, degrees: float) -> list[Op]:
        return [Op("look", dx=self.look.turn_counts(degrees), dy=0.0)]

    def _tap(self, action: str, *, hold: float = 0.08) -> list[Op]:
        key = self._key(action)
        if not key:
            return []
        return [Op("tap", key=key, seconds=hold)]

    def _hold(self, action: str, seconds: float) -> list[Op]:
        key = self._key(action)
        if not key:
            return []
        return [Op("hold", key=key, seconds=seconds)]

    def _fire(self, *, alt: bool = False, hold: float = 0.14) -> list[Op]:
        action = "alt_attack" if alt else "attack"
        return self._tap(action, hold=hold)

    # -- command execution ------------------------------------------------

    def command_ops(self, command: Command) -> list[Op]:
        """Turn a parsed command into primitives (one tick's worth)."""
        action = command.action
        seconds = command.seconds
        if command.kind == "stop":
            return []
        if command.kind == "wait":
            return [Op("wait", seconds=max(0.5, seconds))]
        if command.kind == "look_at":
            if action == "turn_left":
                return self._turn_ops(-max(15.0, min(160.0, seconds * 90.0)))
            if action == "turn_right":
                return self._turn_ops(max(15.0, min(160.0, seconds * 90.0)))
            if action == "look_up":
                return [Op("look", dx=0.0, dy=-self.look.turn_counts(25.0))]
            if action == "look_down":
                return [Op("look", dx=0.0, dy=self.look.turn_counts(25.0))]
            return []
        if command.kind == "move":
            mapping = {"forward": 1.0, "back": -1.0, "left": -1.0, "right": 1.0}
            if action in mapping:
                key = self._key(action)
                return [Op("hold", key=key, seconds=max(0.12, seconds))] if key else []
        if command.kind == "action":
            if action == "fire":
                return self._fire(hold=0.16)
            if action == "alt_fire":
                return self._fire(alt=True, hold=0.16)
            if action == "portal_blue":
                return self._tap("portal_blue", hold=0.06) or self._fire()
            if action == "portal_orange":
                return self._tap("portal_orange", hold=0.06) or self._fire(alt=True)
            if action == "use":
                return self._tap("use", hold=0.12)
            if action == "jump":
                return self._tap("jump", hold=0.12)
            if action == "crouch":
                return [Op("hold", key=self._key("crouch"), seconds=max(0.4, seconds))]
            if action == "sprint":
                return self._hold("sprint", max(0.3, seconds))
            if action == "reload":
                return self._tap("reload", hold=0.1)
            if action == "flashlight":
                return self._tap("flashlight", hold=0.08)
            if action == "gravity_gun":
                return self._tap("gravity_gun", hold=0.12)
            if action.startswith("weapon_"):
                slot = {
                    "weapon_pistol": "weapon_3" if self.game.family != "hl1" else "weapon_2",
                    "weapon_shotgun": "weapon_5" if self.game.family != "hl1" else "weapon_3",
                    "weapon_smg": "weapon_4",
                    "weapon_crowbar": "weapon_1",
                }.get(action, "")
                return self._tap(slot, hold=0.12) if slot else []
        return []

    # -- main decision ----------------------------------------------------

    def decide(
        self,
        features: FrameFeatures | None,
        hud: HudState | None,
        *,
        now: float | None = None,
        menu: bool = False,
        playing: bool = True,
    ) -> Decision:
        now = time.time() if now is None else float(now)
        self.ticks += 1
        hud = hud or HudState()

        if not playing:
            self.state = PolicyState.WAITING
            return Decision(self.state, "还没看到游戏画面，我先等着。", [], ["no-game"], 0.2)

        if menu:
            self.state = PolicyState.MENU
            return Decision(
                self.state,
                "游戏停在菜单/暂停画面，我先不按键。",
                [],
                ["menu"],
                0.8,
            )

        if features is None or features.black:
            self.state = PolicyState.BLIND
            return Decision(
                self.state,
                "截到的画面是全黑的，我不乱按键，先确认游戏还在前台。",
                [],
                ["blind"],
                0.9,
            )

        self._track_scene(features, now)

        # One-shot command from the user/LLM wins for exactly one tick.
        directive = self.directive
        if directive is not None and directive.expired(now):
            self.directive = None
            directive = None
        if directive is not None and directive.command is not None:
            command = directive.command
            self.directive = None
            if command.kind == "stop":
                self.state = PolicyState.PAUSED
                return Decision(self.state, "好，我停手，把操作还给你。", [], ["stop-command"], 1.0)
            ops = self.command_ops(command)
            if ops:
                return Decision(
                    self.state,
                    f"照你说的做：{command.text or command.action}。",
                    ops,
                    [f"command:{command.action}"],
                    0.9,
                )

        mode = ""
        if self._forced_mode and now < self._forced_until:
            mode = self._forced_mode
        if directive is not None and directive.mode in ("puzzle", "fight", "explore", "vehicle", "wait"):
            mode = directive.mode

        # Combat has priority unless an explicit puzzle mode says otherwise.
        combat_evidence = self._combat_evidence(features, hud)
        if mode != "puzzle" and combat_evidence:
            return self._combat(features, hud, now, combat_evidence)

        if mode == "fight":
            return self._combat(features, hud, now, ["forced"])

        if self.game.family == "portal" or mode == "puzzle":
            return self._puzzle(features, hud, now)

        return self._explore(features, hud, now)

    # -- scene tracking ---------------------------------------------------

    def _track_scene(self, features: FrameFeatures, now: float) -> None:
        if features.scene_hash != self._last_hash:
            self._last_hash = features.scene_hash
            self._same_scene_since = now
            if self._stuck_level:
                self._stuck_level = 0
        if features.motion > 0.0025 or features.motion_strength > 0.01:
            self._last_motion_at = now
            if self._stuck_level and now - self._stuck_since > 4.0:
                self._stuck_level = 0

    def _combat_evidence(self, features: FrameFeatures, hud: HudState) -> list[str]:
        evidence: list[str] = []
        if hud.damage_flash:
            evidence.append("正在掉血")
        if features.crosshair and features.crosshair_red > 0.20:
            evidence.append("准星发红")
        # A moving world (tram, vehicle, camera pan) also produces a large block
        # difference.  Only treat motion as an enemy when it is *localised* in
        # the centre while the whole frame is otherwise fairly still.
        if (features.motion_strength > 0.09
                and features.motion < 0.018
                and 0.30 < features.motion_x < 0.70
                and 0.30 < features.motion_y < 0.75):
            evidence.append("视野中央有局部移动")
        if hud.low_health_guess:
            evidence.append("血量区域偏红")
        return evidence

    # -- combat -----------------------------------------------------------

    def _combat(self, features: FrameFeatures, hud: HudState, now: float,
                evidence: list[str]) -> Decision:
        self.state = PolicyState.COMBAT
        red = features.crosshair and features.crosshair_red > 0.20
        ops: list[Op] = []
        intent = "看到目标了，先对准再点射，不扫射。"
        if red:
            ops.extend(self._fire(hold=0.16))
            if self._random.random() < 0.35:
                ops.append(Op("tap", key=self._key("left"), seconds=0.12))
            intent = "准星已经发红，目标就在准星上，短点射。"
            confidence = 0.85
        elif features.motion_strength > 0.02:
            ops.extend(self._look_ops(features.motion_x, features.motion_y))
            intent = "画面中间有东西在动，我先把视角转过去瞄准。"
            confidence = 0.6
        elif hud.damage_flash:
            # Being shot from somewhere we cannot see: back off while facing
            # the centre, then return fire.
            ops.extend(self._hold("back", 0.45))
            ops.extend(self._fire(hold=0.12))
            intent = "正在挨打，先退一步，边退边还击。"
            confidence = 0.55
        else:
            ops.extend(self._fire(hold=0.10))
            intent = "有战斗迹象，先压一轮火力。"
            confidence = 0.4

        if hud.low_health_guess and self._random.random() < 0.5:
            ops.extend(self._hold("back", 0.35))
            intent += "血量看着偏低，先拉开距离。"

        if (now - self._last_fire_at) < 0.18:
            ops = [op for op in ops if op.kind != "tap" or op.key not in (
                self._key("attack"), self._key("alt_attack"))]
        self._last_fire_at = now
        return Decision(self.state, intent, ops, evidence, confidence)

    # -- exploration ------------------------------------------------------

    def _explore(self, features: FrameFeatures, hud: HudState, now: float) -> Decision:
        self.state = PolicyState.EXPLORE
        still_for = now - self._last_motion_at
        if still_for > 2.2 and features.scene_hash == self._last_hash:
            return self._stuck(features, hud, now, still_for)

        self._explore_phase += 1
        ops: list[Op] = []
        intent = "沿走廊向前推进，边走边留意门和敌人。"
        reasons = ["explore"]

        # A gentle look sweep: forward bursts, then a short glance left/right.
        if self._explore_phase % 6 == 0:
            turn = 18.0 * self._explore_turn
            self._explore_turn *= -1
            ops.extend(self._turn_ops(turn))
            intent = "先扫一眼两侧，确认没有岔路和敌人，再继续走。"
            reasons.append("look-sweep")
        else:
            ops.extend(self._hold("forward", 0.75 + self._random.random() * 0.45))
            if self.game.supports_feature("vehicle") and self._random.random() < 0.1:
                ops.extend(self._tap("use", hold=0.08))
                reasons.append("vehicle-exit-check")
        # Doors and buttons: a short E press when the view has been quiet.
        if still_for > 0.6 and (now - self._last_use_at) > 2.8:
            ops.extend(self._tap("use", hold=0.12))
            self._last_use_at = now
            reasons.append("use-door")
            intent = "前面像是门/按钮，我按一下 E 试试。"
        if hud.subtitle:
            reasons.append("subtitle-present")
        return Decision(self.state, intent, ops, reasons, 0.55)

    def _stuck(self, features: FrameFeatures, hud: HudState, now: float,
               still_for: float) -> Decision:
        self.state = PolicyState.STUCK
        if not self._stuck_since:
            self._stuck_since = now
        if now - self._stuck_since > 5.0:
            self._stuck_level = min(3, self._stuck_level + 1)
            self._stuck_since = now
        level = self._stuck_level
        ops: list[Op] = []
        if level == 0:
            ops.extend(self._turn_ops(45.0 * self._explore_turn))
            self._explore_turn *= -1
            intent = "画面一直没变化，我先转个方向看看是不是走错了。"
            reasons = ["stuck:look"]
        elif level == 1:
            ops.extend(self._hold("back", 0.5))
            ops.extend(self._tap("jump", hold=0.12))
            intent = "像是被卡住了，我退半步再跳一下试试。"
            reasons = ["stuck:back-jump"]
        elif level == 2:
            ops.extend(self._hold("left", 0.7))
            ops.extend(self._hold("forward", 0.8))
            intent = "换个方向绕一下，不跟这堵墙较劲。"
            reasons = ["stuck:strafe"]
        else:
            ops.extend(self._tap("use", hold=0.15))
            ops.extend(self._hold("crouch", 0.45))
            ops.extend(self._tap("jump", hold=0.12))
            intent = "试试按 E、蹲下再跳，可能是门或者矮通道。"
            reasons = ["stuck:interact"]
        return Decision(self.state, intent, ops, reasons, 0.45)

    # -- Portal puzzles ---------------------------------------------------

    def _puzzle(self, features: FrameFeatures, hud: HudState, now: float) -> Decision:
        self.state = PolicyState.PUZZLE
        self._portal_phase = (self._portal_phase + 1) % 8
        phase = self._portal_phase
        ops: list[Op] = []
        intent = "解谜模式：先找白色面板，再把蓝门和橙门配对。"
        reasons = [f"puzzle-phase:{phase}"]

        if features.crosshair and features.crosshair_blue > 0.18:
            ops.extend(self._tap("portal_blue", hold=0.06))
            intent = "准星发蓝，这个面能放蓝门，先打蓝门。"
        elif features.crosshair and features.crosshair_orange > 0.18:
            ops.extend(self._tap("portal_orange", hold=0.06))
            intent = "准星发橙，这个面能放橙门，打橙门。"
        elif phase in (0, 1):
            ops.extend(self._turn_ops(30.0))
            intent = "先环顾房间，找能放传送门的白色面板。"
        elif phase == 2:
            if features.white_surface > 0.12:
                ops.extend(self._tap("portal_blue", hold=0.06))
                intent = "画面里有大片白色面板，朝它打一扇蓝门。"
            else:
                ops.extend(self._turn_ops(20.0))
                intent = "这一侧没有明显白面，继续转视角找。"
        elif phase == 3:
            ops.extend(self._turn_ops(65.0))
            intent = "换个角度，找第二面能放橙门的墙。"
        elif phase == 4:
            if features.white_surface > 0.10:
                ops.extend(self._tap("portal_orange", hold=0.06))
                intent = "这一面也像可放置面，打橙门。"
            else:
                ops.extend(self._tap("portal_orange", hold=0.06))
                intent = "先补一扇橙门，再看看通路有没有变化。"
        elif phase == 5:
            ops.extend(self._hold("forward", 0.7))
            intent = "往传送门方向走几步，看看能不能穿过去。"
        elif phase == 6:
            ops.extend(self._tap("use", hold=0.12))
            intent = "附近可能有按钮或方块，按 E 互动一下。"
        else:
            ops.extend(self._hold("jump", 0.12))
            intent = "试试跳一下/跳进传送门，别在死角站着。"

        if hud.damage_flash:
            ops.extend(self._hold("back", 0.35))
            intent += " 挨打了，先退开。"
        return Decision(self.state, intent, ops, reasons, 0.45)
