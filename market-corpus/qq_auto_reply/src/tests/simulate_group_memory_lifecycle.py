"""情景模拟：一个全新虚拟群的记忆生命周期（走插件真实代码路径）。

用一个从未存在过的群号，完整演示"记忆从零建立 → 隔离 → 撤权"这一条链，
每一步都断言，而不是打印日志让读者自己看。

情景设定：
  群 G = 1800000001（虚拟群，绝不可能与真实群号冲突）
  成员：阿澈 100001 / 小满 100002
  步骤：
    1. 基线：该群此刻没有任何记忆
    2. 阿澈在群里说了一件私事 → 写进去
    3. 群域能召回；**另一个群召回不到**（跨群隔离）
    4. 小满在自己的成员域问同一件事 → 召回不到阿澈的（成员级隔离）
    5. 阿澈在自己的成员域 → 召回得到（同群同人可见）
    6. 撤权（scoped_forget）→ 记忆消失
  最后无条件清理。

安全：群号带 1800000 前缀，且脚本开始与结束都做 forget，绝不触碰真实群。

用法：
    python simulate_group_memory_lifecycle.py [--port 48912] [--character 宅久皖萱]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import urllib.error
import urllib.request
from urllib.parse import quote

DEFAULT_REPO_ROOT = pathlib.Path(r"D:\NekoClaw\N.E.K.O")
DEFAULT_CHARACTER = "宅久皖萱"

GROUP = "1800000001"        # 虚拟群
OTHER_GROUP = "1800000002"  # 另一个虚拟群（用于跨群隔离断言）
ACHE = "100001"             # 成员甲
XIAOMAN = "100002"          # 成员乙

#: 写进记忆的语义内容 + 检索词。
#: ⚠️ 必须用**语义内容**而非标记串：`scoped_history` 走 LLM 抽取会改写文本。
FACT_TEXT = "阿澈把自己家门锁的备用钥匙藏在了门口花盆底下"
QUERY = "备用钥匙藏在哪里"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=48912)
    ap.add_argument("--character", default=DEFAULT_CHARACTER)
    ap.add_argument("--repo-root", type=pathlib.Path, default=DEFAULT_REPO_ROOT)
    args = ap.parse_args()

    root = str(args.repo_root)
    for p in (root, str(args.repo_root / "plugin" / "plugins" / "qq_auto_reply" / "_vendor")):
        if p not in sys.path:
            sys.path.insert(0, p)

    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge
    from plugin.plugins.qq_auto_reply.memory_tool_service import (
        resolve_group_recall_subjects,
    )

    CHAR = args.character
    base = f"http://127.0.0.1:{args.port}"
    bridge = QQMemoryBridge(plugin=None)

    def post(path: str, payload: dict, timeout: float = 120.0) -> tuple[int, str]:
        req = urllib.request.Request(
            f"{base}{quote(path, safe='/?=&')}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    # ---- 用插件自己的构造函数得到域（不是脚本手拼）----
    S_GROUP = QQMemoryBridge.group_subject(GROUP)
    S_OTHER = QQMemoryBridge.group_subject(OTHER_GROUP)
    S_ACHE = QQMemoryBridge.group_participant_subject(GROUP, ACHE)
    S_XIAOMAN = QQMemoryBridge.group_participant_subject(GROUP, XIAOMAN)
    ALL = [S_GROUP, S_OTHER, S_ACHE, S_XIAOMAN]

    def cleanup() -> None:
        for s in ALL:
            post(f"/internal/memory/{CHAR}/scoped_forget", {"subject": s})

    async def recall(subjects: list[dict], q: str) -> tuple[int, str]:
        res = await bridge.query_relevant_memory(CHAR, q, subjects=subjects, timeout=60.0)
        return res.rendered_count, (res.text or "")

    failures: list[str] = []
    step = 0

    def report(label: str, ok: bool, detail: str) -> None:
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {label}")
        if detail:
            print(f"         {detail}")
        if not ok:
            failures.append(label)

    try:
        # ── 0. 健康检查 + 基线 ────────────────────────────────────────
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=5) as r:
                assert r.status == 200
        except Exception as exc:
            print(f"[FATAL] memory server 不可达: {base} ({type(exc).__name__})")
            return 2
        print(f"memory server {base}  角色={CHAR}")
        print(f"虚拟群 {GROUP}（成员 {ACHE}/{XIAOMAN}）、对照群 {OTHER_GROUP}\n")

        print("步骤 1 · 基线：该群此刻应没有任何记忆")
        cleanup()  # 确保从零开始
        step += 1
        n, text = asyncio.run(recall([S_GROUP], QUERY))
        report("新群召回为空", n == 0, f"rendered={n} text={text[:40]!r}")

        # ── 2. 写入 ──────────────────────────────────────────────────
        print("\n步骤 2 · 阿澈在群里说了一件私事（走插件 post_scoped_memory_history）")
        step += 1
        history = [{"role": "user", "content": FACT_TEXT}]
        try:
            result = asyncio.run(bridge.post_scoped_memory_history(
                CHAR, history, subject=S_GROUP,
                speaker_label="阿澈", speaker_tier="trusted",
                speaker_id=QQMemoryBridge.speaker_account_id(ACHE),
                display_name="模拟群", timeout=120.0,
            ))
            ok = isinstance(result, dict) and result.get("status") == "processed"
            report("服务端接受并完成抽取",
                   ok, f"status={result.get('status') if isinstance(result, dict) else result} "
                       f"created={result.get('created') if isinstance(result, dict) else '?'}")
        except Exception as exc:
            report("服务端接受并完成抽取", False, f"{type(exc).__name__}: {exc}")

        # ── 3. 群域可见 + 跨群隔离 ───────────────────────────────────
        print("\n步骤 3 · 群域召回 & 跨群隔离")
        step += 1
        n_g, t_g = asyncio.run(recall([S_GROUP], QUERY))
        report("本群能召回", n_g > 0, f"rendered={n_g} text={t_g[:74]!r}")
        step += 1
        n_o, t_o = asyncio.run(recall([S_OTHER], QUERY))
        report("另一个群召回不到（跨群隔离）", n_o == 0,
               f"rendered={n_o}" + (f" text={t_o[:60]!r}" if t_o else ""))

        # ── 4. 成员级隔离 ────────────────────────────────────────────
        print("\n步骤 4 · 成员级隔离（同一群里的另一个人）")
        step += 1
        n_x, t_x = asyncio.run(recall([S_XIAOMAN], QUERY))
        report("小满的成员域召回不到阿澈的事", n_x == 0,
               f"rendered={n_x}" + (f" text={t_x[:60]!r}" if t_x else ""))

        # ── 5. 成员级隔离：必须走**成员桶**写入路径才谈得上 ───────────
        #
        # 修正一个容易搞错的语义（我第一版就在这里断言错了）：
        # `scoped_history` 的落点由 **subject** 决定，不由 speaker 决定。
        # 上面 step 2 用的是群域 subject，所以事实落在**群域** —— 群成员共享，
        # 因此下面"单独查阿澈成员域"本来就是空的，那不是隔离失败。
        #
        # 真正的成员级隔离要这样验：把事实写进**成员桶**（subject = 成员域，
        # 带 speaker_label），然后断言同群另一个人的成员域看不到它。
        print("\n步骤 5 · 成员桶写入 → 成员级隔离")
        step += 1
        member_fact = "阿澈提过自己月底要搬到城西的新住处"
        member_query = "月底要搬到哪里"
        try:
            r2 = asyncio.run(bridge.post_scoped_memory_history(
                CHAR, [{"role": "user", "content": member_fact}],
                subject=S_ACHE,
                speaker_label="阿澈", speaker_tier="trusted",
                speaker_id=QQMemoryBridge.speaker_account_id(ACHE),
                timeout=120.0,
            ))
            report("成员桶写入成功",
                   isinstance(r2, dict) and r2.get("status") == "processed",
                   f"status={r2.get('status') if isinstance(r2, dict) else r2}")
        except Exception as exc:
            report("成员桶写入成功", False, f"{type(exc).__name__}: {exc}")

        step += 1
        n_a, t_a = asyncio.run(recall([S_ACHE], member_query))
        report("阿澈的成员域能召回自己的事", n_a > 0,
               f"rendered={n_a} text={t_a[:74]!r}")

        step += 1
        n_x2, t_x2 = asyncio.run(recall([S_XIAOMAN], member_query))
        report("同群的小满召回不到（成员级隔离）", n_x2 == 0,
               f"rendered={n_x2}" + (f" text={t_x2[:60]!r}" if t_x2 else ""))

        step += 1
        n_o2, _ = asyncio.run(recall([S_OTHER], member_query))
        report("另一个群也召回不到（跨群隔离）", n_o2 == 0, f"rendered={n_o2}")

        # ── 6. 读取侧组装的真实授权组合 ──────────────────────────────
        print("\n步骤 6 · 用插件读取侧函数组装授权（群 + 当前发言人）")
        step += 1

        def stub(member_on: bool):
            return SimpleNamespace(
                memory_bridge=QQMemoryBridge(plugin=None),
                _qq_settings={"group_member_memory_enabled": member_on},
                backlog_store=None, logger=None,
            )

        subs_ache, used = asyncio.run(resolve_group_recall_subjects(
            stub(True), group_id=GROUP, memory_sender_id=ACHE,
        ))
        ids = [s["subject_id"] for s in subs_ache]
        report("member 开启时组装出 [群, 阿澈]",
               used and ids == [f"qq:{GROUP}", f"qq:{GROUP}:{ACHE}"], f"subjects={ids}")

        n_combo, t_combo = asyncio.run(recall(subs_ache, QUERY))
        report("该组合能召回", n_combo > 0, f"rendered={n_combo} text={t_combo[:74]!r}")

        subs_off, used_off = asyncio.run(resolve_group_recall_subjects(
            stub(False), group_id=GROUP, memory_sender_id=ACHE,
        ))
        report("member 关闭时只组装群域（开关真的门控）",
               not used_off and [s["subject_id"] for s in subs_off] == [f"qq:{GROUP}"],
               f"subjects={[s['subject_id'] for s in subs_off]}")

        # ── 7. 撤权 ──────────────────────────────────────────────────
        print("\n步骤 7 · 撤权（scoped_forget 群域）→ 记忆应消失")
        step += 1
        code, body = post(f"/internal/memory/{CHAR}/scoped_forget", {"subject": S_GROUP})
        report("遗忘接口返回成功", code == 200, f"HTTP {code} {body[:60]}")
        step += 1
        n_after, t_after = asyncio.run(recall([S_GROUP], QUERY))
        report("撤权后召回为空", n_after == 0,
               f"rendered={n_after}" + (f" text={t_after[:60]!r}" if t_after else ""))
        step += 1
        n_after2, _ = asyncio.run(recall(subs_ache, QUERY))
        report("撤权后组合授权也召回为空", n_after2 == 0, f"rendered={n_after2}")

    finally:
        print("\n--- cleanup ---")
        cleanup()
        print("  已遗忘全部模拟域")

    print("\n=== RESULT ===")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print(f"PASSED — {step} 步全部符合预期：写入→隔离→撤权链路成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
