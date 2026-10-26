"""群组记忆作用域隔离的**实证测试**（打真实 memory server）。

回答代码阅读答不了的问题：**服务端的作用域隔离到底有没有生效。**

探测过程中的一个重要发现（也是本脚本结构的由来）：
  `scoped_context` 渲染的是**合成后的 persona/reflection**，不是刚写进去的原始
  fact（实测：写入返回 `created:1` 并给出 fact_ids，但 `scoped_context` 立刻读回
  空串）。所以隔离必须用 `/query_memory`（facts + reflections 全路径召回）来验证
  ——那也正是插件 `recall_memory` 工具走的入口。

  `/query_memory` 的 subjects 语义（服务端 routes.py:3347-3356）：
    - 省略 `subjects`         → legacy 私有语料（本体的单人记忆）
    - `subjects: []`          → fail-closed，零行
    - `subjects: [1..8 项]`   → 只在这些域内检索

覆盖：群间隔离、同群成员隔离、跨域（群↔私聊）隔离、合并授权的并集、空授权
fail-closed、以及 legacy（省略 subjects）不被群调用污染。

安全约定：
  - 只用 `TESTISO` 前缀的假 group/actor id，**绝不触碰真实群号**；
  - 每个 subject 写入前先 `scoped_forget` 确保干净；
  - 无论成功或异常，结束前必删所有测试 subject；
  - 向量检索可能因模型缺失而不可用（日志见 model_file_missing），故哨兵用**唯一
    关键词**，保证 BM25 也能命中，不依赖 embedding。

用法：
    python scratch_memory_isolation.py
    python scratch_memory_isolation.py --port 48912 --character 宅久皖萱
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import quote

DEFAULT_PORT = 48912
DEFAULT_CHARACTER = "宅久皖萱"

TAG = "TESTISO"
GA, GB = f"{TAG}GA", f"{TAG}GB"
AX, AY = f"{TAG}AX", f"{TAG}AY"
PRIV = f"{TAG}PRIV"
PLATFORM = "qq"

#: 哨兵关键词：同一词同时用于写入的事实文本与检索 query，确保 BM25 能命中。
KEY = {
    "group_a": f"SENTINELALPHA{TAG}",
    "group_b": f"SENTINELBETA{TAG}",
    "member_x": f"SENTINELXRAY{TAG}",
    "member_y": f"SENTINELYANKEE{TAG}",
    "private": f"SENTINELZULU{TAG}",
}

#: 阴性对照专用：**同一个**关键词写进所有域，用来证明 subject 过滤真的在过滤。
SHARED_KEY = f"SHAREDKEYWORD{TAG}"


def subject(kind: str, subject_id: str) -> dict[str, str]:
    return {"subject_kind": kind, "subject_id": subject_id}


#: 每个哨兵所属域的精确 subject_id —— 结构化命中的判据（见 recall_hits）。
_SUBJECT_ID = {
    "group_a": f"{PLATFORM}:{GA}",
    "group_b": f"{PLATFORM}:{GB}",
    "member_x": f"{PLATFORM}:{GA}:{AX}",
    "member_y": f"{PLATFORM}:{GA}:{AY}",
    "private": f"{PLATFORM}:{PRIV}",
}


class Server:
    def __init__(self, port: int, character: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.character = character

    def _post(self, path: str, payload: dict, timeout: float = 30.0) -> tuple[int, str]:
        # 角色名可能是中文，必须 quote 进 URL，否则请求行 ascii 编码会抛异常。
        url = f"{self.base}{quote(path, safe='/?=&')}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:  # 连接层
            return 0, f"{type(exc).__name__}: {exc}"

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base}/health", timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def write_fact(self, subj: dict[str, str], text: str) -> tuple[int, str]:
        return self._post(
            f"/internal/memory/{self.character}/scoped_facts",
            {"subject": subj, "facts": [
                {"text": text, "importance": 9, "source": "user_observation"},
            ]},
        )

    def query(self, query_text: str, subjects: list[dict[str, str]] | None) -> tuple[int, str]:
        payload: dict = {"query": query_text}
        if subjects is not None:
            payload["subjects"] = subjects
        return self._post(f"/query_memory/{self.character}", payload)

    def recall_hits(
        self,
        query_text: str,
        subjects: list[dict[str, str]] | None,
        *,
        sentinels: dict[str, str] | None = None,
    ) -> set[str]:
        """检索并返回**命中哨兵**的域名集合。

        ``sentinels`` 默认用 ``KEY``；阴性对照传 ``{name: SHARED_KEY}``。

        ⚠️ 必须解析 ``results``，不能对响应体做子串匹配：``/query_memory`` 的
        响应里包含**回显的 query 字符串**，而哨兵关键词正是 query 本身，于是
        任何子串检测都会恒为真（踩过一次，得出过"14 项全泄漏"的假结论）。

        判定"某哨兵被命中"的口径：results 里存在一条 entry，其 ``subject_id``
        等于该哨兵所属域，**且**其 text 含该哨兵关键词。
        """
        table = KEY if sentinels is None else sentinels
        code, body = self.query(query_text, subjects)
        if code != 200:
            return set()
        try:
            payload = json.loads(body)
        except Exception:
            return set()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return set()
        hits: set[str] = set()
        for entry in results:
            if not isinstance(entry, dict):
                continue
            sid = str(entry.get("subject_id") or "")
            text = str(entry.get("text") or "")
            for name, sentinel in table.items():
                if sentinel in text and sid == _SUBJECT_ID[name]:
                    hits.add(name)
        return hits

    def forget(self, subj: dict[str, str]) -> tuple[int, str]:
        return self._post(f"/internal/memory/{self.character}/scoped_forget", {"subject": subj})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--character", default=DEFAULT_CHARACTER)
    args = ap.parse_args()

    srv = Server(args.port, args.character)
    if not srv.health():
        print(f"[FATAL] memory server 不可达: {srv.base}/health")
        return 2
    print(f"memory server OK: {srv.base}   character={args.character}")

    subs = {
        "group_a": subject("group_chat", f"{PLATFORM}:{GA}"),
        "group_b": subject("group_chat", f"{PLATFORM}:{GB}"),
        "member_x": subject("group_participant", f"{PLATFORM}:{GA}:{AX}"),
        "member_y": subject("group_participant", f"{PLATFORM}:{GA}:{AY}"),
        "private": subject("participant", f"{PLATFORM}:{PRIV}"),
    }
    failures: list[str] = []

    def cleanup() -> None:
        print("\n--- cleanup ---")
        for key, subj in subs.items():
            code, _ = srv.forget(subj)
            print(f"  forget {key:9} -> {code}")

    try:
        print("\n[1] 预清理（让测试 subject 从空状态开始）")
        for key, subj in subs.items():
            code, _ = srv.forget(subj)
            print(f"  pre-forget {key:9} -> {code}")

        print("\n[2] 写入唯一哨兵事实")
        for key, subj in subs.items():
            code, body = srv.write_fact(subj, f"唯一标识 {KEY[key]} 的测试事实")
            ok = code == 200 and '"stored"' in body
            print(f"  write {key:9} -> {code}  {'stored' if ok else body[:80]}")
            if not ok:
                print("[FATAL] 写入失败，断言无意义")
                return 3

        print("\n[3] 正向：每个域检索自己的哨兵，应命中且只命中自己")
        for key in subs:
            hits = srv.recall_hits(KEY[key], [subs[key]])
            ok = hits == {key}
            print(f"  {key:9} 查自己 -> 命中={sorted(hits) or '无'}  [{'ok' if ok else 'FAIL'}]")
            if not ok:
                failures.append(f"{key} 查自己时命中={sorted(hits)}，期望只有它自己")

        print("\n[4] 反向：逐对交叉检索，只授权 reader 时不得命中 other 的哨兵")
        pairs = [
            ("group_a", "group_b"), ("group_a", "member_x"), ("group_a", "private"),
            ("group_b", "group_a"), ("group_b", "member_x"), ("group_b", "private"),
            ("member_x", "member_y"), ("member_x", "group_b"), ("member_x", "private"),
            ("member_y", "member_x"), ("member_y", "group_a"),
            ("private", "group_a"), ("private", "group_b"), ("private", "member_x"),
        ]
        for reader, other in pairs:
            hits = srv.recall_hits(KEY[other], [subs[reader]])
            leaked = other in hits
            if leaked:
                failures.append(f"{reader} 只授权自己时命中了 {other} 的哨兵（泄漏）")
            print(f"  {reader:9} 查 {other:9} 的哨兵 -> {'LEAK!!' if leaked else 'ok (零命中)'}")

        print("\n[5] 合并授权：应看到并集内，不该看到授权外")
        hits = srv.recall_hits(KEY["group_a"], [subs["group_a"], subs["member_x"]])
        print(f"  [群A, 成员X] 查群A哨兵 -> 命中={sorted(hits)}  (期望含 group_a)")
        if "group_a" not in hits:
            failures.append("合并授权时群A自己的事实都没召回")
        for k in ("group_b", "member_y", "private"):
            if k in hits:
                failures.append(f"合并授权时命中了授权外的 {k}")

        # 阴性对照：这是**唯一**能证明"过滤器真的在过滤"的测试。
        # 第 3/4 节的哨兵是唯一关键词，BM25 本来只会命中那一条 —— 即使 subject
        # 过滤完全没接上，那两节也会全绿。把同一个关键词写进多个域再只授权其一，
        # 才能把"过滤生效"和"关键词恰好唯一"区分开。
        print("\n[5b] 阴性对照：同一关键词写进多域，只授权其一必须只回一条")
        shared_sentinels = {name: SHARED_KEY for name in subs}
        for key in subs:
            srv.write_fact(subs[key], f"共享关键词 {SHARED_KEY} 出现在域 {key}")
        for key in subs:
            hits = srv.recall_hits(SHARED_KEY, [subs[key]], sentinels=shared_sentinels)
            ok = hits == {key}
            print(f"  只授权 {key:9} -> 命中={sorted(hits) or '无'}  [{'ok' if ok else 'FAIL'}]")
            if not ok:
                failures.append(
                    f"阴性对照失败：只授权 {key} 却命中 {sorted(hits)}"
                    f"——subject 过滤可能未生效（或 BM25 未命中，需人工判断）"
                )
        all_hits = srv.recall_hits(SHARED_KEY, list(subs.values()), sentinels=shared_sentinels)
        print(f"  三个都授权 -> 命中={sorted(all_hits)}  (期望三个都在)")
        if all_hits != set(subs):
            failures.append(f"全部授权时只命中 {sorted(all_hits)}，期望全部 {sorted(subs)}")

        print("\n[6] 空授权必须 fail-closed（不能回落 legacy）")
        code, body = srv.query(KEY["group_a"], [])
        print(f"  subjects=[] -> {code}  {body[:90]}")
        if code == 200:
            hits = srv.recall_hits(KEY["group_a"], [])
            if hits:
                failures.append(f"空 subjects 返回了内容: {sorted(hits)}")
            else:
                print("    200 但零命中（可接受）")
        elif code == 422:
            print("    422 显式拒绝（符合预期）")
        else:
            failures.append(f"空 subjects 返回意外状态 {code}")

        print("\n[7] legacy 分离：省略 subjects 不能命中群域哨兵")
        hits = srv.recall_hits(KEY["group_a"], None)
        print(f"  省略 subjects（legacy 私有语料）-> 群域命中={sorted(hits) or '无'}")
        if hits:
            failures.append(f"legacy 路径命中了群域内容: {sorted(hits)}")

        print("\n=== RESULT ===")
        if failures:
            print(f"FAILED ({len(failures)}):")
            for f in failures:
                print("  -", f)
            return 1
        print("PASSED — 作用域隔离全部成立")
        return 0
    finally:
        cleanup()


if __name__ == "__main__":
    sys.exit(main())
