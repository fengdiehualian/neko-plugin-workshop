#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plugin_database 入口层的端到端验证（桩 SDK，参照 neko_docs/tests/stub_run.py）。

宿主只在 N.E.K.O 进程内提供 ``plugin.sdk.plugin``。这里搭一棵 shim 包树：

    <tmp>/shim/plugin/__init__.py                  (空)
    <tmp>/shim/plugin/sdk/__init__.py              (空)
    <tmp>/shim/plugin/sdk/plugin/__init__.py       桩 SDK
    <tmp>/shim/plugin/plugins/plugin_database/     真实插件源码副本

判据：
  * startup/shutdown 生命周期返回 Ok
  * db_execute / db_fetch_all / db_fetch_one 走 sqlite 真实读写
  * register_hot_schema -> hot_promote -> hot_query -> hot_demote 全链路
  * skill_stat_record / skill_stat_query 落库
  * db_switch_driver 对坏 DSN 返回 Err（结构化错误），恢复 sqlite 后继续可用

用法： 任意 Python 3.11+: python tests\\stub_run.py
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
PLUGIN_ID = "plugin_database"
RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label, detail))
    print(("[PASS] " if ok else "[FAIL] ") + label + (f"  -> {detail}" if detail else ""), flush=True)


STUB_SDK = '''\
"""桩 plugin.sdk.plugin —— 宿主之外驱动插件源码用。"""
from __future__ import annotations


class SdkError(Exception):
    pass


class Ok:
    def __init__(self, value=None):
        self.value = value
        self.error = None

    def __repr__(self):
        return f"Ok({self.value!r})"


class Err:
    def __init__(self, error):
        self.error = error
        self.value = None

    def __repr__(self):
        return f"Err({self.error!r})"


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

    @property
    def config(self):
        return self._config

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


def build_shim(root: Path) -> Path:
    plugin_pkg = root / "plugin"
    sdk = plugin_pkg / "sdk"
    sdk_plugin = sdk / "plugin"
    plugins = plugin_pkg / "plugins"
    target = plugins / PLUGIN_ID
    sdk_plugin.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    # 缓存目录必须排除：它们的 ACL 在部分环境（含本机沙箱）拒绝列举，会让
    # copytree 直接失败。
    shutil.copytree(
        PLUGIN_DIR,
        target,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".vscode", ".pytest_cache", ".ruff_cache"
        ),
    )
    (plugin_pkg / "__init__.py").write_text("", encoding="utf-8")
    (sdk / "__init__.py").write_text("", encoding="utf-8")
    (sdk_plugin / "__init__.py").write_text(STUB_SDK, encoding="utf-8")
    (plugins / "__init__.py").write_text("", encoding="utf-8")
    return root


def make_temp_dir(prefix: str) -> Path:
    """建一个唯一临时目录。

    不用 ``tempfile.mkdtemp()``：在受限 ACL 环境（本机 DSH 沙箱）下它产出的目录
    立即不可访问（listdir/mkdir 全 WinError 3/5），而 ``os.makedirs()`` 在同一
    父目录完全正常。这里用 makedirs + uuid 复刻 mkdtemp 的语义，两个环境都能跑。
    """
    import uuid

    base = Path(tempfile.gettempdir())
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = HERE
    for _ in range(50):
        cand = base / f"{prefix}{uuid.uuid4().hex[:8]}"
        try:
            cand.mkdir()
            return cand
        except FileExistsError:
            continue
    raise RuntimeError("could not create a temp dir")


async def main() -> int:
    tmp = make_temp_dir("neko_db_stub_")
    build_shim(tmp)
    sys.path.insert(0, str(tmp))
    storage = tmp / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    ctx = {
        "plugin_id": PLUGIN_ID,
        "plugin_dir": tmp / "plugin" / "plugins" / PLUGIN_ID,
        "storage_dir": storage,
        "config": {"database": {"driver": "sqlite"}},
    }

    import importlib

    from plugin.sdk.plugin import Err, Ok  # noqa: F401  (桩 SDK 的结果类型)

    mod = importlib.import_module(f"plugin.plugins.{PLUGIN_ID}")
    plugin = mod.PluginDatabasePlugin(ctx)

    r = await plugin.on_startup()
    check(isinstance(r, Ok), "startup -> Ok", repr(r))
    r = await plugin.db_status()
    check(isinstance(r, Ok) and r.value["driver"] == "sqlite", "db_status sqlite", repr(r))

    r = await plugin.db_execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    check(isinstance(r, Ok), "db_execute DDL", repr(r))
    r = await plugin.db_execute("INSERT INTO t (v) VALUES (?)", ["hello"])
    check(isinstance(r, Ok) and r.value["rowcount"] == 1, "db_execute INSERT", repr(r))
    r = await plugin.db_fetch_all("SELECT * FROM t", [])
    check(isinstance(r, Ok) and r.value["count"] == 1 and r.value["rows"][0]["v"] == "hello", "db_fetch_all", repr(r))
    r = await plugin.db_fetch_one("SELECT v FROM t WHERE id = ?", [1])
    check(isinstance(r, Ok) and r.value["row"]["v"] == "hello", "db_fetch_one", repr(r))

    r = await plugin.register_hot_schema()
    check(isinstance(r, Ok) and r.value["table"] == "hot_knowledge", "register_hot_schema", repr(r))
    r = await plugin.hot_promote(
        content_hash="abc123",
        content="高频知识内容",
        embedding=[0.1, 0.2, 0.3],
        hit_count=5,
        first_hit_at="2026-09-26T10:00:00",
        last_hit_at="2026-09-26T10:05:00",
    )
    check(isinstance(r, Ok), "hot_promote", repr(r))
    r = await plugin.hot_query(limit=10)
    check(isinstance(r, Ok) and r.value["count"] == 1, "hot_query", repr(r.value if isinstance(r, Ok) else r))
    r = await plugin.hot_demote(demotion_days=0)
    check(isinstance(r, Ok) and r.value["demoted"] == 1, "hot_demote", repr(r))
    r = await plugin.hot_query(limit=10)
    check(isinstance(r, Ok) and r.value["count"] == 0, "hot_query excludes demoted", repr(r.value if isinstance(r, Ok) else r))

    await plugin.skill_stat_record(skill="knowledge-search", duration_ms=12.5, ok=True)
    await plugin.skill_stat_record(skill="knowledge-search", duration_ms=20.0, ok=False)
    r = await plugin.skill_stat_query()
    ok_row = isinstance(r, Ok) and r.value["count"] == 1 and r.value["rows"][0]["calls"] == 2
    check(ok_row, "skill stats", repr(r.value if isinstance(r, Ok) else r))

    r = await plugin.db_switch_driver({"driver": "postgres", "postgres": {"dsn": "postgresql://u:p@127.0.0.1:1/none"}})
    check(isinstance(r, Err), "switch to unreachable driver -> Err(结构化)", repr(r))
    r = await plugin.db_switch_driver({"driver": "sqlite", "sqlite": {"path": str(storage / "plugin_database.db")}})
    check(isinstance(r, Ok) and r.value["driver"] == "sqlite", "switch back to sqlite", repr(r))
    r = await plugin.db_fetch_one("SELECT 1 AS x", [])
    check(isinstance(r, Ok) and r.value["row"]["x"] == 1, "usable after switch-back", repr(r))

    r = await plugin.on_shutdown()
    check(isinstance(r, Ok), "shutdown -> Ok", repr(r))

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
