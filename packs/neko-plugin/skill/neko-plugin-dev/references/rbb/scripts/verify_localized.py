#!/usr/bin/env python3
"""校验一份本地化副本是否保住了不可变项。

五道闸：
  1. 不变块（<!-- RBB:INVARIANTS --> 之间）——逐字版比哈希，翻译版比规则条数与逐条锚点；
  2. 规则完整性——块内编号必须正好 1..N（N≥rules_min），删条、改号都会露馅；
  3. 语义锚点（localization.contract.json 的 must_have）——中英任一写法均算命中；
  4. 关键章节——正典比正文 SHA-256，本地化副本比最小长度与章节内锚点；
  5. 声明行——`localized: host=... | kept=... | changed=... | unsupported=...`。

`--canonical` 不是开关，是自证：只对契约里列出的包内参考件生效，对任何其它文件一律拒绝（rc 2），
免得"本地化者"给自己开个后门跳过声明检查。

用法：
    python scripts/verify_localized.py <file>              # 校验本地化副本（验收就用这个，不加 flag）
    python scripts/verify_localized.py <file> --canonical   # 仅用于包内参考件，其它路径会被拒绝
    python scripts/verify_localized.py --print-hash SKILL.md # 打印不变块哈希，用于更新契约
    python scripts/verify_localized.py --print-section-hash SKILL.md # 打印关键章节正文哈希

退出码：0 = PASS，1 = FAIL（缺什么会列出来），2 = 用法错误（如对包外文件用 --canonical）。
"""

from __future__ import annotations

__version__ = "1.2.15"

import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONTRACT = os.path.join(os.path.dirname(HERE), "localization.contract.json")
# 随包英文正典的基线钉在代码里，不能只相信同目录、同样可被改写的契约。
CANONICAL_SHA256 = "fa0f991fbfd236d860a2c401aff6f104f994e97d2b1fa100c23eee9d046fbc93"
# 关键章节的正文哈希同样钉在代码里：只改契约删掉 critical_sections 不能放行。
CANONICAL_SECTION_SHA256 = {
    "step2": "031a0a7cbff5c5f3b500c46a12d35e83036ff4109b5c1d041abbdb30623613a4",
    "step3": "6bba58cd17a327df6284844010f17ba8a72e59e588c8bf4e31e7499caace8d76",
    "hard_constraints": "c5699bd8be27016e9fcab95b80f0702dca3b44ed49df00501895855c25e61761",
    "untrusted": "c878e673c7ca0394c4d211114dfdb589df4238735d4806b5a08d28dacabc984f",
}
CANONICAL_SECTION_FLOORS = {
    "step2": 500,
    "step3": 500,
    "hard_constraints": 350,
    "untrusted": 180,
}
# 锚点也钉进代码：只改契约删掉某节锚点，会让下游副本的检查变宽松。
CANONICAL_SECTION_ANCHORS = {
    "step2": (
        ("scoring", ("activity", "活跃度"), ()),
        ("modifiability", ("Modifiability gate", "可改性硬闸"), ()),
        ("verdict_table", (), ("Join", "Extend", "Fork", "Build")),
        ("no_fifth", ("There is no fifth verdict", "没有第五个判决"), ()),
    ),
    "step3": (
        ("contributing", ("CONTRIBUTING",), ()),
        ("branch", ("Branch first, never the default branch", "分支优先，绝不直写主干"), ()),
        ("adapter", ("stable seam and a thin adapter", "稳定接入口 + 薄适配层"), ()),
        ("ai", ("Disclose AI assistance", "说清 AI 参与"), ()),
    ),
    "hard_constraints": (
        ("never_invent", ("Never invent repos", "不虚构仓库"), ()),
        ("public_write", ("per-action confirmation", "每一次公开写都要逐次确认"), ()),
        ("never_join", ("Never Join a repo", "不 Join 可改性判红"), ()),
    ),
    "untrusted": (
        ("data", ("data, not instructions", "数据，不是指令"), ()),
        ("no_exec", ("Never execute a command", "绝不直接在宿主机执行"), ()),
        ("verdict", ("deliverable's verdict", "交付结论必须来自"), ()),
    ),
}
CANONICAL_RULES_MIN = 5
CANONICAL_RULE_ANCHOR_IDS = frozenset({"rule_1", "rule_2", "rule_3", "rule_4", "rule_5"})
CANONICAL_MUST_HAVE_IDS = frozenset({
    "gate", "verdict_order", "deliverable", "contributing_rule", "public_write_gate",
    "quick_lane", "untrusted_repository_content", "modifiability_green_only",
    "no_fifth_verdict", "branch_first", "thin_adapter", "never_join_red",
})
CANONICAL_DECLARATION_KEYS = ("host", "kept", "changed", "unsupported")
CANONICAL_DECLARATION_WITHIN_LAST = 8


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _marker_line(line: str, marker: str) -> bool:
    """整行锚定：说明文字或反引号里的提及不算块边界。"""
    return re.fullmatch(rf"<!--\s*{re.escape(marker)}\s*-->", line.strip()) is not None


def _fence_mask(lines: list[str]) -> list[bool]:
    """标记反引号与波浪号围栏； closing fence 必须同字符且不少于开栏长度。"""
    masked: list[bool] = []
    fence_char = ""
    fence_len = 0
    for line in lines:
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char, fence_len = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                fence_char, fence_len = "", 0
            masked.append(True)
            continue
        masked.append(bool(fence_char))
    return masked


def extract_block(text: str, begin: str, end: str) -> str | None:
    """取不变块。跳过代码围栏里的"标记示例"，并在多个候选里选规则最全的那个。"""
    lines = text.splitlines()
    fenced = _fence_mask(lines)

    begins = [i for i, line in enumerate(lines) if not fenced[i] and _marker_line(line, begin)]
    ends = [i for i, line in enumerate(lines) if not fenced[i] and _marker_line(line, end)]
    candidates: list[str] = []
    for b in begins:
        e = next((x for x in ends if x > b), None)
        if e is None:
            continue
        candidates.append("\n".join(lines[b + 1:e]).strip())
    if not candidates:
        return None
    # 演示用的空块/占位块排在前面时，别让它顶替真块
    return max(candidates, key=lambda c: (len(RULE_RE.findall(c)), -candidates.index(c)))


def block_hash(inner: str) -> str:
    norm = "\n".join(line.rstrip() for line in inner.strip().splitlines())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def find_lang_marker(inner: str, prefix: str) -> str | None:
    m = re.search(re.escape(prefix) + r"\s*([A-Za-z0-9_-]+)", inner)
    return m.group(1) if m else None


RULE_RE = re.compile(r"^(\d+)\.\s+\S", re.MULTILINE)  # 只认第 0 列的编号，缩进子项不算规则


def check_rules(inner: str, rules_min: int) -> tuple[list[str], list[str], list[int]]:
    """返回 (缺失项, 提示项, 编号列表)。"""
    numbers = [int(n) for n in RULE_RE.findall(inner)]
    missing, notes = [], []
    if len(numbers) < rules_min:
        missing.append(f"rules_count({len(numbers)}<{rules_min})")
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        missing.append(f"rules_numbering({','.join(map(str, numbers))})")
    if rules_min and len(numbers) > rules_min:
        notes.append(f"rules {len(numbers)} > rules_min {rules_min} (可能拆条了)")
    return missing, notes, numbers


def parse_frontmatter(text: str) -> dict | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            data: dict[str, str] = {}
            for line in lines[1:i]:
                if ":" in line and not line.startswith((" ", "\t")):
                    k, v = line.split(":", 1)
                    data[k.strip()] = v.strip()
            return data
    return None


NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def check_frontmatter(fm: dict | None) -> tuple[list[str], list[str]]:
    """返回 (缺失项, 提示项)。没有 frontmatter 的聊天指令副本不算失败。"""
    if fm is None:
        return [], ["no frontmatter (chat-instruction copy?)"]
    missing, notes = [], []
    name = fm.get("name", "")
    desc = fm.get("description", "")
    if not name:
        missing.append("frontmatter_name")
    elif len(name) > 64:
        missing.append(f"frontmatter_name_too_long({len(name)})")
    elif not NAME_RE.match(name):
        missing.append("frontmatter_name_format")
    if not desc:
        missing.append("frontmatter_description")
    elif len(desc) > 1024:
        missing.append(f"frontmatter_description_too_long({len(desc)})")
    if "license" not in fm:
        notes.append("no license field")
    return missing, notes


def _anchor_present(text: str, needle: object) -> bool:
    """锚点忽略大小写；ASCII 词按词边界匹配，中文等多字节文本仍按子串匹配。"""
    if not isinstance(needle, str) or not needle.strip():
        return False
    if not needle.isascii():
        return needle.lower() in text.lower()
    pattern = re.escape(needle)
    if needle[0].isalnum() or needle[0] == "_":
        pattern = rf"(?<![A-Za-z0-9_]){pattern}"
    if needle[-1].isalnum() or needle[-1] == "_":
        pattern = rf"{pattern}(?![A-Za-z0-9_])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def check_anchors(scope: str, items: list[dict], where: str) -> list[str]:
    missing = []
    for item in items:
        ident = item.get("id", "?")
        any_of = item.get("any", [])
        all_of = item.get("all", [])
        if not any_of and not all_of:
            missing.append(f"{where}:{ident}(no any/all spec)")
            continue
        if any_of and not any(_anchor_present(scope, s) for s in any_of):
            missing.append(f"{where}:{ident}")
        absent = [s for s in all_of if not _anchor_present(scope, s)]
        if absent:
            missing.append(f"{where}:{ident}(needs all of: {', '.join(absent)})")
    return missing


def section_hash(body: str) -> str:
    norm = "\n".join(line.rstrip() for line in body.strip().splitlines())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def extract_section(text: str, headings: list[str]) -> str | None:
    """按二级标题取章节正文；代码围栏里的示例标题不算。"""
    if not isinstance(headings, list):
        return None
    wanted = {
        heading.strip().casefold()
        for heading in headings
        if isinstance(heading, str) and heading.strip()
    }
    if not wanted:
        return None
    lines = text.splitlines()
    fenced = _fence_mask(lines)
    starts: list[int] = []
    for index, line in enumerate(lines):
        if fenced[index]:
            continue
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match and match.group(1).strip().casefold() in wanted:
            starts.append(index)
    candidates: list[str] = []
    for start in starts:
        end = next(
            (index for index in range(start + 1, len(lines))
             if not fenced[index] and re.match(r"^##\s+\S", lines[index])),
            len(lines),
        )
        candidates.append("\n".join(lines[start + 1:end]).strip())
    return max(candidates, key=len) if candidates else None


def check_critical_sections(text: str, items: list[dict], is_canonical: bool) -> list[str]:
    """关键章节不能只剩一个子串：正典比正文哈希，本地化副本比最小长度 + 多条锚点。"""
    missing: list[str] = []
    if not isinstance(items, list):
        return ["critical_sections(not_list)"]
    for item in items:
        if not isinstance(item, dict):
            missing.append("critical_sections(not_object)")
            continue
        ident = item.get("id", "?")
        headings = item.get("headings", [])
        if not isinstance(headings, list) or not any(
            isinstance(heading, str) and heading.strip() for heading in headings
        ):
            missing.append(f"section:{ident}(bad_headings)")
            continue
        body = extract_section(text, headings)
        if body is None:
            missing.append(f"section:{ident}")
            continue
        if is_canonical and item.get("sha256"):
            if section_hash(body) != str(item["sha256"]).lower():
                missing.append(f"section_hash:{ident}")
            continue
        anchors = item.get("anchors", [])
        anchor_errors = anchor_spec_errors(anchors, f"section:{ident}:anchors")
        if anchor_errors:
            missing.extend(anchor_errors)
            continue
        try:
            floor = int(item.get("min_chars", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            missing.append(f"section_floor:{ident}(invalid)")
            continue
        if floor and len(body.strip()) < floor:
            missing.append(f"section_short:{ident}({len(body.strip())}<{floor})")
        for anchor in anchors:
            aid = anchor.get("id", "?")
            any_of = anchor.get("any", [])
            all_of = anchor.get("all", [])
            if any_of and not any(_anchor_present(body, needle) for needle in any_of):
                missing.append(f"section_anchor:{ident}:{aid}")
            absent = [needle for needle in all_of if not _anchor_present(body, needle)]
            if absent:
                missing.append(f"section_anchor:{ident}:{aid}(needs all of: {', '.join(absent)})")
    return missing


def id_set(value: object) -> frozenset[str]:
    """取契约锚点条目的 id 集合；形状不对按空集合处理。"""
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(item.get("id", "")) for item in value if isinstance(item, dict))


def anchor_signature(value: object) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """把契约里的 anchors 归一成可逐字比较的形状；形状不对按空签名处理。"""
    if not isinstance(value, list):
        return ()
    rows: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for anchor in value:
        if not isinstance(anchor, dict):
            return ()
        any_of = anchor.get("any", []) or []
        all_of = anchor.get("all", []) or []
        if not isinstance(any_of, list) or not isinstance(all_of, list):
            return ()
        rows.append(
            (str(anchor.get("id", "")), tuple(str(item) for item in any_of), tuple(str(item) for item in all_of))
        )
    return tuple(rows)


def anchor_spec_errors(value: object, field: str) -> list[str]:
    """契约里的 any/all 必须是「非空字符串列表」；坏契约要在加载时报 contract 错。"""
    if not isinstance(value, list):
        return [f"{field}(not_list)"]
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{field}[{index}](not_object)")
            continue
        ident = str(item.get("id", "") or f"#{index}")
        for key in ("any", "all"):
            if key not in item:
                continue
            needles = item.get(key)
            if not isinstance(needles, list):
                errors.append(f"{field}:{ident}({key}_not_list)")
                continue
            for needle in needles:
                if not isinstance(needle, str) or not needle.strip():
                    errors.append(f"{field}:{ident}({key}_empty_or_non_string)")
    return errors


def contract_root_errors(contract: object) -> list[str]:
    """契约顶层、block 与 declaration 的基本形状；坏字段会让后续代码直接抛栈。"""
    if not isinstance(contract, dict):
        return ["contract(not_object)"]
    block = contract.get("block")
    if "block" in contract and not isinstance(block, dict):
        return ["block(not_object)"]
    if isinstance(block, dict):
        for key in ("begin", "end", "lang_marker_prefix", "canonical_file", "canonical_sha256"):
            if key in block and not isinstance(block[key], str):
                return [f"block.{key}(not_string)"]
        if "canonical_files" in block:
            files = block["canonical_files"]
            if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
                return ["block.canonical_files(not_string_list)"]
    declaration = contract.get("declaration")
    if "declaration" in contract and not isinstance(declaration, dict):
        return ["declaration(not_object)"]
    if isinstance(declaration, dict):
        if "keys" in declaration:
            keys = declaration["keys"]
            if not isinstance(keys, list) or not all(isinstance(item, str) for item in keys):
                return ["declaration.keys(not_string_list)"]
        if "prefix" in declaration and not isinstance(declaration["prefix"], str):
            return ["declaration.prefix(not_string)"]
    return []


def check_contract_baseline(contract: dict) -> list[str]:
    """契约里的章节项不能少于代码内置基线（删项、降长度、换哈希、删锚点都会露馅）。"""
    missing: list[str] = []
    critical = contract.get("critical_sections", [])
    if not isinstance(critical, list):
        missing.append("critical_sections(not_list)")
        critical = []
    for index, item in enumerate(critical):
        if not isinstance(item, dict):
            missing.append(f"critical_sections[{index}](not_object)")
    missing += anchor_spec_errors(contract.get("rule_anchors"), "rule_anchors")
    missing += anchor_spec_errors(contract.get("must_have"), "must_have")
    items = {str(item.get("id", "")): item for item in critical if isinstance(item, dict)}
    for ident, pinned in CANONICAL_SECTION_SHA256.items():
        item = items.get(ident)
        if item is None:
            missing.append(f"critical_sections:{ident}")
            continue
        if not isinstance(item.get("headings"), list) or not item.get("headings") or not all(
            isinstance(heading, str) and heading.strip() for heading in item.get("headings")
        ):
            missing.append(f"critical_sections_headings:{ident}(invalid)")
        if str(item.get("sha256", "")).lower() != pinned:
            missing.append(f"critical_sections_hash:{ident}")
        try:
            floor = int(item.get("min_chars", 0) or 0)
        except (TypeError, ValueError):
            floor = 0
        if floor < CANONICAL_SECTION_FLOORS[ident]:
            missing.append(f"critical_sections_floor:{ident}({floor}<{CANONICAL_SECTION_FLOORS[ident]})")
        if anchor_signature(item.get("anchors")) != CANONICAL_SECTION_ANCHORS[ident]:
            missing.append(f"critical_sections_anchors:{ident}")
    try:
        rules_min = int(contract.get("rules_min", 0) or 0)
    except (TypeError, ValueError):
        rules_min = 0
    if rules_min != CANONICAL_RULES_MIN:
        missing.append(f"rules_min:{rules_min}!={CANONICAL_RULES_MIN}")
    if id_set(contract.get("rule_anchors")) != CANONICAL_RULE_ANCHOR_IDS:
        missing.append("rule_anchor_ids")
    if id_set(contract.get("must_have")) != CANONICAL_MUST_HAVE_IDS:
        missing.append("must_have_ids")
    declaration = contract.get("declaration", {})
    if not isinstance(declaration, dict):
        missing.append("declaration")
    else:
        if tuple(declaration.get("keys", [])) != CANONICAL_DECLARATION_KEYS:
            missing.append("declaration_keys")
        try:
            within = int(declaration.get("within_last", 0) or 0)
        except (TypeError, ValueError):
            within = 0
        if within != CANONICAL_DECLARATION_WITHIN_LAST:
            missing.append(f"declaration_within_last:{within}!={CANONICAL_DECLARATION_WITHIN_LAST}")
    return missing


def check_declaration(text: str, decl: dict) -> list[str]:
    """声明行必须是文件末尾那条；代码围栏里的示例行不算（SKILL.md 自己就有一段示例）。"""
    prefix = decl.get("prefix", "localized:")
    keys = decl.get("keys", [])
    within = int(decl.get("within_last", 8))
    candidates: list[str] = []
    lines = text.splitlines()
    fenced = _fence_mask(lines)
    for line, in_fence in zip(lines, fenced):
        if in_fence or not line.strip():
            continue
        candidates.append(line.strip())
    # 文件末尾追加版权/生成时间不算错，只要声明行还在最后 N 个非空、非代码行里
    for line in reversed(candidates[-within:]):
        if line.lower().startswith(prefix.lower()):
            return [f"declaration:{k}" for k in keys if k not in line]
    return ["declaration"]


def main() -> int:
    # 只在 CLI 入口调整控制台编码；被插件 import 时绝不能改写宿主 stdout/stderr。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description="校验本地化副本是否保住了不可变项")
    ap.add_argument("file", help="待校验的本地化副本")
    ap.add_argument("--contract", default=DEFAULT_CONTRACT,
                    help="契约文件路径；只允许包内 localization.contract.json")
    ap.add_argument("--canonical", action="store_true",
                    help="仅用于包内参考件；对其它路径会被拒绝（rc 2）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--print-hash", action="store_true",
                       help="只打印不变块哈希（用于更新契约），不做校验")
    group.add_argument("--print-section-hash", action="store_true",
                       help="只打印关键章节正文哈希（用于更新契约），不做校验")
    ap.add_argument("--quiet", action="store_true", help="只输出结果行")
    args = ap.parse_args()
    print_only = args.print_hash or args.print_section_hash

    # --contract 不能指向任意文件：换成一份空契约（rules_min=0、无锚点）就能跳过全部检查。
    if os.path.realpath(args.contract) != os.path.realpath(DEFAULT_CONTRACT):
        print("REFUSED  --contract 只允许指向随包的 localization.contract.json，"
              "不允许替换成更宽松的契约。", file=sys.stderr)
        return 2

    if not os.path.exists(args.file):
        print(f"FAIL  file not found: {args.file}")
        return 1
    text = read_text(args.file)

    try:
        with open(args.contract, "r", encoding="utf-8") as fh:
            contract = json.load(fh)
    except FileNotFoundError:
        if args.print_hash:
            contract = {}  # --print-hash 用默认标记即可，首次生成契约时还没有它
        else:
            print(f"FAIL  contract not found: {args.contract}")
            return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL  contract is not valid JSON: {exc}")
        return 1

    root_errors = contract_root_errors(contract)
    if root_errors:
        print("FAIL  contract 顶层结构不合法：")
        print("MISSING: " + ", ".join(root_errors))
        return 1

    want_hash = str((contract.get("block") or {}).get("canonical_sha256") or "")
    if not print_only and not re.fullmatch(r"[0-9a-fA-F]{64}", want_hash):
        print("FAIL  contract 缺少合法的 block.canonical_sha256（64 位十六进制）")
        return 1
    if not print_only and want_hash.lower() != CANONICAL_SHA256:
        print("FAIL  contract 的正典哈希与代码内置基线不一致；拒绝使用被改写的基线。")
        return 1
    try:
        rules_floor = int(contract.get("rules_min", 0))
    except (TypeError, ValueError):
        rules_floor = 0
    if not print_only and (rules_floor < 1 or not contract.get("must_have")):
        print("FAIL  contract 的 rules_min 必须 ≥ 1，且 must_have 不能为空")
        return 1
    if not print_only:
        baseline_missing = check_contract_baseline(contract)
        if baseline_missing:
            print("FAIL  contract 校验失败（基线被放宽或结构不合法）：")
            print("MISSING: " + ", ".join(baseline_missing))
            return 1

    block_cfg = contract.get("block", {})
    begin = block_cfg.get("begin", "RBB:INVARIANTS:BEGIN")
    end = block_cfg.get("end", "RBB:INVARIANTS:END")
    prefix = block_cfg.get("lang_marker_prefix", "RBB:INVARIANTS:LANG:")
    want_hash = block_cfg.get("canonical_sha256", "")

    inner = extract_block(text, begin, end)
    if args.print_section_hash:
        items = contract.get("critical_sections") or []
        if not isinstance(items, list) or not items:
            print("FAIL  contract 的 critical_sections 必须是非空数组", file=sys.stderr)
            return 1
        failed = False
        for item in items:
            if not isinstance(item, dict):
                print("MISSING critical_sections(not_object)", file=sys.stderr)
                failed = True
                continue
            ident = str(item.get("id", "?"))
            headings = item.get("headings")
            if not isinstance(headings, list) or not any(
                isinstance(heading, str) and heading.strip() for heading in headings
            ):
                print(f"MISSING section:{ident}(bad_headings)", file=sys.stderr)
                failed = True
                continue
            body = extract_section(text, headings)
            if body is None:
                print(f"MISSING section:{ident}", file=sys.stderr)
                failed = True
                continue
            print(f"{ident} {section_hash(body)}")
        return 1 if failed else 0
    if args.print_hash:
        if inner is None:
            print("FAIL  no invariants block found", file=sys.stderr)
            return 1
        print(block_hash(inner))
        return 0

    # --canonical 只能指向契约里登记的包内参考件，避免本地化者给自己开后门
    base = os.path.dirname(os.path.abspath(args.contract))
    allowed = {os.path.realpath(os.path.join(base, f))
               for f in block_cfg.get("canonical_files", [])}
    real_file = os.path.realpath(args.file)
    is_ref = real_file in allowed
    is_canonical = real_file == os.path.realpath(os.path.join(base, block_cfg.get("canonical_file", "SKILL.md")))
    if args.canonical and not is_ref:
        print(f"REFUSED  {os.path.basename(args.file)} 不在包内参考件白名单里，"
              "不能用 --canonical；本地化副本必须带 localized: 声明行完整校验。",
              file=sys.stderr)
        return 2

    missing: list[str] = []
    notes: list[str] = []
    mode = "missing"
    rule_nums = 0

    if inner is None:
        missing.append("invariants_block")
    else:
        got = block_hash(inner)
        lang = find_lang_marker(inner, prefix)
        if want_hash and got == want_hash:
            mode = "verbatim"
        elif lang:
            mode = f"translated({lang})"
        else:
            mode = "hash_mismatch"
            missing.append("invariants_hash")
        if is_canonical and got != CANONICAL_SHA256:
            missing.append("canonical_invariants_hash")

        r_missing, r_notes, rule_nums = check_rules(inner, int(contract.get("rules_min", 0)))
        missing += r_missing
        notes += r_notes
        missing += check_anchors(inner, contract.get("rule_anchors", []), "rule")

    fm_missing, fm_notes = check_frontmatter(parse_frontmatter(text))
    missing += fm_missing
    notes += fm_notes

    missing += check_anchors(text, contract.get("must_have", []), "anchor")
    missing += check_critical_sections(text, contract.get("critical_sections", []), is_canonical)

    if not (args.canonical or is_ref):
        missing += check_declaration(text, contract.get("declaration", {}))

    name = os.path.basename(args.file)
    if missing:
        print(f"FAIL  {name}  mode={mode}  rules={rule_nums}")
        print("MISSING: " + ", ".join(missing))
        if notes and not args.quiet:
            for n in notes:
                print(f"note: {n}")
        print("修：把缺的项补回去（不许静默删）；宿主真的做不到，写进 unsupported 并说明原因。")
        return 1

    total = len(contract.get("must_have", []))
    print(f"PASS  {name}  invariant-block={mode}  rules={rule_nums}/{contract.get('rules_min', '?')}"
          f"  anchors={total}/{total}" + ("  (reference copy)" if is_ref else ""))
    if notes and not args.quiet:
        for n in notes:
            print(f"note: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
