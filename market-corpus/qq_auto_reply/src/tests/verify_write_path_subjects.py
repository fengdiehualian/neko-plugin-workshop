"""写入侧验证（基于真实生产数据）：fact 的**内容自述**是否与它落库的**域**一致。

不需要跑消息分发链路 —— 磁盘上已有的 fact 就是写入侧的产物。每一条群 fact 里
都带着"群号为 NNNN"/"QQ: NNNN"这类由抽取器写下的自述，把自述与 subject_id 交叉
核对，就直接验证了「消息里的群号/发言人 → 落库的域」这个映射。

这是"写入侧"最有力的证据形式：它检查的是一段真实群聊跑完全流程后的**落点**，
而不是我构造的输入。
"""
from __future__ import annotations

import json
import os
import re

d = os.path.expandvars(r"%LOCALAPPDATA%\N.E.K.O\memory\宅久皖萱")
facts = json.load(open(os.path.join(d, "facts.json"), encoding="utf-8"))
print(f"总 facts = {len(facts)}")

# ── 1) subject_id 形态合法性（对所有 kind）──────────────────────────
bad_shape = []
kinds: dict = {}
for f in facts:
    k = f.get("subject_kind")
    sid = str(f.get("subject_id") or "")
    kinds[k] = kinds.get(k, 0) + 1
    parts = sid.split(":")
    if k == "group_chat" and (len(parts) != 2 or not all(parts)):
        bad_shape.append((k, sid))
    elif k == "group_participant" and (len(parts) != 3 or not all(parts)):
        bad_shape.append((k, sid))
    elif k == "participant" and (len(parts) != 2 or not all(parts)):
        bad_shape.append((k, sid))
print("按 kind 统计 =", kinds)
print("形态非法 =", bad_shape or "无")

# ── 2) 群域：文本自述群号 vs 所属域的群号 ──────────────────────────
# 只抓显式自述（"群号为1048307485" / "群号: 985066274"），不抓裸数字 ——
# 裸数字会命中发言人 QQ（第一次就踩了这个坑，得出 12/12 全不一致的假结论）。
pat_group = re.compile(r"群号[为是:：\s]*(\d{9,11})")
ok = []
bad = []
for f in facts:
    if f.get("subject_kind") != "group_chat":
        continue
    sid = str(f.get("subject_id") or "")
    text = str(f.get("text") or "")
    gid = sid.split(":", 1)[1] if ":" in sid else ""
    for stated in pat_group.findall(text):
        (ok if stated == gid else bad).append((sid, stated, text))

print()
print("=== 群域：自述群号 vs 所属域 ===")
print(f"一致 = {len(ok)}   不一致 = {len(bad)}")
for sid, stated, text in ok[:6]:
    print(f"  [ok ] 域={sid} 自述={stated} | {text[:66]}")
for sid, stated, text in bad[:6]:
    print(f"  [BAD] 域={sid} 自述={stated} | {text[:66]}")

# ── 3) 成员域：文本自述的发言人 QQ vs 域里的发言人头 ────────────────
pat_qq = re.compile(r"QQ[为是:：\s]*(\d{5,12})")
m_ok = m_bad = 0
m_samples = []
for f in facts:
    if f.get("subject_kind") != "group_participant":
        continue
    parts = str(f.get("subject_id") or "").split(":")
    if len(parts) != 3:
        continue
    head = parts[2]
    text = str(f.get("text") or "")
    for stated in pat_qq.findall(text):
        if stated == head:
            m_ok += 1
        else:
            m_bad += 1
            if len(m_samples) < 5:
                m_samples.append((f.get("subject_id"), stated, text))
print()
print("=== 成员域：文本自述发言人 QQ vs 域中发言人头 ===")
print(f"一致 = {m_ok}   不一致 = {m_bad}")
for sid, stated, text in m_samples:
    print(f"  [??] 域={sid} 文本QQ={stated} | {text[:66]}")

# ── 4) 群域集合 ────────────────────────────────────────────────────
known = sorted({
    str(f.get("subject_id") or "").split(":", 1)[1]
    for f in facts
    if f.get("subject_kind") == "group_chat" and ":" in str(f.get("subject_id") or "")
})
print()
print("出现的群域 =", known)
