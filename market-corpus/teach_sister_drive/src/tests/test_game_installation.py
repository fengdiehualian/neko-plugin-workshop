import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from plugin.plugins.teach_sister_drive import game_installation as gi


@pytest.fixture
def setup(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    release = plugin / "release"
    release.mkdir(parents=True)
    game = tmp_path / "游戏 空格"
    game.mkdir()
    executable = bytearray(70)
    executable[:2] = b"MZ"
    executable[60:64] = (64).to_bytes(4, "little")
    executable[64:70] = b"PE\0\0\x64\x86"
    (game / gi.EXE).write_bytes(executable)
    assembly = game / gi.ASSEMBLY
    assembly.parent.mkdir(parents=True)
    assembly.write_bytes(b"original-game")
    mod = release / "mod.dll"
    mod.write_bytes(b"new-mod")
    loader = release / "loader.zip"
    files = ["winhttp.dll", "doorstop_config.ini", "BepInEx/core/BepInEx.dll", "BepInEx/core/0Harmony.dll"]
    with ZipFile(loader, "w") as z:
        for name in files:
            z.writestr(name, b"loader:" + name.encode())
        z.writestr("../outside.txt", b"never-extract")

    def asset(path):
        return {
            "size": path.stat().st_size,
            "sha256": gi.digest(path),
            "bundled_file": "release/" + path.name,
            "urls": [],
        }

    catalog = {
        "schema_version": 1,
        "version": "0.1.0",
        "game_assembly_sha256": [gi.digest(assembly)],
        "mod": asset(mod),
        "loader": {**asset(loader), "files": files},
    }
    (release / "mod-catalog.json").write_text(json.dumps(catalog), encoding="utf8")
    monkeypatch.setattr(gi, "game_running", lambda: False)
    return SimpleNamespace(
        plugin=plugin, release=release, game=game, assembly=assembly, catalog=catalog, data=tmp_path / "data"
    )


def test_steam_multiple_libraries_modern_and_legacy_paths(setup, tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    library = tmp_path / "另一个库"
    common = library / "steamapps/common"
    common.mkdir(parents=True)
    setup.game.rename(common / setup.game.name)
    quoted = json.dumps(str(library), ensure_ascii=False)
    for body in (f'"0" {{"path" {quoted}}}', f'"0" {quoted}'):
        (steam / "steamapps/libraryfolders.vdf").write_text('"libraryfolders" {' + body + "}", encoding="utf8")
        (library / f"steamapps/appmanifest_{gi.APP_ID}.acf").write_text(
            '"AppState" {' + f'"appid" "{gi.APP_ID}" "installdir" "{setup.game.name}" "buildid" "123"' + "}",
            encoding="utf8",
        )
        found = gi.discover_games([steam])
        assert len(found) == 1
        assert found[0]["path"] == str((common / setup.game.name).resolve())
        assert found[0]["build_id"] == "123"


def test_manifest_cannot_escape_steam_library(tmp_path):
    (tmp_path / "steamapps").mkdir()
    (tmp_path / f"steamapps/appmanifest_{gi.APP_ID}.acf").write_text(
        '"AppState" {"appid" "' + gi.APP_ID + '" "installdir" "../../outside"}'
    )
    assert gi.discover_games([tmp_path]) == []


def test_fresh_install_is_offline_complete_and_never_changes_game_assembly(setup, monkeypatch):
    monkeypatch.setattr(gi.urllib.request, "build_opener", lambda *a: pytest.fail("bundled installation used network"))
    result = gi.install_mod(setup.game, setup.plugin, setup.data)
    assert result["status"] == "installed" and result["mod_current"] and result["loader_installed"]
    assert (setup.game / gi.MOD_TARGET).read_bytes() == b"new-mod"
    assert setup.assembly.read_bytes() == b"original-game"
    assert not (setup.game.parent / "outside.txt").exists()
    assert not (setup.game / ".neko-sister-drive-install.lock").exists()
    assert json.loads((Path(result["backup"]) / "installation.json").read_text())["status"] == "installed"


def test_already_installed_is_read_only_even_while_game_runs(setup, monkeypatch):
    gi.install_mod(setup.game, setup.plugin, setup.data)
    records = list((setup.data / "install-backups").iterdir())
    monkeypatch.setattr(gi, "game_running", lambda: True)
    assert gi.install_mod(setup.game, setup.plugin, setup.data)["status"] == "already_installed"
    assert list((setup.data / "install-backups").iterdir()) == records


def test_running_game_cannot_be_modified(setup, monkeypatch):
    monkeypatch.setattr(gi, "game_running", lambda: True)
    with pytest.raises(gi.InstallationError, match="退出游戏"):
        gi.install_mod(setup.game, setup.plugin, setup.data)
    assert not (setup.game / "BepInEx").exists()


def test_wrong_game_architecture_cannot_receive_x64_loader(setup):
    (setup.game / gi.EXE).write_bytes(b"not-a-supported-executable")
    with pytest.raises(gi.InstallationError, match="尚未验证"):
        gi.install_mod(setup.game, setup.plugin, setup.data)
    assert not (setup.game / "BepInEx").exists()


def test_corrupt_bundled_mod_and_unknown_game_are_rejected(setup):
    (setup.release / "mod.dll").write_bytes(b"bad")
    with pytest.raises(gi.InstallationError, match="缺失"):
        gi.install_mod(setup.game, setup.plugin, setup.data)
    setup.assembly.write_bytes(b"new-game-version")
    with pytest.raises(gi.InstallationError, match="尚未验证"):
        gi.install_mod(setup.game, setup.plugin, setup.data)
    assert not (setup.game / "BepInEx").exists()


def test_existing_unrecognized_loader_is_preserved(setup):
    (setup.game / "winhttp.dll").write_bytes(b"another-loader")
    with pytest.raises(gi.InstallationError, match="加载器"):
        gi.install_mod(setup.game, setup.plugin, setup.data)
    assert (setup.game / "winhttp.dll").read_bytes() == b"another-loader"


def test_update_backs_up_old_dll_without_replacing_existing_loader(setup):
    gi.install_mod(setup.game, setup.plugin, setup.data)
    (setup.game / gi.MOD_TARGET).write_bytes(b"old-mod")
    (setup.game / "winhttp.dll").write_bytes(b"existing-loader")
    result = gi.install_mod(setup.game, setup.plugin, setup.data)
    assert (Path(result["backup"]) / gi.MOD_TARGET).read_bytes() == b"old-mod"
    assert (setup.game / "winhttp.dll").read_bytes() == b"existing-loader"
    assert (setup.game / gi.MOD_TARGET).read_bytes() == b"new-mod"


def test_partial_write_rolls_back_new_files_and_clears_lock(setup, monkeypatch):
    replace = gi.os.replace
    calls = []

    def failing(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise PermissionError("simulated failure")
        return replace(source, target)

    monkeypatch.setattr(gi.os, "replace", failing)
    with pytest.raises(PermissionError):
        gi.install_mod(setup.game, setup.plugin, setup.data)
    assert not (setup.game / gi.MOD_TARGET).exists()
    assert not (setup.game / ".neko-sister-drive-install.lock").exists()
    record = next((setup.data / "install-backups").glob("*/installation.json"))
    assert json.loads(record.read_text())["status"] == "rolled_back"
    assert setup.assembly.read_bytes() == b"original-game"


def test_target_directory_link_cannot_write_outside_game(setup, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (setup.game / "BepInEx").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(gi.InstallationError, match="链接"):
        gi.install_mod(setup.game, setup.plugin, setup.data)
    assert not list(outside.iterdir())


def test_verified_download_and_bad_checksum_do_not_touch_game(setup, monkeypatch):
    content = b"downloaded"
    expected = hashlib.sha256(content).hexdigest()
    asset = {"size": len(content), "sha256": expected, "urls": ["https://example.test/mod.dll"]}

    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.test/mod.dll"

    monkeypatch.setattr(
        gi.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **kw: Response(content))
    )
    path = gi.obtain_asset(asset, setup.plugin, setup.data)
    assert path.read_bytes() == content
    with pytest.raises(gi.InstallationError, match="HTTPS"):
        gi.obtain_asset(
            {**asset, "sha256": "0" * 64, "urls": ["http://example.test/mod.dll"]}, setup.plugin, setup.data
        )
    with pytest.raises(gi.InstallationError, match="校验"):
        gi.obtain_asset({**asset, "sha256": "0" * 64}, setup.plugin, setup.data)
    assert not list(setup.data.glob("*.part"))
    assert not (setup.game / "BepInEx").exists()


def test_shipped_release_matches_catalog_and_contains_no_game_assembly():
    plugin = Path(__file__).parents[1]
    catalog = gi.load_catalog(plugin)
    for kind in ("mod", "loader"):
        asset = catalog[kind]
        path = plugin / asset["bundled_file"]
        assert path.stat().st_size == asset["size"] and gi.digest(path) == asset["sha256"]
    with ZipFile(plugin / catalog["loader"]["bundled_file"]) as bundle:
        assert set(catalog["loader"]["files"]).issubset(bundle.namelist())
        assert "changelog.txt" not in catalog["loader"]["files"]
        assert not any("Assembly-CSharp" in name for name in bundle.namelist())


def test_uninstall_backs_up_our_mod_preserves_other_files_and_allows_reinstall(setup):
    gi.install_mod(setup.game, setup.plugin, setup.data)
    other = setup.game / "BepInEx/plugins/AnotherMod.dll"
    other.write_bytes(b"other-mod")
    save = setup.game / "save.json"
    save.write_bytes(b"player-save")
    before = {str(p.relative_to(setup.game)): p.read_bytes() for p in setup.game.rglob("*") if p.is_file()}
    result = gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    assert result["status"] == "uninstalled" and not result["mod_installed"] and not result["mod_current"]
    assert result["loader_installed"] and not (setup.game / gi.MOD_TARGET).exists()
    assert (Path(result["backup"]) / gi.MOD_TARGET).read_bytes() == b"new-mod"
    record = json.loads((Path(result["backup"]) / "uninstallation.json").read_text(encoding="utf8"))
    assert record["status"] == "uninstalled" and record["sha256"] == hashlib.sha256(b"new-mod").hexdigest()
    after = {str(p.relative_to(setup.game)): p.read_bytes() for p in setup.game.rglob("*") if p.is_file()}
    assert after == {name: content for name, content in before.items() if name != str(Path(gi.MOD_TARGET))}
    assert gi.install_mod(setup.game, setup.plugin, setup.data)["mod_current"]


def test_uninstall_without_mod_is_read_only_even_if_game_runs(setup, monkeypatch):
    monkeypatch.setattr(gi, "game_running", lambda: True)
    result = gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    assert result["status"] == "not_installed" and not setup.data.exists()


def test_running_game_and_existing_lock_block_uninstall(setup, monkeypatch):
    gi.install_mod(setup.game, setup.plugin, setup.data)
    monkeypatch.setattr(gi, "game_running", lambda: True)
    with pytest.raises(gi.InstallationError, match="退出游戏"):
        gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    monkeypatch.setattr(gi, "game_running", lambda: False)
    lock = setup.game / ".neko-sister-drive-install.lock"
    lock.write_bytes(b"another-task")
    with pytest.raises(gi.InstallationError, match="已有"):
        gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    assert lock.read_bytes() == b"another-task"
    assert (setup.game / gi.MOD_TARGET).read_bytes() == b"new-mod"


def test_uninstall_allows_older_mod_after_game_update(setup):
    gi.install_mod(setup.game, setup.plugin, setup.data)
    setup.assembly.write_bytes(b"game-update")
    (setup.game / gi.MOD_TARGET).write_bytes(b"old-mod")
    result = gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    assert not result["supported"] and result["status"] == "uninstalled"
    assert (Path(result["backup"]) / gi.MOD_TARGET).read_bytes() == b"old-mod"
    assert setup.assembly.read_bytes() == b"game-update"


def test_failed_uninstall_backup_never_removes_mod(setup, monkeypatch):
    gi.install_mod(setup.game, setup.plugin, setup.data)

    def fail(*args, **kwargs):
        raise PermissionError("backup denied")

    monkeypatch.setattr(gi.shutil, "copy2", fail)
    with pytest.raises(PermissionError, match="backup denied"):
        gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    assert (setup.game / gi.MOD_TARGET).read_bytes() == b"new-mod"
    assert not (setup.game / ".neko-sister-drive-install.lock").exists()


def test_game_starting_during_backup_cancels_uninstall(setup, monkeypatch):
    gi.install_mod(setup.game, setup.plugin, setup.data)
    running = iter([False, True])
    monkeypatch.setattr(gi, "game_running", lambda: next(running))
    with pytest.raises(gi.InstallationError, match="已经启动"):
        gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    assert (setup.game / gi.MOD_TARGET).read_bytes() == b"new-mod"
    assert not (setup.game / ".neko-sister-drive-install.lock").exists()


def test_failed_uninstall_record_restores_mod(setup, monkeypatch):
    gi.install_mod(setup.game, setup.plugin, setup.data)
    write = Path.write_text

    def fail_uninstalled(path, content, *args, **kwargs):
        if path.name == "uninstallation.json" and json.loads(content)["status"] == "uninstalled":
            raise PermissionError("record write denied")
        return write(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_uninstalled)
    with pytest.raises(PermissionError, match="record write denied"):
        gi.uninstall_mod(setup.game, setup.plugin, setup.data)
    assert (setup.game / gi.MOD_TARGET).read_bytes() == b"new-mod"
    assert not (setup.game / ".neko-sister-drive-install.lock").exists()


def test_uninstall_never_deletes_a_target_outside_game(setup, tmp_path):
    # Windows directory junctions do not require the symlink privilege.
    import os
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    dll = outside / "NekoSisterDrive.dll"
    dll.write_bytes(b"other-mod")
    bepinex = setup.game / "BepInEx"
    bepinex.mkdir()
    link = bepinex / "plugins"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
        if result.returncode:
            pytest.skip("directory junction unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(gi.InstallationError, match="链接"):
            gi.uninstall_mod(setup.game, setup.plugin, setup.data)
        assert dll.read_bytes() == b"other-mod"
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()
