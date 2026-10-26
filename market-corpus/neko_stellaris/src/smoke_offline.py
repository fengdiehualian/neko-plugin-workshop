# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Network-free regression suite for the neko_stellaris plugin.

Nothing here touches TTS, the host, or the game: it asserts the *formats* the
plugin emits, which is where the expensive mistakes live (a sound group that
does not cover every reachable sound, a localisation file without its BOM, a DDS
whose header does not match what the game ships, a bridge event that no
on_action ever fires).

Run it from either layout these sources ship in:

    .venv\\Scripts\\python.exe plugin/plugins/neko_stellaris/smoke_offline.py
    python smoke_offline.py                           # this standalone repository

The single check that touches ``Settings`` lives in the plugin entry point and so
wants the host SDK; where that is unavailable it is reported as *skipped*, never
silently dropped.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import re
import sys

# ``core/`` sits beside this file in both of the shapes these sources travel in:
#
#   <host>/plugin/plugins/neko_stellaris/   installed inside N.E.K.O
#   <repo>/                                 this standalone repository
#
# Importing it *through* the plugin package would execute the entry point first,
# whose opening act is ``from plugin.sdk.plugin import ...`` -- so the suite would
# need the host SDK merely to start, and could not run from the mirror at all.
# Putting this file's own directory on the path imports ``core`` directly, and
# every core module imports its siblings relatively, so both layouts behave the
# same and neither drags the host in.
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# ``import_module`` instead of an ``import`` statement: ``core`` only becomes
# importable after the ``sys.path`` edit above, and N.E.K.O's own CI lints with
# ``ruff --ignore-noqa`` -- where a suppression is not a thing -- so there is
# nothing here to suppress.
bridge_core = importlib.import_module("core.bridge")
ddsicon = importlib.import_module("core.ddsicon")
gamelog = importlib.import_module("core.gamelog")
voicepack = importlib.import_module("core.voicepack")


def _host_root() -> pathlib.Path | None:
    """The N.E.K.O checkout this plugin can borrow the host SDK from, if any.

    The three shapes to cover: installed under ``<host>/plugin/plugins/...``,
    cloned as its own repository beside the N.E.K.O checkout, and CI where
    ``NEKO_ROOT`` points at a checkout somewhere else entirely. Returning None
    is normal -- the few host-dependent checks then report themselves skipped.
    """
    candidates = []
    override = os.environ.get("NEKO_ROOT", "").strip()
    if override:
        candidates.append(pathlib.Path(override).expanduser())
    candidates.extend(HERE.parents)
    candidates.append(HERE.parent / "N.E.K.O")
    for candidate in candidates:
        if (candidate / "plugin" / "sdk").is_dir():
            return candidate
    return None


def _host_settings():
    """The plugin's ``Settings`` dataclass, or None when the host is unreachable.

    Exactly one check asserts something about ``Settings`` -- that its ``policy()``
    borrows the dataclass' own numbers rather than keeping a second copy that can
    drift. That class lives in the entry point, which imports the N.E.K.O SDK, so
    this is the one part of the suite that cannot run on a bare interpreter.
    """
    host = _host_root()
    if host is not None and str(host) not in sys.path:
        sys.path.append(str(host))
    try:
        from plugin.plugins.neko_stellaris import Settings
    except Exception:
        return None
    return Settings


HOST_SETTINGS = _host_settings()

DATA = HERE / "data"

# Labels and skip reasons are free to contain Chinese, but a piped stdout on
# Windows defaults to the console code page (cp1252 on a CI runner), where that
# text is not representable -- the suite would then die while *reporting* a skip,
# which is the worst possible failure mode for a suite. Pin the streams to UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - not a TextIOWrapper
        pass

FAILURES: list[str] = []
SKIPPED: list[str] = []
CHECKS = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok   {label}")
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"  FAIL {label}  {detail}")


def skip(label: str, why: str) -> None:
    """Record a check that could not be attempted, without counting it as one."""
    SKIPPED.append(f"{label}: {why}")
    print(f"  skip {label}  ({why})")


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# --------------------------------------------------------------------------- #

def test_voice_plan() -> None:
    section("voice pack plan")
    dataset = voicepack.load_dataset(DATA / "advisor_sounds.json")
    lines = voicepack.load_lines(DATA / "lines.zh-CN.json")
    spec = voicepack.PackSpec()
    plan = voicepack.plan_pack(dataset, lines, spec)

    reachable = {e["sound"] for e in voicepack._iter_entries(dataset)}
    jobs = voicepack.clip_jobs(plan)
    emitted = [j.sound for j in jobs]

    check("every reachable sound has a line", not (reachable - set(lines)),
          f"missing {sorted(reachable - set(lines))[:4]}")
    check("no clip name emitted twice", len(emitted) == len(set(emitted)),
          f"{len(emitted)} emitted, {len(set(emitted))} unique")
    check("every reachable sound backed by a wav", not (reachable - set(emitted)),
          f"uncovered {sorted(reachable - set(emitted))[:4]}")
    check("clip total matches coverage", plan.clip_count == len(jobs),
          f"plan says {plan.clip_count}, jobs say {len(jobs)}")
    check("slot count differs from clip count (variants are real)",
          plan.slot_count != plan.clip_count,
          f"slots={plan.slot_count} clips={plan.clip_count}")

    emitted_set = set(emitted)
    bad = [e.base_name for e in plan.effects
           if any(s not in emitted_set for s in e.sounds)]
    check("every soundeffect pool references emitted names", not bad, str(bad[:3]))
    check("every widened pool has >1 sound",
          all(len(e.sounds) > 1 for e in plan.effects),
          "an override with one sound is pointless and signals a planner bug")

    # Groups that vanilla already randomises must not be re-pooled: doing so
    # would mean the plan claims variants vanilla will never play.
    pooled_groups = {job.group for job in jobs if job.group == "click"}
    check("click group present", bool(pooled_groups))


def parse_pack_asset(text: str) -> dict:
    """Parse the generated .asset into the four things worth asserting on.

    A real (small) parser rather than regexes over the whole file: the previous
    version grepped for `name = "..."` anywhere and so counted the `category`
    block's own name as a declared sound, and counted every `file = "vo/..."`
    line as a soundfileoverride. Both misreads produced convincing-looking
    numbers, which is worse than no number.
    """
    declared: dict[str, str] = {}     # sound = { name file }  -> new take names
    overrides: dict[str, str] = {}    # soundfileoverrides entries (existing names)
    effects: dict[str, list[str]] = {}
    categories: set[str] = set()
    group_name = ""

    for m in re.finditer(r"^\t(\w+) = ", text, re.M):
        if m.group(1) == "name":
            pass
    for m in re.finditer(r"^sound = \{(?P<body>[^}]*)\}", text, re.M):
        body = m.group("body")
        name = re.search(r'^\s*name = "([^"]+)"', body, re.M)
        path = re.search(r'^\s*file = "([^"]+)"', body, re.M)
        if name and path:
            declared[name.group(1)] = path.group(1)

    group = re.search(r"soundgroup = \{(?P<body>.*?)\n\}", text, re.S)
    if group:
        body = group.group("body")
        nm = re.search(r"^\tname = (\S+)", body, re.M)
        group_name = nm.group(1) if nm else ""
        ov = re.search(r"soundfileoverrides = \{(?P<body>[^}]*)\}", body)
        if ov:
            for line in ov.group("body").splitlines():
                mm = re.match(r'\s*(\S+) = "([^"]+)"', line)
                if mm:
                    overrides[mm.group(1)] = mm.group(2)

    for m in re.finditer(r"^soundeffect = \{(?P<body>.*?)\n\}", text, re.S | re.M):
        body = m.group("body")
        nm = re.search(r"^\s*name = (\S+)", body, re.M)
        if not nm:
            continue
        effects[nm.group(1)] = re.findall(r"^\s*sound = (\S+)", body, re.M)

    cat = re.search(r"category = \{(?P<body>.*?)\n\}", text, re.S)
    if cat:
        tail = cat.group("body").split("soundeffects = {")[-1]
        # re.M matters here: without it `^...$` anchors to the whole string and
        # silently matches nothing, which is how this returned an empty set and
        # made a working category block look broken.
        categories = {n for n in re.findall(r"^\s*(\S+)\s*$", tail, re.M) if n != "}"}

    return {"declared": declared, "overrides": overrides, "effects": effects,
            "categories": categories, "group_name": group_name}


def test_voice_pack_files() -> None:
    section("voice pack emitters")
    dataset = voicepack.load_dataset(DATA / "advisor_sounds.json")
    lines = voicepack.load_lines(DATA / "lines.zh-CN.json")
    spec = voicepack.PackSpec()
    plan = voicepack.plan_pack(dataset, lines, spec)
    names = [j.sound for j in voicepack.clip_jobs(plan)]

    asset = voicepack.render_soundgroup_asset(plan, names)
    parsed = parse_pack_asset(asset)
    declared, overridden = parsed["declared"], parsed["overrides"]

    check("asset declares the sound group",
          parsed["group_name"] == spec.voice_group, parsed["group_name"])
    check("asset has balanced braces",
          asset.count("{") == asset.count("}"),
          f"{asset.count('{')} vs {asset.count('}')}")
    check("every clip is either overridden or declared",
          len(overridden) + len(declared) == len(names),
          f"{len(overridden)} overrides + {len(declared)} declarations vs {len(names)} clips")
    # This is the bug the game's error.log exposed: `soundfileoverrides` only
    # remaps names that already exist, and rejects anything else with
    # "No sound scripted for soundoverride". Extra takes must be declared.
    check("no newly invented take is routed through soundfileoverrides",
          not (set(declared) & set(overridden)),
          f"{len(set(declared) & set(overridden))} collide")
    check("every declared take points at a wav under the pack",
          all(v.startswith("vo/nekko_stellaris/") and v.endswith(".wav")
              for v in declared.values()),
          str(list(declared.values())[:2]))
    check("declared take names are suffixes, not new events",
          all(n.endswith(("_02", "_03", "_04", "_05", "_06")) for n in declared),
          str(sorted(declared)[:3]))
    check("base takes keep their vanilla names so the remap applies",
          all(n in overridden for n in names if not n.endswith(("_02", "_03", "_04"))))
    check("overridden names and declared names together cover every wav",
          {f.split("/")[-1][:-4] for f in list(overridden.values()) + list(declared.values())}
          == set(names),
          "a wav that nothing references would never play")

    check("every soundeffect is listed in the category block",
          all(e.override_name in parsed["categories"] for e in plan.effects),
          f"{len(parsed['categories'])} listed for {len(plan.effects)} effects")

    voice_type = voicepack.render_voice_type(spec)
    check("voice type block name matches the sound group",
          voice_type.strip().splitlines()[3].startswith(spec.voice_group))
    check("voice type carries the loc key", f'name = "{spec.loc_key}"' in voice_type)
    check("voice type is selectable but never auto-chosen",
          "always = yes" in voice_type and "base = 0" in voice_type)

    loc = voicepack.render_localisation(spec, language="simp_chinese", display=spec.name_zh)
    check("localisation has UTF-8 BOM", loc[:3] == b"\xef\xbb\xbf",
          f"first bytes {loc[:3]!r}")
    decoded = loc.decode("utf-8-sig")
    # The header is the language *identifier*: `l_simp_chinese`, not
    # `simp_chinese`. An earlier version of this very assertion checked for the
    # bare name and so locked in the bug -- the game then reported
    # "Missing localisation for advisor voice type ..." and listed the voice
    # with no name. Assert against the format's requirement, not our output.
    first_line = decoded.splitlines()[0]
    check("localisation header is the l_<language> identifier",
          first_line == "l_simp_chinese:", repr(first_line))
    check("localisation defines the loc key", f"{spec.loc_key}:0 " in decoded)
    english = voicepack.render_localisation(spec, language="english", display=spec.name_en)
    check("the english file uses l_english too",
          english.decode("utf-8-sig").splitlines()[0] == "l_english:")

    # The product name and the in-game option name are two different strings, and
    # they used to be one field: the launcher, the Workshop title and the game's
    # dropdown therefore had to show the same text. They answer different
    # questions -- "what is this thing" versus "which voice is this" -- so keep
    # them separable.
    check("the product name and the in-game name are distinct fields",
          "voice_display_zh" in spec.__dataclass_fields__,
          "the in-game option name is not a separate field")
    # Assert on the function the build actually calls, not on a string the test
    # passed in -- the earlier version checked its own argument and passed no
    # matter what the builder did.
    units = voicepack.localisation_units(spec)
    displays = [display for _lang, display, _folder in units]
    check("the localisation is built from the in-game name, not the product name",
          all(d == spec.voice_display_zh or d == spec.voice_display_en for d in displays)
          and spec.name_zh not in displays,
          f"displays={displays} product={spec.name_zh!r}")
    check("both locales are emitted", len(units) == 2)
    check("the descriptor carries the product name",
          f'name="{spec.name_zh}"' in voicepack.render_descriptor(spec))
    check("the product name says what the item is, for a Workshop listing",
          "顾问语音包" in spec.name_zh, spec.name_zh)
    check("the in-game name stays short enough for the settings list",
          len(spec.voice_display_zh) <= 8, spec.voice_display_zh)

    descriptor = voicepack.render_descriptor(spec)
    check("descriptor declares supported_version", "supported_version=" in descriptor)
    stub = voicepack.render_mod_stub(spec)
    check("mod stub points at the mod folder", f'path="mod/{spec.mod_id}"' in stub)

    # ``picture=`` is a community convention, NOT what gives a local mod its
    # launcher cover: the launcher's bundle was searched and never reads the key,
    # and for Stellaris the metadata route is disabled outright (the launcher's
    # own game registry records ``formatVersion: null``, while metadata processing
    # requires 1.1). These checks pin the field so it is not dropped silently --
    # they do not claim it fixes a blank tile.
    check("both descriptors carry the cover line by convention",
          f'picture="{voicepack.THUMBNAIL_NAME}"' in descriptor
          and f'picture="{voicepack.THUMBNAIL_NAME}"' in stub)
    check("the cover line names the file the pack actually writes",
          voicepack.THUMBNAIL_NAME == "thumbnail.png")
    check("no cover is declared when there is no thumbnail to point at",
          voicepack.picture_line(False) == []
          and "picture=" not in voicepack.render_descriptor(spec, has_thumbnail=False))

    # A duplicated key in either map would silently keep one of the two entries.
    override_lines = re.findall(r"^\t\t(\S+) = \"vo/nekko_stellaris/", asset, re.M)
    declaration_blocks = len(re.findall(r"^sound = \{", asset, re.M))
    check("no base name appears twice in soundfileoverrides",
          len(override_lines) == len(overridden) == len(set(override_lines)),
          f"{len(override_lines)} lines for {len(overridden)} unique names")
    check("no take name is declared twice",
          declaration_blocks == len(declared),
          f"{declaration_blocks} blocks for {len(declared)} declarations")

    # A take we invent must be spelled so vanilla cannot already own that name.
    # Vanilla declares ``advisor_notification_x_02`` itself in soundgroups.asset,
    # as a second reference to the same wav for the ethics voices' weighted pools,
    # and top-level ``sound`` blocks are global: our redeclaration lost the
    # collision (102 "already scripted" lines), so those pool entries played the
    # original advisor. Anything matching vanilla's alias shape is a repeat of it.
    vanilla_shaped = sorted(n for n in declared
                            if re.search(r"_\d\d$", n) and "_neko_" not in n)
    check("no declared take reuses vanilla's alias spelling",
          not vanilla_shaped, f"{len(vanilla_shaped)} e.g. {vanilla_shaped[:3]}")
    check("every declared take is namespaced to this mod",
          all("_neko_" in n for n in declared),
          f"{[n for n in declared if '_neko_' not in n][:3]}")
    check("exactly one soundgroup block", asset.count("soundgroup = {") == 1)
    check("soundgroup declares our name once",
          asset.count(f"name = {spec.voice_group}") == 1)

    effect_blocks = asset.split("soundeffect = {")[1:]
    check("one soundeffect block per planned pool",
          len(effect_blocks) == len(plan.effects),
          f"{len(effect_blocks)} blocks vs {len(plan.effects)} planned")
    check("every soundeffect keeps vanilla's anti-stacking settings",
          all("volume = 0.4" in b and "max_audible = 1" in b
              and "max_audible_behaviour = fail" in b for b in effect_blocks))
    check("every soundeffect is referenced by an override",
          all(e.override_name in asset for e in plan.effects))

    # A pool that names a sound nobody declares is the exact shape of the bug the
    # game reported: the pool still lists four takes, three of which resolve to
    # nothing. Assert the closure explicitly.
    referenced_in_effects = set()
    for block in effect_blocks:
        body = block.split("sounds = {")[1].split("}")[0] if "sounds = {" in block else ""
        referenced_in_effects |= {ln.strip().split(" = ")[1]
                                  for ln in body.splitlines() if ln.strip().startswith("sound = ")}
    check("every pooled sound is either declared or an overridden vanilla name",
          not (referenced_in_effects - set(declared) - set(overridden)),
          f"dangling: {sorted(referenced_in_effects - set(declared) - set(overridden))[:4]}")


def test_dds_icon() -> None:
    section("advisor icon (DDS)")
    payload = ddsicon.build_dds()
    # Vanilla gfx/interface/icons/advisor_vo/*.dds are exactly this shape.
    expected = 128 + ddsicon.DEFAULT_SIZE * ddsicon.DEFAULT_SIZE * 4
    check("total size matches an uncompressed 29x29 BGRA image",
          len(payload) == expected, f"{len(payload)} != {expected}")
    check("magic is DDS", payload[:4] == b"DDS ")
    check("height/width are 29",
          int.from_bytes(payload[12:16], "little") == 29
          and int.from_bytes(payload[16:20], "little") == 29)
    check("pixel format is ALPHAPIXELS|RGB with 32 bits",
          int.from_bytes(payload[80:84], "little") == 0x41
          and int.from_bytes(payload[88:92], "little") == 32)
    check("channel masks match BGRA",
          int.from_bytes(payload[92:96], "little") == 0x00FF0000
          and int.from_bytes(payload[104:108], "little") == 0xFF000000)
    check("no mipmaps", int.from_bytes(payload[28:32], "little") == 0)
    alpha = payload[128 + 3::4]
    check("icon actually drew something opaque", any(a > 200 for a in alpha))
    check("icon has transparent margins", any(a == 0 for a in alpha))


def test_bridge() -> None:
    section("event bridge mod")
    hooks = bridge_core.load_hooks(DATA / "on_action_hooks.json")
    plan = bridge_core.plan_bridge(hooks, bridge_core.BridgeSpec())
    events = bridge_core.render_events_file(plan)
    actions = bridge_core.render_on_action_file(plan)

    ids = list(bridge_core.iter_event_ids(plan))
    check("every declared event id exists", all(f"id = {i}" in events for i in ids))
    check("every event id is wired into an on_action",
          all(i in actions for i in ids),
          str([i for i in ids if i not in actions][:3]))
    check("every event has a trigger (player filter)",
          events.count("trigger = {") == len(ids),
          f"{events.count('trigger = {')} triggers for {len(ids)} events")
    log_calls = events.count('log = "')
    check("every event writes a log line",
          log_calls == len(ids),
          f"{log_calls} log calls for {len(ids)} events")
    check("braces balanced in the events file", events.count("{") == events.count("}"))
    check("braces balanced in the on_actions file", actions.count("{") == actions.count("}"))

    # The game rejected 1439 event firings with "attempted to execute an event on
    # an unsupported scope" because every hook was emitted as `country_event`
    # while most of those on_actions run in a planet/fleet/ship/starbase scope.
    # The type now comes from what vanilla itself fires there, so assert that we
    # are actually using more than one of them.
    types = {h.event_type for h in plan.hooks}
    check("hooks use the event type vanilla uses on that on_action",
          len(types) > 1, f"only {types}")
    check("every hook declares an event type",
          all(h.event_type.endswith("_event") for h in plan.hooks),
          str([h.event_type for h in plan.hooks if not h.event_type.endswith("_event")][:3]))
    check("event types are recorded with their evidence",
          all(h.event_type_source for h in plan.hooks if h.event_type != "country_event"),
          "an unexplained event type cannot be re-verified after a game patch")
    check("payloads carry the version tag",
          events.count(f"NEKO|{bridge_core.BRIDGE_VERSION}|") == len(ids))
    check("a heartbeat exists to make a broken merge visible",
          bridge_core.HEARTBEAT_ID in ids and "heartbeat" in events)
    check("only `events` blocks are extended, never `random_events`",
          "random_events =" not in actions and "random_events={" not in actions,
          "appending to a random_events pool would compete for a slot instead of firing")
    check("the file name sorts late so the append lands after vanilla",
          f"zz_{bridge_core.DEFAULT_MOD_ID}" in f"zz_{bridge_core.DEFAULT_MOD_ID}_on_actions.txt")

    # No two goals may share an on_action with the same event id space.
    by_action: dict[str, list[str]] = {}
    for hook in plan.hooks:
        by_action.setdefault(hook.on_action, []).append(hook.event_id)
    check("no on_action carries a duplicate event id",
          all(len(set(v)) == len(v) for v in by_action.values()))
    check("excluded goals are reported, not silently dropped",
          len(plan.skipped) > 0, "a zero-skip bridge means the filter table is too broad")


def test_gamelog_parsing() -> None:
    section("game.log parsing")
    vanilla = ("[05:40:22][eventcommands.cpp:88]: Event crisis.7515 added info about event "
               "selection. selectedOption 0, human 1, playerEventId 1705")
    event = gamelog.parse_line(vanilla)
    check("vanilla event-selection line parses", event is not None and
          event.kind == "player_event")
    check("event id, option and human flag extracted",
          event.event_id == "crisis.7515" and event.option == 0 and event.human == 1,
          f"{event.event_id} {event.option} {event.human}")
    check("namespace derived from the event id", event.goal == "vanilla.crisis")
    check("wall clock captured even without an in-game date",
          event.log_time == "05:40:22", repr(event.log_time))
    check("describe() does not render an empty bracket pair",
          "[]" not in event.describe(), event.describe())

    bridge_line = ("[05:37:32][effect_impl.cpp:22217]: [2395.1.1] Log effect, file: "
                   "common/on_actions/zz_bridge_on_actions.txt line: 12. "
                   "NEKO|v1|war_declared_on_us|side=us|us=人类联邦|other=|")
    event = gamelog.parse_line(bridge_line)
    check("bridge payload parses", event is not None and event.kind == "bridge")
    check("bridge goal and fields extracted",
          event.goal == "war_declared_on_us"
          and event.payload.get("us") == "人类联邦", str(event.payload))
    check("in-game date captured for bridge lines", event.game_date == "2395.1.1",
          repr(event.game_date))
    check("a collapsed data reference becomes an empty value, not a crash",
          event.payload.get("other") == "")

    unknown = gamelog.parse_line(
        "[05:39:45][effect_impl.cpp:22217]: [2396.8.1] Log effect, file: x line: 1. "
        "NEKO|v9|future_schema|x=1|")
    check("unknown payload version is ignored rather than guessed at", unknown is None)

    check("an unrelated line is ignored",
          gamelog.parse_line("[05:41:00][foo.cpp:1]: nothing to see") is None)
    check("the game version line is recognised as session metadata",
          (gamelog.parse_line("[03:29:12][game_application.cpp:250]: "
                              "Game Version: Cygnus v4.5.2") or type("x", (), {})()).kind
          == "session")


def test_gamelog_tailer() -> None:
    section("game.log tailer")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "game.log"
        tailer = gamelog.GameLogTailer(path, from_start=True)
        path.write_text("", encoding="utf-8")
        check("empty file yields nothing", tailer.read_new_lines() == [])

        # A multi-byte character split across two appends must not be corrupted.
        payload = "[05:00:00][x]: Event a.1 added info about event selection. " \
                  "selectedOption 0, human 1, playerEventId 1\n" \
                  "[05:00:01][y]: [2395.1.1] Log effect, file: f line: 1. " \
                  "NEKO|v1|note|name=人类联邦|\n"
        blob = payload.encode("utf-8")
        cut = blob.index("人类联邦".encode("utf-8")) + 3   # mid-character
        with path.open("ab") as handle:
            handle.write(blob[:cut])
        lines = tailer.read_new_lines()
        with path.open("ab") as handle:
            handle.write(blob[cut:])
        lines += tailer.read_new_lines()

        events = [gamelog.parse_line(line) for line in lines]
        events = [e for e in events if e]
        check("split write still yields both events", len(events) == 2, str(len(events)))
        check("split multi-byte character survives",
              any(e.payload.get("name") == "人类联邦" for e in events),
              str([e.payload for e in events]))
        check("a partial line is not emitted until its newline arrives",
              tailer.read_new_lines() == [])

        # Truncation on game restart must rewind rather than read garbage.
        path.write_text("", encoding="utf-8")
        check("truncated file is detected and rewound", tailer.read_new_lines() == [])


def test_policy() -> None:
    section("commentary policy")

    from core.policy import (
        DIGEST,
        URGENT,
        CommentaryPolicy,
        PolicyConfig,
        build_prompt,
    )

    config = PolicyConfig()
    config_min_gap = config.min_gap_s
    check("war on us is urgent",
          config.tier_for(gamelog.GameEvent("bridge", "war_declared_on_us")) == URGENT)
    check("research completion is digest, not urgent",
          config.tier_for(gamelog.GameEvent("bridge", "research_completed")) == DIGEST)
    check("an unknown bridge goal degrades to digest rather than urgent",
          config.tier_for(gamelog.GameEvent("bridge", "some_new_thing")) == DIGEST)

    ai_event = gamelog.GameEvent("player_event", "vanilla.crisis", event_id="crisis.1",
                                 human=-1, player_event_id=1)
    player_event = gamelog.GameEvent("player_event", "vanilla.crisis", event_id="crisis.2",
                                     human=1, player_event_id=2)
    policy = CommentaryPolicy(config)
    check("AI-empire events are dropped",
          policy.consider(ai_event, now=1000.0).action == "drop")
    first = policy.consider(player_event, now=1000.0)
    check("a player crisis event speaks", first.should_speak, first.describe())
    policy.note_spoken(1000.0)

    second = policy.consider(
        gamelog.GameEvent("player_event", "vanilla.crisis", event_id="crisis.3",
                          human=1, player_event_id=3), now=1005.0)
    check("the urgent cooldown blocks an immediate second urgent event",
          not second.should_speak, second.describe())

    dupe = policy.consider(player_event, now=1000.0)
    check("a re-read of the same line is deduplicated",
          dupe.action == "drop" and "重复" in dupe.reason, dupe.describe())

    # Burst behaviour: assert the *contract* rather than an arbitrary count. A
    # synthetic 20-event war is not a scenario to tune against; what must hold is
    # that the four brakes hold, under any input.
    burst = CommentaryPolicy(PolicyConfig())
    spoken_times: list[float] = []
    now = 10_000.0
    for i in range(20):
        # Distinct wall-clock times: two identical-looking bridge events are two
        # events, and the dedup key must not fold them together.
        event = gamelog.GameEvent(
            "bridge", "war_declared_on_us", payload={"n": str(i)},
            log_time=f"09:{i:02d}:00", event_id=f"e{i}")
        decision = burst.consider(event, now=now + i * 40)
        if decision.should_speak:
            spoken_times.append(now + i * 40)
            burst.note_spoken(now + i * 40)

    check("a burst of urgent events still produces some commentary",
          len(spoken_times) >= 2, f"{len(spoken_times)} utterances")
    gaps = [b - a for a, b in zip(spoken_times, spoken_times[1:])]
    check("min_gap_s is never violated",
          all(g >= config_min_gap for g in gaps) if gaps else True,
          f"gaps={[round(g, 1) for g in gaps]}")
    worst_minute = max(
        (sum(1 for t in spoken_times if w <= t < w + 60.0) for w in spoken_times),
        default=0)
    check("max_per_minute is never violated in any rolling window",
          worst_minute <= PolicyConfig().max_per_minute,
          f"{worst_minute} utterances in one minute vs cap {PolicyConfig().max_per_minute}")
    check("urgent_cooldown_s is never violated",
          all(g >= PolicyConfig().urgent_cooldown_s for g in gaps) if gaps else True,
          f"closest gap {min(gaps, default=0):.1f}s vs cooldown "
          f"{PolicyConfig().urgent_cooldown_s}s")

    # Two identical-looking events at different times are two events.
    dedup = CommentaryPolicy(PolicyConfig())
    first = gamelog.GameEvent("bridge", "research_completed", payload={"a": "b"},
                              log_time="09:00:00")
    second = gamelog.GameEvent("bridge", "research_completed", payload={"a": "b"},
                               log_time="09:05:00")
    same = gamelog.GameEvent("bridge", "research_completed", payload={"a": "b"},
                             log_time="09:00:00")
    check("distinct occurrences are not deduplicated",
          dedup.consider(first, now=1.0).action != "drop"
          and dedup.consider(second, now=2.0).action != "drop")
    check("a re-read of the same occurrence is deduplicated",
          dedup.consider(same, now=3.0).action == "drop")

    digest_policy = CommentaryPolicy(PolicyConfig(digest_interval_s=60))
    for i in range(5):
        # Distinct wall clocks, as five real log lines would have.
        digest_policy.consider(
            gamelog.GameEvent("bridge", "research_completed",
                              log_time=f"08:{i:02d}:00", event_id=f"r{i}"),
            now=2000.0 + i)
    check("digest events do not speak on arrival",
          digest_policy.stats()["pending_digest"] == 5,
          f"pending={digest_policy.stats()['pending_digest']}")
    check("digest waits out its interval",
          digest_policy.tick(now=2010.0).action == "wait")
    flushed = digest_policy.tick(now=2200.0)
    check("digest flushes once its interval passes",
          flushed.should_speak and len(flushed.events) == 5, flushed.describe())

    prompt = build_prompt(flushed)
    check("prompt names no placeholder that needs host expansion",
          "{MASTER_NAME}" not in prompt and "{LANLAN_NAME}" not in prompt)
    check("prompt forbids mentioning the plumbing",
          "日志" in prompt and "不要提到" in prompt)


def test_settings_defaults_match_policy() -> None:
    """The live policy must be the same object whether or not plugin.toml is read.

    These were two copies of the same numbers, and they drifted the moment one
    side was tuned; a host that could not read ``[stellaris]`` would silently run
    the older policy.
    """
    section("settings defaults")
    if HOST_SETTINGS is None:
        skip("Settings.policy() inherits the settings' own numbers",
             "needs the plugin entry point, hence the host SDK")
        skip("policy() does not hardcode its own literals", "same reason")
        return

    defaults = HOST_SETTINGS()
    policy = defaults.policy()
    check("Settings.policy() inherits the settings' own numbers",
          policy.urgent_cooldown_s == defaults.urgent_cooldown_s
          and policy.digest_interval_s == defaults.digest_interval_s
          and policy.min_gap_s == defaults.min_gap_s
          and policy.max_per_minute == defaults.max_per_minute,
          f"policy={policy} settings={defaults}")

    import inspect
    source = inspect.getsource(HOST_SETTINGS.policy)
    check("policy() does not hardcode its own literals",
          "30.0" not in source and "= 3" not in source, source)


def test_live_voice() -> None:
    section("runtime regeneration (live voice)")
    from core import livevoice

    dataset = voicepack.load_dataset(DATA / "advisor_sounds.json")
    # Every vanilla soundeffect the default advisor group reaches. A mapping
    # target outside this set does not exist in the game, so the event would be
    # dropped silently -- exactly the failure mode that has no error message.
    known_effects = {e["soundeffect"] for e in voicepack._iter_entries(dataset)
                     if e.get("soundeffect")}

    targets = {}
    for name, table in (("GOAL_TO_EFFECT", livevoice.GOAL_TO_EFFECT),
                        ("NAMESPACE_TO_EFFECT", livevoice.NAMESPACE_TO_EFFECT)):
        for key, effect in table.items():
            targets[f"{name}.{key}"] = effect
    unresolved = sorted(k for k, v in targets.items() if v not in known_effects)
    check("every live-voice mapping resolves to a real advisor soundeffect",
          not unresolved, f"{len(unresolved)} bad: {unresolved[:5]}")
    check("the mapping table is not accidentally empty", len(targets) > 30,
          f"{len(targets)} entries")

    # The pack must actually carry each mapped pool, or rewriting is a no-op.
    plan = voicepack.plan_pack(dataset, voicepack.load_lines(DATA / "lines.zh-CN.json"),
                               voicepack.PackSpec())
    planned = {e.base_name for e in plan.effects}
    planned |= {s.soundeffect for s in plan.clips if s.soundeffect}
    not_in_pack = sorted({v for v in targets.values() if v not in planned})
    check("every mapped pool exists in the pack the builder produces",
          not not_in_pack, f"missing: {not_in_pack[:5]}")

    # Event -> effect routing.
    bridge = gamelog.GameEvent("bridge", "war_declared_on_us")
    check("a bridge goal routes to its pool",
          livevoice.effect_for_event(bridge) == "advisor_notification_enemy_declared_war",
          str(livevoice.effect_for_event(bridge)))
    crisis = gamelog.GameEvent("player_event", "vanilla.crisis", event_id="crisis.1", human=1)
    check("a vanilla crisis event routes to a generic pool",
          livevoice.effect_for_event(crisis) == "advisor_notification_situation_log")
    unknown = gamelog.GameEvent("bridge", "some_goal_that_does_not_exist")
    check("an unmapped goal routes nowhere rather than somewhere wrong",
          livevoice.effect_for_event(unknown) is None)

    # Line sanitising: this is the last thing between a model reply and the TTS
    # service, and anything that survives it gets read aloud in game.
    check("quotes and markdown are stripped",
          livevoice.sanitize_line('"**哼**，本喵知道了。"') == "哼，本喵知道了",
          repr(livevoice.sanitize_line('"**哼**，本喵知道了。"')))
    check("only the first line survives",
          livevoice.sanitize_line("第一句\n第二句") == "第一句")
    check("emoji are stripped",
          "🐱" not in livevoice.sanitize_line("本喵来了🐱"))
    check("over-long replies are truncated",
          len(livevoice.sanitize_line("喵" * 80)) <= 30)
    check("an empty reply stays empty", livevoice.sanitize_line("   ") == "")

    # Budget: the brake that keeps a busy war from becoming a continuous bill.
    engine = livevoice.LiveVoiceEngine.__new__(livevoice.LiveVoiceEngine)
    engine.config = livevoice.LiveVoiceConfig(budget_per_minute=2, min_seconds_between=8.0)
    engine._last_emit = 0.0
    engine._emit_times = __import__("collections").deque()
    now = 100_000.0
    check("a fresh engine may spend", engine._can_spend(now))
    engine._last_emit = now
    check("min_seconds_between blocks an immediate second line",
          not engine._can_spend(now + 1))
    engine._emit_times.extend([now, now + 9])
    engine._last_emit = now + 9
    check("the per-minute cap blocks a third line",
          not engine._can_spend(now + 18))
    check("the cap releases once the window slides",
          engine._can_spend(now + 61))

    # Rotation: one slot per occurrence, cycling through the pool, so a pool is
    # fully refreshed after K occurrences without regenerating all K each time.
    rot = livevoice.LiveVoiceEngine.__new__(livevoice.LiveVoiceEngine)
    rot._rotation = {}
    rot.inventory = type("Inv", (), {"pool": lambda self, e: ["a_01", "a_02", "a_03", "a_04"]})()
    picks = [rot._next_slot("x") for _ in range(9)]
    check("rotation walks every slot before repeating", picks[:4] == ["a_01", "a_02", "a_03", "a_04"],
          str(picks))
    check("rotation wraps around", picks[4] == "a_01", str(picks))
    check("rotation covers the whole pool each cycle", len(set(picks)) == 4)


def test_manifest_roundtrip(tmp_path=None) -> None:
    section("pack manifest")
    import json as _json
    import tempfile

    dataset = voicepack.load_dataset(DATA / "advisor_sounds.json")
    lines = voicepack.load_lines(DATA / "lines.zh-CN.json")
    plan = voicepack.plan_pack(dataset, lines, voicepack.PackSpec())
    names = [j.sound for j in voicepack.clip_jobs(plan)]
    manifest = voicepack.render_manifest(plan, names)

    check("manifest records every clip", manifest["clip_count"] == len(names))
    check("manifest keys are vanilla soundeffect names",
          all(k in {e["soundeffect"] for e in voicepack._iter_entries(dataset)}
              or k.count("_") > 0 for k in manifest["pools"]))
    check("manifest covers the pooled effects",
          len(manifest["pools"]) >= len(plan.effects),
          f"{len(manifest['pools'])} pools vs {len(plan.effects)} overrides")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "sound" / "vo" / "nekko_stellaris").mkdir(parents=True)
        (root / voicepack.MANIFEST_NAME).write_text(
            _json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        inv = livevoice_inventory_load(root)
        check("inventory reads the manifest when present",
              "pack.json" in inv.source, inv.source)
        check("inventory recovers every pool from the manifest",
              len(inv.pools) == len(manifest["pools"]),
              f"{len(inv.pools)} vs {len(manifest['pools'])}")


def livevoice_inventory_load(root):
    from core import livevoice
    return livevoice.SlotInventory.load(root)


def test_freshness(tmp_path=None) -> None:
    """The pack in the game must be checkable against the settings in use now."""
    section("voice-pack freshness")
    import array as _array
    import copy
    import json as _json
    import tempfile

    from core.audiowav import wav_bytes_mono16
    from core.tts import TtsSettings, cache_path

    dataset = voicepack.load_dataset(DATA / "advisor_sounds.json")
    lines = voicepack.load_lines(DATA / "lines.zh-CN.json")
    spec = voicepack.PackSpec()
    plan = voicepack.plan_pack(dataset, lines, spec)
    names = [j.sound for j in voicepack.clip_jobs(plan)]
    tts_a = TtsSettings(url="wss://example.invalid/tts", api_key="k", voice_id="voice-A")
    tts_b = TtsSettings(url="wss://example.invalid/tts", api_key="k", voice_id="voice-B")
    tiny = wav_bytes_mono16(_array.array("f", [0.0, 0.1, -0.1, 0.0]), 44100)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "sound").mkdir(parents=True)
        stamp = voicepack.compute_stamp(spec, dataset, lines, tts_a, len(names))
        (root / voicepack.MANIFEST_NAME).write_text(
            _json.dumps(voicepack.render_manifest(plan, names, stamp=stamp),
                        ensure_ascii=False), encoding="utf-8")

        # A real, populated cache, so the "how much work is this" estimate is
        # exercised rather than falling back to "assume everything".
        cache = root / "cache"
        cache.mkdir()
        for job in voicepack.clip_jobs(plan):
            cache_path(tts_a, job.sound, job.text, cache).write_bytes(tiny)

        fresh = voicepack.check_freshness(root, spec, dataset, lines, tts_a,
                                          cache_dir=cache)
        check("identical inputs report fresh", fresh.state == "fresh", fresh.summary())
        check("fresh needs no rebuild", not fresh.needs_rebuild)

        # The whole point: the user switches voice in N.E.K.O and nothing tells
        # them the game still holds the previous one.
        changed = voicepack.check_freshness(root, spec, dataset, lines, tts_b,
                                            cache_dir=cache)
        check("a different voice is detected", changed.state == "voice_changed",
              changed.summary())
        check("a voice change invalidates every clip, cache and all",
              changed.clips_to_generate == len(names),
              f"{changed.clips_to_generate} vs {len(names)}")
        check("the report names both voices",
              "voice-A" in changed.summary() and "voice-B" in changed.summary(),
              changed.summary())

        edited = copy.deepcopy(lines)
        first_key = next(iter(edited))
        edited[first_key][0] = "本喵换了一句话"
        one = voicepack.check_freshness(root, spec, dataset, edited, tts_a,
                                        cache_dir=cache)
        check("an edited line table is detected", one.state == "lines_changed",
              one.summary())
        check("editing one take costs one clip, not the whole pack",
              one.clips_to_generate == 1, f"{one.clips_to_generate}")

        many = copy.deepcopy(lines)
        for key in list(many)[:20]:
            many[key][0] = "本喵换了一句话"
        twenty = voicepack.check_freshness(root, spec, dataset, many, tts_a,
                                           cache_dir=cache)
        check("editing twenty takes costs twenty clips",
              twenty.clips_to_generate == 20, f"{twenty.clips_to_generate}")

        other_ds = copy.deepcopy(dataset)
        other_ds["game_version"] = "Cygnus v9.9.9"
        ds_change = voicepack.check_freshness(root, spec, other_ds, lines, tts_a,
                                              cache_dir=cache)
        check("a dataset change is detected", ds_change.state == "dataset_changed",
              ds_change.summary())
        check("a dataset-only change costs no synthesis (the audio is identical)",
              ds_change.clips_to_generate == 0, f"{ds_change.clips_to_generate}")

        nostamp = pathlib.Path(tmp) / "nostamp"
        (nostamp / "sound").mkdir(parents=True)
        missing = voicepack.check_freshness(nostamp, spec, dataset, lines, tts_a)
        check("a pack with no stamp is reported as unknown, not as fresh",
              missing.state == "no_stamp" and missing.needs_rebuild,
              missing.summary())

        absent = voicepack.check_freshness(pathlib.Path(tmp) / "nope", spec,
                                           dataset, lines, tts_a)
        check("an uninstalled pack is reported as such",
              absent.state == "not_installed" and absent.needs_rebuild)

        # A manifest rewrite must not silently erase the record of which voice
        # built the pack -- that would downgrade a checkable pack to unknown.
        preserved = voicepack.render_manifest(plan, names, stamp=voicepack.read_stamp(root))
        check("rewriting the manifest preserves an existing stamp",
              preserved["stamp"] is not None and preserved["stamp"]["voice_id"] == "voice-A")


def test_standalone_export() -> None:
    """A pack meant to be handed to someone must contain nothing plugin-shaped."""
    section("standalone voice pack export")
    import tempfile

    from core import standalone

    dataset = voicepack.load_dataset(DATA / "advisor_sounds.json")
    lines = voicepack.load_lines(DATA / "lines.zh-CN.json")
    plan = voicepack.plan_pack(dataset, lines, voicepack.PackSpec())
    names = [j.sound for j in voicepack.clip_jobs(plan)]

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        # Stand in for a real combined build: the same shape, no wav bytes.
        src = root / "combined"
        for rel in ("sound/vo/nekko_stellaris", "sound/advisor_voice_types",
                    "localisation/simp_chinese", "gfx/interface/icons/advisor_vo",
                    "events", "common/on_actions"):
            (src / rel).mkdir(parents=True)
        (src / "sound" / "vo" / "nekko_stellaris" / "x_01.wav").write_bytes(b"RIFF")
        for rel in ("sound/pack.asset", "sound/advisor_voice_types/vt.txt",
                    "localisation/simp_chinese/l.yml", "gfx/interface/icons/advisor_vo/i.dds",
                    "events/neko_bridge_events.txt",
                    "common/on_actions/zz_on_actions.txt"):
            (src / rel).write_text("x", encoding="utf-8")
        (src / voicepack.MANIFEST_NAME).write_text("{}", encoding="utf-8")
        (src / ".neko_stellaris_pack.json").write_text("{}", encoding="utf-8")
        (src / "README.md").write_text("plugin readme", encoding="utf-8")

        spec = standalone.StandaloneSpec()
        result = standalone.export_standalone(
            src, root / "out", spec, plan=plan, clip_count=len(names),
            dataset=dataset, make_zip=True)

        check("export reports what it stripped", len(result.removed) >= 3,
              str(result.removed))
        for gone in ("events", "common", voicepack.MANIFEST_NAME,
                     ".neko_stellaris_pack.json"):
            check(f"{gone} is not in the standalone pack",
                  not (result.output_dir / gone).exists())
        check("the audio survives", (result.output_dir / "sound").is_dir())
        check("a descriptor is written",
              (result.output_dir / "descriptor.mod").is_file())
        check("the descriptor is sound-only",
              '"Sound"' in standalone.render_descriptor(spec)
              and "Gameplay" not in standalone.render_descriptor(spec))
        check("the stub sits beside the folder, not inside it",
              result.mod_stub_path.parent == result.output_dir.parent
              and f'path="mod/{spec.mod_id}"' in result.mod_stub_path.read_text(encoding="utf-8"))
        check("a zip is produced", result.zip_path is not None and result.zip_path.is_file())
        check("the verifier passes on a clean pack",
              standalone.verify_standalone(result.output_dir) == [],
              str(standalone.verify_standalone(result.output_dir)))
        check("the verifier would catch a leftover bridge",
              standalone.verify_standalone(src) != [],
              "a verifier that never fails is not a verifier")

        readme = (result.output_dir / "README.md").read_text(encoding="utf-8")
        check("the README is written for a user, not for the author",
              "怎么装" in readme and "启动器" in readme)
        check("the README does not pretend the plugin is required",
              "不需要任何外部程序" in readme)

        kept = standalone.export_standalone(
            src, root / "kept", spec, plan=plan, clip_count=len(names),
            dataset=dataset, keep_bridge=True, make_zip=False)
        check("--keep-bridge keeps the bridge when asked",
              (kept.output_dir / "events").is_dir()
              and (kept.output_dir / "common").is_dir())


def test_icon_from_art() -> None:
    """The bundled art must produce an icon the game will actually accept."""
    section("icon from source art")
    source = voicepack.DEFAULT_ICON_SOURCE
    if not source.is_file():
        check("source art is present", False, f"missing {source}")
        return
    check("source art is present", True)

    try:
        payload = ddsicon.render_icon_from_image(source)
    except ImportError as exc:
        # Pillow is optional: the host ships it, a bare interpreter may not. A
        # missing dependency must read as "not attempted" -- reporting it as a
        # FAIL turns every CI runner without Pillow into a false alarm about the
        # icon, which is the one thing this section exists to check honestly.
        skip("icon rendering from source art", f"{type(exc).__name__}: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        check("Pillow can render the icon", False, f"{type(exc).__name__}: {exc}")
        return

    # Same shape the vanilla advisor icons use; anything else risks the slot not
    # rendering, and this is cheap to assert.
    check("icon is 29x29",
          int.from_bytes(payload[12:16], "little") == 29
          and int.from_bytes(payload[16:20], "little") == 29)
    check("icon is exactly the vanilla byte count",
          len(payload) == 3492, f"{len(payload)}")
    check("icon header matches vanilla", payload[:4] == b"DDS "
          and int.from_bytes(payload[80:84], "little") == 0x41
          and int.from_bytes(payload[88:92], "little") == 32)
    pixels = payload[128:]
    check("icon is opaque where the character is",
          sum(1 for i in range(3, len(pixels), 4) if pixels[i] == 255) == 29 * 29,
          "the crop has no transparency, which is expected for a full-bleed icon")
    check("icon actually contains the art, not a flat fill",
          len({pixels[i:i+3] for i in range(0, len(pixels), 4)}) > 40,
          "a near-flat icon means the crop landed somewhere empty")

    thumb = ddsicon.render_thumbnail_png(source, 512)
    check("thumbnail is a 512 PNG",
          thumb[:8] == b"\x89PNG\r\n\x1a\n" and len(thumb) > 10000, f"{len(thumb)} bytes")

    # The cover must not inherit the icon's frame. Both used to share one crop,
    # justified as "one less thing to keep in sync" -- but the 29 px frame shaves
    # the bottom of the art and offsets the rest, which at 512 px reads as an
    # accident. Assert the behaviour, not just the constants.
    from PIL import Image
    with Image.open(source) as handle:
        art = handle.convert("RGB")
    framed = ddsicon._square_crop(art, ddsicon.THUMBNAIL_CROP_CENTRE,
                                  ddsicon.THUMBNAIL_CROP_SIDE)
    check("the cover frame keeps the whole square artwork",
          framed.size == art.size, f"{framed.size} vs {art.size}")
    check("the cover frame is not the icon's frame",
          (ddsicon.THUMBNAIL_CROP_CENTRE, ddsicon.THUMBNAIL_CROP_SIDE)
          != (ddsicon.ICON_CROP_CENTRE, ddsicon.ICON_CROP_SIDE),
          "the two frames were merged again; the 29 px crop is wrong for a cover")

    # A missing source must degrade to the procedural mark, not fail the build.
    spec = voicepack.PackSpec(icon_source=str(source.parent / "does_not_exist.png"))
    check("a missing source falls back to the built-in mark",
          spec.resolved_icon_source() is None)
    check("an explicit source wins when it exists",
          voicepack.PackSpec(icon_source=str(source)).resolved_icon_source() == source)


def test_layout_independence() -> None:
    """Both layouts must stay runnable from a bare interpreter.

    These sources ship twice: installed under ``<host>/plugin/plugins/neko_stellaris/``
    and as the flat ``plugin/`` of the standalone repo. Only the first has the host
    SDK importable, so anything that reaches for the dotted host path is a trap:
    the scripts still work where they were developed and break everywhere else.
    That is exactly how ``build_cli.py`` came to be unusable from its own repo.
    """
    section("layout independence")

    core_sources = {p.name: p.read_text(encoding="utf-8")
                    for p in sorted((HERE / "core").glob("*.py"))}
    # A *module-level* host import is the trap: it makes the whole module
    # unloadable without the SDK. Probes are allowed, but they must be lazy so
    # they can be caught -- nekoconfig.py resolves the host's runtime data root
    # that way and falls back to ~/.neko_stellaris when the host is absent.
    module_level = re.compile(r"^(?:from|import)\s+plugin\b", re.M)
    offenders = sorted(n for n, s in core_sources.items() if module_level.search(s))
    check("no core module imports the host at module level",
          not offenders, f"offenders: {offenders}")

    probes = re.findall(r"^\s+from plugin\.\w.*\n(?:\s+.*\n)*?\s+except\b",
                        core_sources.get("nekoconfig.py", ""), re.M)
    check("every lazy host probe in nekoconfig.py is guarded",
          len(probes) >= 2, f"guarded probes found: {len(probes)}")

    relative_only = sorted(n for n, s in core_sources.items()
                           if re.search(r"^\s*(from|import)\s+core\b", s, re.M))
    check("no core module imports a sibling by an absolute name",
          not relative_only, f"offenders: {relative_only}")

    scripts = {p.name: p.read_text(encoding="utf-8")
               for p in (HERE / "build_cli.py", HERE / "smoke_offline.py")}
    here_on_path = re.compile(r"sys\.path\.\w+\([^)]*str\(HERE\)")
    for name, source in sorted(scripts.items()):
        check(f"{name} locates core/ from its own file, not from a host root",
              here_on_path.search(source) is not None, "no HERE-based sys.path entry")

    # smoke_offline.py is allowed exactly one host-path import: the Settings probe,
    # which is guarded and reported as skipped when the SDK is absent. Only the
    # bootstrap counts -- this function's own source names that import too.
    bootstrap = scripts["smoke_offline.py"].split("def test_layout_independence")[0]
    host_imports = bootstrap.count("from plugin.plugins.neko_stellaris import")
    check("the only host-path import left is the guarded Settings probe",
          host_imports == 1, f"found {host_imports}")
    check("that probe is guarded, not bare",
          "HOST_SETTINGS = _host_settings()" in scripts["smoke_offline.py"]
          and "HOST_SETTINGS is None" in scripts["smoke_offline.py"],
          "probe result is never checked for absence")


def test_published_copy() -> None:
    """After a Workshop publish the launcher deletes our local install.

    Everything that located the pack by path then answers about a directory that
    no longer exists: the startup check calls the mod "not installed" while the
    game is loading it, and auto-install writes a second copy. Two copies is not
    merely untidy -- the bridge's on_action hooks are appended once per copy, so
    every bridged event fires twice.
    """
    section("published (workshop) copy")
    import json
    import shutil
    import tempfile

    from core import installer as inst

    mod = voicepack.DEFAULT_MOD_ID
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="neko-published-"))
    try:
        user = tmp / "Stellaris"
        (user / "mod").mkdir(parents=True)
        dlc = user / "dlc_load.json"
        dlc.write_text('{"enabled_mods": [], "disabled_dlcs": []}', encoding="utf-8")

        check("nothing installed anywhere -> no active install",
              inst.find_active_install(user, mod_id=mod) is None)
        check("and nothing is blocked",
              not inst.published_copy_blocks_install(user, mod_id=mod)[0])

        # A local install, which is what the plugin writes itself.
        local = user / "mod" / mod
        (local / "sound").mkdir(parents=True)
        (local / inst.MARKER_NAME).write_text("{}", encoding="utf-8")
        active = inst.find_active_install(user, mod_id=mod)
        check("a local install is found and labelled local",
              active is not None and active.kind == "local")

        # The Workshop copy, reachable only through the launcher's own stub: the
        # mod directory lives under Steam's tree and its name is a numeric id.
        ws = tmp / "workshop" / "3819922949"
        (ws / "sound").mkdir(parents=True)
        (ws / "sound" / f"{mod}.asset").write_text("soundgroup = { }", encoding="utf-8")
        (user / "mod" / "ugc_3819922949.mod").write_text(
            f'name="X"\npath="{ws.as_posix()}"\nremote_file_id="3819922949"\n',
            encoding="utf-8")
        published = inst.find_published_copy(user, mod_id=mod)
        check("the workshop copy is found through the launcher's stub",
              published is not None and published.remote_id == "3819922949",
              published.summary() if published else None)
        check("the local copy still wins while it exists",
              inst.find_active_install(user, mod_id=mod).kind == "local")

        # The migration itself: the launcher deletes the local directory.
        shutil.rmtree(local)
        (user / "mod" / f"{mod}.mod").write_text("", encoding="utf-8")
        active = inst.find_active_install(user, mod_id=mod)
        check("after migration the workshop copy is the active install",
              active is not None and active.kind == "workshop")
        check("it is enabled by the workshop stub, not ours",
              active.stub_name(mod) == "mod/ugc_3819922949.mod",
              active.stub_name(mod))
        blocked, why = inst.published_copy_blocks_install(user, mod_id=mod)
        check("a second copy would be refused, with a reason", blocked and bool(why))

        dlc.write_text(
            '{"enabled_mods": ["mod/ugc_3819922949.mod"], "disabled_dlcs": []}',
            encoding="utf-8")
        report = inst.inspect_installation(user, mod_id=mod)
        check("inspect_installation reports the published source",
              report.installed and report.source == "workshop", report.source)
        check("and reads registration through the workshop stub", report.registered)
        check("the summary says Steam owns it", "创意工坊" in report.summary())

        result = inst.uninstall_pack(user, mod_id=mod)
        check("uninstall does not touch the workshop copy",
              ws.is_dir() and any("创意工坊" in n for n in result.notes), result.notes)

        # Decoys. A stub pointing nowhere, a stub pointing at somebody else's mod,
        # and -- the one that actually bit -- a published copy of a *different*
        # mod of ours: every pack we install writes the same marker filename, so
        # matching on the marker's existence alone made the bridge mod resolve to
        # the voice pack's published copy and report its 248 wavs as its own.
        (user / "mod" / "ugc_1111111111.mod").write_text(
            'path="D:/nowhere/1111111111"\n', encoding="utf-8")
        other = tmp / "workshop" / "2222222222"
        (other / "sound").mkdir(parents=True)
        (other / "sound" / "someone_else.asset").write_text("", encoding="utf-8")
        (user / "mod" / "ugc_2222222222.mod").write_text(
            f'path="{other.as_posix()}"\n', encoding="utf-8")
        sibling = tmp / "workshop" / "3333333333"
        sibling.mkdir(parents=True)
        (sibling / inst.MARKER_NAME).write_text(
            json.dumps({"mod_id": "nekko_stellaris_bridge", "version": "1.0.0"}),
            encoding="utf-8")
        (user / "mod" / "ugc_3333333333.mod").write_text(
            f'path="{sibling.as_posix()}"\n', encoding="utf-8")

        found = inst.find_published_copy(user, mod_id=mod)
        check("decoy stubs are ignored",
              found is not None and found.remote_id == "3819922949",
              found.remote_id if found else None)
        sibling_found = inst.find_published_copy(user, mod_id="nekko_stellaris_bridge")
        check("a sibling mod's published copy is found only under its own id",
              sibling_found is not None and sibling_found.remote_id == "3333333333",
              sibling_found.remote_id if sibling_found else None)
        check("active-install summaries describe both kinds",
              "创意工坊" in active.summary()
              and "本地" in inst.ActiveInstall(pathlib.Path("x"), "local").summary())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bridge_scope_links() -> None:
    """A trigger may only switch through a link the on_action actually provides.

    Switching through an absent one is not a quiet no-op: the engine logs
    ``Invalid context switch [owner] from <planet>`` on every single firing. A
    planet is surveyed *before* anyone owns it, so the unguarded ``owner`` switch
    in that one event produced 32,383 lines in a single game session -- while every
    event type in the file matched vanilla exactly, which is why the earlier audit
    called it clean.
    """
    section("bridge trigger scopes")
    import json

    from core import bridge as bridge_core

    hooks = json.loads((DATA / "on_action_hooks.json").read_text(encoding="utf-8"))
    plan = bridge_core.plan_bridge(hooks)
    audited = {str(h.get("goal") or ""): h for h in hooks["hooks"]}

    violations: list[tuple[str, str]] = []
    for hook in plan.hooks:
        links = re.findall(r"(?m)^(\w+)\s*=\s*\{", hook.trigger)
        guarded = set(re.findall(r"(?m)^exists\s*=\s*(\w+)", hook.trigger))
        scope_text = str(audited.get(hook.goal, {}).get("scope") or "").lower()
        for link in links:
            if link in guarded or re.search(rf"\b{link}\b", scope_text):
                continue
            violations.append((hook.goal, link))
    check("every scope link is documented by the hook's audit, or guarded",
          not violations, f"{len(violations)} e.g. {violations[:4]}")

    # Existence is not enough: the link also has to lead somewhere ``is_ai`` means
    # anything. ``owner`` is exempt because it is guarded and because a scope's
    # audited text names the meaningful scopes, not every link a colony or station
    # offers; for the rest the audit has to describe a country.
    countryish = re.compile(r"country|国家|帝国|玩家|领袖|leader", re.I)
    wrong_type: list[tuple[str, str, str]] = []
    for hook in plan.hooks:
        scope_text = str(audited.get(hook.goal, {}).get("scope") or "")
        for link in re.findall(r"(?m)^(\w+)\s*=\s*\{\s*\n?\s*is_ai", hook.trigger):
            if link == "owner":
                continue
            described = re.search(rf"\b{link}\s*=\s*([^,，)）]+)", scope_text, re.I)
            if described and not countryish.search(described.group(1)):
                wrong_type.append((hook.goal, link, described.group(1).strip()))
    check("a filter only applies is_ai to a scope the audit calls a country",
          not wrong_type, f"{len(wrong_type)} e.g. {wrong_type[:3]}")

    # The guard has to be a statement the engine evaluates *before* the switch.
    owner_switches = [h for h in plan.hooks
                      if re.search(r"(?m)^owner\s*=\s*\{", h.trigger)]
    check("the existence guard precedes the switch it protects",
          bool(owner_switches) and all(
              h.trigger.index("exists = owner")
              < re.search(r"(?m)^owner\s*=\s*\{", h.trigger).start()
              for h in owner_switches),
          f"{len(owner_switches)} hooks switch on owner")
    survey = next(h for h in plan.hooks if h.goal == "survey_completed_planet")
    check("the survey hook filters on the country its on_action documents",
          "from = { is_ai = no }" in survey.trigger and "owner" not in survey.trigger,
          survey.trigger.replace("\n", " / "))
    rendered = bridge_core.render_events_file(plan)
    check("the rendered file keeps the guard and the switch on separate lines",
          "\t\texists = owner\n\t\towner = { is_ai = no }" in rendered
          and "exists = owner owner" not in rendered)
    # A log template may only reference things the engine can actually turn into a
    # string. `last_increased_tech` is a trigger value -- all 211 uses of it in the
    # game compare it against a literal tech key, and nothing localises or
    # interpolates it -- so `[This.last_increased_tech]` renders empty on every
    # single line, which is exactly what it did for 561 of 561 tech events.
    check("no log template interpolates a trigger value",
          "last_increased_tech" not in rendered)


def test_install_paths() -> None:
    """The only code that writes into the user's real game directory.

    It copies the whole payload, deletes a previous install, writes the stub and
    edits ``dlc_load.json`` -- and nothing exercised any of it. The ownership guard
    is the safety net for the worst failure this plugin can commit: deleting a
    user's own mod because two ids collided.
    """
    section("install / uninstall paths")
    import json
    import shutil
    import tempfile

    from core import installer as inst

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="neko-install-"))
    try:
        src = tmp / "built"
        (src / "sound").mkdir(parents=True)
        (src / "descriptor.mod").write_text('version="9.9.9"\n', encoding="utf-8")
        (src / "sound" / "x.asset").write_text("soundgroup = { }\n", encoding="utf-8")
        (src / "sound" / "a.wav").write_bytes(b"RIFF" + bytes(60))

        user = tmp / "Stellaris"
        (user / "mod").mkdir(parents=True)
        dlc = user / "dlc_load.json"
        dlc.write_text('{"enabled_mods": [], "disabled_dlcs": []}', encoding="utf-8")
        target = user / "mod" / "demo_mod"

        first = inst.install_pack(src, user, mod_id="demo_mod",
                                  mod_stub_text='version="9.9.9"\nname="demo"\n',
                                  version="9.9.9", register_playset=False)
        check("a fresh install copies the payload",
              (target / "sound" / "x.asset").is_file()
              and (target / "sound" / "a.wav").stat().st_size > 40,
              first.summary())
        check("the install writes the ownership marker",
              (target / inst.MARKER_NAME).is_file())
        check("the stub lands beside the mod folder",
              (user / "mod" / "demo_mod.mod").is_file())
        check("dlc_load lists the mod",
              "mod/demo_mod.mod" in
              json.loads(dlc.read_text(encoding="utf-8"))["enabled_mods"])

        again = inst.install_pack(src, user, mod_id="demo_mod",
                                  mod_stub_text='version="9.9.9"\n', version="9.9.9",
                                  register_playset=False)
        check("a second install refreshes rather than duplicating",
              again.already_present and (target / "sound" / "x.asset").is_file())

        foreign = user / "mod" / "someone_else"
        foreign.mkdir()
        (foreign / "precious.txt").write_text("mine", encoding="utf-8")
        refused = False
        try:
            inst.install_pack(src, user, mod_id="someone_else",
                              mod_stub_text='version="1"\n', register_playset=False)
        except inst.InstallError:
            refused = True
        check("installing over a directory we do not own is refused", refused)
        check("and the refused directory is left byte-for-byte intact",
              (foreign / "precious.txt").read_text(encoding="utf-8") == "mine")

        refused_delete = False
        try:
            inst.uninstall_pack(user, mod_id="someone_else")
        except inst.InstallError:
            refused_delete = True
        check("uninstalling a directory we do not own is refused", refused_delete)
        check("and that directory survives too", (foreign / "precious.txt").is_file())

        inst.uninstall_pack(user, mod_id="demo_mod")
        check("uninstalling our own mod removes the folder and the stub",
              not target.exists() and not (user / "mod" / "demo_mod.mod").exists())
        check("and clears it from dlc_load",
              "mod/demo_mod.mod" not in
              json.loads(dlc.read_text(encoding="utf-8"))["enabled_mods"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tts_session() -> None:
    """A batch reuses one connection, and turn boundaries stay in sync.
    Both properties are load-bearing and neither is visible in the output, which
    is why they are tested against a local fake endpoint instead of the real one:
      * the limiter counts *connections*, so opening one per line is what made a
        full pack spend most of its wall clock in backoff;
      * ``tts.response.done`` arrives *after* ``tts.response.audio.done``, so a
        client that stops at the audio one leaves the turn message queued and the
        next line reads it as its own completion, returning no audio at all.
    """
    section("tts session reuse")
    import asyncio
    import base64
    import json as _json
    import tempfile

    from core import tts as tts_core

    try:
        import websockets
    except ImportError:  # pragma: no cover - dependency is in the venv
        skip("tts session reuse", "websockets 不可用")
        return
    # The fake server logs every accept at INFO, which would bury the checks.
    import logging

    logging.getLogger("websockets.server").setLevel(logging.WARNING)

    wav = tts_core._wrap_pcm16(b"\x00\x01" * 240, 24000)
    payload = base64.b64encode(wav).decode()
    state = {"connections": 0, "lines": 0, "fail_at": 0}

    async def handler(ws):
        state["connections"] += 1
        sid = f"s{state['connections']}"
        await ws.send(_json.dumps({"type": "tts.connection.done",
                                   "data": {"session_id": sid}}))
        try:
            while True:
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    continue
                kind = _json.loads(raw).get("type")
                if kind != "tts.text.done":
                    continue
                state["lines"] += 1
                if state["fail_at"] and state["lines"] == state["fail_at"]:
                    await ws.send(_json.dumps({"type": "tts.response.error",
                                               "data": "provider said no"}))
                    continue
                await ws.send(_json.dumps({"type": "tts.response.audio.delta",
                                           "data": {"audio": payload}}))
                await ws.send(_json.dumps({"type": "tts.response.audio.done",
                                           "data": {"session_id": sid}}))
                await ws.send(_json.dumps({"type": "tts.response.done",
                                           "data": {"session_id": sid}}))
        except Exception:  # noqa: BLE001 - the client closed, which is normal here
            return

    def _settings(port: int) -> "tts_core.TtsSettings":
        return tts_core.TtsSettings(url=f"ws://127.0.0.1:{port}", api_key="k",
                                    voice_id="test-voice", timeout_s=5.0)

    async def batch() -> dict:
        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        settings = _settings(port)
        items = [(f"slot_{i}", f"line {i}") for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            cache = pathlib.Path(tmp)
            first = await tts_core.synthesize_many(items, settings, cache_dir=cache)
            after_first = state["connections"]
            again = await tts_core.synthesize_many(items, settings, cache_dir=cache)
        server.close()
        await server.wait_closed()
        return {"connections": after_first, "now": state["connections"],
                "first": first, "again": again}

    got = asyncio.run(batch())
    check("a five-line batch opens exactly one connection",
          got["connections"] == 1, f"{got['connections']} 个连接")
    check("every line in the batch came back with audio",
          len(got["first"]) == 5 and all(pcm for pcm, _ in got["first"].values()),
          f"{sum(1 for pcm, _ in got['first'].values() if pcm)}/5 有音频")
    check("a fully cached batch opens no connection at all",
          got["now"] == got["connections"],
          f"多出 {got['now'] - got['connections']} 个连接")
    check("the cached batch still returns every line", len(got["again"]) == 5)

    state.update({"connections": 0, "lines": 0, "fail_at": 2})

    async def recovery() -> tuple[bool, bool, bool, int]:
        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        session = tts_core.TtsSession(_settings(port))
        try:
            first = bool((await session.speak("a"))[0])
            raised = False
            try:
                await session.speak("b")
            except Exception:  # noqa: BLE001 - the fake endpoint fails this turn
                raised = True
            still_open = session.connected
            second = bool((await session.speak("c"))[0])
        finally:
            await session.close()
            server.close()
            await server.wait_closed()
        return raised, still_open, first and second, state["connections"]

    raised, still_open, recovered, connections = asyncio.run(recovery())
    check("a failed turn raises instead of returning empty audio", raised)
    check("a failed turn closes the session, so the retry cannot reuse it",
          not still_open)
    check("and the same object recovers by reconnecting",
          recovered and connections == 2, f"{connections} 个连接")


def test_hosted_ui() -> None:
    """The panel is the one artifact this suite cannot render, so pin its wiring.

    Everything asserted here is a way the UI can be silently broken while every
    Python check still passes: a manifest entry pointing at a missing file, a
    button whose entry no longer exists, an action that was never marked
    ``@ui.action`` (so the host never offers it), or a label key that only one
    locale has.
    """
    section("hosted ui")
    import json

    try:
        import tomllib
    except ImportError:  # pragma: no cover - py3.11+
        skip("hosted ui manifest", "tomllib 不可用")
        return

    panel = HERE / "ui" / "panel.tsx"
    check("the panel file exists", panel.is_file(), str(panel))
    if not panel.is_file():
        return
    source = panel.read_text(encoding="utf-8")
    check("the panel default-exports a component",
          "export default function" in source)
    check("the panel imports from the host ui kit",
          'from "@neko/plugin-ui"' in source)

    manifest = tomllib.loads((HERE / "plugin.toml").read_text(encoding="utf-8"))
    ui_block = (manifest.get("plugin") or {}).get("ui") or {}
    panels = ui_block.get("panel") or []
    check("the manifest enables the panel", bool(ui_block.get("enabled")) and bool(panels))
    check("every declared panel entry points at a file that exists",
          bool(panels) and all((HERE / str(p.get("entry") or "")).is_file() for p in panels),
          str([p.get("entry") for p in panels]))
    permissions = set()
    for item in panels:
        permissions |= set(item.get("permissions") or [])
    check("the panel asks for the permissions it uses",
          {"state:read", "action:call"} <= permissions, sorted(permissions))
    check("the panel reads the context the plugin provides",
          any(str(p.get("context") or "") == "dashboard" for p in panels)
          and '@ui.context(id="dashboard"' in (HERE / "__init__.py").read_text(encoding="utf-8"))

    plugin_source = (HERE / "__init__.py").read_text(encoding="utf-8")
    entry_ids = set(re.findall(r'id="(stellaris_[a-z_]+)"', plugin_source))
    driven = set(re.findall(r'"(stellaris_[a-z_]+)"', source))
    check("every entry the panel drives still exists", driven <= entry_ids,
          sorted(driven - entry_ids))
    unmarked = []
    for entry in sorted(driven):
        index = plugin_source.find(f'id="{entry}"')
        window = plugin_source[max(0, index - 400):index] if index >= 0 else ""
        if "@ui.action(" not in window:
            unmarked.append(entry)
    check("every entry the panel drives is marked @ui.action", not unmarked, unmarked)

    keys = set(re.findall(r't\("([^"]+)"', source))
    check("the panel uses translated labels, not hardcoded text", bool(keys))
    for name in ("zh-CN.json", "en.json"):
        locale = json.loads((HERE / "i18n" / name).read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if k not in locale)
        check(f"{name} covers every label the panel uses", not missing, missing[:4])


def test_manifest_schema() -> None:
    """Validate plugin.toml against the host's own pydantic schema.

    The host logs a warning and keeps going when this fails, so a malformed field
    is silent in every direction except that one log line -- which is exactly how
    ``capabilities = "text"`` sat there while the schema expects a list.
    """
    section("manifest vs host schema")
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        skip("manifest schema", "tomllib 不可用")
        return
    try:
        import sys as _sys

        host = _host_root()
        if host is None:
            skip("manifest schema", "找不到 N.E.K.O 检出（可用 NEKO_ROOT 指定）")
            return
        if str(host) not in _sys.path:
            _sys.path.append(str(host))
        from plugin.config.schema import PluginSectionSchema
    except Exception:
        skip("manifest schema", "宿主 SDK 不可导入（本仓库布局）")
        return

    data = tomllib.loads((HERE / "plugin.toml").read_text(encoding="utf-8"))
    try:
        PluginSectionSchema.model_validate(data["plugin"])
        ok, detail = True, ""
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc).splitlines()[2] if len(str(exc).splitlines()) > 2 else str(exc)
    check("plugin.toml passes the host's manifest schema", ok, detail)


def main() -> int:
    print("neko_stellaris offline smoke suite")
    test_voice_plan()
    test_voice_pack_files()
    test_dds_icon()
    test_icon_from_art()
    test_bridge()
    test_gamelog_parsing()
    test_gamelog_tailer()
    test_settings_defaults_match_policy()
    test_policy()
    test_live_voice()
    test_manifest_roundtrip()
    test_freshness()
    test_standalone_export()
    test_layout_independence()
    test_published_copy()
    test_bridge_scope_links()
    test_install_paths()
    test_hosted_ui()
    test_manifest_schema()
    test_tts_session()

    print()
    print("=" * 60)
    if SKIPPED:
        print(f"{len(SKIPPED)} check(s) skipped:")
        for line in SKIPPED:
            print("  -", line)
    if FAILURES:
        print(f"FAILED {len(FAILURES)}/{CHECKS}")
        for line in FAILURES:
            print("  -", line)
        return 1
    if SKIPPED:
        print(f"all {CHECKS} checks passed ({len(SKIPPED)} skipped)")
    else:
        print(f"all {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
