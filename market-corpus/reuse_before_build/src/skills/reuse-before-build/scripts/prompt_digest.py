#!/usr/bin/env python3
"""reuse-before-build · 粘贴型提示词的版本摘要

`references/host-adapters.md` 里那几段"复制粘贴就能用"的提示词，最容易在复制、
精简、翻译的过程中静默走样。这个脚本给它们配 sha256：

    --verify        登记表（prompt-digests.json）与文档里的块是否仍一致
    --check FILE    你手上这段提示词是哪个 id、哪一版；不认识就是 NO MATCH
    --digest FILE   只算摘要，不查表（给新版本入库用）
    --emit          按当前文档生成登记表 JSON（维护者用，别手工抄摘要）

归一化规则（登记与核对必须走同一套，否则天天误报）：
UTF-8 文本、LF 换行、去掉每行行尾空白、去掉首尾空行。
所以 CRLF、尾随空格、复制时多出的空行都不影响判定；改一个词就会不匹配。

退出码：
    0  一致 / 命中
    1  --verify 有不一致，或 --check 没有命中
    2  用法、文件或登记表本身有问题
"""

from __future__ import annotations

__version__ = "1.2.7"

import argparse
import hashlib
import json
import os
import re
import sys

try:  # Windows 控制台默认 cp936，中文会炸；能改就改
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(ROOT, "references", "host-adapters.md")
REGISTRY = os.path.join(ROOT, "prompt-digests.json")

BEGIN_RX = re.compile(r"^\s*<!--\s*RBB:PROMPT:BEGIN\s+id=([A-Za-z0-9._-]+)\s*-->\s*$")
END_RX = re.compile(r"^\s*<!--\s*RBB:PROMPT:END\s+id=([A-Za-z0-9._-]+)\s*-->\s*$")
HEADING_RX = re.compile(r"^##\s+(.+?)\s*$")


def normalize(text: str) -> str:
    """摘要与核对共用：LF、去行尾空白、去首尾空行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


def digest_of(text: str) -> tuple[str, int]:
    norm = normalize(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest(), len(norm)


def extract_blocks(path: str) -> list[dict]:
    """按 <!-- RBB:PROMPT:BEGIN id=x --> 取围栏内容，返回 id/label/起始行号/正文。

    状态机：等围栏 → 收正文 → 等 END。中间出现别的非空行就报错，
    免得把"标记套错位置"这种错误悄悄当成正常块。
    """
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    blocks: list[dict] = []
    heading = ""
    current: dict | None = None
    state = ""  # "before_fence" | "in_fence" | "after_fence"
    body: list[str] = []

    for idx, line in enumerate(lines, start=1):
        if current is None:
            m = HEADING_RX.match(line)
            if m:
                heading = m.group(1)
            b = BEGIN_RX.match(line)
            if b:
                current = {"id": b.group(1), "label": heading, "begin_line": idx}
                state, body = "before_fence", []
            elif END_RX.match(line):
                raise ValueError(f"第 {idx} 行有 END 但没有对应的 BEGIN：{path}")
            continue

        if state == "before_fence":
            if line.strip().startswith("```"):
                state = "in_fence"
            elif line.strip():
                raise ValueError(f"块 {current['id']} 的 BEGIN 与围栏之间多了内容（第 {idx} 行）：{path}")
            continue

        if state == "in_fence":
            if line.strip().startswith("```"):
                state = "after_fence"
            else:
                body.append(line)
            continue

        e = END_RX.match(line)
        if e:
            if e.group(1) != current["id"]:
                raise ValueError(f"第 {idx} 行 END id={e.group(1)} 与 BEGIN id={current['id']} 不匹配：{path}")
            blocks.append({**current, "text": "\n".join(body)})
            current, state = None, ""
            continue
        if line.strip():
            raise ValueError(f"块 {current['id']} 围栏结束后、END 之前多了内容（第 {idx} 行）：{path}")

    if current is not None:
        raise ValueError(f"块 {current['id']} 有 BEGIN 没有 END（起始第 {current['begin_line']} 行）：{path}")
    if not blocks:
        raise ValueError(f"没有找到任何 RBB:PROMPT 块：{path}")
    return blocks


def load_registry(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def registry_entries(registry: dict) -> list[dict]:
    """登记表结构校验。坏表必须走 FAIL/rc=2，不能抛 KeyError traceback 给使用者。"""
    if not isinstance(registry, dict):
        raise ValueError("prompt-digests.json 顶层不是对象")
    prompts = registry.get("prompts")
    if not isinstance(prompts, list):
        raise ValueError("prompt-digests.json 缺少 prompts 数组")
    seen: set[str] = set()
    for entry in prompts:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError("prompt-digests.json 里有条目缺 id（或条目本身不是对象）")
        if entry["id"] in seen:
            raise ValueError(f"prompt-digests.json 里 id={entry['id']} 登记了两次")
        seen.add(entry["id"])
        versions = entry.get("versions")
        if not isinstance(versions, list):
            raise ValueError(f"登记项 {entry['id']} 的 versions 不是数组")
        for version in versions:
            if not isinstance(version, dict) or not isinstance(version.get("sha256"), str):
                raise ValueError(f"登记项 {entry['id']} 有版本缺 sha256（或版本不是对象）")
    return prompts


def read_input(target: str) -> str:
    if target == "-":
        return sys.stdin.read()
    if not os.path.isfile(target):
        raise FileNotFoundError(f"找不到文件：{target}")
    with open(target, encoding="utf-8-sig", errors="strict") as fh:
        return fh.read()


def cmd_verify(args) -> int:
    try:
        blocks = extract_blocks(SOURCE)
        registry = load_registry(REGISTRY)
        entries = {p["id"]: p for p in registry_entries(registry)}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL  无法核对：{exc}", file=sys.stderr)
        return 2

    failures = 0
    seen: set[str] = set()

    for block in blocks:
        seen.add(block["id"])
        entry = entries.get(block["id"])
        if entry is None:
            failures += 1
            print(f"FAIL  {block['id']}  文档里有这个块，但 prompt-digests.json 没登记", file=sys.stderr)
            continue
        versions = entry.get("versions") or []
        if not versions:
            failures += 1
            print(f"FAIL  {block['id']}  登记项没有 versions", file=sys.stderr)
            continue
        got, chars = digest_of(block["text"])
        current = versions[0]
        if got == current.get("sha256") and chars == current.get("chars"):
            print(f"PASS  {block['id']:<22} version={current.get('version','?'):<4} sha256={got[:16]}… chars={chars}")
        else:
            failures += 1
            print(
                f"FAIL  {block['id']}  文档已改，摘要对不上：文档 {got[:16]}…/{chars} 字 "
                f"vs 登记 {str(current.get('sha256'))[:16]}…/{current.get('chars')} 字。"
                "确认改动是故意的之后，用 --emit 重新生成登记表，并把旧摘要留在 versions[1:] 的历史里。",
                file=sys.stderr,
            )

    for extra in sorted(set(entries) - seen):
        failures += 1
        print(f"FAIL  {extra}  登记了但文档里没有这个块", file=sys.stderr)

    if failures:
        print(f"\n{failures} 处不一致：提示词版本和登记表已经分叉，不要发版。", file=sys.stderr)
        return 1

    print(f"\nPASS  {len(blocks)} 段提示词与 prompt-digests.json 一致。")
    return 0


def cmd_check(args) -> int:
    try:
        text = read_input(args.check)
        registry = load_registry(REGISTRY)
        entries = registry_entries(registry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL  无法核对：{exc}", file=sys.stderr)
        return 2

    got, chars = digest_of(text)
    hits: list[dict] = []
    for entry in entries:
        versions = entry.get("versions", [])
        for idx, version in enumerate(versions):
            if version.get("sha256") == got:
                hits.append(
                    {
                        "id": entry["id"],
                        "label": entry.get("label", ""),
                        "version": version.get("version", "?"),
                        "released": version.get("released", ""),
                        "chars": chars,
                        "current": idx == 0,
                    }
                )

    if not hits:
        print(f"NO MATCH  sha256={got}  chars={chars}")
        print("这段提示词不在登记表里：要么被改过一个字，要么是从未登记的版本抄来的。"
              "别当成功——重新拷一份原文，或对照 host-adapters.md 的当前版本。", file=sys.stderr)
        return 1

    stale = [h for h in hits if not h["current"]]
    for hit in hits:
        released = f" released={hit['released']}" if hit["released"] else ""
        state = "CURRENT" if hit["current"] else "STALE"
        print(f"MATCH  {state}  id={hit['id']} version={hit['version']}{released} chars={hit['chars']}  sha256={got}")
        if hit["label"]:
            print(f"       {hit['label']}")
    if stale:
        print("这是历史版本，不是随包当前版；按 host-adapters.md 重新拷一份当前提示词。", file=sys.stderr)
        return 1
    return 0


def cmd_digest(args) -> int:
    try:
        text = read_input(args.digest)
    except OSError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 2
    got, chars = digest_of(text)
    print(f"sha256={got}  chars={chars}")
    return 0


def cmd_emit(args) -> int:
    try:
        blocks = extract_blocks(SOURCE)
    except (OSError, ValueError) as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 2

    old: dict[str, list[dict]] = {}
    if os.path.isfile(REGISTRY):
        try:
            old = {p["id"]: p.get("versions", []) for p in registry_entries(load_registry(REGISTRY))}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[warn] 读旧登记表失败，历史摘要不会保留：{exc}", file=sys.stderr)

    prompts = []
    for block in blocks:
        got, chars = digest_of(block["text"])
        versions = [{"version": args.version, "sha256": got, "chars": chars, "released": args.released}]
        for prev in old.get(block["id"], []):
            if prev.get("sha256") == got:
                continue
            versions.append({k: v for k, v in prev.items() if v})
        prompts.append(
            {
                "id": block["id"],
                "label": block["label"],
                "source": "references/host-adapters.md",
                "versions": versions,
            }
        )

    payload = {
        "schema": 1,
        "normalize": "utf-8, LF, 去行尾空白, 去首尾空行",
        "note": "versions[0] 是当前随包的版本；旧版本留在后面，用来识别用户手上那版。用 scripts/prompt_digest.py --emit 生成，别手抄摘要。",
        "prompts": prompts,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="粘贴型提示词的 sha256 登记与核对（reuse-before-build）",
        epilog="不带参数时等同 --verify。",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verify", action="store_true", help="核对登记表与 references/host-adapters.md（默认）")
    group.add_argument("--check", dest="check", metavar="FILE", help="反查这段提示词是哪个 id/版本；用 - 读 stdin")
    group.add_argument("--digest", dest="digest", metavar="FILE", help="只算摘要；用 - 读 stdin")
    group.add_argument("--emit", action="store_true", help="按当前文档生成登记表 JSON 到 stdout")
    parser.add_argument("--version", default="p1", help="配合 --emit：这次的新版本号，默认 p1")
    parser.add_argument("--released", default="", help="配合 --emit：这批提示词随哪个 repo 版本发布，例如 v1.2.0")
    args = parser.parse_args()

    if args.check is not None:
        return cmd_check(args)
    if args.digest is not None:
        return cmd_digest(args)
    if args.emit:
        return cmd_emit(args)
    return cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
