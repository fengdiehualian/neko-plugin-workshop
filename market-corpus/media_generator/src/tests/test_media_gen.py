"""media_generator 单元测试：纯函数 + 未配置 Key 的错误分支（不触发真实网络请求）。

注意：与 card_master 测试一致，模块在函数内导入，避免 isort first-party 判定在本地/CI 间分歧。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import pytest

_KEYS = [
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_IMAGE_MODEL",
    "DOUBAO_ARK_API_KEY",
    "DOUBAO_ARK_ENDPOINT",
    "KLING_API_KEY",
    "KLING_SECRET_KEY",
]


@pytest.fixture(autouse=True)
def _clean_keys(monkeypatch):
    """确保测试环境无任何媒体 API Key，错误分支可确定。"""
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ══════════ 纯函数 ══════════


def test_normalize_size():
    from plugin.plugins.media_generator import providers

    assert providers.normalize_size("") == "1024x1024"
    assert providers.normalize_size("512x512") == "512x512"
    assert providers.normalize_size("768x1024") == "768x1024"
    assert providers.normalize_size("1024×768") == "1024x768"
    assert providers.normalize_size("999x999") == "1024x1024"  # 非法回退默认


def test_list_configured_providers_empty():
    from plugin.plugins.media_generator import providers

    assert providers.list_configured_providers() == []


def test_build_kling_jwt_structure():
    from plugin.plugins.media_generator import providers

    token = providers.build_kling_jwt("ak_test", "sk_test")
    parts = token.split(".")
    assert len(parts) == 3

    header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    assert header["alg"] == "HS256"
    assert payload["iss"] == "ak_test"
    assert payload["exp"] > payload["nbf"]

    # 用同一密钥重算签名，验证签名正确
    signing_input = f"{parts[0]}.{parts[1]}"
    expect = base64.urlsafe_b64encode(
        hmac.new(b"sk_test", signing_input.encode("ascii"), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    assert parts[2] == expect


def test_fmt_url():
    from plugin.plugins.media_generator import providers

    assert providers._fmt_url('  "https://x.com/a.png"  ') == "https://x.com/a.png"


# ══════════ 未配置 Key 的错误分支 ══════════


def test_dashscope_image_no_key():
    from plugin.plugins.media_generator import providers

    result = _run(providers.generate_image_dashscope("一只猫"))
    assert result["is_error"] is True
    assert result["error"] == "NO_DASHSCOPE_KEY"


def test_openai_image_no_key():
    from plugin.plugins.media_generator import providers

    result = _run(providers.generate_image_openai("一只猫"))
    assert result["is_error"] is True
    assert result["error"] == "NO_OPENAI_KEY"


def test_doubao_video_no_key():
    from plugin.plugins.media_generator import providers

    result = _run(providers.generate_video_doubao("一只猫"))
    assert result["is_error"] is True
    assert result["error"] == "NO_DOUBAO_ARK"


def test_kling_video_no_key():
    from plugin.plugins.media_generator import providers

    result = _run(providers.generate_video_kling("一只猫"))
    assert result["is_error"] is True
    assert result["error"] == "NO_KLING_KEY"


# ══════════ 插件工具层 ══════════


def _plugin():
    from plugin.plugins.media_generator import MediaGeneratorPlugin

    inst = MediaGeneratorPlugin.__new__(MediaGeneratorPlugin)
    inst._save_dir = None
    return inst


def test_generate_image_no_prompt():
    inst = _plugin()
    result = _run(inst.generate_image(prompt="", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROMPT"


def test_generate_image_no_provider():
    inst = _plugin()
    result = _run(inst.generate_image(prompt="一只猫", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROVIDER"


def test_generate_video_no_prompt():
    inst = _plugin()
    result = _run(inst.generate_video(prompt="", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROMPT"


def test_generate_video_no_provider():
    inst = _plugin()
    result = _run(inst.generate_video(prompt="一只猫", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_VIDEO_PROVIDER"


def test_unknown_provider_rejected():
    """配置了 key 时传未知 provider → UNKNOWN_PROVIDER（不发网络请求）。"""
    import os

    os.environ["DASHSCOPE_API_KEY"] = "fake-key-for-branch-test"
    os.environ["DOUBAO_ARK_API_KEY"] = "fake-ark"
    os.environ["DOUBAO_ARK_ENDPOINT"] = "fake-endpoint"
    try:
        inst = _plugin()
        result = _run(inst.generate_image(prompt="一只猫", provider="foo", parameters={}))
        assert result["is_error"] is True
        assert result["error"] == "UNKNOWN_PROVIDER"

        inst2 = _plugin()
        result2 = _run(inst2.generate_video(prompt="一只猫", provider="bar", parameters={}))
        assert result2["is_error"] is True
        assert result2["error"] == "UNKNOWN_PROVIDER"
    finally:
        os.environ.pop("DASHSCOPE_API_KEY", None)
        os.environ.pop("DOUBAO_ARK_API_KEY", None)
        os.environ.pop("DOUBAO_ARK_ENDPOINT", None)


# ══════════ v0.2.0：自动路由入口 ══════════


def test_entries_registered():
    """注册的 plugin_entry 入口（run / media_gen）必须可被宿主收集（入口点 > 0）。"""
    from plugin.plugins.media_generator import MediaGeneratorPlugin

    entries = {}
    for name in dir(MediaGeneratorPlugin):
        if name.startswith("_"):
            continue
        attr = getattr(MediaGeneratorPlugin, name, None)
        meta = getattr(attr, "__neko_event_meta__", None)
        if meta is not None and getattr(meta, "event_type", None) == "plugin_entry":
            entries[meta.id] = meta
    assert "run" in entries, f"缺少通用入口 run，现有：{sorted(entries)}"
    assert "media_gen" in entries, f"缺少专用入口 media_gen，现有：{sorted(entries)}"
    assert entries["run"].name == "媒体生成器"


def test_entry_run_image_no_key():
    """入口 run：action=generate_image、无 Key → NO_PROVIDER（可路由、可诊断）。"""
    inst = _plugin()
    result = _run(inst.entry_run(action="generate_image", prompt="一只猫", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROVIDER"


def test_entry_run_video_no_key():
    """入口 run：action=generate_video、无 Key → NO_VIDEO_PROVIDER。"""
    inst = _plugin()
    result = _run(inst.entry_run(action="generate_video", prompt="一只猫", duration=5, parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_VIDEO_PROVIDER"


def test_entry_run_default_is_image():
    """入口 run：无 action 时默认按文生图处理（视频特征参数缺省 → image）。"""
    inst = _plugin()
    result = _run(inst.entry_run(prompt="一只猫", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROVIDER"  # 走了图片分支


def test_entry_run_duration_implies_video():
    """入口 run：给了 duration 但无 action → 自动判断为视频。"""
    inst = _plugin()
    result = _run(inst.entry_run(prompt="一只猫", duration=10, parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_VIDEO_PROVIDER"


def test_entry_run_no_prompt():
    inst = _plugin()
    result = _run(inst.entry_run(prompt="", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROMPT"


def test_entry_run_unknown_action():
    inst = _plugin()
    result = _run(inst.entry_run(action="make_3d", prompt="一只猫", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "UNKNOWN_ACTION"


def test_entry_media_gen_forwards():
    """入口 media_gen：kind=image 转发到图片分支（无 Key → NO_PROVIDER）。"""
    inst = _plugin()
    result = _run(inst.entry_media_gen(prompt="一只猫", kind="image", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROVIDER"


def test_entry_media_gen_no_kind_default_image():
    """入口 media_gen：无 kind、无视频特征 → 默认图片。"""
    inst = _plugin()
    result = _run(inst.entry_media_gen(prompt="一只猫", parameters={}))
    assert result["is_error"] is True
    assert result["error"] == "NO_PROVIDER"
