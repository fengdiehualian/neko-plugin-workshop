"""fail-to-pass 证据：把「接续摘要」的必要条件逐个拆掉，确认看门狗会红。

拆的六种情况，每一种都对应一个真实坏结果：

1. 会话消失时不捕获 → 下一句还是割裂（功能等于没做）
2. 注入后不消费 → 同一段摘要在之后每个新会话里反复出现（她会一直"上次聊到…"）
3. 连接失败也消费 → 一次超时把这段上下文永久吃掉
4. 未授权的会话也留摘要 → 把没授权落库的对话原文写到磁盘
5. 不看角色就注入 → 换人格后把上个角色的临场上下文交给新角色
6. 不看过期就注入 → 半小时前的"刚才"被当成刚才

铁律（沿用 verify_free_route_persona_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_session_handoff_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_session_handoff.py"),
    str(TESTS / "test_qq_session_handoff_wiring.py"),
]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "session_runtime_service.py",
        "会话消失时不捕获摘要（下一句还是割裂）",
        """        handoff = getattr(self.plugin, "session_handoff_service", None)
        if handoff is not None and user_data is not None:
            try:
                handoff.capture(session_key, user_data)""",
        """        handoff = None
        if handoff is not None and user_data is not None:
            try:
                handoff.capture(session_key, user_data)""",
    ),
    (
        "session_bootstrap_service.py",
        "注入后不消费（摘要会一直重复出现）",
        """            if handoff is not None and handoff_section:
                # 连上了**并且真的注入了**才消费：连不上会把整轮丢掉重试；而
                # 没注入（换了角色 / 过期）时更要留着 —— 换回来它还是那个角色
                # 自己的上下文。
                try:""",
        """            if False:
                try:""",
    ),
    (
        "session_bootstrap_service.py",
        "连接失败也消费（一次超时吃掉上下文）",
        """            await asyncio.wait_for(
                user_session.connect(instructions=instructions),
                timeout=self.plugin._ai_connect_timeout_seconds,
            )
            if handoff is not None and handoff_section:""",
        """            if handoff is not None and handoff_section:
                handoff.consume(session_key)
            await asyncio.wait_for(
                user_session.connect(instructions=instructions),
                timeout=self.plugin._ai_connect_timeout_seconds,
            )
            if False:""",
    ),
    (
        "session_handoff_service.py",
        "未授权的会话也留摘要（把没授权的原文写到磁盘）",
        """        if not user_data.get("memory_enabled"):""",
        """        if False:""",
    ),
    (
        "session_handoff_service.py",
        "不看角色就注入（把上个角色的上下文交给新角色）",
        """        if her_name is not None and stored_name and str(her_name) != stored_name:""",
        """        if False:""",
    ),
    (
        "session_handoff_service.py",
        "不看过期就注入（半小时前的\"刚才\"）",
        """        if time.time() - float(note.get("at") or 0.0) > TTL_SECONDS:""",
        """        if False:""",
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
    results.append(("对照（接续摘要在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 接续摘要的每处必要条件都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
