"""deepseekharness_bridge 的冒烟测试。

这些测试刻意不依赖 N.E.K.O. 宿主或真实 harness：只验证插件自身的纯逻辑
（manifest 契约、工具名、参数转换），因此可以在没有运行环境的机器上跑。

插件目录通过 ``NEKO_PLUGIN_DIR`` 或相对本文件推断获得，不依赖 pytest 的包
导入机制——插件根目录的 ``__init__.py`` 导入 ``plugin.sdk``，在 CI 里并不存在。
"""

from __future__ import annotations

import ast
import json
import os
import re
import tomllib
from pathlib import Path

import pytest

# 优先用环境变量，其次相对本文件推断（tests/ 的上一级）。
PLUGIN_DIR = Path(
    os.environ.get("NEKO_PLUGIN_DIR") or Path(__file__).resolve().parent.parent
).resolve()


def _manifest() -> dict:
    with (PLUGIN_DIR / "plugin.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_plugin_dir_resolves() -> None:
    """推断或配置的插件目录必须真的包含 plugin.toml。"""
    assert (PLUGIN_DIR / "plugin.toml").is_file(), f"未找到 plugin.toml：{PLUGIN_DIR}"


def test_manifest_required_fields() -> None:
    plugin = _manifest()["plugin"]

    assert plugin["id"] == "deepseekharness_bridge"
    assert plugin["name"]
    assert plugin["version"]
    assert plugin["entry"].startswith("plugin.plugins.deepseekharness_bridge:")


def test_folder_name_matches_plugin_id() -> None:
    """分发时源码目录名必须与插件 ID 一致，否则安装包按 ID 建目录会错位。"""
    assert PLUGIN_DIR.name == _manifest()["plugin"]["id"]


def test_runtime_flags_are_booleans() -> None:
    runtime = _manifest()["plugin_runtime"]
    assert isinstance(runtime["enabled"], bool)
    assert isinstance(runtime["auto_start"], bool)
    assert 0 < runtime["timeout"] <= 300


def test_entry_reference_is_well_formed() -> None:
    """入口必须形如 module.path:ClassName。"""
    entry = _manifest()["plugin"]["entry"]
    module_path, _, class_name = entry.partition(":")
    assert re.fullmatch(r"[A-Za-z_][\w.]*", module_path)
    assert re.fullmatch(r"[A-Za-z_]\w*", class_name)
    assert class_name.endswith("Plugin")


def test_no_requirements_txt() -> None:
    """包内不允许 requirements.txt；第三方依赖必须走 pyproject + vendor/。"""
    assert not (PLUGIN_DIR / "requirements.txt").exists()


def test_declares_no_external_dependencies() -> None:
    """本插件零第三方依赖：SDK 是可选项，运行时动态探测。"""
    pyproject = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


def test_entry_module_syntax_is_valid() -> None:
    source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    ast.parse(source)


def _decorator_name(deco: ast.expr) -> str | None:
    """取装饰器名字，兼容 `@x`、`@x()`、`@mod.x`、`@mod.x()` 四种写法。"""
    target = deco.func if isinstance(deco, ast.Call) else deco
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def test_entries_are_async() -> None:
    """运行时触发的 @plugin_entry / @llm_tool 处理器必须是 async def。"""
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    decorated = {"plugin_entry", "llm_tool"}

    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if _decorator_name(deco) in decorated:
                checked += 1
                assert isinstance(node, ast.AsyncFunctionDef), (
                    f"{node.name} 用 @{_decorator_name(deco)} 装饰但不是 async def"
                )

    assert checked >= 3, "预期的入口数量没到，说明 AST 检查没生效"


def test_ui_actions_are_backed_by_entries() -> None:
    """每个 @ui.action 必须同时是 @plugin_entry，否则面板调不到它。"""
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))

    action_only: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {_decorator_name(deco) for deco in node.decorator_list}
        if "action" in names and "plugin_entry" not in names:
            action_only.append(node.name)

    assert not action_only, f"这些函数只有 @ui.action 没有 @plugin_entry：{action_only}"


def test_panel_entry_file_exists() -> None:
    """manifest 里声明的 UI 入口文件必须真的存在，否则面板显示不可用。"""
    manifest = _manifest()
    for surface in manifest.get("plugin", {}).get("ui", {}).get("panel", []):
        entry = surface.get("entry")
        if entry:
            assert (PLUGIN_DIR / entry).is_file(), f"UI 入口缺失：{entry}"


def test_no_secrets_in_source() -> None:
    """源码里不能出现硬编码密钥。"""
    for path in sorted(PLUGIN_DIR.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"sk-[A-Za-z0-9]{16,}", text), f"{path.name} 疑似含密钥"


def test_embedded_dsh_bundle_is_complete() -> None:
    """内嵌的 DSH bundle 必须自包含，否则 bootstrap 无从复制。

    This is what makes "just start the N.E.K.O. plugin" work: the DSH-side
    bundle ships inside this plugin instead of relying on a sibling checkout.
    """
    bundle = PLUGIN_DIR / "bundled" / "dsh-plugin-neko"
    assert (bundle / "package.json").is_file(), "缺少 package.json"
    assert (bundle / "lib" / "index.js").is_file(), "缺少 lib/index.js"
    assert (bundle / "cordis.patch.yml").is_file(), "缺少 cordis.patch.yml"

    manifest = json.loads((bundle / "package.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "dsh-plugin-neko"
    assert manifest["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"

    entry = (bundle / "lib" / "index.js").read_text(encoding="utf-8")
    for needle in ("export const name", "export function apply", "defineTool"):
        assert needle in entry, f"lib/index.js 缺少 {needle}"


def test_embedded_bundle_patch_points_at_package_name() -> None:
    """bundle patch 必须按包名引用自己，不能是相对路径。

    A relative path only works inside a --patch overlay; an installed bundle
    needs the package name so Node resolution finds it.
    """
    patch = (PLUGIN_DIR / "bundled" / "dsh-plugin-neko" / "cordis.patch.yml").read_text(
        encoding="utf-8"
    )
    assert "dsh-plugin-neko" in patch
    assert not re.search(r"name:\s*['\"]?\./", patch), "patch 里不能出现相对路径"


def test_bootstrap_hooks_exist() -> None:
    """startup 必须触发 DSH 侧 bootstrap，否则用户还得手动装。"""
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))

    startup = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {_decorator_name(deco) for deco in node.decorator_list}
        if "lifecycle" in names:
            # lifecycle(id="startup")
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call):
                    for kw in deco.keywords:
                        if kw.arg == "id" and getattr(kw.value, "value", "") == "startup":
                            startup = node

    assert startup is not None, "找不到 startup 生命周期"
    body = ast.dump(startup)
    assert "_ensure_dsh_bridge_installed" in body, "startup 没有调用 DSH 侧 bootstrap"


def test_cli_matches_the_headless_contract() -> None:
    """CLI 调用必须符合 headless profile 的真实契约。

    Verified against DSH 0.2.1-rc.2: `--profile headless "<task>"` takes the
    task as a positional argument. There is no `run` subcommand, no
    `--prompt-file`, and `--session-id` only accepts an id for a session that
    already exists (passing a fresh one fails the run).

    Checks the code, not the comments: docstrings are allowed to mention the
    flags this deliberately avoids.
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))

    # Collect string literals that live in expressions (i.e. real arguments),
    # skipping docstrings and comments entirely.
    run_cli = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_via_cli"
        ),
        None,
    )
    assert run_cli is not None, "找不到 _run_via_cli"

    literals: list[str] = []
    for node in ast.walk(run_cli):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)

    assert "run" not in literals, "headless 没有 run 子命令"
    assert "prompt-file" not in literals, "headless 不接受 --prompt-file"
    assert "--session-id" not in literals, "--session-id 只能用于已存在的会话"
    assert "--profile" in literals, "必须显式传 --profile"

    # The task is positional, so it must be appended rather than flag-prefixed.
    appended = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        for node in ast.walk(run_cli)
    )
    assert appended, "任务必须以位置参数追加到 argv"


def test_desktop_launcher_env_is_set() -> None:
    """桌面端 CLI 必须经 exe 以 node 运行时启动。"""
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    run_cli = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_via_cli"
        ),
        None,
    )
    assert run_cli is not None
    literals = [
        node.value
        for node in ast.walk(run_cli)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert "ELECTRON_RUN_AS_NODE" in literals
    assert "--expose-internals" in literals


def test_desktop_cli_is_a_fallback_source() -> None:
    """桌面端安装必须被当作 dsh 可执行文件的一个来源。

    Without this, a desktop-only install (no PATH entry, no source checkout)
    resolves to nothing and the panel stays "not ready".
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    resolve = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_resolve_dsh_executable"
        ),
        None,
    )
    assert resolve is not None, "找不到 _resolve_dsh_executable"
    body = ast.dump(resolve)
    assert "_find_dsh_install" in body, "没有把桌面端安装作为查找来源"
    assert "dsh-desktop-host" in body, "没有定位 asar 内的 CLI"


def test_desktop_cli_probes_the_archive_not_its_entries() -> None:
    """桌面端分支必须检查 app.asar 本身，而不是 asar 内的路径。

    app.asar is a single archive file: Path.exists() on a path inside it is
    always False for a plain interpreter — only the app's Electron runtime
    resolves asar entries. Requiring the inner path to exist made the desktop
    install undetectable, which is exactly the "not ready" bug.
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    resolve = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_resolve_dsh_executable"
        ),
        None,
    )
    assert resolve is not None

    literals = [
        node.value
        for node in ast.walk(resolve)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert "app.asar" in literals, "没有检查 app.asar 归档本身"

    # Which variables get .exists() called on them. `asar` must be one of them,
    # and the inner cli.js path must not be — that is the whole bug.
    def var_name(node: ast.expr) -> str | None:
        return node.id if isinstance(node, ast.Name) else None

    checked: set[str] = set()
    for node in ast.walk(resolve):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "exists"):
            continue
        name = var_name(func.value)
        if name:
            checked.add(name)

    assert "asar" in checked, f"exists() 没作用在 asar 上；实际检查了 {sorted(checked)}"
    assert "cli" not in checked, "exists() 不能作用在 asar 内的 cli.js 路径上"


def test_logs_do_not_record_task_body() -> None:
    """运行日志只记元数据。

    用 AST 取出每个 logger 调用的参数，检查其中没有名为 ``task`` 的变量
    （那会把任务正文写进日志）。日志文案里出现 "task" 这个词是允许的，
    如 "dispatching harness task"。
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        if func.value.id != "logger":
            continue

        for arg in node.args:
            names = {
                n.id for n in ast.walk(arg) if isinstance(n, ast.Name)
            }
            assert "task" not in names, (
                f"logger.{func.attr}() 的参数里出现 task 变量（第 {node.lineno} 行）"
            )


def test_model_catalog_matches_the_running_dsh() -> None:
    """模型目录必须与 DSH 0.2.1-rc.2 内置的一致。

    Read from @deepseek-ai/dsh-llm-deepseek's DEFAULT_MODELS. An id outside this
    set fails at request time with MODEL_UNSUPPORTED, and the previous default
    ("deepseek-v4-flash") was not in it.
    """
    source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    assert '"deepseek-flash"' in source, "缺少 deepseek-flash"
    assert '"deepseek-v4-pro"' in source, "缺少 deepseek-v4-pro"

    manifest = _manifest()
    model = manifest.get("deepseekharness_bridge", {}).get("model")
    assert model == "deepseek-flash", f"默认模型应为 deepseek-flash，实际 {model}"

    # The stale id must not survive as a usable value. It may appear where a
    # stale value is being corrected, so check the manifest and the catalog
    # rather than the whole source.
    catalog = re.search(r"_MODEL_CATALOG[^\]]*\]", source, re.DOTALL)
    assert catalog is not None
    assert "deepseek-v4-flash" not in catalog.group(0), "目录里还有不存在的模型 ID"


def test_model_choice_is_exposed_to_the_panel() -> None:
    """模型必须能通过面板切换，且选项来自目录而非自由输入。"""
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))

    setter = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "panel_set_model"
        ),
        None,
    )
    assert setter is not None, "找不到 panel_set_model"

    # The enum is what makes the panel render a dropdown.
    schema = next(
        (
            deco
            for deco in setter.decorator_list
            if isinstance(deco, ast.Call) and _decorator_name(deco) == "plugin_entry"
        ),
        None,
    )
    assert schema is not None
    body = ast.dump(schema)
    assert "enum" in body, "input_schema 缺少 enum，面板会退化成自由输入"
    assert "_KNOWN_MODELS" in body, "enum 必须来自模型目录"


def test_account_route_is_the_default() -> None:
    """默认必须走账号路由，不要求用户去申请 API key。

    Verified against DSH 0.2.1-rc.2: headless disables credentials,
    authorization and deepseek-account, and a patch layer that sets them back
    to `disabled: false` makes an existing DeepSeek sign-in work — the run
    reaches the model and prints its answer with no API key.
    """
    manifest = _manifest()
    section = manifest.get("deepseekharness_bridge", {})
    assert section.get("provider") == "deepseek-account", (
        "默认凭据路由应为 deepseek-account"
    )


def test_account_patch_reenables_the_disabled_rows() -> None:
    """生成的 patch 必须把三个被禁用的服务重新打开。

    Runs the writer for real instead of grepping source: the rows are assembled
    by string join, so an AST check says nothing about the emitted YAML.
    """
    import asyncio
    import importlib
    import sys
    import tempfile
    import types

    # Stub the SDK surface: the module imports plugin.sdk.plugin at top level,
    # and the class subclasses NekoPluginBase, so it has to be a real class.
    stub = types.ModuleType("plugin.sdk.plugin")

    class _Base:
        def __init__(self, *a, **k):
            pass

    class _Decorator:
        """Supports both ``@x`` and ``@x(id=...)`` usage."""

        def __init__(self, *a, **k):
            pass

        def __call__(self, target=None, **kwargs):
            # Bare use returns the target; keyword use returns a decorator.
            if target is None:
                return lambda fn: fn
            return target

        def __getattr__(self, name):
            return _Decorator()

    stub.NekoPluginBase = _Base
    for name in (
        "neko_plugin", "plugin_entry", "lifecycle", "llm_tool", "ui",
        "Ok", "Err", "SdkError", "unwrap", "unwrap_or", "Result",
        "Plugins", "PluginRouter", "PluginConfig", "PluginStore", "SystemInfo",
        "EntryKind", "PluginMeta", "LlmToolMeta", "OsActivitySnapshot",
        "get_os_activity_snapshot", "PluginI18n", "tr", "PluginSettings",
        "SettingsField", "TransportError", "get_plugin_logger", "hook",
        "before_entry", "after_entry", "around_entry", "replace_entry",
        "quick_action", "plugin", "message", "on_event", "custom_event",
        "timer_interval",
    ):
        setattr(stub, name, _Decorator())

    sys.modules.setdefault("plugin", types.ModuleType("plugin"))
    sys.modules.setdefault("plugin.sdk", types.ModuleType("plugin.sdk"))
    sys.modules["plugin.sdk.plugin"] = stub

    sys.path.insert(0, str(PLUGIN_DIR.parent))
    module = importlib.import_module("deepseekharness_bridge")
    cls = module.DeepseekharnessBridgePlugin
    instance = cls.__new__(cls)
    instance.metadata = {}
    instance.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )
    instance._dsh_executable = None
    instance._dsh_desktop_launcher = None
    instance._runs = {}
    instance._dsh_bridge_state = "unknown"
    instance._sdk_available = None

    async def _cfg(key, default=None):
        return default

    instance._cfg = _cfg

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        patch = asyncio.run(instance._write_account_patch(workspace, "deepseek-flash"))
        assert patch is not None, "账号路由下必须生成 patch"
        text = patch.read_text(encoding="utf-8")

        for row in ("credentials", "authorization", "deepseek-account"):
            assert row in text, f"patch 没有重新启用 {row}"
        assert "disabled: false" in text, "patch 缺少 disabled: false"
        assert "provider: deepseek-account" in text
        assert "model: deepseek-flash" in text

        # permission-presets must declare all three sandbox modes as real
        # presets and point defaultPreset at the chosen one, so the user's
        # read-only selection is honoured instead of throwing "custom".
        assert "permission-presets" in text, "patch 缺少 permission-presets"
        for preset in ("read-only", "workspace-write", "danger-full-access"):
            assert f"{preset}:" in text, f"patch 没有声明 {preset} 预设"
            assert "approval: never" in text, "preset 缺少 approval: never"
        assert "defaultPreset: workspace-write" in text, "defaultPreset 未指向所选模式"
        # approval must be never on every preset (headless has no answerer:
        # "ask" fails closed).
        assert text.count("approval: never") == 3, "三个 preset 都应 approval: never"

        # The API-key route must not re-enable the account stack.

        async def _cfg_official(key, default=None):
            return "deepseek-official" if key == "provider" else default

        instance._cfg = _cfg_official
        assert asyncio.run(instance._write_account_patch(workspace, "m")) is None


def test_cli_passes_the_account_patch() -> None:
    """CLI 必须带上账号 patch，且不再传无效的 --model。

    The headless app accepts only the task, --session-id and --json, so
    `--model` is silently swallowed as a positional and breaks the task.
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    run_cli = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_via_cli"
        ),
        None,
    )
    assert run_cli is not None

    literals = [
        node.value
        for node in ast.walk(run_cli)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert "--patch" in literals, "CLI 没有传 --patch"
    assert "--model" not in literals, "headless 不接受 --model"
    assert "_write_account_patch" in ast.dump(run_cli), "CLI 没有调用账号 patch 生成"


def test_panel_run_task_waits_for_result() -> None:
    """面板提交任务必须等任务跑完再返回结果，不能只回个 session id。

    用户的要求是"等待任务结束返回"：提交后表单应当阻塞到 harness 给出答案，
    返回值里带 response / status / elapsed_seconds，而不只是一个 id。所以
    panel_run_task 的本体必须直接 await 真正的运行调用，而不是丢到后台。
    """
    source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    entry = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "panel_run_task"
        ),
        None,
    )
    assert entry is not None, "找不到 panel_run_task"

    # 本体不能再有后台协程 / _background_tasks 残留——那会让调用立即返回。
    body = ast.dump(entry)
    assert "create_task" not in body, "panel_run_task 不应把任务丢到后台"
    assert "_background_tasks" not in body, "不应再持有后台任务引用"

    # The harness run must be awaited in the entry's own statements (not inside
    # a nested background coroutine). Walk each statement and stop at function
    # boundaries so nested scopes don't leak their awaits in.
    def awaits_of(node: ast.AST) -> set[str]:
        found: set[str] = set()
        stack = list(ast.iter_child_nodes(node))
        while stack:
            current = stack.pop()
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # do not descend into nested scopes
            if isinstance(current, ast.Await) and isinstance(current.value, ast.Call):
                func = current.value.func
                if isinstance(func, ast.Attribute):
                    found.add(func.attr)
            stack.extend(ast.iter_child_nodes(current))
        return found

    entry_awaits: set[str] = set()
    for stmt in entry.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        entry_awaits |= awaits_of(stmt)

    assert "_invoke_harness" in entry_awaits, (
        "panel_run_task 本体没有 await _invoke_harness，会立即返回"
    )

    # The return value must carry the finished task's answer, not just an id.
    for key in ("response", "status", "elapsed_seconds"):
        assert key in body, f"返回值缺少 {key}（任务结果未一并返回）"


def test_run_log_stays_metadata_only() -> None:
    """磁盘日志只写元数据，不写任务正文和模型输出。"""
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    writer = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_append_run_log"
        ),
        None,
    )
    assert writer is not None, "找不到 _append_run_log"

    literals = {
        node.value
        for node in ast.walk(writer)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # These are the allowed keys; task/output/error must not be among them.
    assert "session_id" in literals
    assert "status" in literals
    for forbidden in ("task", "output", "error"):
        assert forbidden not in literals, f"日志写入了 {forbidden}"


def test_panel_submits_via_raw_api_call() -> None:
    """面板必须用 Button + api.call 提交，不能用 ActionForm。

    ActionForm and Select's onChange both rendered but never reached the entry —
    the plugin log showed panel_refresh firing while panel_run_task and
    panel_set_model never did. ActionButton (which also uses api.call) worked,
    so the submit path is fine and the form helpers are the broken part.
    """
    panel = (PLUGIN_DIR / "ui" / "panel.tsx").read_text(encoding="utf-8")

    # JSX usage, not the identifier: ActionButton contains "ActionForm" as a
    # substring, so a bare `in` check would false-positive on the import.
    assert "<ActionForm" not in panel, "不要用 ActionForm 提交任务"
    assert "api.call" in panel, "必须通过 api.call 提交"
    assert "panel_run_task" in panel
    assert "InlineError" in panel, "提交失败必须显示出来，不能静默"
    assert "onClick" in panel, "必须用 Button 的 onClick 触发"
    # Select's onChange never reached the entry either.
    assert "<Select" not in panel, "不要用 Select 的 onChange 触发调用"


def test_patch_flag_precedes_profile() -> None:
    """--patch 必须放在 --profile 之前。

    --patch is a launcher flag. Placed after --profile the launcher treats it as
    a positional and the run dies with "too many arguments. Expected 0
    arguments but got 1" — which is exactly what the v0.1.8 log showed.
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    run_cli = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_via_cli"
        ),
        None,
    )
    assert run_cli is not None

    # Source line of each flag's `argv += [...]`. The --patch one sits inside an
    # if-block, so body order is not enough — line numbers are.
    patch_line: int | None = None
    profile_line: int | None = None
    for node in ast.walk(run_cli):
        if not isinstance(node, ast.AugAssign):
            continue
        if not (isinstance(node.target, ast.Name) and node.target.id == "argv"):
            continue
        if not isinstance(node.value, ast.List):
            continue
        literals = [
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if "--patch" in literals:
            patch_line = node.lineno
        if "--profile" in literals:
            profile_line = node.lineno

    assert patch_line is not None, "CLI 没有传 --patch"
    assert profile_line is not None, "CLI 没有传 --profile"
    assert patch_line < profile_line, (
        "--patch 必须出现在 --profile 之前，否则会被当成位置参数"
    )


def test_stale_config_values_are_corrected() -> None:
    """运行时配置里的旧值必须在读取时被修正。

    The runtime config outlives plugin upgrades, so a value that was valid once
    keeps shadowing the manifest default. A real install ran with
    profile=sdk-minimal and model=deepseek-v4-flash — the headless CLI rejects
    sdk-minimal's argument shape and deepseek-v4-flash is not a model DSH knows.
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    cfg = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_cfg"
        ),
        None,
    )
    assert cfg is not None, "找不到 _cfg"
    body = ast.dump(cfg)

    assert "sdk-minimal" in body, "没有修正旧的 profile 值"
    assert "deepseek-v4-flash" in body, "没有修正旧的 model 值"
    assert '"headless"' in body or "'headless'" in body, "没有回落到 headless"
    assert "_DEFAULT_MODEL" in body, "没有回落到默认模型"


def test_sandbox_is_configurable_and_not_read_only() -> None:
    """沙箱必须可配，且默认不是 read-only。

    headless inherits dsh-base's read-only default, which denies every write —
    the harness refused to create a folder and reported it needed an approval
    channel a one-shot CLI run cannot service. The patch now sets the mode and
    the workspace root explicitly.

    Verified against DSH 0.2.1-rc.2 with workspace-write: the harness created
    the requested folder inside the workspace.
    """
    manifest = _manifest()
    section = manifest.get("deepseekharness_bridge", {})
    mode = section.get("sandbox_mode")
    assert mode == "workspace-write", f"默认沙箱应为 workspace-write，实际 {mode}"
    assert mode != "read-only", "read-only 会让 harness 无法创建任何文件"

    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    writer = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_write_account_patch"
        ),
        None,
    )
    assert writer is not None

    # Rows are emitted as prefixed strings ("- id: sandbox-policy",
    # "    workspaceRoot: "), so match the source segment rather than exact
    # literals.
    source_of_writer = ast.get_source_segment(
        (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"), writer
    )
    assert source_of_writer is not None
    assert "sandbox-policy" in source_of_writer, "patch 没有配置 sandbox-policy 行"
    assert "workspaceRoot" in source_of_writer, "patch 没有写 workspaceRoot"
    assert "mode" in source_of_writer, "patch 没有写 mode"


def test_effort_is_configurable() -> None:
    """思考强度必须可配——patch 曾经写死 max。"""
    manifest = _manifest()
    assert manifest["deepseekharness_bridge"].get("reasoning_effort") == "max"

    source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "reasoningEffort" in source, "patch 没有写 reasoningEffort"
    assert "_EFFORT_LEVELS" in source
    assert '"high"' in source, "缺少 high 档位"


def test_choice_validation_compares_against_ids() -> None:
    """校验必须和 ID 元组比，不能和字典元组比。

    The panel's sandbox and effort buttons silently did nothing: the values were
    checked against _SANDBOX_MODES / _EFFORT_LEVELS, which are tuples of dicts,
    so every string compared unequal and fell back to the default. The log line
    "unknown sandbox mode 'danger-full-access'; using 'workspace-write'" is what
    gave it away.
    """
    tree = ast.parse((PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"))
    writer = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_write_account_patch"
        ),
        None,
    )
    assert writer is not None
    source = ast.get_source_segment(
        (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8"), writer
    )
    assert source is not None

    assert "not in _KNOWN_EFFORTS" in source, "思考强度校验用了错误的集合"
    assert "not in _KNOWN_SANDBOXES" in source, "沙箱校验用了错误的集合"
    assert "not in _EFFORT_LEVELS" not in source, "不能和字典元组比较"
    assert "not in _SANDBOX_MODES" not in source, "不能和字典元组比较"


def test_stale_config_warning_fires_once() -> None:
    """配置告警只能打一次，否则每次面板刷新都重复一行。"""
    source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "_warn_once" in source, "缺少 _warn_once"
    # The two known-stale corrections must go through it.
    for needle in ("profile-stale", "model-stale"):
        assert needle in source, f"{needle} 没有走 _warn_once"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
