"""steam_manager 单元测试：纯函数与错误分支（不依赖真实 Steam 安装）。

注意：与 card_master/media_generator 测试一致，插件模块在函数内导入，
避免 isort first-party 判定在本地/CI 间分歧。
"""

from __future__ import annotations

# ══════════ parse_vdf ══════════


def test_parse_vdf_flat_key_values() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    text = '"root" { "name" "Counter-Strike 2" "appid" "730" }'
    data = parse_vdf(text)
    assert data["root"]["name"] == "Counter-Strike 2"
    assert data["root"]["appid"] == "730"


def test_parse_vdf_nested_blocks() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    text = (
        '"libraryfolders"\n'
        '{\n'
        '\t"1"\t"D:\\SteamLibrary"\n'
        '\t"2"\n'
        '\t{\n'
        '\t\t"path"\t"C:\\Games\\Steam"\n'
        '\t}\n'
        '}\n'
    )
    data = parse_vdf(text)
    assert data["libraryfolders"]["1"] == "D:\\SteamLibrary"
    assert data["libraryfolders"]["2"]["path"] == "C:\\Games\\Steam"


def test_parse_vdf_escaped_quotes() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    text = '"AppState" { "name" "a \\"quoted\\" name" }'
    data = parse_vdf(text)
    assert data["AppState"]["name"] == 'a "quoted" name'


def test_parse_vdf_empty() -> None:
    from plugin.plugins.steam_manager.steam import parse_vdf

    assert parse_vdf("") == {}
    assert parse_vdf(None) == {}


# ══════════ _state_flags_text ══════════


def test_state_flags_text() -> None:
    from plugin.plugins.steam_manager.steam import _state_flags_text

    assert _state_flags_text(4) == "已安装"
    assert _state_flags_text(2) == "需要更新"
    assert _state_flags_text(0) == "未安装"
    assert _state_flags_text(5) == "已安装"  # 4+1（含已安装位）


# ══════════ scan_games ══════════


def _make_fake_library(tmp_path, steamapps: dict[str, str]) -> str:
    """构造伪 Steam 目录树，返回 steam_path。"""
    apps_dir = tmp_path / "steamapps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    for app_id, name in steamapps.items():
        acf = (
            '"AppState"\n'
            '{\n'
            f'\t"appid"\t\t"{app_id}"\n'
            f'\t"name"\t\t"{name}"\n'
            '\t"StateFlags"\t"4"\n'
            '\t"SizeOnDisk"\t"10485760"\n'
            '}\n'
        )
        (apps_dir / f"appmanifest_{app_id}.acf").write_text(acf, encoding="utf-8")
    return str(tmp_path)


def test_scan_games_single_library(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import scan_games

    steam_path = _make_fake_library(tmp_path, {"730": "Counter-Strike 2", "570": "DOTA 2"})
    games = scan_games(steam_path)
    assert len(games) == 2
    names = {g["name"] for g in games}
    assert names == {"Counter-Strike 2", "DOTA 2"}
    cs = next(g for g in games if g["app_id"] == "730")
    assert cs["installed"] is True
    assert cs["size_mb"] == 10.0


def test_scan_games_with_libraryfolders(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import scan_games

    steam_path = _make_fake_library(tmp_path, {"730": "Main Game"})
    # 扩展库
    lib2 = tmp_path / "lib2"
    (lib2 / "steamapps").mkdir(parents=True)
    (lib2 / "steamapps" / "appmanifest_413150.acf").write_text(
        '"AppState"\n{\n\t"appid"\t"413150"\n\t"name"\t"Stardew Valley"\n\t"StateFlags"\t"4"\n}\n',
        encoding="utf-8",
    )
    vdf = (
        '"libraryfolders"\n'
        '{\n'
        f'\t"0"\t"{steam_path}"\n'
        f'\t"1"\t"{lib2}"\n'
        '}\n'
    )
    (tmp_path / "steamapps" / "libraryfolders.vdf").write_text(vdf, encoding="utf-8")
    games = scan_games(steam_path)
    assert len(games) == 2
    assert {g["name"] for g in games} == {"Main Game", "Stardew Valley"}


def test_scan_games_deduplicates(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import scan_games

    steam_path = _make_fake_library(tmp_path, {"730": "Dup"})
    games = scan_games(steam_path)
    assert len(games) == 1


def test_find_game_by_appid_and_name(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import find_game

    steam_path = _make_fake_library(tmp_path, {"730": "Counter-Strike 2", "570": "DOTA 2"})
    assert find_game(steam_path, "730")[0]["name"] == "Counter-Strike 2"
    assert {g["name"] for g in find_game(steam_path, "counter")} == {"Counter-Strike 2"}
    assert len(find_game(steam_path, "")) == 2
    assert find_game(steam_path, "nonexistent-game-xyz") == []


def test_find_game_honors_limit(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import find_game

    steam_path = _make_fake_library(tmp_path, {"730": "Game A", "570": "Game B", "10": "Game C"})
    assert len(find_game(steam_path, "", limit=2)) == 2


# ══════════ launch_game 错误分支 ══════════


def test_launch_game_invalid_appid(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import launch_game

    result = launch_game(str(tmp_path), "abc")
    assert result["ok"] is False
    assert result["error"] == "INVALID_APP_ID"


def test_launch_game_exe_missing(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import launch_game

    result = launch_game(str(tmp_path), "730")
    assert result["ok"] is False
    assert result["error"] == "STEAM_EXE_NOT_FOUND"


# ══════════ v0.3.0：凭证 / 本地账号 / 价格 / 打开应用 ══════════


def test_cookie_value_extract() -> None:
    from plugin.plugins.steam_manager.steam import _cookie_value

    cookie = 'sessionid=abc123; steamLoginSecure=7656119%7C%7Ctoken; foo="bar"'
    assert _cookie_value(cookie, "sessionid") == "abc123"
    assert _cookie_value(cookie, "steamLoginSecure") == "7656119%7C%7Ctoken"
    assert _cookie_value(cookie, "missing") == ""


def test_read_local_account(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import read_local_account

    config = tmp_path / "config"
    config.mkdir()
    (config / "loginusers.vdf").write_text(
        '"users"\n'
        '{\n'
        '\t"76561198000000001"\n'
        '\t{\n'
        '\t\t"PersonaName"\t\t"Player One"\n'
        '\t\t"mostrecent"\t\t"2"\n'
        '\t\t"RememberPassword"\t\t"1"\n'
        '\t}\n'
        '\t"76561198000000002"\n'
        '\t{\n'
        '\t\t"PersonaName"\t\t"Player Two"\n'
        '\t\t"mostrecent"\t\t"1"\n'
        '\t}\n'
        '}\n',
        encoding="utf-8",
    )
    account = read_local_account(str(tmp_path))
    assert account is not None
    assert account["steam_id"] == "76561198000000001"
    assert account["name"] == "Player One"
    assert account["most_recent"] == 2


def test_read_local_account_missing_file(tmp_path) -> None:
    from plugin.plugins.steam_manager.steam import read_local_account

    assert read_local_account(str(tmp_path)) is None
    assert read_local_account(None) is None


def test_final_fen_conversion() -> None:
    from plugin.plugins.steam_manager.steam import _final_fen

    assert _final_fen({"final": 4999}) == 4999
    assert _final_fen({"final": None}) is None
    assert _final_fen(None) is None
    assert _final_fen({"final": "abc"}) is None


def test_open_app_invalid_appid() -> None:
    from plugin.plugins.steam_manager.steam import open_app

    result = open_app("C:\\Steam", "not-a-number", False)
    assert result["ok"] is False
    assert result["error"] == "INVALID_APP_ID"


def test_open_app_installed_no_steam() -> None:
    from plugin.plugins.steam_manager.steam import open_app

    result = open_app(None, "730", True)
    assert result["ok"] is False
    assert result["error"] == "STEAM_NOT_FOUND"


def test_open_app_not_installed_opens_store(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    opened: list[str] = []
    monkeypatch.setattr(steam, "webbrowser", type("W", (), {"open": staticmethod(lambda u: opened.append(u))})())
    result = steam.open_app(None, "730", False)
    assert result["ok"] is True
    assert "商店页" in result["output"]
    assert len(opened) == 1
    assert "steam://openurl/https://store.steampowered.com/app/730" in opened[0]

# ══════════ v0.3.1：复查修复回归测试 ══════════


def test_scan_games_limit_zero_returns_all(tmp_path) -> None:
    """scan_games(limit=0) 必须返回全部（此前 limit=0 时首条即返回截断）。"""
    from plugin.plugins.steam_manager.steam import scan_games

    steam_path = _make_fake_library(tmp_path, {"730": "A", "570": "B", "10": "C"})
    games = scan_games(steam_path, limit=0)
    assert len(games) == 3


def test_find_game_filters_before_truncating(tmp_path) -> None:
    """库超 limit 时，关键词匹配项不再被截掉（先全扫后过滤再截断）。"""
    from plugin.plugins.steam_manager.steam import find_game

    steam_path = _make_fake_library(tmp_path, {"730": "Game A", "570": "Game B", "10": "Target Hit", "20": "D", "30": "E"})
    # 关键词在库内且匹配项位于扫描较后位置；limit=3 不应截掉它
    matched = find_game(steam_path, "target", limit=3)
    assert len(matched) == 1
    assert matched[0]["name"] == "Target Hit"


def test_friend_list_batches_over_100(monkeypatch) -> None:
    """好友 >100 时 GetPlayerSummaries 必须分批（每次 ≤100），不再单次超限。"""
    from plugin.plugins.steam_manager import steam

    fake_ids = [f"7656119{10000000 + i}" for i in range(150)]
    calls: list[str] = []

    def fake_get(url, referer=None, timeout=15.0):
        calls.append(url)
        if "GetFriendList" in url:
            return {"friendslist": {"friends": [{"steamid": sid} for sid in fake_ids]}}
        # GetPlayerSummaries：返回本批 id 的概要
        start = url.index("steamids=") + len("steamids=")
        ids_part = url[start:]
        import urllib.parse as up
        ids = up.unquote(ids_part).split(",")
        return {"response": {"players": [{"steamid": sid, "personaname": f"P{sid[-4:]}"} for sid in ids]}}

    monkeypatch.setattr(steam, "_http_get_json", fake_get)
    friends = steam.friend_list("key", "76561190000000000")
    assert len(friends) == 150
    assert len(calls) == 3  # 1 次好友列表 + 2 次概要（100+50 分批）
    assert all("steamids=" in c for c in calls[1:])
    # 每批不得超过 100 个 steamid
    for c in calls[1:]:
        ids_part = c[c.index("steamids=") + len("steamids="):]
        import urllib.parse as up
        assert len(up.unquote(ids_part).split(",")) <= 100
    assert all(f["name"].startswith("P") for f in friends)

# ══════════ v0.3.2：凭证自动配置（保存/验证） ══════════


def test_save_api_key_and_cookie(tmp_path) -> None:
    from plugin.plugins.steam_manager import steam

    key_path = steam.save_api_key("ABCDEF012345", path=str(tmp_path / "steam_api_key.txt"))
    cookie_path = steam.save_community_cookie("sessionid=x; steamLoginSecure=y", path=str(tmp_path / "steam_cookie.txt"))
    assert key_path.endswith("steam_api_key.txt")
    assert cookie_path.endswith("steam_cookie.txt")
    assert open(key_path, encoding="utf-8").read().strip() == "ABCDEF012345"
    assert "sessionid=x" in open(cookie_path, encoding="utf-8").read()


def test_save_api_key_empty_raises(tmp_path) -> None:
    from plugin.plugins.steam_manager import steam

    try:
        steam.save_api_key("  ", path=str(tmp_path / "k.txt"))
        raise AssertionError("应当抛 ValueError")
    except ValueError:
        pass


def test_validate_api_key_ok(monkeypatch) -> None:
    """成功响应无 code 字段（players 非空即有效）——实网确认成功响应结构。"""
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "_http_get_json", lambda url: {"response": {"players": [{"steamid": "76561197960287930"}]}})
    ok, msg = steam.validate_api_key("KEY123")
    assert ok is True
    assert "有效" in msg


def test_validate_api_key_invalid(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "_http_get_json", lambda url: {"code": 101, "response": None})
    ok, msg = steam.validate_api_key("BAD")
    assert ok is False
    assert "InvalidKey" in msg


def test_validate_api_key_forbidden(monkeypatch) -> None:
    """无效 Key 实测返回 HTTP 403（而非 code=101），归类为无效。"""
    from plugin.plugins.steam_manager import steam

    def _raise(_url):
        raise OSError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(steam, "_http_get_json", _raise)
    ok, msg = steam.validate_api_key("BAD")
    assert ok is False
    assert "403" in msg


def test_validate_community_cookie_ok(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "_probe_cookie_page", lambda cookie: ("https://steamcommunity.com/profiles/76561198", '<div class="personaName">x</div>'))
    ok, msg = steam.validate_community_cookie("sessionid=abc; steamLoginSecure=xyz")
    assert ok is True
    assert "有效" in msg


def test_validate_community_cookie_missing_session(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    ok, msg = steam.validate_community_cookie("steamLoginSecure=xyz")
    assert ok is False
    assert "sessionid" in msg


def test_validate_community_cookie_stale(monkeypatch) -> None:
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "_probe_cookie_page", lambda cookie: ("https://steamcommunity.com/login", "<html>login</html>"))
    ok, msg = steam.validate_community_cookie("sessionid=abc; steamLoginSecure=xyz")
    assert ok is False
    assert "无效或已过期" in msg


def test_credentials_status() -> None:
    from plugin.plugins.steam_manager import steam

    status = steam.credentials_status()
    assert set(status) == {"api_key_configured", "cookie_configured", "can_use_friends", "can_reply_friend"}
    assert status["can_use_friends"] == status["api_key_configured"]
    assert status["can_reply_friend"] == (status["api_key_configured"] and status["cookie_configured"])

# ══════════ v0.3.2：浏览器 Cookie 自动探测 ══════════


def test_decrypt_cookie_value_v10_ok(monkeypatch) -> None:
    """v10 加密值可被 AES-GCM 解密；非 v10 原样返回。"""
    import os
    import sys

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from plugin.plugins.steam_manager import steam

    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    ct = AESGCM(aes_key).encrypt(nonce, b"sessionvalue123", None)
    encrypted = b"v10" + nonce + ct
    assert steam._decrypt_cookie_value(encrypted, aes_key) == "sessionvalue123"
    assert steam._decrypt_cookie_value(b"plain-text", aes_key) == "plain-text"


def test_probe_browser_cookies_no_browser_db(monkeypatch, tmp_path) -> None:
    """无浏览器 Cookie 库时返回 found=False 且不抛错。"""
    from plugin.plugins.steam_manager import steam

    monkeypatch.setattr(steam, "_iter_browser_cookie_dbs", lambda: [])
    result = steam.probe_browser_cookies()
    assert result["found"] is False
    assert result["sessionid"] == ""


def test_probe_browser_cookies_finds_session(monkeypatch, tmp_path) -> None:
    """探测到 sessionid 时正确返回（不依赖真实浏览器）。"""
    from plugin.plugins.steam_manager import steam

    rows = [("sessionid", "", b"v10"), ("steamLoginSecure", "", b"v10")]
    monkeypatch.setattr(steam, "_iter_browser_cookie_dbs", lambda: [("fake.db", "fake.json")])

    import os
    import sqlite3

    db = os.path.join(str(tmp_path), "cookies.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cookies (name TEXT, value TEXT, encrypted_value BLOB, host_key TEXT)")
    for n, v, e in rows:
        con.execute("INSERT INTO cookies VALUES (?,?,?,?)", (n, v, e, "steamcommunity.com"))
    con.commit()
    con.close()

    def _fake_copy(src, dst):
        import shutil

        shutil.copyfile(db, dst)

    monkeypatch.setattr(steam, "_copy_cookie_db", _fake_copy)
    monkeypatch.setattr(steam, "_decrypt_cookie_value", lambda enc, key: b"decoded-value".decode() if enc == b"v10" else "")
    monkeypatch.setattr(steam, "_chrome_aes_key", lambda path: b"k" * 32)
    result = steam.probe_browser_cookies()
    assert result["sessionid"] == "decoded-value"
