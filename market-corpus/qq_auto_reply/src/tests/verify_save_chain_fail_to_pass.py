"""fail-to-pass 证据：把 2026-09-23 的生产事故注入回去，确认看门狗会红。

事故原样：

    TypeError: QQDashboardService.save_settings() got an unexpected keyword argument
               'reply_burst_window_seconds'

注入方式用 `__signature__` 模拟"签名少了一个形参"，**不改源文件**。三种注入各自
对应一条不变量：

1. 前端提交了签名里没有的键       → 不变量 1（生产 TypeError）
2. dashboard 收了 schema 没声明的键 → 不变量 2（静默丢弃）
3. saveable 键但签名里没有          → 不变量 3（用户改不了）

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_save_chain_fail_to_pass.py
"""

from __future__ import annotations

import inspect
import sys
from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # N.E.K.O 仓库根

# 用 import_module 而不是 `from … import …`：本文件必须先改 sys.path 再导入插件，
# 而模块级 import 语句排在这些赋值/调用之后就是 E402（ruff 只豁免"前面全是导入与
# sys.path 操作"的情形，本文件前面还有 ROOT/PLUGIN 两个赋值，所以会被报）。
# CI 上那条 gate 带 `--ignore-noqa`，抑制在那里不作数 —— 于是从**构造上**避开。
QQDashboardService = import_module(
    "plugin.plugins.qq_auto_reply.dashboard_service"
).QQDashboardService

TEST_FILE = str(Path(__file__).resolve().parent / "test_qq_settings_save_chain.py")
_ORIGINAL = QQDashboardService.save_settings
#: 未被污染的签名快照。必须在这里就取好：`QQDashboardService.save_settings` 与
#: `_ORIGINAL` 是**同一个函数对象**，一旦设了 `__signature__`，
#: `inspect.signature()` 之后返回的就是被改过的版本 —— 基于它再构造下一轮注入
#: 会重复添加同名参数，直接 ValueError 把脚本带走（表现是只跑完第一项就退出，
#: 看起来却像成功）。
_PRISTINE = inspect.signature(_ORIGINAL)


def _with_signature(drop: tuple[str, ...] = (), add: tuple[str, ...] = ()):
    """构造一个去掉/添加了若干形参的签名（始终基于原始快照）。"""
    params = [p for name, p in _PRISTINE.parameters.items() if name not in drop]
    params.extend(
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None)
        for name in add
    )
    return inspect.Signature(params)


def _run() -> tuple[int, str]:
    # 不能加 `-p no:randomly`：pytest.ini 的 addopts 里带着固定的
    # `--randomly-seed=20260731`，禁用插件会让那个参数变成未识别 → pytest
    # 以 exit=4（用法错误）退出。这曾经让"注入的 bug 被抓住"变成假阳性：
    # exit != 0 并不等于测试失败。
    #
    # pytest.main 在**同一进程里第二次**调用时会抛 SystemExit，直接带走本脚本
    # —— 表现是只打印了第一项就退出，后面的项根本没跑（比假阳性更糟：看起来
    # 像成功了）。所以这里必须接住它。
    try:
        return int(pytest.main([TEST_FILE, "-q", "--no-header"])), ""
    except SystemExit as exc:  # noqa: PERF203 - 见上
        return int(exc.code or 0), ""


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    for label, drop, add, should_fail in [
        (
            "前端提交 reply_burst_window_seconds 但签名没有（生产事故原位）",
            ("reply_burst_window_seconds",),
            (),
            True,
        ),
        (
            "dashboard 收了一个 schema 没声明的键",
            (),
            ("attention_not_a_real_key",),
            True,
        ),
        (
            "签名完好（对照：必须绿）",
            (),
            (),
            False,
        ),
    ]:
        # 只设一次：类属性和 `_ORIGINAL` 是同一个函数对象
        _ORIGINAL.__signature__ = _with_signature(drop, add)
        code, _ = _run()
        if code == 4:
            print(f"[MISS] pytest 用法错误(exit=4)，本项结论无效: {label}")
            results.append((label, False, "pytest usage error"))
            continue
        failed = code != 0
        ok = failed == should_fail
        results.append((label, ok, f"exit={code} 期望{'红' if should_fail else '绿'}"))
        print(f"[{'OK  ' if ok else 'MISS'}] {label}: exit={code}")

    delattr(_ORIGINAL, "__signature__")

    # ── 第 4 项：情绪倍率表(JSON) 的静默丢弃 ──────────────────────────
    # 这条修在内联字符串里（`napka.html` 的 doSave），签名注入够不到，只能起新进程
    # 做文件级注入：`pytest.main` 同进程不重新导入模块，改了文件也不会生效。
    import subprocess

    html = PLUGIN / "static" / "napcat.html"
    source = html.read_text(encoding="utf-8")
    old_entry = (
        "attention_emotion_multipliers:(function(){try{return JSON.parse("
        "document.getElementById('cfg-att-emotion-multipliers').value)}"
        "catch(e){return undefined}})(),"
    )
    new_entry = "attention_emotion_multipliers:emoParsed,"
    label = "还原：JSON 打错被吞成 undefined（静默丢弃 + 假成功）"
    if new_entry not in source:
        results.append((label, False, "找不到待替换文本"))
        print(f"[MISS] {label}: 找不到待替换文本，本项结论无效")
    else:
        try:
            html.write_text(source.replace(new_entry, old_entry, 1), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", TEST_FILE, "-q", "--no-header"],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            code = proc.returncode
        finally:
            html.write_text(source, encoding="utf-8")
        restored = html.read_text(encoding="utf-8") == source
        ok = code == 1 and restored
        results.append((label, ok, f"exit={code} 期望红"))
        print(f"[{'OK  ' if ok else 'MISS'}] {label}: exit={code}  源码已恢复={restored}")

    # ── 第 5 项：把宿主注入的信封键（`_ctx`）重新算成"不可识别的键" ──────
    # 真机 17:59:37 那条警告就是这么来的：宿主每次都塞 `_ctx`，于是每次保存都报一条，
    # 噪音正好把真正的手滑键名淹掉。还原它 → 那条看门狗必须红。
    entry_py = PLUGIN / "__init__.py"
    entry_source = entry_py.read_text(encoding="utf-8")
    envelope_aware = (
        '        dropped = sorted(\n'
        '            k for k in kw\n'
        '            if k not in self._CONFIG_SAVE_KEYS and k != "action" and not k.startswith("_")\n'
        '        )'
    )
    envelope_blind = (
        '        dropped = sorted(\n'
        '            k for k in kw\n'
        '            if k not in self._CONFIG_SAVE_KEYS and k != "action"\n'
        '        )'
    )
    label = "还原：宿主的 `_ctx` 信封又被算成「不可识别的键」（每次保存都报警）"
    if envelope_aware not in entry_source:
        results.append((label, False, "找不到待替换文本"))
        print(f"[MISS] {label}: 找不到待替换文本，本项结论无效")
    else:
        try:
            entry_py.write_text(entry_source.replace(envelope_aware, envelope_blind, 1), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", TEST_FILE, "-q", "--no-header"],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            code = proc.returncode
        finally:
            entry_py.write_text(entry_source, encoding="utf-8")
        restored = entry_py.read_text(encoding="utf-8") == entry_source
        ok = code == 1 and restored
        results.append((label, ok, f"exit={code} 期望红"))
        print(f"[{'OK  ' if ok else 'MISS'}] {label}: exit={code}  源码已恢复={restored}")

    print()
    missed = [name for name, ok, _ in results if not ok]
    if missed:
        print(f"[FAIL] {len(missed)} 项行为不符合预期，看门狗可能抓不住真实事故：")
        for name in missed:
            print(f"   - {name}")
        return 1
    print(f"[PASS] {len(results)} 项注入行为全部符合预期 —— 看门狗确实能抓住这类断裂")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
