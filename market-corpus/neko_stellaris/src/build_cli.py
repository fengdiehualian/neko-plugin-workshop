# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Standalone CLI: build and install the Stellaris mods without N.E.K.O running.

The plugin entries are the normal way to do this, but they require the host to
be up. This exists for the cases where it is not: scripting a rebuild after a
Stellaris patch, generating a pack on a machine where the desktop app is not
installed, or watching the TTS batch in a terminal instead of a progress bar.

    python plugin/plugins/neko_stellaris/build_cli.py plan
    python plugin/plugins/neko_stellaris/build_cli.py voice --install
    python plugin/plugins/neko_stellaris/build_cli.py bridge --install
    python plugin/plugins/neko_stellaris/build_cli.py status
    python plugin/plugins/neko_stellaris/build_cli.py uninstall
    python build_cli.py uninstall                           # this repository (plugin root)

Nothing here re-implements the pipeline; it only wires arguments to the same
``core`` modules the entries call, so the two paths cannot drift.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import pathlib
import sys
import time

# ``core/`` sits beside this file in both of the shapes these sources travel in:
#
#   <host>/plugin/plugins/neko_stellaris/   installed inside N.E.K.O
#   <repo>/                                 this repository (repo root = plugin root)
#
# Importing it *through* the plugin package would execute the entry point first,
# which imports the host SDK -- so this CLI would only ever run inside a host
# checkout, which defeats the point of having it. Importing ``core`` from this
# file's own directory works in both layouts and needs no host at all.
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# These are ``import_module`` calls rather than ``import`` statements on purpose:
# ``core`` only becomes importable after the ``sys.path`` edit above, so real
# import statements would sit below executable code (E402) and need a suppression.
# N.E.K.O's own CI lints with ``ruff --ignore-noqa``, where a suppression does not
# exist, so the imports are spelled as calls and there is nothing to suppress.
bridge_core = importlib.import_module("core.bridge")
combined_core = importlib.import_module("core.combined")
installer = importlib.import_module("core.installer")
nekoconfig = importlib.import_module("core.nekoconfig")
standalone_core = importlib.import_module("core.standalone")
voicepack = importlib.import_module("core.voicepack")
TtsSettings = importlib.import_module("core.tts").TtsSettings

DATA = HERE / "data"


def _log_setup(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_plan(variants: int, name: str = ""):
    dataset = voicepack.load_dataset(DATA / "advisor_sounds.json")
    lines = voicepack.load_lines(DATA / "lines.zh-CN.json")
    spec = voicepack.PackSpec(max_variants=variants or voicepack.DEFAULT_VARIANTS)
    if name:
        # Both locales carry the same string. The Chinese one is what shows in
        # game, and a translated second name would be another thing to keep in
        # sync for no benefit.
        spec = voicepack.PackSpec(max_variants=spec.max_variants,
                                  name_zh=name, name_en=name)
    return voicepack.plan_pack(dataset, lines, spec), spec, dataset, lines


def cmd_check(args: argparse.Namespace) -> int:
    """Compare the installed pack against what the current settings would build."""
    plan, spec, dataset, lines = _load_plan(args.variants, getattr(args, "name", ""))
    user = nekoconfig.find_stellaris_user_dir()
    if not user:
        print("未找到 Stellaris 用户目录", file=sys.stderr)
        return 3
    resolved = nekoconfig.load_settings(voice_override=args.voice or "")
    from core.tts import TtsSettings
    tts = TtsSettings(url=resolved.url, api_key=resolved.api_key, voice_id=resolved.voice_id)

    active = installer.find_active_install(user, mod_id=spec.mod_id)
    mod_dir = (active.path if active is not None
               else pathlib.Path(user) / "mod" / spec.mod_id)
    if active is not None and active.is_published:
        print("已装位置  :", active.summary())
    fresh = voicepack.check_freshness(
        mod_dir, spec, dataset, lines, tts,
        cache_dir=nekoconfig.tts_cache_dir())
    print("状态      :", fresh.state)
    print("结论      :", fresh.summary())
    if fresh.installed:
        print("已安装音色:", fresh.installed.voice_id or "（未记录）",
              "| 构建于", time.strftime("%Y-%m-%d %H:%M",
                                        time.localtime(fresh.installed.built_at or 0)))
    if fresh.current:
        print("当前音色  :", fresh.current.voice_id or "（无）")
    print("需合成    :", fresh.clips_to_generate, "/", fresh.current.clip_count if fresh.current else 0)
    return 0 if fresh.state == "fresh" else 1


def _workdir(args: argparse.Namespace) -> pathlib.Path:
    """Where build output goes. Caches live separately, in the shared TTS cache."""
    if args.workdir:
        return pathlib.Path(args.workdir)
    return nekoconfig.default_work_dir()


def cmd_manifest(args: argparse.Namespace) -> int:
    """(Re)write pack.json into an installed pack without touching TTS.

    The manifest is what lets live slot rewriting map a vanilla soundeffect name
    to the wavs behind it. It is pure metadata derived from the plan, so a pack
    built before the manifest existed can be brought up to date in under a
    second instead of re-running the whole synthesis to regenerate one file.
    """
    plan, _spec, _ds, _ln = _load_plan(args.variants, getattr(args, "name", ""))
    names = [j.sound for j in voicepack.clip_jobs(plan)]

    targets: list[pathlib.Path] = []
    if args.install:
        user = nekoconfig.find_stellaris_user_dir()
        if not user:
            print("未找到 Stellaris 用户目录", file=sys.stderr)
            return 3
        active = installer.find_active_install(user, mod_id=voicepack.DEFAULT_MOD_ID)
        targets.append(active.path if active is not None
                       else pathlib.Path(user) / "mod" / voicepack.DEFAULT_MOD_ID)
    targets.append(_workdir(args) / "voice")

    wrote = 0
    for target in targets:
        if not (target / "sound").is_dir():
            continue
        # Preserve any stamp already on disk. `manifest` exists to backfill the
        # pool map, and writing a null stamp would erase the record of which
        # voice built this pack -- turning a checkable pack into an unknown one.
        payload = voicepack.render_manifest(plan, names,
                                            stamp=voicepack.read_stamp(target))
        (target / voicepack.MANIFEST_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {target / voicepack.MANIFEST_NAME}")
        wrote += 1
    if not wrote:
        print("没有找到已生成的语音包目录", file=sys.stderr)
        return 4
    print(f"清单包含 {len(payload['pools'])} 个 soundeffect 池、{len(names)} 个槽位")
    if not payload.get("stamp"):
        print("提示：这个包没有构建记录（stamp），check 会报 no_stamp。"
              "用 `voice --install` 重建一次即可补上。")
    return 0


async def _build_combined(args: argparse.Namespace) -> int:
    """Build the single mod that carries both the voice pack and the event bridge."""
    plan, spec, dataset, lines = _load_plan(args.variants, getattr(args, "name", ""))
    hooks = bridge_core.load_hooks(DATA / "on_action_hooks.json")
    bridge_plan = bridge_core.plan_bridge(
        hooks, bridge_core.BridgeSpec(name_zh=combined_core.DEFAULT_NAME_ZH,
                                      name_en=combined_core.DEFAULT_NAME_EN))

    resolved = nekoconfig.load_settings(voice_override=args.voice or "")
    if not resolved.voice_id:
        print("没有可用音色：用 --voice 指定，或先让 N.E.K.O 配好音色", file=sys.stderr)
        return 2
    tts = TtsSettings(url=resolved.url, api_key=resolved.api_key,
                      voice_id=resolved.voice_id, concurrency=args.concurrency)

    out = _workdir(args) / "combined"
    started = time.monotonic()
    last = [0.0]

    def on_progress(done: int, total: int, label: str) -> None:
        now = time.monotonic()
        if done == total or now - last[0] >= args.progress_every:
            last[0] = now
            print(f"  [{done}/{total}] {label}  (+{now - started:.0f}s)", flush=True)

    cspec = combined_core.CombinedSpec()
    result = await combined_core.build_combined(
        plan, bridge_plan, out, tts, spec=cspec,
        cache_dir=nekoconfig.tts_cache_dir(), dataset=dataset, lines=lines,
        on_progress=on_progress, on_phase=lambda m: print(f"-- {m}", flush=True))

    print("combined:", result.summary())
    print(f"限流重试: {result.voice.tts_stats.rate_limit_rejections} 次")
    for warning in result.voice.plan.warnings:
        print("  warn:", warning)

    if args.install:
        user = nekoconfig.find_stellaris_user_dir()
        if not user:
            print("未找到 Stellaris 用户目录，跳过安装", file=sys.stderr)
            return 3
        installed = installer.install_pack(
            out, user, mod_id=voicepack.DEFAULT_MOD_ID,
            mod_stub_text=combined_core.render_mod_stub(cspec),
            version=cspec.mod_version)
        print("install:", installed.summary())
        for note in installed.notes:
            print("  note:", note)

        # Retire the standalone bridge. Leaving it enabled would append the same
        # on_actions a second time, so every hooked event would fire -- and be
        # logged -- twice.
        try:
            retired = installer.uninstall_pack(user, mod_id=bridge_core.DEFAULT_MOD_ID)
            print("已退掉旧的独立桥接 mod:", retired.summary())
        except installer.InstallError as exc:
            print(f"旧的独立桥接 mod 未清理（{exc}）——请在启动器里取消勾选它，"
                  f"否则事件会被记录两次", file=sys.stderr)
    return 1 if result.voice.tts_stats.failed else 0


def cmd_standalone(args: argparse.Namespace) -> int:
    """Export a plugin-free voice pack that works on its own.

    Deliberately does not install by default: the combined build is what serves
    the plugin's live commentary, and silently replacing it with a voice-only
    pack would strip the event bridge while looking like a harmless export.
    """
    plan, _spec, dataset, _lines = _load_plan(args.variants, getattr(args, "name", ""))
    names = [j.sound for j in voicepack.clip_jobs(plan)]

    source = pathlib.Path(args.source) if args.source else _workdir(args) / "combined"
    if not (source / "sound").is_dir():
        print(f"找不到已构建的整合版目录：{source}\n先跑一次 combined（不必 --install）",
              file=sys.stderr)
        return 2

    default = standalone_core.StandaloneSpec()
    ss = standalone_core.StandaloneSpec(
        mod_id=args.mod_id or default.mod_id,
        name_zh=args.name or default.name_zh,
        name_en=args.name or default.name_en,
    )
    out = pathlib.Path(args.out) if args.out else source.parent / f"{ss.mod_id}-standalone"
    result = standalone_core.export_standalone(
        source, out, ss, plan=plan, clip_count=len(names),
        dataset=dataset, keep_bridge=args.keep_bridge, make_zip=not args.no_zip)

    print("standalone:", result.summary())
    print(f"  已移除的插件内容: {', '.join(result.removed) or '（无）'}")
    print(f"  mod 文件夹: {result.output_dir}")
    print(f"  存根文件  : {result.mod_stub_path}")
    if result.zip_path:
        print(f"  压缩包    : {result.zip_path}")

    problems = standalone_core.verify_standalone(result.output_dir)
    if problems:
        print("!! 校验未通过：", file=sys.stderr)
        for problem in problems:
            print("   -", problem, file=sys.stderr)
        return 5
    print("校验通过：是纯游戏数据，不含任何插件依赖")

    if args.install:
        user = nekoconfig.find_stellaris_user_dir()
        if not user:
            print("未找到 Stellaris 用户目录", file=sys.stderr)
            return 3
        installed = installer.install_pack(
            result.output_dir, user, mod_id=ss.mod_id,
            mod_stub_text=standalone_core.render_mod_stub(ss),
            version=ss.mod_version)
        print("install:", installed.summary())
        for note in installed.notes:
            print("  note:", note)
        print("注意：这会把整合版覆盖成纯语音包，实时解说会失去事件桥的细节。")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    plan, _spec, _ds, _ln = _load_plan(args.variants, getattr(args, "name", ""))
    print("plan:", plan.summary())
    print("coverage:", plan.coverage)
    for warning in plan.warnings:
        print("  warn:", warning)
    print(f"soundeffect 覆盖: {len(plan.effects)}")
    print(f"将生成 {len(voicepack.clip_jobs(plan))} 条录音，输出到 {_workdir(args) / 'voice'}")
    return 0


async def _build_voice(args: argparse.Namespace) -> int:
    plan, spec, dataset, lines = _load_plan(args.variants, getattr(args, "name", ""))
    resolved = nekoconfig.load_settings(voice_override=args.voice or "")
    print("voice:", resolved.describe())
    if not resolved.voice_id:
        print("没有可用音色：用 --voice 指定，或先让 N.E.K.O 配好音色", file=sys.stderr)
        return 2

    tts = TtsSettings(url=resolved.url, api_key=resolved.api_key,
                      voice_id=resolved.voice_id, concurrency=args.concurrency)
    root = _workdir(args)
    out = root / "voice"
    started = time.monotonic()
    last = [0.0]

    def on_progress(done: int, total: int, label: str) -> None:
        now = time.monotonic()
        if done == total or now - last[0] >= args.progress_every:
            last[0] = now
            print(f"  [{done}/{total}] {label}  (+{now - started:.0f}s)", flush=True)

    result = await voicepack.build_pack(
        plan, out, tts, cache_dir=nekoconfig.tts_cache_dir(),
        dataset=dataset, lines=lines,
        on_progress=on_progress, on_phase=lambda m: print(f"-- {m}", flush=True))

    print("build:", result.summary())
    print(f"限流重试: {result.tts_stats.rate_limit_rejections} 次，"
          f"最终请求间隔 {result.tts_stats.final_gap_s:.2f}s")
    for warning in result.plan.warnings:
        print("  warn:", warning)
    if result.tts_stats.failed:
        print(f"!! {result.tts_stats.failed} 条失败：")
        for line in result.tts_stats.errors[:10]:
            print("   ", line)

    if args.install:
        user = nekoconfig.find_stellaris_user_dir()
        if not user:
            print("未找到 Stellaris 用户目录，跳过安装", file=sys.stderr)
            return 3
        installed = installer.install_pack(
            out, user, mod_id=spec.mod_id,
            mod_stub_text=voicepack.render_mod_stub(spec),
            version=spec.mod_version)
        print("install:", installed.summary())
        for note in installed.notes:
            print("  note:", note)
    return 1 if result.tts_stats.failed else 0


def cmd_bridge(args: argparse.Namespace) -> int:
    hooks = bridge_core.load_hooks(DATA / "on_action_hooks.json")
    default_spec = bridge_core.BridgeSpec()
    spec = (bridge_core.BridgeSpec(name_zh=args.name, name_en=args.name)
            if getattr(args, "name", "") else default_spec)
    plan = bridge_core.plan_bridge(hooks, spec)
    out = _workdir(args) / "bridge"
    result = bridge_core.build_bridge(plan, out)
    print("bridge:", result.summary())
    print("excluded goals:")
    for goal, why in result.skipped:
        print(f"  - {goal}: {why}")
    if args.install:
        user = nekoconfig.find_stellaris_user_dir()
        if not user:
            print("未找到 Stellaris 用户目录，跳过安装", file=sys.stderr)
            return 3
        installed = installer.install_pack(
            out, user, mod_id=plan.spec.mod_id,
            mod_stub_text=bridge_core.render_mod_stub(plan.spec),
            version=plan.spec.mod_version)
        print("install:", installed.summary())
        for note in installed.notes:
            print("  note:", note)
    return 0


def cmd_playset(args: argparse.Namespace) -> int:
    """Enable the installed mods inside the launcher's current playset."""
    user = nekoconfig.find_stellaris_user_dir()
    if not user:
        print("未找到 Stellaris 用户目录", file=sys.stderr)
        return 3
    if installer.launcher_is_running():
        print("Paradox 启动器正在运行——先完全关闭它（含托盘图标）再试。\n"
              "它退出时会用自己的内存状态重写数据库，现在写的会被丢掉。", file=sys.stderr)
        return 4
    failed = 0
    for mod_id in (voicepack.DEFAULT_MOD_ID, bridge_core.DEFAULT_MOD_ID):
        try:
            print(installer.register_in_launcher_playset(user, mod_id=mod_id).summary())
        except installer.InstallError as exc:
            failed += 1
            print(f"{mod_id}: 失败 — {exc}", file=sys.stderr)
    return 5 if failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    user = nekoconfig.find_stellaris_user_dir()
    print("Stellaris 用户目录 :", user or "未找到")
    print("Stellaris 安装目录 :", nekoconfig.find_stellaris_install_dir() or "未找到")
    resolved = nekoconfig.load_settings()
    print("音色               :", resolved.voice_id or "<无>", f"({resolved.voice_source})")
    print("TTS 端点           :", resolved.url, f"({resolved.url_source})")
    print("密钥               :", "已配置" if resolved.api_key else "未配置",
          f"({resolved.key_source})")
    if not user:
        return 1
    from core import gamelog
    print("日志               :", gamelog.describe_log(user))
    for mod_id in (voicepack.DEFAULT_MOD_ID, bridge_core.DEFAULT_MOD_ID):
        print("mod                :", installer.inspect_installation(user, mod_id=mod_id).summary())
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    user = nekoconfig.find_stellaris_user_dir()
    if not user:
        print("未找到 Stellaris 用户目录", file=sys.stderr)
        return 3
    for mod_id in (voicepack.DEFAULT_MOD_ID, bridge_core.DEFAULT_MOD_ID):
        try:
            print(installer.uninstall_pack(user, mod_id=mod_id).summary())
        except installer.InstallError as exc:
            print(f"{mod_id}: 拒绝卸载 — {exc}", file=sys.stderr)
    return 0


PROBE_TEXT = ("实时语音通道测试成功，听到这句话就说明游戏会按需读取音频文件，"
              "动态替换可行。")

# Slots to rewrite for the experiment. The three `advisor_sample_vo_*` entries are
# the ones a player can re-trigger on demand (picking the voice in the settings
# list plays a sample), so they carry the experiment; the click responses and one
# notification are there in case the sample turns out to be cached by the UI, so
# a conclusive result does not depend on a single trigger.
PROBE_DEFAULT_SOUNDS = (
    "advisor_sample_vo_01",
    "advisor_sample_vo_02",
    "advisor_sample_vo_03",
    "advisor_clicking_respons_01",
    "advisor_clicking_respons_02",
    "advisor_clicking_respons_03",
    "advisor_notification_situation_log_01",
)


def _installed_mod_dir() -> pathlib.Path:
    user = nekoconfig.find_stellaris_user_dir()
    if not user:
        raise SystemExit("未找到 Stellaris 用户目录")
    active = installer.find_active_install(user, mod_id=voicepack.DEFAULT_MOD_ID)
    if active is not None and (active.path / "sound").is_dir():
        return active.path
    mod_dir = pathlib.Path(user) / "mod" / voicepack.DEFAULT_MOD_ID
    if not (mod_dir / "sound").is_dir():
        raise SystemExit(f"语音包未安装：{mod_dir}")
    return mod_dir


async def _probe_install(args: argparse.Namespace) -> int:
    """Render one unmistakable line and write it over real advisor slots.

    This is the empirical test for the question the whole "runtime" design hangs
    on: does the engine read a wav from disk when it plays it, or does it cache
    the file at load? The asset mapping is deliberately left untouched, so a
    positive result means no reload, no console and no keyboard automation is
    needed to change what the advisor says.

    ``--all`` overwrites every slot, which removes the last source of ambiguity:
    the earlier default set required the tester to successfully trigger one of
    seven specific sounds, and "I could not make it fire" is indistinguishable
    from "it is cached". With every slot rewritten, any advisor line at all --
    a survey finishing, a click acknowledgement, the settings preview -- carries
    the answer.
    """
    mod_dir = _installed_mod_dir()
    vo_dir = voicepack.vo_dir(mod_dir)
    if args.all:
        sounds = sorted(p.stem for p in vo_dir.glob("*.wav"))
    elif args.sound:
        sounds = list(args.sound)
    else:
        sounds = list(PROBE_DEFAULT_SOUNDS)

    missing = [n for n in sounds if not (vo_dir / f"{n}.wav").is_file()]
    sounds = [n for n in sounds if n not in missing]
    for name in missing:
        print(f"跳过不存在的槽位: {name}", file=sys.stderr)
    if not sounds:
        print("没有可覆盖的槽位", file=sys.stderr)
        return 2

    resolved = nekoconfig.load_settings(voice_override=args.voice or "")
    if not resolved.voice_id:
        print("没有可用音色", file=sys.stderr)
        return 2
    from core.audiowav import prepare_for_game, wav_bytes_mono16
    from core.tts import TtsSettings, synthesize_with_retry

    tts = TtsSettings(url=resolved.url, api_key=resolved.api_key,
                      voice_id=resolved.voice_id)
    text = args.text or PROBE_TEXT
    print(f"合成探针台词（{resolved.voice_id}）：{text}")
    pcm, rate = await synthesize_with_retry(text, tts, label="probe")
    samples, out_rate = prepare_for_game(voicepack.wrap_raw(pcm, rate))
    payload = wav_bytes_mono16(samples, out_rate)
    print(f"探针音频 {len(payload)} 字节 / {len(samples) / out_rate:.1f}s")
    print(f"覆盖 {len(sounds)} 个槽位…")

    written = []
    for name in sounds:
        try:
            voicepack.rewrite_slot(mod_dir, sound_name=name, wav_payload=payload)
            written.append(name)
        except voicepack.VoicePackError as exc:
            print(f"  跳过 {name}: {exc}", file=sys.stderr)
    print(f"完成：{len(written)} 个槽位已覆盖探针句，原音频全部备份。")
    print()
    print("现在做这个实验（顺序很重要）：")
    print("  1. 【游戏必须已经在运行】——如果你现在才启动游戏，它是启动时读盘，")
    print("     结果永远是「听到旧音频」，实验作废。")
    print("  2. 回游戏，随便触发一个顾问语音。最容易的三条：")
    print("     a) 派科研船去勘测一个没勘测过的星系（勘测完成会播顾问）")
    print("     b) 设置 → 声音 → 顾问语音，重新点选「N.E.K.O 顾问语音（兰兰）」试听")
    print("     c) 控制台（~ 键）里试： audio.playeffect advisor_notification_research_complete")
    print("  3. 听：")
    print(f"     「{text[:16]}…」  → 引擎按需读盘，动态替换可行")
    print("     「喵，本喵是悠怡…」 之类的原台词 → 引擎启动时缓存，实时注入做不到")
    print()
    print("测完还原：  build_cli.py probe-restore")
    return 0


def cmd_probe_status(args: argparse.Namespace) -> int:
    mod_dir = _installed_mod_dir()
    state = voicepack.load_slot_state(mod_dir)
    slots = state.get("slots") or {}
    if not slots:
        print("没有正在生效的探针槽位。")
        return 0
    print(f"被覆盖的槽位（{len(slots)} 个）：")
    for name, entry in sorted(slots.items()):
        import time as _t
        age = _t.time() - float(entry.get("written_at") or 0)
        print(f"  {name:44s} {entry.get('size')} 字节，{age:.0f} 秒前写入")
    print(f"\n备份目录: {next(iter(slots.values())).get('backup', '')}")
    print("还原： build_cli.py probe-restore")
    return 0


def cmd_probe_restore(args: argparse.Namespace) -> int:
    mod_dir = _installed_mod_dir()
    restored = voicepack.restore_slots(mod_dir)
    if not restored:
        print("没有需要还原的槽位。")
    else:
        print(f"已还原 {len(restored)} 个槽位：")
        for name in restored:
            print("  ", name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_cli",
        description="构建/安装 N.E.K.O 的《群星》顾问语音包与事件桥接 mod。")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印调试日志")
    parser.add_argument("--workdir", default="",
                        help="工作目录（默认与插件共用同一个运行时数据根）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="只预览计划，不联网、不写盘")
    p_plan.add_argument("--variants", type=int, default=0)
    p_plan.set_defaults(func=cmd_plan)

    p_manifest = sub.add_parser(
        "manifest", help="把 pack.json 清单写进已生成的语音包（不联网、不重打 TTS）")
    p_manifest.add_argument("--variants", type=int, default=0)
    p_manifest.add_argument("--install", action="store_true",
                            help="同时写进游戏 mod 目录里那份")
    p_manifest.set_defaults(func=cmd_manifest)

    p_check = sub.add_parser(
        "check", help="对比已安装的语音包与当前设置：音色/台词/数据集是否已过期")
    p_check.add_argument("--variants", type=int, default=0)
    p_check.add_argument("--voice", default="")
    p_check.set_defaults(func=cmd_check)

    p_standalone = sub.add_parser(
        "standalone",
        help="导出一份独立可用的语音包：纯游戏数据，不依赖插件，另出压缩包")
    p_standalone.add_argument("--variants", type=int, default=0)
    p_standalone.add_argument("--name", default="", help="显示名（默认 YUI）")
    p_standalone.add_argument("--mod-id", default="", help="mod 文件夹/存根名")
    p_standalone.add_argument("--source", default="", help="从哪个整合版目录导出")
    p_standalone.add_argument("--out", default="", help="导出到哪里")
    p_standalone.add_argument("--keep-bridge", action="store_true",
                              help="保留事件桥（默认移除：没有插件时它只写没人读的日志）")
    p_standalone.add_argument("--no-zip", action="store_true")
    p_standalone.add_argument("--install", action="store_true",
                              help="同时装进游戏（会覆盖整合版，实时解说将失去事件桥细节）")
    p_standalone.set_defaults(func=cmd_standalone)

    p_combined = sub.add_parser(
        "combined", help="生成整合版：一个 mod 同时带语音包与事件桥（推荐）")
    p_combined.add_argument("--variants", type=int, default=0)
    p_combined.add_argument("--voice", default="")
    p_combined.add_argument("--concurrency", type=int, default=2)
    p_combined.add_argument("--progress-every", type=float, default=15.0)
    p_combined.add_argument("--install", action="store_true")
    p_combined.set_defaults(func=lambda a: asyncio.run(_build_combined(a)))

    p_voice = sub.add_parser("voice", help="只生成语音包（不带动画桥）")
    p_voice.add_argument("--variants", type=int, default=0)
    p_voice.add_argument("--voice", default="", help="覆盖音色 id")
    p_voice.add_argument("--concurrency", type=int, default=2,
                         help="并发合成数；免费端点限制很严，调高只会换来更多退避")
    p_voice.add_argument("--progress-every", type=float, default=15.0)
    p_voice.add_argument("--install", action="store_true", help="生成后安装到游戏")
    p_voice.add_argument("--name", default="",
                         help="顾问语音在游戏里显示的名字（默认 YUI）")
    p_voice.set_defaults(func=lambda a: asyncio.run(_build_voice(a)))

    p_bridge = sub.add_parser("bridge", help="生成事件桥接 mod")
    p_bridge.add_argument("--install", action="store_true")
    p_bridge.add_argument("--name", default="", help="启动器里显示的名字（默认 YUI 事件桥）")
    p_bridge.set_defaults(func=cmd_bridge)

    p_status = sub.add_parser("status", help="查看路径、音色与安装状态")
    p_status.set_defaults(func=cmd_status)

    p_playset = sub.add_parser(
        "playset", help="把已安装的 mod 启用进启动器播放集（需先关闭启动器）")
    p_playset.set_defaults(func=cmd_playset)

    p_probe = sub.add_parser(
        "probe-install",
        help="实验：往已安装的顾问语音槽位里写入一句探针台词，验证引擎是否按需读盘")
    p_probe.add_argument("--sound", action="append", default=[],
                         help="要覆盖的声音名，可重复；默认覆盖试听与点击应答槽位")
    p_probe.add_argument("--all", action="store_true",
                         help="覆盖全部 248 个槽位——推荐，这样任何顾问语音都能验出结果")
    p_probe.add_argument("--text", default="", help="自定义探针台词")
    p_probe.add_argument("--voice", default="")
    p_probe.set_defaults(func=lambda a: asyncio.run(_probe_install(a)))

    p_pstatus = sub.add_parser("probe-status", help="查看当前被覆盖的槽位")
    p_pstatus.set_defaults(func=cmd_probe_status)

    p_prestore = sub.add_parser("probe-restore", help="把探针覆盖的槽位还原成原音频")
    p_prestore.set_defaults(func=cmd_probe_restore)

    p_un = sub.add_parser("uninstall", help="移除本工具安装的 mod（整合版与旧的独立桥接都会清）")
    p_un.set_defaults(func=cmd_uninstall)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _log_setup(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
