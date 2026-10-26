# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Export a plugin-free, standalone advisor voice pack.

## What "standalone" means here

The audio was never dependent on N.E.K.O: a Stellaris mod that replaces the
advisor's sound group works on its own, offline, forever. What the combined
build carries on top of that is *plugin* machinery, and none of it belongs in a
pack someone might install by hand, hand to a friend, or put on the Workshop:

* ``events/`` and ``common/on_actions/`` -- the event bridge. It exists to feed
  the plugin's live commentary; with no plugin running it appends 47 hidden
  events to every on_action and writes lines nothing reads.
* ``pack.json`` -- the manifest that maps vanilla soundeffect names to slots, for
  runtime rewriting. Pure plugin bookkeeping.
* ``.neko_stellaris_pack.json`` -- the ownership marker the installer uses to
  decide whether it may replace a directory.
* ``README.md`` -- written for someone debugging the integration, not for someone
  who just wants the voice.

What is left is exactly what a voice pack is: a soundgroup, its wavs, a voice
type, an icon and two localisation files.

## Why it is an export rather than a second build

Pruning a finished combined build keeps one implementation of the audio. A
separate "voice-only" builder would be a second place for the asset, the
localisation or the icon to be assembled differently, and those differences
would only ever surface after a game launch.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import voicepack

logger = logging.getLogger("neko_stellaris.standalone")

# Plugin-side artifacts, i.e. everything that only means something with the
# plugin loaded. Kept as one list so "what makes a pack non-standalone" has a
# single answer.
PLUGIN_ARTIFACTS: tuple[str, ...] = (
    voicepack.MANIFEST_NAME,
    voicepack.SLOT_STATE_NAME,
    ".neko_stellaris_pack.json",
    ".slot_backup",
)
BRIDGE_DIRS: tuple[str, ...] = ("events", "common")


@dataclass(frozen=True)
class StandaloneSpec:
    """Identity of the distributable pack.

    ``mod_id`` doubles as the folder name and the ``.mod`` stub name, so it is
    the one value a publisher might want to change without touching anything
    else.
    """

    mod_id: str = voicepack.DEFAULT_MOD_ID
    mod_version: str = "1.0.1"
    supported_version: str = "4.5.*"
    name_zh: str = voicepack.MOD_NAME_ZH
    name_en: str = voicepack.MOD_NAME_EN
    # The in-game advisor-voice option, which is not the product name.
    voice_display_zh: str = voicepack.VOICE_DISPLAY_ZH
    voice_display_en: str = voicepack.VOICE_DISPLAY_EN
    tags: tuple[str, ...] = ("Sound",)

    def voice_spec(self) -> voicepack.PackSpec:
        return voicepack.PackSpec(
            mod_id=self.mod_id,
            name_zh=self.name_zh,
            name_en=self.name_en,
            voice_display_zh=self.voice_display_zh,
            voice_display_en=self.voice_display_en,
            mod_version=self.mod_version,
            supported_version=self.supported_version,
            tags=self.tags,
        )


def render_descriptor(spec: StandaloneSpec, *, has_thumbnail: bool | None = None) -> str:
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


def render_mod_stub(spec: StandaloneSpec, *, has_thumbnail: bool | None = None) -> str:
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


def render_readme(spec: StandaloneSpec, plan: voicepack.PackPlan,
                  clip_count: int, *, game_version: str = "") -> str:
    """README written for whoever receives the pack, not for its author."""
    return f"""# {spec.name_zh}

面向《群星》的独立顾问语音包。**不需要任何外部程序**，装上去就能用，离线可用。

适配版本：`{spec.supported_version}`{'（音频与声音清单提取自 ' + game_version + '）' if game_version else ''}

## 它做了什么

把原版顾问的全部 **{plan.slot_count} 个声音名**换成中文录音，共 **{clip_count} 条**。
不修改任何游戏机制、不新增事件、不写日志——只替换音频。

原版默认顾问组自己不带任何覆盖，所以这些声音原本全部落到英文录音上。要换就得
**全部覆盖**，只换一部分会导致游戏在一条句子里混着播中英文（`advisor_clicking_respons_01`
这一个音效单独就有 4 个成员音）。

## 怎么装

1. 把整个 `{spec.mod_id}` 文件夹放进
   `文档\\Paradox Interactive\\Stellaris\\mod\\`
2. 把 `{spec.mod_id}.mod` 放进同一个 `mod\\` 目录（它里面的 `path=` 指向上面那个文件夹）
3. 打开 Paradox 启动器，在 mod 列表里勾选「{spec.name_zh}」，然后启动游戏
4. 进游戏后到 `设置 → 声音 → 顾问语音`，选「{spec.name_zh}」

> 启动器只负责列出 mod，**勾选这一步必须手动做**——它不会把新装的 mod 自动加进播放集。
> 如果勾选后进游戏还是没声音，检查启动器里这个 mod 是不是启用状态。

## 内容

| 组 | 声音数 | 每组变体 |
|---|---|---|
| 顾问通知 | {plan.coverage.get('notification', 0) // max(1, _takes(plan, 'notification'))} | {_takes(plan, 'notification')} |
| 教程提示 | {plan.coverage.get('tutorial', 0) // max(1, _takes(plan, 'tutorial'))} | {_takes(plan, 'tutorial')} |
| 试听自我介绍 | 3 | 1 |
| 点击应答 | 16 | 1 |
| 通用短语 | 5 | 1 |

同一条变体不会重复出现，游戏在事件发生时随机抽一条。

音频格式：PCM 16 位 / 单声道 / 44100 Hz——和原版顾问录音完全一致。

## 卸载

在启动器里取消勾选，然后删掉 `mod\\{spec.mod_id}\\` 和 `mod\\{spec.mod_id}.mod`。

## 关于

语音由 [N.E.K.O](https://github.com/Project-N-E-K-O) 的 TTS 合成，角色是 YUI。
本包是纯游戏数据，不包含也不依赖该程序的任何代码。
"""


def _takes(plan: voicepack.PackPlan, group: str) -> int:
    """How many takes a group actually got, for the coverage table."""
    counts = [len(slot.lines) for slot in plan.clips if slot.group == group]
    return max(counts) if counts else 1


@dataclass
class StandaloneResult:
    output_dir: Path
    mod_stub_path: Path
    zip_path: Path | None
    files: int
    bytes_total: int
    removed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        size = f"{self.bytes_total / 1048576:.1f} MiB"
        archive = f"，压缩包 {self.zip_path.name}" if self.zip_path else ""
        return (f"{self.files} 个文件 / {size} → {self.output_dir}{archive}"
                f"（移除了 {len(self.removed)} 项插件内容）")


def export_standalone(
    source_dir: str | Path,
    out_dir: str | Path,
    spec: StandaloneSpec,
    *,
    plan: voicepack.PackPlan,
    clip_count: int,
    dataset: dict[str, Any] | None = None,
    keep_bridge: bool = False,
    make_zip: bool = True,
) -> StandaloneResult:
    """Copy a built combined tree, drop the plugin machinery, and re-brand it."""
    src, out = Path(source_dir), Path(out_dir)
    if not (src / "sound").is_dir():
        raise FileNotFoundError(f"源目录不是语音包：{src}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    skip = set(PLUGIN_ARTIFACTS) | {"README.md"}
    if not keep_bridge:
        skip |= set(BRIDGE_DIRS)

    removed: list[str] = []
    files = 0
    total = 0
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        first = rel.parts[0]
        if first in skip or rel.as_posix() in skip:
            if first not in removed:
                removed.append(first)
            continue
        if item.is_dir():
            continue
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        files += 1
        total += target.stat().st_size

    has_cover = (out / voicepack.THUMBNAIL_NAME).is_file()
    (out / "descriptor.mod").write_text(
        render_descriptor(spec, has_thumbnail=has_cover), encoding="utf-8", newline="\n")
    (out / "README.md").write_text(
        render_readme(spec, plan, clip_count,
                      game_version=str((dataset or {}).get("game_version") or "")),
        encoding="utf-8", newline="\n")
    files += 2

    # The stub lives beside the mod folder, not inside it, so it is written to
    # the export root rather than nested one level down.
    stub = out.parent / f"{spec.mod_id}.mod"
    stub.write_text(render_mod_stub(spec, has_thumbnail=has_cover),
                    encoding="utf-8", newline="\n")

    zip_path = None
    if make_zip:
        zip_path = out.parent / f"{spec.mod_id}-{spec.mod_version}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(stub, stub.name)
            for item in sorted(out.rglob("*")):
                if item.is_file():
                    archive.write(item, Path(spec.mod_id) / item.relative_to(out))

    return StandaloneResult(out, stub, zip_path, files, total, removed)


def verify_standalone(mod_dir: str | Path) -> list[str]:
    """Return the reasons a directory is *not* a clean standalone pack.

    Checked rather than assumed, because the whole point is that nothing here
    depends on the plugin: a leftover manifest or bridge directory would not
    break anything visibly, it would just quietly make the artifact dishonest.
    """
    mod = Path(mod_dir)
    problems: list[str] = []
    if not (mod / "descriptor.mod").is_file():
        problems.append("缺少 descriptor.mod")
    if not (mod / "sound").is_dir():
        problems.append("缺少 sound/")
    for name in PLUGIN_ARTIFACTS:
        if (mod / name).exists():
            problems.append(f"仍带着插件内容：{name}")
    for name in BRIDGE_DIRS:
        if (mod / name).exists():
            problems.append(f"仍带着事件桥目录：{name}")
    wavs = list((mod / "sound" / voicepack.VO_SUBDIR).glob("*.wav")) \
        if (mod / "sound" / voicepack.VO_SUBDIR).is_dir() else []
    if not wavs:
        problems.append("sound/vo 下没有音频")
    return problems
