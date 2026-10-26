# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Regenerate advisor lines while the game is running.

## What changed, and why this exists

The engine reads a sound's wav **from disk at playback time**. That is not an
assumption any more: it was measured by overwriting all 248 slots mid-session
with one unmistakable sentence, then triggering an advisor line in the running
game, which played the new audio. The `.asset` mapping was deliberately left
untouched, so no asset reload, console command or window focus is involved --
only the bytes behind an already-registered name change.

That combination is the useful half of "real-time voice in game":

* the sound **name** is frozen at load, so we cannot add new ones;
* the **file** is not, so we can change what any existing name will say.

## What is still impossible, and why

The game plays the advisor line within ~100 ms of the event, while generating a
line costs a 2-4 s model call plus TTS. So a line generated *because* of an event
can never be the line that event plays. Anyone claiming per-event real-time
in-game voice is skipping that race.

## What this does instead

Each advisor soundeffect is a *pool* of K takes that the game picks from at
random. So after an event fires we regenerate **one** pool slot for that event
and advance a per-event rotation. Over K occurrences every slot has been
rewritten with a line generated at play time from the live situation; from then
on, any occurrence has a (K-1)/K chance of playing a line written after the
previous one.

The one-line budget per occurrence is deliberate. An event lands every 10-30
seconds and a pool is 4 wide, so regenerating the whole pool would be 4 model
calls plus 4 TTS calls per event -- far past what the free TTS endpoint tolerates
and far more than the perceived benefit. Rotating spends one call per occurrence
and still reaches full coverage within four.
"""

from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import voicepack
from .audiowav import prepare_for_game, wav_bytes_mono16
from .gamelog import GameEvent
from .nekoconfig import find_stellaris_user_dir
from .tts import TtsSettings, synthesize_with_retry

logger = logging.getLogger("neko_stellaris.livevoice")

# Which pool each bridge goal refills. This is the table that makes a generated
# line land on the right event: an advisor soundeffect is what the game calls, so
# a goal with no entry here simply gets no live lines rather than a wrong one.
GOAL_TO_EFFECT: dict[str, str] = {
    "war_declared_on_us": "advisor_notification_enemy_declared_war",
    "war_declared_by_us": "advisor_notification_player_declare_war",
    "war_entered_as_late_joiner": "advisor_notification_generic_war_declare",
    "war_ended": "advisor_notification_generic_war_declare",
    "war_won": "advisor_notification_generic_war_declare",
    "war_lost": "advisor_notification_generic_war_declare",
    "status_quo_signed": "advisor_notification_generic_war_declare",
    "fleet_destroyed": "advisor_notification_fleet_destroyed",
    "fleet_destroyed_by_us": "advisor_notification_fleet_destroyed",
    "battle_won": "advisor_notification_hostile_fleet_engaged",
    "battle_lost": "advisor_notification_hostile_fleet_engaged",
    "battle_started": "advisor_notification_hostile_fleet_detected",
    "colony_established": "advisor_notification_colony_established",
    "colonization_started": "advisor_notification_colonization_progress",
    "colony_destroyed": "advisor_notification_colony_ship_destroyed_01",
    "building_completed": "advisor_notification_construction_complete",
    "building_upgraded": "advisor_notification_construction_complete",
    "district_completed": "advisor_notification_construction_complete",
    "zone_completed": "advisor_notification_construction_complete",
    "planet_rebellion": "advisor_notification_planet_in_revolt",
    "starbase_occupied": "advisor_notification_hostile_station_engaged",
    "starbase_destroyed": "advisor_notification_spaceport_lost",
    "starbase_disabled": "advisor_notification_spaceport_under_attack",
    "system_lost": "advisor_notification_frontier_outpost_lost",
    "waystation_lost": "advisor_notification_frontier_outpost_lost",
    "mining_research_station_destroyed": "advisor_notification_mining_station_lost",
    "tech_researched": "advisor_notification_research_complete",
    "survey_completed_system": "advisor_notification_system_survey_complete",
    "survey_completed_planet": "advisor_notification_system_survey_complete",
    "special_project_completed_debris": "advisor_notification_debris_analysed",
    "special_project_completed_modification": "advisor_notification_species_modified",
    "special_project_completed_uplift": "advisor_notification_pre_sapient_uplifted",
    "special_project_completed_archaeology": "advisor_notification_special_project_complete",
    "special_project_completed_astral_rift": "advisor_notification_special_project_complete",
    "first_contact_with_empire": "advisor_notification_alien_vessel_encountered",
    # Stellaris has no dedicated first-contact advisor pool; the pack therefore
    # has no such pool either, and mapping this to a name that does not exist
    # would silently drop the event. Reusing the vessel-encounter pool keeps the
    # event audible.
    "first_contact_finished": "advisor_notification_alien_vessel_encountered",
    "communications_established": "advisor_notification_alien_empire_communication_01",
    "federation_joined_or_formed": "advisor_notification_federation_formed",
    "federation_left": "advisor_notification_federation_formed",
    "federation_new_leader": "advisor_notification_federation_formed",
    "leader_died": "advisor_notification_situation_log",
    "new_ruler": "advisor_notification_situation_log",
    "leader_hired": "advisor_notification_situation_log",
    "megastructure_completed": "advisor_notification_special_project_complete",
    "megastructure_build_start": "advisor_notification_construction_complete",
    "megastructure_upgraded": "advisor_notification_construction_complete",
    "empire_destroyed": "advisor_notification_hostile_fleet_engaged",
}

# Vanilla event-selection lines carry only a namespace, so they can only reach a
# generic pool. Kept tiny on purpose: guessing a specific pool from a namespace
# would put a line about a war on a research notification.
NAMESPACE_TO_EFFECT: dict[str, str] = {
    "crisis": "advisor_notification_situation_log",
    "war_in_heaven": "advisor_notification_generic_war_declare",
    "fallen_empires_awakening": "advisor_notification_situation_log",
    "fallen_machine_empire": "advisor_notification_situation_log",
    "galcom": "advisor_notification_incoming_transmission_01",
    "federation": "advisor_notification_federation_formed",
    "marauder": "advisor_notification_hostile_fleet_detected",
}


class LiveVoiceError(RuntimeError):
    """Raised when a live line cannot be produced."""


def effect_for_event(event: GameEvent) -> str | None:
    """Which advisor pool an event should refill, or ``None`` if unknown."""
    if event.kind == "bridge":
        return GOAL_TO_EFFECT.get(event.goal)
    if event.kind == "player_event":
        namespace = event.event_id.split(".", 1)[0] if "." in event.event_id else ""
        return NAMESPACE_TO_EFFECT.get(namespace)
    return None


# --------------------------------------------------------------------------- #
# Inventory: what the *installed* pack actually contains
# --------------------------------------------------------------------------- #

_EFFECT_BLOCK_RE = re.compile(r"soundeffect\s*=\s*\{(?P<body>[^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")


class SlotInventory:
    """Maps each advisor soundeffect to the pool of sound names behind it.

    Prefers the pack's own ``pack.json``. The fallback parses the soundgroup,
    which cannot recover the mapping on its own: override soundeffects are named
    ``<mod_id>_<vanilla effect>``, so a vanilla name is not a key in the asset
    anywhere. Getting this wrong is invisible -- every lookup returns an empty
    pool and the feature simply never fires -- which is why the manifest exists
    and why the fallback warns instead of quietly returning nothing.
    """

    def __init__(self, pools: dict[str, list[str]], vo_dir: Path, source: str):
        self.pools = pools
        self.vo_dir = vo_dir
        self.source = source

    @classmethod
    def load(cls, mod_dir: str | Path) -> "SlotInventory":
        mod = Path(mod_dir)
        vo = voicepack.vo_dir(mod)
        manifest = mod / voicepack.MANIFEST_NAME
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                pools = {str(k): [str(s) for s in v]
                         for k, v in (data.get("pools") or {}).items() if v}
                if pools:
                    return cls(pools, vo, f"{voicepack.MANIFEST_NAME}（{len(pools)} 个池）")
            except Exception as exc:  # noqa: BLE001
                logger.warning("pack.json 无法解析，退回解析 .asset: %s", exc)

        pools = cls._from_asset(mod)
        if pools:
            logger.warning(
                "语音包没有 %s，只能从 .asset 恢复 %d 个池；"
                "单条效果（池宽 1）无法恢复。重新生成一次语音包即可获得清单。",
                voicepack.MANIFEST_NAME, len(pools))
        return cls(pools, vo, f".asset 回退（{len(pools)} 个池）")

    @staticmethod
    def _from_asset(mod: Path) -> dict[str, list[str]]:
        assets = sorted((mod / "sound").glob("*.asset"))
        if not assets:
            raise LiveVoiceError(f"找不到语音包资产文件：{mod / 'sound'}")
        text = "\n".join(a.read_text(encoding="utf-8", errors="replace") for a in assets)

        overrides: dict[str, str] = {}
        block = re.search(r"soundeffectoverrides\s*=\s*\{(?P<body>[^{}]*)\}", text)
        if block:
            for line in block.group("body").splitlines():
                m = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*([A-Za-z0-9_]+)\s*$", line)
                if m:
                    overrides[m.group(1)] = m.group(2)

        ours: dict[str, list[str]] = {}
        for match in _EFFECT_BLOCK_RE.finditer(text):
            body = match.group("body")
            name = re.search(r"^\s*name\s*=\s*(\S+)", body, re.M)
            if not name:
                continue
            sounds = re.findall(r"^\s*sound\s*=\s*(\S+)", body, re.M)
            sounds += re.findall(r"sound\s*=\s*(\S+?)\s+weight", body)
            if sounds:
                ours[name.group(1)] = sorted(dict.fromkeys(sounds))

        return {vanilla: ours[override] for vanilla, override in overrides.items()
                if ours.get(override)}

    def pool(self, effect: str) -> list[str]:
        return list(self.pools.get(effect) or [])

    def has_slot(self, sound_name: str) -> bool:
        return (self.vo_dir / f"{sound_name}.wav").is_file()

    def summary(self) -> str:
        widths = sorted(len(p) for p in self.pools.values())
        if not widths:
            return "0 个池（清单与 .asset 都没能恢复映射）"
        return (f"{len(self.pools)} 个 soundeffect 池，成员数 {widths[0]}~{widths[-1]}，"
                f"来源 {self.source}")


# --------------------------------------------------------------------------- #
# Game state: enough context for a line that sounds like it is about now
# --------------------------------------------------------------------------- #

@dataclass
class GameSnapshot:
    empire: str = ""
    date: str = ""
    version: str = ""

    def describe(self) -> str:
        bits = [b for b in (self.empire, self.date) if b]
        return " / ".join(bits)

    def as_prompt_line(self) -> str:
        if not self.describe():
            return ""
        return f"当前局势：帝国「{self.empire or '未知'}」，日期 {self.date or '未知'}。"


def read_snapshot(stellaris_user_dir: str | Path | None = None) -> GameSnapshot:
    """Newest save's metadata: empire name, in-game date, game version.

    The ``meta`` member of a ``.sav`` is a small plain-text header inside the zip,
    so this stays cheap enough to run on every refresh -- unlike ``gamestate``,
    which is tens of megabytes of Clausewitz text.
    """
    user = Path(stellaris_user_dir) if stellaris_user_dir else find_stellaris_user_dir()
    if user is None:
        return GameSnapshot()
    saves = sorted((user / "save games").rglob("*.sav"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not saves:
        return GameSnapshot()
    try:
        with zipfile.ZipFile(saves[0]) as archive:
            raw = archive.read("meta").decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取存档 meta 失败: %s", exc)
        return GameSnapshot()
    return GameSnapshot(
        empire=(re.search(r'^\s*name\s*=\s*"([^"]*)"', raw, re.M) or [None, ""])[1],
        date=(re.search(r'^\s*date\s*=\s*"([^"]*)"', raw, re.M) or [None, ""])[1],
        version=(re.search(r'^\s*version\s*=\s*"([^"]*)"', raw, re.M) or [None, ""])[1],
    )


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #

_LINE_SYSTEM = """你在为一个游戏内语音助手写台词。这个助手是《N.E.K.O》里的猫娘角色 YUI（林悠怡），自称「本喵」，称玩家为「碳基生物」或「你」。她嘴上傲娇、其实很关心玩家，说话简短口语化。

要求：
1. **只输出台词本身**，不要引号、不要解释、不要编号、不要任何前后缀。
2. **8~18 个汉字，一句话**。这是游戏里插播的语音，长了会挡操作。
3. 不要出现游戏专有名词和数值（不要写"科研点数""合金""舰队规模"），只表达情绪和陪伴。
4. 不要 emoji、颜文字、括号动作、markdown。
5. 语气要贴合事件的情绪方向（被宣战=紧张护主，舰队被毁=沉痛愤怒，科研完成=轻快邀功）。"""

_LINE_USER = """事件：{meaning}
触发它的游戏内事实：{facts}
{state}

参考风格（这是已有台词的语气，别照抄）：
{examples}

写 1 条新台词。"""


@dataclass
class LineRequest:
    effect: str
    meaning: str
    facts: str
    snapshot: GameSnapshot
    examples: list[str] = field(default_factory=list)


class LineGenerator:
    """Turns a game event into one short line, via the host model gateway."""

    def __init__(self, model_usage: str = "analysis"):
        self.model_usage = model_usage
        self.calls = 0
        self.failures = 0
        self.last_error = ""

    async def generate(self, ctx: Any, request: LineRequest) -> str:
        client = await ctx.models.get_client()
        prompt = _LINE_USER.format(
            meaning=request.meaning or request.effect,
            facts=request.facts or "（没有更多细节）",
            state=request.snapshot.as_prompt_line(),
            examples="\n".join(f"- {line}" for line in request.examples[:4]) or "- （无）",
        )
        self.calls += 1
        try:
            response = await client.chat.completions.create(
                model=self.model_usage,
                messages=[
                    {"role": "system", "content": _LINE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=64,
            )
        except Exception as exc:  # noqa: BLE001 - gateway errors are open-ended
            self.failures += 1
            self.last_error = str(exc)
            raise LiveVoiceError(f"模型调用失败: {exc}") from exc

        text = (response.choices[0].message.content or "").strip()
        return sanitize_line(text)


_BAD_CHARS = re.compile(r"[`*_#\[\]{}<>|]|[\U0001F300-\U0001FAFF]|\s+")


def sanitize_line(text: str) -> str:
    """Make a model reply safe to hand to TTS and to the game.

    Deliberately strict: a stray markdown fence or emoji gets read aloud by the
    TTS service, and an over-long line blocks the game's UI, so anything that
    survives this is something we would be happy to hear mid-battle.
    """
    line = text.strip().strip('"\u201c\u201d\u2018\u2019\' ')
    line = line.splitlines()[0] if line else ""
    line = _BAD_CHARS.sub("", line)
    line = line.strip("，。、；：!?！？ ")
    if len(line) > 30:
        line = line[:30]
    return line


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

@dataclass
class LiveVoiceConfig:
    enabled: bool = True
    model_usage: str = "analysis"
    # Model + TTS calls allowed per minute. This is the throttle that keeps a busy
    # war from turning into a continuous API bill.
    budget_per_minute: int = 4
    # Hard ceiling on queued events so an unreadable log cannot grow unbounded.
    max_pending: int = 64
    min_seconds_between: float = 8.0


@dataclass
class LiveVoiceStats:
    generated: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    last_line: str = ""
    last_slot: str = ""
    last_error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "written": self.written,
            "skipped": self.skipped,
            "failed": self.failed,
            "last_line": self.last_line,
            "last_slot": self.last_slot,
            "last_error": self.last_error,
        }


class LiveVoiceEngine:
    """Queue of events to refill, drained under a rate budget."""

    def __init__(self, mod_dir: str | Path, lines_source: dict[str, list[str]],
                 config: LiveVoiceConfig | None = None,
                 tts: TtsSettings | None = None,
                 snapshot_fn: Callable[[], GameSnapshot] | None = None):
        self.mod_dir = Path(mod_dir)
        self.config = config or LiveVoiceConfig()
        self.tts = tts
        self.inventory = SlotInventory.load(self.mod_dir)
        self.lines_source = lines_source
        self.generator = LineGenerator(self.config.model_usage)
        self.stats = LiveVoiceStats()
        self._queue: deque[tuple[str, str, str]] = deque()   # (effect, meaning, facts)
        self._rotation: dict[str, int] = {}
        self._last_emit = 0.0
        self._emit_times: deque[float] = deque()
        self._snapshot_fn = snapshot_fn or (lambda: read_snapshot())

    # -- intake ------------------------------------------------------------ #

    def observe(self, event: GameEvent) -> bool:
        """Queue a refill for an event. Returns False when it is not actionable."""
        effect = effect_for_event(event)
        if not effect:
            self.stats.skipped += 1
            return False
        pool = self.inventory.pool(effect)
        if not pool:
            # The effect exists in our mapping but not in the installed pack.
            self.stats.skipped += 1
            return False
        if len(self._queue) >= self.config.max_pending:
            self._queue.popleft()
        meaning = _MEANINGS.get(effect, effect)
        self._queue.append((effect, meaning, _facts_for(event)))
        return True

    # -- budget ------------------------------------------------------------ #

    def _can_spend(self, now: float) -> bool:
        if now - self._last_emit < self.config.min_seconds_between:
            return False
        cutoff = now - 60.0
        while self._emit_times and self._emit_times[0] < cutoff:
            self._emit_times.popleft()
        return len(self._emit_times) < max(1, self.config.budget_per_minute)

    def pending(self) -> int:
        return len(self._queue)

    # -- work -------------------------------------------------------------- #

    async def drain(self, ctx: Any, *, now: float | None = None) -> int:
        """Generate and write one line per free budget slot. Returns lines written."""
        now = time.time() if now is None else now
        written = 0
        while self._queue and self._can_spend(now):
            effect, meaning, facts = self._queue.popleft()
            try:
                line = await self.generator.generate(ctx, LineRequest(
                    effect=effect,
                    meaning=meaning,
                    facts=facts,
                    snapshot=self._snapshot_fn(),
                    examples=self.lines_source.get(effect, [])[:4],
                ))
            except LiveVoiceError as exc:
                self.stats.failed += 1
                self.stats.last_error = str(exc)
                logger.warning("实时台词生成失败（%s）: %s", effect, exc)
                return written
            self.stats.generated += 1
            if not line:
                self.stats.failed += 1
                self.stats.last_error = "模型返回空台词"
                continue
            if await self._write(ctx, effect, line):
                written += 1
            self._last_emit = now
            self._emit_times.append(now)
            now = time.time()
        return written

    def _next_slot(self, effect: str) -> str:
        pool = self.inventory.pool(effect)
        index = self._rotation.get(effect, 0) % len(pool)
        self._rotation[effect] = index + 1
        return pool[index]

    async def _write(self, ctx: Any, effect: str, line: str) -> bool:
        slot = self._next_slot(effect)
        if not self.inventory.has_slot(slot):
            self.stats.failed += 1
            self.stats.last_error = f"槽位文件不存在: {slot}"
            return False
        if self.tts is None:
            self.stats.failed += 1
            self.stats.last_error = "未配置 TTS"
            return False
        try:
            pcm, rate = await synthesize_with_retry(line, self.tts, label=slot)
        except Exception as exc:  # noqa: BLE001
            self.stats.failed += 1
            self.stats.last_error = f"TTS 失败: {exc}"
            return False
        samples, out_rate = prepare_for_game(voicepack.wrap_raw(pcm, rate))
        try:
            voicepack.rewrite_slot(self.mod_dir, sound_name=slot,
                                   wav_payload=wav_bytes_mono16(samples, out_rate))
        except Exception as exc:  # noqa: BLE001
            self.stats.failed += 1
            self.stats.last_error = f"写入槽位失败: {exc}"
            return False
        self.stats.written += 1
        self.stats.last_line = line
        self.stats.last_slot = slot
        logger.info("实时台词 → %s：%s", slot, line)
        return True

    def status(self) -> dict[str, Any]:
        return {
            "inventory": self.inventory.summary(),
            "pending": self.pending(),
            "model_usage": self.config.model_usage,
            "model_calls": self.generator.calls,
            "budget_per_minute": self.config.budget_per_minute,
            **self.stats.as_dict(),
        }


# Human-readable label per effect, reused from the pack dataset's `meaning`.
_MEANINGS: dict[str, str] = {}


def load_meanings(dataset: dict[str, Any]) -> dict[str, str]:
    """effect name -> Chinese meaning, from the bundled advisor dataset."""
    out: dict[str, str] = {}
    for entry in dataset.get("groups", {}).values():
        for item in entry:
            effect = item.get("soundeffect")
            if effect and item.get("meaning"):
                out.setdefault(effect, item["meaning"])
    _MEANINGS.clear()
    _MEANINGS.update(out)
    return out


def _facts_for(event: GameEvent) -> str:
    """The concrete details of an event, for the prompt.

    Values that the engine may have collapsed to empty (the audit found that
    ``[X.GetName]`` sometimes yields nothing and never errors) are dropped rather
    than passed through as ``key=`` noise.
    """
    bits: list[str] = []
    if event.kind == "player_event":
        bits.append(f"事件编号 {event.event_id}")
        bits.append(f"玩家选择第 {event.option} 个选项")
    for key, value in sorted((event.payload or {}).items()):
        if value:
            bits.append(f"{key}={value}")
    if event.game_date:
        bits.append(f"游戏日期 {event.game_date}")
    return "；".join(bits)
