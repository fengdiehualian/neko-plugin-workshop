# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Install (or remove) a built voice pack into the Stellaris user directory.

Three pieces have to line up for the game to see a local mod:

1. ``<user>/mod/<mod_id>/``      the content, including ``descriptor.mod``
2. ``<user>/mod/<mod_id>.mod``   the stub the launcher scans, carrying ``path=``
3. ``<user>/dlc_load.json``      ``enabled_mods`` gains ``mod/<mod_id>.mod``

Step 3 is the step people forget, and the failure is silent: the mod shows up in
the launcher but the game never loads its sound group, so the voice simply is not
in the list. The launcher can also rewrite ``dlc_load.json`` when a playset is
saved, which is why the installer reports what it changed instead of claiming the
mod is live — the honest statement is "the game-side flag is set".

Destructive operations are guarded by an ownership marker. Only a directory that
this plugin created is ever replaced or deleted; anything else is refused rather
than merged into.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

MARKER_NAME = ".neko_stellaris_pack.json"


class InstallError(RuntimeError):
    """Raised when the target directory cannot be used safely."""


@dataclass
class InstallResult:
    mod_dir: Path
    stub_path: Path
    enabled_list_path: Path
    already_present: bool
    files_copied: int
    bytes_copied: int
    action: str = "install"          # "install" | "uninstall"
    dlc_load_backup: str = ""
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.action == "uninstall":
            return (f"已卸载：删除 {self.files_copied} 个文件 / "
                    f"{self.bytes_copied / 1048576:.1f} MiB（{self.mod_dir}）")
        state = "已存在，已刷新" if self.already_present else "新安装"
        return (f"{state}：{self.mod_dir}（{self.files_copied} 个文件 / "
                f"{self.bytes_copied / 1048576:.1f} MiB），"
                f"dlc_load.json 已登记")


def _read_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise InstallError(f"{path.name} 不是合法 JSON，拒绝改写：{exc}") from exc
    return data if isinstance(data, dict) else dict(default)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")


def _is_our_dir(path: Path) -> bool:
    return (path / MARKER_NAME).is_file()


# --------------------------------------------------------------------------- #
# Finding a copy we did not put there
# --------------------------------------------------------------------------- #

STUB_GLOBS = ("ugc_*.mod", "pdx_*.mod")


def _marker_mod_id(path: Path) -> str:
    """The ``mod_id`` recorded in our ownership marker, or "" if there is none.

    The marker alone is not an identity: it says "this plugin wrote here", not
    *which* mod. Every pack this plugin installs carries one with the same name,
    so matching on its existence made the bridge mod resolve to the voice pack's
    published copy.
    """
    try:
        data = json.loads((path / MARKER_NAME).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - absent, unreadable or not JSON
        return ""
    return str(data.get("mod_id") or "") if isinstance(data, dict) else ""


def _stub_remote_id(stub: Path) -> str:
    """The item id a stub's filename implies: ``ugc_3819922949.mod`` -> that number.

    Used only when the stub carries no ``remote_file_id``; the launcher writes
    that field, so in practice this is the fallback for stubs written by hand.
    """
    stem = stub.stem
    for prefix in ("ugc_", "pdx_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return stem.split("_")[0]


def _parse_mod_stub(path: Path) -> dict[str, str]:
    """The ``key="value"`` lines of a ``.mod`` descriptor.

    Deliberately tiny and tolerant: the format is flat, it is written by three
    different programs (us, the launcher, the game), and a stub we cannot parse is
    not an error worth raising -- it just is not the one we are looking for.
    """
    found: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for match in re.finditer(r'(?m)^\s*([A-Za-z_]+)\s*=\s*"([^"]*)"', text):
        found[match.group(1)] = match.group(2)
    return found


@dataclass(frozen=True)
class PublishedCopy:
    """A subscribed Workshop / PDX copy of one of our mods, found on disk."""

    path: Path
    remote_id: str
    stub: Path

    def summary(self) -> str:
        return f"创意工坊副本 {self.path}（条目 {self.remote_id}，由 Steam 托管）"


def find_published_copy(stellaris_user_dir: str | Path, *,
                        mod_id: str) -> PublishedCopy | None:
    """The subscribed copy of ``mod_id``, if this machine has one.

    Publishing our own mod to the Workshop makes the launcher *migrate* the local
    install: it deletes ``<user>/mod/<mod_id>/`` and its stub, subscribes the
    Workshop item and enables ``mod/ugc_<id>.mod`` instead. From then on nothing
    but Steam writes that directory, so the plugin cannot assume its files are
    where it left them. Without this lookup the startup check answers "not
    installed" for a mod the game is actively loading, and auto-install quietly
    creates a second copy of it -- two entries with the same name in the launcher,
    and with both enabled the bridge's on_action hooks are appended twice, so every
    bridged event fires twice.

    Identified by a file only we generate, never by name: display names are
    translated and two mods may share one.
    """
    mod_root = Path(stellaris_user_dir) / "mod"
    if not mod_root.is_dir():
        return None
    for pattern in STUB_GLOBS:
        for stub in sorted(mod_root.glob(pattern)):
            info = _parse_mod_stub(stub)
            raw = info.get("path")
            if not raw:
                continue
            # Stubs use forward slashes inside quotes; Windows accepts both.
            candidate = Path(raw)
            if not candidate.is_dir():
                continue
            ours = ((candidate / "sound" / f"{mod_id}.asset").is_file()
                    or _marker_mod_id(candidate) == mod_id)
            if ours:
                return PublishedCopy(candidate,
                                     info.get("remote_file_id") or _stub_remote_id(stub),
                                     stub)
    return None


@dataclass(frozen=True)
class ActiveInstall:
    """Where the game is actually reading this mod from, and who owns that copy."""

    path: Path
    kind: str                      # "local" | "workshop"
    stub: Path | None = None
    remote_id: str = ""

    @property
    def is_published(self) -> bool:
        return self.kind == "workshop"

    def stub_name(self, mod_id: str) -> str:
        """The ``dlc_load.json`` entry that enables this copy."""
        if self.stub is not None:
            return f"mod/{self.stub.name}"
        return f"mod/{mod_id}.mod"

    def summary(self) -> str:
        if self.kind == "workshop":
            return (f"创意工坊副本 {self.path}"
                    f"（条目 {self.remote_id or '?'}，由 Steam 托管）")
        return f"本地安装 {self.path}"


def find_active_install(stellaris_user_dir: str | Path, *,
                        mod_id: str) -> ActiveInstall | None:
    """Resolve the copy the game loads, preferring the one we own.

    The local copy wins when it exists because that is the one this plugin writes
    and the one its freshness check was always written against; the published copy
    is the fallback, and after a migration it is the only one present.
    """
    user = Path(stellaris_user_dir)
    local = user / "mod" / mod_id
    if (local / MARKER_NAME).is_file() or (local / "sound").is_dir():
        return ActiveInstall(local, "local", user / "mod" / f"{mod_id}.mod")
    published = find_published_copy(user, mod_id=mod_id)
    if published is not None:
        return ActiveInstall(published.path, "workshop", published.stub,
                             published.remote_id)
    return None


def published_copy_blocks_install(stellaris_user_dir: str | Path, *,
                                  mod_id: str) -> tuple[bool, str]:
    """Should an automatic/plugin-entry install be refused, and why.

    Only the CLI is allowed to install over a published copy, and only because it
    is the explicit escape hatch: an author testing a change locally wants to do it
    on purpose, having dealt with the Workshop copy first.
    """
    if (Path(stellaris_user_dir) / "mod" / mod_id).is_dir():
        return False, ""
    published = find_published_copy(stellaris_user_dir, mod_id=mod_id)
    if published is None:
        return False, ""
    return True, (
        f"{published.summary()} 已经在游戏里生效，本地不再安装第二份 —— "
        f"同名两份会同时出现在启动器列表里，两个都勾选时桥接的钩子会被追加两次，"
        f"每个事件触发两遍。要更新它请重新上传工坊版本；确实需要装本地一份用于测试，"
        f"请先用命令行 build_cli.py combined --install。"
    )



# --------------------------------------------------------------------------- #
# Paradox launcher integration
# --------------------------------------------------------------------------- #

LAUNCHER_DB_NAME = "launcher-v2.sqlite"
LAUNCHER_PROCESS_HINTS = ("paradox launcher", "bootstrapper", "dowser", "launcher")


def launcher_db_path(stellaris_user_dir: str | Path) -> Path | None:
    candidate = Path(stellaris_user_dir) / LAUNCHER_DB_NAME
    return candidate if candidate.is_file() else None


def launcher_is_running() -> bool:
    """True when the Paradox launcher holds the DB.

    Writing while it runs is not just racy: the launcher keeps the playset in
    memory and rewrites the database when it exits or when you press Play, so a
    write made underneath it is silently discarded. Refusing is the honest
    behaviour -- a "succeeded" message followed by nothing working is worse than
    being told to close a window.
    """
    import subprocess
    try:
        completed = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return False
    if completed.returncode != 0:
        return False
    text = (completed.stdout or "").lower()
    return any(hint in text for hint in LAUNCHER_PROCESS_HINTS)


@dataclass
class PlaysetRegistration:
    playset_id: str
    playset_name: str
    mod_id: str
    registry_id: str
    already_enabled: bool
    changed: bool
    note: str = ""

    def summary(self) -> str:
        if not self.changed:
            return f"{self.registry_id} 已在播放集「{self.playset_name}」中"
        return f"{self.registry_id} 已加入播放集「{self.playset_name}」并启用"


def register_in_launcher_playset(
    stellaris_user_dir: str | Path,
    *,
    mod_id: str,
    force: bool = False,
) -> PlaysetRegistration:
    """Enable an already-discovered mod inside the launcher's current playset.

    Why this is needed even though the mod is already on disk: the launcher scans
    ``mod/`` and inserts a row for every ``.mod`` file it finds, but only the mods
    it knew about when the playset was created join that playset. A mod installed
    while the launcher was open therefore shows up in the mod list yet never
    reaches ``dlc_load.json``, so the game behaves as if it were not installed.

    This writes exactly one row into ``playsets_mods`` -- the four-column join
    table, whose shape is stable -- and never touches the 41-column ``mods``
    table, which the launcher owns and which already has a correct row by the
    time we get here. That is the same edit as ticking the checkbox by hand, and
    deliberately the smallest one available.
    """
    user = Path(stellaris_user_dir)
    db = launcher_db_path(user)
    registry_id = f"mod/{mod_id}.mod"
    if db is None:
        raise InstallError("没有找到 launcher-v2.sqlite，无法写入播放集")

    if launcher_is_running() and not force:
        raise InstallError(
            "Paradox 启动器正在运行，它会在退出时覆盖这次写入。"
            "先关掉启动器再重试，或者直接在启动器 mod 列表里勾选。")

    import sqlite3
    con = sqlite3.connect(str(db), timeout=10)
    try:
        cur = con.cursor()
        row = cur.execute(
            "select id, name from mods where gameRegistryId = ?", (registry_id,)
        ).fetchone()
        if row is None:
            raise InstallError(
                f"启动器还没发现 {registry_id}；先打开一次启动器让它扫描 mod/ 目录")
        mod_uuid, mod_name = row

        playset = cur.execute(
            "select id, name from playsets "
            "where isRemoved = 0 order by isActive desc, "
            "coalesce(lastUsedAt, 0) desc, coalesce(updatedOn, 0) desc limit 1"
        ).fetchone()
        if playset is None:
            raise InstallError("启动器里没有任何播放集，先在启动器里创建一个")
        playset_id, playset_name = playset

        existing = cur.execute(
            "select enabled from playsets_mods where playsetId = ? and modId = ?",
            (playset_id, mod_uuid),
        ).fetchone()

        if existing is not None and existing[0]:
            return PlaysetRegistration(playset_id, playset_name, mod_id, registry_id,
                                       already_enabled=True, changed=False)

        if existing is None:
            (next_pos,) = cur.execute(
                "select coalesce(max(position), -1) + 1 from playsets_mods where playsetId = ?",
                (playset_id,),
            ).fetchone()
            cur.execute(
                "insert into playsets_mods (playsetId, modId, enabled, position) "
                "values (?, ?, 1, ?)",
                (playset_id, mod_uuid, next_pos),
            )
        else:
            cur.execute(
                "update playsets_mods set enabled = 1 where playsetId = ? and modId = ?",
                (playset_id, mod_uuid),
            )
        con.commit()
        return PlaysetRegistration(playset_id, playset_name, mod_id, registry_id,
                                   already_enabled=False, changed=True)
    finally:
        con.close()


def install_pack(
    source_dir: str | Path,
    stellaris_user_dir: str | Path,
    *,
    mod_id: str,
    mod_stub_text: str,
    version: str = "1.0.0",
    enable: bool = True,
    register_playset: bool = True,
) -> InstallResult:
    """Copy ``source_dir`` into the mod folder and register it."""
    src = Path(source_dir)
    user = Path(stellaris_user_dir)
    if not (src / "descriptor.mod").is_file():
        raise InstallError(f"源目录缺少 descriptor.mod：{src}")
    if not (user / "mod").parent.is_dir():
        raise InstallError(f"不是 Stellaris 用户目录：{user}")

    mod_root = user / "mod"
    mod_root.mkdir(parents=True, exist_ok=True)
    target = mod_root / mod_id

    already = target.exists()
    if already and not _is_our_dir(target):
        # Someone else's mod (or a hand-made one) lives here. Refuse rather than
        # overwrite: destroying a user's own mod because the id collided is far
        # worse than making them rename ours.
        raise InstallError(
            f"{target} 已存在且不是本插件创建的（缺少 {MARKER_NAME}），拒绝覆盖")

    if already:
        shutil.rmtree(target)

    files = 0
    total = 0
    for item in sorted(src.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
        files += 1
        total += dest.stat().st_size

    (target / MARKER_NAME).write_text(json.dumps({
        "mod_id": mod_id,
        "version": version,
        "generator": "neko_stellaris",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    stub = mod_root / f"{mod_id}.mod"
    stub.write_text(mod_stub_text, encoding="utf-8", newline="\n")

    result = InstallResult(
        mod_dir=target,
        stub_path=stub,
        enabled_list_path=user / "dlc_load.json",
        already_present=already,
        files_copied=files,
        bytes_copied=total,
    )

    if enable:
        _enable_mod(result.enabled_list_path, f"mod/{mod_id}.mod", result)

    if register_playset:
        # Best effort, and never fatal: the files on disk are the deliverable, and
        # this step can legitimately fail (launcher open, launcher never run).
        try:
            registration = register_in_launcher_playset(user, mod_id=mod_id)
            result.notes.append(registration.summary())
        except Exception as exc:  # noqa: BLE001
            result.notes.append(
                f"播放集未自动登记（{exc}）——"
                f"在 Paradox 启动器的 mod 列表里勾选「{mod_id}」即可")
    return result


def _enable_mod(dlc_load_path: Path, entry: str, result: InstallResult) -> None:
    data = _read_json(dlc_load_path, {"enabled_mods": [], "disabled_dlcs": []})
    mods = data.get("enabled_mods")
    if not isinstance(mods, list):
        mods = []
    if entry in mods:
        result.notes.append("该 mod 早已在 dlc_load.json 的 enabled_mods 里")
        return

    if dlc_load_path.is_file() and not result.dlc_load_backup:
        backup = dlc_load_path.with_name("dlc_load.json.neko-backup")
        if not backup.exists():
            shutil.copy2(dlc_load_path, backup)
            result.dlc_load_backup = str(backup)

    mods.append(entry)
    data["enabled_mods"] = mods
    data.setdefault("disabled_dlcs", [])
    _write_json(dlc_load_path, data)
    result.notes.append(
        "已写入 dlc_load.json；若你用 Paradox 启动器的播放集启动，"
        "启动器保存播放集时可能覆盖它，届时在启动器里勾选一次即可")

def uninstall_pack(stellaris_user_dir: str | Path, *, mod_id: str,
                   disable: bool = True) -> InstallResult:
    """Remove a pack this plugin installed. Refuses to touch anything else."""
    user = Path(stellaris_user_dir)
    target = user / "mod" / mod_id
    stub = user / "mod" / f"{mod_id}.mod"
    dlc_load = user / "dlc_load.json"

    if target.exists() and not _is_our_dir(target):
        raise InstallError(f"{target} 不是本插件创建的，拒绝删除")

    files = 0
    total = 0
    if target.exists():
        for item in target.rglob("*"):
            if item.is_file():
                files += 1
                total += item.stat().st_size
        shutil.rmtree(target)
    if stub.exists():
        stub.unlink()

    result = InstallResult(
        mod_dir=target, stub_path=stub, enabled_list_path=dlc_load,
        already_present=False, files_copied=files, bytes_copied=total,
        action="uninstall",
    )
    if disable and dlc_load.is_file():
        data = _read_json(dlc_load, {"enabled_mods": [], "disabled_dlcs": []})
        entry = f"mod/{mod_id}.mod"
        mods = [m for m in (data.get("enabled_mods") or []) if m != entry]
        data["enabled_mods"] = mods
        data.setdefault("disabled_dlcs", [])
        _write_json(dlc_load, data)
        result.notes.append("已从 dlc_load.json 的 enabled_mods 移除")

    # Say why "0 files deleted" is the right answer rather than a failure: the
    # copy that is actually loaded is Steam's, and no tool of ours may touch it.
    published = find_published_copy(user, mod_id=mod_id)
    if published is not None:
        result.notes.append(
            f"注意：{published.summary()} 仍然装着，本地卸载不会影响它 —— "
            f"要移除请在 Steam 里取消订阅（或在启动器里取消勾选）。")
    return result


@dataclass
class Installation:
    """What is currently installed, as observed on disk."""

    mod_id: str
    installed: bool
    mod_dir: str
    stub_exists: bool
    registered: bool
    version: str
    clip_count: int
    bytes_total: int
    file_count: int = 0
    kind: str = "voice"
    source: str = "local"          # "local" | "workshop"
    remote_id: str = ""

    def summary(self) -> str:
        if not self.installed:
            return f"{self.mod_id}: 未安装"
        # The bridge mod has no wavs, so reporting "0 个 wav" for it would read
        # like a broken install rather than a different kind of mod.
        size = f"{self.bytes_total / 1048576:.1f} MiB"
        payload = (f"{self.clip_count} 个 wav / {size}" if self.kind == "voice"
                   else f"{self.file_count} 个文件 / {size}")
        if self.source == "workshop":
            where = (f"创意工坊 {self.remote_id or '?'} 副本（Steam 托管，本地不安装），"
                     f"dlc_load={'已登记' if self.registered else '未登记'}")
        else:
            where = (f"stub={'有' if self.stub_exists else '无'}，"
                     f"dlc_load={'已登记' if self.registered else '未登记'}")
        return f"{self.mod_id}: 已安装 v{self.version or '?'}，{payload}，{where}"


def inspect_installation(stellaris_user_dir: str | Path, *, mod_id: str) -> Installation:
    """Report the on-disk state without changing anything.

    Inspects whichever copy the game actually loads -- after the launcher migrates
    a published mod, that is the Workshop directory, not the one this plugin used
    to write.
    """
    user = Path(stellaris_user_dir)
    active = find_active_install(user, mod_id=mod_id)
    target = active.path if active is not None else user / "mod" / mod_id
    stub = (active.stub if active is not None and active.stub is not None
            else user / "mod" / f"{mod_id}.mod")
    entry = active.stub_name(mod_id) if active is not None else f"mod/{mod_id}.mod"
    dlc_load = user / "dlc_load.json"

    version = ""
    marker = target / MARKER_NAME
    if marker.is_file():
        try:
            version = str(json.loads(marker.read_text(encoding="utf-8")).get("version") or "")
        except Exception:  # noqa: BLE001
            version = ""

    clips = 0
    total = 0
    files = 0
    vo_dir = target / "sound" / "vo" / "nekko_stellaris"
    if vo_dir.is_dir():
        for item in vo_dir.glob("*.wav"):
            clips += 1
            total += item.stat().st_size
    if total == 0 and target.is_dir():
        # Not a voice pack: report whole-tree size so an installed bridge mod
        # still shows something meaningful.
        for item in target.rglob("*"):
            if item.is_file():
                files += 1
                total += item.stat().st_size

    registered = False
    if dlc_load.is_file():
        try:
            data = json.loads(dlc_load.read_text(encoding="utf-8"))
            registered = entry in (data.get("enabled_mods") or [])
        except Exception:  # noqa: BLE001
            registered = False

    return Installation(
        mod_id=mod_id,
        installed=active is not None or target.is_dir(),
        mod_dir=str(target),
        stub_exists=stub.is_file(),
        registered=registered,
        version=version,
        clip_count=clips,
        bytes_total=total,
        file_count=files,
        kind="voice" if clips else "other",
        source=active.kind if active is not None else "local",
        remote_id=active.remote_id if active is not None else "",
    )
