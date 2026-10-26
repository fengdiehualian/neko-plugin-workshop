"""game_event / speak 官方猫娘通道的行为测试（本地无 SDK，走兜底壳）。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    root = Path(__file__).resolve().parents[1]
    # 目录名带前缀不能直接 import，按文件路径加载并注册进 sys.modules
    spec = importlib.util.spec_from_file_location("neko_survivors_pkg", root / "__init__.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["neko_survivors_pkg"] = m
    spec.loader.exec_module(m)
    return m


_MOD = _load_module()
_safe_rel = _MOD._safe_rel


def _load_plugin_class():
    return _MOD.SurvivorsPlugin


def _make():
    log: list = []
    cls = _load_plugin_class()
    p = object.__new__(cls)
    p._last_event_at = {}
    p._last_any_at = 0.0
    p._last_speak_at = 0.0
    p._lanlan_cache = (0.0, "")
    p._sync_task = None

    def fake_push(**kwargs):
        log.append(kwargs)

    p.push_message = fake_push  # 实例属性遮蔽类方法
    return p, log


def test_game_event_pushes_respond_message():
    p, log = _make()
    r = asyncio.run(p.game_event(event="boss_spawn", text="第 10 波，Boss 登场"))
    assert getattr(r, "value", None) == {"status": "queued"}   # 真实 SDK Ok.value
    assert len(log) == 1
    kw = log[0]
    assert kw["visibility"] == ["chat"]
    assert kw["ai_behavior"] == "respond"        # 官方猫娘会真的开口（自带形象+TTS）
    assert kw["source"] == "neko_survivors"
    assert "第 10 波" in kw["parts"][0]["text"]


def test_throttle_same_event_and_global_gap():
    p, log = _make()
    asyncio.run(p.game_event(event="boss_spawn", text="Boss 登场"))
    r = asyncio.run(p.game_event(event="boss_spawn", text="又来"))
    assert (getattr(r, "value", None) or {}).get("status") == "throttled"
    r2 = asyncio.run(p.game_event(event="boss_kill", text="打倒了"))  # 全局间隔未到
    assert (getattr(r2, "value", None) or {}).get("status") == "throttled"
    assert len(log) == 1


def test_empty_text_rejected():
    p, log = _make()
    r = asyncio.run(p.game_event(event="win", text="   "))
    assert not hasattr(r, "value") or getattr(r, "value", None) is None  # Err 分支
    assert len(log) == 0


def test_speak_forwards_to_host_tts():
    p, _log = _make()
    calls: list = []

    def fake_req(method, path, body=None, timeout=15.0):
        calls.append((method, path, body))
        if path == "/api/characters":
            return 200, '{"当前猫娘":"YUI"}'.encode("utf-8")
        if path.endswith("/speak"):
            return 200, b'{"ok":true,"method":"project_tts"}'
        return 404, b""

    p._http_request = fake_req
    r = asyncio.run(p.speak(text="干得漂亮，喵。"))
    assert getattr(r, "value", {}).get("status") == "spoken"
    method, path, body = calls[-1]
    assert method == "POST" and path.endswith("/api/game/neko_survivors/speak")
    payload = body.decode("utf-8")
    assert '"line"' in payload and "干得漂亮" in payload   # 照念台词原样转发
    assert "YUI" in payload                                 # lanlan_name 自动带


def test_speak_no_throttle_anymore():
    """第一百二十八轮：speak 无 CD —— 连发三句全部转发（官方 TTS 免费）。"""
    p, _log = _make()
    sent: list = []

    def fake_req(method, path, body=None, timeout=15.0):
        if path == "/api/characters":
            return 200, '{"当前猫娘":"YUI"}'.encode("utf-8")
        if path.endswith("/speak"):
            sent.append(json.loads(body.decode("utf-8"))["line"])
            return 200, b'{"ok":true,"method":"project_tts"}'
        return 404, b""

    p._http_request = fake_req
    for line in ("第一句", "第二句", "第三句"):
        r = asyncio.run(p.speak(text=line))
        assert getattr(r, "value", {}).get("status") == "spoken"
    assert sent == ["第一句", "第二句", "第三句"]


def test_sync_avatar_pins_and_model_refs():
    root = Path(__file__).resolve().parents[1]
    init = (root / "__init__.py").read_text(encoding="utf-8")
    assert '@lifecycle(id="startup")' in init            # 启动自动同步
    assert '"/api/game/{GAME_TYPE}/speak"' in init       # 官方 TTS 转发
    assert 'current.json' in init                        # 前端模型清单
    assert "_safe_rel" in init                           # 路径穿越防护
    p, _log = _make()
    refs = p._model_refs({"FileReferences": {
        "Moc": "a.moc3", "Textures": ["t1.png", "t2.png"], "Physics": "p.json",
        "Expressions": [{"File": "e.exp3.json"}],
        "Motions": {"Idle": [{"File": "m.motion3.json", "Sound": "s.wav"}]},
    }})
    assert refs == ["a.moc3", "p.json", "t1.png", "t2.png",
                    "e.exp3.json", "m.motion3.json", "s.wav"]
    with pytest.raises(ValueError):
        _safe_rel("../evil.png")
    with pytest.raises(ValueError):
        _safe_rel("a/../../b.png")


def test_manifest_and_bridge_pins():
    root = Path(__file__).resolve().parents[1]
    toml = (root / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "neko_survivors"' in toml
    assert 'entry = "plugin.plugins.neko_survivors:SurvivorsPlugin"' in toml
    init = (root / "__init__.py").read_text(encoding="utf-8")
    assert 'id="game_event"' in init
    assert 'ai_behavior="respond"' in init
    assert '@ui.action(id="game_event"' in init       # action 白名单（没它面板 403）
    assert 'permissions = ["action:call"]' in toml    # surface 允许调 action
    bridge = (root / "static" / "game" / "src" / "plugin-bridge.js").read_text(encoding="utf-8")
    assert "hosted-ui/action/" in bridge           # 同源 action 通道
    assert "deriveBase" in bridge or "candidateBases" in bridge  # 基址运行时推导（防前缀丢失；176轮重构改名）
    assert "new URL(path, location.origin)" in bridge  # URL 绝对化
    assert "fetch('/static" not in bridge          # 不再跨域读静态模型资源
