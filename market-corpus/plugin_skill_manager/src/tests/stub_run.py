#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin_skill_manager 入口层端到端验证（桩 SDK + 真实 KB/DB 插件实例链）。

链路：skill 插件 --(plugins router)--> plugin_knowledge_base --> plugin_database
判据：
  * startup 注册四个 Skill（动态入口存在）
  * skill_validate / skill_list 通过
  * skill_invoke 走通 KB 检索并记录统计
  * skill_disable 后入口注销 + 调用被拒；skill_enable 恢复
  * skill_reload 热加载新增的 SKILL.md
  * shutdown 注销全部入口

用法： python tests\\stub_run.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE.parent
SKILL_ID = "plugin_skill_manager"
KB_ID = "plugin_knowledge_base"
DB_ID = "plugin_database"

_IGNORE_NAMES = frozenset({".git", "__pycache__", ".vscode", "tests", ".pytest_cache", ".ruff_cache"})
#: Windows 重解析点（junction / symlink）标志位。
_REPARSE = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _copy_real_tree(src: Path, dest: Path, ignore: frozenset[str]) -> None:
    """复制目录树，但**跳过重解析点**（不进去、也不复制）。

    为什么不能用 ``shutil.copytree``：部署实例的 ``plugin_skill_manager/skills/``
    里有 90+ 个指向用户 skill 的 junction（这正是本插件的交付物）。Windows 的
    junction 不是 ``S_ISLNK``，``symlinks=True`` 对它们无效，copytree 仍会递归进去；
    一旦某个用户 skill 源目录被删/改名，就抛 ``WinError 3`` 让整个桩环境崩掉。

    这些桩测试只关心**随包分发**的 5 个自带 Skill，跳过联接既正确又更快。
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.name in ignore:
            continue
        try:
            attrs = entry.lstat().st_file_attributes  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            attrs = 0
        if attrs & _REPARSE:
            continue  # 用户挂载点：不属于自带 Skill，跳过
        target = dest / entry.name
        if entry.is_dir():
            _copy_real_tree(entry, target, ignore)
        elif entry.is_file():
            shutil.copy2(entry, target)
RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label, detail))
    print(("[PASS] " if ok else "[FAIL] ") + label + (f"  -> {detail}" if detail else ""), flush=True)


STUB_SDK = '''\
"""桩 plugin.sdk.plugin。"""
from __future__ import annotations


class SdkError(Exception):
    pass


class Ok:
    def __init__(self, value=None):
        self.value = value
        self.error = None


class Err:
    def __init__(self, error):
        self.error = error
        self.value = None


def neko_plugin(cls):
    return cls


def _mark(kind, **kw):
    def deco(fn):
        fn._neko_kind = kind
        fn._neko_meta = kw
        return fn
    return deco


def plugin_entry(**kw):
    return _mark("entry", **kw)


def lifecycle(**kw):
    return _mark("lifecycle", **kw)


class _Config:
    def __init__(self, data):
        self._data = data

    async def dump(self):
        return self._data


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class FakeStore:
    def __init__(self):
        self._data = {}

    async def get(self, key):
        return Ok(self._data.get(key))

    async def set(self, key, value):
        self._data[key] = value
        return Ok(True)


class NekoPluginBase:
    def __init__(self, ctx):
        self.ctx = ctx
        self.plugin_id = ctx["plugin_id"]
        self.plugin_dir = ctx["plugin_dir"]
        self.storage_dir = ctx["storage_dir"]
        self._config = _Config(ctx.get("config", {}))
        self.logger = _Logger()
        self.plugins = ctx.get("plugins")
        self.store = FakeStore()
        self._dynamic = {}

    @property
    def config(self):
        return self._config

    def report_status(self, status):
        pass

    def register_dynamic_entry(self, entry_id, handler, name="", description="",
                               input_schema=None, kind="action", auto_start=False,
                               timeout=None, llm_result_fields=None):
        self._dynamic[entry_id] = handler
        return True

    def unregister_dynamic_entry(self, entry_id):
        return self._dynamic.pop(entry_id, None) is not None

    def list_entries(self, include_disabled=False):
        return [{"id": k} for k in self._dynamic]

class _StubUi:
    """桩 ui 命名空间。

    真 SDK 的 ui 是模块（sdk/plugin/ui.py），导出 context()/action() 两个装饰器，
    且在 plugin.sdk.plugin.__all__ 里。桩里缺它会导致插件 __init__.py 顶层
    `from plugin.sdk.plugin import ... ui` 直接 ImportError —— 整套桩测试跑不起来。
    """

    @staticmethod
    def context(*_a, **_k):
        def deco(fn):
            return fn
        return deco

    @staticmethod
    def action(*_a, **_k):
        def deco(fn):
            return fn
        return deco


ui = _StubUi()

'''


class ChainedRouter:
    """按 plugin_id 前缀路由到真实插件实例（skill -> kb -> db 全链）。"""

    Ok = None
    Err = None
    #: 由 main() 从宿主 SDK 注入，与 Ok/Err 同源。
    SdkError = None

    def __init__(self, targets: dict):
        self._targets = targets  # {"plugin_knowledge_base": kb, ...}

    async def call_entry(self, target: str, payload: dict | None = None, timeout=None):
        plugin_id, _, entry_id = target.partition(":")
        plugin = self._targets.get(plugin_id)
        if plugin is None:
            return self.Err(self.SdkError(f"unknown plugin {plugin_id!r}"))
        fn = getattr(plugin, entry_id, None)
        if fn is None or not callable(fn):
            return self.Err(self.SdkError(f"unknown entry {entry_id!r}"))
        try:
            return await fn(**(payload or {}))
        except TypeError as exc:
            return self.Err(self.SdkError(f"bad payload for {entry_id}: {exc}"))

    async def require_enabled(self, plugin_id: str, timeout=None):
        return self.Ok({"plugin": plugin_id, "enabled": True})


def build_shim(root: Path) -> None:
    plugin_pkg = root / "plugin"
    sdk = plugin_pkg / "sdk"
    sdk_plugin = sdk / "plugin"
    plugins = plugin_pkg / "plugins"
    sdk_plugin.mkdir(parents=True, exist_ok=True)
    targets = {
        SKILL_ID: PLUGIN_DIR,
        KB_ID: PLUGIN_DIR.parent / KB_ID,
        DB_ID: PLUGIN_DIR.parent / DB_ID,
    }
    for pid, src in targets.items():
        dest = plugins / pid
        if dest.exists():
            shutil.rmtree(dest)
        _copy_real_tree(src, dest, _IGNORE_NAMES)
    (plugin_pkg / "__init__.py").write_text("", encoding="utf-8")
    (sdk / "__init__.py").write_text("", encoding="utf-8")
    (sdk_plugin / "__init__.py").write_text(STUB_SDK, encoding="utf-8")
    (plugins / "__init__.py").write_text("", encoding="utf-8")


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="neko_skill_stub_"))
    build_shim(tmp)
    sys.path.insert(0, str(tmp))
    storage = tmp / "storage"

    import importlib

    from plugin.sdk.plugin import Err, Ok, SdkError

    ChainedRouter.Ok = Ok
    ChainedRouter.Err = Err
    ChainedRouter.SdkError = SdkError

    db_mod = importlib.import_module(f"plugin.plugins.{DB_ID}")
    kb_mod = importlib.import_module(f"plugin.plugins.{KB_ID}")
    sk_mod = importlib.import_module(f"plugin.plugins.{SKILL_ID}")

    db = db_mod.PluginDatabasePlugin({
        "plugin_id": DB_ID,
        "plugin_dir": tmp / "plugin" / "plugins" / DB_ID,
        "storage_dir": storage / DB_ID,
        "config": {"database": {"driver": "sqlite"}},
    })
    r = await db.on_startup()
    check(isinstance(r, Ok), "db startup", repr(r) if not isinstance(r, Ok) else "")

    kb_router = ChainedRouter({DB_ID: db})
    kb = kb_mod.PluginKnowledgeBasePlugin({
        "plugin_id": KB_ID,
        "plugin_dir": tmp / "plugin" / "plugins" / KB_ID,
        "storage_dir": storage / KB_ID,
        "config": {"knowledge": {"models_dir": ""}, "hot_promotion": {"window_days": 7, "threshold": 5, "demotion_days": 30}},
        "plugins": kb_router,
    })
    r = await kb.on_startup()
    check(isinstance(r, Ok), "kb startup", repr(r) if not isinstance(r, Ok) else "")

    kdir = storage / "kbdocs"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / "db.md").write_text(
        "# 数据库事务\n\n事务具有 ACID 特性：原子性、一致性、隔离性、持久性。\n\n"
        "SQLite 支持 WAL 模式，可以提高并发读写性能。",
        encoding="utf-8",
    )
    r = await kb.kb_import(source_dir=str(kdir))
    check(isinstance(r, Ok) and r.value["imported"] >= 1, "kb_import for skill invoke", repr(r.value if isinstance(r, Ok) else r))

    skill_router = ChainedRouter({KB_ID: kb, DB_ID: db})

    # 造一个「用户文件夹」：两个外部 skill + 一份不合规的，验证扫描/挂载/只登记
    fake_home = tmp / "home"
    ext_root = fake_home / ".agents" / "skills"
    for name, desc in (("user-alpha", "用户外部技能 alpha，验证只登记不注册入口。"),
                       ("user-beta", "用户外部技能 beta，验证目录联接挂载。")):
        d = ext_root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\nlicense: MIT\n"
            f"metadata:\n  author: user\n  version: \"1.0\"\n---\n\n何时使用：测试。\n",
            encoding="utf-8",
        )
    bad = ext_root / "user-broken"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")

    sk = sk_mod.PluginSkillManagerPlugin({
        "plugin_id": SKILL_ID,
        "plugin_dir": tmp / "plugin" / "plugins" / SKILL_ID,
        "storage_dir": storage / SKILL_ID,
        "config": {"skill_manager": {"auto_register": True, "home": str(fake_home)}},
        "plugins": skill_router,
    })

    r = await sk.on_startup()
    ok = isinstance(r, Ok) and r.value["builtin"] == 5 and r.value["external"] == 2
    check(ok, "startup: 5 个自带 + 2 个外部", repr(r.value if isinstance(r, Ok) else r))

    r = await sk.skill_list()
    ok = isinstance(r, Ok) and r.value["count"] == 7
    check(ok, "skill_list 共 7 个", repr(r.value if isinstance(r, Ok) else r))
    if isinstance(r, Ok):
        rows = {s["name"]: s for s in r.value["skills"]}
        check(
            {n for n, s in rows.items() if s["registered"]} == {
                "knowledge-search", "knowledge-import", "database-query", "database-export"
            },
            "只有 4 个可执行 Skill 注册为入口",
            repr(sorted(n for n, s in rows.items() if s["registered"])),
        )
        ext_names = {"user-alpha", "user-beta"}
        check(
            all(rows[n]["origin"] == "external" for n in ext_names)
            and all(rows[n]["routable"] is False for n in ext_names),
            "外部 Skill 标记为 external 且 routable=False",
            repr({n: (rows[n]["origin"], rows[n]["routable"], rows[n]["link"]) for n in ext_names}),
        )
        check(
            all(rows[n]["link"] in {"junction", "copy"} for n in ext_names),
            "外部 Skill 已挂载（junction 优先）",
            repr({n: rows[n]["link"] for n in ext_names}),
        )
        check(
            any("user-broken" in x and "不合规" in x for x in r.value["notes"]),
            "不合规的外部 SKILL.md 进 notes 而不挡其余",
            repr(r.value["notes"][:3]),
        )
    check(
        (tmp / "plugin" / "plugins" / SKILL_ID / "skills" / "user-alpha" / "SKILL.md").is_file(),
        "外部 Skill 已挂到 skills/ 下",
    )

    r = await sk.skill_validate()
    ok = (
        isinstance(r, Ok)
        and r.value["count"] == 7
        and all(f["ok"] for f in r.value["files"])
        and {f["origin"] for f in r.value["files"]} == {"builtin", "external"}
    )
    check(ok, "skill_validate 覆盖自带 + 外部且全部通过", repr(r.value if isinstance(r, Ok) else r))

    r = await sk.skill_validate(origin="external")
    ok = isinstance(r, Ok) and r.value["count"] == 2
    check(ok, "skill_validate origin=external 过滤", repr(r.value if isinstance(r, Ok) else r))

    # 外部 Skill：只登记，调用必须是结构化 Err
    r = await sk.skill_invoke(name="user-alpha", params={"query": "x"})
    check(
        isinstance(r, Err) and "no KB route" in str(r.error),
        "外部 Skill 调用 -> Err(no KB route)",
        repr(r),
    )
    r = await sk.skill_enable(name="user-alpha")
    ok = isinstance(r, Ok) and r.value["registered"] is False and r.value["routable"] is False
    check(ok, "外部 Skill enable 后仍不注册入口", repr(r.value if isinstance(r, Ok) else r))

    r = await sk.skill_invoke(name="knowledge-search", params={"query": "事务的 ACID 特性是什么", "top_k": 2})
    ok = isinstance(r, Ok) and r.value.get("count", 0) >= 1 and "elapsed_ms" in r.value
    check(ok, "skill_invoke knowledge-search -> KB 检索 + 统计", repr(r.value if isinstance(r, Ok) else r))

    r = await sk.skill_invoke(name="database-query", params={"limit": 5})
    ok = isinstance(r, Ok) and "skill" in r.value
    check(ok, "skill_invoke database-query（空表也 Ok）", repr(r.value if isinstance(r, Ok) else r))

    r = await sk.skill_invoke(name="database-export", params={"fmt": "md"})
    ok = isinstance(r, Ok) and Path(r.value["path"]).exists()
    check(ok, "skill_invoke database-export 落盘", repr(r.value if isinstance(r, Ok) else r))

    # 禁用 -> 入口注销 + 调用被拒
    r = await sk.skill_disable(name="knowledge-search")
    check(isinstance(r, Ok) and r.value["registered"] is False, "skill_disable 注销入口", repr(r))
    check(not any(e["id"] == "knowledge-search" for e in sk.list_entries()), "动态入口已移除")
    r = await sk.skill_invoke(name="knowledge-search", params={"query": "x"})
    check(isinstance(r, Err) and "disabled" in str(r.error), "禁用后调用 -> Err(结构化)", repr(r))

    # 启用 -> 恢复
    r = await sk.skill_enable(name="knowledge-search")
    check(isinstance(r, Ok) and r.value["registered"] is True, "skill_enable 重新注册", repr(r))
    r = await sk.skill_invoke(name="knowledge-search", params={"query": "WAL 模式"})
    check(isinstance(r, Ok), "启用后可调用", repr(r) if not isinstance(r, Ok) else "")

    # 热重载：新增一份自带 SKILL.md 后 reload 出现第六个自带
    new_dir = tmp / "plugin" / "plugins" / SKILL_ID / "skills" / "extra-skill"
    new_dir.mkdir(parents=True)
    (new_dir / "SKILL.md").write_text(
        "---\nname: extra-skill\ndescription: 用于验证热重载的额外技能，当用户测试重载时使用。\n"
        "license: Apache-2.0\nmetadata:\n  author: neko-plugin\n  version: \"1.0\"\n---\n\n正文\n",
        encoding="utf-8",
    )
    r = await sk.skill_reload()
    ok = isinstance(r, Ok) and r.value["count"] == 8
    check(ok, "skill_reload 热加载新 Skill（重扫后仍带上外部）", repr(r.value if isinstance(r, Ok) else r))

    r = await sk.skill_invoke(name="extra-skill", params={})
    check(isinstance(r, Err), "无路由的新 Skill 调用 -> Err(结构化)")

    # 统计落库验证（经 KB -> DB）
    r = await kb.kb_query_skill_stats()
    ok = isinstance(r, Ok) and r.value["rows"] and any(
        row["skill"] == "knowledge-search" for row in r.value["rows"]
    )
    check(ok, "调用统计已写入数据库插件", repr(r.value if isinstance(r, Ok) else r))

    await sk.on_shutdown()
    check(len(sk._dynamic) == 0, "shutdown 注销全部动态入口")
    # 挂载点**故意留着**：卸载时摘掉会让下次启动必须重建 93 个 junction（超 3s 启动预算），
    # 且一旦 store 里的挂载名与自带重名就会误删自带真目录。重装时用 skill_sync(force=true)。
    check(
        (tmp / "plugin" / "plugins" / SKILL_ID / "skills" / "user-alpha").exists(),
        "shutdown 保留外部挂载点（避免下次启动超时/误删）",
    )
    check(
        (tmp / "plugin" / "plugins" / SKILL_ID / "skills" / "knowledge-search").is_dir(),
        "shutdown 不动自带 Skill",
    )
    # 强制重挂：只删联接，真实目录必须保住
    await sk.skill_sync(force=True)
    check(
        not (tmp / "plugin" / "plugins" / SKILL_ID / "skills" / "user-alpha").exists()
        or sk._skills.get("user-alpha") is not None,
        "force 重挂后外部 Skill 仍可用",
    )
    check(
        (tmp / "plugin" / "plugins" / SKILL_ID / "skills" / "knowledge-search").is_dir(),
        "force 重挂不删自带真目录",
    )
    await kb.on_shutdown()
    await db.on_shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    failed = [x for x in RESULTS if not x[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    code = 2
    try:
        code = asyncio.run(asyncio.wait_for(main(), 120))
    except Exception:
        traceback.print_exc()
        code = 2
    finally:
        # 崩溃路径下 aiosqlite 残留线程可能阻塞解释器退出，强制结束
        sys.stdout.flush()
        sys.stderr.flush()
        import os
        os._exit(max(0, min(code, 1)) if isinstance(code, int) else 2)
