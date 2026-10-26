"""fail-to-pass 证据：把"免费线请求必须带本体人设"的必要条件逐个拆掉，确认看门狗会红。

拆的八种情况，每一种都对应一个真实的坏结果：

1. 免费线也不带 system → 请求被免费端 400，表情包自动描述又变回"点了没反应"
2. 自配 API 也塞人设 → 白付 3k 字符的 token（付费线上没有任何收益）
3. 人设取不到就整条放弃（连图都不发）→ 一个可恢复的取配置失败变成功能全灭
4. 用硬编码的标志句代替本体人设 → 本体一改人设，插件就开始 400 且没人会想到来看
5. 人设不替换占位符 → 模型看到字面量 `{LANLAN_NAME}`，描述会跑偏
6. XML 修复不带人设 → 这条 `except: pass` 的修复路径永远不生效
7. "我在听"那条闸不带人设 → 永远不响
8. 缓冲总结不带人设 → 连发多条后的总结回复永远出不来

铁律（沿用 verify_sticker_auto_desc_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_free_route_persona_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_free_route_persona.py"),
]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "__init__.py",
        "免费线也不带 system（请求被免费端 400）",
        """            system_prompt = self._free_route_system_prompt(model_config)
            messages: list[dict[str, Any]] = []
            if system_prompt:""",
        """            system_prompt = ""
            messages: list[dict[str, Any]] = []
            if system_prompt:""",
    ),
    (
        "__init__.py",
        "自配 API 也塞人设（白付 token）",
        """        base_url = str(model_config.get("base_url") or "").lower()
        if FREE_ROUTE_HOST_HINT not in base_url:
            return \"\"""",
        """        base_url = str(model_config.get("base_url") or "").lower()
        if False:
            return \"\"""",
    ),
    (
        "__init__.py",
        "人设取不到就整条放弃（连带图都不发）",
        """            messages: list[dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})""",
        """            if not system_prompt:
                return ""
            messages: list[dict[str, Any]] = []
            messages.append({"role": "system", "content": system_prompt})""",
    ),
    (
        "__init__.py",
        "用硬编码标志句代替本体人设（人设一改就 400）",
        """            from .session_instruction_service import _apply_role_placeholders

            return _apply_role_placeholders(
                persona, lanlan_name=her_name, master_name=master_name,
            ) or \"\"""",
        """            return "The system periodically sends some useful information\"""",
    ),
    (
        "__init__.py",
        "人设不替换占位符（模型看到 {LANLAN_NAME}）",
        """            from .session_instruction_service import _apply_role_placeholders

            return _apply_role_placeholders(
                persona, lanlan_name=her_name, master_name=master_name,
            ) or \"\"""",
        """            return persona""",
    ),
    (
        "reply_postprocess_node.py",
        "XML 修复不带人设（这条 except: pass 的路径永远不生效）",
        """            messages: list[dict[str, Any]] = []
            free_system = self.plugin._free_route_system_prompt(model_config)
            if free_system:
                messages.append({"role": "system", "content": free_system})""",
        """            messages: list[dict[str, Any]] = []""",
    ),
    (
        "reply_buffer_service.py",
        "「我在听」那条闸不带人设（永远不响）",
        """            messages: list[dict[str, Any]] = []
            free_system = self.plugin._free_route_system_prompt(model_config)
            if free_system:
                messages.append({"role": "system", "content": free_system})""",
        """            messages: list[dict[str, Any]] = []""",
    ),
    (
        "reply_buffer_service.py",
        "缓冲总结不带人设（连发多条后的总结永远出不来）",
        """            free_system = self.plugin._free_route_system_prompt(_mc)
            if free_system:
                await client.connect(instructions=free_system)""",
        """            pass""",
    ),
]


def _run_pytest() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *TEST_FILES, "-q", "--no-header"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode


def main() -> int:
    results: list[tuple[str, bool]] = []
    sources = {rel: (PLUGIN / rel).read_text(encoding="utf-8")
               for rel, _l, _o, _w in MUTATIONS}

    for rel, label, old, new in MUTATIONS:
        source = sources[rel]
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((f"{rel}: {label}", False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((f"{rel}: {label}", False))
            continue

        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = code == 1 and restored
        results.append((f"{rel}: {label}", ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: exit={code}（期望 1）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（修复在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 免费线看图的每处必要条件都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
