# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Generate the companion "event bridge" mod that writes structured events to game.log.

## Why a companion mod at all

The base game already logs every event the player is offered
(``Event crisis.7515 added info about event selection``), which is enough to
know *that* something happened and which option was taken. It is not enough to
know *what*: "war declared on you" and "a neighbour surveyed a planet" reach the
log through the same sentence shape, and nothing in it names the empire or the
fleet. The bridge closes that gap by calling the script effect
``log = "NEKO|v1|<goal>|k=v|..."`` from the on_actions that actually carry the
detail.

## The mechanism, and the one assumption worth stating

Stellaris merges on_action definitions **by top-level name**: a mod file that
repeats ``on_country_attacked = { events = { neko_bridge.1 } }`` appends to the
vanilla block rather than replacing it. This is the standard way every major mod
extends pulses, but it could not be *proved from the shipped files* -- the base
game's 489 top-level names contain no duplicates, so there is no vanilla example
to point at. It is documented behaviour, and a replacement semantic would break
essentially every mod in existence.

Because that assumption is load-bearing, the pack emits a **heartbeat**: a hidden
event on ``on_bi_yearly_pulse_country`` that logs ``NEKO|v1|heartbeat|``. If the
merge semantics ever differ, the symptom is concrete and checkable -- no
heartbeat, and the two vanilla events on that hook stop firing -- rather than a
silent no-op.

## Player filtering

``is_player`` is not a Stellaris trigger (it does not appear in
``script_documentation`` and has zero uses in the shipped scripts); the real one
is ``is_ai = no``. on_action blocks have no ``limit`` field either, so filtering
lives in each generated hidden event's ``trigger = {}``:

* country-scoped hooks  -> ``this`` / ``root`` = ``{ is_ai = no }``
* planet/fleet/ship/starbase/station/leader-scoped hooks -> ``owner = { is_ai = no }``

Both scope links are then *also* used as payload, which is deliberate: if a
scope assumption is wrong the trigger fails closed (the event simply never
fires) instead of flooding the log with every AI empire's business.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("neko_stellaris.bridge")

DEFAULT_MOD_ID = "nekko_stellaris_bridge"
DEFAULT_NAMESPACE = "neko_bridge"
BRIDGE_VERSION = "v1"

# Trigger body for each goal, keyed off the audited scope. A goal missing from
# this table is *not* emitted: guessing a scope link wrong is worse than not
# having the hook, because the failure modes are a silent dead hook or a flooded
# log. Everything here corresponds to a `verified` row in on_action_hooks.json.
COUNTRY_THIS = "this = { is_ai = no }"
COUNTRY_ROOT = "root = { is_ai = no }"
# ``exists`` first, and it is not decoration. Several of these on_actions fire in a
# scope whose ``owner`` link is legitimately absent -- a planet surveyed before
# anyone colonised it, a neutral starbase, a station that was just destroyed. An
# unguarded switch there does not fail quietly: the engine logs
# "Invalid context switch [owner] from <planet>" on every single firing, which was
# 32,383 lines in one session from one event, plus 109 from another.
#
# The guard also states the intent correctly: a thing with no owner is not the
# player's, so the hook should simply not fire.
OWNER_THIS = "exists = owner\nowner = { is_ai = no }"
# Same failure, different link: on_planet_surveyed documents `Root = Planet,
# From = Country`, so the surveying country is `from` -- and `from` can be absent
# too, because the comment notes it can fire "without a ship through changes in
# intel levels".
FROM_COUNTRY = "exists = from\nfrom = { is_ai = no }"

GOAL_FILTERS: dict[str, str] = {
    # -- war ------------------------------------------------------------- #
    "war_declared_on_us": COUNTRY_THIS,          # this = attacked country
    "war_declared_by_us": COUNTRY_ROOT,          # root = war leader
    "war_entered_as_late_joiner": COUNTRY_THIS,
    "war_ended": COUNTRY_ROOT,
    "war_won": COUNTRY_ROOT,
    "war_lost": COUNTRY_ROOT,
    "status_quo_signed": COUNTRY_ROOT,
    # -- fleets and battles ---------------------------------------------- #
    "fleet_destroyed": COUNTRY_THIS,             # this = owner of the lost fleet
    "fleet_destroyed_by_us": COUNTRY_THIS,
    "battle_won": COUNTRY_THIS,
    "battle_lost": COUNTRY_THIS,
    "battle_started": COUNTRY_THIS,
    # -- colonies and planets -------------------------------------------- #
    "colony_established": OWNER_THIS,
    "colonization_started": OWNER_THIS,
    "colony_destroyed": OWNER_THIS,
    "building_completed": OWNER_THIS,
    "building_upgraded": OWNER_THIS,
    "district_completed": OWNER_THIS,
    "zone_completed": OWNER_THIS,
    # The rebel country in `this` is always AI, so the player check has to reach
    # through the planet that was lost. `this = { is_ai = no }` here would be
    # false forever.
    "planet_rebellion": "from = { owner = { is_ai = no } }",
    # -- starbases and stations ------------------------------------------ #
    "starbase_occupied": COUNTRY_THIS,           # this = occupier
    "starbase_destroyed": OWNER_THIS,
    "starbase_disabled": OWNER_THIS,
    "system_lost": COUNTRY_THIS,
    "waystation_lost": OWNER_THIS,
    "mining_research_station_destroyed": OWNER_THIS,
    # -- science --------------------------------------------------------- #
    "tech_researched": COUNTRY_THIS,
    "survey_completed_system": COUNTRY_ROOT,
    "survey_completed_planet": FROM_COUNTRY,        # root = planet, from = country
    "special_project_completed_debris": COUNTRY_THIS,
    "special_project_completed_modification": COUNTRY_THIS,
    "special_project_completed_uplift": OWNER_THIS,
    "special_project_completed_archaeology": OWNER_THIS,
    "special_project_completed_astral_rift": COUNTRY_THIS,
    # -- diplomacy ------------------------------------------------------- #
    "first_contact_with_empire": COUNTRY_THIS,
    # ``this`` here is the first-contact *process*, not a country: the on_action's
    # own comment says "This = first contact scope / From = other country", and the
    # vanilla events fired there all reach the country through ``owner``. Filtering
    # with ``this = { is_ai = no }`` evaluated is_ai on a non-country scope, which
    # the engine rejects -- and because this hook never fired in the session that
    # exposed the other scope bugs, nothing ever reported it.
    "first_contact_finished": OWNER_THIS,
    "communications_established": COUNTRY_THIS,
    "federation_joined_or_formed": COUNTRY_THIS,  # this = federation leader
    "federation_left": COUNTRY_THIS,
    "federation_new_leader": COUNTRY_THIS,
    # -- leaders --------------------------------------------------------- #
    "leader_died": COUNTRY_THIS,
    "new_ruler": COUNTRY_THIS,
    "leader_hired": OWNER_THIS,                  # this = leader, not country
    # -- megastructures -------------------------------------------------- #
    "megastructure_completed": COUNTRY_ROOT,
    "megastructure_build_start": COUNTRY_ROOT,
    "megastructure_upgraded": COUNTRY_ROOT,
    # -- endgame --------------------------------------------------------- #
    "empire_destroyed": COUNTRY_THIS,
}

# Goals deliberately left out of a default build, with the reason. Kept as data
# so the build report can explain the omission instead of silently dropping rows.
EXCLUDED_DEFAULT: dict[str, str] = {
    "ship_destroyed": "单舰级事件，大舰队战会每秒刷数十行，会写爆 game.log",
    "battle_joined": "每支加入战斗的舰队各触发一次，噪声远大于信息量",
    "battle_joined_defender": "同上",
    "survey_planet_by_ship": "每个行星勘测都触发，科考船全程刷",
    "colony_ownerless": "作用域注释明确说明 from 在此刻失效，无法可靠判定玩家",
    "starbase_transfer": "与 starbase_occupied 语义重叠且更频繁",
    "communications_established_pre": "与 communications_established 重复",
    "leader_spawned": "与 leader_hired 重叠",
    "ruler_created": "与 new_ruler 重叠",
    "leader_died_notify": "与 leader_died 重复触发",
    "planet_rebellion_owner_switched": "与 planet_rebellion 重复触发",
    "first_contact_started": "scope 属审计中的 inferred 档，等核实后再开",
    "first_contact_finished": "on_first_contact_finished 的作用域是 first_contact 对象，"
                              "没有可靠的国家链接可用来过滤玩家；first_contact_with_empire 已覆盖该事件",
    "country_created": "scope 属审计中的 inferred 档，且 AI 建国极频繁",
}

HEARTBEAT_ON_ACTION = "on_bi_yearly_pulse_country"
HEARTBEAT_ID = "neko_bridge.0"


class BridgeError(RuntimeError):
    """Raised when the bridge mod cannot be generated from the hook dataset."""


@dataclass(frozen=True)
class BridgeSpec:
    mod_id: str = DEFAULT_MOD_ID
    namespace: str = DEFAULT_NAMESPACE
    # Same naming rule as the voice pack: bare, no parentheses, no product
    # framing. "事件桥" stays because it is the one mod whose job is purely
    # mechanical, and the launcher list is where that is worth saying.
    name_zh: str = "YUI 事件桥"
    name_en: str = "YUI Event Bridge"
    mod_version: str = "1.0.0"
    supported_version: str = "4.5.*"
    heartbeat: bool = True
    include_extra: frozenset[str] = frozenset()


@dataclass
class BridgeHook:
    event_id: str
    goal: str
    on_action: str
    trigger: str
    log_text: str
    file: str
    line: int
    confidence: str
    event_type: str = "country_event"
    event_type_source: str = ""


@dataclass
class BridgePlan:
    spec: BridgeSpec
    hooks: list[BridgeHook] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{len(self.hooks)} hooks, {len(self.skipped)} skipped "
                f"(namespace {self.spec.namespace})")


def load_hooks(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("hooks"):
        raise BridgeError(f"钩子数据集为空：{path}")
    return data


def _normalise_log_text(raw: str, goal: str) -> str:
    """Force the dataset's log text into the ``NEKO|v1|<goal>|...`` wire shape.

    The audit wrote ``NEKO|<goal>|k=v`` (no version); the reader requires the
    version so a future schema change cannot be mistaken for data.
    """
    text = str(raw or "").strip()
    if not text:
        return f"NEKO|{BRIDGE_VERSION}|{goal}|"
    parts = text.split("|")
    if parts and parts[0] == "NEKO":
        parts = parts[1:]
    if parts and parts[0] == BRIDGE_VERSION:
        parts = parts[1:]
    if parts and parts[0] == goal:
        parts = parts[1:]
    # Every value is wrapped in the game's own square-bracket reference syntax
    # already; leave it untouched and let the reader cope with a value that the
    # engine collapsed to empty (a documented, silent failure mode).
    return "|".join(["NEKO", BRIDGE_VERSION, goal, *[p for p in parts if p]]) + "|"


def plan_bridge(hooks_data: dict, spec: BridgeSpec | None = None) -> BridgePlan:
    spec = spec or BridgeSpec()
    plan = BridgePlan(spec=spec)
    seen: set[str] = set()

    for record in hooks_data["hooks"]:
        goal = str(record.get("goal") or "")
        if not goal or goal in seen:
            continue
        if goal not in GOAL_FILTERS and goal not in spec.include_extra:
            reason = EXCLUDED_DEFAULT.get(goal, "未在本插件的过滤表中（scope 未经核实）")
            plan.skipped.append((goal, reason))
            continue
        on_action = str(record.get("on_action") or "")
        if not on_action:
            plan.skipped.append((goal, "数据集缺少 on_action 名"))
            continue
        seen.add(goal)
        plan.hooks.append(BridgeHook(
            event_id="",  # assigned below, in stable goal order
            goal=goal,
            on_action=on_action,
            trigger=GOAL_FILTERS.get(goal, ""),
            log_text=_normalise_log_text(record.get("recommended_log_text", ""), goal),
            file=str(record.get("file") or ""),
            line=int(record.get("line") or 0),
            confidence=str(record.get("confidence") or ""),
            event_type=str(record.get("event_type") or "country_event"),
            event_type_source=str(record.get("event_type_source") or ""),
        ))

    plan.hooks.sort(key=lambda h: h.goal)
    for index, hook in enumerate(plan.hooks, start=1):
        plan.hooks[index - 1] = BridgeHook(
            event_id=f"{spec.namespace}.{index}",
            goal=hook.goal,
            on_action=hook.on_action,
            trigger=hook.trigger,
            log_text=hook.log_text,
            file=hook.file,
            line=hook.line,
            confidence=hook.confidence,
            event_type=hook.event_type,
            event_type_source=hook.event_type_source,
        )
    return plan


def render_events_file(plan: BridgePlan) -> str:
    """Render ``events/<namespace>_events.txt`` with one hidden event per hook."""
    lines = [
        "# Generated by the N.E.K.O Stellaris plugin (neko_stellaris).",
        "#",
        "# Every event here is invisible, trigger-gated to the player, and does",
        "# exactly one thing: call the `log` effect so an external program can see",
        "# what happened. Nothing is added to the game's own event pools.",
        f"# Source dataset: {len(plan.hooks)} audited on_action hooks.",
        "",
        f"namespace = {plan.spec.namespace}",
        "",
    ]

    if plan.spec.heartbeat:
        lines += [
            "# Liveness probe. If the on_action merge semantics ever stop working,",
            "# this line stops appearing and the failure is visible instead of silent.",
            "country_event = {",
            f"\tid = {HEARTBEAT_ID}",
            "\thide_window = yes",
            "\tis_triggered_only = yes",
            "\ttrigger = {",
            *(f"\t\t{line}" for line in COUNTRY_THIS.splitlines()),
            "\t}",
            "\timmediate = {",
            f'\t\tlog = "NEKO|{BRIDGE_VERSION}|heartbeat|"',
            "\t}",
            "}",
            "",
        ]

    for hook in plan.hooks:
        lines += [
            f"# {hook.goal}  <- {hook.on_action}",
            f"#   source: {hook.file}:{hook.line} (confidence={hook.confidence})",
            f"#   event type: {hook.event_type} — {hook.event_type_source}",
            f"{hook.event_type} = {{",
            f"\tid = {hook.event_id}",
            "\thide_window = yes",
            "\tis_triggered_only = yes",
            "\ttrigger = {",
            *(f"\t\t{line}" for line in hook.trigger.splitlines()),
            "\t}",
            "\timmediate = {",
            f'\t\tlog = "{hook.log_text}"',
            "\t}",
            "}",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_on_action_file(plan: BridgePlan) -> str:
    """Render ``common/on_actions/<mod_id>_on_actions.txt``.

    The filename is prefixed so it loads after the vanilla files in ASCII order,
    which is what makes the append deterministic.
    """
    by_action: dict[str, list[str]] = {}
    if plan.spec.heartbeat:
        by_action.setdefault(HEARTBEAT_ON_ACTION, []).append(HEARTBEAT_ID)
    for hook in plan.hooks:
        by_action.setdefault(hook.on_action, []).append(hook.event_id)

    lines = [
        "# Generated by the N.E.K.O Stellaris plugin (neko_stellaris).",
        "#",
        "# Stellaris merges on_action blocks by top-level name, so repeating a",
        "# vanilla name here APPENDS our event to the base game's list rather than",
        "# replacing it. Only `events` is ever extended: several vanilla hooks use",
        "# `random_events` to draw from a pool, and adding to those would make our",
        "# event compete for a slot instead of always firing.",
        "#",
        "# Scopes follow each hook's audited definition; see the event file for the",
        "# per-hook source line.",
        "",
    ]
    for on_action in sorted(by_action):
        lines.append(f"{on_action} = {{")
        lines.append("\tevents = {")
        for event_id in by_action[on_action]:
            lines.append(f"\t\t{event_id}")
        lines.append("\t}")
        lines.append("}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_descriptor(spec: BridgeSpec) -> str:
    return "\n".join([
        f'version="{spec.mod_version}"',
        "tags={",
        '\t"Gameplay"',
        '\t"Utilities"',
        "}",
        f'name="{spec.name_zh}"',
        f'supported_version="{spec.supported_version}"',
        "",
    ])


def render_mod_stub(spec: BridgeSpec) -> str:
    return "\n".join([
        f'version="{spec.mod_version}"',
        "tags={",
        '\t"Gameplay"',
        '\t"Utilities"',
        "}",
        f'name="{spec.name_zh}"',
        f'supported_version="{spec.supported_version}"',
        f'path="mod/{spec.mod_id}"',
        "",
    ])


def render_readme(plan: BridgePlan) -> str:
    """Ship the caveats inside the mod, so they survive being copied anywhere."""
    included = "\n".join(f"- `{h.goal}` -> `{h.on_action}`" for h in plan.hooks)
    skipped = "\n".join(f"- `{goal}`: {why}" for goal, why in plan.skipped[:20])
    return f"""# {plan.spec.name_zh}

由 N.E.K.O 的 `neko_stellaris` 插件生成。这个 mod 本身**不改变任何游戏内容**：
它只把游戏里发生的事写进 `logs/game.log`，供外部程序读取。

## 它做什么

给 {len(plan.hooks)} 个 on_action 各追加一个**隐藏事件**。每个事件只做一件事：
调用脚本效果 `log = "NEKO|{BRIDGE_VERSION}|<事件名>|键=值|..."`。

## 为什么需要它

原版 game.log 已经会记录每条事件选择（`Event crisis.7515 added info about event
selection`），所以「发生了什么」不用 mod 也能知道。但「到底是谁对谁做了什么」它不写：
「你被宣战」和「邻居勘测了行星」在原版日志里长得一模一样。这个 mod 补的就是这层信息。

## 一个必须说清楚的假设

Stellaris 按顶层名字**合并** on_action：在本 mod 里重复写
`on_country_attacked = {{ events = {{ ... }} }}` 是**追加**，不是覆盖。
这是所有大型 mod 扩展 pulse 的标准做法，但**无法用原版文件举证**——
原版 489 个顶层名里一个重名都没有，找不到例子。它是文档行为，且如果它是覆盖语义，
现存几乎每个 mod 都会坏。

正因为这个假设是承重的，包里放了**心跳**：`{HEARTBEAT_ON_ACTION}` 上挂了一个
`{HEARTBEAT_ID}`，每两年写一行 `NEKO|{BRIDGE_VERSION}|heartbeat|`。
如果哪天合并语义变了，症状是具体可查的——心跳消失，且该钩子上原版的 2 个事件也停了——
而不是悄无声息地什么都不发生。

## 玩家过滤

`is_player` 在 Stellaris 里**不是**触发器，正确的是 `is_ai = no`。on_action 块也没有
`limit` 字段，所以过滤写在各事件的 `trigger = {{}}` 里。国家作用域用 `this` / `root`，
行星/舰队/舰船/星港/空间站/领袖作用域用 `owner`。

这意味着**作用域判断错了会失败关闭**（事件干脆不触发），而不是把全银河 AI 的动静刷进日志。

## 包含的钩子

{included}

## 故意排除的

{skipped}

## 怎么验证它在工作

1. 进游戏存一次档，退出。
2. 打开 `文档\\Paradox Interactive\\Stellaris\\logs\\game.log`，搜 `NEKO|`。
3. 应该能看到 `NEKO|{BRIDGE_VERSION}|...` 的行。搜不到就先看心跳行在不在。
"""


@dataclass
class BridgeBuildResult:
    output_dir: Path
    hook_count: int
    files: list[str]
    skipped: list[tuple[str, str]]

    def summary(self) -> str:
        return (f"{self.hook_count} hooks -> {self.output_dir} "
                f"({len(self.files)} files, {len(self.skipped)} goals skipped)")


def build_bridge(plan: BridgePlan, out_dir: str | Path) -> BridgeBuildResult:
    """Write the bridge mod. Idempotent; the directory is fully regenerated."""
    out = Path(out_dir)
    (out / "events").mkdir(parents=True, exist_ok=True)
    (out / "common" / "on_actions").mkdir(parents=True, exist_ok=True)

    written: list[str] = []

    def _write(rel: str, text: str) -> None:
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        written.append(rel)

    _write(f"events/{plan.spec.namespace}_events.txt", render_events_file(plan))
    # Leading "zz_" keeps the file last in ASCII load order, which is the
    # conventional way to make an append land after the definitions it extends.
    _write(f"common/on_actions/zz_{plan.spec.mod_id}_on_actions.txt",
           render_on_action_file(plan))
    _write("descriptor.mod", render_descriptor(plan.spec))
    _write("README.md", render_readme(plan))

    return BridgeBuildResult(output_dir=out, hook_count=len(plan.hooks),
                             files=written, skipped=plan.skipped)


def iter_event_ids(plan: BridgePlan) -> Iterable[str]:
    if plan.spec.heartbeat:
        yield HEARTBEAT_ID
    for hook in plan.hooks:
        yield hook.event_id
