# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Build a Stellaris advisor voice-pack mod from a line table plus TTS.

## Why the mod looks the way it does

The reference mod people usually copy (``绫地宁宁顾问语言``, workshop 2847776264)
is written for **Stellaris 3.4** and its shape no longer exists. In 4.5 an
advisor voice is a *sound group*, and the voice-type file must name that group:

    soundgroup = { name = l_<key> ... }          <- sound/<anything>.asset
    l_<key> = { name = "<loc key>" ... }          <- sound/advisor_voice_types/*.txt

The vanilla proof is ``l_venerable_scientist_vir``, defined in
``sound/vo/chinese/voiceovers_chinese.asset`` — a DLC-added advisor voice that
lives in its own asset file, exactly like ours. Note that paths inside those
files are relative to the ``.asset`` file's own directory.

## Why every sound gets overridden

The default group (``l_english``) carries **no** overrides, so every advisor
sound it reaches resolves to the base definitions in ``sound/sound.asset``,
which point at the English recordings in ``vo/notifications/``. A custom group
that overrides only *some* sounds therefore plays Chinese for the overridden
ones and English for the rest — including mid-sentence, since several
``soundeffect`` entries pick randomly among 3-16 member sounds
(``advisor_clicking_respons_01`` alone has four). So the dataset covers the
complete reachable set (82 names) and the builder refuses to emit a partial
pack.

## Variants

An event like ``advisor_notification_research_complete`` is one ``soundeffect``
containing one sound, so extra takes need two extra pieces, which is exactly
what vanilla's authoritarian group does for its "extra lines": define more
sounds and point the soundeffect at the pool. Concretely, for N lines:

* sound ``..._01`` .. ``..._0N``  (``_01`` already exists; the rest are new)
* ``soundeffectoverrides = { <base se> = <our se> }``
* a ``soundeffect`` listing all N sounds, keeping vanilla's
  ``volume = 0.4 / max_audible = 1 / max_audible_behaviour = fail``

Soundeffect entries that already pool several sounds are left in charge of
randomisation; their members each get a single line and no override is emitted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import ddsicon
from .audiowav import prepare_for_game, wav_bytes_mono16
from .tts import TtsSettings, TtsStats, synthesize_many

logger = logging.getLogger("neko_stellaris.voicepack")

DEFAULT_MOD_ID = "nekko_stellaris_advisor"
DEFAULT_VOICE_GROUP = "l_nekko_stellaris"
DEFAULT_LOC_KEY = "advisor_nekko_stellaris"

# The display name, defined once. The same string appears in the launcher, in the
# game's advisor-voice dropdown and in the standalone export, so three copies
# would be three chances to drift -- and each drift shows up as a different name
# in one of those places.
#
# Two names, not one, because they answer two different questions.
#
# MOD_NAME_* is the *product*: it shows in the launcher, and it becomes the Steam
# Workshop title on upload, where a browser has to work out what the item is from
# the name alone -- hence "顾问语音包" spelled out rather than implied.
#
# VOICE_DISPLAY_* is the *in-game option*, which appears in
# Settings -> Sound -> Advisor Voice beside 灵能 / 军人 / 外交官. That list is
# nothing but short bare names, so the product framing is dropped there and only
# the character is left.
#
# They were a single field until this distinction was asked for, which meant the
# launcher and the game necessarily showed the same string.
MOD_NAME_ZH = "星辰喵语 顾问语音包"
MOD_NAME_EN = "Starlight Meow — Advisor Voice Pack"
VOICE_DISPLAY_ZH = "YUI"
VOICE_DISPLAY_EN = "YUI"

# Source art for the advisor icon and the Workshop thumbnail. Resolved relative
# to this module so it works identically from the N.E.K.O plugin tree and from
# the standalone checkout. Empty or missing falls back to the procedural mark.
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_ICON_SOURCE = ASSET_DIR / "yui_icon_source.png"

# Kept as aliases so existing callers and the CLI's single --name keep working;
# the value they get is the product name.
DEFAULT_NAME_ZH = MOD_NAME_ZH
DEFAULT_NAME_EN = MOD_NAME_EN

# Where the wavs live inside the mod, and how the .asset refers to them.
# Paths in a .asset are relative to the .asset file, and the .asset sits in
# sound/, so "vo/..." resolves to sound/vo/....
VO_SUBDIR = "vo/nekko_stellaris"
# Machine-readable build manifest written next to descriptor.mod. Deliberately
# not a dotfile: it is part of the pack's public surface, not an internal note.
MANIFEST_NAME = "pack.json"
# The mod cover, at the mod root. Named by the descriptors' ``picture=`` line, and
# the only cover a non-Steam mod has.
THUMBNAIL_NAME = "thumbnail.png"
DEFAULT_VARIANTS = 4
SORT_ORDER = 40

# How many takes each group is worth. This is a budget decision, not a format
# one: notification lines are the ones a player hears hundreds of times and are
# worth re-recording, while click acknowledgements are already randomised by
# vanilla across 16 sound names and re-recording those would triple the TTS bill
# for the least noticeable group.
VARIANTS_BY_GROUP: dict[str, int] = {
    "notification": DEFAULT_VARIANTS,
    "tutorial": 2,
    "sample": 1,
    "click": 1,
    "generic": 2,
}

# Vanilla keeps these on every advisor notification soundeffect. Copied
# verbatim: they are what stops a new line from stacking on a playing one.
EFFECT_VOLUME = 0.4
EFFECT_MAX_AUDIBLE = 1
EFFECT_MAX_AUDIBLE_BEHAVIOUR = "fail"


class VoicePackError(RuntimeError):
    """Raised when the pack cannot be assembled from the given inputs."""


@dataclass(frozen=True)
class PackSpec:
    """Identity and layout knobs for the generated mod."""

    mod_id: str = DEFAULT_MOD_ID
    voice_group: str = DEFAULT_VOICE_GROUP
    loc_key: str = DEFAULT_LOC_KEY
    # The product name: launcher list, and the Workshop title on upload.
    name_zh: str = MOD_NAME_ZH
    name_en: str = MOD_NAME_EN
    # The in-game advisor-voice option. Deliberately separate: see MOD_NAME_ZH.
    voice_display_zh: str = VOICE_DISPLAY_ZH
    voice_display_en: str = VOICE_DISPLAY_EN
    mod_version: str = "1.0.1"
    supported_version: str = "4.5.*"
    tags: tuple[str, ...] = ("Sound",)
    max_variants: int = DEFAULT_VARIANTS
    icon_size: int = ddsicon.DEFAULT_SIZE
    # Path to a source illustration for the icon and thumbnail. Defaults to the
    # bundled art; set to "" to use the procedural mark instead, or to a path to
    # use different art without touching code.
    icon_source: str = ""

    def takes_for(self, group: str) -> int:
        """Per-group take budget; ``max_variants`` globally caps it."""
        budget = VARIANTS_BY_GROUP.get(group, 1)
        if self.max_variants > 0:
            budget = min(budget, self.max_variants)
        return max(1, budget)

    def resolved_icon_source(self) -> Path | None:
        """The icon source to use, or ``None`` for the procedural mark.

        An explicit ``icon_source`` wins; otherwise the bundled art is used if it
        is present. Returning ``None`` rather than raising keeps the pack
        buildable in a checkout where the art was never added.
        """
        if self.icon_source:
            path = Path(self.icon_source)
            return path if path.is_file() else None
        return DEFAULT_ICON_SOURCE if DEFAULT_ICON_SOURCE.is_file() else None


@dataclass
class SoundSlot:
    """One advisor sound from the bundled dataset, joined to its authored lines."""

    sound: str
    soundeffect: str
    group: str
    meaning: str
    lines: list[str]


@dataclass
class SoundEffectPlan:
    """A soundeffect the pack has to define itself (because it pools >1 sound)."""

    base_name: str
    override_name: str
    sounds: list[str]


@dataclass
class PackPlan:
    """Everything the pack will contain, computed without touching the network."""

    spec: PackSpec
    clips: list[SoundSlot] = field(default_factory=list)   # one entry per sound name
    effects: list[SoundEffectPlan] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def slot_count(self) -> int:
        """Distinct sound names the pack overrides."""
        return len(self.clips)

    @property
    def clip_count(self) -> int:
        """Total wav files, i.e. slots times their take counts.

        Distinct from ``slot_count`` on purpose: the two numbers differ by the
        variant budget, and reporting slots under the name "clips" made the build
        log claim 82 clips for a 248-file pack.
        """
        return sum(self.coverage.values())

    def summary(self) -> str:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(self.coverage.items()))
        return (f"{self.slot_count} 个声音 / {self.clip_count} 条录音 ({parts}), "
                f"{len(self.effects)} 个 soundeffect 覆盖, "
                f"{len(self.warnings)} 条警告")


def _variant_sound_name(base: str, index: int) -> str:
    """Name for the ``index``-th take (1-based) of a base sound.

    Takes after the first must be *invented*, and an invented sound name has to be
    spelled so vanilla cannot already own it.

    The obvious ``_02``/``_03`` spelling was wrong, and silently so: vanilla
    declares ``advisor_notification_x_02`` itself in ``soundgroups.asset``, as an
    extra reference to the same wav, so that the ethics voices' weighted pools
    (``_01`` weight 1000 + ``_02`` weight 800) can bias a line. Top-level ``sound``
    blocks are global, so our redeclaration of those names lost the collision --
    the engine logged "already scripted" 102 times and kept vanilla's file. Each
    pool then listed one name pointing at our audio and three pointing at vanilla's,
    so three draws in four played the *original* advisor in the middle of a YUI
    pool. That is what "some lines are missing" was.

    A ``_neko_`` infix cannot collide: no vanilla sound name contains it, and the
    suffix is no longer a bare two-digit number that vanilla's alias scheme could
    grow into.
    """
    if index <= 1:
        return base
    stem = base[:-3] if base.endswith("_01") else base
    return f"{stem}_neko_{index:02d}"


def _effect_member_counts(dataset: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in _iter_entries(dataset):
        se = entry.get("soundeffect") or ""
        if se:
            counts[se] = counts.get(se, 0) + 1
    return counts


def _iter_entries(dataset: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for group_name, entries in (dataset.get("groups") or {}).items():
        for entry in entries or []:
            if entry.get("sound"):
                yield {"group": group_name, **entry}


def load_dataset(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not (data.get("groups") or {}):
        raise VoicePackError(f"数据集缺少 groups: {path}")
    return data


def load_lines(path: str | Path) -> dict[str, list[str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("lines")
    if not isinstance(raw, dict):
        raise VoicePackError(f"台词表缺少 lines 对象: {path}")
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            texts = [value]
        elif isinstance(value, list):
            texts = [str(v) for v in value if str(v).strip()]
        else:
            texts = []
        cleaned = [re.sub(r"\s+", " ", t).strip() for t in texts]
        out[str(key)] = [t for t in cleaned if t]
    return out


def plan_pack(dataset: dict[str, Any], lines: dict[str, list[str]],
              spec: PackSpec | None = None) -> PackPlan:
    """Decide the full clip/effect layout without synthesizing anything.

    Kept separate from :func:`build_pack` so the layout can be inspected and
    asserted in tests, and so a build can be previewed before spending minutes
    of TTS calls.
    """
    spec = spec or PackSpec()
    plan = PackPlan(spec=spec)
    member_counts = _effect_member_counts(dataset)
    dataset_sounds = {e["sound"] for e in _iter_entries(dataset)}

    missing = sorted(dataset_sounds - set(lines))
    if missing:
        plan.warnings.append(
            f"{len(missing)} 个声音没有台词，将跳过（游戏会退回原版英文）：{missing[:6]}"
            + ("..." if len(missing) > 6 else "")
        )

    coverage: dict[str, int] = {}
    pool_names: dict[str, list[str]] = {}
    dropped_pooled = 0

    for entry in sorted(_iter_entries(dataset), key=lambda e: e["sound"]):
        sound = entry["sound"]
        authored = lines.get(sound) or []
        if not authored:
            continue
        group = entry["group"]
        effect = entry.get("soundeffect") or ""
        pooled = member_counts.get(effect, 1) > 1

        if pooled:
            # Vanilla's soundeffect already randomises across its members, so this
            # slot contributes exactly one take. Extra authored lines are dropped
            # rather than emitted as files nothing can ever reference.
            takes = authored[:1]
            dropped_pooled += max(0, len(authored) - 1)
        else:
            limit = spec.takes_for(group)
            if spec.max_variants <= 0:
                limit = len(authored)
            takes = authored[:max(1, limit)]

        plan.clips.append(SoundSlot(sound, effect, group, entry.get("meaning", ""), takes))
        coverage[group] = coverage.get(group, 0) + len(takes)

        if effect and len(takes) > 1:
            pool_names.setdefault(effect, [])
            pool_names[effect].extend(
                _variant_sound_name(sound, i) for i in range(1, len(takes) + 1))

    if dropped_pooled:
        # One summary line, not one per sound: a dozen identical notices bury any
        # real warning next to them.
        plan.warnings.append(
            f"{dropped_pooled} 条台词未使用：所属 soundeffect 已由原版随机抽取多条，"
            f"每个声音名只取第 1 条（click/generic/sample 组）"
        )

    # An override is only needed where we actually widened a pool; leaving the
    # single-take effects alone keeps the generated asset small and keeps vanilla
    # in charge of anything we did not touch.
    for effect_name, names in sorted(pool_names.items()):
        if len(names) > 1:
            plan.effects.append(SoundEffectPlan(
                base_name=effect_name,
                override_name=f"{spec.mod_id}_{effect_name}",
                sounds=names,
            ))

    plan.coverage = coverage
    return plan


@dataclass
class ClipJob:
    """A single wav to produce: the sound name it backs and the text to speak."""

    sound: str
    text: str
    group: str
    meaning: str


def clip_jobs(plan: PackPlan) -> list[ClipJob]:
    """Flatten the plan into one job per sound name / take."""
    jobs: list[ClipJob] = []
    for slot in plan.clips:
        for index, text in enumerate(slot.lines, start=1):
            jobs.append(ClipJob(
                sound=_variant_sound_name(slot.sound, index),
                text=text,
                group=slot.group,
                meaning=slot.meaning,
            ))
    return jobs


# --------------------------------------------------------------------------- #
# File emission
# --------------------------------------------------------------------------- #

def render_soundgroup_asset(plan: PackPlan, clip_names: Sequence[str]) -> str:
    """Render sound/<mod_id>.asset.

    Extra takes cannot be declared with ``soundfileoverrides``: that map only
    *remaps* a sound name that already exists, and the engine rejects anything
    else with ``No sound scripted for soundoverride '<name>'``. In game that was
    64 error-log lines and a variant feature that silently did nothing -- the
    pool still named four sounds, three of which resolved to nothing.

    So the two mechanisms are used for what each is actually for:

    * ``sound = { name file }`` declares the **new** take names (``_02`` and up).
      This is how the reference advisor mod ships its takes, and it is the only
      proven way to introduce a sound name from a mod.
    * ``soundfileoverrides`` remaps the **existing** base names (``_01``), which
      is what vanilla's own DLC voice does.

    A ``category`` block is emitted because every soundeffect without one draws
    a ``does not have a category assigned`` warning from the asset factory; it is
    not fatal, but 58 of them is 58 lines of noise in error.log.
    """
    spec = plan.spec
    existing, declared = _classify_clip_names(plan, clip_names)

    lines: list[str] = [
        "# Generated by the N.E.K.O Stellaris plugin (neko_stellaris).",
        "# Do not edit by hand: rebuild from the plugin so it stays in sync with",
        "# the line table and the extracted advisor sound dataset.",
        "#",
        f"# voice group : {spec.voice_group}",
        f"# clips       : {len(clip_names)} "
        f"({len(existing)} 覆盖原版名 + {len(declared)} 新声明)",
        "",
    ]

    if plan.effects:
        lines += [
            "category = {",
            '\tname = "Voice"',
            "\tsoundeffects = {",
        ]
        lines += [f"\t\t{effect.override_name}" for effect in plan.effects]
        lines += ["\t}", "}", ""]

    for sound in declared:
        lines += [
            "sound = {",
            f'\tname = "{sound}"',
            f'\tfile = "{VO_SUBDIR}/{sound}.wav"',
            "}",
        ]
    if declared:
        lines.append("")

    lines += [
        "soundgroup = {",
        f"\tname = {spec.voice_group}",
        f"\tsort_order = {SORT_ORDER}",
        "",
        "\tsoundfileoverrides = {",
    ]
    for name in existing:
        lines.append(f'\t\t{name} = "{VO_SUBDIR}/{name}.wav"')
    lines.append("\t}")

    if plan.effects:
        lines.append("")
        lines.append("\tsoundeffectoverrides = {")
        for effect in plan.effects:
            lines.append(f"\t\t{effect.base_name} = {effect.override_name}")
        lines.append("\t}")

    lines.append("}")
    lines.append("")

    for effect in plan.effects:
        lines.append("soundeffect = {")
        lines.append(f"\tname = {effect.override_name}")
        lines.append("\tsounds = {")
        for sound in effect.sounds:
            lines.append(f"\t\tsound = {sound}")
        lines.append("\t}")
        lines.append(f"\tvolume = {EFFECT_VOLUME}")
        lines.append(f"\tmax_audible = {EFFECT_MAX_AUDIBLE}")
        lines.append(f"\tmax_audible_behaviour = {EFFECT_MAX_AUDIBLE_BEHAVIOUR}")
        lines.append("}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _classify_clip_names(plan: PackPlan, clip_names: Sequence[str]) -> tuple[list[str], list[str]]:
    """Split clip names into those overriding an existing sound and those declaring new ones.

    The first take of each pool keeps the vanilla name (so ``soundfileoverrides``
    can remap it); every later take is a name that does not exist in the base
    game and therefore needs declaring.
    """
    pooled = {sound for effect in plan.effects for sound in effect.sounds[1:]}
    existing = [n for n in clip_names if n not in pooled]
    declared = [n for n in clip_names if n in pooled]
    return existing, declared


def render_voice_type(spec: PackSpec) -> str:
    """Render sound/advisor_voice_types/<mod_id>.txt.

    ``weight = { base = 0 }`` keeps the voice out of automatic selection: it is
    offered in the settings list but never swapped in behind the player's back,
    which is what a *custom* voice should do.
    """
    return "\n".join([
        "# Generated by the N.E.K.O Stellaris plugin (neko_stellaris).",
        "# The block name must match the sound group name of the same voice.",
        "",
        f"{spec.voice_group} = {{",
        f'\tname = "{spec.loc_key}"',
        f'\ticon = "gfx/interface/icons/advisor_vo/{spec.mod_id}.dds"',
        "",
        "\tplayable = {",
        "\t\talways = yes",
        "\t}",
        "",
        "\tweight = {",
        "\t\tbase = 0",
        "\t}",
        "}",
        "",
    ])


def localisation_units(spec: PackSpec) -> tuple[tuple[str, str, str], ...]:
    """The (language, display string, folder) triples to emit.

    A named function rather than an inline tuple so the choice of *which* name
    goes into the localisation is testable without running a build. Getting this
    wrong is invisible until someone opens the settings list and reads the wrong
    string there.
    """
    return (
        ("simp_chinese", spec.voice_display_zh, "simp_chinese"),
        ("english", spec.voice_display_en, "english"),
    )


def render_localisation(spec: PackSpec, *, language: str, display: str) -> bytes:
    """Render a localisation file as UTF-8 **with BOM**.

    Two details that both have to be right or the voice appears in the advisor
    list with no name at all:

    * the header is the *language identifier* ``l_<language>``, not the bare
      language name. Writing ``simp_chinese:`` produced
      ``Missing localisation for advisor voice type l_nekko_stellaris`` in
      error.log and a nameless entry in the voice list.
    * the file must start with ``EF BB BF``. Every vanilla localisation file
      does, and the parser uses it to choose the encoding; without it Chinese
      renders as mojibake.
    """
    body = "\n".join([
        f"l_{language}:",
        f' {spec.loc_key}:0 "{display}"',
        "",
    ])
    return b"\xef\xbb\xbf" + body.encode("utf-8")


def picture_line(has_thumbnail: bool) -> list[str]:
    """The ``picture=`` descriptor line, or nothing when there is no cover.

    Community convention, **not** a launcher mechanism: the Paradox launcher's
    bundle was searched and it never reads a ``picture`` key. A local mod's cover
    cannot in fact be set from inside the mod at all -- see ``docs`` in the
    README's known limitations. The line is emitted anyway because it is what
    Workshop mods in this install carry (``ugc_2941031061.mod``), because the
    in-game uploader is a separate program we cannot inspect, and because it costs
    nothing. It is not what fixes a blank tile, and must not be described as such.
    """
    return [f'picture="{THUMBNAIL_NAME}"'] if has_thumbnail else []


def render_descriptor(spec: PackSpec, *, has_thumbnail: bool | None = None) -> str:
    if has_thumbnail is None:
        has_thumbnail = spec.resolved_icon_source() is not None
    tags = "\n".join(f'\t"{tag}"' for tag in spec.tags)
    return "\n".join([
        f'version="{spec.mod_version}"',
        "tags={",
        tags,
        "}",
        f'name="{spec.name_zh}"',
        *picture_line(has_thumbnail),
        f'supported_version="{spec.supported_version}"',
        "",
    ])


def render_manifest(plan: PackPlan, clip_names: Sequence[str],
                    stamp: "PackStamp | None" = None) -> dict[str, Any]:
    """Describe the built pack so consumers need not reverse-engineer the asset.

    Keys are the **vanilla** soundeffect names, which is what a game event maps
    to; the soundgroup itself only knows the prefixed override names. Anything
    reading this can therefore answer "which wavs will this event play" without
    knowing how the pack was laid out.
    """
    pools: dict[str, list[str]] = {}
    by_effect: dict[str, list[str]] = {}
    for slot in plan.clips:
        if not slot.soundeffect:
            continue
        names = [_variant_sound_name(slot.sound, i) for i in range(1, len(slot.lines) + 1)]
        by_effect.setdefault(slot.soundeffect, []).extend(names)
    for effect in plan.effects:
        pools[effect.base_name] = list(effect.sounds)
    for effect, names in by_effect.items():
        if len(names) == 1:
            pools[effect] = names

    return {
        "format": 1,
        "generator": "neko_stellaris",
        "mod_id": plan.spec.mod_id,
        "voice_group": plan.spec.voice_group,
        "supported_version": plan.spec.supported_version,
        "max_variants": plan.spec.max_variants,
        "clip_count": len(clip_names),
        "coverage": dict(plan.coverage),
        "vo_subdir": VO_SUBDIR,
        "pools": {k: v for k, v in sorted(pools.items()) if v},
        "stamp": stamp.as_dict() if stamp else None,
    }


# --------------------------------------------------------------------------- #
# Freshness: is the pack in the game the one this voice would produce now?
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PackStamp:
    """What a given pack was built from.

    Exists because the failure it prevents is invisible: switch the character's
    voice in N.E.K.O, and the game keeps playing the previous voice with nothing
    anywhere saying so. Recording the inputs lets startup answer the question
    instead of leaving the user to notice by ear, weeks later.
    """

    voice_id: str = ""
    voice_url: str = ""
    voice_fingerprint: str = ""
    lines_revision: str = ""
    dataset_revision: str = ""
    art_revision: str = ""
    game_version: str = ""
    clip_count: int = 0
    built_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "voice_url": self.voice_url,
            "voice_fingerprint": self.voice_fingerprint,
            "lines_revision": self.lines_revision,
            "dataset_revision": self.dataset_revision,
            "art_revision": self.art_revision,
            "game_version": self.game_version,
            "clip_count": self.clip_count,
            "built_at": self.built_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PackStamp | None":
        if not isinstance(raw, dict):
            return None
        return cls(
            voice_id=str(raw.get("voice_id") or ""),
            voice_url=str(raw.get("voice_url") or ""),
            voice_fingerprint=str(raw.get("voice_fingerprint") or ""),
            lines_revision=str(raw.get("lines_revision") or ""),
            dataset_revision=str(raw.get("dataset_revision") or ""),
            art_revision=str(raw.get("art_revision") or ""),
            game_version=str(raw.get("game_version") or ""),
            clip_count=int(raw.get("clip_count") or 0),
            built_at=float(raw.get("built_at") or 0.0),
        )


def _revision(payload: Any) -> str:
    """Stable content hash of a JSON-able input."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _art_revision(spec: PackSpec) -> str:
    """Identity of everything that shapes the icon and the cover.

    Tracked apart from the audio because it goes stale for entirely different
    reasons: swap the illustration, or re-frame the cover, and every wav stays
    valid while the images already installed in the game do not. Without this the
    freshness check reported "fresh" and left the previous cover in place -- the
    same shape of mistake as reading the wrong line table, in a different input.
    """
    digest = hashlib.sha256()
    source = spec.resolved_icon_source()
    if source is None:
        digest.update(b"procedural-mark")
    else:
        path = Path(source)
        digest.update(path.name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"unreadable")
    # The frames count as inputs too: they are tuned constants, and retuning one
    # changes the output without touching a byte of the source art.
    digest.update(repr((
        ddsicon.ICON_CROP_CENTRE, ddsicon.ICON_CROP_SIDE,
        ddsicon.THUMBNAIL_CROP_CENTRE, ddsicon.THUMBNAIL_CROP_SIDE,
        ddsicon.ICON_CONTRAST, ddsicon.ICON_SATURATION, ddsicon.ICON_SHARPEN,
        spec.icon_size,
    )).encode("utf-8"))
    return digest.hexdigest()[:16]


def compute_stamp(spec: PackSpec, dataset: dict[str, Any], lines: dict[str, list[str]],
                  tts: "TtsSettings | None", clip_count: int) -> PackStamp:
    return PackStamp(
        voice_id=(tts.voice_id if tts else ""),
        voice_url=(tts.url if tts else ""),
        voice_fingerprint=(tts.fingerprint() if tts else ""),
        lines_revision=_revision(lines),
        dataset_revision=_revision(dataset),
        art_revision=_art_revision(spec),
        game_version=str(dataset.get("game_version") or ""),
        clip_count=clip_count,
        built_at=time.time(),
    )


def read_stamp(mod_dir: str | Path) -> PackStamp | None:
    """The stamp recorded in an installed pack, if it has one."""
    path = Path(mod_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return PackStamp.from_dict(json.loads(path.read_text(encoding="utf-8")).get("stamp"))
    except Exception:  # noqa: BLE001
        return None


@dataclass
class Freshness:
    """Verdict of comparing an installed pack against what would be built now."""

    state: str                       # fresh | voice_changed | lines_changed |
                                     # dataset_changed | art_changed |
                                     # no_stamp | not_installed
    reasons: list[str] = field(default_factory=list)
    installed: PackStamp | None = None
    current: PackStamp | None = None
    clips_to_generate: int = 0

    @property
    def needs_rebuild(self) -> bool:
        return self.state != "fresh"

    def summary(self) -> str:
        if self.state == "fresh":
            return f"语音包是最新的（{self.current.clip_count if self.current else 0} 条）"
        head = {
            "not_installed": "游戏里没装顾问语音包",
            "no_stamp": "已安装的语音包没有构建记录（旧版本生成的），无法确认它是用哪个音色做的",
            "voice_changed": "N.E.K.O 的音色变了，游戏里还是旧音色",
            "lines_changed": "台词表变了，游戏里还是旧台词",
            "dataset_changed": "游戏版本对应的顾问事件清单变了",
            "art_changed": "图标或封面的来源/取景变了，游戏里还是旧的图",
            "missing_bridge": "游戏里那份只有语音，没有事件桥（旧版本装的）",
        }.get(self.state, self.state)
        detail = f"；需重新合成 {self.clips_to_generate} 条" if self.clips_to_generate else ""
        return head + detail + ("（" + "；".join(self.reasons) + "）" if self.reasons else "")


def check_freshness(
    mod_dir: str | Path,
    spec: PackSpec,
    dataset: dict[str, Any],
    lines: dict[str, list[str]],
    tts: "TtsSettings | None",
    *,
    cache_dir: str | Path | None = None,
) -> Freshness:
    """Compare an installed pack against what the current settings would build.

    Deliberately reports *which* input changed rather than just "stale": a voice
    change invalidates all 248 clips and takes minutes, while a single edited
    line invalidates one clip and takes seconds. The user should be able to see
    that difference before agreeing to the work.

    The plan is derived here from the arguments rather than accepted from the
    caller. An earlier version took a pre-built plan and it was trivially easy to
    pass one built from *different* lines than the ones being compared, which made
    the check report "0 clips to generate" for a genuinely changed line table --
    a wrong answer to the only question this function exists to answer. Planning
    is pure and walks 82 entries, so recomputing costs nothing.
    """
    mod = Path(mod_dir)
    if not (mod / "sound").is_dir():
        return Freshness("not_installed", ["找不到 sound/ 目录"])

    plan = plan_pack(dataset, lines, spec)
    current = compute_stamp(spec, dataset, lines, tts, len(clip_jobs(plan)))
    installed = read_stamp(mod)

    if installed is None:
        return Freshness("no_stamp", ["pack.json 里没有 stamp 字段"],
                         installed=None, current=current,
                         clips_to_generate=current.clip_count)

    reasons: list[str] = []
    state = "fresh"
    if tts is not None and installed.voice_fingerprint != current.voice_fingerprint:
        state = "voice_changed"
        reasons.append(f"游戏里用的是 {installed.voice_id or '未知'}，"
                       f"现在是 {current.voice_id or '未知'}")
    elif installed.lines_revision != current.lines_revision:
        state = "lines_changed"
        reasons.append("台词表内容不同")
    elif installed.dataset_revision != current.dataset_revision:
        state = "dataset_changed"
        reasons.append(f"数据集版本 {installed.game_version or '?'} → "
                       f"{current.game_version or '?'}")
    elif installed.art_revision != current.art_revision:
        # Last because it is the cheapest to satisfy: the images are redrawn from
        # the source art and every wav stays cached, so this rebuild costs seconds
        # even though it rewrites the whole mod directory.
        state = "art_changed"
        reasons.append("音频不受影响，重建只需重绘图标与封面")

    clips = 0
    # art_changed is deliberately excluded: the images are redrawn while every wav
    # is reused, so reporting the full clip count here would price a rebuild that
    # costs seconds as if it were the minutes-long TTS batch, and talk the user out
    # of work that is essentially free.
    if state in ("voice_changed", "lines_changed", "dataset_changed"):
        from .tts import TtsSettings as _Tts
        from .tts import count_cache_misses
        if isinstance(tts, _Tts) and cache_dir:
            clips = count_cache_misses(
                [(j.sound, j.text) for j in clip_jobs(plan)], tts, cache_dir)
        else:
            clips = current.clip_count

    return Freshness(state, reasons, installed, current, clips)


def render_mod_stub(spec: PackSpec, *, has_thumbnail: bool | None = None) -> str:
    """The ``<mod_id>.mod`` stub that has to sit in ``Documents/.../Stellaris/mod/``.

    It is the descriptor plus a ``path=`` line telling the launcher where the
    real folder is. Relative paths are resolved against the user data directory,
    so ``mod/<mod_id>`` is the portable form.
    """
    if has_thumbnail is None:
        has_thumbnail = spec.resolved_icon_source() is not None
    tags = "\n".join(f'\t"{tag}"' for tag in spec.tags)
    return "\n".join([
        f'version="{spec.mod_version}"',
        "tags={",
        tags,
        "}",
        f'name="{spec.name_zh}"',
        *picture_line(has_thumbnail),
        f'supported_version="{spec.supported_version}"',
        f'path="mod/{spec.mod_id}"',
        "",
    ])


@dataclass
class BuildResult:
    output_dir: Path
    clips_written: int
    bytes_written: int
    skipped_existing: int
    plan: PackPlan
    tts_stats: TtsStats
    elapsed_s: float

    def summary(self) -> str:
        return (f"{self.clips_written} clips / {self.bytes_written / 1048576:.1f} MiB "
                f"→ {self.output_dir} in {self.elapsed_s:.1f}s "
                f"(tts={self.tts_stats.done} cached={self.tts_stats.cached} "
                f"failed={self.tts_stats.failed})")


async def build_pack(
    plan: PackPlan,
    out_dir: str | Path,
    tts: TtsSettings,
    *,
    cache_dir: str | Path | None = None,
    dataset: dict[str, Any] | None = None,
    lines: dict[str, list[str]] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> BuildResult:
    """Materialise the whole mod directory. Idempotent; safe to re-run."""
    import time

    started = time.monotonic()
    out = Path(out_dir)
    jobs = clip_jobs(plan)
    if not jobs:
        raise VoicePackError("计划里没有任何片段，检查台词表是否与数据集对应")

    phases = on_phase or (lambda _msg: None)

    def _report(done: int, total: int, label: str) -> None:
        if on_progress:
            on_progress(done, total, label)

    # 1. Synthesize (cached).
    phases(f"合成 {len(jobs)} 条语音")
    stats = TtsStats()
    raw = await synthesize_many(
        [(job.sound, job.text) for job in jobs],
        tts,
        cache_dir=Path(cache_dir) if cache_dir else None,
        on_progress=_report,
        stats=stats,
    )

    # 2. Convert + write clips. Files are named exactly after the sound name
    #    they back, so the asset mapping is readable without cross-referencing.
    phases("转码并写入音频")
    vo_dir = out / "sound" / VO_SUBDIR
    vo_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    total_bytes = 0
    missing: list[str] = []
    for job in jobs:
        payload = raw.get(job.sound)
        if not payload:
            missing.append(job.sound)
            continue
        pcm, rate = payload
        samples, out_rate = prepare_for_game(wrap_raw(pcm, rate))
        blob = wav_bytes_mono16(samples, out_rate)
        target = vo_dir / f"{job.sound}.wav"
        target.write_bytes(blob)
        written += 1
        total_bytes += len(blob)
    if missing:
        plan.warnings.append(f"{len(missing)} 条合成失败未写入：{missing[:6]}")

    # 3. Static files.
    phases("写入 mod 清单与资源定义")
    clip_names = [job.sound for job in jobs if job.sound not in missing]

    # Drop clips from an earlier build whose names left the plan. Nothing
    # references them -- the asset is rewritten from `clip_names` alone -- but they
    # still ship: renaming the takes left their 166 predecessors in place and the
    # mod silently grew from 36 MiB to 62 MiB, with every referenced file present
    # and correct, so nothing looked wrong until the install reported the size.
    keep = set(clip_names)
    stale = sorted(p for p in vo_dir.glob("*.wav") if p.stem not in keep)
    for path in stale:
        path.unlink()
    if stale:
        logger.info("清理了 %d 个已不在计划内的旧音频：%s",
                    len(stale), [p.stem for p in stale[:4]])

    (out / "sound").mkdir(parents=True, exist_ok=True)
    (out / "sound" / f"{plan.spec.mod_id}.asset").write_text(
        render_soundgroup_asset(plan, clip_names), encoding="utf-8", newline="\n")

    voice_dir = out / "sound" / "advisor_voice_types"
    voice_dir.mkdir(parents=True, exist_ok=True)
    (voice_dir / f"{plan.spec.mod_id}.txt").write_text(
        render_voice_type(plan.spec), encoding="utf-8", newline="\n")

    # The *in-game option* name, not the product name: this string is what the
    # player reads in Settings -> Sound -> Advisor Voice.
    for language, display, folder in localisation_units(plan.spec):
        loc_dir = out / "localisation" / folder
        loc_dir.mkdir(parents=True, exist_ok=True)
        (loc_dir / f"{plan.spec.mod_id}_l_{language}.yml").write_bytes(
            render_localisation(plan.spec, language=language, display=display))

    icon_source = plan.spec.resolved_icon_source()
    icon_path = out / "gfx" / "interface" / "icons" / "advisor_vo" / f"{plan.spec.mod_id}.dds"
    if icon_source is not None:
        try:
            ddsicon.write_icon_from_image(icon_source, icon_path, plan.spec.icon_size)
            # thumbnail.png at the mod root is what the launcher and the Workshop
            # listing show, named by ``picture=`` in both descriptors.
            (out / THUMBNAIL_NAME).write_bytes(
                ddsicon.render_thumbnail_png(icon_source))
        except Exception as exc:  # noqa: BLE001 - Pillow missing or art unreadable
            logger.warning("源图无法用作图标（%s），回退到程序生成的标记", exc)
            if not icon_path.is_file():
                ddsicon.write_dds(icon_path, plan.spec.icon_size)
            (out / THUMBNAIL_NAME).unlink(missing_ok=True)
    else:
        ddsicon.write_dds(icon_path, plan.spec.icon_size)
        (out / THUMBNAIL_NAME).unlink(missing_ok=True)

    # Ask the directory, not the spec: the render above can fail and delete the
    # cover, and a ``picture=`` pointing at a file that is not there is the same
    # empty tile in the launcher.
    (out / "descriptor.mod").write_text(
        render_descriptor(plan.spec,
                          has_thumbnail=(out / THUMBNAIL_NAME).is_file()),
        encoding="utf-8", newline="\n")

    # A machine-readable manifest of what was actually built. Without it, a
    # consumer asking "which wavs implement advisor_notification_X" has to
    # reverse-engineer the soundgroup, and cannot: the override soundeffects
    # carry a ``<mod_id>_`` prefix, so the asset alone does not map a *vanilla*
    # soundeffect name to its pool. Runtime slot rewriting needs exactly that
    # mapping, and deriving it by guesswork produced a table in which every
    # entry resolved to an empty pool -- silently, which is the worst kind.
    (out / MANIFEST_NAME).write_text(
        json.dumps(render_manifest(
            plan, clip_names,
            stamp=compute_stamp(plan.spec, dataset or {}, lines or {}, tts, len(clip_names)),
        ), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    return BuildResult(
        output_dir=out,
        clips_written=written,
        bytes_written=total_bytes,
        skipped_existing=stats.cached,
        plan=plan,
        tts_stats=stats,
        elapsed_s=time.monotonic() - started,
    )


def wrap_raw(pcm: bytes, rate: int) -> bytes:
    """Wrap raw PCM16 from the TTS layer into a WAV so the decoder can read it."""
    from .audiowav import wrap_pcm16_wav
    return wrap_pcm16_wav(pcm, rate)


# --------------------------------------------------------------------------- #
# Runtime slot rewriting
# --------------------------------------------------------------------------- #

SLOT_STATE_NAME = ".neko_stellaris_slots.json"


def slot_state_path(mod_dir: str | Path) -> Path:
    return Path(mod_dir) / SLOT_STATE_NAME


def vo_dir(mod_dir: str | Path) -> Path:
    """Directory holding the pack's wav files.

    Exists so the ``sound/`` + ``VO_SUBDIR`` join has exactly one definition: it
    was previously spelled out in two modules, and the CLI's copy dropped the
    ``vo/`` level, so ``probe-install --all`` silently found zero slots and
    reported "nothing to overwrite" instead of doing the experiment.
    """
    return Path(mod_dir) / "sound" / VO_SUBDIR


def load_slot_state(mod_dir: str | Path) -> dict:
    path = slot_state_path(mod_dir)
    if not path.is_file():
        return {"slots": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"slots": {}}
    if not isinstance(data, dict):
        return {"slots": {}}
    data.setdefault("slots", {})
    return data


def save_slot_state(mod_dir: str | Path, state: dict) -> None:
    slot_state_path(mod_dir).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Replace a file's contents without ever exposing a partial write.

    The engine now reads these files at playback time (verified: overwriting a
    slot mid-session changes what the advisor says on the next line), so a plain
    ``write_bytes`` is a real hazard -- an advisor event firing during the write
    could be handed a truncated WAV. Writing beside the target and calling
    ``os.replace`` makes the swap atomic on both NTFS and POSIX, and keeps the
    temporary on the same volume so the replace is a rename, not a copy.
    """
    tmp = target.with_name(target.name + ".neko-tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def rewrite_slot(
    mod_dir: str | Path,
    *,
    sound_name: str,
    wav_payload: bytes,
    backup_dir: str | Path | None = None,
) -> dict:
    """Replace the audio behind an already-registered sound name.

    This is the runtime mutation the engine honours: the ``.asset`` mapping is
    untouched, only the bytes the mapping points at change. Because the name
    never changes, no asset reload, console command or window focus is involved.

    The original bytes are copied aside on first rewrite so ``restore`` is always
    possible; without that, a failed experiment or a bad generated line would
    silently leave the user's voice pack corrupted.
    """
    mod = Path(mod_dir)
    target = vo_dir(mod) / f"{sound_name}.wav"
    if not target.parent.is_dir():
        raise VoicePackError(f"找不到语音包目录：{target.parent}")
    if not target.is_file():
        raise VoicePackError(f"槽位不存在：{target.name}")

    state = load_slot_state(mod)
    slots = state.setdefault("slots", {})
    entry = slots.setdefault(sound_name, {})

    if not entry.get("original_saved"):
        store = Path(backup_dir) if backup_dir else mod / ".slot_backup"
        store.mkdir(parents=True, exist_ok=True)
        backup = store / f"{sound_name}.wav"
        if not backup.exists():
            shutil.copy2(target, backup)
        entry["backup"] = str(backup)
        entry["original_saved"] = True

    _atomic_write_bytes(target, wav_payload)
    entry["size"] = len(wav_payload)
    entry["written_at"] = time.time()
    save_slot_state(mod, state)
    return {"sound": sound_name, "path": str(target), "bytes": len(wav_payload)}


def restore_slots(mod_dir: str | Path) -> list[str]:
    """Put every rewritten slot back to the bytes that shipped with the pack."""
    mod = Path(mod_dir)
    state = load_slot_state(mod)
    restored: list[str] = []
    for sound_name, entry in (state.get("slots") or {}).items():
        backup = Path(str(entry.get("backup") or ""))
        target = vo_dir(mod) / f"{sound_name}.wav"
        if backup.is_file():
            shutil.copy2(backup, target)
            restored.append(sound_name)
    state["slots"] = {}
    save_slot_state(mod, state)
    return restored
