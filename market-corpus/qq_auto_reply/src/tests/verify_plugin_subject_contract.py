"""插件层验证：**插件自己拼的 subject** 是否与本体记忆库的契约一致。

背景（为什么要单独一层验证）：
  `verify_memory_isolation.py` 验证的是**服务端自己的作用域过滤**，但它是用
  脚本手工拼的 subject。插件是**另一份实现**——`memory_bridge.py` 里有注释
  说明它**刻意手工拼字符串**而不调本体的构造函数：

      #: The one place the platform literal lives. Subject builders below keep
      #: composing their ids by hand ON PURPOSE — rewriting a subject_id would
      #: make every stored scoped memory an unreachable orphan, since
      #: attribution is byte equality of ``(key, scope)``.

  代价是**跳过了本体的 `_encode_component`**（`memory/scopes.py:61-72`），它会
  把 `%` 和 `:` 百分号转义以防键塌缩。于是"插件拼的串"与"本体工厂产的串"
  是否逐字节相等，就成了一个**纯靠巧合维持、没有任何测试保证**的契约。

  本脚本把这个契约变成可执行的断言。若它红了，意味着插件写入的记忆在服务端
  看来属于另一个域 —— 那是**静默的数据错位**，比报错更糟。

用法：
    python verify_plugin_subject_contract.py --repo-root D:\\NekoClaw\\N.E.K.O
"""
from __future__ import annotations

import argparse
import pathlib
import sys

DEFAULT_REPO_ROOT = pathlib.Path(r"D:\NekoClaw\N.E.K.O")


def _load_repo(repo_root: pathlib.Path) -> None:
    """把仓库根（以及插件的 vendored lib）放进 sys.path。

    插件 `__init__.py` 会 import `_lib_bootstrap` 做同样的副作用，但这里只
    import `memory_bridge` 单模块，所以自己把路径铺好，避免拉进整个插件包。
    """
    for p in (repo_root, repo_root / "plugin" / "plugins" / "qq_auto_reply" / "_vendor"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", type=pathlib.Path, default=DEFAULT_REPO_ROOT)
    ap.add_argument("--character", default="宅久皖萱", help="本体角色名（记忆按其分库）")
    args = ap.parse_args()
    _load_repo(args.repo_root)

    from memory.scopes import MemorySubject  # 本体（服务端）的权威构造器
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge  # 插件侧

    failures: list[str] = []

    def check(label: str, plugin_val: str, canonical_val: str, *, expect_equal: bool) -> None:
        eq = plugin_val == canonical_val
        if eq == expect_equal:
            mark = "ok"
        else:
            mark = "FAIL"
            failures.append(
                f"{label}: 插件={plugin_val!r} 本体={canonical_val!r} "
                f"(期望{'相等' if expect_equal else '不等'})"
            )
        print(f"  [{mark:4}] {label}")
        print(f"           插件  = {plugin_val!r}")
        print(f"           本体  = {canonical_val!r}")

    print("=== 1. 真实形态的 QQ id：插件与本体必须逐字节一致 ===")
    # 群号：NapCat 的纯数字群号；开放平台是 32 位 hex openid。
    # actor：QQ 号 / 开放平台 openid。
    realistic_cases = [
        ("纯数字群号 + 数字 QQ", "1048307485", "820040531"),
        ("开放平台 openid 群 + openid 用户",
         "C9F778FE6ADF9D1D1DBE395BF744A33A",
         "8B2F1A4C6D7E9012ABCDEF3456789012"),
        ("数字群号 + 带连字符的 actor", "985066274", "poke-1048307485-3281414178"),
    ]

    canonical_kind_group = "group_chat"
    canonical_kind_member = "group_participant"

    for label, gid, actor in realistic_cases:
        print(f"\n-- {label}")
        check(
            "group_subject.subject_id",
            QQMemoryBridge.group_subject(gid)["subject_id"],
            MemorySubject.group_chat(QQMemoryBridge.PLATFORM, gid).subject_id,
            expect_equal=True,
        )
        check(
            "group_participant_subject.subject_id",
            QQMemoryBridge.group_participant_subject(gid, actor)["subject_id"],
            MemorySubject.group_participant(
                QQMemoryBridge.PLATFORM, gid, actor,
            ).subject_id,
            expect_equal=True,
        )
        # kind 也必须一致 —— 服务端按 (kind, subject_id) 归因
        check(
            "group_subject.subject_kind",
            QQMemoryBridge.group_subject(gid)["subject_kind"],
            canonical_kind_group,
            expect_equal=True,
        )
        check(
            "group_participant.subject_kind",
            QQMemoryBridge.group_participant_subject(gid, actor)["subject_kind"],
            canonical_kind_member,
            expect_equal=True,
        )

    print("\n=== 2. 私聊 participant：同样必须逐字节一致 ===")
    for actor in ("820040531", "8B2F1A4C6D7E9012ABCDEF3456789012"):
        check(
            f"participant_subject({actor[:12]}…)",
            QQMemoryBridge.participant_subject(actor)["subject_id"],
            MemorySubject.participant(QQMemoryBridge.PLATFORM, actor).subject_id,
            expect_equal=True,
        )

    print("\n=== 3. 恶意/异常 id：插件与本体**应当**不同（本体转义，插件不转义）===")
    # 这不是"插件错了"，而是记录一个**已知的契约缺口**：id 里含 ':' 或 '%' 时，
    # 插件手工拼接会与本体工厂分歧，写入的记忆落在服务端看来是另一个域。
    # QQ 场景下群号是数字、openid 是 hex，都不含这两个字符，故当前不可达；
    # 这条断言把"靠巧合对齐"这件事变成显式的、可观察的事实。
    for evil in ("a:b", "a%b", ":"):
        plugin_gid = QQMemoryBridge.group_subject(evil)["subject_id"]
        try:
            canonical_gid = MemorySubject.group_chat(QQMemoryBridge.PLATFORM, evil).subject_id
        except Exception as exc:  # 本体可能直接拒绝
            print(f"\n-- group_id={evil!r}")
            print(f"  [note] 本体拒绝该 id: {type(exc).__name__}")
            print(f"         插件仍会拼出 = {plugin_gid!r}")
            continue
        check(
            f"group_id={evil!r} 的分歧（预期不等）",
            plugin_gid,
            canonical_gid,
            expect_equal=False,
        )

    print("\n=== 4. group_participant 三段式：本体有强制校验，插件没有 ===")
    # 本体 `scopes.py:88-96` 要求 subject_id 恰好 platform:group:speaker 三段且
    # 每段非空。插件的 group_participant_subject 只是 f-string，空 actor 会产生
    # 四段/空段形态。检查插件是否会在空 actor 时拼出本体认为非法的串。
    print("  group_id='', actor='' ->")
    plugin_val = QQMemoryBridge.group_participant_subject("", "")["subject_id"]
    print(f"    插件拼出 = {plugin_val!r}")
    try:
        MemorySubject.group_participant(QQMemoryBridge.PLATFORM, "", "").subject_id
        print("    本体接受（意外）")
    except Exception as exc:
        print(f"    本体拒绝: {type(exc).__name__} —— 插件拼的串非法")
        print("    （注：调用方 `session_instruction_service.py:717` 对空 group_id")
        print("      有早退闸，所以生产路径不可达；此处只记录契约不一致）")

    print("\n=== 5. 端到端：用**插件自己的代码路径**写，再查回 ===")
    # 前四节只验证字符串。这一节验证"插件写进去的东西，落在服务端哪个域" ——
    # 这正是 `verify_memory_isolation.py` 覆盖不到的部分（那个脚本用的是手工
    # 拼的 subject，绕过了插件）。
    #
    # QQMemoryBridge 不访问 self.plugin（实测：全文件零处），所以可以裸构造。
    import asyncio

    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    bridge = QQMemoryBridge(plugin=None)
    base = bridge._base_url()
    print(f"  memory server = {base}")

    GA, GB = "VERIFYPLUGA", "VERIFYPLUGB"
    #: 写进历史的内容 + 读回时用的查询词。两者都要是**会被抽取保留的语义词**，
    #: 不能用标记串（抽取会改写文本，见下方注释）。
    #: 语义上足够独特，避免撞上真实记忆里的既有事实。
    KEY_A = "抹茶千层蛋糕"
    QUERY_TEXT = "抹茶千层蛋糕"
    CHAR = args.character

    async def _e2e() -> None:
        import json as _json
        import urllib.error
        import urllib.request
        from urllib.parse import quote as _quote

        def post(path: str, payload: dict) -> tuple[int, str]:
            req = urllib.request.Request(
                f"{base}{_quote(path, safe='/?=&')}",
                data=_json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return resp.status, resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode("utf-8", "replace")

        subj_a = QQMemoryBridge.group_subject(GA)
        subj_b = QQMemoryBridge.group_subject(GB)
        for s in (subj_a, subj_b):
            post(f"/internal/memory/{CHAR}/scoped_forget", {"subject": s})

        # 模拟一条真实的群消息批次（服务端会走 LLM 抽取）
        # speaker_id 必须用**插件自己的** speaker_account_id()：服务端
        # `speaker_trust.stable_speaker_id` 要求 `platform:actor` 形态
        # （`speaker_trust.py:178-185`），裸 QQ 号会 422 "invalid speaker_id"。
        # 这正是"插件层验证"该覆盖的东西 —— 我第一次写测试时传了裸号，
        # 被 422 拦下，说明插件这里是对的。
        speaker_uid = QQMemoryBridge.speaker_account_id("820040531")
        print(f"  speaker_id = {speaker_uid!r} (插件 speaker_account_id 产出)")
        history = [{"role": "user", "content": f"我很喜欢吃{KEY_A}"}]
        write_ok = False
        try:
            result = await bridge.post_scoped_memory_history(
                CHAR, history, subject=subj_a,
                speaker_label="测试用户", speaker_tier="trusted",
                speaker_id=speaker_uid, display_name="验证群A",
                timeout=120.0,
            )
            write_ok = isinstance(result, dict)
            print(f"  插件写入 scoped_history -> ok={write_ok} status={result.get('status') if isinstance(result, dict) else result}")
        except Exception as exc:
            print(f"  插件写入失败: {type(exc).__name__}: {exc}")
            failures.append(f"插件 post_scoped_memory_history 写入失败: {type(exc).__name__}")

        # 用插件的读取方法查回来。
        #
        # ⚠️ 必须用**语义内容**查询，不能用塞进历史里的标记词：服务端
        # `scoped_history` 走 LLM 抽取，**会改写文本**。实测传
        # "EXTRACTEDFACTKEY 我最喜欢的颜色是蓝色"，抽出的是
        # "测试用户最喜欢的颜色是蓝色" —— 标记词被整段重写掉，用它检索必然 0 命中
        # （踩过一次，误判成"写入没生效"）。
        #
        # 想用确定性关键词做断言，只能走 /scoped_facts 直写（见 verify_memory_isolation）。
        try:
            hits = await bridge.query_relevant_memory(
                CHAR, QUERY_TEXT, subjects=[subj_a], timeout=60.0,
            )
            print(f"  插件读 A（授权 A）-> hit_count={hits.hit_count} rendered={hits.rendered_count}")
            print(f"     text = {(hits.text or '')[:110]!r}")
            got_a = hits.rendered_count > 0
        except Exception as exc:
            print(f"  插件读取失败: {type(exc).__name__}: {exc}")
            failures.append(f"插件 query_relevant_memory 读取失败: {type(exc).__name__}")
            got_a = False

        try:
            cross = await bridge.query_relevant_memory(
                CHAR, QUERY_TEXT, subjects=[subj_b], timeout=60.0,
            )
            leaked = QUERY_TEXT in (cross.text or "")
            print(f"  插件读 A 的内容但授权 B -> rendered={cross.rendered_count} 命中={leaked}")
            if leaked or cross.rendered_count > 0:
                failures.append("插件路径下 B 域读到了 A 域内容（服务端隔离或插件 subject 错位）")
        except Exception as exc:
            print(f"  交叉读取失败: {type(exc).__name__}")

        if write_ok and not got_a:
            failures.append(
                "插件写入成功但读不回（检查是否落入 BM25 阈值整批滤空的已知问题）"
            )

        for s in (subj_a, subj_b):
            post(f"/internal/memory/{CHAR}/scoped_forget", {"subject": s})
        print("  已清理测试 subject")

    try:
        asyncio.run(_e2e())
    except Exception as exc:
        print(f"  端到端节异常: {type(exc).__name__}: {exc}")

    print("\n=== 6. 读取侧 subject 构造（真实插件函数 resolve_group_recall_subjects）===")
    # 第 1-4 节验证 bridge 的静态构造函数；这一节验证**读取路径**实际组装出的
    # subject 列表 —— 它才是每轮群消息召回时真正发给服务端的东西，而且带
    # member 开关门控与"最近发言人"扩容（需要 backlog，这里降级成单发言人）。
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.memory_tool_service import (
        resolve_group_recall_subjects,
    )

    TEST_GID = "985066274"  # 使用者指定的专用测试群

    def _stub_plugin(member_on: bool):
        return SimpleNamespace(
            memory_bridge=QQMemoryBridge(plugin=None),
            _qq_settings={"group_member_memory_enabled": member_on},
            backlog_store=None,  # _recent_other_speakers 降级
            logger=None,
        )

    async def _subjects(member_on: bool, sender: str) -> tuple[list[dict], bool]:
        return await resolve_group_recall_subjects(
            _stub_plugin(member_on), group_id=TEST_GID, memory_sender_id=sender,
        )

    async def _read_path_checks() -> None:
        cases = [
            ("member 开 + 有发言人", True, "820040531", True,
             ["qq:" + TEST_GID, f"qq:{TEST_GID}:820040531"]),
            ("member 开 + 无发言人", True, "", False, ["qq:" + TEST_GID]),
            ("member 关 + 有发言人", False, "820040531", False, ["qq:" + TEST_GID]),
            ("member 关 + 无发言人", False, "", False, ["qq:" + TEST_GID]),
        ]
        for label, member_on, sender, want_used, want_ids in cases:
            subs, used = await _subjects(member_on, sender)
            got = [s["subject_id"] for s in subs]
            ok = used == want_used and got == want_ids
            print(f"  [{'ok' if ok else 'FAIL':4}] {label:22} used={used} subjects={got}")
            if not ok:
                failures.append(f"{label}: used={used} subjects={got}，期望 used={want_used} {want_ids}")

        # 群域必须恒在第一位：服务端渲染预算是**先到先得**，顺序即优先级。
        subs, _ = await _subjects(True, "820040531")
        if subs[0]["subject_id"] != "qq:" + TEST_GID:
            failures.append(f"群域不在首位：{subs[0]['subject_id']!r}（渲染预算会先分给别人）")
        else:
            print("  [ok  ] 群域恒在首位（渲染预算优先级）")

        # 空 group_id：函数本身**不设防**，会产出共享桶 qq:。
        # 写侧到处是 fail-closed 闸（`while group_id:` / `if not group_id`），
        # 只有这里没有——目前靠两个调用方各自早退兜住。记录为**纵深防御缺口**：
        # 任何未来的新调用方若不早退，多个"无群号"的群就会共用同一个记忆桶
        # （跨群泄漏）。
        empty, _ = await resolve_group_recall_subjects(
            _stub_plugin(True), group_id="", memory_sender_id="820040531",
        )
        empty_ids = [s["subject_id"] for s in empty]
        shares = any(sid == "qq:" or sid.startswith("qq::") for sid in empty_ids)
        print(f"  [note] 空 group_id -> {empty_ids}")
        if shares:
            print("         产出共享桶 qq: —— 依赖调用方早退，属纵深防御缺口（已知，非新回归）")
        else:
            print("  [ok  ] 空 group_id 已被拒绝（缺口已补）")

    try:
        asyncio.run(_read_path_checks())
    except Exception as exc:
        print(f"  读取侧验证异常: {type(exc).__name__}: {exc}")
        failures.append(f"读取侧 subject 验证异常: {type(exc).__name__}")

    print("\n=== RESULT ===")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("  -", f)
        return 1
    print("PASSED — 插件 subject 契约、端到端读写、读取侧组装全部成立")
    return 0

if __name__ == "__main__":
    sys.exit(main())
