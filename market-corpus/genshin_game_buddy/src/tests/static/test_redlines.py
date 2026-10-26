"""E 节红线回归：对插件自有源码 / 清单做静态扫描。

两条方法论：
  1. **只扫会被执行的代码**——用 ``ast.unparse`` 去掉注释与 docstring 后再正则。
     否则"本插件不涉及 extension / [plugin.host]"这类说明文字会把红线自己点红。
  2. 能上 AST 的就上 AST（装饰器、参数互斥、顶层调用），正则只用于"禁用 token"。
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib

import pytest
from conftest import PLUGIN_DIR, PLUGIN_MODULES, read_plugin_source

MANIFEST_TEXT = (PLUGIN_DIR / "plugin.toml").read_text(encoding="utf-8")
DECORATOR_TARGETS = (
    "plugin_entry",
    "llm_tool",
    "lifecycle",
    "message",
    "timer_interval",
    "custom_event",
)
PUSH_ALLOWED_KWARGS = {
    "source",
    "visibility",
    "ai_behavior",
    "parts",
    "priority",
    "metadata",
    "coalesce_key",
}


def _trees() -> dict[str, ast.Module]:
    return {name: ast.parse(read_plugin_source(name)) for name in PLUGIN_MODULES}


def _strip_docstrings(node: ast.AST) -> ast.AST:
    holder_types = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for child in ast.walk(node):
        if not isinstance(child, holder_types):
            continue
        body = list(child.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            child.body = body[1:] or [ast.Pass()]
    return node


def executable_blob() -> str:
    """所有插件模块去掉注释/docstring 后的源码拼接。"""
    chunks = []
    for name in PLUGIN_MODULES:
        tree = _strip_docstrings(ast.parse(read_plugin_source(name)))
        chunks.append(ast.unparse(ast.fix_missing_locations(tree)))
    return "\n".join(chunks)


def calls_to(name: str) -> list[tuple[str, ast.Call]]:
    found = []
    for module, tree in _trees().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if callee == name:
                    found.append((module, node))
    return found


def assert_absent(pattern: str, *, text: str | None = None) -> None:
    haystack = executable_blob() if text is None else text
    assert not re.search(pattern, haystack), f"forbidden pattern: {pattern}"


# =============================================================================
# E-01 / E-02 / E-05 ~ E-08 / E-11 / E-12：被移除或未生效的 API 面
# =============================================================================


@pytest.mark.static
@pytest.mark.parametrize(
    "pattern",
    [
        r"plugin\.sdk\.extension",
        r"NekoExtensionBase",
        r"extension_entry",
        r"'extension'",
        r'"extension"',
    ],
)
def test_redline__no_removed_extension_surface(pattern: str) -> None:
    """KB B1 / C1：extension 包类型与 [plugin.host] 已彻底移除。"""
    assert_absent(pattern)


@pytest.mark.static
def test_redline__manifest_has_no_host_section() -> None:
    """清单里不能真的出现 ``[plugin.host]`` 表。

    刻意不用正则扫全文：plugin.toml 的说明注释里正当地写着"不写 [plugin.host]"，
    扫文本会把注释也算成违规（这正是要把扫描对象限定为结构的原因）。
    """
    manifest = tomllib.loads(MANIFEST_TEXT)
    assert "host" not in manifest.get("plugin", {})
    assert not any(
        line.strip().startswith("[plugin.host]") for line in MANIFEST_TEXT.splitlines()
    )


@pytest.mark.static
@pytest.mark.parametrize(
    "decorator", ["hook", "before_entry", "after_entry", "around_entry", "replace_entry"]
)
def test_redline__no_hook_decorators(decorator: str) -> None:
    """KB B2 / C2：Hook 装饰器只注册元数据、当前不执行。"""
    for _module, tree in _trees().items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for decorator_node in node.decorator_list:
                target = decorator_node.func if isinstance(decorator_node, ast.Call) else decorator_node
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                assert name != decorator, f"{decorator} used at line {node.lineno}"
    assert_absent(rf"\b{decorator}\(")


@pytest.mark.static
def test_redline__no_underscore_entry_methods() -> None:
    """KB B3：``collect_entries`` 跳过 ``_`` 开头的成员，入口会静默消失。"""
    offenders = []
    for module, tree in _trees().items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("_"):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                    if decorator.func.id in DECORATOR_TARGETS:
                        offenders.append(f"{module}:{node.lineno} {node.name}")
    assert offenders == []


@pytest.mark.static
def test_redline__all_entry_and_tool_handlers_are_async() -> None:
    """KB B4：运行时入口必须是 async def。"""
    offenders = []
    for module, tree in _trees().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) or not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name):
                    if decorator.func.id in DECORATOR_TARGETS:
                        offenders.append(f"{module}:{node.lineno} {node.name}")
    assert offenders == []


@pytest.mark.static
def test_redline__no_auto_start_on_message_decorator() -> None:
    """KB B5：``@message`` 的签名不接受 ``auto_start``，传了会 TypeError。"""
    for module, node in calls_to("message"):
        names = {kw.arg for kw in node.keywords}
        assert "auto_start" not in names, f"{module}:{node.lineno}"


@pytest.mark.static
def test_redline__no_mutually_exclusive_decorator_args() -> None:
    """KB B7：``input_schema`` 与 ``params`` 互斥；三个 ``llm_result_*`` 互斥。"""
    for module, tree in _trees().items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            names = {kw.arg for kw in node.keywords}
            assert not ({"input_schema", "params"} <= names), f"{module}:{node.lineno}"
            llm_fields = {"llm_result_fields", "llm_result_model", "fields"}
            assert len(llm_fields & names) <= 1, f"{module}:{node.lineno}"


@pytest.mark.static
@pytest.mark.parametrize("pattern", [r"self\.memory\b", r"MemoryClient", r"query_memory"])
def test_redline__no_removed_memory_surface(pattern: str) -> None:
    """KB D1 / D2：``self.memory`` / ``MemoryClient`` 已删除，``query_memory`` 是弃用占位。"""
    assert_absent(pattern)


@pytest.mark.static
@pytest.mark.parametrize(
    "pattern",
    [
        r"where_in\b",
        r"where_eq\b",
        r"where_contains\b",
        r"where_regex\b",
        r"where_gt\b",
        r"where_ge\b",
        r"where_lt\b",
        r"where_le\b",
        r"get_message_plane_all",
    ],
)
def test_redline__no_removed_bus_helpers(pattern: str) -> None:
    """KB D3 / D4：Bus 的 where_* 与消息平面快路径已移除。"""
    assert_absent(pattern)


@pytest.mark.static
@pytest.mark.parametrize(
    "pattern",
    [
        r"plugin\._types\.result",
        r"'script'",
        r'"script"',
        r"requirements(_monitor)?\.txt",
    ],
)
def test_redline__no_removed_result_module_or_requirements_txt(pattern: str) -> None:
    """KB D6 / D7：``plugin._types.result`` 与 requirements*.txt 都不被接受。"""
    assert_absent(pattern)
    assert not list(PLUGIN_DIR.glob("requirements*.txt"))


@pytest.mark.static
def test_redline__finish_uses_delivery_not_reply() -> None:
    """KB D9 / C7：``finish(reply=...)`` 是弃用别名；本插件干脆不用 ``finish``。"""
    sites = calls_to("finish")
    assert sites == [], [f"{module}:{node.lineno}" for module, node in sites]
    for _module, node in calls_to("finish"):
        assert "reply" not in {kw.arg for kw in node.keywords}


@pytest.mark.static
def test_redline__no_reload_lifecycle() -> None:
    """KB D10：``lifecycle(id="reload")`` 不再被派发。"""
    for module, node in calls_to("lifecycle"):
        ids = [kw.value.value for kw in node.keywords if kw.arg == "id" and isinstance(kw.value, ast.Constant)]
        assert ids != ["reload"], f"{module}:{node.lineno}"


# =============================================================================
# E-09：push_message v2 唯一形式
# =============================================================================


@pytest.mark.static
def test_redline__push_message_v2_only() -> None:
    """KB D5：push_message 只允许 parts + visibility + ai_behavior（+ source/priority/metadata）。"""
    # 4 个调用点（按行号升序）：
    #   _push_context                  —— 全量语境块（period/switch 触发）
    #   _scan_and_activate_query       —— 2.0.59 用户消息命中 → query 术语卡（read）
    #   ocr_perceive scene-prompt-push —— 2.0.61 场景命中 → 推 prompt（read）
    #   _push_proactive                —— 场景切换 respond 提示
    sites = calls_to("push_message")
    assert len(sites) == 4, [f"{module}:{node.lineno}" for module, node in sites]
    module, node = sites[0]
    names = {kw.arg for kw in node.keywords}
    assert names == {"source", "visibility", "ai_behavior", "parts", "priority", "metadata"}, (
        module,
        sorted(names),
    )
    assert names <= PUSH_ALLOWED_KWARGS
    # 位置参数也不允许（v2 全关键字）
    assert node.args == []


@pytest.mark.static
@pytest.mark.parametrize(
    "deprecated", ["message_type", "content", "delivery", "reply", "binary_data", "binary_url", "unsafe", "fast_mode"]
)
def test_redline__no_deprecated_push_message_fields(deprecated: str) -> None:
    for module, node in calls_to("push_message"):
        assert deprecated not in {kw.arg for kw in node.keywords}, f"{module}:{node.lineno} {deprecated}"


# =============================================================================
# E-13 ~ E-18：越界 import、手拼路径、平台段、import 期副作用
# =============================================================================


@pytest.mark.static
@pytest.mark.parametrize(
    "pattern",
    [
        r"plugin\.sdk\.shared",
        r"plugin\.core\b",
        r"plugin\.server\b",
        r"\butils\.\w",
    ],
)
def test_redline__no_sdk_internal_or_host_utils_imports(pattern: str) -> None:
    """KB E-13：只能 import ``plugin.sdk.plugin`` 这一层门面。"""
    assert_absent(pattern)


@pytest.mark.static
def test_redline__imports_stay_on_the_public_facade() -> None:
    """把上一条收紧成正向断言：绝对 import 只允许 ``plugin.sdk.plugin``。"""
    offenders = []
    for module, tree in _trees().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith("plugin") and node.module != "plugin.sdk.plugin":
                    offenders.append(f"{module}:{node.lineno} {node.module}")
    assert offenders == [], offenders


@pytest.mark.static
@pytest.mark.parametrize(
    "pattern",
    [
        r"LOCALAPPDATA",
        r"\bAPPDATA\b",
        r"Path\.home\(",
        r"NEKO_PLUGIN_DATA_DIR",
        r"expanduser\(",
    ],
)
def test_redline__no_hand_rolled_storage_paths(pattern: str) -> None:
    """KB E-14：数据根只能来自 ``data_path()/cache_path()``，不得自拼。"""
    assert_absent(pattern)


@pytest.mark.static
@pytest.mark.parametrize(
    "section",
    ["[plugin.database]", "[plugin_state]", "[plugin.install]", "[plugin.host]", "[adapter]"],
)
def test_redline__no_unused_platform_sections(section: str) -> None:
    """KB W2/W3/W4/C4/C5/C8：本插件不使用这些平台段。"""
    assert not any(line.strip().startswith(section) for line in MANIFEST_TEXT.splitlines())


@pytest.mark.static
def test_redline__no_document_parse_permission() -> None:
    """KB W1 / C3：本插件不需要 ``document:parse``（也不该顺手加上）。"""
    assert_absent(r"document:parse")
    manifest = tomllib.loads(MANIFEST_TEXT)
    permissions = manifest["plugin"]["ui"]["guide"][0]["permissions"]
    allowed = {
        "state:read",
        "config:read",
        "config:write",
        "action:call",
        "document:parse",
        "logs:read",
        "runs:read",
    }
    assert set(permissions) <= allowed
    assert permissions == ["state:read"]  # 权限必须最小


@pytest.mark.static
def test_redline__ui_surfaces_are_declared_and_exist() -> None:
    """A-17 ~ A-21：声明了 surface 就必须 enabled + 文件存在 + id 唯一 + 后缀为 md。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    ui = manifest["plugin"]["ui"]
    assert ui["enabled"] is True
    guides = ui.get("guide", [])
    assert guides
    ids = [guide["id"] for guide in guides]
    assert len(ids) == len(set(ids))
    for guide in guides:
        assert guide["title"].strip()
        entry = PLUGIN_DIR / guide["entry"]
        assert entry.is_file(), entry
        assert entry.suffix == ".md"


@pytest.mark.static
def test_redline__no_undeclared_third_party_imports() -> None:
    """KB D7 相关：第三方依赖必须声明 + vendor。
    拍板 2.0.62：允许 winrt-* 前缀（OCR 引擎依赖，pyproject.toml + sync 管理）。"""
    pyproject = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject["project"]["dependencies"]
    allowed_prefixes = ("winrt-",)
    for dep in deps:
        assert any(dep.startswith(p) for p in allowed_prefixes), (
            f"非白名单依赖：{dep}（仅允许 winrt-* 前缀）"
        )

    stdlib = set(sys.stdlib_module_names)
    allowed_roots = stdlib | {"plugin", "__future__", "winrt"}
    offenders = []
    for module, tree in _trees().items():
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root not in allowed_roots:
                    offenders.append(f"{module}:{node.lineno} {root}")
    assert offenders == [], offenders


@pytest.mark.static
def test_redline__no_import_time_side_effects() -> None:
    """KB R8：``auto_start = false`` 不阻止父进程 import，顶层必须是纯定义。"""
    offenders = []
    for module, tree in _trees().items():
        for statement in tree.body:
            if isinstance(
                statement,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom),
            ):
                continue
            for node in ast.walk(statement):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
                # 唯一允许的顶层调用：re.compile(...)（纯常量折叠，无 IO）
                if callee == "compile" and owner == "re":
                    continue
                offenders.append(f"{module}:{node.lineno} {owner + '.' if owner else ''}{callee}")
    assert offenders == [], offenders


@pytest.mark.static
@pytest.mark.xfail(
    raises=AssertionError,
    strict=True,
    reason=(
        "capture_screen / ocr_screen / ocr_perceive 读取 self.bus.frames 用于屏幕 OCR"
        "（ocr_perceive 现以主动抓屏为主、bus.frames 为辅助信号）"
    ),
)
def test_redline__no_bus_reads_in_source() -> None:
    """KB E-22：不读 ``self.bus``（conversations/frames 是全插件可读的敏感面）。"""
    assert_absent(r"self\.bus\b")
    assert_absent(r"ctx\.bus\b")
    assert_absent(r"\bbus\.(messages|events|lifecycle|conversations|frames|memory)\b")


# =============================================================================
# 清单自身的红线 / 一致性
# =============================================================================


@pytest.mark.static
def test_manifest__identity_is_consistent() -> None:
    """A-01 / A-03 / A-07 / A-08：目录名、id、entry、版本必须自洽。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    plugin = manifest["plugin"]
    assert plugin["id"] == "genshin_game_buddy" == PLUGIN_DIR.name
    assert plugin["type"] == "plugin"
    assert plugin["name"].strip()
    assert plugin["description"].strip()
    assert plugin["short_description"].strip()
    assert re.match(r"^[a-z][a-z0-9_]*$", plugin["id"])  # init 的正则（KB C6）
    assert re.match(r"^[A-Za-z0-9_-]+$", plugin["id"])  # schema 的正则
    assert re.match(r"^\d+\.\d+\.\d+", plugin["version"])
    assert re.match(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$", plugin["entry"])

    pyproject = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == plugin["version"]


@pytest.mark.static
def test_manifest__description_does_not_overclaim() -> None:
    """A-04：描述不得声称"检测游戏 / 自动操作"这类不存在的能力。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    description = manifest["plugin"]["description"]
    for claim in ("自动操作", "检测到你", "自动点击", "自动战斗"):
        assert claim not in description
    assert "不" in description


@pytest.mark.static
def test_manifest__entry_resolves_to_decorated_plugin_class() -> None:
    """A-08 / B6：entry 必须解析到带 ``@neko_plugin`` 的 NekoPluginBase 子类。"""
    from plugin.sdk.plugin import NekoPluginBase
    from plugin.sdk.shared.constants import NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG

    manifest = tomllib.loads(MANIFEST_TEXT)
    module_path, _, class_name = manifest["plugin"]["entry"].partition(":")
    module = __import__(module_path, fromlist=["*"])
    cls = getattr(module, class_name)
    assert issubclass(cls, NekoPluginBase)
    decorated = getattr(cls, NEKO_PLUGIN_META_ATTR, None) or getattr(cls, NEKO_PLUGIN_TAG, None)
    assert decorated is not None


@pytest.mark.static
def test_manifest__keywords_are_valid_regex_fragments() -> None:
    """A-06：keywords 是正则片段，必须都能编译且非空。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    keywords = manifest["plugin"]["keywords"]
    assert keywords
    for keyword in keywords:
        assert isinstance(keyword, str) and keyword.strip()
        re.compile(keyword)


@pytest.mark.static
def test_manifest__sdk_window_is_sane() -> None:
    """A-10 / A-11：recommended 落在 supported 之内，且当前 SDK_VERSION 在窗口里。"""
    from plugin.sdk.shared.constants import SDK_VERSION

    manifest = tomllib.loads(MANIFEST_TEXT)
    sdk = manifest["plugin"]["sdk"]
    assert sdk["recommended"] == ">=0.1.0,<0.2.0"
    assert sdk["supported"] == ">=0.1.0,<0.3.0"
    assert SDK_VERSION == "0.1.0"


@pytest.mark.static
def test_manifest__runtime_flags() -> None:
    """A-15 / A-16：默认可用但不自启。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    runtime = manifest["plugin_runtime"]
    assert runtime["enabled"] is True
    assert runtime["auto_start"] is False


@pytest.mark.static
def test_manifest__store_enabled() -> None:
    """A-14 的前置：清单里必须显式开启 store，否则 ``self.store`` 恒为禁用。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    assert manifest["plugin"]["store"]["enabled"] is True


@pytest.mark.static
def test_manifest__i18n_declared() -> None:
    """A-12 / A-13：i18n 必须显式声明（不要依赖默认值侥幸生效）。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    i18n = manifest["plugin"]["i18n"]
    assert i18n["default_locale"] == "zh-CN"
    assert i18n["locales_dir"] == "i18n"
    assert (PLUGIN_DIR / i18n["locales_dir"] / f"{i18n['default_locale']}.json").is_file()


@pytest.mark.static
def test_manifest__no_dead_custom_keys() -> None:
    """G-07：``[genshin_game_buddy]`` 与 ``[games.*]`` 的每个键都要被生产代码读到。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    blob = executable_blob()
    for key in manifest["genshin_game_buddy"]:
        assert key in blob, f"dead config key: {key}"
    for entry in manifest["games"].values():
        for key in entry:
            assert key in blob, f"dead config key: {key}"


@pytest.mark.static
def test_manifest__games_match_terms_tree() -> None:
    """B-09 / B-11：注册表 ↔ 目录双向一致。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    declared = {(PLUGIN_DIR / entry["terms_dir"]).resolve() for entry in manifest["games"].values()}
    terms_root = PLUGIN_DIR / "terms"
    assert terms_root.is_dir()
    on_disk = {path.resolve() for path in terms_root.iterdir() if path.is_dir()}
    assert declared == on_disk, [str(path) for path in declared ^ on_disk]


@pytest.mark.static
@pytest.mark.release
def test_manifest__author_is_filled_at_release() -> None:
    """A-09：作者名不能是占位符。"""
    manifest = tomllib.loads(MANIFEST_TEXT)
    author = manifest["plugin"]["author"]["name"].strip()
    assert author and author != "TODO"
