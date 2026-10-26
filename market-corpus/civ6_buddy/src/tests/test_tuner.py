"""civ6_tuner 协议与错误码测试（不连真实游戏，CI 可跑）。

覆盖：帧打包/解包、非法帧防御、set_focus 参数校验、
无 Tuner 时 list_cities/tuner_status 返回明确错误码。

注：『无游戏』用例的前提是 4318 没有监听；游戏开着（Tuner 在线）时
这些用例自动跳过（避免误连真实游戏/误改游戏状态）。
"""

import importlib
import socket

import pytest

_tuner = importlib.import_module("civ6_tuner")


def _tuner_listening() -> bool:
    """只读探测：4318（游戏 FireTuner）当前是否在监听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 4318)) == 0


skip_if_game = pytest.mark.skipif(
    _tuner_listening(),
    reason="游戏 Tuner（4318）在监听：『无游戏』用例不成立，跳过以免误连真实游戏",
)


def test_pack_unpack_roundtrip():
    payload = "CMD:0:print('hi')"
    raw = _tuner._pack(_tuner.TAG_COMMAND, payload)
    tag, body = _tuner._unpack(raw)
    assert tag == _tuner.TAG_COMMAND
    assert body == payload


def test_unpack_rejects_short_frame():
    with pytest.raises(_tuner.TunerError):
        _tuner._unpack(b"\x00\x01")


def test_unpack_rejects_bad_length():
    # 声称长度 9999999999（>1e7 上限）
    # 手工构造超长长度帧
    import struct
    buf = struct.pack(_tuner.HEADER_FMT, 99_999_999, _tuner.TAG_COMMAND) + b"\x00"
    with pytest.raises(_tuner.TunerError):
        _tuner._unpack(buf)


@skip_if_game
def test_tuner_status_no_game():
    import asyncio
    r = asyncio.run(_tuner.tuner_status())
    # 未开 Tuner/未装 SDK 时必然连不上（本机当前无 4318 监听）
    assert r["ok"] is False
    assert r["error"] == "GAME_TUNER_OFF"


@skip_if_game
def test_list_cities_no_game():
    import asyncio
    r = asyncio.run(_tuner.list_cities())
    assert r["ok"] is False
    assert r["error"] == "GAME_TUNER_OFF"
    assert r["cities"] == []


@skip_if_game
def test_set_focus_validates_yield():
    import asyncio
    r = asyncio.run(_tuner.set_focus("NOPE"))
    assert r["ok"] is False
    assert r["error"] == "BAD_YIELD"
    r2 = asyncio.run(_tuner.set_focus("production"))
    assert r2["ok"] is False
    assert r2["error"] == "GAME_TUNER_OFF"  # 参数合法但无游戏


def test_lua_scripts_contain_guards():
    # 脚本含本地玩家守卫，防未进局误执行
    assert "ERR:NO_PLAYER" in _tuner.LUA_LIST_CITIES
    assert "ERR:NO_PLAYER" in _tuner.LUA_SET_FOCUS
    assert "YIELD_DEFAULT" in _tuner.LUA_SET_FOCUS


@skip_if_game
def test_at_war_no_game():
    import asyncio
    r = asyncio.run(_tuner.at_war())
    # 未开 Tuner 时必然连不上
    assert r["ok"] is False
    assert r["error"] == "GAME_TUNER_OFF"


def test_at_war_lua_guard():
    # 战争脚本含本地玩家守卫 + 逐玩家 IsAtWarWith 战争计数
    assert "ERR:NO_PLAYER" in _tuner.LUA_AT_WAR
    assert "IsAtWarWith" in _tuner.LUA_AT_WAR
    assert "at_war=" in _tuner.LUA_AT_WAR
