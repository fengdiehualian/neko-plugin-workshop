"""Discover Steam libraries and install only the versioned driving mod payload.

No Steam login, recursive drive scan, game assembly rewrite, or source compiler.
All entry points are shared by the plugin UI and the optional CLI installer.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

APP_ID = "3950270"
EXE = "teach_sister_to_drive.exe"
ASSEMBLY = "teach_sister_to_drive_Data/Managed/Assembly-CSharp.dll"
MOD_TARGET = "BepInEx/plugins/NekoSisterDrive.dll"
_LOCK = threading.Lock()


class InstallationError(ValueError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def parse_vdf(text: str) -> dict:
    if len(text) > 2 * 1024 * 1024:
        raise InstallationError("Steam 库记录过大")
    tokens = re.findall(r'"((?:\\.|[^"\\])*)"|([{}])|(?m:(//[^\n]*))', text)
    values = [brace or re.sub(r'\\([\\"])', r"\1", word) for word, brace, comment in tokens if not comment]
    stack, result = [], {}
    current, key = result, None
    for value in values:
        if value == "{":
            if key is None or len(stack) >= 16:
                raise InstallationError("Steam 库记录格式不正确")
            child = {}
            current[key] = child
            stack.append(current)
            current = child
            key = None
        elif value == "}":
            if key is not None or not stack:
                raise InstallationError("Steam 库记录格式不正确")
            current = stack.pop()
        elif key is None:
            key = value
        else:
            current[key] = value
            key = None
    if stack or key is not None:
        raise InstallationError("Steam 库记录不完整")
    return result


def steam_roots() -> list[Path]:
    roots = []
    if os.name == "nt":
        import winreg

        for hive, key, name in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
            except OSError:
                pass
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]) / "Steam")
    return list(dict.fromkeys(path.resolve() for path in roots if path.is_dir()))


def libraries(roots: list[Path]) -> list[Path]:
    result = []
    for root in roots:
        result.append(root.resolve())
        for location in (root / "steamapps/libraryfolders.vdf", root / "config/libraryfolders.vdf"):
            if not location.is_file():
                continue
            data = parse_vdf(location.read_text(encoding="utf-8-sig"))
            for key, value in data.get("libraryfolders", data.get("LibraryFolders", {})).items():
                if not key.isdigit():
                    continue
                path = value.get("path") if isinstance(value, dict) else value
                if path and Path(path).is_absolute():
                    result.append(Path(path).resolve())
    return list(dict.fromkeys(result))


def game_root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if root.is_file() and root.name.lower() == EXE:
        root = root.parent
    if not (root / EXE).is_file() or not (root / ASSEMBLY).is_file():
        raise InstallationError("所选目录不是受支持的《教妹妹开车》游戏目录")
    return root


def discover_games(roots: list[Path] | None = None) -> list[dict]:
    result = []
    for library in libraries(steam_roots() if roots is None else roots):
        manifest = library / f"steamapps/appmanifest_{APP_ID}.acf"
        if not manifest.is_file():
            continue
        data = parse_vdf(manifest.read_text(encoding="utf-8-sig")).get("AppState", {})
        folder = data.get("installdir", "")
        if data.get("appid") != APP_ID or not folder:
            continue
        common = (library / "steamapps/common").resolve()
        candidate = (common / folder).resolve()
        if not candidate.is_relative_to(common):
            continue
        try:
            root = game_root(candidate)
        except InstallationError:
            continue
        result.append(
            {
                "path": str(root),
                "name": data.get("name", "教妹妹开车 Demo"),
                "app_id": APP_ID,
                "build_id": data.get("buildid", ""),
            }
        )
    return list({item["path"]: item for item in result}.values())


def load_catalog(plugin_dir: Path) -> dict:
    catalog = json.loads((plugin_dir / "release/mod-catalog.json").read_text(encoding="utf8"))
    if catalog.get("schema_version") != 1:
        raise InstallationError("模组版本清单不受支持")
    return catalog


def inspect_game(path: str | Path, catalog: dict) -> dict:
    root = game_root(path)
    fingerprint = digest(root / ASSEMBLY)
    with (root / EXE).open("rb") as executable:
        dos = executable.read(64)
        pe = b""
        if len(dos) == 64 and dos[:2] == b"MZ":
            offset = int.from_bytes(dos[60:64], "little")
            if 64 <= offset <= 8 * 1024 * 1024:
                executable.seek(offset)
                pe = executable.read(6)
    windows_x64 = pe[:4] == b"PE\0\0" and pe[4:6] == b"\x64\x86"
    supported = windows_x64 and fingerprint in catalog.get("game_assembly_sha256", [])
    target = _target(root, MOD_TARGET)
    installed = digest(target) if target.is_file() else ""
    return {
        "path": str(root),
        "supported": supported,
        "windows_x64": windows_x64,
        "assembly_sha256": fingerprint,
        "mod_installed": bool(installed),
        "mod_current": installed == catalog["mod"]["sha256"],
        "mod_version": catalog["version"] if installed == catalog["mod"]["sha256"] else "",
        "loader_installed": all(
            (root / name).is_file()
            for name in ("BepInEx/core/BepInEx.dll", "BepInEx/core/0Harmony.dll", "winhttp.dll", "doorstop_config.ini")
        ),
    }


def _https(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise InstallationError("模组下载源必须是无凭据的 HTTPS 地址")
    return url


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _https(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def obtain_asset(asset: dict, plugin_dir: Path, cache_dir: Path) -> Path:
    expected = asset.get("sha256", "")
    size = asset.get("size")
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or not isinstance(size, int) or not 0 < size <= 32 * 1024 * 1024:
        raise InstallationError("模组清单缺少有效校验值或文件大小")
    cached = cache_dir / expected
    bundled = (plugin_dir / asset.get("bundled_file", "missing")).resolve()
    for path in (bundled, cached):
        if (path == cached or path.is_relative_to(plugin_dir.resolve())) and path.is_file():
            if path.stat().st_size == size and digest(path) == expected:
                return path
    urls = asset.get("urls", [])
    if not urls:
        raise InstallationError("模组安装文件缺失，且此版本尚未配置下载源；请重新取得完整插件包")
    cache_dir.mkdir(parents=True, exist_ok=True)
    for url in urls:
        _https(url)
        temporary = cache_dir / (uuid.uuid4().hex + ".part")
        try:
            opener = urllib.request.build_opener(_HttpsRedirects())
            with opener.open(
                urllib.request.Request(url, headers={"User-Agent": "NekoSisterDrive-Installer"}), timeout=20
            ) as response:
                _https(response.geturl())
                with temporary.open("wb") as output:
                    received = 0
                    while chunk := response.read(64 * 1024):
                        received += len(chunk)
                        if received > size:
                            raise InstallationError("模组下载超过清单大小")
                        output.write(chunk)
            if temporary.stat().st_size != size or digest(temporary) != expected:
                raise InstallationError("模组下载校验失败，未修改游戏")
            os.replace(temporary, cached)
            return cached
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
    raise InstallationError("模组下载失败或校验未通过，未修改游戏")


def game_running() -> bool:
    if os.name != "nt":
        raise InstallationError("当前安装器仅支持 Windows 游戏版本")
    process = subprocess.run(
        ["tasklist.exe", "/FI", f"IMAGENAME eq {EXE}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if process.returncode:
        raise InstallationError("无法确认游戏是否退出，请稍后重试")
    return any(row and row[0].lower() == EXE for row in csv.reader(process.stdout.splitlines()))


def _target(root: Path, relative: str) -> Path:
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise InstallationError("安装目标链接指向游戏目录之外")
    return target


def install_mod(path: str | Path, plugin_dir: Path, data_dir: Path) -> dict:
    if not _LOCK.acquire(blocking=False):
        raise InstallationError("已有模组安装或卸载任务正在进行")
    try:
        catalog = load_catalog(plugin_dir)
        state = inspect_game(path, catalog)
        if not state["supported"]:
            raise InstallationError("当前游戏构建尚未验证兼容，未安装模组")
        if state["mod_current"] and state["loader_installed"]:
            return {"status": "already_installed", "message": "模组已是当前版本", **state}
        root = Path(state["path"])
        if game_running():
            raise InstallationError("请先正常退出游戏，再安装或更新模组")
        cache = data_dir / "mod-cache"
        files = {MOD_TARGET: obtain_asset(catalog["mod"], plugin_dir, cache).read_bytes()}
        if not state["loader_installed"]:
            for name in ("winhttp.dll", "doorstop_config.ini", ".doorstop_version", "BepInEx/core"):
                if (root / name).exists():
                    raise InstallationError("发现已有或不完整的模组加载器，请先确认加载器状态；未覆盖")
            archive = obtain_asset(catalog["loader"], plugin_dir, cache)
            with ZipFile(archive) as bundle:
                for item in bundle.infolist():
                    name = item.filename
                    if item.is_dir():
                        continue
                    if name not in catalog["loader"]["files"]:
                        continue
                    if (
                        PurePosixPath(name).is_absolute()
                        or ".." in PurePosixPath(name).parts
                        or item.file_size > 8 * 1024 * 1024
                    ):
                        raise InstallationError("加载器压缩包包含无效路径或过大文件")
                    files[name] = bundle.read(item)
            if set(catalog["loader"]["files"]) - files.keys():
                raise InstallationError("模组加载器文件不完整")
        targets = {name: _target(root, name) for name in files}
        data_dir.mkdir(parents=True, exist_ok=True)
        backup = (
            data_dir
            / "install-backups"
            / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        )
        backup.mkdir(parents=True)
        previous, written = {}, []
        lock_path = root / ".neko-sister-drive-install.lock"
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise InstallationError("游戏目录已有安装任务；如上次异常退出，请检查安装记录后重试") from None
        os.close(lock_fd)
        try:
            for name, target in targets.items():
                previous[name] = target.is_file()
                if target.exists() and not target.is_file():
                    raise InstallationError("模组目标不是普通文件")
                if target.is_file():
                    dest = backup / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, dest)
            (backup / "installation.json").write_text(
                json.dumps(
                    {"game_path": str(root), "version": catalog["version"], "previous": previous, "status": "prepared"},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf8",
            )
            if game_running():
                raise InstallationError("游戏已经启动，安装已取消")
            for name, content in files.items():
                target = _target(root, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".neko-drive-", dir=target.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                    os.replace(temporary, target)
                    written.append(name)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            for name, content in files.items():
                if digest(targets[name]) != hashlib.sha256(content).hexdigest():
                    raise InstallationError("安装后校验失败")
            record = {
                "game_path": str(root),
                "version": catalog["version"],
                "previous": previous,
                "status": "installed",
                "installed_hashes": {name: digest(target) for name, target in targets.items()},
            }
            (backup / "installation.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf8")
        except Exception:
            for name in reversed(written):
                if previous[name]:
                    shutil.copy2(backup / name, targets[name])
                else:
                    targets[name].unlink(missing_ok=True)
            (backup / "installation.json").write_text(
                json.dumps(
                    {"game_path": str(root), "previous": previous, "status": "rolled_back"},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf8",
            )
            raise
        finally:
            lock_path.unlink(missing_ok=True)
        return {
            "status": "installed",
            "message": "模组已安装，请重新启动游戏后连接",
            "backup": str(backup),
            **inspect_game(root, catalog),
        }
    finally:
        _LOCK.release()


def uninstall_mod(path: str | Path, plugin_dir: Path, data_dir: Path) -> dict:
    """Back up and remove our DLL; shared loaders, other mods and saves stay intact."""
    if not _LOCK.acquire(blocking=False):
        raise InstallationError("已有模组安装或卸载任务正在进行")
    try:
        state = inspect_game(path, load_catalog(plugin_dir))
        if not state["mod_installed"]:
            return {**state, "status": "not_installed", "message": "当前游戏未安装本插件模组"}
        root = Path(state["path"])
        if game_running():
            raise InstallationError("请先正常退出游戏，再卸载模组")
        lock_path = root / ".neko-sister-drive-install.lock"
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise InstallationError("游戏目录已有安装或卸载任务，请稍后重试") from None
        os.close(lock_fd)
        removed = False
        try:
            target = _target(root, MOD_TARGET)
            if not target.is_file():
                raise InstallationError("模组文件已变化，请重新检测游戏")
            original_hash = digest(target)
            backup = (
                data_dir
                / "uninstall-backups"
                / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
            )
            saved = backup / MOD_TARGET
            saved.parent.mkdir(parents=True)
            shutil.copy2(target, saved)
            if digest(saved) != original_hash:
                raise InstallationError("模组备份校验失败，未卸载")
            record_path = backup / "uninstallation.json"
            record = {"game_path": str(root), "target": MOD_TARGET, "sha256": original_hash, "status": "prepared"}
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf8")
            if game_running():
                raise InstallationError("游戏已经启动，卸载已取消")
            target = _target(root, MOD_TARGET)
            if digest(target) != original_hash:
                raise InstallationError("模组文件已变化，未卸载；请重新检测游戏")
            target.unlink()
            removed = True
            record["status"] = "uninstalled"
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf8")
        except Exception:
            if removed:
                shutil.copy2(saved, _target(root, MOD_TARGET))
            raise
        finally:
            lock_path.unlink(missing_ok=True)
        return {
            **state,
            "status": "uninstalled",
            "mod_installed": False,
            "mod_current": False,
            "mod_version": "",
            "backup": str(backup),
            "message": "模组已卸载并备份；共享加载器和游戏存档已保留，可重新安装",
        }
    finally:
        _LOCK.release()


def installation_status(plugin_dir: Path, path: str = "") -> dict:
    catalog = load_catalog(plugin_dir)
    candidates = (
        [{"path": str(game_root(path)), "name": "教妹妹开车", "app_id": APP_ID, "build_id": ""}]
        if path.strip()
        else discover_games()
    )
    games = []
    for candidate in candidates:
        games.append({**candidate, **inspect_game(candidate["path"], catalog)})
    return {
        "games": games,
        "version": catalog["version"],
        "game_running": game_running(),
        "message": "已找到游戏，请安装模组" if games else "未找到已安装的游戏，可以手动填写游戏目录",
    }
