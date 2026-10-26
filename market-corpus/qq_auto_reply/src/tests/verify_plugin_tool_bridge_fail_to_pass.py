"""fail-to-pass 证据：插件工具桥**四道闸**逐个拆掉，看门狗必须红。

四道闸，每一道都对应一个真实的坏结果：

1. **通道闸**（只在开放平台挂）→ 拆掉：NapCat 那边"群里什么样的人都有"，也会拿到
   别的插件的工具面；
2. **启动闸**（只带已启动的插件）→ 拆掉：没启动的插件也进提示词，模型点到只会拿到错误；
3. **非 QQ 闸**（排除自己与 qq* 家族）→ 拆掉：让她"通过调用别的插件"再回到 QQ 发送
   链路，自指；
4. **权限闸**（all 给名册内的人、admin 只给管理员、认不出来的人什么都不给）→ 拆掉：
   陌生人 @ 一下就能指挥插件（本体里有米家、点歌、Minecraft 这些真能干事的）。

外加一条不是闸但要命的：**handler 只认本轮挂上去的 entry 清单** —— 拆掉它，
模型可以自己编一个 entry id 透传给插件。

铁律（沿用 verify_session_handoff_fail_to_pass.py）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_plugin_tool_bridge_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [str(TESTS / "test_qq_plugin_tool_bridge.py")]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "reply_generation_service.py",
        "拆掉通道闸（NapCat 上也会挂别的插件的工具）",
        """        client = getattr(self.plugin, "qq_client", None)
        if client is None or not media_seam.is_open_platform(client):
            return [], []""",
        """        client = getattr(self.plugin, "qq_client", None)
        if client is None:
            return [], []""",
    ),
    (
        "plugin_tool_service.py",
        "拆掉启动闸（没启动的插件也进提示词 —— 界面照旧全量列）",
        """            if row is None or not row.get("running"):
                continue""",
        """            if row is None:
                continue""",
    ),
    (
        "plugin_tool_service.py",
        "拆掉非 QQ 闸（自己与 qq* 家族也进候选，自指）",
        """    @staticmethod
    def _is_excluded(plugin_id: str) -> bool:
        \"\"\"QQ 家族（含自己）不进候选：让她"通过调用别的插件"再回到 QQ 发送链路是自指。\"\"\"
        lowered = str(plugin_id or "").strip().lower()
        return any(lowered.startswith(prefix) for prefix in EXCLUDED_ID_PREFIXES)""",
        """    @staticmethod
    def _is_excluded(plugin_id: str) -> bool:
        return False""",
    ),
    (
        "plugin_tool_service.py",
        "拆掉权限闸（认不出来的人也拿到 all 档工具）",
        """        if level in ("trusted", "normal"):
            return {TIER_ALL}
        return set()""",
        """        return {TIER_ALL}""",
    ),
    (
        "plugin_tool_service.py",
        "admin 档也对普通人开放（分级形同虚设）",
        """        if level == "admin":
            return {TIER_ALL, TIER_ADMIN}""",
        """        if level in ("admin", "trusted", "normal"):
            return {TIER_ALL, TIER_ADMIN}""",
    ),
    (
        "plugin_tool_service.py",
        "handler 不再校验 entry 是否在本轮清单里（模型可自行编造）",
        """            if entry_id not in entries:""",
        """            if False:""",
    ),
    (
        "plugin_tool_service.py",
        "结果不再脱敏（别的插件的 key 尾号/密钥形状会进她的嘴）",
        """        if isinstance(payload, dict):
            payload = redact_payload(payload)""",
        """        if False:
            payload = redact_payload(payload)""",
    ),
    (
        "plugin_tool_service.py",
        "文本里的密钥形状不再掩（`****149a` / `sk-…` / `Bearer …` 照样念出去）",
        """        text = redact_text(str(text or "").strip())""",
        """        text = str(text or "").strip()""",
    ),
    (
        "plugin_tool_service.py",
        "失败时不再嘱咐她别说原文（她会把内部错误念给群里听）",
        """            text = "调用失败：" + text + _FAILURE_MANNER_HINT""",
        """            text = "调用失败：" + text""",
    ),
    (
        "plugin_tool_service.py",
        "工具描述里不再禁止「没调用就说已经提交了」（她又可以空口承诺）",
        """        lines.append(
            "⚠️ 只有**真的调用本工具并拿到结果**之后，才能对使用者说「我已经提交了 / 我去办」"
            "这类话。没调用、或调用失败时，不许这么说。"
        )""",
        "        pass",
    ),
    (
        "plugin_tool_service.py",
        "界面那条路退回「按年龄算新鲜」的缓存（刚启动的插件最多 60 秒仍显示未启动）",
        """        try:
            return await asyncio.wait_for(self.refresh_candidates(), timeout=max(0.1, timeout))""",
        """        try:
            return await asyncio.wait_for(self.wait_for_a_fresh_cache(timeout), timeout=max(0.1, timeout))""",
    ),
    (
        "__init__.py",
        "界面查询改回读缓存（同上：卡片状态会滞后）",
        """        candidates = await service.refresh_for_ui()""",
        """        candidates = await service.list_candidates(refresh=True)""",
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
    sources = {
        rel: (PLUGIN / rel).read_text(encoding="utf-8")
        for rel, _l, _o, _w in MUTATIONS
    }

    for rel, label, old, new in MUTATIONS:
        source = sources[rel]
        count = source.count(old)
        if count != 1:
            print(f"[MISS] {rel}: 锚点出现 {count} 次（期望 1 次），本项结论无效: {old[:60]!r}")
            results.append((label, False))
            continue
        mutated = source.replace(old, new, 1)
        if mutated == source:
            print(f"[MISS] {rel}: 替换没有实际发生")
            results.append((label, False))
            continue

        try:
            (PLUGIN / rel).write_text(mutated, encoding="utf-8")
            code = _run_pytest()
        finally:
            (PLUGIN / rel).write_text(source, encoding="utf-8")

        restored = (PLUGIN / rel).read_text(encoding="utf-8") == source
        ok = code != 0 and restored
        results.append((label, ok))
        print(f"[{'OK  ' if ok else 'MISS'}] {rel} — {label}: exit={code}（期望非 0）；已恢复={restored}")

    code_clean = _run_pytest()
    control_ok = code_clean == 0
    results.append(("对照（四道闸都在位必须绿）", control_ok))
    print(f"[{'OK  ' if control_ok else 'MISS'}] 对照: exit={code_clean}（期望 0）")

    missed = [n for n, good in results if not good]
    print()
    if missed:
        print(f"[FAIL] {len(missed)} 项不符合预期: {missed}")
        return 1
    print(f"[PASS] {len(results)}/{len(results)} —— 四道闸与 entry 白名单都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
