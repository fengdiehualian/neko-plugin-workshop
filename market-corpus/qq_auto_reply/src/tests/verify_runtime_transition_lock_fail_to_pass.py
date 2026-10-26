"""fail-to-pass 证据：运行时启停互斥的**必要条件**逐个拆掉，看门狗必须红。

钉的是 2026-09-27 00:18:53 那条 ``AttributeError: 'NoneType' object has no attribute
'get'``：宿主 ``connect()`` 卡在 token 请求上时，另一个入口并发 ``disconnect()`` 把
``_http`` 置空。四处闸门（服务三个入口 + 插件的收尾重建）逐一失效，都要能被看见。

铁律（沿用 verify_open_platform_media_fail_to_pass.py）：
1. **先确认目标用例在干净树上绿** —— 否则"红"可能是别的原因造成的假证据；
2. 替换前确认锚点唯一、且替换真的发生；
3. 恢复放 ``finally`` 并逐字节核对；
4. 每次变异都跑一遍**控制组**（无关用例必须照样绿）。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_runtime_transition_lock_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TARGET_FILE = str(TESTS / "test_qq_runtime_transition_lock.py")
#: 控制组：与启停互斥无关，任何变异都不该影响它。
CONTROL_FILE = str(TESTS / "test_qq_permission_levels.py")

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "runtime_ops_service.py",
        "启动入口不再持闸（stop 又能插进 connect 中间）",
        """    async def start_auto_reply(self):
        async with runtime_transition_guard(self.plugin).hold("启动自动回复"):
            return await self._start_auto_reply_locked()""",
        """    async def start_auto_reply(self):
        return await self._start_auto_reply_locked()""",
    ),
    (
        "runtime_ops_service.py",
        "停止入口不再持闸（在飞的启动能被它腰斩）",
        """    async def stop_runtime(self, *, stop_napcat: bool):
        async with runtime_transition_guard(self.plugin).hold("停止运行时"):
            await self._stop_runtime_locked(stop_napcat=stop_napcat)""",
        """    async def stop_runtime(self, *, stop_napcat: bool):
        await self._stop_runtime_locked(stop_napcat=stop_napcat)""",
    ),
    (
        "runtime_transition.py",
        "闸门永远当自己是重入（等于没锁）",
        """        if self._depth and self._owner is task:""",
        """        if True:""",
    ),
    (
        "__init__.py",
        "收尾重建不再持闸（停→丢对象→启 敞着做）",
        """        async with runtime_transition_guard(self).hold("收尾重建运行时"):""",
        """        if True:""",
    ),
]


def _adapt(source: str, anchor: str) -> str:
    """把按 LF 写的锚点适配到目标文件的换行风格（本仓库 CRLF 居多）。"""
    if "\r\n" in source and "\r\n" not in anchor:
        return anchor.replace("\n", "\r\n")
    return anchor


def _run(*test_files: str) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *test_files, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    results: list[tuple[str, bool]] = []
    sources = {rel: (PLUGIN / rel).read_text(encoding="utf-8")
               for rel, _l, _o, _w in MUTATIONS}

    baseline = _run(TARGET_FILE)
    if baseline != 0:
        print(f"[FAIL] 目标用例在干净树上就是红的（exit={baseline}）—— 本次取证无效，先修它")
        return 1
    print(f"[OK  ] 目标用例在干净树上绿（exit={baseline}）")

    for rel, label, old_raw, new_raw in MUTATIONS:
        source = sources[rel]
        old, new = _adapt(source, old_raw), _adapt(source, new_raw)
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old_raw[:60]!r}")
            results.append((f"{rel}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((f"{rel}: {label}", False))
            continue

        target_code = control_code = -1
        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            target_code = _run(TARGET_FILE)
            control_code = _run(CONTROL_FILE)
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = target_code != 0 and control_code == 0 and restored
        results.append((f"{rel}: {label}", ok))
        print(
            f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: "
            f"目标 exit={target_code}（期望非 0）；控制组 exit={control_code}（期望 0）；已恢复={restored}"
        )

    code_clean = _run(TARGET_FILE)
    control_ok = code_clean == 0
    results.append(("对照（闸门都在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 四处闸门都是必要条件，拆哪一处看门狗都会红")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
