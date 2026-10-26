"""call_mom 冒烟测试（无 SDK 依赖，纯静态检查）。

不 import plugin.sdk——测试可能在宿主环境外运行。
运行：uv run pytest plugin/plugins/call_mom/tests/test_smoke.py -q
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def test_manifest_required_fields():
    manifest = tomllib.loads((PLUGIN_DIR / "plugin.toml").read_text(encoding="utf-8"))
    plugin = manifest["plugin"]
    for field in ("id", "name", "version", "entry"):
        assert plugin.get(field), f"plugin.toml 缺少必填字段: {field}"
    assert plugin["id"] == PLUGIN_DIR.name, "文件夹名必须与插件 id 一致"
    assert "call_mom:CallMomPlugin" in plugin["entry"], "entry 应指向 CallMomPlugin"
    assert "supported" in manifest.get("plugin", {}).get("sdk", {}), "缺少 [plugin.sdk].supported"


def test_runtime_config_section():
    config = tomllib.loads(
        (PLUGIN_DIR / "config.example.toml").read_text(encoding="utf-8")
    )
    section = config["call_mom"]
    assert isinstance(section.get("prefix"), str) and section["prefix"].strip()


def test_entry_code_declares_contract():
    code = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "@neko_plugin" in code
    assert "NekoPluginBase" in code
    for decorator in ("@plugin_entry", "@lifecycle", "@timer_interval"):
        assert decorator in code, f"缺少装饰器: {decorator}"
    # 主通道：规则以 LLM 工具描述常驻每轮上下文
    assert "register_llm_tool" in code, "缺少规则工具注册"
    assert "call_mom_rule" in code
    # 辅助通道：respond 注入（当前会话立即生效）+ 用户不可见
    assert 'ai_behavior="respond"' in code
    assert "visibility=[]" in code
    # 巡检：main_server 注册表丢失时补注册
    assert "/api/tools" in code


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
