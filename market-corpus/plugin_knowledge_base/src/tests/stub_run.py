#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin_knowledge_base 入口层端到端验证（桩 SDK + 真实 plugin_database 实例）。

shim 包树同 neko_docs/tests/stub_run.py 约定；不同点：``self.plugins`` 是一个
指向**真实 plugin_database 插件实例**的进程内路由器，验证 KB -> DB 跨插件链路
（sqlite + vec0 + FTS5 全链路）。

判据（任务书验收）：
  * 导入 3 个 .md 文件 -> kb_import Ok 且 inserted >= 3
  * 执行 5 次检索 -> 第 5 次命中触发提升（window_hits/promoted 字段）
  * hot_knowledge 表出现对应记录（kb_hot_list / kb_stats 可见）
  * kb_hot_export 落盘 Markdown
  * Skill 统计经 kb_record_skill_stat 转发落库

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
KB_ID = "plugin_knowledge_base"
DB_ID = "plugin_database"
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


class NekoPluginBase:
    def __init__(self, ctx):
        self.ctx = ctx
        self.plugin_id = ctx["plugin_id"]
        self.plugin_dir = ctx["plugin_dir"]
        self.storage_dir = ctx["storage_dir"]
        self._config = _Config(ctx.get("config", {}))
        self.logger = _Logger()
        self.plugins = ctx.get("plugins")

    @property
    def config(self):
        return self._config

    def report_status(self, status):
        pass

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


class InProcessRouter:
    """把 self.plugins.call_entry 路由到真实的 plugin_database 插件实例。"""

    Ok = None  # build_shim 后由 main() 注入桩类型
    Err = None
    SdkError = None  # 同样由 main() 注入，与 Ok/Err 同源

    def __init__(self, db_plugin):
        self._db = db_plugin

    async def call_entry(self, target: str, payload: dict | None = None, timeout=None):
        plugin_id, _, entry_id = target.partition(":")
        if plugin_id != DB_ID:
            return self.Err(self.SdkError(f"unknown plugin {plugin_id!r}"))
        fn = getattr(self._db, entry_id, None)
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
    kb_target = plugins / KB_ID
    db_target = plugins / DB_ID
    for t in (kb_target, db_target):
        if t.exists():
            shutil.rmtree(t)
    shutil.copytree(PLUGIN_DIR, kb_target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".vscode", "tests"))
    shutil.copytree(PLUGIN_DIR.parent / DB_ID, db_target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".vscode", "tests"))
    (plugin_pkg / "__init__.py").write_text("", encoding="utf-8")
    (sdk / "__init__.py").write_text("", encoding="utf-8")
    (sdk_plugin / "__init__.py").write_text(STUB_SDK, encoding="utf-8")
    (plugins / "__init__.py").write_text("", encoding="utf-8")


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="neko_kb_stub_"))
    build_shim(tmp)
    sys.path.insert(0, str(tmp))
    storage = tmp / "storage"
    storage.mkdir(parents=True, exist_ok=True)

    import importlib

    from plugin.sdk.plugin import Err, Ok, SdkError  # 桩类型

    InProcessRouter.Ok = Ok
    InProcessRouter.Err = Err
    InProcessRouter.SdkError = SdkError

    db_mod = importlib.import_module(f"plugin.plugins.{DB_ID}")
    kb_mod = importlib.import_module(f"plugin.plugins.{KB_ID}")

    db_ctx = {
        "plugin_id": DB_ID,
        "plugin_dir": tmp / "plugin" / "plugins" / DB_ID,
        "storage_dir": storage / DB_ID,
        "config": {"database": {"driver": "sqlite"}},
    }
    db_plugin = db_mod.PluginDatabasePlugin(db_ctx)
    r = await db_plugin.on_startup()
    check(isinstance(r, Ok), "db startup -> Ok", repr(r))

    router = InProcessRouter(db_plugin)
    kb_ctx = {
        "plugin_id": KB_ID,
        "plugin_dir": tmp / "plugin" / "plugins" / KB_ID,
        "storage_dir": storage / KB_ID,
        "config": {
            "knowledge": {"models_dir": ""},
            "hot_promotion": {"window_days": 7, "threshold": 5, "demotion_days": 30},
        },
        "plugins": router,
    }
    (kb_ctx["storage_dir"] / "knowledge").mkdir(parents=True)
    kdir = kb_ctx["storage_dir"] / "knowledge"
    (kdir / "db.md").write_text(
        "# 数据库事务\n\n事务具有 ACID 特性：原子性、一致性、隔离性、持久性。\n\n"
        "SQLite 支持 WAL 模式，可以提高并发读写性能。\n\n连接池用于复用数据库连接。",
        encoding="utf-8",
    )
    (kdir / "kb.md").write_text(
        "# 知识库\n\n滑动窗口热度追踪会在窗口内命中次数达到阈值时，把知识块提升到热数据表。\n\n"
        "分块策略按段落切分，块大小 512 tokens，重叠 64 tokens。",
        encoding="utf-8",
    )
    (kdir / "misc.md").write_text(
        "# 闲聊\n\n今天天气不错，适合出去散步。\n\n猫娘喜欢小鱼干。",
        encoding="utf-8",
    )

    kb = kb_mod.PluginKnowledgeBasePlugin(kb_ctx)
    r = await kb.on_startup()
    ok_startup = isinstance(r, Ok)
    check(ok_startup, "kb startup -> Ok（含依赖检查/索引初始化/模型加载）", repr(r) if not ok_startup else "")
    if not ok_startup:
        return 1

    r = await kb.kb_model_status()
    check(isinstance(r, Ok) and r.value["ready"], "kb_model_status ready", repr(r))

    r = await kb.kb_import()
    ok_import = isinstance(r, Ok) and r.value["imported"] >= 3
    check(ok_import, "kb_import 3 个 md 文件", repr(r.value if isinstance(r, Ok) else r))

    promoted_seen = False
    for i in range(5):
        r = await kb.kb_search(query="事务的 ACID 特性是什么", top_k=3)
        if not isinstance(r, Ok):
            check(False, f"kb_search #{i+1}", repr(r))
            return 1
        hits = r.value["hits"]
        if any(h.get("promoted") for h in hits):
            promoted_seen = True
    check(bool(hits), "kb_search 5 次返回命中", f"top={hits[0]['source']}" if hits else "no hits")
    check(promoted_seen, "滑动窗口触发提升（promoted=True 出现）")

    r = await kb.kb_hot_list(limit=10)
    check(isinstance(r, Ok) and r.value["count"] >= 1, "kb_hot_list 有记录", repr(r.value if isinstance(r, Ok) else r))

    r = await kb.kb_stats()
    check(isinstance(r, Ok) and r.value["hot_active"] >= 1 and r.value["chunks"] >= 3, "kb_stats", repr(r.value if isinstance(r, Ok) else r))

    r = await kb.kb_hot_export(fmt="md")
    ok_export = isinstance(r, Ok) and Path(r.value["path"]).exists() and r.value["count"] >= 1
    check(ok_export, "kb_hot_export 落盘", repr(r.value if isinstance(r, Ok) else r))

    await kb.kb_record_skill_stat(skill="knowledge-search", duration_ms=8.0, ok=True)
    r = await kb.kb_query_skill_stats()
    check(isinstance(r, Ok) and r.value["rows"] and r.value["rows"][0]["skill"] == "knowledge-search", "skill stats 转发落库", repr(r.value if isinstance(r, Ok) else r))

    r = await kb.kb_search(query="")
    check(isinstance(r, Err), "空查询 -> Err(结构化)")

    r = await kb.kb_demote_sweep()
    check(isinstance(r, Ok), "kb_demote_sweep", repr(r))

    await kb.on_shutdown()
    await db_plugin.on_shutdown()
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
