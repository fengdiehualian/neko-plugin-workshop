# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Build the voice pack and the event bridge as a single Stellaris mod.

## Why one mod instead of two

A Stellaris mod is just a folder, so ``sound/`` and ``events/`` and
``common/on_actions/`` coexist happily. Keeping them apart bought nothing and
cost two real things:

* **A silent partial install.** Enable the voice pack, forget the bridge, and
  everything still "works" -- the game plays YUI's lines, the plugin still finds
  events -- but the commentary loses every detail the bridge supplies. The only
  symptom is that the character is vaguer than she should be, which is not a
  symptom anyone would report.
* **A double-append hazard.** Both halves can register the same on_action. With
  two mods enabled the engine appends ``events = { neko_bridge.N }`` twice, so
  every hooked event fires twice and every line is written twice. Merging
  removes the possibility; the installer additionally retires the old standalone
  bridge so an upgrade cannot leave both behind.

The two halves stay separate *builders* -- this module only composes their
output into one directory, so there is exactly one implementation of each.

Their file layouts are disjoint (``sound/``, ``localisation/``, ``gfx/`` versus
``events/``, ``common/on_actions/``), which is what makes composition safe
rather than a merge of conflicting trees. The three files they would both write
-- ``descriptor.mod``, the ``.mod`` stub and ``README.md`` -- are owned here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import bridge as bridge_core
from . import voicepack

logger = logging.getLogger("neko_stellaris.combined")

# Re-exported rather than restated: one definition, three consumers.
DEFAULT_NAME_ZH = voicepack.DEFAULT_NAME_ZH
DEFAULT_NAME_EN = voicepack.DEFAULT_NAME_EN

# Both halves, honestly. The launcher shows these, and a mod that replaces audio
# *and* appends script hooks is both.
COMBINED_TAGS: tuple[str, ...] = ("Sound", "Gameplay", "Utilities")


@dataclass(frozen=True)
class CombinedSpec:
    """Identity of the single mod that carries both halves."""

    mod_id: str = voicepack.DEFAULT_MOD_ID
    mod_version: str = "1.0.1"
    supported_version: str = "4.5.*"
    name_zh: str = DEFAULT_NAME_ZH
    name_en: str = DEFAULT_NAME_EN
    # The product name and the in-game option name are different strings; see
    # voicepack.MOD_NAME_ZH for why.
    voice_display_zh: str = voicepack.VOICE_DISPLAY_ZH
    voice_display_en: str = voicepack.VOICE_DISPLAY_EN
    tags: tuple[str, ...] = COMBINED_TAGS

    def voice_spec(self) -> voicepack.PackSpec:
        return voicepack.PackSpec(
            mod_id=self.mod_id,
            name_zh=self.name_zh,
            name_en=self.name_en,
            voice_display_zh=self.voice_display_zh,
            voice_display_en=self.voice_display_en,
            mod_version=self.mod_version,
            supported_version=self.supported_version,
        )


def render_descriptor(spec: CombinedSpec, *, has_thumbnail: bool | None = None) -> str:
    if has_thumbnail is None:
        has_thumbnail = spec.voice_spec().resolved_icon_source() is not None
    tags = "\n".join(f'\t"{tag}"' for tag in spec.tags)
    return "\n".join([
        f'version="{spec.mod_version}"',
        "tags={",
        tags,
        "}",
        f'name="{spec.name_zh}"',
        *voicepack.picture_line(has_thumbnail),
        f'supported_version="{spec.supported_version}"',
        "",
    ])


def render_mod_stub(spec: CombinedSpec, *, has_thumbnail: bool | None = None) -> str:
    if has_thumbnail is None:
        has_thumbnail = spec.voice_spec().resolved_icon_source() is not None
    tags = "\n".join(f'\t"{tag}"' for tag in spec.tags)
    return "\n".join([
        f'version="{spec.mod_version}"',
        "tags={",
        tags,
        "}",
        f'name="{spec.name_zh}"',
        *voicepack.picture_line(has_thumbnail),
        f'supported_version="{spec.supported_version}"',
        f'path="mod/{spec.mod_id}"',
        "",
    ])


def render_readme(spec: CombinedSpec, voice_result: voicepack.BuildResult,
                  bridge_plan: bridge_core.BridgePlan) -> str:
    """One README covering both halves, since a user now has one thing to read."""
    voice_plan = voice_result.plan
    excluded = "\n".join(f"- `{goal}`: {why}" for goal, why in bridge_plan.skipped[:12])
    return f"""# {spec.name_zh}

由 N.E.K.O 的 `neko_stellaris` 插件生成。**一个 mod，两件事。**

## 一、游戏内顾问语音

把原版顾问的全部 {voice_plan.slot_count} 个声音名换成 YUI 的中文录音，共
{voice_plan.clip_count} 条。在 `设置 → 声音 → 顾问语音` 里选「{spec.voice_display_zh}」。

* 语音组 `{voice_plan.spec.voice_group}`，全部音频是 PCM16 / 单声道 / 44100 Hz
* 每个事件多条变体，游戏随机抽一条
* 引擎在**播放时**从磁盘读这些 wav，所以 N.E.K.O 可以在游戏运行中改写台词内容
  （声音名不变、文件内容可变）

## 二、事件桥

给 {len(bridge_plan.hooks)} 个 on_action 各追加一个**隐藏事件**，只做一件事：
调用脚本效果 `log = "NEKO|v1|<事件名>|键=值|"` 把游戏里发生的事写进 `logs/game.log`，
供外部程序读取。**不改变任何游戏内容。**

原版日志本来就记录每条事件选择，但「你被宣战」和「邻居勘测了行星」在里面长得一模一样。
这个桥补的就是「谁对谁做了什么」。

**心跳**：`{bridge_core.HEARTBEAT_ON_ACTION}` 上挂了 `{bridge_core.HEARTBEAT_ID}`，
每两年写一行 `NEKO|v1|heartbeat|`。如果哪天 Stellaris 改了 on_action 的合并语义，
症状是心跳消失（而不是悄无声息地什么都不发生）。

## 故意排除的钩子

{excluded}

## 怎么验证它在工作

1. 进游戏玩一会儿，存档，退出。
2. 打开 `文档\\Paradox Interactive\\Stellaris\\logs\\game.log`，搜 `NEKO|`。
3. 应该能看到 `NEKO|v1|...` 的行；搜不到就先找心跳行在不在。
4. `logs/error.log` 里搜 `nekko_stellaris`，正常情况下应该是零行。
"""


@dataclass
class CombinedBuildResult:
    output_dir: Path
    voice: voicepack.BuildResult
    bridge: bridge_core.BridgeBuildResult
    files: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.voice.clips_written} 条录音 / "
                f"{self.voice.bytes_written / 1048576:.1f} MiB + "
                f"{self.bridge.hook_count} 个事件钩子 → {self.output_dir}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "output": str(self.output_dir),
            "clips": self.voice.clips_written,
            "hooks": self.bridge.hook_count,
            "bytes": self.voice.bytes_written,
            "summary": self.summary(),
        }


async def build_combined(
    voice_plan: voicepack.PackPlan,
    bridge_plan: bridge_core.BridgePlan,
    out_dir: str | Path,
    tts: Any,
    *,
    spec: CombinedSpec | None = None,
    cache_dir: str | Path | None = None,
    dataset: dict[str, Any] | None = None,
    lines: dict[str, list[str]] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> CombinedBuildResult:
    """Materialise the combined mod. Idempotent; the directory is regenerated."""
    spec = spec or CombinedSpec()
    out = Path(out_dir)
    phase = on_phase or (lambda _m: None)

    phase("生成顾问语音")
    voice_result = await voicepack.build_pack(
        voice_plan, out, tts, cache_dir=cache_dir,
        dataset=dataset, lines=lines,
        on_progress=on_progress, on_phase=on_phase)

    # The bridge's builder re-reads nothing from disk and writes only under
    # events/ and common/, so running it second cannot disturb the audio.
    phase("生成事件桥")
    bridge_result = bridge_core.build_bridge(bridge_plan, out)

    # Reclaim the files both halves would have written.
    phase("写入合并清单")
    (out / "descriptor.mod").write_text(
        render_descriptor(spec, has_thumbnail=(out / voicepack.THUMBNAIL_NAME).is_file()),
        encoding="utf-8", newline="\n")
    (out / "README.md").write_text(
        render_readme(spec, voice_result, bridge_plan), encoding="utf-8", newline="\n")

    # Record the composition so a later check can tell what this directory is.
    for manifest in (out / voicepack.MANIFEST_NAME,):
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["includes_bridge"] = True
                data["bridge_namespace"] = bridge_plan.spec.namespace
                data["bridge_hooks"] = len(bridge_plan.hooks)
                manifest.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                logger.warning("无法把桥接信息写进 %s: %s", manifest.name, exc)

    return CombinedBuildResult(out, voice_result, bridge_result)


def combined_stamp_is_stale(mod_dir: str | Path) -> str:
    """Empty when the directory really is a combined build, else the reason.

    Guards the upgrade path: a directory that is *only* the voice pack must be
    rebuilt before it can be trusted to carry the bridge, otherwise enabling it
    leaves live commentary quietly deprived of every event detail.
    """
    path = Path(mod_dir) / voicepack.MANIFEST_NAME
    if not path.is_file():
        return "没有 pack.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "pack.json 无法解析"
    if not data.get("includes_bridge"):
        return "这个目录里还没有事件桥"
    if not (Path(mod_dir) / "events" / "neko_bridge_events.txt").is_file():
        return "缺少桥接事件文件"
    if not (Path(mod_dir) / "common" / "on_actions").is_dir():
        return "缺少桥接 on_actions"
    return ""
