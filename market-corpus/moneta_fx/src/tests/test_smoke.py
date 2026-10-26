# isort: skip_file
"""冒烟测试：插件清单、入口注册与 Hosted TSX 面板文件。

测试以平铺方式导入被测模块（from engine import ...）。CI 把插件目录拷进
N.E.K.O. 树后没有 .git，ruff 无法判定这些是首方模块，会和 pytest 混成一组；
这里的 isort pragma 让导入分组保持人工排好的样子。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from engine import TradeError

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (PLUGIN_ROOT / relative).read_text(encoding="utf-8")


def test_plugin_toml_declares_identity():
    text = read("plugin.toml")
    assert 'id = "moneta_fx"' in text
    assert 'name = "MONETA FX · 外汇模拟交易室"' in text
    assert 'entry = "plugin.plugins.moneta_fx.plugin:MonetaFxPlugin"' in text
    assert 'version = "1.0.5"' in text


def test_plugin_toml_declares_ui_and_i18n():
    text = read("plugin.toml")
    assert "[plugin.ui]" in text
    assert 'entry = "ui/panel.tsx"' in text
    assert 'context = "terminal"' in text
    assert "[plugin.i18n]" in text
    assert 'default_locale = "zh-CN"' in text
    assert "[plugin.store]" in text


def test_entry_module_matches_manifest():
    text = read("__init__.py")
    assert "MonetaFxPlugin" in text


def test_required_files_exist():
    for relative in (
        "plugin.toml",
        "__init__.py",
        "config.example.toml",
        "config.schema.json",
        "pyproject.toml",
        "ruff.toml",
        "README.md",
        "LICENSE",
        "ui/panel.tsx",
        "docs/quickstart.md",
        "i18n/zh-CN.json",
        "i18n/en.json",
        ".github/workflows/verify.yml",
        ".github/workflows/release.yml",
    ):
        assert (PLUGIN_ROOT / relative).is_file(), f"缺少 {relative}"


def test_workflows_target_this_plugin_id():
    for workflow in ("verify.yml", "release.yml"):
        text = read(f".github/workflows/{workflow}")
        assert "plugin-id: moneta_fx" in text


def test_i18n_files_are_valid_json_and_share_keys():
    zh = json.loads(read("i18n/zh-CN.json"))
    en = json.loads(read("i18n/en.json"))
    assert zh and en
    missing = set(zh) - set(en)
    assert not missing, f"en.json 缺少键: {sorted(missing)[:5]}"


def test_panel_exports_a_default_component():
    text = read("ui/panel.tsx")
    assert "export default function" in text


def test_panel_imports_only_supported_modules():
    text = read("ui/panel.tsx")
    for match in re.finditer(r'^import\s+(?:type\s+)?.*?from\s+"([^"]+)"', text, re.MULTILINE):
        specifier = match.group(1)
        assert specifier.startswith(".") or specifier == "@neko/plugin-ui", f"不支持的导入: {specifier}"


def test_panel_uses_no_dynamic_import():
    assert "import(" not in read("ui/panel.tsx")


def test_config_schema_is_valid_json():
    schema = json.loads(read("config.schema.json"))
    assert schema["type"] == "object"
    assert "moneta" in schema["properties"]


def test_config_schema_declares_every_moneta_key():
    """[moneta] 里的每个键都必须在 config.schema.json 里声明。

    宿主用 JSON Schema 校验配置，schema 未声明的键会让插件在列表里
    显示"config could not be parsed or validated"。
    """

    manifest = tomllib.loads(read("plugin.toml"))
    example = tomllib.loads(read("config.example.toml"))
    schema = json.loads(read("config.schema.json"))

    declared = set(schema["properties"]["moneta"]["properties"])
    for source, config in (("plugin.toml", manifest), ("config.example.toml", example)):
        actual = set(config.get("moneta", {}))
        missing = actual - declared
        assert not missing, f"{source} 的 [moneta] 有 schema 未声明的键: {sorted(missing)}"


def test_plugin_toml_defaults_to_the_cloud_backend():
    """清单里的 [moneta] 默认连云端，装上去就能用服务端账户。

    改成空 server_url 会让插件退回本地模式，用户看到的账户和浏览器端
    不是同一份——这个默认值是有意为之。
    """

    moneta = tomllib.loads(read("plugin.toml"))["moneta"]
    assert moneta["use_cloud"] is True, "默认应为云端模式"
    assert moneta["server_url"].startswith("http"), "server_url 必须是完整地址"


def test_sdk_range_contains_the_host_version():
    """[plugin.sdk] 的 supported 必须覆盖宿主的 SDK_VERSION。

    宿主的 SDK_VERSION 目前是 0.1.0（plugin/sdk/shared/constants.py）。
    文档里的 "SDK v2" 指 API 代次而不是版本号，写成 >=2.0.0 会让
    _check_sdk_compatibility 直接判不兼容，插件根本加载不了。
    """

    HOST_SDK_VERSION = "0.1.0"

    sdk = tomllib.loads(read("plugin.toml"))["plugin"]["sdk"]
    for key in ("recommended", "supported"):
        spec = sdk.get(key)
        assert spec, f"plugin.sdk.{key} 未声明"
        assert Version(HOST_SDK_VERSION) in SpecifierSet(spec), (
            f"plugin.sdk.{key} = {spec!r} 不包含宿主 SDK 版本 {HOST_SDK_VERSION}"
        )


class _Ctx:
    """最小插件上下文，供生命周期与云端模式测试使用。"""

    logger = None


def _make_plugin():
    """按文件路径加载插件模块。

    不能写 ``from plugin import MonetaFxPlugin``——tests/conftest.py 装的
    SDK stub 占用 "plugin" 这个名字；也不能 ``import plugin_module``，
    因为插件文件名就叫 plugin.py，平铺导入时装的是 stub 那一个。
    """

    spec = importlib.util.spec_from_file_location("moneta_plugin_under_test", PLUGIN_ROOT / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MonetaFxPlugin(_Ctx())


def test_cloud_mode_startup_skips_local_runtime():
    """云端可达时 on_startup 只建 CloudClient，不建本地 MarketRuntime。

    这是曾经崩过的路径：面板随后调用 _bind()，而 _bind 无脑访问
    self.runtime.world，_runtime 是 None 就抛"插件尚未启动"。
    """

    plugin = _make_plugin()
    plugin._moneta_settings["server_url"] = "http://moneta-test.invalid:4173"
    plugin._moneta_settings["use_cloud"] = True

    # ping 必然失败（域名不存在），应当回退本地模式并正常建 runtime。
    result = asyncio.run(plugin.on_startup())
    assert result.value["status"] == "ready"
    assert result.value["mode"] == "local"
    assert plugin._runtime is not None


def test_bind_works_in_cloud_mode_without_local_runtime():
    """云端模式下 _bind 不能碰 self.runtime。"""

    plugin = _make_plugin()
    plugin._moneta_settings["server_url"] = "http://moneta-test.invalid:4173"
    plugin._moneta_settings["use_cloud"] = True
    plugin._cloud = object()  # 假装已连接，_cloud_enabled() 只看配置

    # 没建本地 runtime，_bind 必须能用。
    session = plugin._bind("panel-1", "user-1")
    assert session.user_id == "user-1"
    assert session.world is None
    # Session.user 要给出可读的错误，而不是 AttributeError。
    with pytest.raises(TradeError):
        _ = session.user


def test_getmembers_does_not_crash_before_startup():
    """宿主要求：startup 之前 inspect.getmembers 必须能跑完。

    宿主的 ``_rebuild_ui_context_map`` 在 startup 之前用
    ``inspect.getmembers(instance, predicate=callable)`` 枚举插件成员，
    会读取每个 property 的值。任何 property 在这里抛异常都会让整个
    插件进程崩掉——曾经 runtime property 抛 SdkError，开机即崩。
    """

    import inspect

    plugin = _make_plugin()

    # 未 startup：_runtime 是 None，getmembers 也必须安全返回。
    members = inspect.getmembers(plugin, predicate=callable)
    assert members, "至少应收集到插件的 entry 方法"

    # 每个 property 单独读取也不能抛。
    for name, prop in inspect.getmembers(type(plugin), lambda o: isinstance(o, property)):
        try:
            getattr(plugin, name)
        except Exception as exc:  # pragma: no cover - 失败时给出明确信息
            pytest.fail(f"property {name!r} 在 startup 前读取时抛了 {type(exc).__name__}: {exc}")


def test_require_runtime_gives_readable_error():
    """_require_runtime() 才负责报错，且消息可读。"""

    from plugin.sdk.plugin import SdkError

    plugin = _make_plugin()
    with pytest.raises(SdkError, match="本地模式尚未启动"):
        plugin._require_runtime()

    # startup 之后就能拿到 runtime。
    asyncio.run(plugin.on_startup())
    assert plugin._require_runtime() is not None


# 面板 ui/panel.tsx 通过 call(id) 调用的动作，缺一个面板就报"xxx 动作不可用"。
PANEL_ACTIONS = {
    "state", "estimate", "order", "close", "close_all", "cancel", "modify",
    "developer_login", "register", "login", "logout", "control", "report", "settings",
}


def _ui_action_ids(plugin) -> set:
    """按宿主的识别逻辑收集带 __neko_ui_action__ 元数据的 entry id。"""

    ids = set()
    for name in dir(plugin):
        try:
            member = getattr(plugin, name)
        except Exception:
            continue
        if not callable(member):
            continue
        for candidate in (member, getattr(member, "__func__", None)):
            meta = getattr(candidate, "__neko_ui_action__", None) if candidate else None
            if isinstance(meta, dict):
                ids.add(str(meta.get("id")))
                break
    return ids


def test_panel_actions_are_declared():
    """面板用到的每个动作都必须有 @ui.action，否则 props.actions 里没有它。

    宿主的 _collect_ui_actions() 只收带 __neko_ui_action__ 属性的 entry，
    漏加一个，前端 call(id) 就抛"xxx 动作不可用"。
    """

    plugin = _make_plugin()
    declared = _ui_action_ids(plugin)
    missing = PANEL_ACTIONS - declared
    assert not missing, f"面板需要但未声明 @ui.action 的动作: {sorted(missing)}"


def test_ui_action_metadata_is_complete():
    """每个 @ui.action 都要有 label/group，宿主按它们排序和分组。"""

    plugin = _make_plugin()
    for name in dir(plugin):
        try:
            member = getattr(plugin, name)
        except Exception:
            continue
        if not callable(member):
            continue
        for candidate in (member, getattr(member, "__func__", None)):
            meta = getattr(candidate, "__neko_ui_action__", None) if candidate else None
            if isinstance(meta, dict):
                assert meta.get("label"), f"{name} 的 @ui.action 缺 label"
                assert meta.get("group"), f"{name} 的 @ui.action 缺 group"
                assert isinstance(meta.get("order"), int), f"{name} 的 @ui.action order 必须是整数"
                break
