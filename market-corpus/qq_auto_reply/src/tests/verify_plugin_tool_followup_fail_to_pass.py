"""fail-to-pass 证据：异步结果回投的**每一道闸、每一条"不许撒谎"**逐个拆掉，看门狗必须红。

这个功能的风险全在"她**主动**说话"上，所以拆任何一条都不该是"少个功能"，而是：

* 拆开关闸 → 使用者关了它还会发；
* 拆值班闸 → 值班停了还主动往外发消息（越权）；
* 拆通道闸 → NapCat 那条通道上也主动说话（与工具桥同一条范围约定）；
* 拆会话上限 / 总数上限 → 一次涌出好几条，还会互相挤掉；
* 投递后不出队 → 同一条结果反复说；
* 把"跟丢了"说成成功、把"失败"说成成功、说完成却没正文也当成功 → **她开始编**；
* 提示词不再禁止她提"插件/系统/task_id" → 她开始把内部词念给使用者听；
* 回投那一轮也挂插件工具 → 一个异步结果能生出下一个异步任务，跑成环；
* 不校验 poller 就登记 → 承诺了一句永远没人兑现的话。

铁律（沿用 `verify_plugin_tool_bridge_fail_to_pass.py`）：替换前确认替换真的发生；
恢复放 `finally` 并逐字节核对；每个用例起新进程。

手动运行（不参与 pytest 收集）：
    python plugin/plugins/qq_auto_reply/tests/verify_plugin_tool_followup_fail_to_pass.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # N.E.K.O 仓库根
PLUGIN = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent

TEST_FILES = [
    str(TESTS / "test_qq_plugin_tool_followup.py"),
    str(TESTS / "test_qq_source_kind_sets.py"),
]

#: (相对路径, 说明, 原文, 替换成)
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "plugin_tool_followup_service.py",
        "拆掉开关闸（使用者关掉了也照样登记、照样发）",
        '        return bool(settings.get("qq_open_plugin_followup_enabled", True))',
        "        return True",
    ),
    (
        "plugin_tool_followup_service.py",
        "拆掉值班闸（监听都停了还主动往外发消息）",
        """        if not bool(getattr(self.plugin, "_running", False)):
            return False, "值班已停\"""",
        "",
    ),
    (
        "plugin_tool_followup_service.py",
        "拆掉通道闸（NapCat 通道上也主动说话）",
        """        if client is None or not media_seam.is_open_platform(client):
            return False, "当前不是开放平台通道\"""",
        """        if client is None:
            return False, "当前不是开放平台通道\"""",
    ),
    (
        "plugin_tool_followup_service.py",
        "拆掉「同一会话只等一件」（一次涌出好几条）",
        "        if self._conversation_pending(convo) >= MAX_PENDING_PER_CONVERSATION:",
        "        if False:",
    ),
    (
        "plugin_tool_followup_service.py",
        "拆掉总数上限（同时在等的任务无限涨）",
        "        if len(self._pending) >= MAX_PENDING:",
        "        if False:",
    ),
    (
        "plugin_tool_followup_service.py",
        "投递成功后不出队（同一条结果反复说）",
        """        if delivered:
            self._pending.pop(key, None)
            self._save()
            return""",
        """        if delivered:
            self._save()
            return""",
    ),
    (
        "plugin_tool_followup_service.py",
        "「说完成却没正文」也当成功（她会开始编）",
        """            if not record["output"]:
                # 说是完成了却没有正文：不能编，按"跟丢了"处理。
                self._mark_terminal(record, "lost")
            else:
                self._mark_terminal(record, "done")""",
        """            self._mark_terminal(record, "done")""",
    ),
    (
        "plugin_tool_followup_service.py",
        "失败的任务被说成成功",
        """            record["output"] = extract_result(payload) or _error_text(payload)
            self._mark_terminal(record, "failed")""",
        """            record["output"] = extract_result(payload) or _error_text(payload)
            self._mark_terminal(record, "done")""",
    ),
    (
        "plugin_tool_followup_service.py",
        "查不到的任务被说成成功（把「跟丢了」判成 done）",
        """                self._mark_terminal(record, "lost")
                await self._settle(key, record)
                return
            self._save()""",
        """                self._mark_terminal(record, "done")
                await self._settle(key, record)
                return
            self._save()""",
    ),
    (
        "plugin_tool_followup_service.py",
        "提示词不再禁止她提「插件/系统/task_id」",
        '                "不要提到插件、系统、后台、异步任务、task_id 这类词；"\n',
        "",
    ),
    (
        "plugin_tool_followup_service.py",
        "回投的 prompt 里不带结果正文（她只能瞎说）",
        '        output = str(record.get("output") or "").strip() or "（没有拿到具体内容）"',
        '        output = "（略）"',
    ),
    (
        "plugin_tool_followup_service.py",
        "群里回投不带群 id（结果投不回原来那个群）",
        '            group_id=str(convo.get("group_id") or "") if is_group else None,',
        "            group_id=None,",
    ),
    (
        "plugin_tool_followup_service.py",
        "登记时不校验 poller（承诺一句永远没人兑现的话）",
        '        if not probe["plugin_id"] or not probe["task_id"] or not probe["poller"]:',
        '        if not probe["plugin_id"] or not probe["task_id"]:',
    ),
    (
        "plugin_tool_followup_service.py",
        "拆掉「查得到进度才登记」（排程任务那类假阳性也被登记，过一会儿报假警报）",
        """        if not await self.probe_is_watchable(probe):""",
        """        if False:""",
    ),
    (
        "reply_generation_service.py",
        "回投那一轮也挂插件工具（一个结果生出下一个任务，跑成环）",
        """        if str(getattr(context, "source_kind", "") or "") == KIND_PLUGIN_TOOL_RESULT:""",
        """        if False:""",
    ),
    (
        "plugin_tool_followup_service.py",
        "「正要发」的记号不落盘（发出去了但没记账 → 重启后重发一遍）",
        """        record["delivering_at"] = time.time()
        self._save()""",
        """        record["delivering_at"] = time.time()""",
    ),
    (
        "plugin_tool_followup_service.py",
        "重启后把「停在正要发」的那条重新投一遍（重复消息）",
        """            if row.get("delivering_at"):""",
        """            if False:""",
    ),
    (
        "plugin_tool_followup_service.py",
        "只看有没有生成出文字就算送达（生成了没送到也算兑现了）",
        """    result = getattr(outcome, "delivery_result", None)
    delivered = getattr(result, "delivered", None)
    reply_text = getattr(outcome, "reply_text", None)
    if delivered is None:
        return bool(reply_text), ("没生成出可发送的内容" if not reply_text else "已发出")
    if bool(delivered):
        return True, "已发出"
    if not reply_text:
        return False, "没生成出可发送的内容"
    return False, "生成了但投递没成功（delivered=False）\"""",
        """    reply_text = getattr(outcome, "reply_text", None)
    return bool(reply_text), ("没生成出可发送的内容" if not reply_text else "已发出")""",
    ),
    (
        "plugin_tool_service.py",
        "桥的日志不再双写文件（重载后「挂没挂工具/她调没调」就查不到了）",
        """    try:
        logger = plugin.logger
        write = logger.warning if level in ("WARN", "ERROR") else logger.info
        write(msg)
    except Exception:
        pass""",
        """    return""",
    ),
    (
        "pipeline_models.py",
        "新的 source_kind 不算「名义发言人」（会拿别人的成员记忆公开发言）",
        """    KIND_GROUP_JOIN_NOTICE,
    KIND_PLUGIN_TOOL_RESULT,
})""",
        """    KIND_GROUP_JOIN_NOTICE,
})""",
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
    print(f"[PASS] {len(results)}/{len(results)} —— 回投的每道闸与每条「不许撒谎」都真的被钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
